"""
tests/test_llm.py

LLM 抽象层的完整测试套件，覆盖：
  - 数据结构（LLMConfig、LLMResponse、ToolCall、LLMUsage）
  - LLMClient 抽象接口的合约
  - Factory 的注册、创建、错误处理
  - AnthropicProvider 的响应解析逻辑（Mock SDK）
  - OpenAIProvider  的响应解析逻辑（Mock SDK）
  - OllamaProvider  的响应解析逻辑（Mock HTTP）
  - Agent 与 LLMClient 的集成（注入 Mock client）
  - 运行时 provider 切换
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mini_agent.llm.base import (
    LLMClient, LLMConfig, LLMResponse, LLMUsage,
    ToolCall, ToolSchema, LLMConfigError, LLMProviderError,
)
from mini_agent.llm.factory import create_client, register_provider, list_providers, _REGISTRY


# ══════════════════════════════════════════════════════════════════════════════
# 测试数据
# ══════════════════════════════════════════════════════════════════════════════

def make_config(provider="anthropic", model="claude-opus-4-5", api_key="test-key") -> LLMConfig:
    return LLMConfig(provider=provider, model=model, api_key=api_key)


def make_response(text="Hello", tool_calls=None, input_tokens=10, output_tokens=20) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=tool_calls or [],
        usage=LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens,
                       total_tokens=input_tokens + output_tokens),
        stop_reason="end_turn",
    )


SAMPLE_TOOLS = [
    ToolSchema(name="bash", description="Run shell command",
               input_schema={"type": "object", "properties": {"command": {"type": "string"}},
                             "required": ["command"]})
]

SAMPLE_MESSAGES = [{"role": "user", "content": "Hello"}]


# ══════════════════════════════════════════════════════════════════════════════
# 数据结构单元测试
# ══════════════════════════════════════════════════════════════════════════════

class TestLLMUsage(unittest.TestCase):

    def test_addition(self):
        a = LLMUsage(input_tokens=10, output_tokens=20, total_tokens=30)
        b = LLMUsage(input_tokens=5,  output_tokens=10, total_tokens=15)
        c = a + b
        self.assertEqual(c.input_tokens, 15)
        self.assertEqual(c.output_tokens, 30)
        self.assertEqual(c.total_tokens, 45)

    def test_default_zeros(self):
        u = LLMUsage()
        self.assertEqual(u.input_tokens, 0)
        self.assertEqual(u.output_tokens, 0)


class TestLLMResponse(unittest.TestCase):

    def test_is_complete_when_no_tool_calls(self):
        r = make_response()
        self.assertTrue(r.is_complete)
        self.assertFalse(r.has_tool_calls)

    def test_has_tool_calls_when_present(self):
        tc = ToolCall(id="id1", name="bash", input={"command": "ls"})
        r = make_response(tool_calls=[tc])
        self.assertTrue(r.has_tool_calls)
        self.assertFalse(r.is_complete)

    def test_tool_call_fields(self):
        tc = ToolCall(id="abc", name="read_file", input={"path": "/tmp/x"})
        self.assertEqual(tc.id, "abc")
        self.assertEqual(tc.name, "read_file")
        self.assertEqual(tc.input["path"], "/tmp/x")


class TestLLMConfig(unittest.TestCase):

    def test_from_app_config_anthropic(self):
        mock_cfg = MagicMock()
        mock_cfg.llm_provider = "anthropic"
        mock_cfg.llm_base_url = ""
        mock_cfg.model = "claude-opus-4-5"
        mock_cfg.api_key = "sk-test"
        mock_cfg.max_tokens = 4096
        llm_cfg = LLMConfig.from_app_config(mock_cfg)
        self.assertEqual(llm_cfg.provider, "anthropic")
        self.assertEqual(llm_cfg.model, "claude-opus-4-5")
        self.assertTrue(llm_cfg.requires_api_key)

    def test_from_app_config_ollama_no_key_required(self):
        mock_cfg = MagicMock()
        mock_cfg.llm_provider = "ollama"
        mock_cfg.llm_base_url = ""
        mock_cfg.model = "llama3.1"
        mock_cfg.api_key = ""
        mock_cfg.max_tokens = 2048
        llm_cfg = LLMConfig.from_app_config(mock_cfg)
        self.assertFalse(llm_cfg.requires_api_key)

    def test_extra_dict_default_empty(self):
        cfg = LLMConfig(provider="openai", model="gpt-4o", api_key="k")
        self.assertIsInstance(cfg.extra, dict)
        self.assertEqual(len(cfg.extra), 0)


# ══════════════════════════════════════════════════════════════════════════════
# 抽象接口合约测试
# ══════════════════════════════════════════════════════════════════════════════

class ConcreteClient(LLMClient):
    """最小可用的 LLMClient 实现，用于合约测试。"""

    def chat(self, messages, system, tools):
        return make_response(text="chat response")

    def stream(self, messages, system, tools, on_token):
        on_token("stream ")
        on_token("response")
        return make_response(text="stream response")


class TestLLMClientContract(unittest.TestCase):

    def setUp(self):
        self.cfg = make_config()
        self.client = ConcreteClient(self.cfg)

    def test_chat_returns_llm_response(self):
        resp = self.client.chat(SAMPLE_MESSAGES, "system", SAMPLE_TOOLS)
        self.assertIsInstance(resp, LLMResponse)

    def test_stream_calls_callback(self):
        tokens = []
        resp = self.client.stream(SAMPLE_MESSAGES, "system", SAMPLE_TOOLS,
                                  on_token=lambda t: tokens.append(t))
        self.assertEqual(tokens, ["stream ", "response"])
        self.assertIsInstance(resp, LLMResponse)

    def test_provider_name_derived_from_class(self):
        self.assertIn("Concrete", self.client.provider_name)

    def test_validate_config_raises_without_api_key(self):
        cfg = LLMConfig(provider="anthropic", model="x", api_key="", requires_api_key=True)
        client = ConcreteClient(cfg)
        with self.assertRaises(LLMConfigError):
            client.validate_config()

    def test_validate_config_ok_without_key_when_not_required(self):
        cfg = LLMConfig(provider="ollama", model="x", api_key="", requires_api_key=False)
        client = ConcreteClient(cfg)
        client.validate_config()   # should not raise

    def test_format_tools_default_anthropic_format(self):
        result = self.client.format_tools(SAMPLE_TOOLS)
        self.assertEqual(len(result), 1)
        self.assertIn("input_schema", result[0])
        self.assertEqual(result[0]["name"], "bash")

    def test_repr_includes_model_and_provider(self):
        r = repr(self.client)
        self.assertIn("claude-opus-4-5", r)


# ══════════════════════════════════════════════════════════════════════════════
# Factory 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestFactory(unittest.TestCase):

    def test_create_anthropic_client(self):
        cfg = make_config(provider="anthropic")
        with patch("mini_agent.llm.providers.anthropic.AnthropicProvider._build_client", return_value=MagicMock()):
            client = create_client(cfg)
        from mini_agent.llm.providers.anthropic import AnthropicProvider
        self.assertIsInstance(client, AnthropicProvider)

    def test_unknown_provider_raises(self):
        cfg = make_config(provider="nonexistent-provider-xyz")
        with self.assertRaises(LLMConfigError) as ctx:
            create_client(cfg)
        self.assertIn("nonexistent-provider-xyz", str(ctx.exception))

    def test_register_custom_provider(self):
        register_provider("test-mock", lambda: ConcreteClient)
        cfg = LLMConfig(provider="test-mock", model="mock-model", api_key="k",
                        requires_api_key=False)
        client = create_client(cfg)
        self.assertIsInstance(client, ConcreteClient)
        # cleanup
        del _REGISTRY["test-mock"]

    def test_list_providers_returns_sorted_unique(self):
        providers = list_providers()
        self.assertIn("anthropic", providers)
        self.assertIn("openai", providers)
        self.assertIn("ollama", providers)
        # No duplicates
        self.assertEqual(len(providers), len(set(providers)))

    def test_claude_alias_maps_to_anthropic(self):
        cfg = make_config(provider="claude")
        with patch("mini_agent.llm.providers.anthropic.AnthropicProvider._build_client", return_value=MagicMock()):
            client = create_client(cfg)
        from mini_agent.llm.providers.anthropic import AnthropicProvider
        self.assertIsInstance(client, AnthropicProvider)


# ══════════════════════════════════════════════════════════════════════════════
# AnthropicProvider 响应解析测试
# ══════════════════════════════════════════════════════════════════════════════

class TestAnthropicProvider(unittest.TestCase):

    def _make_provider(self):
        from mini_agent.llm.providers.anthropic import AnthropicProvider
        cfg = make_config(provider="anthropic")
        with patch.object(AnthropicProvider, "_build_client", return_value=MagicMock()):
            return AnthropicProvider(cfg)

    def _make_sdk_response(self, text="Hello", tool_calls=None):
        """Build a minimal mock of anthropic.types.Message."""
        resp = MagicMock()
        content = []
        if text:
            block = MagicMock(); block.type = "text"; block.text = text
            content.append(block)
        for tc in (tool_calls or []):
            block = MagicMock(); block.type = "tool_use"
            block.id = tc["id"]; block.name = tc["name"]; block.input = tc["input"]
            content.append(block)
        resp.content = content
        resp.stop_reason = "end_turn" if not tool_calls else "tool_use"
        resp.usage = MagicMock(input_tokens=10, output_tokens=20)
        return resp

    def test_parse_text_response(self):
        provider = self._make_provider()
        raw = self._make_sdk_response(text="Hello world")
        resp = provider._parse_response(raw)
        self.assertEqual(resp.text, "Hello world")
        self.assertFalse(resp.has_tool_calls)
        self.assertEqual(resp.usage.input_tokens, 10)
        self.assertEqual(resp.usage.total_tokens, 30)

    def test_parse_tool_use_response(self):
        provider = self._make_provider()
        raw = self._make_sdk_response(
            text="",
            tool_calls=[{"id": "tc1", "name": "bash", "input": {"command": "ls"}}]
        )
        resp = provider._parse_response(raw)
        self.assertTrue(resp.has_tool_calls)
        self.assertEqual(resp.tool_calls[0].name, "bash")
        self.assertEqual(resp.tool_calls[0].input["command"], "ls")

    def test_parse_mixed_text_and_tools(self):
        provider = self._make_provider()
        raw = self._make_sdk_response(
            text="I'll run that.",
            tool_calls=[{"id": "tc2", "name": "read_file", "input": {"path": "/tmp/f"}}]
        )
        resp = provider._parse_response(raw)
        self.assertEqual(resp.text, "I'll run that.")
        self.assertEqual(len(resp.tool_calls), 1)

    def test_format_tools_uses_input_schema(self):
        provider = self._make_provider()
        result = provider.format_tools(SAMPLE_TOOLS)
        self.assertIn("input_schema", result[0])
        self.assertNotIn("parameters", result[0])

    def test_chat_delegates_to_sdk(self):
        provider = self._make_provider()
        raw = self._make_sdk_response(text="OK")
        provider._client.messages.create.return_value = raw
        resp = provider.chat(SAMPLE_MESSAGES, "system", SAMPLE_TOOLS)
        self.assertEqual(resp.text, "OK")
        provider._client.messages.create.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# OpenAIProvider 响应解析测试
# ══════════════════════════════════════════════════════════════════════════════

class TestOpenAIProvider(unittest.TestCase):

    def _make_provider(self):
        from mini_agent.llm.providers.openai import OpenAIProvider
        cfg = LLMConfig(provider="openai", model="gpt-4o", api_key="test-key")
        with patch.object(OpenAIProvider, "_build_client", return_value=MagicMock()):
            return OpenAIProvider(cfg)

    def _make_sdk_response(self, text="Hello", tool_calls=None):
        resp = MagicMock()
        choice = MagicMock()
        choice.message.content = text
        choice.finish_reason = "stop" if not tool_calls else "tool_calls"
        # Build mock tool_calls on the message
        mock_tcs = []
        for tc in (tool_calls or []):
            mock_tc = MagicMock()
            mock_tc.id = tc["id"]
            mock_tc.function.name = tc["name"]
            mock_tc.function.arguments = json.dumps(tc["input"])
            mock_tcs.append(mock_tc)
        choice.message.tool_calls = mock_tcs if tool_calls else None
        resp.choices = [choice]
        resp.usage = MagicMock(prompt_tokens=15, completion_tokens=25, total_tokens=40)
        return resp

    def test_parse_text_response(self):
        provider = self._make_provider()
        raw = self._make_sdk_response(text="Hi there")
        resp = provider._parse_response(raw)
        self.assertEqual(resp.text, "Hi there")
        self.assertEqual(resp.stop_reason, "end_turn")

    def test_parse_tool_calls(self):
        provider = self._make_provider()
        raw = self._make_sdk_response(
            text="",
            tool_calls=[{"id": "call_1", "name": "bash", "input": {"command": "pwd"}}]
        )
        resp = provider._parse_response(raw)
        self.assertTrue(resp.has_tool_calls)
        self.assertEqual(resp.tool_calls[0].name, "bash")
        self.assertEqual(resp.stop_reason, "tool_use")

    def test_format_tools_uses_parameters(self):
        provider = self._make_provider()
        result = provider.format_tools(SAMPLE_TOOLS)
        self.assertEqual(result[0]["type"], "function")
        self.assertIn("parameters", result[0]["function"])
        self.assertNotIn("input_schema", result[0]["function"])

    def test_prepend_system_message(self):
        msgs = [{"role": "user", "content": "Hi"}]
        result = self._make_provider()._prepend_system(msgs, "Be helpful")
        self.assertEqual(result[0]["role"], "system")
        self.assertEqual(result[0]["content"], "Be helpful")
        self.assertEqual(len(result), 2)

    def test_prepend_system_replaces_existing(self):
        msgs = [{"role": "system", "content": "old"}, {"role": "user", "content": "Hi"}]
        result = self._make_provider()._prepend_system(msgs, "new system")
        self.assertEqual(result[0]["content"], "new system")
        self.assertEqual(len(result), 2)

    def test_usage_mapped_correctly(self):
        provider = self._make_provider()
        raw = self._make_sdk_response()
        resp = provider._parse_response(raw)
        self.assertEqual(resp.usage.input_tokens, 15)
        self.assertEqual(resp.usage.output_tokens, 25)
        self.assertEqual(resp.usage.total_tokens, 40)


# ══════════════════════════════════════════════════════════════════════════════
# OpenRouterProvider 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestOpenRouterProvider(unittest.TestCase):

    def _make_provider(self, extra=None):
        from mini_agent.llm.providers.openrouter import OpenRouterProvider
        cfg = LLMConfig(
            provider="openrouter",
            model="anthropic/claude-opus-4-7",
            api_key="sk-or-test",
            extra=extra or {},
        )
        with patch.object(OpenRouterProvider, "_build_client", return_value=MagicMock()):
            return OpenRouterProvider(cfg)

    def test_default_base_url_injected(self):
        provider = self._make_provider()
        self.assertEqual(provider.config.base_url, "https://openrouter.ai/api/v1")

    def test_provider_name(self):
        provider = self._make_provider()
        self.assertEqual(provider.provider_name, "OpenRouter")

    def test_default_headers_injected(self):
        provider = self._make_provider()
        headers = provider.config.extra["default_headers"]
        self.assertIn("HTTP-Referer", headers)
        self.assertIn("X-Title", headers)
        self.assertEqual(headers["X-Title"], "mini_agent")

    def test_custom_headers_via_extra(self):
        provider = self._make_provider(extra={
            "http_referer": "https://mysite.com",
            "x_title": "MyApp",
        })
        headers = provider.config.extra["default_headers"]
        self.assertEqual(headers["HTTP-Referer"], "https://mysite.com")
        self.assertEqual(headers["X-Title"], "MyApp")

    def test_custom_base_url_not_overridden(self):
        from mini_agent.llm.providers.openrouter import OpenRouterProvider
        cfg = LLMConfig(
            provider="openrouter",
            model="openai/gpt-4o",
            api_key="sk-or-test",
            base_url="https://custom-proxy.example.com/v1",
        )
        with patch.object(OpenRouterProvider, "_build_client", return_value=MagicMock()):
            p = OpenRouterProvider(cfg)
        self.assertEqual(p.config.base_url, "https://custom-proxy.example.com/v1")

    def test_api_key_from_env(self):
        from mini_agent.llm.providers.openrouter import OpenRouterProvider
        cfg = LLMConfig(provider="openrouter", model="openai/gpt-4o", api_key="")
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-from-env"}):
            with patch.object(OpenRouterProvider, "_build_client", return_value=MagicMock()):
                p = OpenRouterProvider(cfg)
        self.assertEqual(p.config.api_key, "sk-or-from-env")

    def test_registered_in_factory(self):
        from mini_agent.llm.factory import _REGISTRY
        self.assertIn("openrouter", _REGISTRY)
        self.assertIn("or", _REGISTRY)

    def test_factory_alias_or(self):
        from mini_agent.llm.factory import _REGISTRY
        # "or" 和 "openrouter" 指向同一个 loader
        self.assertIs(_REGISTRY["or"], _REGISTRY["openrouter"])

    def test_list_providers_includes_openrouter(self):
        from mini_agent.llm.factory import list_providers
        self.assertIn("openrouter", list_providers())

    def test_openai_provider_default_headers_passthrough(self):
        """OpenAIProvider._build_client 能正确透传 default_headers。"""
        from mini_agent.llm.providers.openai import OpenAIProvider
        cfg = LLMConfig(
            provider="openai",
            model="gpt-4o",
            api_key="k",
            extra={"default_headers": {"X-Custom": "value"}},
        )
        mock_openai_cls = MagicMock(return_value=MagicMock())
        with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_cls)}):
            OpenAIProvider(cfg)
        mock_openai_cls.assert_called_once()
        call_kwargs = mock_openai_cls.call_args[1]
        self.assertIn("default_headers", call_kwargs)
        self.assertEqual(call_kwargs["default_headers"]["X-Custom"], "value")

    def test_default_headers_not_leaked_into_request_kwargs(self):
        """default_headers 是客户端级配置，不应混入 chat.completions.create()/stream() 的 kwargs。"""
        provider = self._make_provider()
        kwargs = provider._build_kwargs(
            messages=[{"role": "user", "content": "hi"}], tools=None, stream=False
        )
        self.assertNotIn("default_headers", kwargs)

    def test_default_headers_not_leaked_stream_kwargs(self):
        provider = self._make_provider()
        kwargs = provider._build_kwargs(
            messages=[{"role": "user", "content": "hi"}], tools=None, stream=True
        )
        self.assertNotIn("default_headers", kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# AgnesProvider 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestAgnesProvider(unittest.TestCase):

    def _make_provider(self, **cfg_kwargs):
        from mini_agent.llm.providers.agnes import AgnesProvider
        defaults = dict(provider="agnes", model="agnes-2.0-flash", api_key="agnes-test-key")
        defaults.update(cfg_kwargs)
        cfg = LLMConfig(**defaults)
        with patch.object(AgnesProvider, "_build_client", return_value=MagicMock()):
            return AgnesProvider(cfg)

    def test_default_base_url_injected(self):
        provider = self._make_provider()
        self.assertEqual(provider.config.base_url, "https://apihub.agnes-ai.com/v1")

    def test_provider_name(self):
        provider = self._make_provider()
        self.assertEqual(provider.provider_name, "Agnes")

    def test_custom_base_url_not_overridden(self):
        provider = self._make_provider(base_url="https://custom-proxy.example.com/v1")
        self.assertEqual(provider.config.base_url, "https://custom-proxy.example.com/v1")

    def test_api_key_from_env(self):
        from mini_agent.llm.providers.agnes import AgnesProvider
        cfg = LLMConfig(provider="agnes", model="agnes-2.0-flash", api_key="")
        with patch.dict("os.environ", {"AGNES_API_KEY": "agnes-from-env"}):
            with patch.object(AgnesProvider, "_build_client", return_value=MagicMock()):
                p = AgnesProvider(cfg)
        self.assertEqual(p.config.api_key, "agnes-from-env")

    def test_explicit_api_key_wins_over_env(self):
        from mini_agent.llm.providers.agnes import AgnesProvider
        cfg = LLMConfig(provider="agnes", model="agnes-2.0-flash", api_key="explicit-key")
        with patch.dict("os.environ", {"AGNES_API_KEY": "agnes-from-env"}):
            with patch.object(AgnesProvider, "_build_client", return_value=MagicMock()):
                p = AgnesProvider(cfg)
        self.assertEqual(p.config.api_key, "explicit-key")

    def test_base_url_from_env(self):
        from mini_agent.llm.providers.agnes import AgnesProvider
        cfg = LLMConfig(provider="agnes", model="agnes-2.0-flash", api_key="k")
        with patch.dict("os.environ", {"AGNES_BASE_URL": "https://env-proxy.example.com/v1"}):
            with patch.object(AgnesProvider, "_build_client", return_value=MagicMock()):
                p = AgnesProvider(cfg)
        self.assertEqual(p.config.base_url, "https://env-proxy.example.com/v1")

    def test_registered_in_factory(self):
        from mini_agent.llm.factory import _REGISTRY
        self.assertIn("agnes", _REGISTRY)

    def test_list_providers_includes_agnes(self):
        from mini_agent.llm.factory import list_providers
        self.assertIn("agnes", list_providers())

    def test_create_client_via_factory(self):
        from mini_agent.llm.factory import create_client
        from mini_agent.llm.providers.agnes import AgnesProvider
        cfg = LLMConfig(provider="agnes", model="agnes-1.5-flash", api_key="k")
        with patch.object(AgnesProvider, "_build_client", return_value=MagicMock()):
            client = create_client(cfg)
        self.assertIsInstance(client, AgnesProvider)

    def test_format_tools_openai_compatible(self):
        """Agnes 完全兼容 OpenAI 的 tools/function 结构。"""
        provider = self._make_provider()
        tools = [ToolSchema(
            name="get_weather", description="Get the current weather for a location",
            input_schema={"type": "object", "properties": {"location": {"type": "string"}},
                          "required": ["location"]},
        )]
        formatted = provider.format_tools(tools)
        self.assertEqual(formatted, [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather for a location",
                "parameters": {"type": "object", "properties": {"location": {"type": "string"}},
                               "required": ["location"]},
            },
        }])

    def test_build_kwargs_uses_model(self):
        provider = self._make_provider()
        kwargs = provider._build_kwargs(
            messages=[{"role": "user", "content": "hi"}], tools=None, stream=False
        )
        self.assertEqual(kwargs["model"], "agnes-2.0-flash")

    def test_parse_response_text_and_usage(self):
        provider = self._make_provider()
        mock_choice = MagicMock()
        mock_choice.message.content = "Hello from Agnes!"
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_resp.usage.prompt_tokens = 10
        mock_resp.usage.completion_tokens = 5
        mock_resp.usage.total_tokens = 15

        resp = provider._parse_response(mock_resp)
        self.assertEqual(resp.text, "Hello from Agnes!")
        self.assertEqual(resp.stop_reason, "end_turn")
        self.assertEqual(resp.usage.input_tokens, 10)
        self.assertEqual(resp.usage.output_tokens, 5)


# ══════════════════════════════════════════════════════════════════════════════
# OllamaProvider 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestOllamaProvider(unittest.TestCase):

    def _make_provider(self):
        from mini_agent.llm.providers.ollama import OllamaProvider
        cfg = LLMConfig(provider="ollama", model="llama3.1", api_key="",
                        requires_api_key=False)
        return OllamaProvider(cfg)

    def _make_ollama_raw(self, text="Hello", tool_calls=None):
        msg = {"content": text, "tool_calls": []}
        if tool_calls:
            for tc in tool_calls:
                msg["tool_calls"].append({
                    "function": {"name": tc["name"], "arguments": tc["input"]}
                })
        return {
            "message": msg,
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 8,
            "eval_count": 16,
        }

    def test_parse_text_response(self):
        provider = self._make_provider()
        raw = self._make_ollama_raw(text="Hi from ollama")
        resp = provider._parse_response(raw)
        self.assertEqual(resp.text, "Hi from ollama")
        self.assertFalse(resp.has_tool_calls)

    def test_parse_tool_calls(self):
        provider = self._make_provider()
        raw = self._make_ollama_raw(
            text="",
            tool_calls=[{"name": "bash", "input": {"command": "echo hi"}}]
        )
        resp = provider._parse_response(raw)
        self.assertTrue(resp.has_tool_calls)
        self.assertEqual(resp.tool_calls[0].name, "bash")

    def test_usage_extracted_from_raw(self):
        provider = self._make_provider()
        raw = self._make_ollama_raw()
        resp = provider._parse_response(raw)
        self.assertEqual(resp.usage.input_tokens, 8)
        self.assertEqual(resp.usage.output_tokens, 16)
        self.assertEqual(resp.usage.total_tokens, 24)

    def test_no_api_key_required(self):
        provider = self._make_provider()
        # Should not raise even with empty api_key
        provider.validate_config()

    def test_format_tools_openai_compat(self):
        provider = self._make_provider()
        result = provider.format_tools(SAMPLE_TOOLS)
        self.assertEqual(result[0]["type"], "function")
        self.assertIn("parameters", result[0]["function"])

    def test_connection_error_wrapped(self):
        from mini_agent.llm.base import LLMProviderError
        provider = self._make_provider()
        with patch.object(provider, "_post", side_effect=LLMProviderError("Connection refused")):
            with self.assertRaises(LLMProviderError):
                provider.chat(SAMPLE_MESSAGES, "system", [])


# ══════════════════════════════════════════════════════════════════════════════
# Agent 与 LLMClient 集成测试
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentLLMIntegration(unittest.TestCase):
    """测试 Agent 通过抽象接口驱动 LLM，不关心具体实现。"""

    def _make_agent(self, responses: list[LLMResponse]):
        """创建一个注入了 mock client 的 Agent。"""
        import mini_agent.tools.builtin  # noqa: ensure tools registered
        from mini_agent.agent import Agent
        from mini_agent.config import load_config
        from mini_agent.permissions import PermissionGuard

        cfg = load_config()
        cfg.api_key = "test"
        cfg.stream = False

        mock_client = MagicMock(spec=LLMClient)
        mock_client.chat.side_effect = responses
        mock_client.stream.side_effect = responses

        guard = PermissionGuard(auto_approve=True)
        return Agent(cfg=cfg, guard=guard, llm_client=mock_client), mock_client

    def test_simple_text_turn(self):
        agent, mock_client = self._make_agent([
            make_response(text="Hello there!")
        ])
        result = agent.run_turn("Hi")
        self.assertEqual(result, "Hello there!")
        mock_client.chat.assert_called_once()

    def test_tool_call_loop(self):
        """Agent 应执行工具调用后继续对话，直到收到纯文本响应。"""
        tc = ToolCall(id="tc1", name="bash", input={"command": "echo hi"})
        responses = [
            LLMResponse(text="", tool_calls=[tc], usage=LLMUsage(), stop_reason="tool_use"),
            make_response(text="Done!"),
        ]
        agent, mock_client = self._make_agent(responses)
        result = agent.run_turn("run echo hi")
        self.assertEqual(result, "Done!")
        self.assertEqual(mock_client.chat.call_count, 2)

    def test_stats_accumulate(self):
        agent, _ = self._make_agent([
            make_response(input_tokens=10, output_tokens=20),
        ])
        agent.run_turn("test")
        self.assertEqual(agent.stats.input_tokens, 10)
        self.assertEqual(agent.stats.output_tokens, 20)
        self.assertEqual(agent.stats.turns, 1)

    def test_tool_call_denied(self):
        from mini_agent.permissions import PermissionGuard
        from mini_agent.config import load_config
        import mini_agent.tools.builtin  # noqa
        from mini_agent.agent import Agent

        cfg = load_config()
        cfg.stream = False

        tc = ToolCall(id="tc1", name="bash", input={"command": "rm -rf /"})
        responses = [
            LLMResponse(text="", tool_calls=[tc], usage=LLMUsage(), stop_reason="tool_use"),
            make_response(text="Okay, skipped."),
        ]
        mock_client = MagicMock(spec=LLMClient)
        mock_client.chat.side_effect = responses

        # Guard that denies everything
        guard = PermissionGuard(auto_approve=False, sandbox=True)
        agent = Agent(cfg=cfg, guard=guard, llm_client=mock_client)
        result = agent.run_turn("delete root")
        # Should still complete (with denied tool result)
        self.assertEqual(result, "Okay, skipped.")

    def test_switch_provider(self):
        agent, _ = self._make_agent([make_response()])
        new_client = ConcreteClient(make_config())
        with patch("mini_agent.agent.create_client", return_value=new_client):
            agent.switch_provider(make_config(provider="openai"))
        self.assertIs(agent.llm_client, new_client)

    def test_switch_model_existing_in_pool(self):
        """/model 切换到 fallback chain 中已有的模型时，应直接复用该条目的
        client，不重新创建，且 provider/model 同步更新。"""
        from mini_agent.llm.client_pool import LLMClientPool, ProviderEntry

        agent, _ = self._make_agent([make_response()])
        cfg_a = make_config(provider="anthropic", model="claude-opus-4-5")
        cfg_b = make_config(provider="openai", model="gpt-4o")
        client_a = ConcreteClient(cfg_a)
        client_b = ConcreteClient(cfg_b)
        agent._client_pool = LLMClientPool(entries=[
            ProviderEntry(config=cfg_a, client=client_a),
            ProviderEntry(config=cfg_b, client=client_b),
        ])
        agent._llm = client_a

        with patch("mini_agent.agent.create_client") as mock_create:
            entry = agent.switch_model("gpt-4o")
            mock_create.assert_not_called()  # 已存在，不应重建 client

        self.assertIs(agent.llm_client, client_b)
        self.assertEqual(entry.config.provider, "openai")
        self.assertEqual(agent.cfg.model, "gpt-4o")
        self.assertEqual(agent.cfg.llm_provider, "openai")

    def test_switch_model_not_in_pool_creates_new_entry(self):
        """/model 切换到一个 fallback chain 中不存在的模型名时，应在**当前
        provider** 下创建一条新条目并激活，而不是报错或静默无效。"""
        agent, _ = self._make_agent([make_response()])
        cfg_a = make_config(provider="anthropic", model="claude-opus-4-5")
        from mini_agent.llm.client_pool import LLMClientPool, ProviderEntry
        client_a = ConcreteClient(cfg_a)
        agent._client_pool = LLMClientPool(entries=[ProviderEntry(config=cfg_a, client=client_a)])
        agent._llm = client_a

        new_client = ConcreteClient(make_config(provider="anthropic", model="claude-haiku-4-5"))
        with patch("mini_agent.agent.create_client", return_value=new_client) as mock_create:
            entry = agent.switch_model("claude-haiku-4-5")
            mock_create.assert_called_once()
            called_cfg = mock_create.call_args[0][0]
            self.assertEqual(called_cfg.provider, "anthropic")  # 沿用当前 provider
            self.assertEqual(called_cfg.model, "claude-haiku-4-5")

        self.assertIs(agent.llm_client, new_client)
        self.assertEqual(entry.config.model, "claude-haiku-4-5")
        self.assertEqual(agent.cfg.model, "claude-haiku-4-5")
        # 旧条目仍保留在 fallback chain 中（未被丢弃）
        self.assertEqual(len(agent._client_pool._entries), 2)

    def test_switch_to_provider_default_existing_entry(self):
        """/provider switch <name> 在没给 model 时，应使用该 provider 在
        fallback chain 中出现的第一条（"默认模型"），并复用其 client。"""
        from mini_agent.llm.client_pool import LLMClientPool, ProviderEntry

        agent, _ = self._make_agent([make_response()])
        cfg_a = make_config(provider="anthropic", model="claude-opus-4-5")
        cfg_b1 = make_config(provider="openai", model="gpt-4o")
        cfg_b2 = make_config(provider="openai", model="gpt-4o-mini")
        client_a, client_b1, client_b2 = (
            ConcreteClient(cfg_a), ConcreteClient(cfg_b1), ConcreteClient(cfg_b2),
        )
        agent._client_pool = LLMClientPool(entries=[
            ProviderEntry(config=cfg_a, client=client_a),
            ProviderEntry(config=cfg_b1, client=client_b1),
            ProviderEntry(config=cfg_b2, client=client_b2),
        ])
        agent._llm = client_a

        with patch("mini_agent.agent.create_client") as mock_create:
            entry = agent.switch_to_provider_default("openai")
            mock_create.assert_not_called()

        self.assertIs(agent.llm_client, client_b1)  # 第一条 = 默认模型
        self.assertEqual(entry.config.model, "gpt-4o")
        self.assertEqual(agent.cfg.llm_provider, "openai")

    def test_switch_to_provider_default_unknown_provider_builds_new(self):
        """fallback chain 中完全没有该 provider 时，应解析环境变量 key 并
        创建一个新条目，而不是报错。"""
        agent, _ = self._make_agent([make_response()])
        new_client = ConcreteClient(make_config(provider="openai", model="gpt-4o"))
        with patch("mini_agent.agent.create_client", return_value=new_client) as mock_create, \
             patch("mini_agent.llm.client_pool._get_env_api_key", return_value="env-key"):
            entry = agent.switch_to_provider_default("openai", "gpt-4o")
            mock_create.assert_called_once()
            called_cfg = mock_create.call_args[0][0]
            self.assertEqual(called_cfg.provider, "openai")
            self.assertEqual(called_cfg.model, "gpt-4o")
            self.assertEqual(called_cfg.api_key, "env-key")

        self.assertIs(agent.llm_client, new_client)
        self.assertEqual(entry.config.provider, "openai")

    def test_history_uses_provider_agnostic_format(self):
        """对话历史不应包含任何 provider SDK 特有的对象。"""
        agent, _ = self._make_agent([make_response(text="OK")])
        agent.run_turn("test")
        for msg in agent.history:
            self.assertIsInstance(msg, dict)
            self.assertIn("role", msg)

    def test_clear_history(self):
        agent, _ = self._make_agent([make_response()])
        agent.run_turn("test")
        self.assertGreater(len(agent.history), 0)
        agent.clear_history()
        self.assertEqual(agent.history, [])


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
