from locust import HttpUser, task, between

class ITAppUser(HttpUser):
    # ユーザーごとの思考時間（1〜3秒）
    wait_time = between(1, 3)

    def on_start(self):
        """テスト開始時に指定アカウントで自動ログイン"""
        self.client.post("/login", json={
            "username": "202327000",
            "password": "0000"
        })

    @task
    def check_ai_answer(self):
        """記述式AI採点のテスト"""
        self.client.post("/check_answer", json={
            "mode": "2",
            "id": 1,
            "answer": "意匠権とは物品等のデザインを保護するための知的財産権である",
            "session_id": "class_test_session"
        })
