import sqlite3
import os

DB_PATH = "history.db"

def main():
    if not os.path.exists(DB_PATH):
        print("Error: history.db not found.")
        return

    print("\n==========================================")
    print("      DB Management Tool")
    print("==========================================")
    print(" 1: Show History List (学習履歴一覧)")
    print(" 2: Show Users List (ユーザー一覧)")
    print(" 3: Delete Specific User History (特定ユーザー削除)")
    print(" 4: Delete ALL History (全履歴削除)")
    print(" 0: Exit (終了)")
    print("==========================================")

    choice = input("Select [0-4]: ").strip()

    if choice == "1":
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            rows = cursor.execute("""
                SELECT h.id, u.username, h.mode, h.ジャンル, h.得点, h.満点, h.session_id
                FROM history h
                LEFT JOIN users u ON h.user_id = u.id
                ORDER BY h.id DESC LIMIT 30
            """).fetchall()
        
        if not rows:
            print("\n* No history found.")
        else:
            print("\n[ID | User | Mode | Genre | Score/Max | Session]")
            print("-" * 50)
            for r in rows:
                print(f"{r[0]} | {r[1]} | Mode:{r[2]} | {r[3]} | {r[4]}/{r[5]} | {r[6]}")

    elif choice == "2":
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            rows = cursor.execute("SELECT id, username FROM users").fetchall()
        print("\n[ID | Username]")
        print("-" * 20)
        for r in rows:
            print(f"{r[0]} | {r[1]}")

    elif choice == "3":
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            rows = cursor.execute("SELECT id, username FROM users").fetchall()
            print("\n--- User List ---")
            for r in rows:
                print(f"ID: {r[0]} | User: {r[1]}")
            
            target = input("\nEnter User ID to DELETE history: ").strip()
            if not target.isdigit():
                print("Invalid ID.")
                return
            
            user_id = int(target)
            conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM session_stats WHERE user_id = ?", (user_id,))
            conn.commit()
            print(f"\nSUCCESS: Deleted history for User ID: {user_id}")

    elif choice == "4":
        confirm = input("Are you sure to delete ALL history? (y/n): ").strip().lower()
        if confirm == 'y':
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM history")
                conn.execute("DELETE FROM session_stats")
                conn.commit()
            print("\nSUCCESS: All history deleted.")

if __name__ == "__main__":
    main()
