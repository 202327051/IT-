from flask import Flask, request, jsonify, render_template, Response, send_from_directory # 👈 send_from_directory を追加
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
app.config['SECRET_KEY'] = 'it-pass-key-2026'
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

def init_database():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 問題ID INTEGER, ジャンル TEXT, 回答 TEXT, 得点 INTEGER, 満点 INTEGER, mode TEXT, session_id TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS session_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, timestamp TEXT, accuracy REAL)")
    
    conn.execute("""
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        exam_type TEXT,
        ジャンル TEXT,
        問題文 TEXT,
        ア TEXT,
        イ TEXT,
        ウ TEXT,
        エ TEXT,
        正解 TEXT,
        解説 TEXT,
        mode TEXT
    )
    """)
    conn.commit()
    conn.close()

init_database()

# -----------------------------
# 【修正】実際のCSVファイルをダウンロードする機能（方法２）
# -----------------------------
@app.route('/download_template/<mode_type>', methods=['GET'])
@login_required
def download_template(mode_type):
    # CSVファイルが置かれているディレクトリパス（templates/csv）
    directory = os.path.join(app.root_path, 'templates', 'csv')
    
    if mode_type == "1":
        filename = "〇〇_過去問.csv"  # 👈 実際のファイル名に書き換えてください
    else:
        filename = "〇〇_用語.csv"    # 👈 実際のファイル名に書き換えてください
        
    # 指定フォルダから実際のファイルを安全にダウンロード配信
    return send_from_directory(directory, filename, as_attachment=True)

# -----------------------------
# ログインユーザーが登録した資格一覧の取得
# -----------------------------
@app.route('/get_exams', methods=['GET'])
@login_required
def get_exams():
    db = sqlite3.connect(DB_PATH, timeout=30)
    exams = db.execute("SELECT DISTINCT exam_type FROM questions WHERE user_id = ?", (current_user.id,)).fetchall()
    db.close()
    exam_list = [e[0] for e in exams]
    return jsonify({"exams": exam_list})

# -----------------------------
# CSVファイル アップロード機能（アンダースコア名判定版）
# -----------------------------
@app.route('/upload_csv', methods=['POST'])
@login_required
def upload_csv():
    if 'file' not in request.files:
        return jsonify({"error": "ファイルがありません"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "ファイル名が空です"}), 400

    raw_filename = os.path.splitext(file.filename)[0] # 拡張子なしのファイル名
    
    # ファイル名から資格名とモードを自動判定
    if "_過去問" in raw_filename:
        mode = "1"
        exam_name = raw_filename.split("_過去問")[0]
    elif "_用語" in raw_filename:
        mode = "2"
        exam_name = raw_filename.split("_用語")[0]
    else:
        return jsonify({"error": "ファイル名が正しくありません。末尾に「_過去問」または「_用語」をつけてください。\n例: ITパスポート_用語.csv"}), 400

    if file and file.filename.endswith('.csv'):
        try:
            # Shift-JIS / UTF-8 両方のCSVに対応できるようにバイナリからデコード
            file_bytes = file.stream.read()
            try:
                stream = io.StringIO(file_bytes.decode("utf-8-sig"), newline=None)
                df = pd.read_csv(stream)
            except:
                stream = io.StringIO(file_bytes.decode("shift-jis"), newline=None)
                df = pd.read_csv(stream)
            
            df.columns = [c.strip() for c in df.columns]
            
            conn = sqlite3.connect(DB_PATH, timeout=30)
            # 同じユーザー名・同じ資格・同じモードの既存データを上書き（一度削除）
            conn.execute("DELETE FROM questions WHERE user_id = ? AND exam_type = ? AND mode = ?", (current_user.id, exam_name, mode))
            
            for idx, q in df.iterrows():
                genre = str(q.get("ジャンル", "一般")).strip()
                prob = str(q.get("問題文", "")).strip()
                if not prob:
                    continue # 空行はスキップ
                
                if mode == "1":
                    ans = str(q.get("正解", "")).strip()
                    exp = str(q.get("解説", "")).strip()
                    conn.execute("""
                        INSERT INTO questions (user_id, exam_type, ジャンル, 問題文, ア, イ, ウ, エ, 正解, 解説, mode)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '1')
                    """, (current_user.id, exam_name, genre, prob, str(q.get("ア","")), str(q.get("イ","")), str(q.get("ウ","")), str(q.get("エ","")), ans, exp))
                else:
                    ans = str(q.get("模範解答", "")).strip()
                    kw = str(q.get("必須キーワード", "")).strip()
                    conn.execute("""
                        INSERT INTO questions (user_id, exam_type, ジャンル, 問題文, 正解, 解説, mode)
                        VALUES (?, ?, ?, ?, ?, ?, '2')
                    """, (current_user.id, exam_name, genre, prob, ans, kw, '2'))
                    
            conn.commit()
            conn.close()
            mode_str = "過去問モード" if mode == "1" else "用語説明モード"
            return jsonify({"message": f"「{exam_name}」を{mode_str}用として正常に登録しました！"})
        except Exception as e:
            return jsonify({"error": f"CSVの解析に失敗しました: {str(e)}\nテンプレートCSVをダウンロードして、形式を合わせてください。"}), 500

    return jsonify({"error": "CSVファイルをアップロードしてください"}), 400

