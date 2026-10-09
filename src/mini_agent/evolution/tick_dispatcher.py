"""
evolution/tick_dispatcher.py — tick 慢操作卸载的统一后台派发器

[next_doc/tick_dispatch_only_execution_model_plan.md 阶段一]

背景
----
`SchedulerHeartbeat` 的 `tick()` 约定只做"决策 + 提交"，但实际有多处调用点在 tick 线程
（且持有 `sched_lock`）里同步等待 LLM（路径声明、Objective 拆解、cron local_handler 等），
上游一旦变慢，整个调度心跳就被拖死（见该计划文档 §1 的三份卡死栈快照）。

本模块提供"tick 只派发、不执行"所需的最小设施：

    dispatcher.dispatch(key, fn, timeout=..., on_done=...)   # 立即返回，不等待 fn

语义
----
- **立即返回**：`dispatch()` 不阻塞。同一个 key 已在跑、或并发数已达上限时返回 False
  （语义同 `CronJobRunner.submit()` 返回 False：这次没派发成功，调用方自行决定重试/记账）。
- **有界**：同时在跑的任务数不超过 `max_workers`。不使用 `ThreadPoolExecutor`——它的
  worker 一旦被卡死的任务占住就永久少一个；这里用显式 daemon 线程 + 记账，回收卡死任务时
  直接释放记账名额（卡死线程成为孤儿线程继续在后台跑，Python 无法强杀线程）。
- **可回收**：每次派发生成唯一 token；`reap_stale()` 对超过 `timeout + grace` 仍未返回的任务
  回收记账，并以 `status="timeout"` 回调 `on_done`。迟到线程收尾时发现 token 已失效，
  会跳过释放与回调，不与回收互相踩踏（与 `CronJobRunner.reap_stale_jobs` 同一思路）。
- **不吞业务结果**：`fn` 的返回值、异常、耗时都通过 `DispatchResult` 传给 `on_done`。
  `on_done` 在 worker 线程里被调用，**不应直接修改 tick 线程独占的状态**——需要回写
  调度状态的调用方应把结果放进线程安全的队列，由下一轮 tick 在 tick 线程里消费
  （`CronScheduler.drain_async_handler_results()` 就是这么做的）。
- `on_done` 抛出的异常被吞掉并记录日志，不影响记账释放。

本类不依赖 evolution 里的任何其它模块，可以被 objective_executor / cron_scheduler 等共用。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# 派发结果状态
STATUS_OK = "ok"
STATUS_EXCEPTION = "exception"
STATUS_TIMEOUT = "timeout"   # 被 reap_stale() 回收


@dataclass
class DispatchResult:
    """一次派发的最终结果，传给 `on_done` 回调。"""

    key: str
    label: str
    status: str                       # ok | exception | timeout
    value: Any = None                 # fn 的返回值（status == ok 时有效）
    error: Optional[BaseException] = None
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    @property
    def error_repr(self) -> str:
        if self.error is None:
            return ""
        return f"{type(self.error).__name__}: {self.error}"


@dataclass
class _Running:
    token: str
    key: str
    label: str
    started_at: float
    deadline: float                   # started_at + timeout（不含 grace）
    on_done: Optional[Callable[[DispatchResult], None]]
    thread: Optional[threading.Thread] = None


class TickDispatcher:
    """有界、可去重、可超时回收的后台派发器。线程安全。"""

    def __init__(
        self,
        *,
        max_workers: int = 4,
        default_timeout_seconds: float = 300.0,
        grace_seconds: float = 30.0,
        name_prefix: str = "tick-dispatch",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._max_workers = max(1, int(max_workers))
        self._default_timeout = max(1.0, float(default_timeout_seconds))
        self._grace = max(0.0, float(grace_seconds))
        self._name_prefix = name_prefix
        self._clock = clock
        self._lock = threading.Lock()
        self._running: dict[str, _Running] = {}
        # 被回收后仍可能存活的孤儿线程（仅用于计数/观测）
        self._orphans: list[threading.Thread] = []
        self._dispatched_total = 0
        self._rejected_busy_total = 0     # key 已在跑
        self._rejected_full_total = 0     # 并发数已满
        self._completed_total = 0
        self._failed_total = 0
        self._timed_out_total = 0

    # ── 派发 ────────────────────────────────────────────────────────────────

    def dispatch(
        self,
        key: str,
        fn: Callable[[], Any],
        *,
        timeout: Optional[float] = None,
        on_done: Optional[Callable[[DispatchResult], None]] = None,
        label: str = "",
    ) -> bool:
        """把 `fn` 交给后台线程执行，**立即返回**。成功派发返回 True。

        key 已在跑 / 并发数已满返回 False（不抛异常）。
        """
        timeout_s = self._default_timeout if timeout is None else max(1.0, float(timeout))
        now = self._clock()
        token = uuid.uuid4().hex
        rec = _Running(
            token=token, key=key, label=label or key, started_at=now,
            deadline=now + timeout_s, on_done=on_done,
        )
        with self._lock:
            if key in self._running:
                self._rejected_busy_total += 1
                return False
            if len(self._running) >= self._max_workers:
                self._rejected_full_total += 1
                return False
            self._running[key] = rec
            self._dispatched_total += 1

        t = threading.Thread(
            target=self._run, args=(rec, fn), daemon=True,
            name=f"{self._name_prefix}:{rec.label}"[:100],
        )
        rec.thread = t
        try:
            t.start()
        except Exception:
            # 线程都起不来：撤销记账，按"没派发成功"处理
            with self._lock:
                if self._running.get(key) is rec:
                    del self._running[key]
                self._dispatched_total -= 1
            log.warning("TickDispatcher: failed to start thread for %s", key, exc_info=True)
            return False
        return True

    def _run(self, rec: _Running, fn: Callable[[], Any]) -> None:
        status = STATUS_OK
        value: Any = None
        error: Optional[BaseException] = None
        try:
            value = fn()
        except BaseException as exc:  # noqa: BLE001 — 线程边界必须兜住所有异常
            status = STATUS_EXCEPTION
            error = exc
        finished_at = self._clock()

        with self._lock:
            current = self._running.get(rec.key)
            if current is None or current.token != rec.token:
                # 已被 reap_stale() 回收：记账已释放、on_done 已以 timeout 回调过，
                # 迟到的结果直接丢弃，避免重复释放/重复回调
                return
            del self._running[rec.key]
            if status == STATUS_OK:
                self._completed_total += 1
            else:
                self._failed_total += 1

        self._invoke_on_done(rec, DispatchResult(
            key=rec.key, label=rec.label, status=status, value=value, error=error,
            started_at=rec.started_at, finished_at=finished_at,
        ))

    def _invoke_on_done(self, rec: _Running, result: DispatchResult) -> None:
        if rec.on_done is None:
            return
        try:
            rec.on_done(result)
        except Exception:  # noqa: BLE001
            log.warning("TickDispatcher: on_done for %s raised", rec.key, exc_info=True)

    # ── 回收 ────────────────────────────────────────────────────────────────

    def reap_stale(self, now: Optional[float] = None) -> list[str]:
        """回收超过 `timeout + grace` 仍未返回的任务，返回被回收的 key 列表。

        幂等，可在每次 tick 里调用（只读状态 + 释放记账，不阻塞）。
        """
        now = self._clock() if now is None else now
        reaped: list[_Running] = []
        with self._lock:
            for key, rec in list(self._running.items()):
                if now >= rec.deadline + self._grace:
                    del self._running[key]
                    self._timed_out_total += 1
                    if rec.thread is not None:
                        self._orphans.append(rec.thread)
                    reaped.append(rec)
            self._orphans = [t for t in self._orphans if t.is_alive()]

        for rec in reaped:
            log.warning(
                "TickDispatcher: reaped stale task %s (running %.0fs, timeout %.0fs)",
                rec.key, now - rec.started_at, rec.deadline - rec.started_at,
            )
            self._invoke_on_done(rec, DispatchResult(
                key=rec.key, label=rec.label, status=STATUS_TIMEOUT,
                error=TimeoutError(f"dispatched task exceeded {rec.deadline - rec.started_at:.0f}s"),
                started_at=rec.started_at, finished_at=now,
            ))
        return [r.key for r in reaped]

    # ── 查询 ────────────────────────────────────────────────────────────────

    def is_running(self, key: str) -> bool:
        with self._lock:
            return key in self._running

    def running_keys(self) -> list[str]:
        with self._lock:
            return list(self._running.keys())

    def stats(self) -> dict:
        """观测快照，供 execution_model_status / 看板使用。"""
        with self._lock:
            self._orphans = [t for t in self._orphans if t.is_alive()]
            return {
                "max_workers": self._max_workers,
                "running": len(self._running),
                "running_keys": list(self._running.keys()),
                "dispatched_total": self._dispatched_total,
                "completed_total": self._completed_total,
                "failed_total": self._failed_total,
                "timed_out_total": self._timed_out_total,
                "rejected_busy_total": self._rejected_busy_total,
                "rejected_full_total": self._rejected_full_total,
                "orphan_threads_alive": len(self._orphans),
            }
