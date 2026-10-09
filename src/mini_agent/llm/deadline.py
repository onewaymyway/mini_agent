"""
llm/deadline.py — 后台轻量 LLM 调用的总 deadline（tick 只派发、不执行 阶段 4）

背景（next_doc/tick_dispatch_only_execution_model_plan.md §6.6）：
  单次 ``LLMHelper.ask()`` 最坏阻塞没有上限：默认单次请求超时 120s、``max_retries=3``、
  ``FixedBackoff(10s)``，最坏约 4×120+3×10≈510s；``LLMClientPool`` 有 fallback / 多轮时
  还要再乘。declare_paths、goal_relevance、novelty_judge 这类后台判定调用本质只是辅助，
  不该为此长时间占用线程与 LLM 并发槽位。

机制（thread-local，调用栈上任意一层都能读到，无需层层改签名）：
  ``deadline_scope(total_seconds=..., request_timeout=...)``
      进入作用域时记录一个**绝对**墙钟截止时间（monotonic）与单次请求上限；
      嵌套时取更紧的一方，内层不能放宽外层。
  ``remaining()``                 距截止还有多少秒（无 deadline 返回 None）
  ``check_deadline(where)``       已超出则抛 ``LLMTimeoutError``
  ``effective_timeout(default)``  provider 在发请求时用：min(config.timeout, 剩余, 单次上限)

谁来执行约束：
  - ``RetryPolicy.call_with_retry``：每次重试/退避/断网等待前检查，剩余不够就不再重试；
  - ``LLMClientPool.call_with_pool``：每个 entry / 每轮之间检查，且 round_wait 不超过剩余；
  - ``ProviderMixin._traced_*``：限速等待、并发槽位排队都受 deadline 约束；
  - 各 provider：把 ``effective_timeout()`` 传给 SDK / httpx / urllib 的 timeout 参数。

注意：Python 无法强杀已阻塞在 socket 上的线程，deadline 靠“SDK 层超时 + 重试层预算 +
排队层上限”三重保证；deadline 作用域之外的调用（Agent 主对话、step 执行）完全不受影响。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from .base import LLMTimeoutError

_local = threading.local()

# 即使 deadline 只剩一点点，也给 SDK 一个最小可用的请求超时，避免传 0/负数
# 被 SDK 当成“无超时”。
_MIN_REQUEST_TIMEOUT = 1.0


def _get() -> tuple[Optional[float], Optional[float]]:
    return (
        getattr(_local, "deadline_at", None),
        getattr(_local, "request_cap", None),
    )


@contextmanager
def deadline_scope(
    total_seconds: Optional[float] = None,
    request_timeout: Optional[float] = None,
) -> Iterator[None]:
    """设置当前线程的 LLM 调用预算。两个参数都为 None/<=0 时是空操作。"""
    prev_deadline, prev_cap = _get()
    new_deadline = prev_deadline
    new_cap = prev_cap
    if total_seconds is not None and total_seconds > 0:
        candidate = time.monotonic() + float(total_seconds)
        new_deadline = candidate if prev_deadline is None else min(prev_deadline, candidate)
    if request_timeout is not None and request_timeout > 0:
        candidate_cap = float(request_timeout)
        new_cap = candidate_cap if prev_cap is None else min(prev_cap, candidate_cap)
    _local.deadline_at = new_deadline
    _local.request_cap = new_cap
    try:
        yield
    finally:
        _local.deadline_at = prev_deadline
        _local.request_cap = prev_cap


def has_deadline() -> bool:
    return _get()[0] is not None


def remaining() -> Optional[float]:
    """距总 deadline 的剩余秒数（可为负）；没有 deadline 返回 None。"""
    deadline_at, _ = _get()
    if deadline_at is None:
        return None
    return deadline_at - time.monotonic()


def expired() -> bool:
    rem = remaining()
    return rem is not None and rem <= 0.0


def check_deadline(where: str = "") -> None:
    """deadline 已用尽则抛 LLMTimeoutError（不会被重试层吞回去重试）。"""
    rem = remaining()
    if rem is not None and rem <= 0.0:
        raise LLMTimeoutError(
            f"LLM 后台调用已超过总 deadline{('（' + where + '）') if where else ''}"
        )


def effective_timeout(default: Optional[float]) -> Optional[float]:
    """provider 发起单次请求时使用：取 default / 单次上限 / 剩余时间 三者的最小值。

    没有任何 deadline 约束时原样返回 default，保证旧行为不变。
    """
    deadline_at, cap = _get()
    if deadline_at is None and cap is None:
        return default
    candidates = [v for v in (default, cap) if v is not None and v > 0]
    if deadline_at is not None:
        candidates.append(max(_MIN_REQUEST_TIMEOUT, deadline_at - time.monotonic()))
    if not candidates:
        return default
    return max(_MIN_REQUEST_TIMEOUT, min(candidates))


def clamp_wait(seconds: float) -> float:
    """把一次 sleep/等待时长限制在剩余 deadline 之内（无 deadline 原样返回）。"""
    rem = remaining()
    if rem is None:
        return seconds
    return max(0.0, min(seconds, rem))
