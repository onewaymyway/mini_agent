"""
llm/providers/anthropic.py — Anthropic Claude provider

对接 anthropic Python SDK，将 SDK 原始响应转换为统一的 LLMResponse。
支持：
  - 流式 / 非流式
  - SDK 原生 tool_use（默认）
  - System-prompt tool call 模式（use_system_tool_call=True）
  - 完整调试日志（通过 ProviderMixin）
"""

from __future__ import annotations

from ..base import (
    LLMClient, LLMConfig, LLMResponse, LLMUsage,
    ToolCall, ToolSchema, StreamCallback,
    LLMProviderError, LLMTimeoutError, LLMRateLimitError,
)
from ._base_mixin import ProviderMixin


class AnthropicProvider(ProviderMixin, LLMClient):

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self._client = self._build_client()

    def _build_client(self):
        try:
            import anthropic
        except ImportError:
            raise LLMProviderError("anthropic SDK not installed. Run: pip install anthropic")
        kwargs: dict = {"api_key": self.config.api_key}
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        return anthropic.Anthropic(**kwargs)

    # ── 公共接口（带日志 + system tool call） ─────────────────────────────────

    def chat(self, messages: list[dict], system: str, tools: list[ToolSchema]) -> LLMResponse:
        return self._traced_chat(self._do_chat, messages, system, tools)

    def stream(self, messages: list[dict], system: str, tools: list[ToolSchema],
               on_token: StreamCallback) -> LLMResponse:
        return self._traced_stream(self._do_stream, messages, system, tools, on_token)

    # ── 实际 SDK 调用 ──────────────────────────────────────────────────────────

    def _do_chat(self, messages, system, tools):
        try:
            resp = self._client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=system,
                messages=messages,
                # tools 已通过 system prompt 传递，不传 SDK tools 参数
                timeout=self.config.timeout,
                **self.config.extra,
            )
        except Exception as e:
            raise self._wrap_error(e)
        return self._parse_response(resp)

    def _do_stream(self, messages, system, tools, on_token):
        try:
            with self._client.messages.stream(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=system,
                messages=messages,
                # tools 已通过 system prompt 传递，不传 SDK tools 参数
                timeout=self.config.timeout,
                **self.config.extra,
            ) as stream:
                for token in stream.text_stream:
                    on_token(token)
                raw = stream.get_final_message()
        except Exception as e:
            raise self._wrap_error(e)
        return self._parse_response(raw)

    def format_tools(self, tools: list[ToolSchema]) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]

    def _parse_response(self, resp) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))
        usage = LLMUsage(
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            total_tokens=resp.usage.input_tokens + resp.usage.output_tokens,
        )
        return LLMResponse(
            text="\n".join(text_parts), tool_calls=tool_calls,
            usage=usage, stop_reason=resp.stop_reason or "end_turn", raw=resp,
        )

    def _wrap_error(self, exc: Exception) -> LLMProviderError:
        try:
            import anthropic
            if isinstance(exc, anthropic.RateLimitError):
                return LLMRateLimitError(f"Anthropic rate limit: {exc}")
            if isinstance(exc, anthropic.APITimeoutError):
                return LLMTimeoutError(f"Anthropic timeout: {exc}")
            if isinstance(exc, anthropic.APIError):
                return LLMProviderError(f"Anthropic API error: {exc}")
        except ImportError:
            pass
        return LLMProviderError(f"Anthropic error: {exc}")
