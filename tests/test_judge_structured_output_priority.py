"""
tests/test_judge_structured_output_priority.py

复现并锁定一个真实 bug：TurnJudge（以及任何走 role_agents.verdict.
parse_judge_verdict 的判官，如 GoalJudge）在 feedback 字段里按提示词要求
引用了一段被 TOOL_USE_EXAMPLE_MARKER 标记、但本身格式写坏/不闭合的
<tool_use> 示例时，`is_valid_final_result()` 此前依赖
`strip_example_marked_spans()`——而后者只能剥离"能匹配成完整闭合对"的
<tool_use> 片段。写坏的示例天生匹配不上"完整闭合"，标记形同虚设，命中
`unclosed_tool_use` 规则，把判官本身完全健全的最终 JSON 输出整体judge为
畸形并替换成占位文本，导致下游 `parse_judge_verdict` 找不到 status 字段，
陷入无意义的重试，最终保守 fallback 到 NEED_USER（详见与用户的复盘对话）。

修复后的优先级：
  1. `looks_like_structured_judge_output()`——只要文本已经携带一段可用的
     判官类结构化输出（与 parse_judge_verdict 同一套容错级别：json_repair
     优先，正则兜底命中 "status": "AUTO_CONTINUE" 这类片段也算），直接判定
     健全，不再关心具体 status 取值属于哪个判官类型的白名单——TurnJudge/
     GoalJudge/未来的自定义判官都统一受益，不需要分别打补丁。
  2. 文本包含 TOOL_USE_EXAMPLE_MARKER——整体豁免，不要求标记覆盖的片段
     内部标签闭合完整。
  3. 以上都不满足时才退回原有的 detect_format_issue 启发式检测。

本文件只依赖 perception.format_correction_detector 和
llm.system_tool_call 两个轻量模块，不触达 fastapi/rich 等重依赖，
可在依赖不全的环境下独立运行。
"""

from __future__ import annotations

import unittest

from mini_agent.llm.system_tool_call import TOOL_USE_EXAMPLE_MARKER
from mini_agent.perception.format_correction_detector import (
    is_valid_final_result,
    looks_like_structured_judge_output,
)


class TestLooksLikeStructuredJudgeOutput(unittest.TestCase):
    def test_well_formed_json_with_status(self):
        text = '{"status": "AUTO_CONTINUE", "feedback": "ok"}'
        self.assertTrue(looks_like_structured_judge_output(text))

    def test_different_judge_status_whitelist_also_matches(self):
        # GoalJudge 的合法值是 DONE|CONTINUE|NEED_COMPACT，与 TurnJudge 的
        # NEED_USER|AUTO_CONTINUE|NEED_COMPACT 不同——本函数不应该关心具体
        # 取值属于哪个白名单。
        text = '{"status": "DONE", "feedback": "goal complete"}'
        self.assertTrue(looks_like_structured_judge_output(text))

    def test_truncated_json_with_status_fragment_intact(self):
        # json_repair 也救不回来的截断输出，但 "status": "AUTO_CONTINUE"
        # 这段片段本身还完整——parse_judge_verdict 自己的正则兜底
        # （_loose_extract_status）能命中这种情况，这里也应该一致地判定
        # 为"已携带可用结构化输出"。
        text = 'preamble noise {"status": "AUTO_CONTINUE", "feedback": "... <tool_use>\nnot closed'
        self.assertTrue(looks_like_structured_judge_output(text))

    def test_plain_text_without_status_field_does_not_match(self):
        self.assertFalse(looks_like_structured_judge_output("好的，任务已完成。"))

    def test_empty_text_does_not_match(self):
        self.assertFalse(looks_like_structured_judge_output(""))
        self.assertFalse(looks_like_structured_judge_output(None))  # type: ignore[arg-type]


class TestIsValidFinalResultJudgePriorityFix(unittest.TestCase):
    def test_turn_judge_real_repro_case(self):
        """复现真实案例：TurnJudge 的合法 JSON 输出，feedback 字段里按要求
        用 marker 引用了一段主助手上一轮写坏、没有正常闭合的 <tool_use>
        （闭合标签多打了个 s，变成 </tool_uses>）。此前会被误判为不健全。
        """
        text = (
            '{"status": "AUTO_CONTINUE", "feedback": "观察到格式错误。\\n\\n'
            f'{TOOL_USE_EXAMPLE_MARKER}\\n'
            '<tool_use>\\n{\\"name\\": \\"bash\\", \\"input\\": {\\"command\\": \\"ls\\"}}\\n'
            '</tool_uses>"}'
        )
        self.assertTrue(is_valid_final_result(text))

    def test_goal_judge_real_style_case(self):
        """同样的场景对 GoalJudge（status 取值不同、走同一个
        parse_judge_verdict）也应该同样生效，不需要单独打补丁。"""
        text = (
            '{"status": "CONTINUE", "feedback": "还差一步。\\n\\n'
            f'{TOOL_USE_EXAMPLE_MARKER}\\n'
            '<tool_use>\\n{\\"name\\": \\"read_file\\"}\\n</tool_uses>"}'
        )
        self.assertTrue(is_valid_final_result(text))

    def test_status_fragment_without_full_json_is_still_valid(self):
        """即使 json_repair 也解析不出完整 dict，只要正则兜底能抠到
        "status": "AUTO_CONTINUE" 片段，也不应该再走 tool_use 格式检测。"""
        text = 'garbled prefix {"status": "AUTO_CONTINUE", "feedback": "<tool_use>\nunterminated'
        self.assertTrue(is_valid_final_result(text))

    def test_marker_alone_without_status_field_still_exempted(self):
        """没有 status 字段（比如 evaluator/coach 这类不走 verdict 的纯文本
        判官），但正确带了 TOOL_USE_EXAMPLE_MARKER 引用了一段写坏的例子，
        仍应整体豁免。"""
        text = (
            "这段引用是举例，不是我自己的调用：\n\n"
            f"{TOOL_USE_EXAMPLE_MARKER}\n"
            '<tool_use>\n{"name": "bash"}\n</tool_uses>'
        )
        self.assertTrue(is_valid_final_result(text))

    def test_genuinely_broken_output_without_marker_or_status_still_invalid(self):
        """回归保护：没有 marker、也不是判官结构化输出的真实"写坏的工具调用"，
        仍然必须被判定为不健全——修复不能让真正的坏输出也被放行。"""
        text = (
            "我来帮你处理一下。\n\n"
            "<tool_use>\n"
            '{"name": "bash",\n'
            "<tool_use>"
        )
        self.assertFalse(is_valid_final_result(text))

    def test_normal_final_text_still_valid(self):
        self.assertTrue(is_valid_final_result("好的，任务已完成，测试全部通过。"))

    def test_empty_text_still_invalid(self):
        self.assertFalse(is_valid_final_result(""))
        self.assertFalse(is_valid_final_result("   \n  "))
        self.assertFalse(is_valid_final_result(None))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
