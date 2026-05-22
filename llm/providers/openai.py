"""
llm/providers/openai.py — OpenAI / OpenAI-compatible provider

对接 openai Python SDK，支持：
  - OpenAI 官方 API（gpt-4o, o1-*, gpt-4-turbo 等）
  - Azure OpenAI（通过 base_url + api_key）
  - 任何兼容 OpenAI Chat Completions API 的服务
    （DeepSeek、Moonshot、Qwen、Groq、Together、Fireworks 等）

兼容条件：目标服务的 /v1/chat/completions 接口须支持
  - messages（含 tool 角色）
  - tools（function calling 格式）
  - stream: true 时返回 SSE
"""

from __future__ import annotations

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


class OpenAIProvider(LLMClient):
    """
    OpenAI Chat Completions API provider。

    同时兼容：Azure OpenAI、Groq、Moonshot、DeepSeek 等
    只需在 LLMConfig 中指定 base_url 即可切换。

    tool_choice 默认为 "auto"，可通过 config.extra["tool_choice"] 覆盖。
    """

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self._client = self._build_client()

    def _build_client(self):
        try:
            from openai import OpenAI
        except ImportError:
            raise LLMProviderError(
                "openai SDK not installed. Run: pip install openai"
            )
        kwargs: dict = {"api_key": self.config.api_key}
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        if self.config.timeout:
            kwargs["timeout"] = float(self.config.timeout)
        return OpenAI(**kwargs)

    # ── LLMClient 接口实现 ────────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        system: str,
        tools: list[ToolSchema],
    ) -> LLMResponse:
        """非流式调用。"""
        full_messages = self._prepend_system(messages, system)
        kwargs = self._build_kwargs(full_messages, tools, stream=False)
        try:
            resp = self._client.chat.completions.create(**kwargs)
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
        """流式调用，逐 token 触发 on_token 回调。"""
        full_messages = self._prepend_system(messages, system)
        kwargs = self._build_kwargs(full_messages, tools, stream=True)
        # 收集完整内容用于解析 tool_calls
        collected_text: list[str] = []
        collected_tool_calls: dict[int, dict] = {}   # index → partial tool call

        try:
            with self._client.chat.completions.stream(**kwargs) as stream:
                for event in stream:
                    for choice in getattr(event, "choices", []):
                        delta = getattr(choice, "delta", None)
                        if delta is None:
                            continue
                        # 文本 token
                        if delta.content:
                            on_token(delta.content)
                            collected_text.append(delta.content)
                        # 工具调用 delta
                        for tc_delta in getattr(delta, "tool_calls", None) or []:
                            idx = tc_delta.index
                            if idx not in collected_tool_calls:
                                collected_tool_calls[idx] = {
                                    "id": "",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc_delta.id:
                                collected_tool_calls[idx]["id"] += tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    collected_tool_calls[idx]["name"] += tc_delta.function.name
                                if tc_delta.function.arguments:
                                    collected_tool_calls[idx]["arguments"] += tc_delta.function.arguments
                final = stream.get_final_completion()
        except Exception as e:
            raise self._wrap_error(e)

        usage = LLMUsage(
            input_tokens=getattr(final.usage, "prompt_tokens", 0),
            output_tokens=getattr(final.usage, "completion_tokens", 0),
            total_tokens=getattr(final.usage, "total_tokens", 0),
        )
        tool_calls = self._parse_tool_calls_from_stream(collected_tool_calls)
        return LLMResponse(
            text="".join(collected_text),
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=self._map_finish_reason(
                getattr(final.choices[0], "finish_reason", "stop") if final.choices else "stop"
            ),
            raw=final,
        )

    def format_tools(self, tools: list[ToolSchema]) -> list[dict]:
        """OpenAI function-calling 格式（parameters 字段，非 input_schema）。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _prepend_system(messages: list[dict], system: str) -> list[dict]:
        """OpenAI 无独立 system 参数，需作为首条 system 角色消息插入。"""
        if not system:
            return messages
        # 若已有 system 消息则替换，否则插入到最前
        if messages and messages[0].get("role") == "system":
            return [{"role": "system", "content": system}] + messages[1:]
        return [{"role": "system", "content": system}] + messages

    def _build_kwargs(self, messages: list[dict], tools: list[ToolSchema], stream: bool) -> dict:
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
        kwargs.update({k: v for k, v in self.config.extra.items() if k != "tool_choice"})
        return kwargs

    def _parse_response(self, resp) -> LLMResponse:
        """将 openai.types.chat.ChatCompletion 转换为 LLMResponse。"""
        choice = resp.choices[0] if resp.choices else None
        text = ""
        tool_calls: list[ToolCall] = []

        if choice:
            msg = choice.message
            text = msg.content or ""
            for tc in getattr(msg, "tool_calls", None) or []:
                import json
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        input=json.loads(tc.function.arguments or "{}"),
                    )
                )

        usage = LLMUsage(
            input_tokens=getattr(resp.usage, "prompt_tokens", 0),
            output_tokens=getattr(resp.usage, "completion_tokens", 0),
            total_tokens=getattr(resp.usage, "total_tokens", 0),
        )

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=self._map_finish_reason(
                getattr(choice, "finish_reason", "stop") if choice else "stop"
            ),
            raw=resp,
        )

    @staticmethod
    def _parse_tool_calls_from_stream(collected: dict[int, dict]) -> list[ToolCall]:
        import json
        tool_calls = []
        for _, tc in sorted(collected.items()):
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc["id"], name=tc["name"], input=args))
        return tool_calls

    @staticmethod
    def _map_finish_reason(reason: str) -> str:
        mapping = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
            "content_filter": "stop",
        }
        return mapping.get(reason, reason)

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
