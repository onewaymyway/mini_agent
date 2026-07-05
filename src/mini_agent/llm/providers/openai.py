"""
llm/providers/openai.py — OpenAI / OpenAI-compatible provider

支持 OpenAI 官方 API 及所有兼容服务（Azure、Groq、DeepSeek 等）。
支持 SDK 原生 function calling 和 system-prompt tool call 两种模式。
"""

from __future__ import annotations

import json
from ..base import (
    LLMClient, LLMConfig, LLMResponse, LLMUsage,
    ToolCall, ToolSchema, StreamCallback,
    LLMProviderError, LLMTimeoutError, LLMRateLimitError,
)
from ._base_mixin import ProviderMixin


class OpenAIProvider(ProviderMixin, LLMClient):

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self._client = self._build_client()

    def _build_client(self):
        try:
            from openai import OpenAI
        except ImportError:
            raise LLMProviderError("openai SDK not installed. Run: pip install openai")
        kwargs: dict = {"api_key": self.config.api_key}
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        if self.config.timeout:
            kwargs["timeout"] = float(self.config.timeout)
        # 支持通过 config.extra["default_headers"] 注入额外请求头
        # 用于 OpenRouter 等需要自定义头的服务
        if self.config.extra.get("default_headers"):
            kwargs["default_headers"] = self.config.extra["default_headers"]
        return OpenAI(**kwargs)

    # ── 公共接口 ───────────────────────────────────────────────────────────────

    def chat(self, messages: list[dict], system: str, tools: list[ToolSchema]) -> LLMResponse:
        return self._traced_chat(self._do_chat, messages, system, tools)

    def stream(self, messages: list[dict], system: str, tools: list[ToolSchema],
               on_token: StreamCallback) -> LLMResponse:
        return self._traced_stream(self._do_stream, messages, system, tools, on_token)

    # ── 实际 SDK 调用 ──────────────────────────────────────────────────────────

    def _do_chat(self, messages, system, tools):
        full_messages = self._prepend_system(messages, system)
        kwargs = self._build_kwargs(full_messages, tools, stream=False)
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            raise self._wrap_error(e)
        return self._parse_response(resp)

    def _do_stream(self, messages, system, tools, on_token):
        full_messages = self._prepend_system(messages, system)
        kwargs = self._build_kwargs(full_messages, tools, stream=True)
        # completions.stream() is a context manager and must not receive stream=True
        kwargs.pop("stream", None)
        collected_text: list[str] = []
        collected_reasoning: list[str] = []
        collected_refusal: list[str] = []
        collected_tool_calls: dict[int, dict] = {}

        try:
            with self._client.chat.completions.stream(**kwargs) as s:
                for event in s:
                    # print("event:",event)
                    if hasattr(event,"chunk"):
                        event=event.chunk
                    for choice in getattr(event, "choices", []):
                        delta = getattr(choice, "delta", None)
                        # print("choice:",choice)
                        if delta is None:
                            continue
                        
                        if delta.content:
                            on_token(delta.content)
                            collected_text.append(delta.content)
                        # 推理 token（reasoning_content）：部分兼容网关的 thinking
                        # 模式会走单独的 delta 字段，不提取的话这部分 output_tokens
                        # 会"凭空消失"（text 和 reasoning 都是空的，但 usage 里有数）。
                        reasoning_delta = (
                            getattr(delta, "reasoning_content", None)
                            or getattr(delta, "reasoning", None)
                        )
                        if reasoning_delta:
                            collected_reasoning.append(reasoning_delta)
                        # 安全/合规拒答 delta：同非流式路径的 message.refusal。
                        refusal_delta = getattr(delta, "refusal", None)
                        if refusal_delta:
                            collected_refusal.append(refusal_delta)
                        for tc_delta in getattr(delta, "tool_calls", None) or []:
                            idx = tc_delta.index
                            if idx not in collected_tool_calls:
                                collected_tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                            if tc_delta.id:
                                collected_tool_calls[idx]["id"] += tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    collected_tool_calls[idx]["name"] += tc_delta.function.name
                                if tc_delta.function.arguments:
                                    collected_tool_calls[idx]["arguments"] += tc_delta.function.arguments
                final = s.get_final_completion()
        except Exception as e:
            raise self._wrap_error(e)

        usage = LLMUsage(
            input_tokens=getattr(final.usage, "prompt_tokens", 0),
            output_tokens=getattr(final.usage, "completion_tokens", 0),
            total_tokens=getattr(final.usage, "total_tokens", 0),
        )
        tool_calls = self._parse_tool_calls_stream(collected_tool_calls)
        finish_raw = getattr(final.choices[0], "finish_reason", "stop") if final.choices else "stop"

        # 兜底：流式增量里没读到 refusal delta，但最终 completion 的
        # message.refusal 仍可能非空（有些网关只在 final completion 里给出）。
        final_refusal = "".join(collected_refusal)
        if not final_refusal and final.choices:
            final_refusal = getattr(final.choices[0].message, "refusal", None) or ""

        return LLMResponse(
            text="".join(collected_text), tool_calls=tool_calls,
            usage=usage,
            reasoning="".join(collected_reasoning),
            refusal=final_refusal,
            stop_reason=self._map_finish(finish_raw),
            finish_reason_raw=finish_raw,
        )

    def format_tools(self, tools: list[ToolSchema]) -> list[dict]:
        return [
            {"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.input_schema,
            }} for t in tools
        ]

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _prepend_system(messages: list[dict], system: str) -> list[dict]:
        if not system:
            return messages
        if messages and messages[0].get("role") == "system":
            return [{"role": "system", "content": system}] + messages[1:]
        return [{"role": "system", "content": system}] + messages

    def _build_kwargs(self, messages, tools, stream: bool) -> dict:
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "stream": stream,
        }
        if tools:
            kwargs["tools"] = self.format_tools(tools)
            kwargs["tool_choice"] = self.config.extra.get("tool_choice", "auto")
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        # config.extra 中部分键是客户端级配置（如 default_headers，用于构造
        # OpenAI() client），不应随请求 kwargs 传给 chat.completions.create()/stream()
        _CLIENT_LEVEL_KEYS = {"tool_choice", "default_headers"}
        kwargs.update({k: v for k, v in self.config.extra.items() if k not in _CLIENT_LEVEL_KEYS})
        return kwargs

    def _parse_response(self, resp) -> LLMResponse:
        choice = resp.choices[0] if resp.choices else None
        text = ""
        reasoning = ""
        refusal = ""
        tool_calls: list[ToolCall] = []
        if choice:
            msg = choice.message
            text = msg.content or ""
            # 安全/合规拒答：OpenAI 兼容协议里模型拒答时 content 为 None，
            # 实际拒答文本在 message.refusal 里——不提取的话，output_tokens
            # 会显示消耗了 token，但 text 却是空的（本次要修的现象之一）。
            refusal = getattr(msg, "refusal", None) or ""
            # 思维链/推理内容：不同网关字段名不完全一致，常见的是
            # reasoning_content（DeepSeek/大部分兼容网关）或 reasoning，
            # 都尝试读取；读不到则保持空字符串。
            reasoning = (
                getattr(msg, "reasoning_content", None)
                or getattr(msg, "reasoning", None)
                or ""
            )
            for tc in getattr(msg, "tool_calls", None) or []:
                tool_calls.append(ToolCall(
                    id=tc.id, name=tc.function.name,
                    input=json.loads(tc.function.arguments or "{}"),
                ))
        usage = LLMUsage(
            input_tokens=getattr(resp.usage, "prompt_tokens", 0),
            output_tokens=getattr(resp.usage, "completion_tokens", 0),
            total_tokens=getattr(resp.usage, "total_tokens", 0),
        )
        finish_raw = getattr(choice, "finish_reason", "stop") if choice else "stop"
        return LLMResponse(
            text=text, tool_calls=tool_calls, usage=usage,
            reasoning=reasoning, refusal=refusal,
            stop_reason=self._map_finish(finish_raw),
            finish_reason_raw=finish_raw,
            raw=resp,
        )

    @staticmethod
    def _parse_tool_calls_stream(collected: dict) -> list[ToolCall]:
        result = []
        for _, tc in sorted(collected.items()):
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            result.append(ToolCall(id=tc["id"], name=tc["name"], input=args))
        return result

    @staticmethod
    def _map_finish(reason: str) -> str:
        return {"stop": "end_turn", "tool_calls": "tool_use",
                "length": "max_tokens", "content_filter": "stop"}.get(reason, reason)

    def _wrap_error(self, exc: Exception) -> LLMProviderError:
        try:
            from openai import RateLimitError, APITimeoutError, APIError
            if isinstance(exc, RateLimitError):
                return LLMRateLimitError(f"OpenAI rate limit: {exc}")
            if isinstance(exc, APITimeoutError):
                return LLMTimeoutError(f"OpenAI timeout: {exc}")
            if isinstance(exc, APIError):
                return LLMProviderError(f"OpenAI API error: {exc}")
        except ImportError:
            pass
        return LLMProviderError(f"OpenAI error: {exc}")
