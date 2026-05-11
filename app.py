from flask import Flask, request, jsonify, render_template
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
app.config['SECRET_KEY'] = 'it-pass-secret-2026'
CORS(app)

# --- Flask-Login 設定 ---
login_manager = LoginManager()
login_manager.init_app(app)

DB_PATH = "history.db"

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

# -----------------------------
# 正規化・DB初期化
# -----------------------------
def normalize_text(text):
    text = unicodedata.normalize("NFKC", str(text)).lower()
    return text.replace(" ", "").replace("．", "").replace(".", "").replace(",", "")

def normalize_choice(text):
    text = unicodedata.normalize("NFKC", str(text)).strip().lower()
    hira_to_kata = str.maketrans("あいうえ", "アイウエ")
    text = text.translate(hira_to_kata)
    mapping = {"a": "ア", "ａ": "ア", "i": "イ", "ｉ": "イ", "u": "ウ", "ｕ": "ウ", "e": "エ", "ｅ": "エ"}
    return mapping.get(text, text)

# 起動時にテーブル作成
conn = sqlite3.connect(DB_PATH)
conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
conn.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 問題ID INTEGER, ジャンル TEXT, 回答 TEXT, 得点 INTEGER, 満点 INTEGER, mode TEXT, session_id TEXT)")
conn.execute("CREATE TABLE IF NOT EXISTS session_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, timestamp TEXT, accuracy REAL)")
conn.close()

# -----------------------------
# 認証ルーティング
# -----------------------------
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    # pbkdf2:sha256 方式でハッシュ化（一貫性のため）
    hashed_password = generate_password_hash(data.get('password'), method='pbkdf2:sha256')
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
        conn.commit()
        conn.close()
        return jsonify({"message": "Success"})
    except Exception as e:
        return jsonify({"error": "そのユーザー名は既に使用されています"}), 400

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    db = sqlite3.connect(DB_PATH)
    user = db.execute("SELECT id, password FROM users WHERE username = ?", (data.get('username'),)).fetchone()
    db.close()
    if user and check_password_hash(user[1], data.get('password')):
        login_user(User(user[0]))
        return jsonify({"message": "Logged in"})
    return jsonify({"error": "ユーザー名またはパスワードが正しくありません"}), 401

@app.route('/logout')
def logout():
    logout_user()
    return jsonify({"message": "Logged out"})

# -----------------------------
# メイン機能
# -----------------------------
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
    if mode == "1":
        choices_raw = str(q["選択肢"])
        res["choices"] = [c.strip() for c in choices_raw.split('\n') if c.strip()]
    return jsonify(res)

@app.route('/check_answer', methods=['POST'])
@login_required
def check_answer():
    data = request.json
    mode, q_id, user_ans = str(data.get("mode")), data.get("id"), data.get("answer")
    session_id = data.get("session_id") # フロントから送られてくる今回の挑戦ID
    
    file_path = 'ITパスポート.csv' if mode == '1' else '用語説明.csv'
    df = pd.read_csv(file_path, encoding="utf-8")
    q = df[df['id'] == q_id].iloc[0]
    
    res = {"mode": mode}
    if mode == "1":
        is_correct = normalize_choice(user_ans) == normalize_choice(q["正解"])
        current_score = 1 if is_correct else 0
        res.update({"score": current_score, "max": 1, "correct": str(q["正解"]), "explanation": str(q["解説"])})
    else:
        keywords = [k.strip() for k in str(q["必須キーワード"]).replace("、", ",").split(",")]
        user_norm = normalize_text(user_ans)
        hit = [k for k in keywords if normalize_text(k) in user_norm]
        miss = [k for k in keywords if normalize_text(k) not in user_norm]
        current_score, max_score = len(hit), int(q["満点"])
        res.update({"score": current_score, "max": max_score, "correct": str(q["模範解答"]), "keywords": keywords, "miss": miss})
    
    conn = sqlite3.connect(DB_PATH)
    # session_id を保存することで「今回の挑戦」だけを集計可能にする
    conn.execute("INSERT INTO history (user_id, 問題ID, ジャンル, 回答, 得点, 満点, mode, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (current_user.id, int(q["id"]), q["ジャンル"], str(user_ans), current_score, res["max"], mode, session_id))
    conn.commit()
    conn.close()
    return jsonify(res)

@app.route('/get_final_stats', methods=['POST'])
@login_required
def get_final_stats():
    session_id = request.json.get("session_id")
    conn = sqlite3.connect(DB_PATH)
    # session_id が一致するものだけを集計（前回の挑戦を含まない）
    row = conn.execute("SELECT SUM(得点), SUM(満点) FROM history WHERE user_id = ? AND session_id = ?", (current_user.id, session_id)).fetchone()
    total_score, total_max = row[0] or 0, row[1] or 0
    total_rate = (total_score / total_max * 100) if total_max > 0 else 0
    
    # グラフ用には全体の履歴を保存
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%m/%d %H:%M")
    conn.execute("INSERT INTO session_stats (user_id, timestamp, accuracy) VALUES (?, ?, ?)", (current_user.id, now, total_rate))
    conn.commit()
    
    df_genre = pd.read_sql_query("SELECT ジャンル, SUM(得点) AS s, SUM(満点) AS m, ROUND(SUM(得点)*100.0/SUM(満点), 1) AS rate FROM history WHERE user_id=? AND session_id=? GROUP BY ジャンル ORDER BY rate ASC", conn, params=(current_user.id, session_id))
    conn.close()
    return jsonify({"total_rate": round(total_rate, 1), "total_score": total_score, "total_max": total_max, "genre_stats": df_genre.to_dict(orient='records')})

@app.route('/get_graph')
@login_required
def get_graph():
    df = pd.read_sql_query("SELECT timestamp, accuracy FROM session_stats WHERE user_id=?", sqlite3.connect(DB_PATH), params=(current_user.id,))
    if df.empty: return jsonify({"error": "No data"})
    plt.figure(figsize=(6, 4))
    plt.plot(df['timestamp'], df['accuracy'], marker='o')
    plt.title("Progress")
    plt.ylim(-5, 105); plt.grid(True, alpha=0.3); plt.xticks(rotation=30); plt.tight_layout()
    img = io.BytesIO(); plt.savefig(img, format='png'); img.seek(0)
    plot_url = base64.b64encode(img.getvalue()).decode()
    plt.close()
    return jsonify({"plot": plot_url})

@app.route('/reset_history', methods=['POST'])
@login_required
def reset_history():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM history WHERE user_id = ?", (current_user.id,))
    conn.execute("DELETE FROM session_stats WHERE user_id = ?", (current_user.id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Reset successful"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
