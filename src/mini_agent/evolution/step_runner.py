"""
evolution/step_runner.py — 巩固循环子步骤"限时执行"包装器
（next_doc/wiki_next_phase_improvement_plan.md 第 3 节）

现状问题：`evolution/consolidation.py::run_consolidation()` 与它内部调用的
`perception/library_index.py::LibraryIndex.consolidate()` 各自把子步骤包在
`try/except` 里——异常隔离已经做了，但**耗时没有隔离**：任何一步如果卡在一次
很慢的 `llm_call` 上，后面所有步骤都要等它跑完，整轮巩固的总耗时没有上界。

本模块只解决"耗时隔离"这一件事，不改变现有"失败静默降级、下一轮重试"的
既定风格：`run_step()` 把"抛异常"和"超时"统一处理成同一种结果（跳过、
记录、留给下一轮巩固循环自然重跑），调用方不需要区分这两种失败原因。

实现说明：用线程 + 轮询实现超时，不用 `signal.alarm`（避免和已有的子进程/
子 agent 执行逻辑冲突，且 `signal.alarm` 在非主线程里不可用）。超时后原
线程不会被强杀（Python 没有安全的线程强杀机制）——它会在后台继续跑完，
但主流程不再等待，其返回结果被丢弃。这意味着：
  1. 超时的副作用（比如已经调用出去的 LLM 请求）不会被取消，只是结果不被采用；
  2. 调用方传入的 `fn` 如果有"写入磁盘"之类的副作用，需要自行保证幂等/
     原子写（项目里现有的 wiki writer/dedup 等模块本身已经是这个风格）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, Optional, TypeVar

T = TypeVar("T")

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"

# 轮询间隔：不需要很细的粒度，巩固循环本身就是分钟级的操作，0.5s 足够。
_POLL_INTERVAL_SECONDS = 0.5


@dataclass
class StepResult:
    name: str
    status: str  # "ok" | "error" | "timeout"
    elapsed_seconds: float
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "error": self.error,
        }


class _StepThread(threading.Thread, Generic[T]):
    def __init__(self, fn: Callable[[], T]) -> None:
        super().__init__(daemon=True)
        self._fn = fn
        self.result: Optional[T] = None
        self.exc: Optional[BaseException] = None
        self._done = threading.Event()

    def run(self) -> None:
        try:
            self.result = self._fn()
        except BaseException as exc:  # noqa: BLE001 - 需要把任意异常都带回主线程
            self.exc = exc
        finally:
            self._done.set()

    def wait_done(self, timeout: float) -> bool:
        return self._done.wait(timeout)


def run_step(
    name: str,
    fn: Callable[[], T],
    *,
    timeout_seconds: float,
    default: Optional[T] = None,
) -> tuple[Optional[T], StepResult]:
    """执行一个巩固子步骤，超时或异常都返回 `default`（默认 None）。

    返回 `(结果, StepResult)`。调用方按现有风格处理结果为 None/默认值的情况
    （通常就是"当作本轮没跑这一步，下一轮自然会重试"，不需要额外分支）。
    """
    start = time.monotonic()
    worker: _StepThread[T] = _StepThread(fn)
    worker.start()

    remaining = timeout_seconds
    finished = False
    while remaining > 0:
        step_wait = min(_POLL_INTERVAL_SECONDS, remaining)
        if worker.wait_done(step_wait):
            finished = True
            break
        remaining -= step_wait

    elapsed = time.monotonic() - start

    if not finished:
        return default, StepResult(
            name=name, status=STATUS_TIMEOUT, elapsed_seconds=elapsed,
            error=f"exceeded timeout_seconds={timeout_seconds}",
        )

    if worker.exc is not None:
        return default, StepResult(
            name=name, status=STATUS_ERROR, elapsed_seconds=elapsed,
            error=str(worker.exc),
        )

    return worker.result, StepResult(name=name, status=STATUS_OK, elapsed_seconds=elapsed)


# 巩固循环各子步骤的默认超时预算（次_next_phase_improvement_plan.md 第 3.2 节表格）。
DEFAULT_STEP_TIMEOUTS: dict[str, float] = {
    "prune_candidates": 10.0,
    "capability_map": 10.0,
    "scope_promotion": 10.0,
    "entity_summary_rewrite": 60.0,
    "entity_consolidate": 30.0,
    "wiki_mirror": 45.0,
    "wiki_index_rebuild": 20.0,
    "topics_generation": 90.0,
    "world_model_pending": 45.0,
    "decision_consolidation": 45.0,
    "outcome_tracking": 15.0,
    "affordance_calibration": 15.0,
}


__all__ = [
    "StepResult",
    "run_step",
    "DEFAULT_STEP_TIMEOUTS",
    "STATUS_OK",
    "STATUS_ERROR",
    "STATUS_TIMEOUT",
]
