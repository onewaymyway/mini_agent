"""
llm/providers/nvidia.py — NVIDIA NIM API provider（httpx 原生实现）

使用 httpx 直接调用 NVIDIA NIM 的 OpenAI 兼容 REST API，
不依赖 openai SDK，避免 SDK 版本兼容性问题。

特性：
  - verify=False, trust_env=False
  - 流式（SSE）和非流式
  - reasoning_content 字段提取（思维链流式）
  - tool call 全部通过 system prompt 传递，从响应文本解析
  - <think>/<thinking>/<reasoning> 标签自动提取（由 postprocess 处理）
"""

from __future__ import annotations

import json
import os
from typing import Optional

from ..base import (
    LLMClient, LLMConfig, LLMResponse, LLMUsage,
    ToolSchema, StreamCallback, ReasoningCallback,
    LLMProviderError, LLMTimeoutError, LLMRateLimitError,
    LLMPermanentError,
)
from ._base_mixin import ProviderMixin

_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

_STREAMING_REASONING_MODELS = {
    "nvidia/llama-3.3-nemotron-super-49b-v1",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "stepfun-ai/step-3.5-flash",
    "deepseek-ai/deepseek-r1",
}


class NvidiaProvider(ProviderMixin, LLMClient):
    """
    NVIDIA NIM provider，使用 httpx 直接调用 REST API。

    tool call 不传给 API（全部通过 system prompt），
    响应文本由 ProviderMixin._postprocess 解析 ```tool_call 块。
    """

    def __init__(self, config: LLMConfig) -> None:
        if not config.base_url:
            config.base_url = os.environ.get("NVIDIA_BASE_URL", _DEFAULT_BASE_URL)
        if not config.api_key:
            config.api_key = os.environ.get("NVIDIA_API_KEY", "")
        super().__init__(config)
        self._http = self._build_http_client()

    @property
    def provider_name(self) -> str:
        return "NVIDIA"

    def supports_reasoning(self) -> bool:
        return self.config.model in _STREAMING_REASONING_MODELS

    # ── LLMClient 公共接口 ────────────────────────────────────────────────────

    def chat(self, messages, system, tools):
        return self._traced_chat(self._do_chat, messages, system, tools)

    def stream(self, messages, system, tools, on_token,
               on_reasoning: Optional[ReasoningCallback] = None):
        return self._traced_stream(
            self._do_stream, messages, system, tools,
            on_token, on_reasoning=on_reasoning,
        )

    # ── 实际 HTTP 调用 ────────────────────────────────────────────────────────

    def _do_chat(self, messages, system, tools) -> LLMResponse:
        """非流式调用：POST /chat/completions, stream=false"""
        payload = self._build_payload(messages, system, stream=False)
        try:
            resp = self._http.post(
                self._endpoint(),
                json=payload,
                timeout=self.config.timeout,
            )
            resp.raise_for_status()
            return self._parse_json_response(resp.json())
        except LLMProviderError:
            raise
        except Exception as e:
            raise self._wrap_error(e)

    def _do_stream(self, messages, system, tools, on_token,
                   on_reasoning: Optional[ReasoningCallback] = None) -> LLMResponse:
        """
        流式调用：POST /chat/completions, stream=true
        按 SSE 协议逐行解析 data: {...} 事件。
        """
        payload = self._build_payload(messages, system, stream=True)
        collected_text: list[str] = []
        collected_reasoning: list[str] = []
        usage = LLMUsage()
        finish_reason = "stop"

        try:
            with self._http.stream(
                "POST",
                self._endpoint(),
                json=payload,
                timeout=self.config.timeout,
            ) as resp:
                # httpx 流式模式下，raise_for_status() 在读取响应体前调用会报错
                # 改为手动检查状态码并构造错误
                if resp.status_code >= 400:
                    # 读取错误响应体
                    resp.read()
                    resp.raise_for_status()
                for line in resp.iter_lines():
                    line = line.strip()
                    # print("line:",line)
                    if not line or line == "data: [DONE]":
                        continue
                    if not line.startswith("data:"):
                        continue

                    raw_json = line[len("data:"):].strip()
                    if not raw_json:
                        continue
                    try:
                        chunk = json.loads(raw_json)
                    except json.JSONDecodeError:
                        continue

                    # usage 有时在最后一个 chunk
                    if chunk.get("usage"):
                        u = chunk["usage"]
                        usage = LLMUsage(
                            input_tokens=u.get("prompt_tokens", 0),
                            output_tokens=u.get("completion_tokens", 0),
                            total_tokens=u.get("total_tokens", 0),
                        )

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

                    choice = choices[0]
                    delta = choice.get("delta", {})

                    # reasoning_content（NVIDIA 思维链流式专属字段）
                    reasoning_token = delta.get("reasoning_content") or ""
                    if reasoning_token:
                        collected_reasoning.append(reasoning_token)
                        if on_reasoning:
                            on_reasoning(reasoning_token)

                    # 普通文本
                    content = delta.get("content") or ""
                    if content:
                        on_token(content)
                        collected_text.append(content)

                    fr = choice.get("finish_reason")
                    if fr:
                        finish_reason = fr

        except LLMProviderError:
            raise
        except Exception as e:
            raise self._wrap_error(e)

        return LLMResponse(
            text="".join(collected_text),
            reasoning="".join(collected_reasoning),
            tool_calls=[],       # postprocess 从文本解析
            usage=usage,
            stop_reason=_map_finish_reason(finish_reason),
        )

    # ── 请求构建 ──────────────────────────────────────────────────────────────

    def _build_payload(self, messages, system, stream: bool) -> dict:
        """构建请求体，不传 tools（全部通过 system prompt）。"""
        full_messages = _prepend_system(messages, system)
        payload: dict = {
            "model": self.config.model,
            "messages": full_messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.extra.get("temperature", self.config.temperature),
            "top_p": self.config.extra.get("top_p", 0.9),
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        # 透传 extra 中的其他参数（如 seed）
        for k, v in self.config.extra.items():
            if k not in ("temperature", "top_p", "tool_choice"):
                payload.setdefault(k, v)
        return payload

    def _endpoint(self) -> str:
        base = (self.config.base_url or _DEFAULT_BASE_URL).rstrip("/")
        return f"{base}/chat/completions"

    # ── 响应解析 ──────────────────────────────────────────────────────────────

    def _parse_json_response(self, data: dict) -> LLMResponse:
        choices = data.get("choices", [])
        choice = choices[0] if choices else {}
        message = choice.get("message", {})
        raw_text = message.get("content") or ""

        u = data.get("usage", {})
        usage = LLMUsage(
            input_tokens=u.get("prompt_tokens", 0),
            output_tokens=u.get("completion_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
        )
        return LLMResponse(
            text=raw_text,
            reasoning="",        # postprocess 从 <think> 标签提取
            tool_calls=[],       # postprocess 从 ```tool_call 块解析
            usage=usage,
            stop_reason=_map_finish_reason(
                choice.get("finish_reason") or "stop"
            ),
            raw=data,
        )

    # ── HTTP 客户端 ───────────────────────────────────────────────────────────

    def _build_http_client(self):
        try:
            import httpx
        except ImportError:
            raise LLMProviderError("httpx not installed. Run: pip install httpx")

        return httpx.Client(
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            verify=False,        # 不验证 SSL 证书
            trust_env=False,     # 不读取环境变量代理
            follow_redirects=True,
        )

    # ── 错误包装 ──────────────────────────────────────────────────────────────

    def _wrap_error(self, exc: Exception) -> LLMProviderError:
        try:
            import httpx
            if isinstance(exc, httpx.TimeoutException):
                return LLMTimeoutError(f"NVIDIA NIM timeout: {exc}")
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                try:
                    body = exc.response.json()
                    msg = body.get("error", {}).get("message", str(exc))
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.llm.providers.nvidia.NvidiaProvider._wrap_error')
                    msg = exc.response.text[:300]
                if status == 429:
                    return LLMRateLimitError(f"NVIDIA NIM rate limit (429): {msg}")
                if status == 401:
                    return LLMPermanentError(f"NVIDIA NIM auth error (401): check NVIDIA_API_KEY")
                if status == 403:
                    # 403 通常意味着该 key/账号 对该模型没有权限、被封禁或触发了
                    # 地域限制 —— 这是持久性错误，短时间内重试几乎必然得到同样
                    # 的 403，因此不重试，直接交给上层触发 fallback 切换。
                    return LLMPermanentError(f"NVIDIA NIM forbidden (403): {msg}")
                if status == 400:
                    return LLMProviderError(f"NVIDIA NIM bad request (400): {msg}")
                return LLMProviderError(f"NVIDIA NIM HTTP {status}: {msg}")
        except ImportError:
            pass
        return LLMProviderError(f"NVIDIA NIM error: {exc}")

    def validate_config(self) -> None:
        if not self.config.api_key:
            raise LLMProviderError(
                "NVIDIA NIM requires an API key. "
                "Set NVIDIA_API_KEY or pass api_key= to LLMConfig."
            )


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _prepend_system(messages: list[dict], system: str) -> list[dict]:
    if not system:
        return list(messages)
    if messages and messages[0].get("role") == "system":
        return [{"role": "system", "content": system}] + list(messages[1:])
    return [{"role": "system", "content": system}] + list(messages)


def _map_finish_reason(reason: str) -> str:
    return {
        "stop":           "end_turn",
        "tool_calls":     "tool_use",
        "length":         "max_tokens",
        "content_filter": "stop",
        "eos":            "end_turn",
    }.get(reason or "stop", reason or "end_turn")
