from flask import Flask, request, jsonify, render_template, Response
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
    def __init__(self, id): self.id = id

@login_manager.user_loader
def load_user(user_id): return User(user_id)

def backup_and_restore_db():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_path = os.path.join(BACKUP_DIR, "history_backup.db")
    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
        if os.path.exists(backup_path): shutil.copy(backup_path, DB_PATH)
    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
        shutil.copy(DB_PATH, backup_path)

def normalize_text(text):
    text = unicodedata.normalize("NFKC", str(text)).lower()
    return text.replace(" ", "").replace("．", "").replace(".", "").replace(",", "").replace("、", "").replace("句", "")

def normalize_choice(text):
    text = unicodedata.normalize("NFKC", str(text)).strip().lower()
    text = text.translate(str.maketrans("あいうえ", "アイウエ"))
    mapping = {"a": "ア", "ａ": "ア", "i": "イ", "ｉ": "イ", "u": "ウ", "ｕ": "ウ", "e": "エ", "ｅ": "エ"}
    return mapping.get(text, text)

def read_csv_safely(file_path):
    for enc in ["utf-8-sig", "cp932", "shift_jis", "utf-8"]:
        try: return pd.read_csv(file_path, encoding=enc)
        except: continue
    return pd.read_csv(file_path, encoding="utf-8", errors="replace")

def process_csv_file(file_path, target_user_id, raw_filename):
    if "_選択式" in raw_filename: mode, exam_name = "1", raw_filename.replace("_選択式", "").strip()
    elif "_記述式" in raw_filename: mode, exam_name = "2", raw_filename.replace("_記述式", "").strip()
    else: return False, "ファイル名に「_選択式」「_記述式」を含めてください"

    try:
        df = read_csv_safely(file_path)
        df.columns = [c.strip() for c in df.columns]
        with sqlite3.connect(DB_PATH, timeout=30) as conn:
            conn.execute("DELETE FROM questions WHERE user_id = ? AND exam_type = ? AND mode = ?", (target_user_id, exam_name, mode))
            for _, q in df.iterrows():
                prob = str(q.get("問題文", "")).strip()
                if not prob: continue
                if mode == "1":
                    conn.execute("INSERT INTO questions (user_id, exam_type, ジャンル, 問題文, ア, イ, ウ, エ, 正解, 解説, mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '1')",
                                 (target_user_id, exam_name, str(q.get("ジャンル", "一般")).strip(), prob, str(q.get("ア","")), str(q.get("イ","")), str(q.get("ウ","")), str(q.get("エ","")), str(q.get("正解","")).strip(), str(q.get("解説","")).strip()))
                else:
                    conn.execute("INSERT INTO questions (user_id, exam_type, ジャンル, 問題文, 必須キーワード, 模範解答, mode) VALUES (?, ?, '記述式', ?, ?, ?, '2')",
                                 (target_user_id, exam_name, prob, str(q.get("必須キーワード","")).strip(), str(q.get("模範解答","")).strip()))
            conn.commit()
        return True, exam_name
    except Exception as e: return False, str(e)

def init_database():
    backup_and_restore_db()
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 問題ID INTEGER, ジャンル TEXT, 回答 TEXT, 得点 INTEGER, 満点 INTEGER, mode TEXT, session_id TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS session_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, timestamp TEXT, accuracy REAL, exam_type TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS questions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, exam_type TEXT, ジャンル TEXT, 問題文 TEXT, ア TEXT, イ TEXT, ウ TEXT, エ TEXT, 正解 TEXT, 解説 TEXT, 必須キーワード TEXT, 模範解答 TEXT, mode TEXT)")
        conn.commit()
    for filepath in glob.glob(os.path.join(OFFICIAL_DIR, "*.csv")):
        process_csv_file(filepath, 0, os.path.splitext(os.path.basename(filepath))[0])

init_database()

@app.route('/')
def index(): return render_template('index.html')

@app.route('/download_template/<mode_type>')
@login_required
def download_template(mode_type):
    keyword = "選択式" if mode_type == "1" else "記述式"
    target_files = glob.glob(os.path.join(UPLOAD_DIR, f"*{keyword}*.csv"))
    if not target_files: return "テンプレートファイルが見つかりません。", 404
    df = read_csv_safely(target_files[0])
    bom_csv = b'\xef\xbb\xbf' + df.to_csv(index=False, encoding="utf-8").encode('utf-8')
    return Response(bom_csv, mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(os.path.basename(target_files[0]))}"})

