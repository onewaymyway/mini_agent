"""
llm/providers/_base_mixin.py — Provider 公共 Mixin

为所有 provider 提供两个横切能力：
  1. 调试日志（记录原始输入、实际发给API的输入、原始输出、处理后输出）
  2. system-prompt tool call 模式（自动注入协议、后处理响应）

错误分层说明
------------
  _wrap_error()（各 provider 自己实现）
    负责把 SDK 原生异常（openai.RateLimitError / anthropic.APIError / httpx.HTTPStatusError 等）
    翻译成 LLM 层统一异常（LLMRateLimitError / LLMTimeoutError / LLMProviderError）。
    由于各 SDK 异常类型完全不同，这一层必须在各 provider 内实现，无法合并。

  _upgrade_error()（此 Mixin 提供）
    在 _wrap_error() 之后执行，对已经统一为 LLMProviderError 的异常再做一次
    语义升级：检测消息文本中的 context window 超限关键词，将其升级为
    LLMContextWindowError。关键词检测逻辑所有 provider 完全一致，因此集中在此处，
    不在各 provider 重复。
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from ..base import LLMClient, LLMResponse, LLMUsage, ToolSchema, StreamCallback
from ..base import LLMProviderError, LLMContextWindowError
from mini_agent.orchestrator.concurrency import get_llm_sem
from ..debug_logger import get_debug_logger
from ..system_tool_call import (
    render_tool_list,
    render_tool_results,
    postprocess_response,
    convert_system_to_message,
)
from mini_agent.prompts import pm as _pm

# ── context window 超限关键词（所有 provider 共用） ───────────────────────────
# 各 provider API 报错消息的写法不同，但语义一样；统一放在 Mixin 做一次检测，
# 不在各 provider 的 _wrap_error 里重复。新增 provider 时无需关心此逻辑。
_CONTEXT_WINDOW_KEYWORDS: tuple[str, ...] = (
    "ContextWindowExceeded",       # OpenAI / OpenRouter / NVIDIA NIM 透传
    "context_length_exceeded",     # OpenAI 官方错误码
    "maximum context length",      # OpenAI 错误消息文本
    "prompt is too long",          # Anthropic
    "context window is full",      # 部分兼容层
    "input is too long",           # 部分本地模型
)


def _is_context_window_error(msg: str) -> bool:
    """判断错误消息是否表示 context window 超限。大小写不敏感。"""
    lower = msg.lower()
    return any(kw.lower() in lower for kw in _CONTEXT_WINDOW_KEYWORDS)


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

        # 2b. RPM 频率限速（超限时阻塞等待）
        from mini_agent.orchestrator.concurrency import get_rate_limiter
        get_rate_limiter().acquire()

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
            raise self._upgrade_error(e)

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
        集成 RPM 限速和实时 token 计数（状态栏显示）。
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

        # RPM 频率限速
        from mini_agent.orchestrator.concurrency import get_rate_limiter, get_stream_token_state
        get_rate_limiter().acquire()

        # 包装 on_token：同时更新全局 token 计数状态
        # 注意：start() 返回本路 stream 专属的 stream_id，
        # increment()/stop() 必须带上它，避免并发多路流互相覆盖/提前清空
        # （旧版全局单一 active 标志在并发场景下会导致状态栏间歇性消失）。
        _token_state = get_stream_token_state()
        _stream_id = _token_state.start(model=self.config.model)

        def _counting_on_token(token: str) -> None:
            _token_state.increment(_stream_id)
            on_token(token)

        sem = get_llm_sem()
        sem_label = f"{self.config.provider}/{self.config.model[:20]}"
        t0 = time.monotonic()
        try:
            with sem.acquire(label=sem_label):
                raw_response = impl(
                    messages, system_final, api_tools, _counting_on_token, **extra_kwargs
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
            raise self._upgrade_error(e)
        finally:
            _token_state.stop(_stream_id)

    # ── 错误语义升级 ──────────────────────────────────────────────────────────

    def _upgrade_error(self, exc: Exception) -> Exception:
        """
        对 _wrap_error() 产出的 LLMProviderError 做第二层语义升级。

        分工说明：
          _wrap_error()（各 provider 实现）：SDK 原生异常 → LLM 层统一异常
            不同 SDK 的异常类型完全不同，必须各自翻译，无法合并。

          _upgrade_error()（此处，所有 provider 共用）：LLMProviderError → 更具体的子类
            升级逻辑只依赖消息文本，与 provider 无关，统一放在 Mixin 避免重复。

        目前支持的升级：
          LLMProviderError（含 context window 关键词）→ LLMContextWindowError

        非 LLMProviderError 的异常（如 LLMRateLimitError、LLMTimeoutError）
        直接原样返回，不做升级。
        """
        if isinstance(exc, LLMProviderError) and not isinstance(exc, LLMContextWindowError):
            if _is_context_window_error(str(exc)):
                return LLMContextWindowError(str(exc))
        return exc

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
