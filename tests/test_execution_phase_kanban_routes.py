"""tests/test_execution_phase_kanban_routes.py

覆盖 next_doc/goal_execution_phase_improvement_plan.md Stage C 新增的
REST 端点（api/routes.py）：
  - GET  /v1/goals/{goal_id}/execution_phase
  - POST /v1/goals/{goal_id}/execution_phase
  - POST /v1/goals/{goal_id}/execution_phase/unlock

沿用 tests/test_goal_execution_spec_kanban_routes.py 的最小 FastAPI app
模式，不拉起完整 HttpServer。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.perception.goal_backlog import load_goal_backlog
from mini_agent.storage.paths import AgentPaths


def _make_client(root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    cfg = SimpleNamespace(project_root=str(root))
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg))
    app.state.http_server = SimpleNamespace(bridge=bridge)
    return TestClient(app)


class TestExecutionPhaseKanbanRoutes(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.root)
        self.client = _make_client(self.root)
        backlog = load_goal_backlog(self.paths)
        self.goal = backlog.add_goal(title="周报生成", description="每周整理一次数据报告", priority=50)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_get_execution_phase_default(self):
        resp = self.client.get(f"/v1/goals/{self.goal.id}/execution_phase")
        self.assertEqual(resp.status_code, 200)
        phase = resp.json()["phase"]
        self.assertEqual(phase["mode"], "auto")
        self.assertFalse(phase["locked"])

    def test_set_execution_phase_implicit_lock(self):
        resp = self.client.post(f"/v1/goals/{self.goal.id}/execution_phase", json={"mode": "stable"})
        self.assertEqual(resp.status_code, 200)
        phase = resp.json()["phase"]
        self.assertEqual(phase["mode"], "stable")
        self.assertTrue(phase["locked"])

    def test_set_execution_phase_explicit_lock_false(self):
        resp = self.client.post(
            f"/v1/goals/{self.goal.id}/execution_phase", json={"mode": "explore", "lock": False}
        )
        phase = resp.json()["phase"]
        self.assertEqual(phase["mode"], "explore")
        self.assertFalse(phase["locked"])

    def test_set_execution_phase_invalid_mode_400(self):
        resp = self.client.post(f"/v1/goals/{self.goal.id}/execution_phase", json={"mode": "bogus"})
        self.assertEqual(resp.status_code, 400)

    def test_set_execution_phase_missing_mode_400(self):
        resp = self.client.post(f"/v1/goals/{self.goal.id}/execution_phase", json={})
        self.assertEqual(resp.status_code, 400)

    def test_set_execution_phase_goal_not_found_404(self):
        resp = self.client.post("/v1/goals/does-not-exist/execution_phase", json={"mode": "stable"})
        self.assertEqual(resp.status_code, 404)

    def test_unlock_execution_phase(self):
        self.client.post(f"/v1/goals/{self.goal.id}/execution_phase", json={"mode": "stable"})
        resp = self.client.post(f"/v1/goals/{self.goal.id}/execution_phase/unlock")
        self.assertEqual(resp.status_code, 200)
        phase = resp.json()["phase"]
        self.assertFalse(phase["locked"])
        self.assertEqual(phase["mode"], "stable")

    def test_unlock_execution_phase_goal_not_found_404(self):
        resp = self.client.post("/v1/goals/does-not-exist/execution_phase/unlock")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
