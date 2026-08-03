"""
tests/test_system_connectivity_routes.py

覆盖 next_doc/system_connectivity_gaps_and_missing_capabilities_plan.md P1
配套的看板集成改造新增的 REST 端点：

- GET /v1/self/system_connectivity  汇总 F1（决策消费率）/ F2（统一失败
  模式库）/ F3（建议反馈累积账本）/ F4（用户纠正事件）四路数据，供看板
  "🧠 自我状态"tab 的"🔗 系统关联性"区块只读展示。

沿用 tests/test_goal_fairness_routes.py 的最小 FastAPI app 模式，不拉起
完整 HttpServer。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_system_connectivity_routes.py -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.failure_pattern_store import run_failure_pattern_aggregation_once
from mini_agent.evolution.suggestion_feedback_ledger import record_outcome
from mini_agent.wiki.correction_writer import route_correction
from mini_agent.wiki.decision_consumption import DecisionConsumptionQuery, RelevantDecision, record_consumption
from mini_agent.wiki.writer import write_page


def _make_client(project_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    cfg = SimpleNamespace(project_root=project_root)
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg))
    app.state.http_server = SimpleNamespace(bridge=bridge)
    return TestClient(app)


class TestSystemConnectivityRoute(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.root)
        self.paths.ensure_wiki_dirs()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_empty_state_returns_empty_defaults(self):
        client = _make_client(self.root)
        resp = client.get("/v1/self/system_connectivity")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["decision_consumption"])
        self.assertEqual(body["failure_patterns"], [])
        self.assertEqual(body["suggestion_feedback"], {})
        self.assertEqual(body["recent_corrections"], [])

    def test_reflects_decision_consumption(self):
        decisions = [RelevantDecision(page_id="d1", title="t1", summary="s1")]
        query = DecisionConsumptionQuery(decisions=decisions, query="q")
        record_consumption(self.paths, query, referenced_page_ids=["d1"])
        record_consumption(self.paths, query, referenced_page_ids=[])

        client = _make_client(self.root)
        resp = client.get("/v1/self/system_connectivity")
        dc = resp.json()["decision_consumption"]
        self.assertEqual(dc["total_retrievals"], 2)
        self.assertEqual(dc["consumed"], 1)
        self.assertEqual(dc["consumption_rate"], 0.5)

    def test_reflects_failure_patterns(self):
        exec_path = self.paths.workdir_dir / "objective_executions.json"
        exec_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        exec_path.write_text(json.dumps({"executions": [{
            "objective_title": "部署脚本升级",
            "finished_at": 1000.0,
            "steps": [{"error_msg": "connection timed out"}],
        }]}), encoding="utf-8")
        run_failure_pattern_aggregation_once(self.paths)

        client = _make_client(self.root)
        resp = client.get("/v1/self/system_connectivity")
        patterns = resp.json()["failure_patterns"]
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0]["root_cause_tag"], "timeout")

    def test_reflects_suggestion_feedback_ledger(self):
        record_outcome(self.paths, "cat_a", "rejected")
        record_outcome(self.paths, "cat_a", "rejected")
        record_outcome(self.paths, "cat_a", "rejected")

        client = _make_client(self.root)
        resp = client.get("/v1/self/system_connectivity")
        ledger = resp.json()["suggestion_feedback"]
        self.assertIn("cat_a", ledger)
        self.assertEqual(ledger["cat_a"]["rejected"], 3)
        self.assertEqual(ledger["cat_a"]["accepted"], 0)

    def test_reflects_recent_corrections(self):
        write_page(self.paths, page_id="decision_x", page_type="decision", body="内容")
        route_correction(self.paths, "decision_x", "这个决策理由不对")

        client = _make_client(self.root)
        resp = client.get("/v1/self/system_connectivity")
        corrections = resp.json()["recent_corrections"]
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0]["page_id"], "decision_x")
        self.assertTrue(corrections[0]["marked_stale"])

    def test_missing_project_root_returns_defaults_not_error(self):
        app = FastAPI()
        app.include_router(router)
        bridge = SimpleNamespace(agent=SimpleNamespace(cfg=SimpleNamespace(project_root=None)))
        app.state.http_server = SimpleNamespace(bridge=bridge)
        client = TestClient(app)
        resp = client.get("/v1/self/system_connectivity")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["failure_patterns"], [])


if __name__ == "__main__":
    unittest.main()
