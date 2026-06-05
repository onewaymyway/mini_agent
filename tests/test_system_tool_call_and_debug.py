"""
tests/test_system_tool_call_and_debug.py

测试覆盖：
  - system_tool_call.py：render_tool_list、parse_tool_calls、strip_tool_call_blocks、
                          render_tool_results、postprocess_response、_extract 边界
  - debug_logger.py：DebugConfig、日志文件创建、request/response/error 记录、截断
  - ProviderMixin：_prepare_tools 分支、_postprocess 调用、日志集成
  - Agent 集成：两种模式的 tool result 消息格式
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import mini_agent.tools.builtin  # noqa — register tools
from mini_agent.llm.base import LLMConfig, LLMResponse, LLMUsage, ToolCall, ToolSchema
from mini_agent.llm.system_tool_call import (
    render_tool_list, parse_tool_calls, strip_tool_use_blocks,
    render_tool_results, postprocess_response, extract_thinking_blocks,
)
from mini_agent.llm.debug_logger import DebugConfig, LLMDebugLogger, init_debug_logger, get_debug_logger


# ── 共享测试数据 ──────────────────────────────────────────────────────────────

SAMPLE_TOOLS = [
    ToolSchema(name="bash", description="Run a shell command",
               input_schema={"type": "object",
                             "properties": {"command": {"type": "string"}},
                             "required": ["command"]}),
    ToolSchema(name="read_file", description="Read a file",
               input_schema={"type": "object",
                             "properties": {"path": {"type": "string"}},
                             "required": ["path"]}),
]


def make_response(text="", tool_calls=None) -> LLMResponse:
    return LLMResponse(
        text=text, tool_calls=tool_calls or [],
        usage=LLMUsage(10, 20, 30), stop_reason="end_turn",
    )


# ══════════════════════════════════════════════════════════════════════════════
# render_tool_list 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestRenderToolList(unittest.TestCase):

    def test_empty_tools(self):
        result = render_tool_list([])
        self.assertIn("no tools", result.lower())

    def test_single_tool_has_name(self):
        result = render_tool_list([SAMPLE_TOOLS[0]])
        self.assertIn("bash", result)

    def test_single_tool_has_description(self):
        result = render_tool_list([SAMPLE_TOOLS[0]])
        self.assertIn("Run a shell command", result)

    def test_single_tool_has_parameters(self):
        result = render_tool_list([SAMPLE_TOOLS[0]])
        self.assertIn("command", result)

    def test_multiple_tools_all_present(self):
        result = render_tool_list(SAMPLE_TOOLS)
        self.assertIn("bash", result)
        self.assertIn("read_file", result)

    def test_output_is_valid_markdown(self):
        result = render_tool_list(SAMPLE_TOOLS)
        self.assertIn("```", result)

    def test_parameters_schema_included(self):
        result = render_tool_list(SAMPLE_TOOLS)
        data = json.loads(result.split("```json\n")[1].split("\n```")[0])
        self.assertIn("parameters", data)


# ══════════════════════════════════════════════════════════════════════════════
# parse_tool_calls 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestParseToolCalls(unittest.TestCase):
    """测试 <tool_use> 格式解析（主格式）及旧格式兼容。"""

    def _wrap_new(self, name: str, input_: dict, id_: str = "") -> str:
        """构造新格式 <tool_use> 块。"""
        obj = {"name": name, "input": input_}
        if id_:
            obj["id"] = id_
        return f"<tool_use>\n{json.dumps(obj)}\n</tool_use>"

    def _wrap_legacy(self, obj: dict) -> str:
        """构造旧格式 ```tool_call 块。"""
        return f"```tool_call\n{json.dumps(obj, indent=2)}\n```"

    def test_new_format_single_call(self):
        text = self._wrap_new("bash", {"command": "ls"}, id_="tc_1")
        calls = parse_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "bash")
        self.assertEqual(calls[0].input["command"], "ls")

    def test_new_format_id_auto_generated_when_missing(self):
        text = self._wrap_new("bash", {"command": "pwd"})
        calls = parse_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0].id, "")

    def test_new_format_name_field(self):
        obj = {"name": "read_file", "input": {"path": "/tmp/f"}}
        text = f"<tool_use>\n{json.dumps(obj)}\n</tool_use>"
        calls = parse_tool_calls(text)
        self.assertEqual(calls[0].name, "read_file")
        self.assertEqual(calls[0].input["path"], "/tmp/f")

    def test_legacy_format_still_works(self):
        """旧格式 ```tool_call 向后兼容。"""
        text = self._wrap_legacy({"tool": "bash", "id": "tc_1", "parameters": {"command": "ls"}})
        calls = parse_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "bash")

    def test_new_format_takes_precedence_over_legacy(self):
        """新格式存在时，旧格式被忽略。"""
        new_block = self._wrap_new("bash", {"command": "new"})
        old_block = self._wrap_legacy({"tool": "grep", "parameters": {"pattern": "old"}})
        calls = parse_tool_calls(new_block + "\n" + old_block)
        # New format found -> legacy not scanned
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "bash")

    def test_no_blocks_returns_empty(self):
        calls = parse_tool_calls("Plain text, no tool calls.")
        self.assertEqual(calls, [])

    def test_invalid_json_skipped(self):
        text = "<tool_use>\n{invalid json\n</tool_use>"
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            calls = parse_tool_calls(text)
        self.assertEqual(calls, [])

    def test_mixed_text_and_call(self):
        text = "I'll run this.\n" + self._wrap_new("bash", {"command": "ls"})
        calls = parse_tool_calls(text)
        self.assertEqual(len(calls), 1)

    def test_empty_input(self):
        text = self._wrap_new("bash", {})
        calls = parse_tool_calls(text)
        self.assertEqual(calls[0].input, {})

    def test_input_alias_parameters(self):
        """旧格式的 parameters 字段也被接受。"""
        obj = {"name": "bash", "parameters": {"command": "echo"}}
        text = f"<tool_use>\n{json.dumps(obj)}\n</tool_use>"
        calls = parse_tool_calls(text)
        self.assertEqual(calls[0].input["command"], "echo")


class TestStripToolUseBlocks(unittest.TestCase):

    def test_strips_new_format(self):
        block = '<tool_use>\n{"name":"bash","input":{}}\n</tool_use>'
        text = "Before\n" + block + "\nAfter"
        result = strip_tool_use_blocks(text)
        self.assertNotIn("<tool_use>", result)
        self.assertIn("Before", result)
        self.assertIn("After", result)

    def test_strips_legacy_format(self):
        block = '```tool_call\n{"tool":"bash","parameters":{}}\n```'
        text = "Before\n" + block + "\nAfter"
        result = strip_tool_use_blocks(text)
        self.assertNotIn("```tool_call", result)

    def test_no_blocks_unchanged(self):
        self.assertEqual(strip_tool_use_blocks("Hello world"), "Hello world")

    def test_only_block_returns_empty(self):
        block = '<tool_use>\n{"name":"bash","input":{}}\n</tool_use>'
        self.assertEqual(strip_tool_use_blocks(block), "")


class TestRenderToolResults(unittest.TestCase):

    def test_single_result_format(self):
        tc = ToolCall(id="tc_1", name="bash", input={"command": "ls"})
        result = render_tool_results([tc], ["file1.py\nfile2.py"])
        self.assertIn("<tool_result>", result)
        self.assertIn("bash", result)
        self.assertIn("file1.py", result)

    def test_multiple_results(self):
        tcs = [
            ToolCall(id="tc_1", name="bash", input={}),
            ToolCall(id="tc_2", name="read_file", input={}),
        ]
        result = render_tool_results(tcs, ["output1", "output2"])
        self.assertIn("bash", result)
        self.assertIn("output1", result)
        self.assertIn("output2", result)

    def test_result_is_valid_json_in_block(self):
        tc = ToolCall(id="tc_1", name="bash", input={})
        result = render_tool_results([tc], ["done"])
        block_content = result.split("<tool_result>\n")[1].split("\n</tool_result>")[0]
        data = json.loads(block_content)
        self.assertEqual(data["name"], "bash")
        self.assertEqual(data["output"], "done")


# ══════════════════════════════════════════════════════════════════════════════
# postprocess_response 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestPostprocessResponse(unittest.TestCase):

    def _call_block(self, name="bash", id_="tc_1", params=None) -> str:
        return (
            f'```tool_call\n'
            f'{{"tool":"{name}","id":"{id_}","parameters":{json.dumps(params or {})}}}\n'
            f'```'
        )

    def test_no_tool_call_unchanged(self):
        resp = make_response(text="Hello")
        result = postprocess_response(resp)
        self.assertEqual(result.text, "Hello")
        self.assertEqual(result.tool_calls, [])

    def test_new_format_tool_call_extracted(self):
        block = '<tool_use>\n{"name":"bash","input":{"command":"ls"}}\n</tool_use>'
        resp = make_response(text=block)
        result = postprocess_response(resp)
        self.assertTrue(result.has_tool_calls)
        self.assertEqual(result.tool_calls[0].name, "bash")
        self.assertEqual(result.stop_reason, "tool_use")

    def test_legacy_format_tool_call_extracted(self):
        block = self._call_block("bash", "tc_1", {"command": "ls"})
        resp = make_response(text=block)
        result = postprocess_response(resp)
        self.assertTrue(result.has_tool_calls)
        self.assertEqual(result.tool_calls[0].name, "bash")
        self.assertEqual(result.stop_reason, "tool_use")

    def test_prose_preserved_after_extraction(self):
        block = self._call_block()
        resp = make_response(text="I'll run this.\n" + block)
        result = postprocess_response(resp)
        self.assertIn("I'll run this", result.text)
        self.assertNotIn("```tool_call", result.text)
        self.assertNotIn("<tool_use>", result.text)

    def test_stop_reason_updated_to_tool_use(self):
        block = self._call_block()
        resp = make_response(text=block)
        result = postprocess_response(resp)
        self.assertEqual(result.stop_reason, "tool_use")

    def test_usage_preserved(self):
        block = self._call_block()
        resp = make_response(text=block)
        result = postprocess_response(resp)
        self.assertEqual(result.usage.input_tokens, 10)


# ══════════════════════════════════════════════════════════════════════════════
# DebugConfig & LLMDebugLogger 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestDebugConfig(unittest.TestCase):

    def test_default_disabled(self):
        cfg = DebugConfig()
        self.assertFalse(cfg.enabled)
        self.assertTrue(cfg.log_to_file)
        self.assertFalse(cfg.log_to_console)

    def test_from_env_disabled_by_default(self):
        import os
        env = {k: v for k, v in os.environ.items()
               if k not in ("LLM_DEBUG", "LLM_DEBUG_CONSOLE", "LLM_DEBUG_LOG_DIR")}
        with patch.dict(os.environ, env, clear=True):
            cfg = DebugConfig.from_env()
        self.assertFalse(cfg.enabled)

    def test_from_env_enabled_by_env_var(self):
        with patch.dict(__import__("os").environ, {"LLM_DEBUG": "1"}):
            cfg = DebugConfig.from_env()
        self.assertTrue(cfg.enabled)

    def test_from_env_console_var(self):
        with patch.dict(__import__("os").environ, {"LLM_DEBUG_CONSOLE": "true"}):
            cfg = DebugConfig.from_env()
        self.assertTrue(cfg.log_to_console)


class TestLLMDebugLogger(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_logger(self, console=False) -> LLMDebugLogger:
        cfg = DebugConfig(enabled=True, log_to_file=True,
                          log_to_console=console, log_dir=self.tmp)
        return LLMDebugLogger(cfg, project_root=self.tmp)

    def _read_log(self, logger: LLMDebugLogger) -> list[dict]:
        if not logger.log_file or not logger.log_file.exists():
            return []
        lines = logger.log_file.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(l) for l in lines if l.strip()]

    def test_log_file_created(self):
        logger = self._make_logger()
        self.assertIsNotNone(logger.log_file)
        self.assertTrue(logger.log_file.exists())

    def test_disabled_logger_writes_nothing(self):
        cfg = DebugConfig(enabled=False)
        logger = LLMDebugLogger(cfg)
        seq = logger.log_request(provider="anthropic", model="claude-3", raw_system="system", raw_messages=[], raw_tools=[], actual_system="system", actual_api_tools=[], stream=False)
        self.assertEqual(seq, 0)
        self.assertIsNone(logger.log_file)

    def test_request_logged(self):
        logger = self._make_logger()
        messages = [{"role": "user", "content": "Hi"}]
        seq = logger.log_request(
            provider="anthropic", model="claude-opus-4-5",
            raw_system="Be helpful", raw_messages=messages, raw_tools=[],
            actual_system="Be helpful\n\n## Tool Call...",
            actual_api_tools=[], stream=False,
        )
        entries = self._read_log(logger)
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["event"], "request")
        self.assertEqual(e["provider"], "anthropic")
        self.assertEqual(e["model"], "claude-opus-4-5")
        self.assertEqual(e["seq"], seq)
        # 新格式：request 下有 raw 和 actual 两层
        self.assertIn("raw", e["request"])
        self.assertIn("actual", e["request"])
        self.assertEqual(e["request"]["raw"]["system"], "Be helpful")
        self.assertEqual(e["request"]["actual"]["api_tools"], [])
        self.assertEqual(e["request"]["raw"]["stream"], False)

    def test_response_logged(self):
        logger = self._make_logger()
        seq = logger.log_request(
            provider="openai", model="gpt-4o",
            raw_system="sys", raw_messages=[], raw_tools=[],
            actual_system="sys\n\n## Tool Call...", actual_api_tools=[],
            stream=True,
        )
        response = make_response(text="Hello")
        response.reasoning = ""
        logger.log_response(seq, "openai", "gpt-4o",
                            raw_response=response,
                            processed_response=response,
                            duration_ms=350)
        entries = self._read_log(logger)
        resp_entry = next(e for e in entries if e["event"] == "response")
        self.assertEqual(resp_entry["seq"], seq)
        self.assertEqual(resp_entry["duration_ms"], 350)
        # 新格式：response 下有 raw 和 processed
        self.assertIn("raw", resp_entry["response"])
        self.assertIn("processed", resp_entry["response"])
        self.assertEqual(resp_entry["response"]["raw"]["text"], "Hello")
        self.assertEqual(resp_entry["response"]["raw"]["usage"]["input_tokens"], 10)

    def test_error_logged(self):
        logger = self._make_logger()
        seq = logger.log_request(
            provider="nvidia", model="step-3.5",
            raw_system="sys", raw_messages=[], raw_tools=[],
            actual_system="sys", actual_api_tools=[], stream=False,
        )
        logger.log_error(seq, "nvidia", "step-3.5", ValueError("timeout"), 1200)
        entries = self._read_log(logger)
        err_entry = next(e for e in entries if e["event"] == "error")
        self.assertEqual(err_entry["event"], "error")
        self.assertIn("timeout", err_entry["error"])
        self.assertEqual(err_entry["duration_ms"], 1200)

    def test_seq_increments(self):
        logger = self._make_logger()
        s1 = logger.log_request(provider="a", model="m", raw_system="s", raw_messages=[], raw_tools=[], actual_system="s", actual_api_tools=[], stream=False)
        s2 = logger.log_request(provider="a", model="m", raw_system="s", raw_messages=[], raw_tools=[], actual_system="s", actual_api_tools=[], stream=False)
        self.assertGreater(s2, s1)

    def test_log_entries_are_valid_jsonl(self):
        logger = self._make_logger()
        logger.log_request(
            provider="p", model="m",
            raw_system="system", raw_messages=[{"role": "user", "content": "hi"}], raw_tools=[],
            actual_system="system", actual_api_tools=[], stream=False,
        )
        lines = logger.log_file.read_text().strip().splitlines()
        for line in lines:
            json.loads(line)  # must not raise

    def test_system_text_truncated_when_long(self):
        logger = self._make_logger()
        long_system = "x" * 20000
        logger.log_request(
            provider="a", model="m",
            raw_system=long_system, raw_messages=[], raw_tools=[],
            actual_system=long_system, actual_api_tools=[], stream=False,
        )
        entries = self._read_log(logger)
        logged_system = entries[0]["request"]["raw"]["system"]
        self.assertLess(len(logged_system), 20000)
        self.assertIn("truncated", logged_system)

    def test_message_content_truncated(self):
        logger = self._make_logger()
        long_msg = [{"role": "user", "content": "y" * 20000}]
        logger.log_request(
            provider="a", model="m",
            raw_system="sys", raw_messages=long_msg, raw_tools=[],
            actual_system="sys", actual_api_tools=[], stream=False,
        )
        entries = self._read_log(logger)
        msg_content = entries[0]["request"]["raw"]["messages"][0]["content"]
        self.assertLess(len(msg_content), 20000)

    def test_response_tool_calls_logged(self):
        logger = self._make_logger()
        seq = logger.log_request(
            provider="a", model="m",
            raw_system="s", raw_messages=[], raw_tools=[],
            actual_system="s", actual_api_tools=[], stream=False,
        )
        tc = ToolCall(id="tc_1", name="bash", input={"command": "ls"})
        resp = LLMResponse(text="", tool_calls=[tc], usage=LLMUsage(5, 10, 15),
                           stop_reason="tool_use")
        resp.reasoning = ""
        logger.log_response(seq, "a", "m",
                            raw_response=resp, processed_response=resp,
                            duration_ms=100)
        entries = self._read_log(logger)
        resp_entry = next(e for e in entries if e["event"] == "response")
        # processed 里有 tool_calls
        self.assertEqual(len(resp_entry["response"]["processed"]["tool_calls"]), 1)
        self.assertEqual(resp_entry["response"]["processed"]["tool_calls"][0]["name"], "bash")


# ══════════════════════════════════════════════════════════════════════════════
# ProviderMixin._prepare_tools 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestProviderMixinPrepareTools(unittest.TestCase):

    def _make_mixin(self, use_system_tc: bool):
        """Create a minimal object with ProviderMixin behaviour."""
        from mini_agent.llm.providers._base_mixin import ProviderMixin
        from mini_agent.llm.base import LLMClient

        class MinimalProvider(ProviderMixin, LLMClient):
            def chat(self, m, s, t): ...
            def stream(self, m, s, t, cb): ...

        cfg = LLMConfig(provider="openai", model="gpt-4o", api_key="k",
                        use_system_tool_call=use_system_tc)
        with patch.object(MinimalProvider, "__abstractmethods__", set()):
            obj = MinimalProvider.__new__(MinimalProvider)
            obj.config = cfg
        return obj

    def test_tools_always_go_via_system(self):
        """工具始终通过 system prompt 传递，api_tools 始终为空。"""
        mixin = self._make_mixin(use_system_tc=False)   # flag is now ignored
        _, tools_out = mixin._prepare_tools("Be helpful", SAMPLE_TOOLS)
        self.assertEqual(tools_out, [])

    def test_protocol_always_injected(self):
        """工具协议说明始终注入到 system prompt。"""
        mixin = self._make_mixin(use_system_tc=False)
        system_out, _ = mixin._prepare_tools("Be helpful", SAMPLE_TOOLS)
        self.assertIn("tool_use", system_out)      # 协议注入（新格式）
        self.assertIn("bash", system_out)

    def test_system_tc_flag_makes_no_difference(self):
        """use_system_tool_call flag 现在不影响行为（全局统一）。"""
        m1 = self._make_mixin(use_system_tc=False)
        m2 = self._make_mixin(use_system_tc=True)
        out1, t1 = m1._prepare_tools("sys", SAMPLE_TOOLS)
        out2, t2 = m2._prepare_tools("sys", SAMPLE_TOOLS)
        self.assertEqual(t1, t2)
        self.assertEqual(out1, out2)

    def test_empty_tools_system_unchanged(self):
        """无工具时 system 不变，api_tools 为空。"""
        mixin = self._make_mixin(use_system_tc=False)
        system_out, tools_out = mixin._prepare_tools("Be helpful", [])
        self.assertEqual(system_out, "Be helpful")
        self.assertEqual(tools_out, [])


# ══════════════════════════════════════════════════════════════════════════════
# Agent 集成：两种 tool result 消息格式
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentToolResultFormat(unittest.TestCase):

    def _make_agent(self, use_system_tc: bool, responses: list):
        from mini_agent.agent import Agent
        from mini_agent.config import load_config
        from mini_agent.permissions import PermissionGuard

        cfg = load_config()
        cfg.stream = False
        cfg.use_system_tool_call = use_system_tc

        mock_client = MagicMock()
        mock_client.chat.side_effect = responses
        guard = PermissionGuard(auto_approve=True)
        return Agent(cfg=cfg, guard=guard, llm_client=mock_client)

    def test_tool_result_uses_xml_format(self):
        """工具结果统一用 <tool_result> 文本格式（无论 use_system_tool_call 设置）。"""
        tc = ToolCall(id="tc_1", name="bash", input={"command": "echo hi"})
        responses = [
            LLMResponse(text="", tool_calls=[tc], usage=LLMUsage(), stop_reason="tool_use"),
            make_response(text="Done"),
        ]
        agent = self._make_agent(use_system_tc=False, responses=responses)
        agent.run_turn("run echo hi")

        tool_result_msg = next(
            m for m in agent._history
            if m["role"] == "user"
            and isinstance(m["content"], str)
            and "<tool_result>" in m["content"]
        )
        self.assertIn("<tool_result>", tool_result_msg["content"])
        self.assertIn("bash", tool_result_msg["content"])

    def test_system_tc_mode_also_uses_xml_format(self):
        """system_tc 模式同样使用 <tool_result> 格式。"""
        tc = ToolCall(id="tc_2", name="bash", input={"command": "ls"})
        responses = [
            LLMResponse(text="", tool_calls=[tc], usage=LLMUsage(), stop_reason="tool_use"),
            make_response(text="Done"),
        ]
        agent = self._make_agent(use_system_tc=True, responses=responses)
        agent.run_turn("list files")

        tool_result_msg = next(
            m for m in agent._history
            if m["role"] == "user"
            and isinstance(m["content"], str)
            and "<tool_result>" in m["content"]
        )
        self.assertIn("<tool_result>", tool_result_msg["content"])
        self.assertIn("bash", tool_result_msg["content"])


class TestThinkingExtraction(unittest.TestCase):
    """测试通用 thinking 标签提取逻辑。"""

    def test_extract_think_tag(self):
        from mini_agent.llm.system_tool_call import extract_thinking_blocks
        text = "<think>Step 1: analyse</think>\nFinal answer."
        clean, thinking = extract_thinking_blocks(text)
        self.assertEqual(clean, "Final answer.")
        self.assertIn("Step 1", thinking)

    def test_extract_thinking_tag(self):
        from mini_agent.llm.system_tool_call import extract_thinking_blocks
        text = "<thinking>reasoning here</thinking>\nResult"
        clean, thinking = extract_thinking_blocks(text)
        self.assertEqual(clean, "Result")
        self.assertIn("reasoning here", thinking)

    def test_extract_reasoning_tag(self):
        from mini_agent.llm.system_tool_call import extract_thinking_blocks
        text = "<reasoning>deep thought</reasoning>\nAnswer"
        clean, thinking = extract_thinking_blocks(text)
        self.assertEqual(clean, "Answer")
        self.assertIn("deep thought", thinking)

    def test_multiple_think_blocks_merged(self):
        from mini_agent.llm.system_tool_call import extract_thinking_blocks
        text = "<think>part1</think>\nmiddle<think>part2</think>\nend"
        clean, thinking = extract_thinking_blocks(text)
        self.assertIn("part1", thinking)
        self.assertIn("part2", thinking)
        self.assertIn("middle", clean)

    def test_no_think_block_unchanged(self):
        from mini_agent.llm.system_tool_call import extract_thinking_blocks
        text = "Plain response without thinking."
        clean, thinking = extract_thinking_blocks(text)
        self.assertEqual(clean, text)
        self.assertEqual(thinking, "")

    def test_postprocess_extracts_thinking(self):
        from mini_agent.llm.system_tool_call import postprocess_response
        from mini_agent.llm.base import LLMResponse, LLMUsage
        resp = LLMResponse(
            text="<think>internal</think>\nVisible answer.",
            tool_calls=[], usage=LLMUsage(), stop_reason="end_turn"
        )
        result = postprocess_response(resp)
        self.assertEqual(result.text, "Visible answer.")
        self.assertIn("internal", result.reasoning)

    def test_postprocess_merges_streaming_and_tag_reasoning(self):
        """流式 reasoning_content 和文本中的 <think> 标签应合并，不丢失。"""
        from mini_agent.llm.system_tool_call import postprocess_response
        from mini_agent.llm.base import LLMResponse, LLMUsage
        resp = LLMResponse(
            text="<think>tag-thinking</think>\nAnswer",
            reasoning="stream-reasoning",   # already collected via on_reasoning
            tool_calls=[], usage=LLMUsage(), stop_reason="end_turn"
        )
        result = postprocess_response(resp)
        self.assertIn("stream-reasoning", result.reasoning)
        self.assertIn("tag-thinking", result.reasoning)

    def test_postprocess_tool_call_and_thinking_together(self):
        """同时包含 <think> 和 ```tool_call 块的响应应分别提取。"""
        import json
        from mini_agent.llm.system_tool_call import postprocess_response
        from mini_agent.llm.base import LLMResponse, LLMUsage
        block = '```tool_call\n{"tool":"bash","id":"t1","parameters":{"command":"ls"}}\n```'
        text = f"<think>I should list files</think>\n{block}"
        resp = LLMResponse(text=text, tool_calls=[], usage=LLMUsage(), stop_reason="end_turn")
        result = postprocess_response(resp)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "bash")
        self.assertIn("I should list files", result.reasoning)
        self.assertNotIn("```tool_call", result.text)
        self.assertNotIn("<think>", result.text)

    def test_case_insensitive_think_tag(self):
        from mini_agent.llm.system_tool_call import extract_thinking_blocks
        text = "<THINK>case insensitive</THINK>\nResult"
        clean, thinking = extract_thinking_blocks(text)
        self.assertIn("case insensitive", thinking)
        self.assertEqual(clean, "Result")

    def test_postprocess_noop_when_nothing_to_extract(self):
        """没有 think 块和 tool_call 时应直接返回原对象。"""
        from mini_agent.llm.system_tool_call import postprocess_response
        from mini_agent.llm.base import LLMResponse, LLMUsage
        resp = LLMResponse(
            text="Hello world", tool_calls=[], usage=LLMUsage(), stop_reason="end_turn"
        )
        result = postprocess_response(resp)
        self.assertIs(result, resp)   # same object returned


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
