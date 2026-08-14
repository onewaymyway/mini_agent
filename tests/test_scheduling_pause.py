"""
tests/test_scheduling_pause.py

覆盖看板"停止调度"功能：
  - OperatingState.scheduling_paused 字段的持久化往返
  - global_knowledge.set_scheduling_paused() / is_scheduling_paused()
  - AutonomousLoop.tick() 在暂停状态下最外层短路，不触碰
    cron_scheduler/objective_executor 任何方法，也不推进 tick_count
  - 恢复后 tick() 重新正常调用 cron_scheduler.tick()
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, "src")

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.autonomous_loop import AutonomousLoop
from mini_agent.perception.global_knowledge import (
    OperatingState,
    is_scheduling_paused,
    set_scheduling_paused,
    load_self_profile,
)


def _make_loop(paths, cron_scheduler=None) -> AutonomousLoop:
    return AutonomousLoop(
        goal_backlog=None,
        input_queue=None,
        paths=paths,
        cfg=None,
        cron_scheduler=cron_scheduler,
    )


class TestOperatingStateRoundTrip(unittest.TestCase):
    def test_default_not_paused(self):
        state = OperatingState()
        self.assertFalse(state.scheduling_paused)

    def test_round_trip_paused_fields(self):
        state = OperatingState(
            scheduling_paused=True,
            scheduling_paused_at=123.0,
            scheduling_paused_reason="调试 goal",
        )
        d = state.to_dict()
        restored = OperatingState.from_dict(d)
        self.assertTrue(restored.scheduling_paused)
        self.assertEqual(restored.scheduling_paused_at, 123.0)
        self.assertEqual(restored.scheduling_paused_reason, "调试 goal")

    def test_from_dict_missing_fields_defaults_false(self):
        restored = OperatingState.from_dict({"autonomy_level": "maintenance"})
        self.assertFalse(restored.scheduling_paused)


class TestSchedulingPauseHelpers(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_default_is_not_paused(self):
        self.assertFalse(is_scheduling_paused(self.paths))

    def test_pause_then_query(self):
        set_scheduling_paused(self.paths, True, reason="调试 cron")
        self.assertTrue(is_scheduling_paused(self.paths))
        profile = load_self_profile(self.paths)
        self.assertTrue(profile.operating_state.scheduling_paused)
        self.assertEqual(profile.operating_state.scheduling_paused_reason, "调试 cron")
        self.assertGreater(profile.operating_state.scheduling_paused_at, 0)

    def test_resume_clears_reason_and_timestamp(self):
        set_scheduling_paused(self.paths, True, reason="调试 cron")
        set_scheduling_paused(self.paths, False)
        self.assertFalse(is_scheduling_paused(self.paths))
        profile = load_self_profile(self.paths)
        self.assertEqual(profile.operating_state.scheduling_paused_reason, "")
        self.assertEqual(profile.operating_state.scheduling_paused_at, 0.0)

    def test_pause_preserves_other_operating_state_fields(self):
        # 先设置 autonomy_level，再暂停调度，确认暂停操作不覆盖其它字段
        # （读-改-写而非局部 patch 的行为验证）。
        from mini_agent.perception.global_knowledge import ensure_self_profile, save_self_profile
        profile = ensure_self_profile(self.paths)
        profile.operating_state.autonomy_level = "maintenance"
        save_self_profile(self.paths, profile)

        set_scheduling_paused(self.paths, True)
        restored = load_self_profile(self.paths)
        self.assertEqual(restored.operating_state.autonomy_level, "maintenance")
        self.assertTrue(restored.operating_state.scheduling_paused)


class TestAutonomousLoopPauseShortCircuit(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.cron_scheduler = MagicMock()
        self.cron_scheduler.tick.return_value = []
        self.loop = _make_loop(self.paths, cron_scheduler=self.cron_scheduler)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_tick_calls_cron_scheduler_when_not_paused(self):
        self.loop.tick()
        self.cron_scheduler.tick.assert_called_once()
        self.assertEqual(self.loop.tick_count, 1)

    def test_tick_short_circuits_when_paused(self):
        set_scheduling_paused(self.paths, True, reason="调试")
        self.loop.tick()
        self.cron_scheduler.tick.assert_not_called()
        # tick_count 在暂停期间不应推进，避免看板显示"还在跳动"的错觉
        self.assertEqual(self.loop.tick_count, 0)

    def test_tick_resumes_after_unpause(self):
        set_scheduling_paused(self.paths, True)
        self.loop.tick()
        self.cron_scheduler.tick.assert_not_called()

        set_scheduling_paused(self.paths, False)
        self.loop.tick()
        self.cron_scheduler.tick.assert_called_once()
        self.assertEqual(self.loop.tick_count, 1)

    def test_get_digest_status_exposes_scheduling_paused(self):
        self.assertFalse(self.loop.get_digest_status()["scheduling_paused"])
        set_scheduling_paused(self.paths, True)
        self.assertTrue(self.loop.get_digest_status()["scheduling_paused"])


if __name__ == "__main__":
    unittest.main()
