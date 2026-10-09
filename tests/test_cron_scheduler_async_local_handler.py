"""tests/test_cron_scheduler_async_local_handler.py

[next_doc/tick_dispatch_only_execution_model_plan.md 阶段一]
CronScheduler 的 local_handler 异步化：注入 TickDispatcher 后，tick() 不再同步执行
handler，永远挂住的 handler 不会卡住 tick；失败/超时在 drain_async_handler_results()
里补记 skip 记账。未注入 dispatcher 时行为与旧版一致（见
test_cron_scheduler_local_handler.py）。
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.evolution.tick_dispatcher import TickDispatcher
from mini_agent.storage.paths import AgentPaths


def _wait_until(pred, timeout=3.0, step=0.01):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


def _make(tmp, dispatcher=None, **kw):
    scheduler = CronScheduler(AgentPaths(Path(tmp)), submit_fn=None)
    scheduler.load()
    if dispatcher is not None:
        scheduler.set_tick_dispatcher(dispatcher, **kw)
    return scheduler


def _make_due(scheduler, job_id="sys:async_test", handler=None):
    scheduler.ensure_job(job_id=job_id, name="异步测试", schedule="interval:1")
    if handler is not None:
        scheduler.register_local_handler(job_id, handler)
    scheduler.get(job_id).next_run_at = time.time() - 1
    return job_id


class TestAsyncLocalHandler(unittest.TestCase):
    def test_tick_does_not_block_on_hanging_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = TickDispatcher(max_workers=4)
            scheduler = _make(tmp, d)
            gate = threading.Event()
            job_id = _make_due(scheduler, handler=lambda job: gate.wait(10))

            t0 = time.time()
            triggered = scheduler.tick()
            elapsed = time.time() - t0

            self.assertLess(elapsed, 1.0, "handler 永远挂住时 tick() 也必须秒级返回")
            self.assertIn(job_id, triggered, "派发成功即视为触发（与 job_runner 路径一致）")
            self.assertGreater(scheduler.get(job_id).last_run_at, 0)
            self.assertTrue(scheduler.is_job_running(job_id))
            gate.set()
            self.assertTrue(_wait_until(lambda: not scheduler.is_job_running(job_id)))

    def test_second_trigger_while_running_is_rejected_with_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = TickDispatcher(max_workers=4)
            scheduler = _make(tmp, d)
            gate = threading.Event()
            job_id = _make_due(scheduler, handler=lambda job: gate.wait(10))
            self.assertIn(job_id, scheduler.tick())

            scheduler.get(job_id).next_run_at = time.time() - 1
            triggered = scheduler.tick()
            self.assertNotIn(job_id, triggered)
            self.assertEqual(scheduler.get(job_id).last_skip_reason, "local_handler_already_running")
            gate.set()

    def test_handler_returning_false_is_recorded_on_drain(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = TickDispatcher()
            scheduler = _make(tmp, d)
            job_id = _make_due(scheduler, handler=lambda job: False)
            scheduler.tick()
            self.assertTrue(_wait_until(lambda: not d.is_running(f"cron:{job_id}")))

            self.assertEqual(scheduler.drain_async_handler_results(), 1)
            job = scheduler.get(job_id)
            self.assertEqual(job.last_skip_reason, "local_handler_returned_false")
            self.assertEqual(job.consecutive_skip_count, 1)

    def test_handler_skip_reason_is_carried_back(self):
        """handler 里 set_skip_reason() 写的原因（如 protected_backup_failed）要带回 job。"""
        from mini_agent.evolution.cron_skip_reasons import set_skip_reason

        def _handler(job):
            set_skip_reason(job, "protected_backup_failed", "disk full")
            return False

        with tempfile.TemporaryDirectory() as tmp:
            d = TickDispatcher()
            scheduler = _make(tmp, d)
            job_id = _make_due(scheduler, handler=_handler)
            scheduler.tick()
            self.assertTrue(_wait_until(lambda: not d.is_running(f"cron:{job_id}")))
            scheduler.drain_async_handler_results()
            job = scheduler.get(job_id)
            self.assertEqual(job.last_skip_reason, "protected_backup_failed")
            self.assertEqual(job.last_skip_detail, "disk full")

    def test_handler_exception_is_recorded_on_drain(self):
        def _boom(job):
            raise RuntimeError("kaboom")

        with tempfile.TemporaryDirectory() as tmp:
            d = TickDispatcher()
            scheduler = _make(tmp, d)
            job_id = _make_due(scheduler, handler=_boom)
            scheduler.tick()
            self.assertTrue(_wait_until(lambda: not d.is_running(f"cron:{job_id}")))
            scheduler.drain_async_handler_results()
            job = scheduler.get(job_id)
            self.assertEqual(job.last_skip_reason, "local_handler_exception")
            self.assertIn("kaboom", job.last_skip_detail)

    def test_consecutive_failures_accumulate_across_dispatches(self):
        """派发成功会把 consecutive_skip_count 清零，必须靠 fail streak 累计，
        否则连续失败永远达不到告警阈值。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = TickDispatcher()
            scheduler = _make(tmp, d)
            job_id = _make_due(scheduler, handler=lambda job: False)
            for expected in (1, 2, 3):
                scheduler.get(job_id).next_run_at = time.time() - 1
                scheduler.tick()
                self.assertTrue(_wait_until(lambda: not d.is_running(f"cron:{job_id}")))
                scheduler.drain_async_handler_results()
                self.assertEqual(scheduler.get(job_id).consecutive_skip_count, expected)

    def test_success_resets_failure_streak(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = TickDispatcher()
            scheduler = _make(tmp, d)
            results = iter([False, True, False])
            job_id = _make_due(scheduler, handler=lambda job: next(results))
            counts = []
            for _ in range(3):
                scheduler.get(job_id).next_run_at = time.time() - 1
                scheduler.tick()
                self.assertTrue(_wait_until(lambda: not d.is_running(f"cron:{job_id}")))
                scheduler.drain_async_handler_results()
                counts.append(scheduler.get(job_id).consecutive_skip_count)
            self.assertEqual(counts, [1, 0, 1])

    def test_hung_handler_is_reaped_and_recorded_as_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = TickDispatcher(grace_seconds=0)
            scheduler = _make(tmp, d, timeout_seconds=1)
            gate = threading.Event()
            job_id = _make_due(scheduler, handler=lambda job: gate.wait(30))
            scheduler.tick()
            self.assertTrue(scheduler.is_job_running(job_id))
            time.sleep(1.2)
            scheduler.drain_async_handler_results()  # 内部先 reap_stale
            self.assertFalse(scheduler.is_job_running(job_id), "回收后名额释放")
            job = scheduler.get(job_id)
            self.assertEqual(job.last_skip_reason, "local_handler_timeout")
            gate.set()

    def test_without_dispatcher_behavior_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = _make(tmp)  # 不注入 dispatcher
            calls = []
            job_id = _make_due(scheduler, handler=lambda job: calls.append(1) or True)
            triggered = scheduler.tick()
            self.assertIn(job_id, triggered)
            self.assertEqual(calls, [1], "未开启异步时 handler 仍在 tick 内同步执行")
            self.assertEqual(scheduler.drain_async_handler_results(), 0)

    def test_without_dispatcher_false_still_not_triggered(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = _make(tmp)
            job_id = _make_due(scheduler, handler=lambda job: False)
            self.assertNotIn(job_id, scheduler.tick())


class TestHeartbeatAcceptance(unittest.TestCase):
    """阶段一验收：handler 永远不返回时，SchedulerHeartbeat 的单次 tick 仍是秒级。"""

    def test_heartbeat_tick_stays_fast_with_hung_handler(self):
        from mini_agent.evolution.scheduler_heartbeat import SchedulerHeartbeat

        with tempfile.TemporaryDirectory() as tmp:
            d = TickDispatcher()
            scheduler = _make(tmp, d)
            gate = threading.Event()
            _make_due(scheduler, handler=lambda job: gate.wait(30))

            class _Loop:
                def should_tick(self):
                    return True

                def tick(self):
                    scheduler.drain_async_handler_results()
                    scheduler.tick()

            hb = SchedulerHeartbeat(_Loop(), threading.Lock(), interval_seconds=0.5)
            hb._maybe_tick()
            self.assertLess(hb.last_tick_duration_seconds, 1.0)
            self.assertGreater(hb.last_tick_finished_at, hb.last_tick_started_at - 1)
            gate.set()


if __name__ == "__main__":
    unittest.main()
