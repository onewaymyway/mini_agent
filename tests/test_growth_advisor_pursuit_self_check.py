"""tests/test_growth_advisor_pursuit_self_check.py

覆盖 next_doc/growth_advisor_ideal_advisor_gap_and_roadmap_plan.md
方向 5：学习效果自测环节。

  self_check_hint_for_cycle() —— 累计满 N 轮时生成"顺带自测"提示，
  跟 C1 的 reorganize_hint_for_cycle() 同一种模式。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from mini_agent.evolution import growth_advisor as ga


def _make_goal(tags=("growth_advisor",)):
    return SimpleNamespace(tags=list(tags))


class TestSelfCheckHintForCycle(unittest.TestCase):
    def test_no_hint_for_non_growth_advisor_goal(self):
        goal = _make_goal(tags=("other",))
        hint = ga.self_check_hint_for_cycle(goal, 5)
        self.assertIsNone(hint)

    def test_no_hint_when_cycle_not_multiple(self):
        goal = _make_goal()
        for n in (1, 2, 3, 4, 6, 7):
            self.assertIsNone(ga.self_check_hint_for_cycle(goal, n))

    def test_hint_at_default_threshold(self):
        goal = _make_goal()
        hint = ga.self_check_hint_for_cycle(goal, 5)
        self.assertIsNotNone(hint)
        self.assertIn("第 5 轮", hint)
        self.assertIn("自测", hint)
        self.assertIn("不需要用户当场提交答案", hint)

    def test_hint_respects_custom_cfg_threshold(self):
        goal = _make_goal()
        cfg = SimpleNamespace(pursuit_self_check_every_n_cycles=3)
        self.assertIsNone(ga.self_check_hint_for_cycle(goal, 5, cfg=cfg))
        hint = ga.self_check_hint_for_cycle(goal, 6, cfg=cfg)
        self.assertIsNotNone(hint)
        self.assertIn("第 6 轮", hint)
        self.assertIn("累计满 3 轮", hint)

    def test_zero_or_negative_threshold_disables(self):
        goal = _make_goal()
        for disabled in (0, -1):
            cfg = SimpleNamespace(pursuit_self_check_every_n_cycles=disabled)
            self.assertIsNone(ga.self_check_hint_for_cycle(goal, 10, cfg=cfg))

    def test_cycle_zero_never_hints(self):
        goal = _make_goal()
        self.assertIsNone(ga.self_check_hint_for_cycle(goal, 0))

    def test_hint_does_not_request_scoring(self):
        """[非目标校验] 自测提示不应该要求系统给用户的理解程度打分，
        对齐 growth_advisor_design.md 的既有边界。"""
        goal = _make_goal()
        hint = ga.self_check_hint_for_cycle(goal, 5)
        self.assertIn("请不要对用户的掌握程度做任何", hint)


if __name__ == "__main__":
    unittest.main()
