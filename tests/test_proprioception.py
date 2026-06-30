"""
tests/test_proprioception.py — [具身改进 B1] 本体感知模块测试

覆盖：
  1. AgentInternalState 默认值与归一化
  2. sense() 在不同输入下计算出合理的 cognitive_load / uncertainty /
     risk_perception / energy_budget_ratio
  3. record_tool_outcome() 的 frustration 累积/衰减 + 连续失败计数
  4. reset() 清空内部状态
  5. 边界情况：空输入、max_turns=0、全部失败/全部成功
"""

from __future__ import annotations

import unittest

from mini_agent.perception.proprioception import (
    AgentInternalState,
    ProprioceptionModule,
)


class TestAgentInternalState(unittest.TestCase):
    def test_default_state_is_neutral(self):
        s = AgentInternalState()
        self.assertEqual(s.cognitive_load, 0.0)
        self.assertEqual(s.frustration, 0.0)
        self.assertEqual(s.energy_budget_ratio, 1.0)

    def test_to_dict_rounds_values(self):
        s = AgentInternalState(cognitive_load=0.123456, frustration=0.987654)
        d = s.to_dict()
        self.assertEqual(d["cognitive_load"], 0.123)
        self.assertEqual(d["frustration"], 0.988)


class TestSenseCognitiveLoad(unittest.TestCase):
    def test_passes_through_clamped(self):
        m = ProprioceptionModule()
        state = m.sense(cognitive_load_ratio=0.7, max_turns=10)
        self.assertEqual(state.cognitive_load, 0.7)

    def test_clamps_out_of_range_values(self):
        m = ProprioceptionModule()
        over = m.sense(cognitive_load_ratio=1.5, max_turns=10)
        under = m.sense(cognitive_load_ratio=-0.5, max_turns=10)
        self.assertEqual(over.cognitive_load, 1.0)
        self.assertEqual(under.cognitive_load, 0.0)


class TestSenseUncertainty(unittest.TestCase):
    def test_empty_text_has_zero_uncertainty(self):
        m = ProprioceptionModule()
        state = m.sense(assistant_text="", max_turns=10)
        self.assertEqual(state.uncertainty, 0.0)

    def test_confident_text_has_zero_uncertainty(self):
        m = ProprioceptionModule()
        state = m.sense(assistant_text="The file has been updated successfully.", max_turns=10)
        self.assertEqual(state.uncertainty, 0.0)

    def test_hedging_words_increase_uncertainty(self):
        m = ProprioceptionModule()
        state = m.sense(
            assistant_text="I'm not sure, but maybe this might work, I think.",
            max_turns=10,
        )
        self.assertGreater(state.uncertainty, 0.0)

    def test_chinese_hedging_words_detected(self):
        m = ProprioceptionModule()
        state = m.sense(assistant_text="这个可能不太清楚，我猜应该是配置问题。", max_turns=10)
        self.assertGreater(state.uncertainty, 0.0)


class TestSenseRiskPerception(unittest.TestCase):
    def test_no_tools_zero_risk(self):
        m = ProprioceptionModule()
        state = m.sense(recent_tool_names=[], max_turns=10)
        self.assertEqual(state.risk_perception, 0.0)

    def test_readonly_tools_zero_risk(self):
        m = ProprioceptionModule()
        state = m.sense(recent_tool_names=["read_file", "grep", "ls"], max_turns=10)
        self.assertEqual(state.risk_perception, 0.0)

    def test_risky_tools_raise_risk(self):
        m = ProprioceptionModule()
        state = m.sense(recent_tool_names=["bash", "write_file"], max_turns=10)
        self.assertGreater(state.risk_perception, 0.0)

    def test_risk_perception_clamped_at_one(self):
        m = ProprioceptionModule()
        state = m.sense(
            recent_tool_names=["bash", "write_file", "delete_file", "patch_file", "str_replace"],
            max_turns=10,
        )
        self.assertEqual(state.risk_perception, 1.0)


class TestSenseEnergyBudget(unittest.TestCase):
    def test_full_budget_at_start(self):
        m = ProprioceptionModule()
        state = m.sense(turns_used=0, max_turns=10)
        self.assertEqual(state.energy_budget_ratio, 1.0)

    def test_budget_decreases_with_turns_used(self):
        m = ProprioceptionModule()
        state = m.sense(turns_used=5, max_turns=10)
        self.assertEqual(state.energy_budget_ratio, 0.5)

    def test_budget_clamped_at_zero_when_exceeded(self):
        m = ProprioceptionModule()
        state = m.sense(turns_used=15, max_turns=10)
        self.assertEqual(state.energy_budget_ratio, 0.0)

    def test_max_turns_zero_does_not_crash(self):
        m = ProprioceptionModule()
        state = m.sense(turns_used=0, max_turns=0)
        self.assertEqual(state.energy_budget_ratio, 1.0)


class TestFrustrationTracking(unittest.TestCase):
    def test_starts_at_zero(self):
        m = ProprioceptionModule()
        state = m.sense(max_turns=10)
        self.assertEqual(state.frustration, 0.0)
        self.assertEqual(m.consecutive_failures, 0)

    def test_single_failure_increments(self):
        m = ProprioceptionModule()
        m.record_tool_outcome(success=False)
        state = m.sense(max_turns=10)
        self.assertAlmostEqual(state.frustration, 0.2)
        self.assertEqual(m.consecutive_failures, 1)

    def test_consecutive_failures_accumulate_and_clamp(self):
        m = ProprioceptionModule()
        for _ in range(10):
            m.record_tool_outcome(success=False)
        state = m.sense(max_turns=10)
        self.assertEqual(state.frustration, 1.0)
        self.assertEqual(m.consecutive_failures, 10)

    def test_success_decays_frustration_and_resets_streak(self):
        m = ProprioceptionModule()
        m.record_tool_outcome(success=False)
        m.record_tool_outcome(success=False)
        before = m.sense(max_turns=10).frustration
        m.record_tool_outcome(success=True)
        after = m.sense(max_turns=10)
        self.assertLess(after.frustration, before)
        self.assertEqual(m.consecutive_failures, 0)

    def test_reset_clears_state(self):
        m = ProprioceptionModule()
        m.record_tool_outcome(success=False)
        m.record_tool_outcome(success=False)
        m.reset()
        state = m.sense(max_turns=10)
        self.assertEqual(state.frustration, 0.0)
        self.assertEqual(m.consecutive_failures, 0)


if __name__ == "__main__":
    unittest.main()
