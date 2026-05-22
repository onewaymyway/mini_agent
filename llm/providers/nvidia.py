"""
llm/providers/nvidia.py — NVIDIA NIM API provider

基于 NVIDIA Inference Microservices (NIM) 平台，使用 OpenAI 兼容的接口。
在标准 OpenAI 格式之上支持 NVIDIA 特有的 reasoning_content 字段（思维链输出）。

支持的模型类型：
  - 通用对话模型：meta/llama-3.1-405b-instruct, mistralai/mixtral-8x22b-instruct 等
  - 思维链模型：nvidia/llama-3.3-nemotron-super-49b-v1, stepfun-ai/step-3.5-flash 等
    （这些模型会在 delta 中额外返回 reasoning_content 字段）

环境变量：
  NVIDIA_API_KEY — NVIDIA NIM API key（从 https://build.nvidia.com 获取）

参考用法：
    from llm import create_client, LLMConfig

    client = create_client(LLMConfig(
        provider="nvidia",
        model="stepfun-ai/step-3.5-flash",
        api_key="nvapi-...",
    ))
    response = client.chat(messages, system, tools)
    print(response.text)
    if response.reasoning:
        print("思维链:", response.reasoning)
"""

from __future__ import annotations

import json
import os
from typing import Optional

from llm.base import (
    LLMClient,
    LLMConfig,
    LLMResponse,
    LLMUsage,
    ToolCall,
    ToolSchema,
    StreamCallback,
    ReasoningCallback,
    LLMProviderError,
    LLMTimeoutError,
    LLMRateLimitError,
)

_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# 已知支持 reasoning_content 的模型前缀/名称（用于提示，不做强制限制）
_REASONING_MODELS = {
    "nvidia/llama-3.3-nemotron-super-49b-v1",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "stepfun-ai/step-3.5-flash",
    "deepseek-ai/deepseek-r1",
}


