"""
tests/test_self_diagnosis_feedback_routes.py

覆盖 next_doc/self_diagnosis_feedback_loop_deepening_plan.md 配套的看板集成
改造新增的 REST 端点/字段：

- GET   /v1/self/diagnosis_feedback   聚合 P1-P4 四路信号（改进候选清单/
  建议采纳率回看/能力快照 diff/skill 有效性），只读展示用
- PATCH /v1/goals/{goal_id}           新增 title/description 字段支持

沿用 tests/test_evolution_proposal_routes_track_i_r8.py 的最小 FastAPI app
模式，不拉起完整 HttpServer。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_self_diagnosis_feedback_routes.py -q
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.storage.paths import AgentPaths


def _make_client(project_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=SimpleNamespace(project_root=project_root)))
    app.state.http_server = SimpleNamespace(bridge=bridge)
    return TestClient(app)


class TestDiagnosisFeedbackRoute(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.root)
        self.paths.workdir_dir.mkdir(parents=True, exist_ok=True)
        self.client = _make_client(self.root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_empty_state_returns_none_and_empty_defaults(self):
        """没有任何落盘文件时，四个字段应分别是 None/None/None/[]，不报错。"""
        resp = self.client.get("/v1/self/diagnosis_feedback")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["improvement_backlog"])
        self.assertIsNone(body["suggestion_outcome_review"])
        self.assertIsNone(body["self_model_snapshot_diff"])
        self.assertEqual(body["skill_effectiveness"], [])

    def test_improvement_backlog_read_from_file(self):
        """P1 结果从 improvement_backlog.json 文件直接读取。"""
        backlog = {
            "ran_at": time.time(),
            "sources_read": ["self_maintenance", "gap_scanner"],
            "items": [
                {"subject": "tool:foo", "source": "self_maintenance", "kind": "stale_tool",
                 "summary": "foo 长期未用", "detected_at": time.time(), "score": 5.0},
            ],
        }
        self.paths.improvement_backlog_path.write_text(
            json.dumps(backlog, ensure_ascii=False), encoding="utf-8")
        resp = self.client.get("/v1/self/diagnosis_feedback")
        body = resp.json()
        self.assertEqual(len(body["improvement_backlog"]["items"]), 1)
        self.assertEqual(body["improvement_backlog"]["items"][0]["subject"], "tool:foo")

    def _append_digest(self, record: dict) -> None:
        p = self.paths.workdir_dir / "activity_digest.jsonl"
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def test_reads_latest_of_each_digest_type(self):
        """P2/P3/P4 各自从 activity_digest.jsonl 里取最新一条，忽略更早的同类记录。"""
        now = time.time()
        self._append_digest({"type": "suggestion_outcome_review", "at": now - 100,
                              "findings": [{"tool_name": "old_tool", "verdict": "worse"}]})
        self._append_digest({"type": "suggestion_outcome_review", "at": now,
                              "findings": [{"tool_name": "new_tool", "verdict": "improved"}]})
        self._append_digest({"type": "self_model_snapshot_diff", "at": now,
                              "old_at": now - 7 * 86400, "new_at": now,
                              "weak_domains_old": ["a", "b"], "weak_domains_new": ["a"],
                              "weak_count_change": -1, "deltas": []})
        self._append_digest({"type": "health_report", "at": now,
                              "skill_effectiveness": [
                                  {"skill_name": "s1", "active_sessions": 5, "baseline_sessions": 5,
                                   "active_failure_rate": 0.1, "baseline_failure_rate": 0.4,
                                   "verdict": "effective"},
                              ]})

        resp = self.client.get("/v1/self/diagnosis_feedback")
        body = resp.json()

        self.assertEqual(body["suggestion_outcome_review"]["findings"][0]["tool_name"], "new_tool")
        self.assertEqual(body["self_model_snapshot_diff"]["weak_count_change"], -1)
        self.assertEqual(len(body["skill_effectiveness"]), 1)
        self.assertEqual(body["skill_effectiveness"][0]["skill_name"], "s1")

    def test_no_project_root_returns_defaults_without_error(self):
        app = FastAPI()
        app.include_router(router)
        bridge = SimpleNamespace(agent=SimpleNamespace(cfg=SimpleNamespace(project_root=None)))
        app.state.http_server = SimpleNamespace(bridge=bridge)
        client = TestClient(app)
        resp = client.get("/v1/self/diagnosis_feedback")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["improvement_backlog"])


class TestGoalPatchTitleDescription(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.root)
        self.client = _make_client(self.root)

        from mini_agent.perception.goal_backlog import load_goal_backlog
        backlog = load_goal_backlog(self.paths)
        self.goal = backlog.add_goal(title="旧标题", description="旧描述", priority=30)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_patch_title_and_description(self):
        resp = self.client.patch(
            f"/v1/goals/{self.goal.id}",
            json={"title": "新标题", "description": "新描述"},
        )
        self.assertEqual(resp.status_code, 200)
        updated = resp.json()["goal"]
        self.assertEqual(updated["title"], "新标题")
        self.assertEqual(updated["description"], "新描述")

    def test_blank_title_is_ignored(self):
        """空白标题不应该把已有标题清空——同现有 add_goal 对 title 的必填语义保持一致。"""
        resp = self.client.patch(f"/v1/goals/{self.goal.id}", json={"title": "   "})
        self.assertEqual(resp.status_code, 200)
        updated = resp.json()["goal"]
        self.assertEqual(updated["title"], "旧标题")

    def test_empty_description_is_allowed(self):
        """描述允许被清空为空字符串（跟标题不同，描述本来就是可选字段）。"""
        resp = self.client.patch(f"/v1/goals/{self.goal.id}", json={"description": ""})
        self.assertEqual(resp.status_code, 200)
        updated = resp.json()["goal"]
        self.assertEqual(updated["description"], "")


if __name__ == "__main__":
    unittest.main()
