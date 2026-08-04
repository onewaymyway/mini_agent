"""tests/test_cron_scheduler_priority.py

覆盖 next_doc/scheduling_unification_and_kanban_visibility_improvement_plan.md
P2：CronJob.priority 字段 + CronScheduler.tick() 按优先级排序触发。

  1. 多个同时到期的 job 按 priority 降序触发（不是插入顺序）
  2. priority 缺省字段的旧 cron_jobs.json（反序列化后 priority=0）行为
     等同于改造前
  3. priority 相同时保持稳定排序（不引入随机性）
  4. add_job() 的默认 priority：goal_cycle=10，普通 message=0
  5. 内置 sys: job 加载后 priority=5
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(Path(tmp))


class TestPriorityOrdering(unittest.TestCase):
    def test_due_jobs_fire_in_priority_order(self):
        fired_order: list[str] = []

        def _submit_fn(message, initiator, meta):
            fired_order.append(meta["cron_job_id"])
            return True

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=_submit_fn)
            scheduler.load()
            # 清空内置 job，避免和自定义 job 混在同一轮 tick 里
            for jid in list(scheduler._jobs.keys()):
                if jid.startswith("sys:"):
                    scheduler._jobs[jid].enabled = False

            now = time.time()
            low = scheduler.add_job("low", "interval:60", "low task", priority=0)
            high = scheduler.add_job("high", "interval:60", "high task", priority=10)
            mid = scheduler.add_job("mid", "interval:60", "mid task", priority=5)
            for j in (low, high, mid):
                j.next_run_at = now - 1  # 全部已到期

            scheduler.tick()

            self.assertEqual(fired_order, [high.id, mid.id, low.id])

    def test_equal_priority_keeps_insertion_order(self):
        fired_order: list[str] = []

        def _submit_fn(message, initiator, meta):
            fired_order.append(meta["cron_job_id"])
            return True

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=_submit_fn)
            scheduler.load()
            for jid in list(scheduler._jobs.keys()):
                if jid.startswith("sys:"):
                    scheduler._jobs[jid].enabled = False

            now = time.time()
            first = scheduler.add_job("first", "interval:60", "t1", priority=0)
            second = scheduler.add_job("second", "interval:60", "t2", priority=0)
            for j in (first, second):
                j.next_run_at = now - 1

            scheduler.tick()

            self.assertEqual(fired_order, [first.id, second.id])

    def test_legacy_job_dict_without_priority_defaults_to_zero(self):
        legacy = {
            "id": "user:legacy1", "name": "legacy", "schedule": "interval:60",
            "task_template": "t", "enabled": True,
        }
        job = CronJob.from_dict(legacy)
        self.assertEqual(job.priority, 0)


class TestAddJobDefaultPriority(unittest.TestCase):
    def test_message_job_defaults_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = CronScheduler(_make_paths(tmp), submit_fn=None)
            scheduler.load()
            job = scheduler.add_job("t", "interval:60", "task")
            self.assertEqual(job.priority, 0)

    def test_goal_cycle_job_defaults_to_ten(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = CronScheduler(_make_paths(tmp), submit_fn=None)
            scheduler.load()
            job = scheduler.add_job(
                "goal cycle", "interval:3600", "cycle task",
                goal_id="goal-1", run_mode="goal_cycle",
            )
            self.assertEqual(job.priority, 10)

    def test_explicit_priority_overrides_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = CronScheduler(_make_paths(tmp), submit_fn=None)
            scheduler.load()
            job = scheduler.add_job("t", "interval:60", "task", priority=3)
            self.assertEqual(job.priority, 3)


class TestBuiltinJobPriority(unittest.TestCase):
    def test_builtin_sys_jobs_get_priority_five(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = CronScheduler(_make_paths(tmp), submit_fn=None)
            scheduler.load()
            sys_jobs = [j for j in scheduler._jobs.values() if j.is_system]
            self.assertTrue(sys_jobs)
            for j in sys_jobs:
                self.assertEqual(j.priority, 5)


class TestUpdatePriority(unittest.TestCase):
    def test_update_priority_changes_field_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = CronScheduler(_make_paths(tmp), submit_fn=None)
            scheduler.load()
            job = scheduler.add_job("t", "interval:60", "task")
            self.assertEqual(job.priority, 0)

            ok = scheduler.update_priority(job.id, 7)
            self.assertTrue(ok)
            self.assertEqual(scheduler.get(job.id).priority, 7)

            # 重新加载，确认已落盘
            scheduler2 = CronScheduler(_make_paths(tmp), submit_fn=None)
            scheduler2.load()
            self.assertEqual(scheduler2.get(job.id).priority, 7)

    def test_update_priority_missing_job_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = CronScheduler(_make_paths(tmp), submit_fn=None)
            scheduler.load()
            self.assertFalse(scheduler.update_priority("user:nonexistent", 5))


if __name__ == "__main__":
    unittest.main()