class NvidiaProvider(LLMClient):
    """
    NVIDIA NIM API provider。

    继承 OpenAI 兼容格式，额外处理：
      - reasoning_content 字段（流式思维链 token）
      - 自动设置 base_url 为 NVIDIA NIM 端点
      - 默认从 NVIDIA_API_KEY 环境变量读取 key

    流式调用时，思维链内容通过 on_reasoning 回调分发，
    最终整合进 LLMResponse.reasoning 字段。
    """

    def __init__(self, config: LLMConfig) -> None:
        # 自动填充 base_url
        if not config.base_url:
            config.base_url = _NVIDIA_BASE_URL
        # 自动从环境变量读取 key
        if not config.api_key:
            config.api_key = os.environ.get("NVIDIA_API_KEY", "")
        super().__init__(config)
        self._client = self._build_client()

    @property
    def provider_name(self) -> str:
        return "NVIDIA"

    def supports_reasoning(self) -> bool:
        """判断当前模型是否可能输出 reasoning_content。"""
        return self.config.model in _REASONING_MODELS

    # ── LLMClient 接口实现 ────────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        system: str,
        tools: list[ToolSchema],
        on_reasoning: Optional[ReasoningCallback] = None,
    ) -> LLMResponse:
        """
        非流式调用。

        注意：NVIDIA NIM 的部分思维链模型在非流式模式下
        会将推理过程放在 message.content 开头的 <think>...</think> 块中。
        本方法会自动解析并分离它们。
        """
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
        on_reasoning: Optional[ReasoningCallback] = None,
    ) -> LLMResponse:
        """
        流式调用，分别处理 content token 和 reasoning_content token。

        Args:
            on_token:     普通文本 token 回调（必须）
            on_reasoning: 思维链 token 回调（可选，不传则静默忽略）
        """
        full_messages = self._prepend_system(messages, system)
        kwargs = self._build_kwargs(full_messages, tools, stream=True)

        collected_text: list[str] = []
        collected_reasoning: list[str] = []
        collected_tool_calls: dict[int, dict] = {}
        final_usage = LLMUsage()
        finish_reason = "stop"

        try:
            with self._client.chat.completions.stream(**kwargs) as s:
                for event in s:
                    # print("event",event)
                    t=getattr(event,"chunk",None)
                    if t:
                        event=t
                    # print('getattr(event, "choices", [])',getattr(event, "choices", []))
                    for choice in getattr(event, "choices", []):
                        # print("choice",choice)
                        delta = getattr(choice, "delta", None)
                        if delta is None:
                            continue
                        # print("delta",delta)

                        # ── reasoning_content（NVIDIA 思维链专属字段）──────
                        reasoning_token = getattr(delta, "reasoning_content", None)
                        if reasoning_token:
                            collected_reasoning.append(reasoning_token)
                            if on_reasoning:
                                on_reasoning(reasoning_token)

                        # ── 普通文本 content ──────────────────────────────
                        if delta.content:
                            on_token(delta.content)
                            collected_text.append(delta.content)

                        # ── 工具调用 delta ────────────────────────────────
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

                        if getattr(choice, "finish_reason", None):
                            finish_reason = choice.finish_reason

                final = s.get_final_completion()
                # print("s:",s)
                if final and final.usage:
                    final_usage = LLMUsage(
                        input_tokens=getattr(final.usage, "prompt_tokens", 0),
                        output_tokens=getattr(final.usage, "completion_tokens", 0),
                        total_tokens=getattr(final.usage, "total_tokens", 0),
                    )
        except Exception as e:
            raise self._wrap_error(e)

        tool_calls = self._parse_tool_calls_from_stream(collected_tool_calls)
        return LLMResponse(
            text="".join(collected_text),
            reasoning="".join(collected_reasoning),
            tool_calls=tool_calls,
            usage=final_usage,
            stop_reason=self._map_finish_reason(finish_reason),
        )

    def format_tools(self, tools: list[ToolSchema]) -> list[dict]:
        """NVIDIA NIM 使用 OpenAI function-calling 格式。"""
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

    def _build_client(self):
        try:
            from openai import OpenAI
        except ImportError:
            raise LLMProviderError(
                "openai SDK not installed. Run: pip install openai"
            )
        return OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=float(self.config.timeout),
        )

    @staticmethod
    def _prepend_system(messages: list[dict], system: str) -> list[dict]:
        if not system:
            return messages
        if messages and messages[0].get("role") == "system":
            return [{"role": "system", "content": system}] + messages[1:]
        return [{"role": "system", "content": system}] + messages

    def _build_kwargs(self, messages: list[dict], tools: list[ToolSchema], stream: bool) -> dict:
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.extra.get("temperature", self.config.temperature),
            "top_p": self.config.extra.get("top_p", 0.9),
            # "stream": stream,
        }
        if tools:
            kwargs["tools"] = self.format_tools(tools)
            kwargs["tool_choice"] = self.config.extra.get("tool_choice", "auto")
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        return kwargs

    def _parse_response(self, resp) -> LLMResponse:
        """解析非流式响应，同时处理 <think>...</think> 包裹的推理内容。"""
        choice = resp.choices[0] if resp.choices else None
        raw_text = ""
        tool_calls: list[ToolCall] = []

        if choice:
            msg = choice.message
            print("msg:",msg)
            raw_text = msg.content or ""
            for tc in getattr(msg, "tool_calls", None) or []:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=json.loads(tc.function.arguments or "{}"),
                ))

        # 分离 <think>...</think> 中的推理内容（部分模型非流式时使用此格式）
        text, reasoning = _extract_think_block(raw_text)
        # print("raw_text",raw_text)
        # print("text:",text)
        # print("reasoning:",reasoning)

        usage = LLMUsage(
            input_tokens=getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0,
            output_tokens=getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0,
            total_tokens=getattr(resp.usage, "total_tokens", 0) if resp.usage else 0,
        )
        return LLMResponse(
            text=text,
            reasoning=reasoning,
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=self._map_finish_reason(
                getattr(choice, "finish_reason", "stop") if choice else "stop"
            ),
            raw=resp,
        )

    @staticmethod
    def _parse_tool_calls_from_stream(collected: dict[int, dict]) -> list[ToolCall]:
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
        return {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
            "content_filter": "stop",
        }.get(reason, reason)

    def _wrap_error(self, exc: Exception) -> LLMProviderError:
        try:
            from openai import RateLimitError, APITimeoutError, APIError
            if isinstance(exc, RateLimitError):
                return LLMRateLimitError(f"NVIDIA NIM rate limit: {exc}")
            if isinstance(exc, APITimeoutError):
                return LLMTimeoutError(f"NVIDIA NIM timeout: {exc}")
            if isinstance(exc, APIError):
                return LLMProviderError(f"NVIDIA NIM API error: {exc}")
        except ImportError:
            pass
        return LLMProviderError(f"NVIDIA NIM error: {exc}")


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _extract_think_block(text: str) -> tuple[str, str]:
    """
    从文本中提取 <think>...</think> 块作为推理内容。
    返回 (正文, 推理内容)，若无 think 块则推理内容为空字符串。

    部分 NVIDIA 模型在非流式模式下将推理过程包裹在此标签中。
    """
    import re
    pattern = re.compile(r"<think>(.*?)</think>\s*", re.DOTALL)
    reasoning_parts: list[str] = []

    def collect(m: re.Match) -> str:
        reasoning_parts.append(m.group(1).strip())
        return ""

    clean_text = pattern.sub(collect, text).strip()
    return clean_text, "\n\n".join(reasoning_parts)
