"""
tests/test_unified_dispatch_p5_step4.py

对应 next_doc/goal_cron_unified_scheduler_improvement_plan.md P5 第 4 步
（接管实际派发 · 部分）：

- `CronScheduler.trigger_job_now(job_id)`：与 `tick()` 共用同一份
  `_trigger_and_record()` 记账逻辑，触发成功后 `last_run_at`/`run_count`/
  `next_run_at`/`consecutive_skip_count` 与 `tick()` 触发时更新的字段
  完全一致；触发失败（handler 返回 False）时只递增
  `consecutive_skip_count`，不推进 `last_run_at`；job_id 不存在时返回
  `False`，不抛异常。
- `tick()` 自身的行为经过本轮内部重构后不变（`_trigger_and_record()`
  抽取前后对到期 job 的记账结果应完全相同）。
- `CronChannelAdapter`/`GoalCycleChannelAdapter.execute()`：正确委托到
  `trigger_job_now()`，`cron_scheduler` 为 None 或触发异常时返回 `False`
  而不抛出。
- `ObjectiveChannelAdapter.execute()` 仍然 `raise NotImplementedError`
  （本轮范围之外，见模块头部说明）。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_unified_dispatch_p5_step4.py -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.evolution.unified_task_scheduler import (
    CronChannelAdapter,
    GoalCycleChannelAdapter,
    ObjectiveChannelAdapter,
    SchedulableTask,
)
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp) -> AgentPaths:
    return AgentPaths(Path(tmp))


class TestTriggerJobNow(unittest.TestCase):
    def test_trigger_job_now_success_updates_bookkeeping_like_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:t1", name="t1", schedule="interval:3600")
            scheduler.register_local_handler("sys:t1", lambda job: True)

            job_before = scheduler.get("sys:t1")
            self.assertEqual(job_before.run_count, 0)

            ok = scheduler.trigger_job_now("sys:t1")
            self.assertTrue(ok)

            job_after = scheduler.get("sys:t1")
            self.assertEqual(job_after.run_count, 1)
            self.assertGreater(job_after.last_run_at, 0)
            self.assertGreater(job_after.next_run_at, job_after.last_run_at)
            self.assertEqual(job_after.consecutive_skip_count, 0)

    def test_trigger_job_now_failure_increments_skip_count_not_last_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:t2", name="t2", schedule="interval:3600")
            scheduler.register_local_handler("sys:t2", lambda job: False)

            ok = scheduler.trigger_job_now("sys:t2")
            self.assertFalse(ok)

            job_after = scheduler.get("sys:t2")
            self.assertEqual(job_after.run_count, 0)
            self.assertEqual(job_after.last_run_at, 0)
            self.assertEqual(job_after.consecutive_skip_count, 1)

    def test_trigger_job_now_resets_existing_skip_count_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:t3", name="t3", schedule="interval:3600")

            calls = {"n": 0}

            def _flaky(job):
                calls["n"] += 1
                return calls["n"] > 2  # 前两次失败，第三次成功

            scheduler.register_local_handler("sys:t3", _flaky)

            self.assertFalse(scheduler.trigger_job_now("sys:t3"))
            self.assertFalse(scheduler.trigger_job_now("sys:t3"))
            self.assertEqual(scheduler.get("sys:t3").consecutive_skip_count, 2)

            self.assertTrue(scheduler.trigger_job_now("sys:t3"))
            self.assertEqual(scheduler.get("sys:t3").consecutive_skip_count, 0)

    def test_trigger_job_now_unknown_job_id_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            self.assertFalse(scheduler.trigger_job_now("sys:does_not_exist"))

    def test_tick_bookkeeping_unchanged_after_refactor(self):
        # 内部重构（_trigger_and_record 抽取）前后，tick() 对到期 job 的
        # 记账结果应保持一致：触发一次成功 job，验证字段与改造前的既有
        # 预期完全相同。
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:t4", name="t4", schedule="interval:1")
            scheduler.register_local_handler("sys:t4", lambda job: True)
            job = scheduler.get("sys:t4")
            job.next_run_at = 1.0  # 强制视为已到期

            triggered = scheduler.tick()
            self.assertEqual(triggered, ["sys:t4"])
            job_after = scheduler.get("sys:t4")
            self.assertEqual(job_after.run_count, 1)
            self.assertEqual(job_after.consecutive_skip_count, 0)


class TestCronAdapterExecuteDelegatesToTriggerJobNow(unittest.TestCase):
    def test_cron_adapter_execute_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:c1", name="c1", schedule="interval:3600")
            scheduler.register_local_handler("sys:c1", lambda job: True)

            adapter = CronChannelAdapter(scheduler)
            task = SchedulableTask(source="cron", task_id="sys:c1")
            self.assertTrue(adapter.execute(task))
            self.assertEqual(scheduler.get("sys:c1").run_count, 1)

    def test_cron_adapter_execute_none_scheduler_returns_false(self):
        adapter = CronChannelAdapter(None)
        task = SchedulableTask(source="cron", task_id="sys:whatever")
        self.assertFalse(adapter.execute(task))

    def test_goal_cycle_adapter_execute_delegates(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            job = scheduler.ensure_job(
                job_id="user:gc1", name="gc1", schedule="interval:3600",
            )
            job.run_mode = "goal_cycle"
            scheduler.set_goal_cycle_handler(lambda job: True)

            adapter = GoalCycleChannelAdapter(scheduler)
            task = SchedulableTask(source="goal_cycle", task_id="user:gc1")
            self.assertTrue(adapter.execute(task))
            self.assertEqual(scheduler.get("user:gc1").run_count, 1)

    def test_goal_cycle_adapter_execute_none_scheduler_returns_false(self):
        adapter = GoalCycleChannelAdapter(None)
        task = SchedulableTask(source="goal_cycle", task_id="whatever")
        self.assertFalse(adapter.execute(task))


class TestObjectiveAdapterExecuteStillUnimplemented(unittest.TestCase):
    def test_raises_not_implemented(self):
        adapter = ObjectiveChannelAdapter(None)
        task = SchedulableTask(source="goal", task_id="g1")
        with self.assertRaises(NotImplementedError):
            adapter.execute(task)


if __name__ == "__main__":
    unittest.main()
