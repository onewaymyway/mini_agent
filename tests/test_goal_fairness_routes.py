"""
tests/test_goal_fairness_routes.py

覆盖 next_doc/goal_execution_fairness_improvement_plan.md P5 配套的看板集成
改造新增的 REST 端点：

- GET /v1/self/goal_fairness   汇总每个 active Goal 的调度公平性快照
  （strategy/priority/aging_boost/effective_priority/last_scheduled_at/
  last_touched_at/objective_count），只读展示用。

沿用 tests/test_self_diagnosis_feedback_routes.py 的最小 FastAPI app 模式，
不拉起完整 HttpServer。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_goal_fairness_routes.py -q
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _make_client(project_root: Path, cfg: SimpleNamespace) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg))
    app.state.http_server = SimpleNamespace(bridge=bridge)
    return TestClient(app)


def _default_cfg(project_root: Path, **autonomy_overrides) -> SimpleNamespace:
    autonomy_defaults = dict(
        goal_scheduling_strategy="fair_round_robin",
        fairness_aging_boost_per_day=1.0,
        fairness_aging_boost_max_days=14.0,
    )
    autonomy_defaults.update(autonomy_overrides)
    return SimpleNamespace(
        project_root=project_root,
        next_action_stale_days=7.0,
        autonomy=SimpleNamespace(**autonomy_defaults),
    )


class TestGoalFairnessRoute(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.root)
        self.paths.workdir_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_empty_state_returns_empty_goal_list(self):
        client = _make_client(self.root, _default_cfg(self.root))
        resp = client.get("/v1/self/goal_fairness")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["strategy"], "fair_round_robin")
        self.assertEqual(body["goals"], [])

    def test_reflects_priority_and_aging_boost(self):
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="停滞目标", priority=20)
        backlog.add_objective(title="obj", parent_id=goal.id, priority=20)

        node = backlog.get(goal.id)
        node.last_touched_at = time.time() - 10 * 86400  # 停滞 10 天
        backlog.save()

        client = _make_client(self.root, _default_cfg(self.root))
        resp = client.get("/v1/self/goal_fairness")
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()["goals"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["goal_id"], goal.id)
        self.assertEqual(row["priority"], 20)
        self.assertGreater(row["aging_boost"], 0)
        self.assertGreater(row["effective_priority"], row["priority"])
        self.assertEqual(row["objective_count"], 1)
        self.assertEqual(row["last_scheduled_at"], 0.0)

    def test_never_scheduled_sorts_before_recently_scheduled(self):
        backlog = GoalBacklog(self.paths)
        g_never = backlog.add_goal(title="从未调度", priority=50)
        g_recent = backlog.add_goal(title="刚被调度", priority=50)
        backlog.mark_scheduled(g_recent.id)

        client = _make_client(self.root, _default_cfg(self.root))
        resp = client.get("/v1/self/goal_fairness")
        rows = resp.json()["goals"]
        self.assertEqual(rows[0]["goal_id"], g_never.id)
        self.assertEqual(rows[1]["goal_id"], g_recent.id)

    def test_strategy_field_reflects_config(self):
        client = _make_client(
            self.root, _default_cfg(self.root, goal_scheduling_strategy="priority"),
        )
        resp = client.get("/v1/self/goal_fairness")
        self.assertEqual(resp.json()["strategy"], "priority")


if __name__ == "__main__":
    unittest.main()
