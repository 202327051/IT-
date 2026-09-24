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
import json
import time
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Google Gemini API ライブラリ
from google import genai
from google.genai import types

app = Flask(__name__, template_folder='templates')
app.config['SECRET_KEY'] = 'it-pass-key-2026'
CORS(app)

# --- Gemini APIの初期化 ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

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

def read_csv_safely(file_path):
    encodings = ["utf-8-sig", "cp932", "shift_jis", "utf-8"]
    for enc in encodings:
        try:
            return pd.read_csv(file_path, encoding=enc)
        except (UnicodeDecodeError, Exception):
            continue
    return pd.read_csv(file_path, encoding="utf-8", errors="replace")

def process_csv_file(file_path, target_user_id, raw_filename):
    if "_選択式" in raw_filename:
        mode, exam_name = "1", raw_filename.replace("_選択式", "").strip()
    elif "_記述式" in raw_filename:
        mode, exam_name = "2", raw_filename.replace("_記述式", "").strip()
    else:
        return False, "ファイル名に「_選択式」「_記述式」を含めてください"

    try:
        df = read_csv_safely(file_path)
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
        conn.execute("CREATE TABLE IF NOT EXISTS session_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, timestamp TEXT, accuracy REAL, exam_type TEXT)")
        
        try:
            conn.execute("ALTER TABLE session_stats ADD COLUMN exam_type TEXT")
        except sqlite3.OperationalError:
            pass

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
    keyword = "選択式" if mode_type == "1" else "記述式"
    target_files = glob.glob(os.path.join(UPLOAD_DIR, f"*{keyword}*.csv"))
    
    if not target_files:
        return f"テンプレートファイル（*{keyword}*.csv）が見つかりません。CSV/uploads/ フォルダを確認してください。", 404
        
    file_path = target_files[0]
    filename = os.path.basename(file_path)

    df = read_csv_safely(file_path)
    csv_string = df.to_csv(index=False, encoding="utf-8")
    bom_csv_bytes = b'\xef\xbb\xbf' + csv_string.encode('utf-8')
    encoded_filename = urllib.parse.quote(filename)

    return Response(
        bom_csv_bytes,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Type": "text/csv; charset=utf-8"
        }
    )

@app.route('/get_exams', methods=['GET'])
@login_required
def get_exams():
    current_uid = int(current_user.id)
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        rows = db.execute("SELECT DISTINCT exam_type, user_id FROM questions WHERE user_id = ? OR user_id = 0", (current_uid,)).fetchall()
    
    exam_dict = {}
    for exam, uid in rows:
        if not exam: continue
        if exam not in exam_dict:
            exam_dict[exam] = False
        if int(uid) == current_uid:
            exam_dict[exam] = True

    exams_list = [{"name": k, "can_delete": v} for k, v in exam_dict.items()]
    return jsonify({"exams": exams_list})

