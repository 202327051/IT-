from locust import HttpUser, task, between

class ITAppUser(HttpUser):
    # 待ち時間を0.5秒〜1秒と短くし、一斉アクセスに近い状態を作る
    wait_time = between(0.5, 1)

    @task
    def check_ai_answer(self):
        # 100人が一斉にAI採点（/check_answer）を叩く
        self.client.post("/check_answer", json={
            "mode": "2",
            "id": 1,
            "answer": "意匠権とは物品等のデザインを保護するための知的財産権である",
            "session_id": "class_test_session"
        })
