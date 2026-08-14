"""tests/test_cycle_patrol_overview_routes.py — 覆盖
next_doc/goal_cron_cycle_proactive_patrol_and_health_overview_plan.md
能力 D 在 REST 层（`GET /v1/goals/cycle_diagnostics_overview`）的行为。

沿用 `test_cycle_diagnostics_tuning_routes.py` 的最小 FastAPI app 模式。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.config.loader import load_config
from mini_agent.evolution import cycle_patrol as cp
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.evolution.goal_cron_bridge import make_goal_recurring
from mini_agent.perception.goal_backlog import load_goal_backlog
from mini_agent.storage.paths import AgentPaths


def _make_app_client(cfg) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg, llm_helper=None))
    app.state.http_server = SimpleNamespace(bridge=bridge)
    return TestClient(app)


class TestCycleDiagnosticsOverviewRoute(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        (self.root / "agent_config.json").write_text("{}", encoding="utf-8")
        self.cfg = load_config(config_file=self.root / "agent_config.json", project_root=self.root)
        self.paths = AgentPaths(self.root)
        self.backlog = load_goal_backlog(self.paths)
        self.client = _make_app_client(self.cfg)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _add_recurring_goal(self, title="Test Goal") -> str:
        node = self.backlog.add_goal(title, "desc", priority=10)
        self.backlog.save()
        cs = CronScheduler(self.paths)
        make_goal_recurring(self.backlog, cs, node.id, "interval:3600", "do the thing")
        return node.id

    def test_empty_when_no_recurring_goals(self):
        resp = self.client.get("/v1/goals/cycle_diagnostics_overview")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["data_source"], "live")
        self.assertEqual(body["goals"], [])

    def test_live_fallback_when_no_snapshot(self):
        goal_id = self._add_recurring_goal()
        resp = self.client.get("/v1/goals/cycle_diagnostics_overview")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["data_source"], "live")
        self.assertEqual(len(body["goals"]), 1)
        self.assertEqual(body["goals"][0]["goal_id"], goal_id)
        self.assertIn(body["goals"][0]["severity"], ("red", "yellow", "green"))

    def test_snapshot_used_when_patrol_has_run(self):
        goal_id = self._add_recurring_goal()
        from mini_agent.config.models import CyclePatrolConfig
        patrol_cfg = CyclePatrolConfig(enabled=True, interval_hours=0.0)
        cp.run_cycle_patrol(self.paths, self.backlog, patrol_cfg, app_cfg=self.cfg)

        resp = self.client.get("/v1/goals/cycle_diagnostics_overview")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["data_source"], "patrol_snapshot")
        self.assertEqual(len(body["goals"]), 1)
        self.assertEqual(body["goals"][0]["goal_id"], goal_id)
        self.assertIn("generated_at", body)


if __name__ == "__main__":
    unittest.main()
