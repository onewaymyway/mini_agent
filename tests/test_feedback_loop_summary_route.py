"""tests/test_feedback_loop_summary_route.py — 外部知识反馈闭环 P1-P5 只读
汇总端点 GET /v1/evolution/feedback_loop_summary 测试。

风格对齐 tests/test_notification_routes_p7.py：不拉起完整 HttpServer，只挂
router 到一个最小 FastAPI app，把 app.state.http_server 设成一个满足
routes.py 实际读取路径的轻量 duck-typed 对象。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.storage.paths import AgentPaths


def _make_app(project_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=SimpleNamespace(project_root=project_root)))
    app.state.http_server = SimpleNamespace(bridge=bridge, autonomous_loop=None)
    return TestClient(app)


class TestFeedbackLoopSummaryRoute(unittest.TestCase):
    def test_empty_project_returns_zero_values_without_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_app(Path(tmp))
            resp = client.get("/v1/evolution/feedback_loop_summary")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            for key in (
                "candidate_queue_triage", "wiki_utility_audit",
                "relevance_threshold_calibration",
                "external_trend_capability_link",
                "ecosystem_positioning_scan",
                "monthly_trend_retrospective",
            ):
                self.assertIn(key, data)
                self.assertNotIn("_error", data[key])
            self.assertEqual(data["candidate_queue_triage"]["pending"], 0)
            self.assertEqual(data["wiki_utility_audit"]["total_pages_with_stats"], 0)
            self.assertIsNone(data["monthly_trend_retrospective"]["latest_month"])

    def test_candidate_queue_status_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            p = paths.notification_novelty_candidates
            p.parent.mkdir(parents=True, exist_ok=True)
            records = [
                {"candidate_id": "a", "status": "pending"},
                {"candidate_id": "b", "status": "pending"},
                {"candidate_id": "c", "status": "expired"},
                {"candidate_id": "d", "status": "confirmed"},
            ]
            p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

            client = _make_app(Path(tmp))
            resp = client.get("/v1/evolution/feedback_loop_summary")
            data = resp.json()
            self.assertEqual(data["candidate_queue_triage"], {
                "pending": 2, "expired": 1, "confirmed": 1, "dismissed": 0,
            })

    def test_monthly_retrospective_latest_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            paths.monthly_trend_retrospective_dir.mkdir(parents=True, exist_ok=True)
            paths.monthly_trend_retrospective_path("2026-06").write_text("# 六月回顾", encoding="utf-8")
            paths.monthly_trend_retrospective_path("2026-07").write_text("# 七月回顾", encoding="utf-8")

            client = _make_app(Path(tmp))
            resp = client.get("/v1/evolution/feedback_loop_summary")
            data = resp.json()["monthly_trend_retrospective"]
            self.assertEqual(data["latest_month"], "2026-07")
            self.assertIn("七月回顾", data["latest_content"])
            self.assertEqual(sorted(data["months"]), ["2026-06", "2026-07"])


if __name__ == "__main__":
    unittest.main()
