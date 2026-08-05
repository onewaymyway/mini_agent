"""tests/test_goal_provenance.py — Goal 来源追溯（source_initiator）测试

背景见 docs/goal-provenance-guide.md。覆盖：
  1. turn_context 的 set/get/clear 基本行为
  2. GoalBacklog.add_goal() 在没有显式 source_initiator 时，读取
     thread-local 兜底值；有显式值时不受 thread-local 影响
  3. 序列化/反序列化 round-trip，含历史数据（缺字段）的兜底
  4. 没有任何轮次上下文时（未调用 set_current_turn_initiator）默认 "user"
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.perception import turn_context as tc
from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
from mini_agent.storage.paths import AgentPaths


class TestTurnContext(unittest.TestCase):
    def tearDown(self) -> None:
        tc.clear_current_turn_initiator()

    def test_default_is_user(self) -> None:
        self.assertEqual(tc.get_current_turn_initiator(), "user")
        self.assertEqual(tc.get_current_turn_id(), "")

    def test_set_and_get(self) -> None:
        tc.set_current_turn_initiator("cron", "turn_abc")
        self.assertEqual(tc.get_current_turn_initiator(), "cron")
        self.assertEqual(tc.get_current_turn_id(), "turn_abc")

    def test_clear_resets_to_default(self) -> None:
        tc.set_current_turn_initiator("external", "turn_xyz")
        tc.clear_current_turn_initiator()
        self.assertEqual(tc.get_current_turn_initiator(), "user")
        self.assertEqual(tc.get_current_turn_id(), "")

    def test_empty_initiator_falls_back_to_default(self) -> None:
        tc.set_current_turn_initiator("", "turn_1")
        self.assertEqual(tc.get_current_turn_initiator(), "user")


class TestGoalBacklogSourceInitiator(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))
        self.gb = GoalBacklog(self.paths)
        tc.clear_current_turn_initiator()

    def tearDown(self) -> None:
        tc.clear_current_turn_initiator()
        self._tmp.cleanup()

    def test_no_turn_context_defaults_to_user(self) -> None:
        node = self.gb.add_goal("plain goal")
        self.assertEqual(node.source, "user")
        self.assertEqual(node.source_initiator, "user")

    def test_picks_up_thread_local_when_not_explicit(self) -> None:
        tc.set_current_turn_initiator("cron", "turn_42")
        node = self.gb.add_goal("goal created mid cron-triggered turn")
        # source 仍然是默认的 "user"（调用方——比如 CLI 命令——不知道
        # 这轮对话其实是 cron 触发的），但 source_initiator 正确捕获了
        # 真实的触发上下文。
        self.assertEqual(node.source, "user")
        self.assertEqual(node.source_initiator, "cron")

    def test_explicit_source_initiator_overrides_thread_local(self) -> None:
        tc.set_current_turn_initiator("cron", "turn_42")
        node = self.gb.add_goal(
            "soft-goal-derived", source="agent_derived", source_initiator="autonomous_loop",
        )
        self.assertEqual(node.source, "agent_derived")
        self.assertEqual(node.source_initiator, "autonomous_loop")

    def test_cleared_thread_local_falls_back_to_user(self) -> None:
        tc.set_current_turn_initiator("external", "turn_9")
        tc.clear_current_turn_initiator()
        node = self.gb.add_goal("after clear")
        self.assertEqual(node.source_initiator, "user")

    def test_round_trip_serialization(self) -> None:
        tc.set_current_turn_initiator("cron", "turn_7")
        node = self.gb.add_goal("roundtrip goal")
        d = node.to_dict()
        self.assertEqual(d["source_initiator"], "cron")
        restored = GoalNode.from_dict(d)
        self.assertEqual(restored.source_initiator, "cron")

    def test_legacy_data_without_field_defaults_to_user(self) -> None:
        d = self.gb.add_goal("legacy").to_dict()
        del d["source_initiator"]
        restored = GoalNode.from_dict(d)
        self.assertEqual(restored.source_initiator, "user")


if __name__ == "__main__":
    unittest.main()
