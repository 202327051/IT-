from locust import HttpUser, task, between

class ChoiceQuestionUser(HttpUser):
    # 各ユーザーの思考時間（0.1〜0.5秒の高速アクセスを想定）
    wait_time = between(0.1, 0.5)

    def on_start(self):
        """テスト開始時に指定アカウントで自動ログイン"""
        self.client.post("/login", json={
            "username": "202327000",
            "password": "0000"
        })

    @task(1)
    def start_choice_exam(self):
        """問題取得リクエスト"""
        self.client.post("/get_question", json={
            "mode": "1",
            "exam_type": "ITパスポート",
            "session_id": "choice_class_test"
        })

    @task(2)
    def submit_choice_answer(self):
        """回答送信リクエスト"""
        self.client.post("/check_answer", json={
            "mode": "1",
            "id": 1,
            "answer": "ア",
            "session_id": "choice_class_test"
        })
