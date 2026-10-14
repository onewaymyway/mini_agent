"""
tests/test_format_correction_llm_check.py — 格式纠错的"LLM 二次复核 + 正确格式示例提示"

覆盖：
  1. 纯函数：触发门控 has_tool_call_trace / 复核 prompt / 结果解析 / 示例提示去重
  2. Agent 集成：规则未命中 + 有痕迹 → 调一次 LLM；判有问题注入纠错提示并续跑；
     判没问题/调用失败/输出乱码 → 放行；规则命中、无痕迹、开关关闭、重试用尽 → 不调 LLM
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import mini_agent.tools.builtin  # noqa — register tools
from mini_agent.llm.base import LLMResponse, LLMUsage
from mini_agent.llm.system_tool_call import TOOL_USE_EXAMPLE_MARKER
from mini_agent.perception import format_correction_detector as fcd


# ── 1. 纯函数 ────────────────────────────────────────────────────────────────

class TestTraceGate(unittest.TestCase):
    def test_positive_cases(self):
        for text in (
            "好的 <tool_call>bash\n{...}",
            "xx </tool_use> yy",
            "x < /tool_x",  # 不应命中：`<` 与 `/` 之间有空白
        )[:2]:
            self.assertTrue(fcd.has_tool_call_trace(text), text)
        self.assertTrue(fcd.has_tool_call_trace("```tool_call\n{}\n"))
        self.assertTrue(fcd.has_tool_call_trace('{"name": "bash", "input": {"command": "ls"}}'))
        self.assertTrue(fcd.has_tool_call_trace("<  tool_use>"))

    def test_negative_cases(self):
        # 只是提到单词，不触发
        self.assertFalse(fcd.has_tool_call_trace("解析器会处理 tool_use 和 tool_result 两种块"))
        self.assertFalse(fcd.has_tool_call_trace("def tool_executor(): pass"))
        # 只有 name 没有 input
        self.assertFalse(fcd.has_tool_call_trace('{"name": "alice"}'))
        self.assertFalse(fcd.has_tool_call_trace(""))
        self.assertFalse(fcd.has_tool_call_trace(None))


class TestHintAppendOnce(unittest.TestCase):
    def test_hint_has_marker_before_example(self):
        hint = fcd.build_format_hint()
        self.assertIn(TOOL_USE_EXAMPLE_MARKER, hint)
        self.assertLess(hint.index(TOOL_USE_EXAMPLE_MARKER), hint.index("<tool_use>"))
        self.assertTrue(hint.rstrip().endswith("```"))

    def test_append_when_missing_and_idempotent(self):
        once = fcd.append_hint_once("some feedback")
        self.assertTrue(once.endswith(fcd.build_format_hint()))
        self.assertEqual(fcd.append_hint_once(once), once)
        self.assertEqual(once.count(TOOL_USE_EXAMPLE_MARKER), 1)

    def test_trailing_whitespace_not_duplicated(self):
        once = fcd.append_hint_once("x") + "\n\n"
        self.assertEqual(fcd.append_hint_once(once), once)

    def test_example_in_middle_still_appended_at_end(self):
        msg = fcd.build_format_hint() + "\n\n> trailing note"
        out = fcd.append_hint_once(msg)
        self.assertTrue(out.endswith(fcd.build_format_hint()))
        self.assertEqual(out.count(TOOL_USE_EXAMPLE_MARKER), 2)

    def test_rule_messages_end_with_hint_except_write_truncated(self):
        issue = fcd.detect_format_issue("<tool_use>\n{\"name\": \"bash\",\n<tool_use>")
        self.assertEqual(issue.issue_type, "unclosed_tool_use")
        self.assertTrue(issue.message.endswith(fcd.build_format_hint()))
        self.assertEqual(issue.message.count(TOOL_USE_EXAMPLE_MARKER), 1)
        big = '<tool_use>\n{"name": "write_file", "input": {"content": "' + "x" * 2500
        issue2 = fcd.detect_format_issue(big)
        self.assertEqual(issue2.issue_type, "write_file_truncated")
        self.assertNotIn(TOOL_USE_EXAMPLE_MARKER, issue2.message)


class TestVerdictParsing(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(
            fcd.parse_llm_check_verdict('{"malformed": true, "reason": "name outside json"}'),
            (True, "name outside json"),
        )
        self.assertEqual(fcd.parse_llm_check_verdict('{"malformed": false, "reason": ""}'), (False, ""))

    def test_fenced_and_string_bool(self):
        raw = '```json\n{"malformed": "true", "reason": "unclosed"}\n```'
        self.assertEqual(fcd.parse_llm_check_verdict(raw), (True, "unclosed"))

    def test_loose_regex_fallback(self):
        self.assertEqual(fcd.parse_llm_check_verdict('blah "malformed": false, trailing'), (False, ""))

    def test_unparseable_returns_none(self):
        for raw in ("", None, "I think it is fine", '{"status": "ok"}'):
            self.assertIsNone(fcd.parse_llm_check_verdict(raw), raw)

    def test_reason_tags_stripped_and_truncated(self):
        out = fcd.parse_llm_check_verdict('{"malformed": true, "reason": "bad <tool_use> tag ' + "z" * 500 + '"}')
        self.assertTrue(out[0])
        self.assertNotIn("<tool_use>", out[1])
        self.assertLessEqual(len(out[1]), 300)


class TestPrompt(unittest.TestCase):
    def test_prompt_wraps_text_and_marks_example(self):
        p = fcd.build_llm_check_prompt("hello <tool_call>x")
        self.assertIn("<assistant_output>\nhello <tool_call>x\n</assistant_output>", p)
        self.assertIn(TOOL_USE_EXAMPLE_MARKER, p)

    def test_truncation_keeps_tail(self):
        text = "H" * 5000 + "TAILMARK"
        p = fcd.build_llm_check_prompt(text, max_chars=600)
        self.assertIn("TAILMARK", p)
        self.assertIn("truncated", p)
        self.assertLess(len(p), 3000)


# ── 2. Agent 集成 ────────────────────────────────────────────────────────────

BARE_JSON = '我来看一下目录\n{"name": "bash", "input": {"command": "ls"}}'


def _resp(text="", tool_calls=None):
    return LLMResponse(text=text, tool_calls=tool_calls or [],
                       usage=LLMUsage(10, 20, 30), stop_reason="end_turn")


class TestLLMCheckIntegration(unittest.TestCase):
    def _agent(self, responses, **cfg_overrides):
        from mini_agent.agent import Agent
        from mini_agent.config import load_config
        from mini_agent.permissions import PermissionGuard

        cfg = load_config()
        cfg.stream = False
        cfg.session.auto_save = False
        for k, v in cfg_overrides.items():
            section, field = k.split("__", 1)
            setattr(getattr(cfg, section), field, v)
        client = MagicMock()
        client.chat.side_effect = responses
        agent = Agent(cfg=cfg, guard=PermissionGuard(auto_approve=True), llm_client=client)
        # 与 MagicMock client 无关的既有环境问题：上下文窗口解析返回 MagicMock
        agent._resolve_context_window = lambda: 200000
        return agent, client

    @staticmethod
    def _corrections(agent):
        from mini_agent.history.entry import HType
        return [m for m in agent._history if m.get("_type") == HType.FORMAT_CORRECTION]

    def _run(self, agent, verdict, text="go"):
        """用 verdict 替身替换 ask_background；返回 mock 以便断言调用次数。"""
        fake = MagicMock(side_effect=verdict) if isinstance(verdict, Exception) \
            else MagicMock(return_value=verdict)
        with patch("mini_agent.llm.service.ask_background", fake):
            agent.run_turn(text)
        return fake

    def test_llm_says_malformed_injects_hinted_correction_and_retries(self):
        agent, client = self._agent([_resp(BARE_JSON), _resp("done")])
        fake = self._run(agent, '{"malformed": true, "reason": "no tags around the call"}')
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(fake.call_args.kwargs.get("override_temperature"), 0.0)
        corr = self._corrections(agent)
        self.assertEqual(len(corr), 1)
        self.assertEqual(corr[0]["role"], "user")
        self.assertIn("no tags around the call", corr[0]["content"])
        self.assertTrue(corr[0]["content"].endswith(fcd.build_format_hint()))
        self.assertEqual(corr[0]["content"].count(TOOL_USE_EXAMPLE_MARKER), 1)
        self.assertEqual(client.chat.call_count, 2)  # 纠错后续跑了一次

    def test_llm_says_ok_passes_through(self):
        agent, client = self._agent([_resp(BARE_JSON)])
        fake = self._run(agent, '{"malformed": false, "reason": ""}')
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(self._corrections(agent), [])
        self.assertEqual(client.chat.call_count, 1)

    def test_llm_failure_or_garbage_passes_through(self):
        for verdict in (RuntimeError("boom"), "no idea", ""):
            agent, client = self._agent([_resp(BARE_JSON)])
            self._run(agent, verdict)
            self.assertEqual(self._corrections(agent), [], verdict)
            self.assertEqual(client.chat.call_count, 1)

    def test_rule_hit_does_not_call_llm(self):
        agent, _ = self._agent([_resp('<tool_use>\n{"name": "bash",\n<tool_use>'), _resp("done")])
        fake = self._run(agent, '{"malformed": true, "reason": "x"}')
        self.assertEqual(fake.call_count, 0)
        corr = self._corrections(agent)
        self.assertEqual(len(corr), 1)
        self.assertTrue(corr[0]["content"].endswith(fcd.build_format_hint()))
        # reminder 的 .md 文案自带一份带标记的示例，末尾再追加一份（只按"末尾"去重）
        self.assertGreaterEqual(corr[0]["content"].count(TOOL_USE_EXAMPLE_MARKER), 1)
        self.assertEqual(corr[0]["content"].count(fcd.build_format_hint()), 1)

    def test_reminder_disabled_falls_back_single_hint(self):
        agent, _ = self._agent(
            [_resp('<tool_use>\n{"name": "bash",\n<tool_use>'), _resp("done")],
            reminder__format_issue_enabled=False,
        )
        self._run(agent, "")
        corr = self._corrections(agent)
        self.assertEqual(len(corr), 1)
        self.assertEqual(corr[0]["content"].count(TOOL_USE_EXAMPLE_MARKER), 1)
        self.assertTrue(corr[0]["content"].endswith(fcd.build_format_hint()))

    def test_no_trace_does_not_call_llm(self):
        agent, _ = self._agent([_resp("解析器会处理 tool_use 和 tool_result 两种块。")])
        fake = self._run(agent, '{"malformed": true, "reason": "x"}')
        self.assertEqual(fake.call_count, 0)
        self.assertEqual(self._corrections(agent), [])

    def test_switch_off_does_not_call_llm(self):
        agent, _ = self._agent([_resp(BARE_JSON)], format_correction__llm_check_enabled=False)
        fake = self._run(agent, '{"malformed": true, "reason": "x"}')
        self.assertEqual(fake.call_count, 0)
        self.assertEqual(self._corrections(agent), [])

    def test_retry_budget_exhausted_does_not_call_llm(self):
        agent, client = self._agent(
            [_resp(BARE_JSON), _resp(BARE_JSON)],
            format_correction__max_retries_per_turn=1,
        )
        fake = self._run(agent, '{"malformed": true, "reason": "bad"}')
        # 第一次复核判有问题并重试；第二次预算已满，不再复核，直接结束
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(len(self._corrections(agent)), 1)
        self.assertEqual(client.chat.call_count, 2)


if __name__ == "__main__":
    unittest.main()
