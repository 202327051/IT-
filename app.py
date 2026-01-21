from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from flask_basicauth import BasicAuth
import pandas as pd
import sqlite3
import unicodedata
import datetime
import io
import base64
import os
import matplotlib.pyplot as plt
import matplotlib

# グラフの日本語化け対策
matplotlib.use('Agg') 

# Render環境に合わせたテンプレート指定
app = Flask(__name__, template_folder='templates')
CORS(app)

app.config['BASIC_AUTH_USERNAME'] = '202327000'
app.config['BASIC_AUTH_PASSWORD'] = '0000'
app.config['BASIC_AUTH_FORCE'] = True
basic_auth = BasicAuth(app)

# パスをRenderの標準（カレントディレクトリ）に変更
DB_PATH = "history.db"

def normalize_text(text):
    text = unicodedata.normalize("NFKC", str(text)).lower()
    return text.replace(" ", "").replace("．", "").replace(".", "").replace(",", "")

def normalize_choice(text):
    text = unicodedata.normalize("NFKC", str(text)).strip().lower()
    hira_to_kata = str.maketrans("あいうえ", "アイウエ")
    text = text.translate(hira_to_kata)
    mapping = {"a": "ア", "ａ": "ア", "i": "イ", "ｉ": "イ", "u": "ウ", "ｕ": "ウ", "e": "エ", "ｅ": "エ"}
    return mapping.get(text, text)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_question', methods=['POST'])
def get_question():
    mode = str(request.json.get("mode"))
    # パス修正
    file_path = 'ITパスポート.csv' if mode == '1' else '用語説明.csv'
    
    try:
        df = pd.read_csv(file_path, encoding="utf-8")
        q = df.sample(1).iloc[0]
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    question_text = q["問題文"]
    if mode == "2":
        question_text = f"「{question_text}」について説明してください。"
    
    res = {"id": int(q["id"]), "genre": q["ジャンル"], "question": str(question_text)}
    
    if mode == "1":
        choices_raw = str(q["選択肢"])
        res["choices"] = [c.strip() for c in choices_raw.split('\n') if c.strip()]
        
    return jsonify(res)

@app.route('/check_answer', methods=['POST'])
def check_answer():
    data = request.json
    mode, q_id, user_ans = str(data.get("mode")), data.get("id"), data.get("answer")
    # パス修正
    file_path = 'ITパスポート.csv' if mode == '1' else '用語説明.csv'
    
    df = pd.read_csv(file_path, encoding="utf-8")
    q = df[df['id'] == q_id].iloc[0]

    res = {"mode": mode}
    if mode == "1":
        is_correct = normalize_choice(user_ans) == normalize_choice(q["正解"])
        score = 1 if is_correct else 0
        res.update({"score": score, "max": 1, "correct": str(q["正解"]), "explanation": str(q["解説"])})
    else:
        keywords = [k.strip() for k in str(q["必須キーワード"]).replace("、", ",").split(",")]
        user_norm = normalize_text(user_ans)
        hit = [k for k in keywords if normalize_text(k) in user_norm]
        miss = [k for k in keywords if normalize_text(k) not in user_norm]
        score = len(hit)
        max_score = int(q["満点"])
        res.update({"score": score, "max": max_score, "correct": str(q["模範解答"]), "keywords": keywords, "miss": miss})

    # DB保存
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO history (問題ID, ジャンル, 回答, 得点, 満点, mode) VALUES (?, ?, ?, ?, ?, ?)",
                (int(q["id"]), q["ジャンル"], str(user_ans), score, res["max"], mode))
    conn.commit()
    conn.close()
    return jsonify(res)

@app.route('/get_final_stats', methods=['GET'])
def get_final_stats():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT SUM(得点), SUM(満点) FROM history WHERE mode = '1'")
    row = cur.fetchone()
    total_score, total_max = (row[0] or 0), (row[1] or 0)
    total_rate = (total_score / total_max * 100) if total_max > 0 else 0
    
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%m/%d %H:%M")
    cur.execute("INSERT INTO session_stats (timestamp, accuracy) VALUES (?, ?)", (now, total_rate))
    conn.commit()
    
    df_genre = pd.read_sql_query("SELECT ジャンル, SUM(得点) as s, SUM(満点) as m, ROUND(SUM(得点)*100.0/SUM(満点), 1) as rate FROM history WHERE mode = '1' GROUP BY ジャンル ORDER BY rate ASC", conn)
    conn.close()
    return jsonify({"total_rate": round(total_rate, 1), "total_score": total_score, "total_max": total_max, "genre_stats": df_genre.to_dict(orient='records')})

@app.route('/get_graph', methods=['GET'])
def get_graph():
    conn = sqlite3.connect(DB_PATH)
    df_stats = pd.read_sql_query("SELECT timestamp, accuracy FROM session_stats", conn)
    conn.close()
    if df_stats.empty: return jsonify({"error": "No data"})
    
    plt.figure(figsize=(6, 4))
    plt.plot(df_stats['timestamp'], df_stats['accuracy'], marker='o', color='#007bff')
    plt.title("Progress"); plt.ylim(-5, 105); plt.grid(True, alpha=0.3); plt.xticks(rotation=30); plt.tight_layout()
    
    img = io.BytesIO()
    plt.savefig(img, format='png'); img.seek(0)
    plot_url = base64.b64encode(img.getvalue()).decode()
    plt.close()
    return jsonify({"plot": plot_url})

@app.route('/reset_history', methods=['POST'])
def reset_history():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM history"); cur.execute("DELETE FROM session_stats")
    conn.commit(); conn.close()
    return jsonify({"message": "Reset successful"})

if __name__ == '__main__':
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, 問題ID INTEGER, ジャンル TEXT, 回答 TEXT, 得点 INTEGER, 満点 INTEGER, mode TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS session_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, accuracy REAL)")
    conn.close()

    # Renderのポート番号に合わせる
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
