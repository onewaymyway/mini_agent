"""
tests/test_cron_questions_api_routes.py

覆盖 next_doc/cron_async_user_feedback_mechanism_plan.md 阶段3新增的 REST 端点：
  - GET  /v1/cron_questions/pending
  - GET  /v1/cron_questions/history
  - POST /v1/cron_questions/{id}/answer

沿用 tests/test_kanban_config_routes.py 的最小 FastAPI app 模式，不拉起完整
HttpServer；数据落在临时目录的 notification/cron_questions.jsonl 上。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_cron_questions_api_routes.py -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.notification import questions_store
from mini_agent.storage.paths import AgentPaths


def _make_client(project_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    cfg = SimpleNamespace(project_root=project_root)
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg))
    app.state.http_server = SimpleNamespace(bridge=bridge)
    return TestClient(app)


class TestCronQuestionsApiRoutes(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.root)
        self.client = _make_client(self.root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_pending_endpoint_returns_only_pending_and_supports_job_filter(self):
        questions_store.append_question(self.paths, "user:job1", "要不要继续 A 方案？")
        questions_store.append_question(self.paths, "user:job2", "预算上限是多少？")
        answered = questions_store.append_question(self.paths, "user:job1", "已经回答的问题")
        questions_store.submit_answer(self.paths, answered["question_id"], "答案")

        resp = self.client.get("/v1/cron_questions/pending")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["questions"]), 2)
        self.assertFalse(body["has_more"])

        resp2 = self.client.get("/v1/cron_questions/pending", params={"job_id": "user:job1"})
        body2 = resp2.json()
        self.assertEqual(len(body2["questions"]), 1)
        self.assertEqual(body2["questions"][0]["question"], "要不要继续 A 方案？")

    def test_history_endpoint_returns_only_answered_with_full_history(self):
        q = questions_store.append_question(self.paths, "user:job1", "要不要继续 A 方案？")
        questions_store.submit_answer(self.paths, q["question_id"], "先继续")
        questions_store.submit_answer(self.paths, q["question_id"], "改主意了，先停")
        questions_store.append_question(self.paths, "user:job1", "还没回答的问题")

        resp = self.client.get("/v1/cron_questions/history")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["questions"]), 1)
        record = body["questions"][0]
        self.assertEqual(record["answer"], "改主意了，先停")
        self.assertEqual(len(record["answer_history"]), 2)

    def test_answer_endpoint_submits_and_can_be_resubmitted(self):
        q = questions_store.append_question(self.paths, "user:job1", "预算上限是多少？")
        qid = q["question_id"]

        resp = self.client.post(f"/v1/cron_questions/{qid}/answer", json={"answer": "5000"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["question"]["answer"], "5000")
        self.assertEqual(body["question"]["status"], "answered")

        # 该问题应该从 pending 列表消失，出现在 history 里
        pending = self.client.get("/v1/cron_questions/pending").json()["questions"]
        self.assertEqual(len(pending), 0)
        history = self.client.get("/v1/cron_questions/history").json()["questions"]
        self.assertEqual(len(history), 1)

        # 修改答案：同一个接口再次提交
        resp2 = self.client.post(f"/v1/cron_questions/{qid}/answer", json={"answer": "8000"})
        self.assertEqual(resp2.status_code, 200)
        body2 = resp2.json()
        self.assertEqual(body2["question"]["answer"], "8000")
        self.assertEqual(len(body2["question"]["answer_history"]), 2)

    def test_answer_endpoint_rejects_empty_answer(self):
        q = questions_store.append_question(self.paths, "user:job1", "问题")
        resp = self.client.post(f"/v1/cron_questions/{q['question_id']}/answer", json={"answer": "   "})
        self.assertEqual(resp.status_code, 400)

    def test_answer_endpoint_404_for_unknown_question(self):
        resp = self.client.post("/v1/cron_questions/cq:nope:xxxx/answer", json={"answer": "x"})
        self.assertEqual(resp.status_code, 404)

    def test_dismiss_endpoint_removes_from_pending(self):
        q = questions_store.append_question(self.paths, "user:job1", "还需要回答吗？")
        resp = self.client.post(f"/v1/cron_questions/{q['question_id']}/dismiss")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["question"]["status"], "dismissed")

        pending = self.client.get("/v1/cron_questions/pending").json()["questions"]
        self.assertEqual(len(pending), 0)
        # 忽略不算回答，不应该出现在历史记录里
        history = self.client.get("/v1/cron_questions/history").json()["questions"]
        self.assertEqual(len(history), 0)

    def test_dismiss_endpoint_rejects_already_answered_question(self):
        q = questions_store.append_question(self.paths, "user:job1", "问题")
        questions_store.submit_answer(self.paths, q["question_id"], "答案")
        resp = self.client.post(f"/v1/cron_questions/{q['question_id']}/dismiss")
        self.assertEqual(resp.status_code, 404)
        # 状态不受影响
        stored = questions_store.get_question(self.paths, q["question_id"])
        self.assertEqual(stored["status"], "answered")

    def test_dismiss_endpoint_404_for_unknown_question(self):
        resp = self.client.post("/v1/cron_questions/cq:nope:xxxx/dismiss")
        self.assertEqual(resp.status_code, 404)

    def test_dismiss_endpoint_is_idempotent(self):
        q = questions_store.append_question(self.paths, "user:job1", "问题")
        first = self.client.post(f"/v1/cron_questions/{q['question_id']}/dismiss")
        second = self.client.post(f"/v1/cron_questions/{q['question_id']}/dismiss")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)


if __name__ == "__main__":
    unittest.main()
