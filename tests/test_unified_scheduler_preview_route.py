"""
tests/test_unified_scheduler_preview_route.py

覆盖 next_doc/goal_cron_unified_scheduler_improvement_plan.md P5 第 1-2 步
新增的只读端点 GET /v1/self/unified_scheduler_preview。

沿用 tests/test_scheduling_overview_route.py 的最小 FastAPI app 模式。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_unified_scheduler_preview_route.py -q
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
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _default_cfg(project_root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        project_root=project_root,
        next_action_stale_days=7.0,
        autonomy=SimpleNamespace(
            goal_scheduling_strategy="fair_round_robin",
            fairness_aging_boost_per_day=1.0,
            fairness_aging_boost_max_days=14.0,
            resource_gating_degraded_enabled=True,
            daily_token_budget=200_000,
            frustration_threshold=999,
            frustration_blocked_threshold=999,
        ),
        cron=SimpleNamespace(skip_alert_threshold=5, degraded_max_concurrent=1),
    )


def _make_client(project_root: Path, cfg: SimpleNamespace, cron_scheduler=None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    bridge = SimpleNamespace(
        agent=SimpleNamespace(cfg=cfg),
        _cron_scheduler=cron_scheduler,
        _objective_executor=None,
    )
    app.state.http_server = SimpleNamespace(bridge=bridge, autonomous_loop=None)
    return TestClient(app)


class TestUnifiedSchedulerPreviewRoute(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.root)
        self.paths.workdir_dir.mkdir(parents=True, exist_ok=True)
        self.cfg = _default_cfg(self.root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_empty_state_returns_three_empty_channels(self):
        client = _make_client(self.root, self.cfg)
        resp = client.get("/v1/self/unified_scheduler_preview")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(set(body["channels"].keys()), {"goal", "cron", "goal_cycle"})
        for tasks in body["channels"].values():
            self.assertEqual(tasks, [])
        self.assertEqual(body["suggested_order"], [])

    def test_goal_channel_reflects_active_objectives(self):
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="parent", priority=3)
        obj = backlog.add_objective(title="child objective", parent_id=goal.id, priority=3)
        backlog.save()

        client = _make_client(self.root, self.cfg)
        resp = client.get("/v1/self/unified_scheduler_preview")
        body = resp.json()
        goal_tasks = body["channels"]["goal"]
        self.assertEqual(len(goal_tasks), 1)
        self.assertEqual(goal_tasks[0]["task_id"], obj.id)
        self.assertEqual(goal_tasks[0]["source"], "goal")

    def test_cron_and_goal_cycle_channels_separated(self):
        cs = CronScheduler(self.paths, submit_fn=lambda *a, **k: True)
        normal_job = cs.add_job(name="normal", schedule="interval:3600", task_template="x")
        normal_job.next_run_at = time.time() - 10
        cycle_job = cs.add_job(name="cycle", schedule="interval:3600",
                                task_template="advance", run_mode="goal_cycle", goal_id="g1")
        cycle_job.next_run_at = time.time() - 10
        cs.save()

        client = _make_client(self.root, self.cfg, cron_scheduler=cs)
        resp = client.get("/v1/self/unified_scheduler_preview")
        body = resp.json()
        cron_ids = {t["task_id"] for t in body["channels"]["cron"]}
        cycle_ids = {t["task_id"] for t in body["channels"]["goal_cycle"]}
        self.assertIn(normal_job.id, cron_ids)
        self.assertNotIn(cycle_job.id, cron_ids)
        self.assertIn(cycle_job.id, cycle_ids)
        self.assertNotIn(normal_job.id, cycle_ids)

    def test_suggested_order_merges_all_channels(self):
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="parent", priority=1)
        backlog.add_objective(title="child", parent_id=goal.id, priority=1)
        backlog.save()

        cs = CronScheduler(self.paths, submit_fn=lambda *a, **k: True)
        job = cs.add_job(name="due", schedule="interval:3600", task_template="x", priority=50)
        job.next_run_at = time.time() - 10
        cs.save()

        client = _make_client(self.root, self.cfg, cron_scheduler=cs)
        resp = client.get("/v1/self/unified_scheduler_preview")
        body = resp.json()
        sources = {t["source"] for t in body["suggested_order"]}
        self.assertEqual(sources, {"goal", "cron"})
        # cron job priority=50 明显高于默认 Goal priority=1，应该排最前
        self.assertEqual(body["suggested_order"][0]["source"], "cron")


if __name__ == "__main__":
    unittest.main()
