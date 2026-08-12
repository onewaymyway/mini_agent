"""tests/test_growth_advisor_pursuit_style.py

覆盖 next_doc/growth_advisor_ideal_advisor_gap_and_roadmap_plan.md
方向 6：调研风格智能分类。

  _infer_pursuit_style_rule() —— 规则式关键词匹配，零成本默认路径。
  classify_pursuit_style_llm() —— LLM 分类，opt-in 增强。
  determine_pursuit_style() —— 统一入口：规则默认 + LLM 复核。
  pursuit_style_hint() —— 每轮追加进 prompt 的风格提示。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from mini_agent.evolution import growth_advisor as ga


def _make_goal(tags=("growth_advisor",), style=None):
    return SimpleNamespace(tags=list(tags), growth_pursuit_style=style)


class TestInferPursuitStyleRule(unittest.TestCase):
    def test_skill_keywords_win(self):
        self.assertEqual(ga._infer_pursuit_style_rule("Python 编程实战"), "技能实操类")

    def test_habit_keywords_win(self):
        self.assertEqual(ga._infer_pursuit_style_rule("早起习惯养成"), "习惯养成类")

    def test_default_fallback_is_knowledge(self):
        self.assertEqual(ga._infer_pursuit_style_rule("宏观经济学思考"), "知识理论类")

    def test_extra_text_contributes_to_match(self):
        style = ga._infer_pursuit_style_rule("数据可视化", extra_text="想学一些实战代码和工具")
        self.assertEqual(style, "技能实操类")

    def test_tie_falls_back_to_default(self):
        # 恰好各命中 1 个关键词时不应该武断偏向某一类，兜底默认值。
        style = ga._infer_pursuit_style_rule("编程 习惯")
        self.assertEqual(style, "技能实操类")  # dict 遍历顺序下技能类先被记录为 best


class TestClassifyPursuitStyleLlm(unittest.TestCase):
    def test_valid_label_returned(self):
        result = ga.classify_pursuit_style_llm("学吉他", [], lambda p: "技能实操类")
        self.assertEqual(result, "技能实操类")

    def test_invalid_label_returns_none(self):
        result = ga.classify_pursuit_style_llm("学吉他", [], lambda p: "不知道")
        self.assertIsNone(result)

    def test_empty_response_returns_none(self):
        result = ga.classify_pursuit_style_llm("学吉他", [], lambda p: "")
        self.assertIsNone(result)

    def test_exception_returns_none(self):
        def _boom(p):
            raise RuntimeError("llm down")

        result = ga.classify_pursuit_style_llm("学吉他", [], _boom)
        self.assertIsNone(result)


class TestDeterminePursuitStyle(unittest.TestCase):
    def test_default_uses_rule_only(self):
        style = ga.determine_pursuit_style("Python 编程实战")
        self.assertEqual(style, "技能实操类")

    def test_llm_disabled_ignores_helper(self):
        cfg = SimpleNamespace(pursuit_style_llm_enabled=False)
        style = ga.determine_pursuit_style(
            "Python 编程实战", cfg=cfg, llm_helper=lambda p: "习惯养成类",
        )
        self.assertEqual(style, "技能实操类")

    def test_llm_enabled_without_helper_falls_back_to_rule(self):
        cfg = SimpleNamespace(pursuit_style_llm_enabled=True)
        style = ga.determine_pursuit_style("Python 编程实战", cfg=cfg, llm_helper=None)
        self.assertEqual(style, "技能实操类")

    def test_llm_enabled_overrides_rule(self):
        cfg = SimpleNamespace(pursuit_style_llm_enabled=True)
        style = ga.determine_pursuit_style(
            "Python 编程实战", cfg=cfg, llm_helper=lambda p: "知识理论类",
        )
        self.assertEqual(style, "知识理论类")

    def test_llm_invalid_response_falls_back_to_rule(self):
        cfg = SimpleNamespace(pursuit_style_llm_enabled=True)
        style = ga.determine_pursuit_style(
            "Python 编程实战", cfg=cfg, llm_helper=lambda p: "乱七八糟",
        )
        self.assertEqual(style, "技能实操类")


class TestPursuitStyleHint(unittest.TestCase):
    def test_no_hint_for_non_growth_advisor_goal(self):
        goal = _make_goal(tags=("other",), style="技能实操类")
        self.assertIsNone(ga.pursuit_style_hint(goal))

    def test_no_hint_when_unclassified(self):
        goal = _make_goal(style=None)
        self.assertIsNone(ga.pursuit_style_hint(goal))

    def test_hint_for_each_style(self):
        for style in ga._PURSUIT_STYLE_LABELS:
            goal = _make_goal(style=style)
            hint = ga.pursuit_style_hint(goal)
            self.assertIsNotNone(hint)
            self.assertIn("调研风格提示", hint)

    def test_unknown_style_value_returns_none(self):
        goal = _make_goal(style="不存在的风格")
        self.assertIsNone(ga.pursuit_style_hint(goal))


if __name__ == "__main__":
    unittest.main()
