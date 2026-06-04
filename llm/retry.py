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

from llm.base import LLMResponse

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
    """

    max_retries: int = 2
    conditions: list[RetryCondition] = field(
        default_factory=lambda: [EmptyOutputCondition()]
    )
    retry_delay: float = 0.0

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
            - 即使达到 max_retries 上限，也返回最后一次的响应而非抛出异常，
              调用方可自行决定如何处理降级响应。
            - 如果 call_fn 本身抛出异常，异常会直接向上传播，不做重试。
        """
        response = call_fn()

        for attempt in range(1, self.max_retries + 1):
            triggered = self._check_conditions(response)
            if triggered is None:
                break  # 所有条件均未触发，返回当前响应

            # 触发重试
            reason = triggered.reason
            logger.warning("LLM retry %d/%d — %s", attempt, self.max_retries, reason)
            if on_retry:
                on_retry(attempt, reason)

            if self.retry_delay > 0:
                time.sleep(self.retry_delay)

            response = call_fn()

        return response

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

def default_retry_policy(max_retries: int = 2, retry_delay: float = 0.0) -> RetryPolicy:
    """
    默认重试策略：仅包含 EmptyOutputCondition（空输出即重试）。

    Args:
        max_retries:  最多重试次数，默认 2
        retry_delay:  重试间隔秒数，默认 0（立即重试）
    """
    return RetryPolicy(
        max_retries=max_retries,
        conditions=[EmptyOutputCondition()],
        retry_delay=retry_delay,
    )


def no_retry_policy() -> RetryPolicy:
    """不重试策略（max_retries=0），用于禁用重试。"""
    return RetryPolicy(max_retries=0, conditions=[])
