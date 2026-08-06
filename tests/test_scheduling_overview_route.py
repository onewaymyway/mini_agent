"""
tests/test_scheduling_overview_route.py

覆盖 next_doc/goal_cron_unified_scheduler_improvement_plan.md P4 新增的
只读聚合端点：

- GET /v1/self/scheduling_overview  一个视图聚合 Goal / 普通 cron /
  goal_cycle 三条执行通道当前的运行/排队/跳过状态 + 共享的 ResourceArbiter
  仲裁结果 + P1 分项消耗数字。

沿用 tests/test_goal_fairness_routes.py 的最小 FastAPI app 模式，用真实的
AgentPaths/GoalBacklog/CronScheduler 落到临时目录，不拉起完整 HttpServer。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_scheduling_overview_route.py -q
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


def _default_cfg(project_root: Path, **autonomy_overrides) -> SimpleNamespace:
    autonomy_defaults = dict(
        goal_scheduling_strategy="fair_round_robin",
        fairness_aging_boost_per_day=1.0,
        fairness_aging_boost_max_days=14.0,
        resource_gating_degraded_enabled=True,
        daily_token_budget=200_000,
        frustration_threshold=999,
        frustration_blocked_threshold=999,
    )
    autonomy_defaults.update(autonomy_overrides)
    return SimpleNamespace(
        project_root=project_root,
        next_action_stale_days=7.0,
        autonomy=SimpleNamespace(**autonomy_defaults),
        cron=SimpleNamespace(skip_alert_threshold=5, degraded_max_concurrent=1),
    )


def _make_client(project_root: Path, cfg: SimpleNamespace, autonomous_loop=None,
                  cron_scheduler=None, objective_executor=None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    bridge = SimpleNamespace(
        agent=SimpleNamespace(cfg=cfg),
        _cron_scheduler=cron_scheduler,
        _objective_executor=objective_executor,
    )
    app.state.http_server = SimpleNamespace(bridge=bridge, autonomous_loop=autonomous_loop)
    return TestClient(app)


class _FakeObjectiveExecutor:
    def __init__(self, running=1, max_concurrent=4):
        self._running = running
        self._max = max_concurrent

    def running_count(self):
        return self._running

    def effective_max_concurrent(self):
        return self._max


class TestSchedulingOverviewRoute(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.root)
        self.paths.workdir_dir.mkdir(parents=True, exist_ok=True)
        self.cfg = _default_cfg(self.root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_empty_state_returns_sane_defaults(self):
        client = _make_client(self.root, self.cfg)
        resp = client.get("/v1/self/scheduling_overview")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["goal_channel"]["objective_slots"])
        self.assertIsNone(body["goal_channel"]["queue_head_goal"])
        self.assertEqual(body["cron_channel"]["running"], 0)
        self.assertEqual(body["cron_channel"]["queued"], 0)
        self.assertEqual(body["goal_cycle_channel"]["total_count"], 0)
        self.assertEqual(body["goal_cycle_channel"]["pending_due_count"], 0)

    def test_gating_and_usage_breakdown_present_when_paths_resolvable(self):
        client = _make_client(self.root, self.cfg)
        resp = client.get("/v1/self/scheduling_overview")
        body = resp.json()
        self.assertIsNotNone(body["gating"])
        self.assertIn(body["gating"]["state"], {"full", "degraded", "blocked"})
        self.assertIsNotNone(body["usage_breakdown"])
        self.assertEqual(body["usage_breakdown"]["daily_token_budget"], 200_000)
        self.assertEqual(body["usage_breakdown"]["used_today_cron"], 0)

    def test_goal_channel_reports_objective_slots_and_queue_head(self):
        backlog = GoalBacklog(self.paths)
        older = backlog.add_goal(title="older goal", priority=10)
        newer = backlog.add_goal(title="newer goal", priority=10)
        node_older = backlog.get(older.id)
        node_older.last_scheduled_at = 100.0
        node_newer = backlog.get(newer.id)
        node_newer.last_scheduled_at = 500.0
        backlog.save()

        oe = _FakeObjectiveExecutor(running=2, max_concurrent=4)
        client = _make_client(self.root, self.cfg, objective_executor=oe)
        resp = client.get("/v1/self/scheduling_overview")
        body = resp.json()
        self.assertEqual(body["goal_channel"]["objective_slots"]["running"], 2)
        self.assertEqual(body["goal_channel"]["objective_slots"]["max"], 4)
        # 队首应该是 last_scheduled_at 更早（更久没轮到）的 Goal
        self.assertEqual(body["goal_channel"]["queue_head_goal"]["goal_id"], older.id)

    def test_cron_channel_reports_running_and_over_threshold_jobs(self):
        cs = CronScheduler(self.paths, submit_fn=lambda *a, **k: True)
        job = cs.add_job(name="test_job", schedule="interval:3600",
                          task_template="do something")
        job.consecutive_skip_count = 5
        cs.save()

        client = _make_client(self.root, self.cfg, cron_scheduler=cs)
        resp = client.get("/v1/self/scheduling_overview")
        body = resp.json()
        over = body["cron_channel"]["jobs_over_skip_threshold"]
        self.assertEqual(len(over), 1)
        self.assertEqual(over[0]["job_id"], job.id)
        self.assertEqual(over[0]["consecutive_skip_count"], 5)

    def test_goal_cycle_channel_separated_from_normal_cron(self):
        cs = CronScheduler(self.paths, submit_fn=lambda *a, **k: True)
        normal_job = cs.add_job(name="normal_job", schedule="interval:3600",
                                 task_template="msg")
        cycle_job = cs.add_job(name="goal cycle job", schedule="interval:3600",
                                task_template="advance goal", run_mode="goal_cycle",
                                goal_id="goal_1")
        cycle_job.next_run_at = time.time() - 10  # 已到期
        cs.save()

        client = _make_client(self.root, self.cfg, cron_scheduler=cs)
        resp = client.get("/v1/self/scheduling_overview")
        body = resp.json()
        self.assertEqual(body["goal_cycle_channel"]["total_count"], 1)
        self.assertEqual(body["goal_cycle_channel"]["pending_due_count"], 1)
        self.assertEqual(body["goal_cycle_channel"]["recent"][0]["job_id"], cycle_job.id)
        # 普通 job 不应该混进 goal_cycle_channel，也不应该在 cron_channel 里
        # 把 goal_cycle job 算进 running/queued（两者都不在跑）
        self.assertEqual(body["cron_channel"]["running"], 0)
        self.assertEqual(body["cron_channel"]["queued"], 0)

    def test_scheduling_mode_defaults_when_unified_arbitration_absent(self):
        # cfg 没有 scheduler 字段（旧配置）时，scheduling_mode 应该安全降级，
        # 不报错，unified_arbitration_enabled/channel_weights 都是"关闭"语义。
        client = _make_client(self.root, self.cfg)
        resp = client.get("/v1/self/scheduling_overview")
        body = resp.json()
        mode = body["scheduling_mode"]
        self.assertFalse(mode["unified_arbitration_enabled"])
        self.assertIsNone(mode["channel_weights"])
        self.assertIsNone(mode["degraded_allocation"])
        self.assertTrue(mode["adaptive_concurrency_enabled"] is False or mode["adaptive_concurrency_enabled"] is True)

    def test_scheduling_mode_reports_degraded_allocation_when_unified_and_degraded(self):
        cfg = _default_cfg(
            self.root,
            frustration_threshold=0,  # 让 gating 落入 degraded
            adaptive_concurrency_enabled=True,
        )
        cfg.scheduler = SimpleNamespace(
            unified_arbitration_enabled=True,
            channel_weights={"goal": 2.0, "cron": 1.0},
            degraded_total_slots=3,
        )
        cfg.cron.reserved_min_concurrent = 1
        client = _make_client(self.root, cfg)
        resp = client.get("/v1/self/scheduling_overview")
        body = resp.json()
        mode = body["scheduling_mode"]
        self.assertTrue(mode["unified_arbitration_enabled"])
        self.assertTrue(mode["adaptive_concurrency_enabled"])
        self.assertEqual(mode["channel_weights"], {"goal": 2.0, "cron": 1.0})
        if body["gating"]["state"] == "degraded":
            self.assertIsNotNone(mode["degraded_allocation"])
            self.assertIn("goal", mode["degraded_allocation"])
            self.assertIn("cron", mode["degraded_allocation"])

    def test_cron_channel_reports_max_concurrent_from_job_runner(self):
        from mini_agent.evolution.cron_job_runner import CronJobRunner

        cs = CronScheduler(self.paths, submit_fn=lambda *a, **k: True)
        runner = CronJobRunner(base_cfg=self.cfg, paths=self.paths, max_concurrent=3)
        cs._job_runner = runner
        cs.save()

        client = _make_client(self.root, self.cfg, cron_scheduler=cs)
        resp = client.get("/v1/self/scheduling_overview")
        body = resp.json()
        self.assertEqual(body["cron_channel"]["static_max_concurrent"], 3)
        self.assertEqual(body["cron_channel"]["max_concurrent"], 3)


if __name__ == "__main__":
    unittest.main()
