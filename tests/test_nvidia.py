"""
tests/test_nvidia.py

NVIDIA NIM provider 的完整测试套件，覆盖：
  - 配置自动填充（base_url、api_key 环境变量）
  - 普通响应解析
  - reasoning_content 流式解析
  - <think>...</think> 块提取（非流式推理）
  - 工具调用解析
  - factory 注册（nvidia / nim 别名）
  - Agent 集成（含思维链回调）
  - 错误包装
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from llm.base import (
    LLMConfig, LLMResponse, LLMUsage, ToolCall, ToolSchema,
    LLMProviderError, LLMRateLimitError, LLMTimeoutError,
)
from llm.providers.nvidia import NvidiaProvider, _extract_think_block, _NVIDIA_BASE_URL
from llm.factory import create_client, list_providers, _REGISTRY


# ── 测试工具 ──────────────────────────────────────────────────────────────────

SAMPLE_TOOLS = [
    ToolSchema(
        name="bash",
        description="Run a shell command",
        input_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    )
]

SAMPLE_MESSAGES = [{"role": "user", "content": "Hello"}]


def make_nvidia_config(**kwargs) -> LLMConfig:
    defaults = dict(
        provider="nvidia",
        model="stepfun-ai/step-3.5-flash",
        api_key="nvapi-test-key",
    )
    defaults.update(kwargs)
    return LLMConfig(**defaults)


def make_provider(config: LLMConfig | None = None) -> NvidiaProvider:
    cfg = config or make_nvidia_config()
    with patch.object(NvidiaProvider, "_build_client", return_value=MagicMock()):
        return NvidiaProvider(cfg)


def make_sdk_response(text="Hello", reasoning=None, tool_calls=None):
    """构造模拟的 OpenAI ChatCompletion 响应对象。"""
    resp = MagicMock()
    choice = MagicMock()
    full_text = text
    if reasoning:
        full_text = f"<think>{reasoning}</think>\n{text}"
    choice.message.content = full_text
    choice.message.tool_calls = []
    for tc in (tool_calls or []):
        mock_tc = MagicMock()
        mock_tc.id = tc["id"]
        mock_tc.function.name = tc["name"]
        mock_tc.function.arguments = json.dumps(tc["input"])
        choice.message.tool_calls.append(mock_tc)
    choice.finish_reason = "tool_calls" if tool_calls else "stop"
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=20, completion_tokens=40, total_tokens=60)
    return resp


# ══════════════════════════════════════════════════════════════════════════════
# _extract_think_block 辅助函数测试
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractThinkBlock(unittest.TestCase):

    def test_no_think_block(self):
        text, reasoning = _extract_think_block("Hello world")
        self.assertEqual(text, "Hello world")
        self.assertEqual(reasoning, "")

    def test_single_think_block(self):
        raw = "<think>Step 1: think hard\nStep 2: conclude</think>\nFinal answer."
        text, reasoning = _extract_think_block(raw)
        self.assertEqual(text, "Final answer.")
        self.assertIn("Step 1", reasoning)
        self.assertIn("Step 2", reasoning)

    def test_think_block_stripped_from_text(self):
        raw = "<think>reasoning here</think>\nActual response"
        text, reasoning = _extract_think_block(raw)
        self.assertNotIn("<think>", text)
        self.assertNotIn("</think>", text)

    def test_multiple_think_blocks(self):
        raw = "<think>first</think>\nmiddle<think>second</think>\nend"
        text, reasoning = _extract_think_block(raw)
        self.assertIn("first", reasoning)
        self.assertIn("second", reasoning)
        self.assertIn("middle", text)
        self.assertIn("end", text)

    def test_empty_think_block(self):
        raw = "<think></think>\nJust the response."
        text, reasoning = _extract_think_block(raw)
        self.assertEqual(text, "Just the response.")
        self.assertEqual(reasoning, "")

    def test_multiline_content_preserved(self):
        raw = "<think>\nline1\nline2\nline3\n</think>\nResponse"
        text, reasoning = _extract_think_block(raw)
        self.assertIn("line1", reasoning)
        self.assertIn("line3", reasoning)


# ══════════════════════════════════════════════════════════════════════════════
# NvidiaProvider 配置测试
# ══════════════════════════════════════════════════════════════════════════════

class TestNvidiaProviderConfig(unittest.TestCase):

    def test_auto_sets_base_url(self):
        cfg = make_nvidia_config()
        self.assertEqual(cfg.base_url, None)  # before __init__
        provider = make_provider(cfg)
        self.assertEqual(provider.config.base_url, _NVIDIA_BASE_URL)

    def test_respects_custom_base_url(self):
        cfg = make_nvidia_config(base_url="https://my-proxy.example.com/v1")
        provider = make_provider(cfg)
        self.assertEqual(provider.config.base_url, "https://my-proxy.example.com/v1")

    def test_reads_api_key_from_env(self):
        cfg = LLMConfig(provider="nvidia", model="some-model", api_key="")
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-from-env"}):
            with patch.object(NvidiaProvider, "_build_client", return_value=MagicMock()):
                provider = NvidiaProvider(cfg)
        self.assertEqual(provider.config.api_key, "nvapi-from-env")

    def test_explicit_api_key_takes_priority(self):
        cfg = make_nvidia_config(api_key="nvapi-explicit")
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-from-env"}):
            provider = make_provider(cfg)
        self.assertEqual(provider.config.api_key, "nvapi-explicit")

    def test_provider_name(self):
        self.assertEqual(make_provider().provider_name, "NVIDIA")

    def test_supports_reasoning_known_model(self):
        cfg = make_nvidia_config(model="stepfun-ai/step-3.5-flash")
        provider = make_provider(cfg)
        self.assertTrue(provider.supports_reasoning())

    def test_supports_reasoning_unknown_model(self):
        cfg = make_nvidia_config(model="meta/llama-3.1-8b-instruct")
        provider = make_provider(cfg)
        self.assertFalse(provider.supports_reasoning())


# ══════════════════════════════════════════════════════════════════════════════
# 非流式响应解析测试
# ══════════════════════════════════════════════════════════════════════════════

class TestNvidiaProviderChat(unittest.TestCase):

    def setUp(self):
        self.provider = make_provider()

    def test_plain_text_response(self):
        self.provider._client.chat.completions.create.return_value = \
            make_sdk_response(text="Hello from NVIDIA")
        resp = self.provider.chat(SAMPLE_MESSAGES, "system", [])
        self.assertEqual(resp.text, "Hello from NVIDIA")
        self.assertEqual(resp.reasoning, "")
        self.assertFalse(resp.has_tool_calls)

    def test_response_with_think_block(self):
        self.provider._client.chat.completions.create.return_value = \
            make_sdk_response(text="Final answer", reasoning="I need to think carefully")
        resp = self.provider.chat(SAMPLE_MESSAGES, "system", [])
        self.assertEqual(resp.text, "Final answer")
        self.assertIn("think carefully", resp.reasoning)

    def test_response_with_tool_calls(self):
        self.provider._client.chat.completions.create.return_value = \
            make_sdk_response(
                text="",
                tool_calls=[{"id": "call_1", "name": "bash", "input": {"command": "ls"}}]
            )
        resp = self.provider.chat(SAMPLE_MESSAGES, "system", SAMPLE_TOOLS)
        self.assertTrue(resp.has_tool_calls)
        self.assertEqual(resp.tool_calls[0].name, "bash")
        self.assertEqual(resp.tool_calls[0].input["command"], "ls")

    def test_usage_mapped_correctly(self):
        self.provider._client.chat.completions.create.return_value = \
            make_sdk_response()
        resp = self.provider.chat(SAMPLE_MESSAGES, "system", [])
        self.assertEqual(resp.usage.input_tokens, 20)
        self.assertEqual(resp.usage.output_tokens, 40)
        self.assertEqual(resp.usage.total_tokens, 60)

    def test_stop_reason_mapped(self):
        self.provider._client.chat.completions.create.return_value = \
            make_sdk_response()
        resp = self.provider.chat(SAMPLE_MESSAGES, "system", [])
        self.assertEqual(resp.stop_reason, "end_turn")

    def test_tool_use_stop_reason(self):
        self.provider._client.chat.completions.create.return_value = \
            make_sdk_response(
                tool_calls=[{"id": "tc1", "name": "bash", "input": {"command": "pwd"}}]
            )
        resp = self.provider.chat(SAMPLE_MESSAGES, "system", SAMPLE_TOOLS)
        self.assertEqual(resp.stop_reason, "tool_use")


# ══════════════════════════════════════════════════════════════════════════════
# 流式响应测试（重点：reasoning_content）
# ══════════════════════════════════════════════════════════════════════════════

class TestNvidiaProviderStream(unittest.TestCase):

    def _make_stream_events(
        self,
        text_tokens: list[str],
        reasoning_tokens: list[str] | None = None,
        finish_reason: str = "stop",
        usage_tokens: tuple[int, int] = (10, 20),
    ):
        """构造模拟的流式事件列表。"""
        events = []

        # Reasoning token events
        for token in (reasoning_tokens or []):
            event = MagicMock()
            choice = MagicMock()
            delta = MagicMock()
            delta.content = None
            delta.reasoning_content = token
            delta.tool_calls = None
            choice.delta = delta
            choice.finish_reason = None
            event.choices = [choice]
            events.append(event)

        # Text token events
        for token in text_tokens:
            event = MagicMock()
            choice = MagicMock()
            delta = MagicMock()
            delta.content = token
            delta.reasoning_content = None
            delta.tool_calls = None
            choice.delta = delta
            choice.finish_reason = None
            event.choices = [choice]
            events.append(event)

        # Final event with finish_reason
        final_event = MagicMock()
        final_choice = MagicMock()
        final_delta = MagicMock()
        final_delta.content = None
        final_delta.reasoning_content = None
        final_delta.tool_calls = None
        final_choice.delta = final_delta
        final_choice.finish_reason = finish_reason
        final_event.choices = [final_choice]
        events.append(final_event)

        return events

    def _mock_stream_context(self, events, usage=(10, 20)):
        """构造可作为 context manager 使用的 mock stream。"""
        mock_stream = MagicMock()
        mock_stream.__iter__ = lambda s: iter(events)
        mock_stream.__enter__ = lambda s: s
        mock_stream.__exit__ = MagicMock(return_value=False)
        final = MagicMock()
        final.usage = MagicMock(
            prompt_tokens=usage[0],
            completion_tokens=usage[1],
            total_tokens=sum(usage),
        )
        mock_stream.get_final_completion.return_value = final
        return mock_stream

    def setUp(self):
        self.provider = make_provider()

    def test_text_tokens_collected(self):
        events = self._make_stream_events(["Hello", " world"])
        mock_ctx = self._mock_stream_context(events)
        self.provider._client.chat.completions.stream.return_value = mock_ctx

        tokens = []
        resp = self.provider.stream(SAMPLE_MESSAGES, "system", [], on_token=tokens.append)
        self.assertEqual("".join(tokens), "Hello world")
        self.assertEqual(resp.text, "Hello world")

    def test_reasoning_tokens_separated(self):
        events = self._make_stream_events(
            text_tokens=["Answer"],
            reasoning_tokens=["Step 1 ", "Step 2"],
        )
        mock_ctx = self._mock_stream_context(events)
        self.provider._client.chat.completions.stream.return_value = mock_ctx

        text_tokens, reasoning_tokens = [], []
        resp = self.provider.stream(
            SAMPLE_MESSAGES, "system", [],
            on_token=text_tokens.append,
            on_reasoning=reasoning_tokens.append,
        )
        self.assertEqual("".join(text_tokens), "Answer")
        self.assertEqual("".join(reasoning_tokens), "Step 1 Step 2")
        self.assertEqual(resp.reasoning, "Step 1 Step 2")
        self.assertEqual(resp.text, "Answer")

    def test_reasoning_callback_optional(self):
        """on_reasoning 不传时，思维链 token 静默忽略，不报错。"""
        events = self._make_stream_events(
            text_tokens=["Result"],
            reasoning_tokens=["thinking..."],
        )
        mock_ctx = self._mock_stream_context(events)
        self.provider._client.chat.completions.stream.return_value = mock_ctx

        tokens = []
        resp = self.provider.stream(
            SAMPLE_MESSAGES, "system", [],
            on_token=tokens.append,
            # on_reasoning 不传
        )
        self.assertEqual(resp.text, "Result")
        self.assertEqual(resp.reasoning, "thinking...")

    def test_reasoning_not_leaked_into_text(self):
        """思维链内容不应出现在 resp.text 中。"""
        events = self._make_stream_events(
            text_tokens=["Clean answer"],
            reasoning_tokens=["internal monologue"],
        )
        mock_ctx = self._mock_stream_context(events)
        self.provider._client.chat.completions.stream.return_value = mock_ctx

        resp = self.provider.stream(SAMPLE_MESSAGES, "system", [], on_token=lambda t: None)
        self.assertNotIn("internal monologue", resp.text)

    def test_usage_from_final_completion(self):
        events = self._make_stream_events(["hi"])
        mock_ctx = self._mock_stream_context(events, usage=(15, 30))
        self.provider._client.chat.completions.stream.return_value = mock_ctx

        resp = self.provider.stream(SAMPLE_MESSAGES, "system", [], on_token=lambda t: None)
        self.assertEqual(resp.usage.input_tokens, 15)
        self.assertEqual(resp.usage.output_tokens, 30)


# ══════════════════════════════════════════════════════════════════════════════
# Tool schema 格式测试
# ══════════════════════════════════════════════════════════════════════════════

class TestNvidiaToolFormat(unittest.TestCase):

    def test_format_uses_parameters_not_input_schema(self):
        provider = make_provider()
        result = provider.format_tools(SAMPLE_TOOLS)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "function")
        self.assertIn("parameters", result[0]["function"])
        self.assertNotIn("input_schema", result[0]["function"])

    def test_tool_name_and_description_preserved(self):
        provider = make_provider()
        result = provider.format_tools(SAMPLE_TOOLS)
        fn = result[0]["function"]
        self.assertEqual(fn["name"], "bash")
        self.assertEqual(fn["description"], "Run a shell command")


# ══════════════════════════════════════════════════════════════════════════════
# 错误处理测试
# ══════════════════════════════════════════════════════════════════════════════

class TestNvidiaErrorHandling(unittest.TestCase):

    def setUp(self):
        self.provider = make_provider()

    def test_rate_limit_wrapped(self):
        from openai import RateLimitError
        import httpx
        mock_response = httpx.Response(429, request=httpx.Request("POST", "https://x.com"))
        self.provider._client.chat.completions.create.side_effect = \
            RateLimitError("rate limit", response=mock_response, body={})
        with self.assertRaises(LLMRateLimitError):
            self.provider.chat(SAMPLE_MESSAGES, "system", [])

    def test_timeout_wrapped(self):
        from openai import APITimeoutError
        import httpx
        mock_request = httpx.Request("POST", "https://x.com")
        self.provider._client.chat.completions.create.side_effect = \
            APITimeoutError(request=mock_request)
        with self.assertRaises(LLMTimeoutError):
            self.provider.chat(SAMPLE_MESSAGES, "system", [])

    def test_generic_api_error_wrapped(self):
        from openai import APIError
        import httpx
        mock_request = httpx.Request("POST", "https://x.com")
        self.provider._client.chat.completions.create.side_effect = \
            APIError("bad request", request=mock_request, body={})
        with self.assertRaises(LLMProviderError):
            self.provider.chat(SAMPLE_MESSAGES, "system", [])


# ══════════════════════════════════════════════════════════════════════════════
# Factory 注册测试
# ══════════════════════════════════════════════════════════════════════════════

class TestNvidiaFactoryRegistration(unittest.TestCase):

    def test_nvidia_in_registry(self):
        self.assertIn("nvidia", _REGISTRY)

    def test_nim_alias_in_registry(self):
        self.assertIn("nim", _REGISTRY)

    def test_nvidia_in_list_providers(self):
        self.assertIn("nvidia", list_providers())

    def test_nim_not_duplicated_in_list_providers(self):
        providers = list_providers()
        self.assertEqual(providers.count("nvidia"), 1)
        self.assertNotIn("nim", providers)   # nim is alias, not canonical

    def test_create_nvidia_client(self):
        cfg = make_nvidia_config()
        with patch.object(NvidiaProvider, "_build_client", return_value=MagicMock()):
            client = create_client(cfg)
        self.assertIsInstance(client, NvidiaProvider)

    def test_create_nim_alias(self):
        cfg = LLMConfig(provider="nim", model="meta/llama-3.1-8b-instruct", api_key="k")
        with patch.object(NvidiaProvider, "_build_client", return_value=MagicMock()):
            client = create_client(cfg)
        self.assertIsInstance(client, NvidiaProvider)


# ══════════════════════════════════════════════════════════════════════════════
# Agent 集成测试
# ══════════════════════════════════════════════════════════════════════════════

class TestNvidiaAgentIntegration(unittest.TestCase):

    def _make_agent_with_nvidia(self, responses):
        import tools.builtin  # noqa
        from agent import Agent
        from config import load_config
        from permissions import PermissionGuard

        cfg = load_config()
        cfg.stream = False

        mock_client = MagicMock(spec=NvidiaProvider)
        # NvidiaProvider has on_reasoning in stream signature
        import inspect
        mock_client.stream = MagicMock(side_effect=responses)
        mock_client.chat = MagicMock(side_effect=responses)

        guard = PermissionGuard(auto_approve=True)
        return Agent(cfg=cfg, guard=guard, llm_client=mock_client)

    def test_reasoning_field_in_response(self):
        resp = LLMResponse(
            text="The answer is 42.",
            reasoning="Let me think... 6 * 7 = 42.",
            tool_calls=[],
            usage=LLMUsage(input_tokens=10, output_tokens=20, total_tokens=30),
            stop_reason="end_turn",
        )
        agent = self._make_agent_with_nvidia([resp])
        result = agent.run_turn("What is 6*7?")
        self.assertEqual(result, "The answer is 42.")

    def test_response_without_reasoning_still_works(self):
        resp = LLMResponse(
            text="Hello!",
            reasoning="",
            tool_calls=[],
            usage=LLMUsage(),
            stop_reason="end_turn",
        )
        agent = self._make_agent_with_nvidia([resp])
        result = agent.run_turn("Hi")
        self.assertEqual(result, "Hello!")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
