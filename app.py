from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
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

app = Flask(__name__, template_folder='templates')
app.config['SECRET_KEY'] = 'it-pass-secret-2024'
CORS(app)

# --- Flask-Login 設定 ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

DB_PATH = "history.db"

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

def normalize_text(text):
    text = unicodedata.normalize("NFKC", str(text)).lower()
    return text.replace(" ", "").replace("．", "").replace(".", "").replace(",", "")

def normalize_choice(text):
    text = unicodedata.normalize("NFKC", str(text)).strip().lower()
    hira_to_kata = str.maketrans("あいうえ", "アイウエ")
    text = text.translate(hira_to_kata)
    mapping = {"a": "ア", "ａ": "ア", "i": "イ", "ｉ": "イ", "u": "ウ", "ｕ": "ウ", "e": "エ", "ｅ": "エ"}
    return mapping.get(text, text)

# DB初期化
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 問題ID INTEGER, ジャンル TEXT, 回答 TEXT, 得点 INTEGER, 満点 INTEGER, mode TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS session_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, timestamp TEXT, accuracy REAL)")
    conn.close()

init_db()

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    password = generate_password_hash(data.get('password'))
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
        conn.commit()
        conn.close()
        return jsonify({"message": "登録完了！ログインしてください。"})
    except:
        return jsonify({"error": "そのユーザー名は既に使用されています"}), 400

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    user_info = sqlite3.connect(DB_PATH).execute("SELECT id, password FROM users WHERE username = ?", (data.get('username'),)).fetchone()
    if user_info and check_password_hash(user_info[1], data.get('password')):
        login_user(User(user_info[0]))
        return jsonify({"message": "ログイン成功！"})
    return jsonify({"error": "ユーザー名かパスワードが違います"}), 401

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return jsonify({"message": "ログアウトしました"})

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_question', methods=['POST'])
@login_required
def get_question():
    mode = str(request.json.get("mode"))
    file_path = 'ITパスポート.csv' if mode == '1' else '用語説明.csv'
    df = pd.read_csv(file_path, encoding="utf-8")
    q = df.sample(1).iloc[0]
    res = {"id": int(q["id"]), "genre": q["ジャンル"], "question": str(q["問題文"])}
    if mode == "2": res["question"] = f"「{q['問題文']}」について説明してください。"
    if mode == "1": res["choices"] = [c.strip() for c in str(q["選択肢"]).split('\n') if c.strip()]
    return jsonify(res)

@app.route('/check_answer', methods=['POST'])
@login_required
def check_answer():
    data = request.json
    mode, q_id, user_ans = str(data.get("mode")), data.get("id"), data.get("answer")
    file_path = 'ITパスポート.csv' if mode == '1' else '用語説明.csv'
    df = pd.read_csv(file_path, encoding="utf-8")
    q = df[df['id'] == q_id].iloc[0]
    res = {"mode": mode}

    if mode == "1":
        correct = normalize_choice(user_ans) == normalize_choice(q["正解"])
        res.update({"score": 1 if correct else 0, "max": 1, "correct": str(q["正解"]), "explanation": str(q["解説"])})
    else:
        keywords = [k.strip() for k in str(q["必須キーワード"]).replace("、", ",").split(",")]
        user_norm = normalize_text(user_ans)
        hit = [k for k in keywords if normalize_text(k) in user_norm]
        miss = [k for k in keywords if normalize_text(k) not in user_norm]
        res.update({"score": len(hit), "max": int(q["満点"]), "correct": str(q["模範解答"]), "keywords": keywords, "miss": miss})

    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO history (user_id, 問題ID, ジャンル, 回答, 得点, 満点, mode) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (current_user.id, int(q["id"]), q["ジャンル"], str(user_ans), res["score"], res["max"], mode))
    conn.commit()
    return jsonify(res)

@app.route('/get_final_stats')
@login_required
def get_final_stats():
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT SUM(得点), SUM(満点) FROM history WHERE user_id = ? AND mode = '1'", (current_user.id,)).fetchone()
    total_score, total_max = row[0] or 0, row[1] or 0
    total_rate = (total_score / total_max * 100) if total_max > 0 else 0
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%m/%d %H:%M")
    conn.execute("INSERT INTO session_stats (user_id, timestamp, accuracy) VALUES (?, ?, ?)", (current_user.id, now, total_rate))
    conn.commit()
    df_genre = pd.read_sql_query("SELECT ジャンル, SUM(得点) AS s, SUM(満点) AS m, ROUND(SUM(得点)*100.0/SUM(満点),1) AS rate FROM history WHERE user_id=? AND mode='1' GROUP BY ジャンル", conn, params=(current_user.id,))
    return jsonify({"total_rate": round(total_rate, 1), "total_score": total_score, "total_max": total_max, "genre_stats": df_genre.to_dict(orient='records')})

@app.route('/get_graph')
@login_required
def get_graph():
    df = pd.read_sql_query("SELECT timestamp, accuracy FROM session_stats WHERE user_id = ?", sqlite3.connect(DB_PATH), params=(current_user.id,))
    if df.empty: return jsonify({"error": "データなし"})
    plt.figure(figsize=(6, 4))
    plt.plot(df['timestamp'], df['accuracy'], marker='o')
    plt.title("Study Progress")
    plt.ylim(-5, 105); plt.grid(True, alpha=0.3); plt.xticks(rotation=30); plt.tight_layout()
    img = io.BytesIO()
    plt.savefig(img, format='png'); img.seek(0)
    return jsonify({"plot": base64.b64encode(img.getvalue()).decode()})

@app.route('/reset_history', methods=['POST'])
@login_required
def reset_history():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM history WHERE user_id = ?", (current_user.id,))
    conn.execute("DELETE FROM session_stats WHERE user_id = ?", (current_user.id,))
    conn.commit()
    return jsonify({"message": "リセット完了"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
