"""
orchestrator/concurrency.py — 并发控制中心

提供两个层面的并发限制：
  1. Task 并发（SubAgent 数量）  — TaskSemaphore
  2. LLM 请求并发（所有 provider）— LLMSemaphore

以及一个全局频率限制器：
  3. LLM RPM 限速（每分钟最大请求数）— RateLimiter

还维护两个全局显示状态（供状态栏读取）：
  - 当前流式生成的 token 计数
  - 当前重试倒计时信息

两者都基于带计数和排队可见性的信号量实现：
  - acquire() 返回一个 context manager，退出时自动释放
  - 排队等待期间记录等待者信息（用于状态栏显示）
  - 线程安全

使用方式：
    # 初始化（程序启动时一次）
    from .concurrency import init_concurrency, get_task_sem, get_llm_sem
    init_concurrency(max_tasks=4, max_llm_calls=8)

    # 在 TaskManager 中
    with get_task_sem().acquire("my-task-id"):
        run_task()

    # 在 ProviderMixin 中
    with get_llm_sem().acquire("anthropic/claude"):
        call_api()
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Generator, Optional


# ── 等待者记录 ────────────────────────────────────────────────────────────────

@dataclass
class Waiter:
    label: str           # 可读名称（task_id / provider+model）
    waited_since: float  # 开始等待的时间戳
    kind: str            # "task" | "llm"

    @property
    def waited_seconds(self) -> float:
        return round(time.time() - self.waited_since, 1)


# ── 计数信号量（带排队可见性） ─────────────────────────────────────────────────

class CountingSemaphore:
    """
    带等待队列可见性的计数信号量。

    相比标准 threading.Semaphore，额外维护：
      - active_count: 当前持有者数量
      - waiters:      当前排队者列表（可读取但不可修改）
    """

    def __init__(self, limit: int, kind: str = "task") -> None:
        self._limit = limit
        self._kind = kind
        self._active = 0                          # 当前持有者数
        self._waiters: list[Waiter] = []          # 排队等待者
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    # ── 属性 ──────────────────────────────────────────────────────────────────

    @property
    def limit(self) -> int:
        return self._limit

    @limit.setter
    def limit(self, value: int) -> None:
        with self._cond:
            self._limit = max(1, value)
            self._cond.notify_all()  # 新增 slot，唤醒排队者

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active

    @property
    def waiting_count(self) -> int:
        with self._lock:
            return len(self._waiters)

    @property
    def available(self) -> int:
        with self._lock:
            return max(0, self._limit - self._active)

    def snapshot_waiters(self) -> list[Waiter]:
        """返回当前排队者的快照（线程安全）。"""
        with self._lock:
            return list(self._waiters)

    def snapshot_status(self) -> dict:
        """返回可用于显示的状态快照。"""
        with self._lock:
            return {
                "kind": self._kind,
                "limit": self._limit,
                "active": self._active,
                "waiting": len(self._waiters),
                "available": max(0, self._limit - self._active),
                "waiters": [
                    {"label": w.label, "waited_s": w.waited_seconds}
                    for w in self._waiters
                ],
            }

    # ── 获取 / 释放 ───────────────────────────────────────────────────────────

    @contextmanager
    def acquire(self, label: str = "") -> Generator[None, None, None]:
        """
        获取一个 slot。若已满则阻塞排队等待。
        用作 context manager：
            with sem.acquire("task-abc123"):
                do_work()
        """
        self._wait_and_acquire(label)
        try:
            yield
        finally:
            self._do_release()

    def _wait_and_acquire(self, label: str) -> None:
        """阻塞直到获得 slot（可能排队等待）。"""
        waiter = Waiter(label=label, waited_since=time.time(), kind=self._kind)
        with self._cond:
            if self._active < self._limit:
                # 立即有空位，直接获取
                self._active += 1
                return
            # 需要排队
            self._waiters.append(waiter)
            try:
                while self._active >= self._limit:
                    self._cond.wait(timeout=0.5)
                self._active += 1
            finally:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)

    def _do_release(self) -> None:
        """释放一个 slot 并通知等待者。"""
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify_all()

    def try_acquire(self) -> bool:
        """非阻塞尝试获取，成功返回 True，否则立即返回 False。"""
        with self._cond:
            if self._active < self._limit:
                self._active += 1
                return True
            return False

    def release(self) -> None:
        """手动释放（配合 try_acquire 使用）。"""
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify_all()


# ── 模块级单例 ────────────────────────────────────────────────────────────────

_task_sem: Optional[CountingSemaphore] = None
_llm_sem:  Optional[CountingSemaphore] = None


def init_concurrency(max_tasks: int = 4, max_llm_calls: int = 8) -> None:
    """程序启动时调用一次，初始化两个信号量。"""
    global _task_sem, _llm_sem
    _task_sem = CountingSemaphore(limit=max_tasks,    kind="task")
    _llm_sem  = CountingSemaphore(limit=max_llm_calls, kind="llm")


def get_task_sem() -> CountingSemaphore:
    if _task_sem is None:
        init_concurrency()
    return _task_sem


def get_llm_sem() -> CountingSemaphore:
    if _llm_sem is None:
        init_concurrency()
    return _llm_sem


def set_max_tasks(n: int) -> None:
    get_task_sem().limit = n


def set_max_llm_calls(n: int) -> None:
    get_llm_sem().limit = n


def concurrency_snapshot() -> dict:
    """返回两个信号量的联合状态快照（用于状态栏）。"""
    return {
        "tasks": get_task_sem().snapshot_status(),
        "llm":   get_llm_sem().snapshot_status(),
    }


# ── 全局 RPM 频率限制器 ────────────────────────────────────────────────────────

class RateLimiter:
    """
    滑动窗口频率限制器，线程安全。

    限制每分钟最多发出 max_rpm 次 LLM 请求。
    超出时阻塞等待，直到窗口内请求数低于上限。

    max_rpm=0 表示不限速（默认行为）。
    """

    def __init__(self, max_rpm: int = 0) -> None:
        self._max_rpm = max_rpm
        self._timestamps: deque = deque()   # 最近 60s 内的请求时间戳
        self._lock = threading.Lock()

    @property
    def max_rpm(self) -> int:
        return self._max_rpm

    @max_rpm.setter
    def max_rpm(self, value: int) -> None:
        with self._lock:
            self._max_rpm = max(0, value)

    @property
    def enabled(self) -> bool:
        return self._max_rpm > 0

    def acquire(self) -> float:
        """
        等待直到可以发出一次请求（不超过 RPM 限制）。

        Returns:
            实际等待的秒数（0.0 = 无需等待）
        """
        if not self.enabled:
            return 0.0

        waited = 0.0
        while True:
            with self._lock:
                now = time.time()
                window_start = now - 60.0
                # 清理过期时间戳
                while self._timestamps and self._timestamps[0] < window_start:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._max_rpm:
                    self._timestamps.append(now)
                    return waited

                # 计算需要等到最旧时间戳过期
                oldest = self._timestamps[0]
                wait_sec = (oldest + 60.0) - now + 0.05

            time.sleep(min(wait_sec, 0.5))
            waited += min(wait_sec, 0.5)

    def snapshot(self) -> dict:
        """返回当前状态快照（用于状态栏显示）。"""
        with self._lock:
            now = time.time()
            window_start = now - 60.0
            recent_count = sum(1 for t in self._timestamps if t >= window_start)
            # 如果超限，计算最早可用时间
            wait_sec = 0.0
            if self.enabled and recent_count >= self._max_rpm and self._timestamps:
                oldest = self._timestamps[0]
                wait_sec = max(0.0, (oldest + 60.0) - now)
            return {
                "max_rpm": self._max_rpm,
                "used_in_window": recent_count,
                "enabled": self.enabled,
                "wait_sec": round(wait_sec, 1),
            }


_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(max_rpm=0)
    return _rate_limiter


def set_rpm_limit(max_rpm: int) -> None:
    """动态调整 RPM 限制（0 = 不限速）。"""
    get_rate_limiter().max_rpm = max_rpm


# ── 全局流式 token 计数状态（供状态栏实时显示） ────────────────────────────────

class StreamTokenState:
    """当前正在流式生成的 token 计数状态。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = False
        self.token_count = 0
        self.started_at = 0.0
        self.model = ""

    def start(self, model: str = "") -> None:
        with self._lock:
            self.active = True
            self.token_count = 0
            self.started_at = time.monotonic()
            self.model = model

    def increment(self) -> None:
        # 热路径，用非阻塞方式；Python GIL 保证 int += 1 原子
        if self.active:
            self.token_count += 1

    def stop(self) -> None:
        with self._lock:
            self.active = False

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = (time.monotonic() - self.started_at) if self.active else 0.0
            tps = self.token_count / elapsed if elapsed > 0.1 else 0.0
            return {
                "active": self.active,
                "token_count": self.token_count,
                "elapsed_s": round(elapsed, 1),
                "tokens_per_sec": round(tps, 1),
                "model": self.model,
            }


_stream_token_state = StreamTokenState()


def get_stream_token_state() -> StreamTokenState:
    return _stream_token_state


# ── 全局重试倒计时状态（供状态栏显示） ────────────────────────────────────────

class RetryCountdownState:
    """当前重试等待的状态信息。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = False
        self.attempt = 0
        self.max_retries = 0
        self.wait_until = 0.0
        self.reason = ""

    def start_wait(self, attempt: int, max_retries: int, delay: float, reason: str) -> None:
        with self._lock:
            self.active = True
            self.attempt = attempt
            self.max_retries = max_retries
            self.wait_until = time.monotonic() + delay
            self.reason = reason[:60] if reason else ""

    def stop_wait(self) -> None:
        with self._lock:
            self.active = False

    def snapshot(self) -> dict:
        with self._lock:
            remaining = max(0.0, self.wait_until - time.monotonic())
            return {
                "active": self.active,
                "attempt": self.attempt,
                "max_retries": self.max_retries,
                "remaining_s": round(remaining, 1),
                "reason": self.reason,
            }


_retry_countdown_state = RetryCountdownState()


def get_retry_countdown_state() -> RetryCountdownState:
    return _retry_countdown_state