@app.route('/delete_exam', methods=['POST'])
@login_required
def delete_exam():
    exam_type = request.json.get("exam_type")
    current_uid = int(current_user.id)
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("DELETE FROM questions WHERE user_id = ? AND exam_type = ?", (current_uid, exam_type))
        conn.execute("DELETE FROM session_stats WHERE user_id = ? AND exam_type = ?", (current_uid, exam_type))
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
    if 'file' not in request.files or request.files['file'].filename == '':
        return jsonify({"error": "ファイルが正しくありません"}), 400

    file = request.files['file']
    original_filename = file.filename
    raw_filename = os.path.splitext(original_filename)[0]

    current_uid = int(current_user.id)
    safe_save_name = f"upload_{current_uid}_{int(datetime.datetime.now().timestamp())}.csv"
    file_path = os.path.join(UPLOAD_DIR, safe_save_name)
    file.save(file_path)

    success, result = process_csv_file(file_path, current_uid, raw_filename)
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
    current_uid = int(current_user.id)

    with sqlite3.connect(DB_PATH, timeout=30) as db:
        q = db.execute("""
            SELECT id, ジャンル, 問題文, ア, イ, ウ, エ, exam_type FROM questions 
            WHERE (user_id = ? OR user_id = 0) AND mode = ? AND exam_type = ?
            AND id NOT IN (SELECT 問題ID FROM history WHERE user_id = ? AND session_id = ? AND mode = ?)
            ORDER BY user_id DESC, RANDOM() LIMIT 1
        """, (current_uid, mode, selected_exam, current_uid, session_id, mode)).fetchone()
        
        if not q:
            q = db.execute("""
                SELECT id, ジャンル, 問題文, ア, イ, ウ, エ, exam_type FROM questions 
                WHERE (user_id = ? OR user_id = 0) AND mode = ? AND exam_type = ? 
                ORDER BY user_id DESC, RANDOM() LIMIT 1
            """, (current_uid, mode, selected_exam)).fetchone()

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
    current_uid = int(current_user.id)

    with sqlite3.connect(DB_PATH, timeout=30) as db:
        q = db.execute("SELECT ジャンル, 正解, 解説, 模範解答, 必須キーワード, 問題文 FROM questions WHERE id = ?", (q_id,)).fetchone()

    res = {"mode": mode}
    if mode == "1":
        # 選択式（従来通り）
        is_correct = normalize_choice(user_ans) == normalize_choice(q[1])
        score = 1 if is_correct else 0
        res.update({"score": score, "max": 1, "correct": str(q[1]), "explanation": str(q[2])})
    else:
        # 記述式（Gemini APIによる自動リトライ採点）
        question_text = str(q[5])
        model_answer = str(q[3])
        
        prompt = f"""[問題]:{question_text}
[模範解答]:{model_answer}
[回答]:{user_ans}

意味が合っていれば正解とし、10点満点で採点してJSONのみ出力。
JSON: {{"score": (0-10の整数), "feedback": "簡潔な解説"}}"""

        score = 0
        feedback = ""
        max_retries = 10  # 503混雑や429制限が出ても成功するまで最大10回試行

        for attempt in range(max_retries):
            try:
                response = ai_client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        max_output_tokens=150,
                        temperature=0.1
                    )
                )
                ai_res = json.loads(response.text)
                score = int(ai_res.get("score", 0))
                feedback = ai_res.get("feedback", "")
                break  # 採点成功したらループ終了
            except Exception as e:
                err_str = str(e)
                # 503(混雑) または 429(レート制限/一時上限) の場合は待機時間を徐々に伸ばして再試行
                if ("503" in err_str or "429" in err_str or "UNAVAILABLE" in err_str) and attempt < max_retries - 1:
                    wait_seconds = min(1.0 * (1.5 ** attempt), 8.0)  # 1s, 1.5s, 2.2s, 3.3s ... (最大8秒)
                    time.sleep(wait_seconds)
                else:
                    feedback = f"AI採点エラー: {err_str}"
                    break

        res.update({
            "score": score,
            "max": 10,
            "correct": model_answer,
            "feedback": feedback
        })

    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.execute("INSERT INTO history (user_id, 問題ID, ジャンル, 回答, 得点, 満点, mode, session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (current_uid, q_id, q[0], str(user_ans), score, res["max"], mode, session_id))
        conn.commit()

    backup_and_restore_db()
    return jsonify(res)

@app.route('/get_final_stats', methods=['POST'])
@login_required
def get_final_stats():
    data = request.json
    session_id = data.get("session_id")
    exam_type = data.get("exam_type")
    current_uid = int(current_user.id)

    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        row = conn.execute("SELECT SUM(得点), SUM(満点) FROM history WHERE user_id = ? AND session_id = ? AND mode = '1'", (current_uid, session_id)).fetchone()
        total_score, total_max = row[0] or 0, row[1] or 0
        total_rate = (total_score / total_max * 100) if total_max > 0 else 0

        if total_max > 0:
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%m/%d %H:%M")
            conn.execute("INSERT INTO session_stats (user_id, timestamp, accuracy, exam_type) VALUES (?, ?, ?, ?)", (current_uid, now, total_rate, exam_type))
            conn.commit()

        df_genre = pd.read_sql_query("SELECT ジャンル, SUM(得点) AS s, SUM(満点) AS m, ROUND(SUM(得点)*100.0/SUM(満点), 1) AS rate FROM history WHERE user_id=? AND session_id=? AND mode='1' GROUP BY ジャンル ORDER BY rate ASC", conn, params=(current_uid, session_id))

    backup_and_restore_db()
    return jsonify({"total_rate": round(total_rate, 1), "total_score": total_score, "total_max": total_max, "genre_stats": df_genre.to_dict(orient='records')})

@app.route('/get_graph', methods=['POST'])
@login_required
def get_graph():
    exam_type = request.json.get("exam_type")
    current_uid = int(current_user.id)
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        df = pd.read_sql_query("SELECT timestamp, accuracy FROM session_stats WHERE user_id=? AND exam_type=? ORDER BY id ASC", conn, params=(current_uid, exam_type))

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
    exam_type = request.json.get("exam_type")
    current_uid = int(current_user.id)
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        if exam_type:
            conn.execute("DELETE FROM session_stats WHERE user_id = ? AND exam_type = ?", (current_uid, exam_type))
        else:
            conn.execute("DELETE FROM history WHERE user_id = ?", (current_uid,))
            conn.execute("DELETE FROM session_stats WHERE user_id = ?", (current_uid,))
        conn.commit()
    backup_and_restore_db()
    return jsonify({"message": "Reset successful"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
