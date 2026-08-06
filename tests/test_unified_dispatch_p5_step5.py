"""
tests/test_unified_dispatch_p5_step5.py

对应 next_doc/goal_cron_unified_scheduler_improvement_plan.md P5 第 5 步
（灰度接入统一入口 · 子集）：

- `unified_task_scheduler.dispatch_due_cron_jobs(cron_scheduler)`：合并
  普通 cron + goal_cycle 两条通道到期任务，按 priority 降序统一触发，
  返回值语义与 `CronScheduler.tick()` 一致；`cron_scheduler=None` 时
  返回空列表，不抛异常。
- `_poll_cron_jobs()` 的 `next_run_at <= 0`（未初始化哨兵值）边界修复：
  不应被当成"已到期"。
- `AutonomousLoop._tick_passive()` 新增的
  `scheduler.unified_dispatch_enabled` 灰度开关：默认 False 时行为与
  改造前完全一致（走 `cron_scheduler.tick()`）；True 时改走
  `dispatch_due_cron_jobs()`，触发结果与 digest 记录效果等价。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_unified_dispatch_p5_step5.py -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.evolution.unified_task_scheduler import (
    CronChannelAdapter,
    GoalCycleChannelAdapter,
    dispatch_due_cron_jobs,
)
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp) -> AgentPaths:
    return AgentPaths(Path(tmp))


class TestDispatchDueCronJobs(unittest.TestCase):
    def test_none_scheduler_returns_empty(self):
        self.assertEqual(dispatch_due_cron_jobs(None), [])

    def test_triggers_due_cron_and_goal_cycle_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()

            calls: list[str] = []

            scheduler.ensure_job(job_id="sys:low", name="low", schedule="interval:3600")
            scheduler.get("sys:low").priority = 0
            scheduler.register_local_handler("sys:low", lambda job: (calls.append("low"), True)[1])

            scheduler.ensure_job(job_id="sys:high", name="high", schedule="interval:3600")
            scheduler.get("sys:high").priority = 10
            scheduler.register_local_handler("sys:high", lambda job: (calls.append("high"), True)[1])

            # 都设为已到期
            import time
            now = time.time()
            for job_id in ("sys:low", "sys:high"):
                job = scheduler.get(job_id)
                job.next_run_at = now - 1

            triggered = dispatch_due_cron_jobs(scheduler)

            self.assertEqual(set(triggered), {"sys:low", "sys:high"})
            # priority 降序：high 应先于 low 被触发
            self.assertEqual(calls, ["high", "low"])

            # 记账生效：与 tick()/trigger_job_now() 一致
            self.assertEqual(scheduler.get("sys:high").run_count, 1)
            self.assertEqual(scheduler.get("sys:low").run_count, 1)
            self.assertGreater(scheduler.get("sys:high").next_run_at, now)

    def test_uninitialized_next_run_at_not_treated_as_due(self):
        """next_run_at <= 0（哨兵值）不应被 dispatch_due_cron_jobs 误判为到期。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:fresh", name="fresh", schedule="interval:3600")
            scheduler.register_local_handler("sys:fresh", lambda job: True)

            job = scheduler.get("sys:fresh")
            job.next_run_at = 0  # 模拟哨兵值

            cron_adapter = CronChannelAdapter(scheduler)
            due_ids = {t.task_id for t in cron_adapter.poll_due()}
            self.assertNotIn("sys:fresh", due_ids)

            triggered = dispatch_due_cron_jobs(scheduler)
            self.assertNotIn("sys:fresh", triggered)
            self.assertEqual(scheduler.get("sys:fresh").run_count, 0)

    def test_failed_job_not_in_triggered_list_but_skip_count_increments(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:fail", name="fail", schedule="interval:3600")
            scheduler.register_local_handler("sys:fail", lambda job: False)

            import time
            job = scheduler.get("sys:fail")
            job.next_run_at = time.time() - 1

            triggered = dispatch_due_cron_jobs(scheduler)
            self.assertEqual(triggered, [])
            self.assertEqual(scheduler.get("sys:fail").consecutive_skip_count, 1)

    def test_goal_cycle_job_dispatched_via_goal_cycle_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:gc", name="gc", schedule="interval:3600")
            scheduler.get("sys:gc").run_mode = "goal_cycle"
            scheduler.set_goal_cycle_handler(lambda job: True)

            import time
            job = scheduler.get("sys:gc")
            job.next_run_at = time.time() - 1

            goal_cycle_adapter = GoalCycleChannelAdapter(scheduler)
            self.assertEqual(len(goal_cycle_adapter.poll_due()), 1)

            triggered = dispatch_due_cron_jobs(scheduler)
            self.assertEqual(triggered, ["sys:gc"])


class _FakeCronScheduler:
    """最小可控替身，记录 tick()/dispatch 是否被调用，不涉及真实 IO。"""

    def __init__(self, triggered: list[str]):
        self._triggered = triggered
        self.tick_called = False

    def tick(self) -> list[str]:
        self.tick_called = True
        return self._triggered


class TestAutonomousLoopUnifiedDispatchGate(unittest.TestCase):
    def _make_loop(self, *, unified_dispatch_enabled: bool, cron_scheduler):
        from mini_agent.evolution.autonomous_loop import AutonomousLoop

        cfg = SimpleNamespace(
            scheduler=SimpleNamespace(unified_dispatch_enabled=unified_dispatch_enabled),
            autonomy=SimpleNamespace(level="passive"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            loop = AutonomousLoop(
                goal_backlog=None,
                input_queue=None,
                paths=paths,
                cfg=cfg,
                cron_scheduler=cron_scheduler,
            )
            return loop

    def test_flag_off_uses_tick_directly(self):
        fake = _FakeCronScheduler(["sys:a"])
        loop = self._make_loop(unified_dispatch_enabled=False, cron_scheduler=fake)
        loop._tick_passive()
        self.assertTrue(fake.tick_called)

    def test_flag_on_uses_dispatch_due_cron_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:x", name="x", schedule="interval:3600")
            scheduler.register_local_handler("sys:x", lambda job: True)
            import time
            scheduler.get("sys:x").next_run_at = time.time() - 1

            from mini_agent.evolution.autonomous_loop import AutonomousLoop
            cfg = SimpleNamespace(
                scheduler=SimpleNamespace(unified_dispatch_enabled=True),
                autonomy=SimpleNamespace(level="passive"),
            )
            loop = AutonomousLoop(
                goal_backlog=None,
                input_queue=None,
                paths=paths,
                cfg=cfg,
                cron_scheduler=scheduler,
            )
            loop._tick_passive()
            self.assertEqual(scheduler.get("sys:x").run_count, 1)

    def test_missing_scheduler_cfg_defaults_to_tick_path(self):
        """cfg 上完全没有 scheduler 属性时（老配置对象），应静默退化为走
        tick() 路径，不抛异常。"""
        fake = _FakeCronScheduler(["sys:b"])
        from mini_agent.evolution.autonomous_loop import AutonomousLoop
        cfg = SimpleNamespace(autonomy=SimpleNamespace(level="passive"))
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            loop = AutonomousLoop(
                goal_backlog=None,
                input_queue=None,
                paths=paths,
                cfg=cfg,
                cron_scheduler=fake,
            )
            loop._tick_passive()
            self.assertTrue(fake.tick_called)


if __name__ == "__main__":
    unittest.main()
