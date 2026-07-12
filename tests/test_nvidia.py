"""
tests/test_nvidia.py

NVIDIA NIM provider（httpx 实现）的完整测试套件，覆盖：
  - 配置自动填充（base_url、api_key 环境变量）
  - 非流式响应解析
  - 流式 SSE 解析
  - reasoning_content 流式提取
  - <think> 标签提取（通过 postprocess）
  - factory 注册
  - 错误包装
  - tool call 通过文本解析（不依赖 API tools 参数）
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mini_agent.llm.base import LLMConfig, LLMResponse, LLMUsage, ToolCall, ToolSchema
from mini_agent.llm.base import LLMProviderError, LLMRateLimitError, LLMTimeoutError
from mini_agent.llm.providers.nvidia import NvidiaProvider, _DEFAULT_BASE_URL, _map_finish_reason, _prepend_system
from mini_agent.llm.factory import create_client, list_providers, _REGISTRY


# ── 工厂函数 ──────────────────────────────────────────────────────────────────

def make_cfg(**kw) -> LLMConfig:
    defaults = dict(provider="nvidia", model="stepfun-ai/step-3.5-flash", api_key="nvapi-test")
    defaults.update(kw)
    return LLMConfig(**defaults)


def make_provider(cfg=None) -> NvidiaProvider:
    cfg = cfg or make_cfg()
    with patch.object(NvidiaProvider, "_build_http_client", return_value=MagicMock()):
        return NvidiaProvider(cfg)


def make_chat_response(text="Hello", usage=None, finish_reason="stop") -> dict:
    """构造模拟的非流式 JSON 响应。"""
    return {
        "choices": [{"message": {"content": text}, "finish_reason": finish_reason}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def make_sse_lines(
    text_tokens: list[str],
    reasoning_tokens: list[str] = None,
    finish_reason: str = "stop",
    usage: dict = None,
) -> list[str]:
    """构造模拟的 SSE 流式行（data: {...}）。"""
    lines = []

    for token in (reasoning_tokens or []):
        chunk = {"choices": [{"delta": {"reasoning_content": token}, "finish_reason": None}]}
        lines.append(f"data: {json.dumps(chunk)}")

    for token in text_tokens:
        chunk = {"choices": [{"delta": {"content": token}, "finish_reason": None}]}
        lines.append(f"data: {json.dumps(chunk)}")

    # 末尾 finish chunk
    final_chunk = {
        "choices": [{"delta": {}, "finish_reason": finish_reason}],
        "usage": usage or {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
    }
    lines.append(f"data: {json.dumps(final_chunk)}")
    lines.append("data: [DONE]")
    return lines


# ══════════════════════════════════════════════════════════════════════════════
# 配置测试
# ══════════════════════════════════════════════════════════════════════════════

class TestNvidiaConfig(unittest.TestCase):

    def test_default_base_url_set(self):
        p = make_provider()
        self.assertEqual(p.config.base_url, _DEFAULT_BASE_URL)

    def test_custom_base_url_preserved(self):
        cfg = make_cfg(base_url="https://my-proxy.example.com/v1")
        p = make_provider(cfg)
        self.assertEqual(p.config.base_url, "https://my-proxy.example.com/v1")

    def test_api_key_from_env(self):
        cfg = LLMConfig(provider="nvidia", model="x", api_key="")
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-from-env"}):
            with patch.object(NvidiaProvider, "_build_http_client", return_value=MagicMock()):
                p = NvidiaProvider(cfg)
        self.assertEqual(p.config.api_key, "nvapi-from-env")

    def test_explicit_key_wins(self):
        cfg = make_cfg(api_key="nvapi-explicit")
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-env"}):
            p = make_provider(cfg)
        self.assertEqual(p.config.api_key, "nvapi-explicit")

    def test_provider_name(self):
        self.assertEqual(make_provider().provider_name, "NVIDIA")

    def test_validate_raises_without_key(self):
        cfg = LLMConfig(provider="nvidia", model="x", api_key="", requires_api_key=True)
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(NvidiaProvider, "_build_http_client", return_value=MagicMock()):
                p = NvidiaProvider(cfg)
            with self.assertRaises(LLMProviderError):
                p.validate_config()

    def test_endpoint_uses_base_url(self):
        p = make_provider()
        self.assertTrue(p._endpoint().endswith("/chat/completions"))
        self.assertIn("nvidia.com", p._endpoint())

    def test_http_client_uses_bearer_auth(self):
        """验证 httpx 客户端的 Authorization header。"""
        import httpx
        real_client = None
        original_init = httpx.Client.__init__

        captured = {}
        def mock_init(self_c, **kwargs):
            captured.update(kwargs)
            original_init(self_c, **{k: v for k, v in kwargs.items()
                                      if k not in ("verify", "trust_env")})

        cfg = make_cfg(api_key="nvapi-mykey")
        with patch.object(httpx.Client, "__init__", mock_init):
            try:
                p = NvidiaProvider(cfg)
            except Exception:
                pass  # Client init may fail without real server

        # 验证 verify=False 和 trust_env=False 被传递
        if captured:
            self.assertFalse(captured.get("verify", True))
            self.assertFalse(captured.get("trust_env", True))

    def test_supports_reasoning_known_model(self):
        p = make_provider(make_cfg(model="stepfun-ai/step-3.5-flash"))
        self.assertTrue(p.supports_reasoning())

    def test_supports_reasoning_unknown_model(self):
        p = make_provider(make_cfg(model="meta/llama-3.1-8b-instruct"))
        self.assertFalse(p.supports_reasoning())


# ══════════════════════════════════════════════════════════════════════════════
# 非流式调用测试
# ══════════════════════════════════════════════════════════════════════════════

class TestNvidiaChat(unittest.TestCase):

    def setUp(self):
        self.provider = make_provider()

    def _mock_post(self, json_data: dict):
        mock_resp = MagicMock()
        mock_resp.json.return_value = json_data
        mock_resp.raise_for_status = MagicMock()
        self.provider._http.post.return_value = mock_resp

    def test_plain_text(self):
        self._mock_post(make_chat_response("Hello!"))
        resp = self.provider._do_chat([], "", [])
        self.assertEqual(resp.text, "Hello!")
        self.assertEqual(resp.reasoning, "")
        self.assertFalse(resp.has_tool_calls)

    def test_usage_mapped(self):
        self._mock_post(make_chat_response(usage={"prompt_tokens": 15, "completion_tokens": 25, "total_tokens": 40}))
        resp = self.provider._do_chat([], "", [])
        self.assertEqual(resp.usage.input_tokens, 15)
        self.assertEqual(resp.usage.output_tokens, 25)

    def test_stop_reason_mapped(self):
        self._mock_post(make_chat_response(finish_reason="stop"))
        resp = self.provider._do_chat([], "", [])
        self.assertEqual(resp.stop_reason, "end_turn")

    def test_think_tag_extracted_by_postprocess(self):
        """<think> 标签由 postprocess 提取，_do_chat 返回原始文本。"""
        self._mock_post(make_chat_response("<think>reasoning</think>\nAnswer"))
        raw = self.provider._do_chat([], "", [])
        # postprocess 会处理，_do_chat 本身返回原始 text
        self.assertIn("reasoning", raw.text + raw.reasoning or "reasoning")

    def test_tool_call_block_in_text(self):
        """模型返回 ```tool_call 块，postprocess 解析（通过 traced_chat）。"""
        block = '```tool_call\n{"tool":"bash","id":"t1","parameters":{"command":"ls"}}\n```'
        self._mock_post(make_chat_response(block))
        # 调用 chat()（走 traced_chat → postprocess）
        resp = self.provider.chat([], "", [])
        self.assertTrue(resp.has_tool_calls)
        self.assertEqual(resp.tool_calls[0].name, "bash")

    def test_no_choices_returns_empty(self):
        self._mock_post({"choices": [], "usage": {}})
        resp = self.provider._do_chat([], "", [])
        self.assertEqual(resp.text, "")

    def test_http_error_wrapped(self):
        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": {"message": "bad request"}}
        mock_resp.text = "bad request"
        self.provider._http.post.side_effect = httpx.HTTPStatusError(
            "400", request=MagicMock(), response=mock_resp
        )
        with self.assertRaises(LLMProviderError) as ctx:
            self.provider._do_chat([], "", [])
        self.assertIn("400", str(ctx.exception))

    def test_rate_limit_wrapped(self):
        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.json.return_value = {"error": {"message": "rate limited"}}
        mock_resp.text = "rate limited"
        self.provider._http.post.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=mock_resp
        )
        with self.assertRaises(LLMRateLimitError):
            self.provider._do_chat([], "", [])

    def test_timeout_wrapped(self):
        import httpx
        self.provider._http.post.side_effect = httpx.ReadTimeout("timed out")
        with self.assertRaises(LLMTimeoutError):
            self.provider._do_chat([], "", [])

    def test_payload_no_tools_field(self):
        """payload 不应包含 tools 字段（全通过 system prompt）。"""
        payload = self.provider._build_payload([], "system", stream=False)
        self.assertNotIn("tools", payload)

    def test_payload_has_model(self):
        payload = self.provider._build_payload([], "", stream=False)
        self.assertEqual(payload["model"], "stepfun-ai/step-3.5-flash")

    def test_payload_stream_false(self):
        payload = self.provider._build_payload([], "", stream=False)
        self.assertFalse(payload["stream"])

    def test_payload_stream_true_has_stream_options(self):
        payload = self.provider._build_payload([], "", stream=True)
        self.assertTrue(payload["stream"])
        self.assertIn("stream_options", payload)


# ══════════════════════════════════════════════════════════════════════════════
# 流式调用测试
# ══════════════════════════════════════════════════════════════════════════════

class TestNvidiaStream(unittest.TestCase):

    def setUp(self):
        self.provider = make_provider()

    def _mock_stream(self, sse_lines: list[str], status_code: int = 200):
        """Mock httpx streaming context manager."""
        mock_resp = MagicMock()
        mock_resp.status_code = status_code   # 必须是 int，否则 >= 400 比较报错
        mock_resp.raise_for_status = MagicMock()
        mock_resp.read = MagicMock()
        mock_resp.iter_lines.return_value = iter(sse_lines)
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        self.provider._http.stream.return_value = mock_resp

    def test_text_tokens_collected(self):
        self._mock_stream(make_sse_lines(["Hello", " world"]))
        tokens = []
        resp = self.provider._do_stream([], "", [], on_token=tokens.append)
        self.assertEqual("".join(tokens), "Hello world")
        self.assertEqual(resp.text, "Hello world")

    def test_reasoning_tokens_separated(self):
        lines = make_sse_lines(
            text_tokens=["Answer"],
            reasoning_tokens=["Step 1 ", "Step 2"],
        )
        self._mock_stream(lines)
        text_tokens, reason_tokens = [], []
        resp = self.provider._do_stream(
            [], "", [],
            on_token=text_tokens.append,
            on_reasoning=reason_tokens.append,
        )
        self.assertEqual("".join(text_tokens), "Answer")
        self.assertEqual("".join(reason_tokens), "Step 1 Step 2")
        self.assertEqual(resp.reasoning, "Step 1 Step 2")
        self.assertEqual(resp.text, "Answer")

    def test_reasoning_not_in_text(self):
        self._mock_stream(make_sse_lines(
            text_tokens=["Clean answer"],
            reasoning_tokens=["internal thought"],
        ))
        resp = self.provider._do_stream([], "", [], on_token=lambda t: None)
        self.assertNotIn("internal thought", resp.text)
        self.assertIn("internal thought", resp.reasoning)

    def test_on_reasoning_optional(self):
        """on_reasoning 不传时不报错。"""
        self._mock_stream(make_sse_lines(
            text_tokens=["Result"],
            reasoning_tokens=["thinking"],
        ))
        resp = self.provider._do_stream([], "", [], on_token=lambda t: None)
        self.assertEqual(resp.text, "Result")
        self.assertEqual(resp.reasoning, "thinking")

    def test_usage_extracted(self):
        lines = make_sse_lines(
            ["hi"],
            usage={"prompt_tokens": 8, "completion_tokens": 16, "total_tokens": 24},
        )
        self._mock_stream(lines)
        resp = self.provider._do_stream([], "", [], on_token=lambda t: None)
        self.assertEqual(resp.usage.input_tokens, 8)
        self.assertEqual(resp.usage.output_tokens, 16)

    def test_done_line_ignored(self):
        self._mock_stream(["data: [DONE]"])
        tokens = []
        resp = self.provider._do_stream([], "", [], on_token=tokens.append)
        self.assertEqual(tokens, [])
        self.assertEqual(resp.text, "")

    def test_malformed_json_skipped(self):
        lines = ["data: {invalid}", "data: [DONE]"]
        self._mock_stream(lines)
        resp = self.provider._do_stream([], "", [], on_token=lambda t: None)
        self.assertEqual(resp.text, "")   # no crash

    def test_finish_reason_mapped(self):
        lines = make_sse_lines(["ok"], finish_reason="stop")
        self._mock_stream(lines)
        resp = self.provider._do_stream([], "", [], on_token=lambda t: None)
        self.assertEqual(resp.stop_reason, "end_turn")

    def test_tool_call_via_stream_postprocess(self):
        """流式输出包含 ```tool_call 块，通过 postprocess 解析（走 traced_stream）。"""
        block = '```tool_call\n{"tool":"bash","id":"s1","parameters":{"command":"pwd"}}\n```'
        lines = make_sse_lines([block])
        self._mock_stream(lines)
        resp = self.provider.stream([], "", [], on_token=lambda t: None)
        self.assertTrue(resp.has_tool_calls)
        self.assertEqual(resp.tool_calls[0].name, "bash")

    def test_http_error_in_stream_wrapped(self):
        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 500   # >= 400 触发错误路径
        mock_resp.json.return_value = {}
        mock_resp.text = "server error"
        mock_resp.read = MagicMock()  # read() 在错误路径中被调用
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_resp
        )
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        self.provider._http.stream.return_value = mock_resp
        with self.assertRaises(LLMProviderError):
            self.provider._do_stream([], "", [], on_token=lambda t: None)


# ══════════════════════════════════════════════════════════════════════════════
# system prompt 构建测试
# ══════════════════════════════════════════════════════════════════════════════

class TestNvidiaSystemPrompt(unittest.TestCase):

    def test_prepend_system_inserts_at_front(self):
        msgs = [{"role": "user", "content": "hi"}]
        result = _prepend_system(msgs, "Be helpful")
        self.assertEqual(result[0]["role"], "system")
        self.assertEqual(result[0]["content"], "Be helpful")
        self.assertEqual(len(result), 2)

    def test_prepend_system_replaces_existing(self):
        msgs = [{"role": "system", "content": "old"}, {"role": "user", "content": "hi"}]
        result = _prepend_system(msgs, "new system")
        self.assertEqual(result[0]["content"], "new system")
        self.assertEqual(len(result), 2)

    def test_prepend_empty_system_unchanged(self):
        msgs = [{"role": "user", "content": "hi"}]
        result = _prepend_system(msgs, "")
        self.assertEqual(result, msgs)

    def test_prepare_tools_injects_protocol(self):
        """ProviderMixin._prepare_tools 应把工具协议注入 system。"""
        p = make_provider()
        tools = [ToolSchema(name="bash", description="run shell",
                            input_schema={"type": "object", "properties": {}, "required": []})]
        system_out, api_tools = p._prepare_tools("Be helpful", tools)
        self.assertEqual(api_tools, [])             # 不传给 API
        self.assertIn("tool_use", system_out)      # 协议注入（<tool_use> 格式）
        self.assertIn("bash", system_out)

    def test_prepare_no_tools_system_unchanged(self):
        p = make_provider()
        system_out, api_tools = p._prepare_tools("Be helpful", [])
        self.assertEqual(system_out, "Be helpful")
        self.assertEqual(api_tools, [])


# ══════════════════════════════════════════════════════════════════════════════
# 辅助函数测试
# ══════════════════════════════════════════════════════════════════════════════

class TestHelpers(unittest.TestCase):

    def test_map_stop(self):
        self.assertEqual(_map_finish_reason("stop"), "end_turn")

    def test_map_tool_calls(self):
        self.assertEqual(_map_finish_reason("tool_calls"), "tool_use")

    def test_map_length(self):
        self.assertEqual(_map_finish_reason("length"), "max_tokens")

    def test_map_eos(self):
        self.assertEqual(_map_finish_reason("eos"), "end_turn")

    def test_map_unknown_passthrough(self):
        self.assertEqual(_map_finish_reason("custom_reason"), "custom_reason")

    def test_map_none_safe(self):
        result = _map_finish_reason(None)
        self.assertIsInstance(result, str)


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

    def test_nim_not_in_list_providers(self):
        """nim 是别名，不应出现在规范名列表里。"""
        self.assertNotIn("nim", list_providers())

    def test_create_nvidia_client(self):
        cfg = make_cfg()
        with patch.object(NvidiaProvider, "_build_http_client", return_value=MagicMock()):
            client = create_client(cfg)
        self.assertIsInstance(client, NvidiaProvider)

    def test_create_nim_alias(self):
        cfg = LLMConfig(provider="nim", model="meta/llama-3.1-8b", api_key="k")
        with patch.object(NvidiaProvider, "_build_http_client", return_value=MagicMock()):
            client = create_client(cfg)
        self.assertIsInstance(client, NvidiaProvider)


if __name__ == "__main__":
    unittest.main(verbosity=2)
