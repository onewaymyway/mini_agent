"""
llm/base.py — 统一的 LLM 抽象层

定义所有 provider 必须实现的接口和共享数据结构，
Agent 只依赖这个文件，与任何具体的 SDK 完全解耦。

核心类型：
  LLMConfig     — provider 无关的配置（provider名、model、api_key、参数等）
  ToolCall      — 模型请求调用工具的指令
  LLMUsage      — token 用量统计
  LLMResponse   — 所有 provider 返回的统一响应体
  LLMClient     — 所有 provider 必须实现的抽象基类
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    """模型请求调用的单个工具。"""
    id: str          # 由 provider 分配的唯一 ID，回传 tool_result 时使用
    name: str        # 对应 ToolRegistry 中注册的工具名
    input: dict      # 工具参数（已解析为 dict）


@dataclass
class LLMUsage:
    """Token 用量，用于费用统计和上下文管理。"""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: LLMUsage) -> LLMUsage:
        return LLMUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass
class LLMResponse:
    """
    所有 provider 返回的统一响应体。

    Agent 只与这个类型交互，永远不触碰 SDK 原始对象。
    """
    text: str                          # 文本内容（可能为空，若模型只输出工具调用）
    tool_calls: list[ToolCall]         # 本轮请求的工具调用列表（可能为空）
    usage: LLMUsage                    # token 用量
    stop_reason: str                   # "end_turn" | "tool_use" | "max_tokens" | "stop"
    reasoning: str = ""                # 思维链内容（CoT），仅部分模型支持（如 NVIDIA reasoning 模型）
    refusal: str = ""                  # 安全/合规拒答内容（OpenAI 兼容协议 message.refusal）。
                                        # 非空时说明 output_tokens 消耗在了拒答文本上，而不是
                                        # 普通 content——这是 "output_tokens>0 但 text 为空"
                                        # 最常见的原因之一，务必在日志/上层逻辑里单独处理。
    finish_reason_raw: str = ""        # provider 原始 finish_reason（映射前），用于定位
                                        # "content_filter" 被误判为正常结束（stop/end_turn）
                                        # 等场景——_map_finish() 会把 content_filter 也归并成
                                        # stop，仅看 stop_reason 无法区分内容是否被过滤。
    raw: Any = field(default=None, repr=False)   # 原始 SDK 响应（调试用）

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def is_complete(self) -> bool:
        """没有工具调用，即模型已给出最终文本答复。"""
        return not self.has_tool_calls

    @property
    def is_empty_output(self) -> bool:
        """
        text / reasoning / tool_calls 均为空，但仍可能消耗了 output_tokens
        （常见于安全拒答被丢弃、reasoning token 未解析、或被内容过滤）。
        供上层（重试策略、调试面板）快速判定"疑似内容丢失"场景。
        """
        return not self.text and not self.reasoning and not self.tool_calls and not self.refusal


# ── 工具 Schema 类型 ──────────────────────────────────────────────────────────

@dataclass
class ToolSchema:
    """
    Provider 无关的工具定义。
    各 provider 的 client 负责将其转换为各自 API 要求的格式。
    """
    name: str
    description: str
    input_schema: dict   # JSON Schema object


# ── 流式 token 回调 ───────────────────────────────────────────────────────────

# 流式输出时每个 token 的回调类型（文本 token）
StreamCallback = Callable[[str], None]

# 流式思维链 token 的回调类型（仅支持 CoT 的模型使用，如 NVIDIA reasoning 模型）
ReasoningCallback = Callable[[str], None]


# ── 抽象基类 ──────────────────────────────────────────────────────────────────

class LLMClient(ABC):
    """
    所有 LLM provider 的抽象基类。

    子类只需实现 chat() 和 stream()。
    factory.py 负责根据 provider 名称实例化正确的子类。

    接入新 provider 的步骤：
        1. 在 llm/providers/ 下新建文件，继承 LLMClient
        2. 实现 chat() 和 stream() 两个方法
        3. 在 llm/factory.py 的 _REGISTRY 中注册
    """

    def __init__(self, config: "LLMConfig") -> None:
        self.config = config

    # ── 必须实现 ──────────────────────────────────────────────────────────────

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        system: str,
        tools: list[ToolSchema],
    ) -> LLMResponse:
        """
        发送一轮对话，返回 LLMResponse。

        Args:
            messages:  完整对话历史（OpenAI 格式的 role/content list）
            system:    system prompt 文本
            tools:     本次可用的工具列表
        """
        ...

    @abstractmethod
    def stream(
        self,
        messages: list[dict],
        system: str,
        tools: list[ToolSchema],
        on_token: StreamCallback,
    ) -> LLMResponse:
        """
        流式发送一轮对话。

        每收到一个 token 就调用 on_token(token)，
        流结束后返回完整的 LLMResponse（含完整 text 和 usage）。

        Args:
            messages:  完整对话历史
            system:    system prompt 文本
            tools:     本次可用的工具列表
            on_token:  每个流式 token 的回调
        """
        ...

    # ── 可选覆盖 ──────────────────────────────────────────────────────────────

    @property
    def provider_name(self) -> str:
        """provider 的可读名称，用于日志和错误信息。"""
        return self.__class__.__name__.replace("Provider", "").replace("Client", "")

    def chat_with_retry(
        self,
        messages: list[dict],
        system: str,
        tools: Optional[list[ToolSchema]] = None,
        *,
        max_retries: int = 2,
        retry_delay: float = 5.0,
        retry_policy: Optional["RetryPolicy"] = None,
        on_retry: Optional[Callable[[int, str], None]] = None,
    ) -> LLMResponse:
        """
        带重试的 chat()，供任何调用方复用，无需各自构造 RetryPolicy。

        与裸调用 chat() 的区别：
          - 模型返回空响应（无文本且无工具调用）会自动重试；
          - chat() 抛出异常（网络错误、API 错误等）也会自动重试，
            而不是直接向上传播；
          - 重试预算耗尽后，若最后一次是异常则向上抛出，
            若是"空响应"则原样返回该响应（不抛异常）。

        Args:
            messages, system, tools: 与 chat() 相同。
            max_retries:   最多重试次数（不含首次调用），默认 2。
                           例如长期记忆/用户画像生成等后台任务可传 10。
            retry_delay:   每次重试前的等待秒数，默认 1.0。
            retry_policy:  传入完整的 RetryPolicy 以自定义重试条件，
                           传入时 max_retries/retry_delay 被忽略。
            on_retry:      重试回调 on_retry(attempt, reason)。

        Returns:
            最终的 LLMResponse。
        """
        from .retry import RetryPolicy, EmptyOutputCondition

        policy = retry_policy or RetryPolicy(
            max_retries=max_retries,
            conditions=[EmptyOutputCondition()],
            retry_delay=retry_delay,
            retry_on_exception=True,
        )
        return policy.call_with_retry(
            call_fn=lambda: self.chat(messages, system, tools or []),
            on_retry=on_retry,
        )


    def validate_config(self) -> None:
        """
        在首次调用前验证配置。子类可覆盖以增加校验逻辑。
        默认只检查 api_key 是否存在。
        """
        if self.config.requires_api_key and not self.config.api_key:
            raise LLMConfigError(
                f"{self.provider_name} requires an API key. "
                f"Set the corresponding environment variable or pass api_key= to LLMConfig."
            )

    def format_tools(self, tools: list[ToolSchema]) -> list[dict]:
        """
        将 ToolSchema 列表转换为该 provider API 要求的格式。
        默认实现使用 Anthropic / OpenAI 兼容格式，
        各 provider 可覆盖此方法适配差异。
        """
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"model={self.config.model!r}, "
            f"provider={self.provider_name!r})"
        )


# ── 配置 ──────────────────────────────────────────────────────────────────────

@dataclass
class LLMConfig:
    """
    Provider 无关的 LLM 配置。

    agent.py 和 config.py 只持有这个对象，
    不再直接引用任何 SDK 特定的配置。
    """
    provider: str                  # "anthropic" | "openai" | "ollama" | 自定义名
    model: str                     # 模型 ID，如 "claude-opus-4-5" / "gpt-4o"
    api_key: str = ""              # API key（本地 provider 如 ollama 可留空）
    base_url: Optional[str] = None # 自定义 endpoint，用于代理或本地部署
    max_tokens: int = 8192
    temperature: float = 0.0       # 默认确定性输出（代码场景）
    timeout: int = 120             # 单次请求超时秒数
    extra: dict = field(default_factory=dict)   # provider 专用扩展参数

    # 是否必须提供 api_key（本地 provider 设为 False）
    requires_api_key: bool = True

    # 工具调用模式：True = system prompt 模式（通用兼容），False = SDK 原生 tools 参数
    use_system_tool_call: bool = False

    # system 消息格式：
    #   "system_field" — 使用顶层 system 参数（默认）
    #   "system_role"  — 将 system 注入为 messages[0] role="system"
    system_message_format: str = "system_field"

    @classmethod
    def from_app_config(cls, cfg: Any) -> "LLMConfig":
        """
        从 AppConfig 构建 LLMConfig 的便捷工厂方法。
        优先读取 cfg.llm_provider，其次读取 LLM_PROVIDER 环境变量。
        """
        import os
        provider = (
            getattr(cfg, "llm_provider", None)
            or os.environ.get("LLM_PROVIDER", "anthropic")
        )
        base_url = (
            getattr(cfg, "llm_base_url", None)
            or os.environ.get("LLM_BASE_URL", "")
        ) or None
        use_system_tc = (
            getattr(cfg, "use_system_tool_call", False)
            or os.environ.get("LLM_SYSTEM_TOOL_CALL", "").lower() in ("1", "true", "yes")
        )
        sys_msg_fmt = (
            getattr(cfg, "system_message_format", None)
            or os.environ.get("LLM_SYSTEM_MESSAGE_FORMAT", "system_field")
        )
        return cls(
            provider=provider,
            model=cfg.model,
            api_key=cfg.api_key,
            max_tokens=cfg.max_tokens,
            base_url=base_url,
            requires_api_key=(provider not in ("ollama", "local")),
            use_system_tool_call=use_system_tc,
            system_message_format=sys_msg_fmt,
        )


# ── 异常 ──────────────────────────────────────────────────────────────────────

class LLMError(RuntimeError):
    """所有 LLM 层异常的基类。"""

class LLMConfigError(LLMError):
    """配置错误（缺少 key、不支持的 provider 等）。"""

class LLMProviderError(LLMError):
    """Provider API 返回的错误（HTTP 4xx/5xx、认证失败等）。"""

class LLMTimeoutError(LLMError):
    """请求超时。"""

class LLMRateLimitError(LLMProviderError):
    """触发速率限制（可重试）。"""

class LLMContextWindowError(LLMProviderError):
    """
    上下文窗口超出限制（HTTP 400 ContextWindowExceededError）。

    这是一个确定性错误：相同的历史在相同的模型上重试永远不会成功。
    正确的处理方式是触发 compact（历史压缩），而非重试。

    RetryPolicy.non_retryable_exceptions 默认包含此类型，确保重试循环
    立即退出；调用方（agent._call_llm_with_tools）捕获后触发 auto-compact。
    """

class LLMPermanentError(LLMProviderError):
    """
    持久性 / 不可恢复的 provider 错误（如 HTTP 403 Forbidden、账号被封禁、
    地域 / 权限限制等）。

    这类错误的特征是：在短时间窗口内重试没有意义 —— 相同的 key、相同的
    请求参数重试大概率会得到完全相同的错误，只会浪费时间和 token 预算。

    RetryPolicy.non_retryable_exceptions 默认包含此类型，确保单个 provider
    entry 内不做任何重试等待，立即向上传播；LLMClientPool.DEFAULT_FALLBACK_ON
    同样包含 "LLMPermanentError"，确保 fallback chain 立即切换到下一条配置，
    而不是先在当前配置上重试若干次再切换。
    """
