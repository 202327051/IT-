import sqlite3
import pandas as pd
import os

DB_PATH = "history.db"

def main():
    if not os.path.exists(DB_PATH):
        print("エラー: history.db が見つかりません。")
        return

    print("\n==========================================")
    print("      資格アプリ データベース管理ツール")
    print("==========================================")
    print(" 1: 履歴テーブルを表示する (文字化けなし/全体確認)")
    print(" 2: 登録ユーザー一覧を表示する")
    print(" 3: 【特定のユーザー】の学習履歴を消去する")
    print(" 4: 【全ユーザー】の学習履歴を消去する")
    print(" 0: 終了")
    print("==========================================")

    choice = input("番号を選んで Enter を押してください [0-4]: ").strip()

    if choice == "1":
        # 1: 履歴一覧表示
        with sqlite3.connect(DB_PATH) as conn:
            df = pd.read_sql_query("""
                SELECT h.id, u.username, h.mode, h.ジャンル, h.回答, h.得点, h.満点, h.session_id
                FROM history h
                LEFT JOIN users u ON h.user_id = u.id
                ORDER BY h.id DESC LIMIT 50
            """, conn)
        if df.empty:
            print("\n※ 学習履歴データはまだありません。")
        else:
            print("\n--- 直近の学習履歴 (最大50件) ---")
            print(df.to_string(index=False))

    elif choice == "2":
        # 2: ユーザー一覧表示
        with sqlite3.connect(DB_PATH) as conn:
            df = pd.read_sql_query("SELECT id, username FROM users", conn)
        print("\n--- 登録ユーザー一覧 ---")
        print(df.to_string(index=False))

    elif choice == "3":
        # 3: 特定ユーザーの履歴削除
        with sqlite3.connect(DB_PATH) as conn:
            users = pd.read_sql_query("SELECT id, username FROM users", conn)
            print("\n--- 現在のユーザー一覧 ---")
            print(users.to_string(index=False))
            print("--------------------------")
            
            target = input("\n削除したいユーザーの「ID(数字)」または「ユーザー名」を入力: ").strip()
            if not target:
                print("キャンセルしました。")
                return

            # 数字（ID）か文字列（ユーザー名）かを判定
            if target.isdigit():
                user_id = int(target)
                user_row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
                username = user_row[0] if user_row else f"ID:{user_id}"
            else:
                user_row = conn.execute("SELECT id FROM users WHERE username = ?", (target,)).fetchone()
                if not user_row:
                    print(f"エラー: ユーザー「{target}」が見つかりませんでした。")
                    return
                user_id = user_row[0]
                username = target

            confirm = input(f"本当に「{username} (ID: {user_id})」の学習履歴を全て削除しますか？ (y/n): ").strip().lower()
            if confirm == 'y':
                conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
                conn.execute("DELETE FROM session_stats WHERE user_id = ?", (user_id,))
                conn.commit()
                print(f"✅ ユーザー「{username}」の学習履歴を削除しました。")
            else:
                print("キャンセルしました。")

    elif choice == "4":
        # 4: 全ユーザーの履歴削除
        confirm = input("⚠️ 本当に【全ユーザー】の学習履歴を全て削除しますか？ (y/n): ").strip().lower()
        if confirm == 'y':
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM history")
                conn.execute("DELETE FROM session_stats")
                conn.commit()
            print("✅ 全ユーザーの学習履歴を削除しました。")
        else:
            print("キャンセルしました。")

    elif choice == "0":
        print("終了します。")
    else:
        print("無効な選択肢です。")

if __name__ == "__main__":
    main()
