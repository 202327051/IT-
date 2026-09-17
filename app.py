from flask import Flask, request, jsonify, render_template, send_from_directory, make_response
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
import glob
import shutil
import urllib.parse
import re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

app = Flask(__name__, template_folder='templates')
app.config['SECRET_KEY'] = 'it-pass-key-2026'
CORS(app)

login_manager = LoginManager()
login_manager.init_app(app)

DB_PATH = "history.db"
BACKUP_DIR = "backups"
OFFICIAL_DIR = os.path.join("CSV", "official")
UPLOAD_DIR = os.path.join("CSV", "uploads")

os.makedirs(OFFICIAL_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

def backup_and_restore_db():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_path = os.path.join(BACKUP_DIR, "history_backup.db")

    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
        if os.path.exists(backup_path):
            shutil.copy(backup_path, DB_PATH)

    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
        shutil.copy(DB_PATH, backup_path)

def normalize_text(text):
    text = unicodedata.normalize("NFKC", str(text)).lower()
    return text.replace(" ", "").replace("．", "").replace(".", "").replace(",", "")

def normalize_choice(text):
    text = unicodedata.normalize("NFKC", str(text)).strip().lower()
    hira_to_kata = str.maketrans("あいうえ", "アイウエ")
    text = text.translate(hira_to_kata)
    mapping = {"a": "ア", "ａ": "ア", "i": "イ", "ｉ": "イ", "u": "ウ", "ｕ": "ウ", "e": "エ", "ｅ": "エ"}
    return mapping.get(text, text)

def process_csv_file(file_path, target_user_id, raw_filename):
    if "_選択式" in raw_filename:
        mode, exam_name = "1", raw_filename.replace("_選択式", "").strip()
    elif "_記述式" in raw_filename:
        mode, exam_name = "2", raw_filename.replace("_記述式", "").strip()
    else:
        return False, "ファイル名に「_選択式」「_記述式」を含めてください"

    try:
        try:
            df = pd.read_csv(file_path, encoding="utf-8-sig")
        except:
            df = pd.read_csv(file_path, encoding="shift-jis")

        df.columns = [c.strip() for c in df.columns]

        with sqlite3.connect(DB_PATH, timeout=30) as conn:
            conn.execute("DELETE FROM questions WHERE user_id = ? AND exam_type = ? AND mode = ?", (target_user_id, exam_name, mode))
            for _, q in df.iterrows():
                prob = str(q.get("問題文", "")).strip()
                if not prob: continue
                
                if mode == "1":
                    genre = str(q.get("ジャンル", "一般")).strip()
                    conn.execute("""
                        INSERT INTO questions (user_id, exam_type, ジャンル, 問題文, ア, イ, ウ, エ, 正解, 解説, mode)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '1')
                    """, (target_user_id, exam_name, genre, prob, str(q.get("ア","")), str(q.get("イ","")), str(q.get("ウ","")), str(q.get("エ","")), str(q.get("正解","")).strip(), str(q.get("解説","")).strip()))
                else:
                    genre = "記述式"
                    conn.execute("""
                        INSERT INTO questions (user_id, exam_type, ジャンル, 問題文, 必須キーワード, 模範解答, mode)
                        VALUES (?, ?, ?, ?, ?, ?, '2')
                    """, (target_user_id, exam_name, genre, prob, str(q.get("必須キーワード","")).strip(), str(q.get("模範解答","")).strip()))
            conn.commit()
        return True, exam_name
    except Exception as e:
        return False, str(e)

def init_database():
    backup_and_restore_db()
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 問題ID INTEGER, ジャンル TEXT, 回答 TEXT, 得点 INTEGER, 満点 INTEGER, mode TEXT, session_id TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS session_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, timestamp TEXT, accuracy REAL)")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, exam_type TEXT, ジャンル TEXT, 問題文 TEXT,
            ア TEXT, イ TEXT, ウ TEXT, エ TEXT, 正解 TEXT, 解説 TEXT, 必須キーワード TEXT, 模範解答 TEXT, mode TEXT
        )
        """)
        conn.commit()

    official_files = glob.glob(os.path.join(OFFICIAL_DIR, "*.csv"))
    for filepath in official_files:
        filename = os.path.basename(filepath)
        raw_name = os.path.splitext(filename)[0]
        process_csv_file(filepath, target_user_id=0, raw_filename=raw_name)

init_database()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download_template/<mode_type>', methods=['GET'])
@login_required
def download_template(mode_type):
    filename = "〇〇_選択式.csv" if mode_type == "1" else "〇〇_記述式.csv"
    response = make_response(send_from_directory(UPLOAD_DIR, filename, as_attachment=True, download_name=filename))
    encoded_filename = urllib.parse.quote(filename)
    response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded_filename}"
    return response

@app.route('/get_exams', methods=['GET'])
@login_required
def get_exams():
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        rows = db.execute("SELECT DISTINCT exam_type, user_id FROM questions WHERE user_id = ? OR user_id = 0", (current_user.id,)).fetchall()
    
    exam_dict = {}
    for exam, uid in rows:
        if not exam: continue
        if exam not in exam_dict:
            exam_dict[exam] = False
        if uid == current_user.id:
            exam_dict[exam] = True

    exams_list = [{"name": k, "can_delete": v} for k, v in exam_dict.items()]
    return jsonify({"exams": exams_list})

@app.route('/delete_exam', methods=['POST'])
@login_required
def delete_exam():
    exam_type = request.json.get("exam_type")
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("DELETE FROM questions WHERE user_id = ? AND exam_type = ?", (current_user.id, exam_type))
        conn.commit()
    backup_and_restore_db()
    return jsonify({"message": f"「{exam_type}」を削除しました。"})

@app.route('/get_available_modes', methods=['POST'])
@login_required
def get_available_modes():
    exam_type = request.json.get("exam_type")
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        modes = db.execute("SELECT DISTINCT mode FROM questions WHERE (user_id = ? OR user_id = 0) AND exam_type = ?", (current_user.id, exam_type)).fetchall()
    return jsonify({"modes": [m[0] for m in modes if m[0]]})

@app.route('/upload_csv', methods=['POST'])
@login_required
def upload_csv():
    if 'file' not in request.files or request.files['file'].filename == '':
        return jsonify({"error": "ファイルが正しくありません"}), 400

    file = request.files['file']
    original_filename = file.filename
    raw_filename = os.path.splitext(original_filename)[0]

    safe_save_name = f"upload_{current_user.id}_{int(datetime.datetime.now().timestamp())}.csv"
    file_path = os.path.join(UPLOAD_DIR, safe_save_name)
    file.save(file_path)

    success, result = process_csv_file(file_path, current_user.id, raw_filename)
    if success:
        backup_and_restore_db()
        return jsonify({"message": f"「{result}」を正常に登録しました！"})
    else:
        return jsonify({"error": f"CSV登録失敗: {result}"}), 400

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    try:
        with sqlite3.connect(DB_PATH, timeout=30) as db:
            db.execute("INSERT INTO users (username, password) VALUES (?, ?)", (data.get('username'), generate_password_hash(data.get('password'), method='pbkdf2:sha256')))
            db.commit()
        backup_and_restore_db()
        return jsonify({"message": "Success"})
    except:
        return jsonify({"error": "そのユーザー名は既に使用されています"}), 400

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        user = db.execute("SELECT id, password FROM users WHERE username = ?", (data.get('username'),)).fetchone()
    if user and check_password_hash(user[1], data.get('password')):
        login_user(User(user[0]))
        return jsonify({"message": "Logged in"})
    return jsonify({"error": "認証エラー"}), 401

@app.route('/logout')
def logout():
    logout_user()
    return jsonify({"message": "Logged out"})

@app.route('/get_question', methods=['POST'])
@login_required
def get_question():
    data = request.json
    mode, selected_exam, session_id = str(data.get("mode")), data.get("exam_type"), data.get("session_id")

    with sqlite3.connect(DB_PATH, timeout=30) as db:
        q = db.execute("""
            SELECT id, ジャンル, 問題文, ア, イ, ウ, エ, exam_type FROM questions 
            WHERE (user_id = ? OR user_id = 0) AND mode = ? AND exam_type = ?
            AND id NOT IN (SELECT 問題ID FROM history WHERE user_id = ? AND session_id = ? AND mode = ?)
            ORDER BY user_id DESC, RANDOM() LIMIT 1
        """, (current_user.id, mode, selected_exam, current_user.id, session_id, mode)).fetchone()
        
        if not q:
            q = db.execute("""
                SELECT id, ジャンル, 問題文, ア, イ, ウ, エ, exam_type FROM questions 
                WHERE (user_id = ? OR user_id = 0) AND mode = ? AND exam_type = ? 
                ORDER BY user_id DESC, RANDOM() LIMIT 1
            """, (current_user.id, mode, selected_exam)).fetchone()

    if not q: return jsonify({"error": "問題がありません"}), 404

    genre_display = f"{q[7]} | {q[1]}" if mode == "1" else q[7]
    res = {"id": q[0], "genre": genre_display, "question": str(q[2])}
    if mode == "1": res["choices"] = [f"ア：{q[3]}", f"イ：{q[4]}", f"ウ：{q[5]}", f"エ：{q[6]}"]
    return jsonify(res)

@app.route('/check_answer', methods=['POST'])
@login_required
def check_answer():
    data = request.json
    mode, q_id, user_ans, session_id = str(data.get("mode")), data.get("id"), data.get("answer"), data.get("session_id")

    with sqlite3.connect(DB_PATH, timeout=30) as db:
        q = db.execute("SELECT ジャンル, 正解, 解説, 模範解答, 必須キーワード FROM questions WHERE id = ?", (q_id,)).fetchone()

    res = {"mode": mode}
    if mode == "1":
        is_correct = normalize_choice(user_ans) == normalize_choice(q[1])
        score = 1 if is_correct else 0
        res.update({"score": score, "max": 1, "correct": str(q[1]), "explanation": str(q[2])})
    else:
        raw_kw = str(q[4]).replace('"', '').replace('「', '').replace('」', '')
        keywords = [k.strip() for k in re.split(r'[\n\r,、]+', raw_kw) if k.strip()]
        max_score = len(keywords) if len(keywords) > 0 else 1
        user_norm = normalize_text(user_ans)
        score = len([k for k in keywords if normalize_text(k) in user_norm])
        miss = [k for k in keywords if normalize_text(k) not in user_norm]
        res.update({"score": score, "max": max_score, "correct": str(q[3]), "keywords": keywords, "miss": miss})

    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("INSERT INTO history (user_id, 問題ID, ジャンル, 回答, 得点, 満点, mode, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (current_user.id, q_id, q[0], str(user_ans), score, res["max"], mode, session_id))
        conn.commit()

    backup_and_restore_db()
    return jsonify(res)

@app.route('/get_final_stats', methods=['POST'])
@login_required
def get_final_stats():
    session_id = request.json.get("session_id")
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        row = conn.execute("SELECT SUM(得点), SUM(満点) FROM history WHERE user_id = ? AND session_id = ? AND mode = '1'", (current_user.id, session_id)).fetchone()
        total_score, total_max = row[0] or 0, row[1] or 0
        total_rate = (total_score / total_max * 100) if total_max > 0 else 0

        if total_max > 0:
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%m/%d %H:%M")
            conn.execute("INSERT INTO session_stats (user_id, timestamp, accuracy) VALUES (?, ?, ?)", (current_user.id, now, total_rate))
            conn.commit()

        df_genre = pd.read_sql_query("SELECT ジャンル, SUM(得点) AS s, SUM(満点) AS m, ROUND(SUM(得点)*100.0/SUM(満点), 1) AS rate FROM history WHERE user_id=? AND session_id=? AND mode='1' GROUP BY ジャンル ORDER BY rate ASC", conn, params=(current_user.id, session_id))

    backup_and_restore_db()
    return jsonify({"total_rate": round(total_rate, 1), "total_score": total_score, "total_max": total_max, "genre_stats": df_genre.to_dict(orient='records')})

@app.route('/get_graph')
@login_required
def get_graph():
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        df = pd.read_sql_query("SELECT timestamp, accuracy FROM session_stats WHERE user_id=? ORDER BY id ASC", conn, params=(current_user.id,))

    if df.empty: return jsonify({"error": "データなし"})

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(range(len(df)), df['accuracy'], marker='o', linestyle='-', linewidth=2)
    ax.set_xticks(list(range(len(df))))
    ax.set_xticklabels(df['timestamp'], rotation=30, ha='right')
    ax.set_title("Progress")
    ax.set_ylabel("Accuracy (%)")
    ax.set_ylim(-5, 105)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    img = io.BytesIO()
    fig.savefig(img, format='png')
    img.seek(0)
    plot_url = base64.b64encode(img.getvalue()).decode()
    plt.close(fig)
    return jsonify({"plot": plot_url})

@app.route('/reset_history', methods=['POST'])
@login_required
def reset_history():
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("DELETE FROM history WHERE user_id = ?", (current_user.id,))
        conn.execute("DELETE FROM session_stats WHERE user_id = ?", (current_user.id,))
        conn.commit()
    backup_and_restore_db()
    return jsonify({"message": "Reset successful"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
