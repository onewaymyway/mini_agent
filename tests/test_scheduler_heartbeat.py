"""
tests/test_scheduler_heartbeat.py

覆盖 next_doc/daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
阶段二：SchedulerHeartbeat ——

  - should_tick()==False 时不触发 tick()
  - should_tick()==True 时按轮询间隔正常触发 tick()，且 tick() 期间持有共享锁
  - tick() 抛异常不会导致心跳线程整体退出（下一轮还会继续尝试）
  - stop() 能让线程干净退出
  - 心跳线程被"主循环长时间持锁"阻塞时，只是等锁，不会因此漏跳/卡死
    （锁释放后立刻能追上继续 tick）
"""

from __future__ import annotations

import threading
import time
import unittest

from mini_agent.evolution.scheduler_heartbeat import SchedulerHeartbeat


class _FakeAutonomousLoop:
    def __init__(self, should_tick_values=None, tick_side_effect=None):
        self._should_tick_values = list(should_tick_values or [])
        self.tick_calls = 0
        self._tick_side_effect = tick_side_effect
        self._lock = threading.Lock()

    def should_tick(self) -> bool:
        if self._should_tick_values:
            return self._should_tick_values.pop(0)
        return True

    def tick(self) -> None:
        with self._lock:
            self.tick_calls += 1
            if self._tick_side_effect:
                self._tick_side_effect()


class TestSchedulerHeartbeat(unittest.TestCase):
    def test_ticks_when_should_tick_true(self):
        loop = _FakeAutonomousLoop()
        lock = threading.Lock()
        hb = SchedulerHeartbeat(loop, lock, interval_seconds=0.05)
        hb.start()
        try:
            deadline = time.time() + 2
            while loop.tick_calls < 3 and time.time() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(loop.tick_calls, 3)
        finally:
            hb.stop()
            hb.join(timeout=2)
            self.assertFalse(hb.is_alive())

    def test_skips_tick_when_should_tick_false(self):
        loop = _FakeAutonomousLoop(should_tick_values=[False, False, False])
        lock = threading.Lock()
        hb = SchedulerHeartbeat(loop, lock, interval_seconds=0.05)
        hb.start()
        try:
            time.sleep(0.3)
            self.assertEqual(loop.tick_calls, 0)
        finally:
            hb.stop()
            hb.join(timeout=2)

    def test_tick_exception_does_not_kill_thread(self):
        call_count = {"n": 0}

        def side_effect():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("boom")

        loop = _FakeAutonomousLoop(tick_side_effect=side_effect)
        lock = threading.Lock()
        hb = SchedulerHeartbeat(loop, lock, interval_seconds=0.05)
        hb.start()
        try:
            deadline = time.time() + 2
            while loop.tick_calls < 2 and time.time() < deadline:
                time.sleep(0.02)
            # 第一次 tick 内部抛异常，第二次仍然应该正常被调用到
            self.assertGreaterEqual(loop.tick_calls, 2)
            self.assertTrue(hb.is_alive())
        finally:
            hb.stop()
            hb.join(timeout=2)

    def test_stop_exits_promptly(self):
        loop = _FakeAutonomousLoop()
        lock = threading.Lock()
        hb = SchedulerHeartbeat(loop, lock, interval_seconds=10.0)  # 很长的轮询间隔
        hb.start()
        time.sleep(0.05)
        t0 = time.time()
        hb.stop()
        hb.join(timeout=2)
        elapsed = time.time() - t0
        # stop() 应该让线程几乎立即退出，而不用等满 10 秒的 interval
        self.assertLess(elapsed, 1.0)
        self.assertFalse(hb.is_alive())

    def test_waits_for_lock_held_by_main_loop_then_catches_up(self):
        """模拟"主循环长时间持锁"（对应 on_turn_done/on_turn_failed 那一小段
        状态更新代码），验证心跳线程会等锁而不是跳过/卡死，锁释放后能继续
        正常 tick。"""
        loop = _FakeAutonomousLoop()
        lock = threading.Lock()

        lock.acquire()
        try:
            hb = SchedulerHeartbeat(loop, lock, interval_seconds=0.05)
            hb.start()
            # 心跳线程此刻应该正卡在等锁上，tick_calls 应该还是 0
            time.sleep(0.3)
            self.assertEqual(loop.tick_calls, 0)
        finally:
            lock.release()

        try:
            deadline = time.time() + 2
            while loop.tick_calls < 1 and time.time() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(loop.tick_calls, 1)
        finally:
            hb.stop()
            hb.join(timeout=2)


class TestSchedulerHeartbeatObservability(unittest.TestCase):
    """[daemon_task_hang_recovery_and_watchdog_hardening_plan.md 阶段二]
    last_tick_started_at/last_tick_finished_at/last_tick_duration_seconds。"""

    def test_timestamps_updated_after_normal_tick(self):
        loop = _FakeAutonomousLoop()
        lock = threading.Lock()
        hb = SchedulerHeartbeat(loop, lock, interval_seconds=0.05)

        self.assertEqual(hb.last_tick_started_at, 0.0)
        self.assertEqual(hb.last_tick_finished_at, 0.0)

        hb.start()
        try:
            deadline = time.time() + 2
            while loop.tick_calls < 1 and time.time() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(loop.tick_calls, 1)
            self.assertGreater(hb.last_tick_started_at, 0.0)
            self.assertGreater(hb.last_tick_finished_at, 0.0)
            self.assertGreaterEqual(hb.last_tick_finished_at, hb.last_tick_started_at)
            self.assertGreaterEqual(hb.last_tick_duration_seconds, 0.0)
        finally:
            hb.stop()
            hb.join(timeout=2)

    def test_finished_at_updated_even_when_tick_raises(self):
        """tick() 抛异常时 last_tick_finished_at 依然会被更新——放在
        finally 里，异常场景下也要能看出"心跳还在正常轮转，只是这一次
        业务失败了"，与"心跳彻底停摆"区分开。"""

        def _always_raise():
            raise RuntimeError("boom")

        loop = _FakeAutonomousLoop(tick_side_effect=_always_raise)
        lock = threading.Lock()
        hb = SchedulerHeartbeat(loop, lock, interval_seconds=0.05)
        hb.start()
        try:
            deadline = time.time() + 2
            while loop.tick_calls < 1 and time.time() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(loop.tick_calls, 1)
            self.assertGreater(hb.last_tick_started_at, 0.0)
            self.assertGreater(hb.last_tick_finished_at, 0.0)
        finally:
            hb.stop()
            hb.join(timeout=2)

    def test_timestamps_not_updated_when_should_tick_false(self):
        loop = _FakeAutonomousLoop(should_tick_values=[False, False, False])
        lock = threading.Lock()
        hb = SchedulerHeartbeat(loop, lock, interval_seconds=0.05)
        hb.start()
        try:
            time.sleep(0.3)
            self.assertEqual(loop.tick_calls, 0)
            self.assertEqual(hb.last_tick_started_at, 0.0)
            self.assertEqual(hb.last_tick_finished_at, 0.0)
        finally:
            hb.stop()
            hb.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
