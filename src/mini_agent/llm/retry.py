"""
llm/retry.py — LLM 调用重试条件框架

提供可扩展的重试条件机制。每个条件是一个独立的检查器，
只要任意一个条件判定"需要重试"，就触发重试。

内置条件：
  EmptyOutputCondition   — 模型没有任何文本输出且没有工具调用
  EmptyTextCondition     — 模型文本为空（即使有工具调用也重试）

扩展新条件：
  继承 RetryCondition，实现 should_retry(response) 方法即可，
  然后加入 RetryPolicy.conditions 列表。

示例：
  policy = RetryPolicy(
      max_retries=3,
      conditions=[
          EmptyOutputCondition(),       # 空输出重试
          MyCustomCondition(threshold=0.5),  # 自定义条件
      ],
  )
  response = policy.call_with_retry(call_fn, on_retry=on_retry_fn)
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

from .base import LLMResponse, LLMConfigError

logger = logging.getLogger(__name__)


# ── 重试条件抽象基类 ──────────────────────────────────────────────────────────

class RetryCondition(ABC):
    """
    单个重试条件的抽象基类。

    每个条件只关注一个判断维度，保持职责单一。
    条件之间是 OR 关系：任意一个触发即重试。
    """

    @abstractmethod
    def should_retry(self, response: LLMResponse) -> bool:
        """
        判断是否需要重试。

        Args:
            response: LLM 返回的响应对象

        Returns:
            True  — 触发重试
            False — 本条件不触发重试
        """
        ...

    @property
    def name(self) -> str:
        """条件的可读名称，用于日志输出。"""
        return self.__class__.__name__

    @property
    def reason(self) -> str:
        """重试原因描述，打印到日志中。"""
        return f"[{self.name}] triggered retry"


# ── 内置重试条件 ──────────────────────────────────────────────────────────────

class EmptyOutputCondition(RetryCondition):
    """
    【内置】空输出条件：模型既没有文本输出，也没有工具调用。

    这是最常见的"无效响应"场景：模型返回了一个空响应，
    什么内容都没有，视为调用失败，应该重试。
    """

    def should_retry(self, response: LLMResponse) -> bool:
        has_text = bool(response.text and response.text.strip())
        has_tool_calls = bool(response.tool_calls)
        return not has_text and not has_tool_calls

    @property
    def reason(self) -> str:
        return "[EmptyOutputCondition] 模型无文本输出且无工具调用，视为空响应，触发重试"


class EmptyTextCondition(RetryCondition):
    """
    【内置】空文本条件：模型文本为空（即使有工具调用也重试）。

    比 EmptyOutputCondition 更严格：只要 text 字段为空就重试，
    适用于要求模型必须附带解释文本的场景。
    """

    def should_retry(self, response: LLMResponse) -> bool:
        return not bool(response.text and response.text.strip())

    @property
    def reason(self) -> str:
        return "[EmptyTextCondition] 模型文本输出为空，触发重试"


class StopReasonCondition(RetryCondition):
    """
    【内置】特定停止原因条件：当 stop_reason 命中指定值时重试。

    示例：重试 stop_reason == "max_tokens" 的截断响应。
        condition = StopReasonCondition(stop_reasons={"max_tokens"})
    """

    def __init__(self, stop_reasons: set[str]) -> None:
        self.stop_reasons = stop_reasons

    def should_retry(self, response: LLMResponse) -> bool:
        return response.stop_reason in self.stop_reasons

    @property
    def reason(self) -> str:
        return f"[StopReasonCondition] stop_reason={self.stop_reasons!r}，触发重试"


# ── 重试策略 ──────────────────────────────────────────────────────────────────

@dataclass
class RetryPolicy:
    """
    重试策略：持有一组重试条件，执行带重试的 LLM 调用。

    Attributes:
        max_retries:  最多重试次数（不含首次调用），0 = 不重试
        conditions:   重试条件列表，任意一个触发即重试（OR 关系）
        retry_delay:  每次重试前的等待秒数（0 = 立即重试）
        retry_on_exception:  call_fn() 抛出异常时是否重试（见下）
        non_retryable_exceptions: 即使 retry_on_exception=True，
                       命中这些异常类型时也直接抛出，不计入重试。
                       默认包含 LLMConfigError —— 配置错误（如缺少 api_key）
                       是确定性的，重试不会让它变成功，反而会白白
                       浪费 max_retries 预算并等待 retry_delay。
    """

    max_retries: int = 2
    conditions: list[RetryCondition] = field(
        default_factory=lambda: [EmptyOutputCondition()]
    )
    retry_delay: float = 10.0
    # 为 True 时，call_fn() 抛出异常也会触发重试（与 max_retries 共用预算），
    # 而不是直接向上传播。用于"模型调用偶发网络/超时/API错误，希望自动重试"的场景——
    # 这正是 LLM 调用最常见的失败模式，因此默认开启（见 default_retry_policy）。
    retry_on_exception: bool = False
    non_retryable_exceptions: tuple = field(
        default_factory=lambda: (LLMConfigError,)
    )

    def call_with_retry(
        self,
        call_fn: Callable[[], LLMResponse],
        on_retry: Optional[Callable[[int, str], None]] = None,
    ) -> LLMResponse:
        """
        执行 call_fn，失败时按策略重试。

        Args:
            call_fn:   无参数的调用函数，每次调用返回 LLMResponse
            on_retry:  重试回调，签名为 on_retry(attempt: int, reason: str)
                       可用于打印警告、更新 UI 等，不传则静默重试

        Returns:
            最终的 LLMResponse（首次成功或最后一次重试的结果）

        Note:
            - "响应质量"重试（conditions）和"异常"重试（retry_on_exception）
              共用同一个 max_retries 预算。
            - 若达到 max_retries 上限仍触发质量条件，返回最后一次的响应而非抛出异常，
              调用方可自行决定如何处理降级响应。
            - retry_on_exception=False 时，call_fn 抛出异常会直接向上
              传播，不做重试（兼容旧行为）。retry_on_exception=True（默认）时，
              异常（网络错误、超时 LLMTimeoutError、5xx 等 API 错误）也会计入
              max_retries 预算后重试；预算耗尽后异常会向上抛出。
            - non_retryable_exceptions 命中时，无论 retry_on_exception 取值，
              都立即向上抛出（配置类错误重试无意义）。
        """
        attempt = 0
        while True:
            try:
                response = call_fn()
            except self.non_retryable_exceptions:
                raise
            except Exception as e:
                if not self.retry_on_exception or attempt >= self.max_retries:
                    raise
                attempt += 1
                reason = f"[Exception] {type(e).__name__}: {e}"
                logger.warning("LLM retry %d/%d — %s", attempt, self.max_retries, reason)
                if on_retry:
                    on_retry(attempt, reason)
                if self.retry_delay > 0:
                    time.sleep(self.retry_delay)
                continue

            triggered = self._check_conditions(response)
            if triggered is None or attempt >= self.max_retries:
                return response

            attempt += 1
            reason = triggered.reason
            logger.warning("LLM retry %d/%d — %s", attempt, self.max_retries, reason)
            if on_retry:
                on_retry(attempt, reason)

            if self.retry_delay > 0:
                time.sleep(self.retry_delay)

    def _check_conditions(
        self, response: LLMResponse
    ) -> Optional[RetryCondition]:
        """
        检查所有条件，返回第一个触发的条件；
        所有条件均未触发时返回 None。
        """
        for condition in self.conditions:
            if condition.should_retry(response):
                return condition
        return None

    def add_condition(self, condition: RetryCondition) -> "RetryPolicy":
        """链式添加条件，返回自身便于链式调用。"""
        self.conditions.append(condition)
        return self


# ── 便捷工厂 ──────────────────────────────────────────────────────────────────

def default_retry_policy(
    max_retries: int = 2,
    retry_delay: float = 10.0,
    retry_on_exception: bool = True,
) -> RetryPolicy:
    """
    默认重试策略：包含 EmptyOutputCondition（空输出即重试），
    且默认 retry_on_exception=True —— LLM 调用抛出的异常
    （网络错误、超时 LLMTimeoutError、5xx 等可恢复的 API 错误）
    同样会触发重试，而不是直接中断当前 turn。

    LLMConfigError（如缺少 api_key）属于 non_retryable_exceptions，
    即使 retry_on_exception=True 也不会重试，会立即向上抛出。

    Args:
        max_retries:  最多重试次数，默认 2
        retry_delay:  重试间隔秒数，默认 0（立即重试）
        retry_on_exception: 异常是否计入重试，默认 True。
                       传 False 可恢复"只重试空响应"的旧行为。
    """
    return RetryPolicy(
        max_retries=max_retries,
        conditions=[EmptyOutputCondition()],
        retry_delay=retry_delay,
        retry_on_exception=retry_on_exception,
    )


def no_retry_policy() -> RetryPolicy:
    """不重试策略（max_retries=0），用于禁用重试。"""
    return RetryPolicy(max_retries=0, conditions=[])


def background_retry_policy(max_retries: int = 10, retry_delay: float = 10.0) -> RetryPolicy:
    """
    后台任务重试策略：用于长期记忆/用户画像生成等"调用失败应自动重试"的场景。

    与 default_retry_policy 行为一致（均默认 retry_on_exception=True），
    区别仅在于默认的重试次数更多、间隔更长，适合非交互的后台任务。

    Args:
        max_retries:  最多重试次数，默认 10
        retry_delay:  重试间隔秒数，默认 1.0
    """
    return RetryPolicy(
        max_retries=max_retries,
        conditions=[EmptyOutputCondition()],
        retry_delay=retry_delay,
        retry_on_exception=True,
    )