@app.route('/get_exams', methods=['GET'])
@login_required
def get_exams():
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        rows = db.execute("SELECT DISTINCT exam_type, user_id FROM questions WHERE user_id = ? OR user_id = 0", (int(current_user.id),)).fetchall()
    exam_dict = {}
    for exam, uid in rows:
        if exam: exam_dict[exam] = (int(uid) == int(current_user.id))
    return jsonify({"exams": [{"name": k, "can_delete": v} for k, v in exam_dict.items()]})

@app.route('/delete_exam', methods=['POST'])
@login_required
def delete_exam():
    exam_type = request.json.get("exam_type")
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("DELETE FROM questions WHERE user_id = ? AND exam_type = ?", (int(current_user.id), exam_type))
        conn.execute("DELETE FROM session_stats WHERE user_id = ? AND exam_type = ?", (int(current_user.id), exam_type))
        conn.commit()
    backup_and_restore_db()
    return jsonify({"message": f"「{exam_type}」を削除しました。"})

@app.route('/get_available_modes', methods=['POST'])
@login_required
def get_available_modes():
    exam_type = request.json.get("exam_type")
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        modes = db.execute("SELECT DISTINCT mode FROM questions WHERE (user_id = ? OR user_id = 0) AND exam_type = ?", (int(current_user.id), exam_type)).fetchall()
    return jsonify({"modes": [m[0] for m in modes if m[0]]})

@app.route('/upload_csv', methods=['POST'])
@login_required
def upload_csv():
    file = request.files.get('file')
    if not file or not file.filename: return jsonify({"error": "ファイルエラー"}), 400
    safe_name = f"upload_{current_user.id}_{int(datetime.datetime.now().timestamp())}.csv"
    path = os.path.join(UPLOAD_DIR, safe_name)
    file.save(path)
    ok, res = process_csv_file(path, int(current_user.id), os.path.splitext(file.filename)[0])
    if ok: backup_and_restore_db(); return jsonify({"message": f"「{res}」を登録しました！"})
    return jsonify({"error": f"失敗: {res}"}), 400

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    try:
        with sqlite3.connect(DB_PATH, timeout=30) as db:
            db.execute("INSERT INTO users (username, password) VALUES (?, ?)", (data.get('username'), generate_password_hash(data.get('password'), method='pbkdf2:sha256')))
            db.commit()
        backup_and_restore_db()
        return jsonify({"message": "Success"})
    except: return jsonify({"error": "登録失敗"}), 400

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
def logout(): logout_user(); return jsonify({"message": "Logged out"})

@app.route('/get_question', methods=['POST'])
@login_required
def get_question():
    data = request.json or {}
    mode, exam, session_id = str(data.get("mode")), data.get("exam_type"), data.get("session_id")
    current_uid = int(current_user.id)
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        q = db.execute("SELECT id, ジャンル, 問題文, ア, イ, ウ, エ, exam_type FROM questions WHERE (user_id = ? OR user_id = 0) AND mode = ? AND exam_type = ? AND id NOT IN (SELECT 問題ID FROM history WHERE user_id = ? AND session_id = ? AND mode = ?) ORDER BY RANDOM() LIMIT 1", (current_uid, mode, exam, current_uid, session_id, mode)).fetchone()
        if not q:
            q = db.execute("SELECT id, ジャンル, 問題文, ア, イ, ウ, エ, exam_type FROM questions WHERE (user_id = ? OR user_id = 0) AND mode = ? AND exam_type = ? ORDER BY RANDOM() LIMIT 1", (current_uid, mode, exam)).fetchone()
    if not q: return jsonify({"error": "なし"}), 404
    res = {"id": q[0], "genre": f"{q[7]} | {q[1]}" if mode == "1" else q[7], "question": str(q[2])}
    if mode == "1": res["choices"] = [f"ア：{q[3]}", f"イ：{q[4]}", f"ウ：{q[5]}", f"エ：{q[6]}"]
    return jsonify(res)

