"""
llm/providers/anthropic.py — Anthropic Claude provider

对接 anthropic Python SDK，将 SDK 原始响应转换为统一的 LLMResponse。
支持流式和非流式两种调用模式。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from llm.base import (
    LLMClient,
    LLMConfig,
    LLMResponse,
    LLMUsage,
    ToolCall,
    ToolSchema,
    StreamCallback,
    LLMProviderError,
    LLMTimeoutError,
    LLMRateLimitError,
)

if TYPE_CHECKING:
    pass


class AnthropicProvider(LLMClient):
    """
    Anthropic Messages API provider。

    支持：
      - claude-opus-4-5, claude-sonnet-4-5, claude-haiku-*  等所有 claude-* 模型
      - 流式 / 非流式
      - tool_use（工具调用）
    """

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self._client = self._build_client()

    def _build_client(self):
        try:
            import anthropic
        except ImportError:
            raise LLMProviderError(
                "anthropic SDK not installed. Run: pip install anthropic"
            )
        kwargs: dict = {"api_key": self.config.api_key}
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        return anthropic.Anthropic(**kwargs)

    # ── LLMClient 接口实现 ────────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        system: str,
        tools: list[ToolSchema],
    ) -> LLMResponse:
        """非流式调用，等待完整响应后返回。"""
        try:
            resp = self._client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=system,
                messages=messages,
                tools=self.format_tools(tools),
                timeout=self.config.timeout,
                **self.config.extra,
            )
        except Exception as e:
            raise self._wrap_error(e)

        return self._parse_response(resp)

    def stream(
        self,
        messages: list[dict],
        system: str,
        tools: list[ToolSchema],
        on_token: StreamCallback,
    ) -> LLMResponse:
        """流式调用，每个 token 触发 on_token 回调，结束后返回完整 LLMResponse。"""
        try:
            with self._client.messages.stream(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=system,
                messages=messages,
                tools=self.format_tools(tools),
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
        """Anthropic 的工具格式：input_schema 字段（非 parameters）。"""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    def _parse_response(self, resp) -> LLMResponse:
        """将 anthropic.types.Message 转换为 LLMResponse。"""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, input=block.input)
                )

        usage = LLMUsage(
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            total_tokens=resp.usage.input_tokens + resp.usage.output_tokens,
        )

        return LLMResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=resp.stop_reason or "end_turn",
            raw=resp,
        )

    def _wrap_error(self, exc: Exception) -> LLMProviderError:
        """将 anthropic SDK 异常转换为统一的 LLM 异常。"""
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
