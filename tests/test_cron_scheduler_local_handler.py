"""tests/test_cron_scheduler_local_handler.py

覆盖 §10.1（P3 实施记录）描述的"本地回调"执行路径：
`CronScheduler.register_local_handler` + `ensure_job`。

背景：next_doc/watchlist_notification_goal_design.md §10.1 声称 P3 已经
给 CronScheduler 新增了这两个方法，但复查代码发现 report_tiers.py 里的
`ensure_report_tier_jobs()` 调用的 `cron_scheduler.ensure_job(...)` /
`cron_scheduler.register_local_handler(...)` 在 CronScheduler 类里并不
存在——`tests/test_report_tiers.py` 原本就会因 AttributeError 失败。
本文件补齐这两个方法本身的直接测试，覆盖：
  1. ensure_job：不存在时创建、已存在时返回原有 job（不覆盖）
  2. register_local_handler：注册后 tick() 触发时优先走本地回调，
     不经过 submit_fn/job_runner，且返回值影响 last_run_at 是否推进
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(Path(tmp))


class TestEnsureJob(unittest.TestCase):
    def test_creates_job_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            job = scheduler.ensure_job(
                job_id="sys:test_job", name="测试", schedule="interval:60",
            )
            self.assertEqual(job.id, "sys:test_job")
            self.assertIsNotNone(scheduler.get("sys:test_job"))

    def test_does_not_overwrite_existing_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:test_job", name="原始名字", schedule="interval:60")
            scheduler.disable("sys:test_job")
            # 第二次调用不应该把用户已经 disable 的 job 重新 enable，也不应该
            # 改名字——"缺失才补，已存在不覆盖"（§8 开放项 2）。
            job = scheduler.ensure_job(job_id="sys:test_job", name="新名字", schedule="interval:120")
            self.assertEqual(job.name, "原始名字")
            self.assertFalse(job.enabled)
            self.assertEqual(job.schedule, "interval:60")


class TestRegisterLocalHandler(unittest.TestCase):
    def test_local_handler_fires_instead_of_submit_fn(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            submit_calls = []

            def _submit(msg, initiator, meta):
                submit_calls.append(msg)
                return True

            scheduler = CronScheduler(paths, submit_fn=_submit)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:local_test", name="本地", schedule="interval:1")

            handler_calls = []

            def _handler(job):
                handler_calls.append(job.id)
                return True

            scheduler.register_local_handler("sys:local_test", _handler)

            job = scheduler.get("sys:local_test")
            job.next_run_at = time.time() - 1  # 强制到期

            triggered = scheduler.tick()
            self.assertIn("sys:local_test", triggered)
            self.assertEqual(handler_calls, ["sys:local_test"])
            # 内置 job 首次 load() 时也可能到期触发，只需确认"我们注册的
            # 这个 job"没有经过 submit_fn 这条旧路径即可，不断言其它内置
            # job 是否触发（不是本测试关注的行为）。
            self.assertNotIn("本地", submit_calls)

    def test_local_handler_false_does_not_advance_last_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:local_fail", name="本地失败", schedule="interval:1")
            scheduler.register_local_handler("sys:local_fail", lambda job: False)

            job = scheduler.get("sys:local_fail")
            job.next_run_at = time.time() - 1
            triggered = scheduler.tick()
            self.assertNotIn("sys:local_fail", triggered)


class TestExecutionChannel(unittest.TestCase):
    """[看板"为什么 run_count 不为 0 但执行记录是空的"] 覆盖
    `CronScheduler.execution_channel()` 对四种通道的判定：local_handler
    优先于 dedicated_workspace/message_queue，goal_cycle 优先于两者，
    未知 job_id 返回 "unknown"。"""

    def test_unknown_job_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = CronScheduler(_make_paths(tmp), submit_fn=None)
            scheduler.load()
            self.assertEqual(scheduler.execution_channel("sys:does_not_exist"), "unknown")

    def test_local_handler_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = CronScheduler(_make_paths(tmp), submit_fn=None)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:watchlist_report_hourly", name="关注对象分级汇报（hourly）", schedule="interval:3600")
            scheduler.register_local_handler("sys:watchlist_report_hourly", lambda job: True)
            self.assertEqual(scheduler.execution_channel("sys:watchlist_report_hourly"), "local_handler")

    def test_goal_cycle_job_takes_priority_over_local_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = CronScheduler(_make_paths(tmp), submit_fn=None)
            scheduler.load()
            job = scheduler.add_job(
                name="目标绑定", schedule="interval:3600", task_template="",
                goal_id="goal-1", run_mode="goal_cycle",
            )
            # 就算这个 job_id 恰好也注册了本地回调，goal_cycle 的判定
            # 仍然优先——`_fire()` 本身就是这个优先级。
            scheduler.register_local_handler(job.id, lambda j: True)
            self.assertEqual(scheduler.execution_channel(job.id), "goal_cycle")

    def test_dedicated_workspace_when_job_runner_injected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_job_runner = SimpleNamespace()
            scheduler = CronScheduler(_make_paths(tmp), submit_fn=None, job_runner=fake_job_runner)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:goal_review", name="目标清理", schedule="interval:43200")
            self.assertEqual(scheduler.execution_channel("sys:goal_review"), "dedicated_workspace")

    def test_message_queue_when_no_job_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = CronScheduler(_make_paths(tmp), submit_fn=lambda *a, **k: True)
            scheduler.load()
            scheduler.ensure_job(job_id="sys:plain_job", name="普通任务", schedule="interval:3600")
            self.assertEqual(scheduler.execution_channel("sys:plain_job"), "message_queue")


if __name__ == "__main__":
    unittest.main()
