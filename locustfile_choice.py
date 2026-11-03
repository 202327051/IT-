from locust import HttpUser, task, between

class ChoiceQuestionUser(HttpUser):
    # 先生の指示で一斉に操作するため、待ち時間を0.1〜0.5秒と非常に短く設定
    wait_time = between(0.1, 0.5)

    @task(1)
    def start_choice_exam(self):
        # 1. 100人が一斉に選択式問題を取得 (/get_question)
        self.client.post("/get_question", json={
            "mode": "1",
            "exam_type": "ITパスポート",
            "session_id": "choice_class_test"
        })

    @task(2)
    def submit_choice_answer(self):
        # 2. 100人が一斉に選択肢（ア・イ・ウ・エ）を選んで回答送信 (/check_answer)
        self.client.post("/check_answer", json={
            "mode": "1",
            "id": 1,
            "answer": "ア",
            "session_id": "choice_class_test"
        })