# -----------------------------
# 認証ルーティング
# -----------------------------
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    hashed_password = generate_password_hash(data.get('password'), method='pbkdf2:sha256')
    try:
        db = sqlite3.connect(DB_PATH, timeout=30)
        db.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
        db.commit()
        db.close()
        return jsonify({"message": "Success"})
    except:
        return jsonify({"error": "そのユーザー名は既に使用されています"}), 400

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    db = sqlite3.connect(DB_PATH, timeout=30)
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
# 学習機能ルーティング
# -----------------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_question', methods=['POST'])
@login_required
def get_question():
    data = request.json
    mode = str(data.get("mode"))
    selected_exam = data.get("exam_type")
    
    if not selected_exam:
        return jsonify({"error": "資格が選択されていません"}), 400

    db = sqlite3.connect(DB_PATH, timeout=30)
    q = db.execute("""
        SELECT id, ジャンル, 問題文, ア, イ, ウ, エ, exam_type 
        FROM questions 
        WHERE user_id = ? AND mode = ? AND exam_type = ?
        ORDER BY RANDOM() LIMIT 1
    """, (current_user.id, mode, selected_exam)).fetchone()
    db.close()
    
    if not q:
        return jsonify({"error": f"問題データが見つかりません"}), 404
        
    question_text = q[2]
    if mode == "2":
        question_text = f"「{question_text}」について説明してください。"

    res = {"id": q[0], "genre": f"{q[7]} | {q[1]}", "question": str(question_text)}
    if mode == "1":
        res["choices"] = [f"ア：{q[3]}", f"イ：{q[4]}", f"ウ：{q[5]}", f"エ：{q[6]}"]
    return jsonify(res)

@app.route('/check_answer', methods=['POST'])
@login_required
def check_answer():
    data = request.json
    mode, q_id, user_ans, session_id = str(data.get("mode")), data.get("id"), data.get("answer"), data.get("session_id")
    
    db = sqlite3.connect(DB_PATH, timeout=30)
    q = db.execute("SELECT ジャンル, 正解, 解説 FROM questions WHERE id = ?", (q_id,)).fetchone()
    db.close()

    res = {"mode": mode}
    if mode == "1":
        is_correct = normalize_choice(user_ans) == normalize_choice(q[1])
        current_score = 1 if is_correct else 0
        res.update({"score": current_score, "max": 1, "correct": str(q[1]), "explanation": str(q[2])})
    else:
        raw_kw = str(q[2]).replace('"', '').replace('「', '').replace('」', '').replace("、", ",")
        keywords = [k.strip() for k in raw_kw.split(",") if k.strip()]
        
        user_norm = normalize_text(user_ans)
        hit = [k for k in keywords if normalize_text(k) in user_norm]
        miss = [k for k in keywords if normalize_text(k) not in user_norm]
        current_score, max_score = len(hit), len(keywords)
        res.update({"score": current_score, "max": max_score, "correct": str(q[1]), "keywords": keywords, "miss": miss})

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("INSERT INTO history (user_id, 問題ID, ジャンル, 回答, 得点, 満点, mode, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (current_user.id, q_id, q[0], str(user_ans), current_score, res["max"], mode, session_id))
    conn.commit()
    conn.close()
    return jsonify(res)

@app.route('/get_final_stats', methods=['POST'])
@login_required
def get_final_stats():
    session_id = request.json.get("session_id")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    row = conn.execute("SELECT SUM(得点), SUM(満点) FROM history WHERE user_id = ? AND session_id = ? AND mode = '1'", (current_user.id, session_id)).fetchone()
    total_score, total_max = row[0] or 0, row[1] or 0
    total_rate = (total_score / total_max * 100) if total_max > 0 else 0

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%m/%d %H:%M")
    conn.execute("INSERT INTO session_stats (user_id, timestamp, accuracy) VALUES (?, ?, ?)", (current_user.id, now, total_rate))
    conn.commit()

    df_genre = pd.read_sql_query("SELECT ジャンル, SUM(得点) AS s, SUM(満点) AS m, ROUND(SUM(得点)*100.0/SUM(満点), 1) AS rate FROM history WHERE user_id=? AND session_id=? AND mode='1' GROUP BY ジャンル ORDER BY rate ASC", conn, params=(current_user.id, session_id))
    conn.close()
    return jsonify({"total_rate": round(total_rate, 1), "total_score": total_score, "total_max": total_max, "genre_stats": df_genre.to_dict(orient='records')})

@app.route('/get_graph')
@login_required
def get_graph():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    df = pd.read_sql_query("SELECT timestamp, accuracy FROM session_stats WHERE user_id=? ORDER BY id ASC", conn, params=(current_user.id,))
    conn.close()
    
    if df.empty: 
        return jsonify({"error": "No data"})
    
    plt.figure(figsize=(6, 4))
    x_indices = range(len(df))
    plt.plot(x_indices, df['accuracy'], marker='o', linestyle='-', linewidth=2)
    plt.xticks(x_indices, df['timestamp'], rotation=30, ha='right')
    plt.title("Progress")
    plt.ylabel("Accuracy (%)")
    plt.ylim(-5, 105)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    
    img = io.BytesIO()
    plt.savefig(img, format='png')
    img.seek(0)
    plot_url = base64.b64encode(img.getvalue()).decode()
    plt.close()
    return jsonify({"plot": plot_url})

@app.route('/reset_history', methods=['POST'])
@login_required
def reset_history():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("DELETE FROM history WHERE user_id = ?", (current_user.id,))
    conn.execute("DELETE FROM session_stats WHERE user_id = ?", (current_user.id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Reset successful"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
