"""
tests/test_goal_cron_unified_scheduler_p3.py

覆盖 next_doc/goal_cron_unified_scheduler_improvement_plan.md P3：
tick() 执行看门狗——从"暴露观测字段"升级为"主动检测告警"。

  - 正常节奏的 tick() 不会被误判为卡死（suspected_stuck 保持 False）
  - 模拟一次超长 tick()（用 time.sleep 打桩），看门狗能在预期时间窗口内
    检测到 suspected_stuck，且只告警一次、不重复刷屏
  - 卡住的 tick() 终于返回后，suspected_stuck 复位；下一次再卡住会重新
    告警一次
  - set_tick_interval_seconds() 能实时更新判定阈值
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from mini_agent.evolution.scheduler_heartbeat import SchedulerHeartbeat


class _FakeAutonomousLoop:
    def __init__(self, tick_side_effect=None):
        self.tick_calls = 0
        self._tick_side_effect = tick_side_effect
        self._lock = threading.Lock()

    def should_tick(self) -> bool:
        return True

    def tick(self) -> None:
        with self._lock:
            self.tick_calls += 1
        if self._tick_side_effect:
            self._tick_side_effect()


class TestSchedulerHeartbeatWatchdog(unittest.TestCase):
    def test_normal_ticks_not_flagged_stuck(self):
        loop = _FakeAutonomousLoop()
        lock = threading.Lock()
        hb = SchedulerHeartbeat(
            loop, lock, interval_seconds=0.05,
            tick_interval_seconds=0.05, stuck_threshold_multiplier=2.0,
        )
        hb.start()
        try:
            deadline = time.time() + 3
            while loop.tick_calls < 3 and time.time() < deadline:
                time.sleep(0.02)
            self.assertGreaterEqual(loop.tick_calls, 3)
            self.assertFalse(hb.suspected_stuck)
        finally:
            hb.stop()
            hb.join(timeout=2)

    def test_detects_stuck_tick_and_alerts_once(self):
        stuck_evt = threading.Event()
        release_evt = threading.Event()

        def _stuck_side_effect():
            stuck_evt.set()
            release_evt.wait(timeout=5)

        loop = _FakeAutonomousLoop(tick_side_effect=_stuck_side_effect)
        lock = threading.Lock()
        hb = SchedulerHeartbeat(
            loop, lock, interval_seconds=0.05,
            tick_interval_seconds=0.05, stuck_threshold_multiplier=2.0,
            paths="fake-paths",
        )

        dispatch_calls = []

        class _FakeDispatcher:
            def __init__(self, paths):
                self.paths = paths

            def dispatch(self, message, channels=None):
                dispatch_calls.append(message)
                return {}

        with patch(
            "mini_agent.notification.dispatcher.NotificationDispatcher",
            _FakeDispatcher,
        ):
            hb.start()
            try:
                self.assertTrue(stuck_evt.wait(timeout=2), "tick() 应该已经进入卡死模拟")
                # threshold = 0.05 * 2 = 0.1s，多等几轮确保看门狗已经检测到
                deadline = time.time() + 2
                while not hb.suspected_stuck and time.time() < deadline:
                    time.sleep(0.02)
                self.assertTrue(hb.suspected_stuck)

                # 再等几轮轮询，确认告警只发了一次，没有重复刷屏
                time.sleep(0.3)
                self.assertEqual(len(dispatch_calls), 1)
                self.assertEqual(dispatch_calls[0].source, "scheduler_heartbeat_stuck")
            finally:
                release_evt.set()
                # tick() 结束后 suspected_stuck 应该复位
                deadline = time.time() + 2
                while hb.suspected_stuck and time.time() < deadline:
                    time.sleep(0.02)
                self.assertFalse(hb.suspected_stuck)
                hb.stop()
                hb.join(timeout=2)

    def test_no_alert_dispatched_when_paths_is_none(self):
        """paths 未注入（旧路径/构造失败）时，suspected_stuck 仍然正确
        置位，但不尝试发送通知（静默降级，不抛异常）。"""
        stuck_evt = threading.Event()
        release_evt = threading.Event()

        def _stuck_side_effect():
            stuck_evt.set()
            release_evt.wait(timeout=5)

        loop = _FakeAutonomousLoop(tick_side_effect=_stuck_side_effect)
        lock = threading.Lock()
        hb = SchedulerHeartbeat(
            loop, lock, interval_seconds=0.05,
            tick_interval_seconds=0.05, stuck_threshold_multiplier=2.0,
            paths=None,
        )
        hb.start()
        try:
            self.assertTrue(stuck_evt.wait(timeout=2))
            deadline = time.time() + 2
            while not hb.suspected_stuck and time.time() < deadline:
                time.sleep(0.02)
            self.assertTrue(hb.suspected_stuck)
        finally:
            release_evt.set()
            hb.stop()
            hb.join(timeout=2)

    def test_set_tick_interval_seconds_updates_threshold(self):
        loop = _FakeAutonomousLoop()
        lock = threading.Lock()
        hb = SchedulerHeartbeat(loop, lock, interval_seconds=0.05, tick_interval_seconds=60.0)
        hb.set_tick_interval_seconds(0.05)
        with hb._stats_lock:
            self.assertAlmostEqual(hb._tick_interval_seconds, 0.05)


if __name__ == "__main__":
    unittest.main()