@app.route('/check_answer', methods=['POST'])
@login_required
def check_answer():
    data = request.json or {}
    mode, q_id, user_ans, session_id = str(data.get("mode")), data.get("id"), data.get("answer"), data.get("session_id")
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        q = db.execute("SELECT ジャンル, 正解, 解説, 模範解答, 必須キーワード FROM questions WHERE id = ?", (q_id,)).fetchone()

    if mode == "1":
        score = 1 if normalize_choice(user_ans) == normalize_choice(q[1]) else 0
        res = {"score": score, "max": 1, "correct": str(q[1]), "explanation": str(q[2])}
    else:
        model_answer, raw_keywords = str(q[3] or ""), str(q[4] or "")
        keywords = [k.strip() for k in re.split(r'[,、,\s]+', raw_keywords) if k.strip()]
        norm_ans = normalize_text(user_ans)
        hit, missed = [], []
        
        if keywords:
            for kw in keywords:
                (hit if normalize_text(kw) in norm_ans else missed).append(kw)
            score = round((len(hit) / len(keywords)) * 10)
            fb = [f"キーワード一致率: {len(hit)} / {len(keywords)}"]
            if hit: fb.append(f"⭕ 含まれているキーワード: {', '.join(hit)}")
            if missed: fb.append(f"❌ 不足しているキーワード: {', '.join(missed)}")
            feedback = "\n".join(fb)
        else:
            score = 10 if norm_ans == normalize_text(model_answer) else 0
            feedback = "完全一致判定を行いました。"

        res = {"score": score, "max": 10, "correct": model_answer, "feedback": feedback}

    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("INSERT INTO history (user_id, 問題ID, ジャンル, 回答, 得点, 満点, mode, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (int(current_user.id), q_id, q[0], str(user_ans), res["score"], res["max"], mode, session_id))
        conn.commit()
    backup_and_restore_db()
    return jsonify(res)

@app.route('/get_final_stats', methods=['POST'])
@login_required
def get_final_stats():
    data = request.json or {}
    session_id, exam_type = data.get("session_id"), data.get("exam_type")
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        row = conn.execute("SELECT SUM(得点), SUM(満点) FROM history WHERE user_id = ? AND session_id = ?", (int(current_user.id), session_id)).fetchone()
        t_score, t_max = row[0] or 0, row[1] or 0
        t_rate = (t_score / t_max * 100) if t_max > 0 else 0
        if t_max > 0:
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%m/%d %H:%M")
            conn.execute("INSERT INTO session_stats (user_id, timestamp, accuracy, exam_type) VALUES (?, ?, ?, ?)", (int(current_user.id), now, t_rate, exam_type))
            conn.commit()
        df_g = pd.read_sql_query("SELECT ジャンル, SUM(得点) AS s, SUM(満点) AS m, ROUND(SUM(得点)*100.0/SUM(満点), 1) AS rate FROM history WHERE user_id=? AND session_id=? GROUP BY ジャンル", conn, params=(int(current_user.id), session_id))
    backup_and_restore_db()
    return jsonify({"total_rate": round(t_rate, 1), "total_score": t_score, "total_max": t_max, "genre_stats": df_g.to_dict(orient='records')})

@app.route('/get_graph', methods=['POST'])
@login_required
def get_graph():
    data = request.json or {}
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        df = pd.read_sql_query("SELECT timestamp, accuracy FROM session_stats WHERE user_id=? AND exam_type=? ORDER BY id ASC", conn, params=(int(current_user.id), data.get("exam_type")))
    if df.empty: return jsonify({"error": "なし"})
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(range(len(df)), df['accuracy'], marker='o')
    ax.set_xticks(list(range(len(df)))); ax.set_xticklabels(df['timestamp'], rotation=30, ha='right')
    ax.set_ylim(-5, 105); ax.grid(True, alpha=0.3); fig.tight_layout()
    img = io.BytesIO(); fig.savefig(img, format='png'); plt.close(fig)
    return jsonify({"plot": base64.b64encode(img.getvalue()).decode()})

@app.route('/reset_history', methods=['POST'])
@login_required
def reset_history():
    exam_type = request.json.get("exam_type")
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("DELETE FROM session_stats WHERE user_id = ? AND exam_type = ?", (int(current_user.id), exam_type))
        conn.commit()
    backup_and_restore_db()
    return jsonify({"message": "Reset"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
