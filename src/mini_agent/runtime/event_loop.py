"""runtime/event_loop.py — Phase 8 Sprint 8-2：持续运行的 `AgentRuntime` 外壳。

见 `next_doc/refactor_plan/09-phase8-runtime-convergence-sprint-plan.md`
Sprint 8-2 任务表第一项："支持常驻运行：`while running: run_once(...)`，
加上基本的异常处理和优雅退出"。

止损范围声明（与 Sprint 8-1 一致的保守风格）：
  - 本模块**只负责"反复调用 `run_once()`"这层外壳**，不决定"下一个
    Goal 从哪里来"——`goal_spec_provider` 是调用方注入的
    `Callable[[], Optional[GoalSpec]]`：返回 `None` 表示"这一轮没有
    可执行的 Goal"，本轮跳过（sleep 后重试），不是错误。
  - 一次 `run_once()` 内部异常（`GoalRunner`/Adapter 抛出的任何异常）
    只终止当前这一轮，不终止整个 `run_forever()` 循环——常驻服务的
    基本要求是"一次任务失败不拖垮整个进程"，与 `cli/daemon.py`/
    `evolution/autonomous_loop.py` 里"单个环节异常整体吞掉、只记日志"
    的既有风格一致。
  - 优雅退出支持两种方式：`threading.Event`（`stop_event.set()`，供
    调用方从另一个线程发起停止）与 `KeyboardInterrupt`（本地调试时
    Ctrl-C）。两者都会让 `run_forever()` 正常返回，不抛异常。
  - **不接管任何旧 Scheduler 的触发逻辑**——Sprint 8-2 任务表第二项
    （"选择一个 Scheduler 接入"）留在
    `09-phase8-runtime-convergence-sprint-plan.md` 的"Sprint 8-2
    执行记录"里单独说明评估结论（本次评估发现现有六个候选
    Daemon/AutonomousLoop/Cron/UnifiedTaskScheduler/ObjectiveExecutor/
    ResourceArbiter 均存在与"简单转发一个 Goal 给 AgentRuntime"不兼容
    的具体障碍，按止损条件留痕暂缓，见该文档"变更记录"）。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, TYPE_CHECKING

from mini_agent.runtime.runtime import AgentRuntime, AgentRuntimeResult

if TYPE_CHECKING:
    from mini_agent.goal_mode.spec import GoalSpec

_logger = logging.getLogger("mini_agent.core.trace")

# `goal_spec_provider()` 返回 None 时，本轮跳过前的等待时间；返回一个
# GoalSpec 并跑完一轮后，不额外等待，立刻检查是否还有下一个 Goal——
# 避免"明明有活干，还傻等一个 poll_interval"这种不必要的延迟。
DEFAULT_POLL_INTERVAL_SECONDS = 5.0


@dataclass
class RuntimeEventLoopStats:
    """`run_forever()` 结束后可读的累计统计，供调用方日志/测试断言使用。"""

    iterations_run: int = 0          # 成功拿到 goal_spec 并跑完一次 run_once() 的次数
    iterations_skipped: int = 0      # goal_spec_provider() 返回 None 的次数
    iterations_failed: int = 0       # run_once() 内部抛异常的次数
    last_result: Optional[AgentRuntimeResult] = None
    last_error: Optional[str] = None
    stop_reason: str = ""            # "stop_event" / "keyboard_interrupt" / "max_iterations"
    results: list = field(default_factory=list)  # 仅测试/短期调试场景使用，正式长跑不应无限增长


class RuntimeEventLoop:
    """把 `AgentRuntime.run_once()` 包成一个可以常驻运行的循环。

    不持有 `AgentRuntime` 之外的任何跨轮次业务状态——"下一个 Goal 是
    什么"完全由 `goal_spec_provider` 决定，本类只管"什么时候该跑下一轮、
    异常了怎么办、怎么优雅停下来"。
    """

    def __init__(self, runtime: AgentRuntime, *, poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
        self._runtime = runtime
        self._poll_interval = poll_interval_seconds

    def run_forever(
        self,
        goal_spec_provider: Callable[[], Optional["GoalSpec"]],
        *,
        stop_event: Optional[threading.Event] = None,
        max_iterations: Optional[int] = None,
        keep_results: bool = False,
    ) -> RuntimeEventLoopStats:
        """`while running: run_once(...)`。

        `max_iterations`：可选的总轮次上限（含跳过的轮次），仅用于测试/
        一次性批处理场景不无限循环；生产常驻场景不传（`None`），只靠
        `stop_event`/Ctrl-C 停止。
        `keep_results`：默认 False——正式长跑场景不应该把每一轮的完整
        `AgentRuntimeResult` 都攒在内存里（可能无限增长）；测试场景可以
        显式打开以便断言。
        """
        stats = RuntimeEventLoopStats()
        iteration = 0
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    stats.stop_reason = "stop_event"
                    break
                if max_iterations is not None and iteration >= max_iterations:
                    stats.stop_reason = "max_iterations"
                    break
                iteration += 1

                try:
                    goal_spec = goal_spec_provider()
                except Exception as exc:  # noqa: BLE001 — provider 本身异常不能拖垮循环
                    from mini_agent.errors import log_exception

                    log_exception(exc, where="mini_agent.runtime.event_loop.RuntimeEventLoop.run_forever.provider")
                    stats.iterations_failed += 1
                    stats.last_error = str(exc)
                    self._sleep(stop_event)
                    continue

                if goal_spec is None:
                    stats.iterations_skipped += 1
                    self._sleep(stop_event)
                    continue

                try:
                    result = self._runtime.run_once(goal_spec)
                except Exception as exc:  # noqa: BLE001 — 单轮失败不终止常驻循环
                    from mini_agent.errors import log_exception

                    log_exception(exc, where="mini_agent.runtime.event_loop.RuntimeEventLoop.run_forever.run_once")
                    stats.iterations_failed += 1
                    stats.last_error = str(exc)
                    continue

                stats.iterations_run += 1
                stats.last_result = result
                if keep_results:
                    stats.results.append(result)
                # 跑完一轮就立刻检查下一轮是否还有活干，不额外 sleep。
        except KeyboardInterrupt:
            stats.stop_reason = "keyboard_interrupt"
            _logger.info("RuntimeEventLoop.run_forever: KeyboardInterrupt，优雅退出")

        return stats

    def _sleep(self, stop_event: Optional[threading.Event]) -> None:
        """等待 `poll_interval` 秒，但 `stop_event` 被设置时立刻醒来——
        避免"已经调用了 stop_event.set()，还要傻等满一个 poll_interval
        才能真正退出"。"""
        if stop_event is not None:
            stop_event.wait(timeout=self._poll_interval)
        else:
            time.sleep(self._poll_interval)


__all__ = ["RuntimeEventLoop", "RuntimeEventLoopStats", "DEFAULT_POLL_INTERVAL_SECONDS"]
