"""
blocking_guard.py — HTTP 路由里调用同步阻塞逻辑（典型如 LLM 调用）的通用防护。

背景：FastAPI 的 `async def` 路由跟其他所有协程共享同一个事件循环线程。如果路由内部
直接同步调用一个可能耗时很久的函数（比如 `LLMHelper.ask()`，单次最坏情况
`超时 x 重试次数 + 退避等待` 能到几十秒到几分钟），事件循环会被整个卡住——不是"线程池
不够用"，而是同一进程里其他所有请求（包括跟这次调用毫无关系的看板轮询、health check）
全部要排队等它。详见 next_doc/http_server_blocking_call_guard_plan.md。

用法：

    from mini_agent.utils.blocking_guard import run_blocking

    async def some_route(request: Request):
        ...
        result = await run_blocking(
            ga.some_business_fn, arg1, arg2,
            kw=1,
            where="growth_align",       # 用于熔断分组 + 日志定位
            timeout=45.0,               # 可选，不传则用 cfg/默认值
            fallback=None,              # 可选：超时/熔断时返回什么，不传则抛 HTTPException(504)
        )

约定：
- `fn` 必须是同步函数（`def`，不是 `async def`）。异步函数直接 `await` 就好，不需要这个。
- `where` 建议用稳定的短字符串（路由名/业务动作名），同一个 `where` 共享一套熔断计数。
- 超时不会真正中断线程池里那次调用（Python 线程没有强制中断机制），只是不再让 HTTP
  响应和事件循环被它拖住；调用方如果关心"孤儿线程会不会越堆越多"，应该在 `fn` 内部自己
  的 LLM/网络调用上也设置合理的 socket 级超时（`LLMConfig.timeout` 已经有）。
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, TypeVar

from fastapi import HTTPException

T = TypeVar("T")

DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 120.0

# 哨兵：区分"调用方没传 fallback"和"调用方显式传了 fallback=None"
_UNSET = object()


@dataclass
class _CircuitState:
    """单个 `where` 分组的熔断状态。"""
    consecutive_failures: int = 0
    opened_at: float = 0.0  # 0 表示熔断关闭（正常）

    def is_open(self, now: float, cooldown: float) -> bool:
        if self.opened_at <= 0:
            return False
        if now - self.opened_at >= cooldown:
            # 冷却结束，放行一次探测；不在这里清零，成功/失败回调里再清零或重新打开
            return False
        return True


class _BlockingCallHealth:
    """跨调用共享的轻量熔断器，按 `where` 分组，进程内内存态、无持久化。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: Dict[str, _CircuitState] = {}

    def _get(self, where: str) -> _CircuitState:
        state = self._states.get(where)
        if state is None:
            state = _CircuitState()
            self._states[where] = state
        return state

    def should_short_circuit(self, where: str, cooldown: float) -> bool:
        with self._lock:
            state = self._get(where)
            return state.is_open(time.monotonic(), cooldown)

    def record_success(self, where: str) -> None:
        with self._lock:
            state = self._get(where)
            state.consecutive_failures = 0
            state.opened_at = 0.0

    def record_failure(self, where: str, threshold: int) -> None:
        with self._lock:
            state = self._get(where)
            state.consecutive_failures += 1
            if state.consecutive_failures >= threshold and state.opened_at <= 0:
                state.opened_at = time.monotonic()

    def snapshot(self) -> Dict[str, dict]:
        """只读快照，供 /v1/self/... 之类的观测端点展示，不暴露内部锁对象。"""
        with self._lock:
            return {
                where: {
                    "consecutive_failures": s.consecutive_failures,
                    "circuit_open": s.opened_at > 0,
                }
                for where, s in self._states.items()
            }

    def reset(self, where: str = None) -> None:
        """测试/运维用：清空某个分组（或全部）的熔断状态。"""
        with self._lock:
            if where is None:
                self._states.clear()
            else:
                self._states.pop(where, None)


# 进程内单例：所有调用点共享同一套熔断计数
_health = _BlockingCallHealth()


def get_blocking_call_health_snapshot() -> Dict[str, dict]:
    """给观测端点用：当前各分组的熔断状态。"""
    return _health.snapshot()


def _reset_blocking_call_health_for_tests(where: str = None) -> None:
    """仅供单元测试调用，避免测试之间的熔断状态互相污染。"""
    _health.reset(where)


async def run_blocking(
    fn: Callable[..., T],
    *args: Any,
    where: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    fallback: Any = _UNSET,
    **kwargs: Any,
) -> T:
    """在线程池中运行同步函数 `fn`，带硬超时 + 轻量熔断，避免拖死事件循环。

    - 正常返回：跟直接调用 `fn(*args, **kwargs)` 语义一致。
    - 超时：记一次失败，`fallback` 传了就返回 `fallback`，没传就抛
      `HTTPException(504)`。
    - 熔断打开期间（同一 `where` 连续失败 >= `failure_threshold` 次，且未过
      `cooldown_seconds`）：直接短路，不再起线程调用，同样按"传了 fallback 就返回，
      没传就 504"处理。
    - `fn` 内部抛出的异常会原样落盘（`log_exception`）后继续往上抛（不吞不掩盖），
      因为不同调用点对"业务异常"的降级语义不一样，不适合在这里统一处理。
    """
    if _health.should_short_circuit(where, cooldown_seconds):
        from mini_agent.errors import log_exception
        log_exception(
            RuntimeError(f"blocking_guard 熔断打开，跳过调用：where={where}"),
            where=f"mini_agent.utils.blocking_guard.run_blocking.circuit_open.{where}",
        )
        if fallback is not _UNSET:
            return fallback
        raise HTTPException(status_code=503, detail=f"操作 '{where}' 暂时不可用（连续失败次数过多，正在冷却）")

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(fn, *args, **kwargs), timeout=timeout,
        )
    except asyncio.TimeoutError:
        _health.record_failure(where, failure_threshold)
        from mini_agent.errors import log_exception
        log_exception(
            TimeoutError(f"blocking_guard 调用超时（{timeout}s）：where={where}"),
            where=f"mini_agent.utils.blocking_guard.run_blocking.timeout.{where}",
        )
        if fallback is not _UNSET:
            return fallback
        raise HTTPException(status_code=504, detail=f"操作 '{where}' 超时（>{timeout}s）")
    except Exception as e:
        _health.record_failure(where, failure_threshold)
        from mini_agent.errors import log_exception
        log_exception(e, where=f"mini_agent.utils.blocking_guard.run_blocking.error.{where}")
        raise
    else:
        _health.record_success(where)
        return result
