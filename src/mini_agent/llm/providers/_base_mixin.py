"""
llm/providers/_base_mixin.py — Provider 公共 Mixin

为所有 provider 提供两个横切能力：
  1. 调试日志（记录原始输入、实际发给API的输入、原始输出、处理后输出）
  2. system-prompt tool call 模式（自动注入协议、后处理响应）
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from ..base import LLMClient, LLMResponse, LLMUsage, ToolSchema, StreamCallback
from mini_agent.orchestrator.concurrency import get_llm_sem
from ..debug_logger import get_debug_logger
from ..system_tool_call import (
    render_tool_list,
    render_tool_results,
    postprocess_response,
    convert_system_to_message,
)
from mini_agent.prompts import pm as _pm


class ProviderMixin:
    """
    横切 Mixin，必须与 LLMClient 子类一起使用。
    在 MRO 中放在 LLMClient 之前：class MyProvider(ProviderMixin, LLMClient)
    """

    # ── 带日志和 system tool call 的调用入口 ──────────────────────────────────

    def _traced_chat(
        self,
        impl: Callable,
        messages: list[dict],
        system: str,
        tools: list[ToolSchema],
    ) -> LLMResponse:
        """
        包装 _do_chat()，记录完整的原始/实际请求和原始/处理后响应。
        """
        # 1. 准备工具（注入协议到 system）
        system_final, api_tools = self._prepare_tools(system, tools)

        # 1b. 根据 system_message_format 决定是否将 system 合并进 messages
        system_final, messages = self._apply_system_format(system_final, messages)

        # 2. 记录请求（原始 + 实际发给 API 的）
        logger = get_debug_logger()
        seq = logger.log_request(
            provider=self.config.provider,
            model=self.config.model,
            raw_system=system,
            raw_messages=messages,
            raw_tools=[t.__dict__ for t in tools],
            actual_system=system_final,
            actual_api_tools=api_tools,  # 始终为 []
            stream=False,
        )

        sem = get_llm_sem()
        sem_label = f"{self.config.provider}/{self.config.model[:20]}"
        t0 = time.monotonic()
        try:
            with sem.acquire(label=sem_label):
                raw_response = impl(messages, system_final, api_tools)

            # 3. postprocess（提取 tool_use 块、think 标签）
            processed_response = self._postprocess(raw_response, tools)

            # 4. 记录响应（原始 + 处理后）
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.log_response(
                seq=seq,
                provider=self.config.provider,
                model=self.config.model,
                raw_response=raw_response,
                processed_response=processed_response,
                duration_ms=duration_ms,
            )
            return processed_response

        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.log_error(seq, self.config.provider, self.config.model, e, duration_ms)
            raise

    def _traced_stream(
        self,
        impl: Callable,
        messages: list[dict],
        system: str,
        tools: list[ToolSchema],
        on_token: StreamCallback,
        **extra_kwargs,
    ) -> LLMResponse:
        """
        包装 _do_stream()，记录完整的原始/实际请求和原始/处理后响应。
        extra_kwargs 透传给 impl（如 on_reasoning）。
        """
        system_final, api_tools = self._prepare_tools(system, tools)
        system_final, messages = self._apply_system_format(system_final, messages)

        logger = get_debug_logger()
        seq = logger.log_request(
            provider=self.config.provider,
            model=self.config.model,
            raw_system=system,
            raw_messages=messages,
            raw_tools=[t.__dict__ for t in tools],
            actual_system=system_final,
            actual_api_tools=api_tools,
            stream=True,
        )

        sem = get_llm_sem()
        sem_label = f"{self.config.provider}/{self.config.model[:20]}"
        t0 = time.monotonic()
        try:
            with sem.acquire(label=sem_label):
                raw_response = impl(
                    messages, system_final, api_tools, on_token, **extra_kwargs
                )

            processed_response = self._postprocess(raw_response, tools)

            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.log_response(
                seq=seq,
                provider=self.config.provider,
                model=self.config.model,
                raw_response=raw_response,
                processed_response=processed_response,
                duration_ms=duration_ms,
            )
            return processed_response

        except Exception as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.log_error(seq, self.config.provider, self.config.model, e, duration_ms)
            raise

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    def _prepare_tools(
        self, system: str, tools: list[ToolSchema]
    ) -> tuple[str, list[ToolSchema]]:
        """
        统一使用 system-prompt 工具协议。
        将工具描述和调用格式注入到 system prompt，
        api_tools 始终返回空列表（不向 SDK 传递 tools 参数）。
        无工具时直接返回原始 system，不做修改。
        """
        if not tools:
            return system, []

        tool_list_text = render_tool_list(tools)
        protocol_section = _pm.render(
            "system/tool_call_protocol",
            tool_list=tool_list_text,
        )
        system_with_protocol = system.rstrip() + "\n\n" + protocol_section
        return system_with_protocol, []

    def _postprocess(
        self, response: LLMResponse, original_tools: list[ToolSchema]
    ) -> LLMResponse:
        """
        通用响应后处理：
          - 从文本中提取 <tool_use> 块 → tool_calls
          - 从文本中提取 <think>/<thinking>/<reasoning> → reasoning
          - 清理 text（移除已提取的块）
        对所有 provider 都执行，不依赖 provider 类型。
        """
        return postprocess_response(response)

    def _apply_system_format(
        self, system: str, messages: list[dict]
    ) -> tuple[str, list[dict]]:
        """
        根据 config.system_message_format 决定如何传递 system prompt：

          "system_field" (默认)：
            保持 system 字段不变，messages 不修改。
            发给模型：{ system: "...", messages: [...] }

          "system_role"：
            将 system 内容作为 role="system" 的首条消息注入 messages，
            同时清空 system 字段。
            发给模型：{ system: "", messages: [{"role":"system","content":"..."}, ...] }
        """
        fmt = getattr(self.config, "system_message_format", "system_field")
        if fmt == "system_role":
            return convert_system_to_message(system, messages)
        return system, messages


def make_tool_result_message(tool_calls, results: list[str]) -> dict:
    """构造回注工具结果的 user 消息（<tool_result> XML 格式）。"""
    content = render_tool_results(tool_calls, results)
    return {"role": "user", "content": content}
