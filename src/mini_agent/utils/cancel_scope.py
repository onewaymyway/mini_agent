"""
utils/cancel_scope.py — 任务级"硬取消"令牌（cancel_task 真正中断执行）

背景（next_doc/cancel_task_hard_interrupt_plan.md）：
  此前 cancel_task 只做"协作式取消"：置一个 threading.Event，_agentic_loop 每轮
  迭代顶部检查。这意味着
    * 正在进行的 LLM 请求要等它返回才会停；
    * 正在执行的 bash 命令要等它自己跑完（或超时）才会停；
  在此期间那个任务仍占着并发槽位，工具还可能继续产生副作用（写文件、跑命令）。

机制（thread-local，调用栈上任意一层都能读到，无需层层改签名；与
llm/deadline.py 的 deadline_scope 同一风格）：
  ``CancelToken``
      在原有 Event 之上增加"可被取消时主动调用的 killer 列表"。正在进行的
      阻塞型 I/O（bash 子进程、LLM 请求）在开始前登记一个 killer；
      ``token.cancel()`` 时置位 Event 并立即执行所有已登记的 killer
      （杀子进程树 / 唤醒等待中的 LLM 调用线程），而不是等它们自然结束。
  ``cancel_scope(token)``
      进入作用域时把 token 绑定到当前线程；退出时还原。SubAgent 的执行线程
      在整个 _run_body 外层使用它。
  ``current_token()``
      bash 工具 / LLM provider 层读取；没有 token（主 Agent、cron 等其它
      路径）时返回 None，所有调用点据此完全走旧逻辑——纯 opt-in，默认不改变
      任何现有行为。
  ``TaskCancelled``
      继承 BaseException（而不是 Exception）：LLM 重试层、client_pool 的
      fallback 轮换、各处 ``except Exception`` 都不会把"用户主动取消"误当作
      一次可重试/可降级的失败而继续发起新请求。只有明确知道取消语义的最外层
      （SubAgent._run_body）才捕获它。

注意：Python 无法强杀一个阻塞在 socket 上的线程。LLM 请求的取消是"调用方立即
返回 + 后台线程自行结束并丢弃结果"（流式请求在下一个 token 到达时还会主动
断开连接）；bash 则是真正对进程组发 SIGKILL（Windows 为 taskkill /T /F）。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator, Optional


class TaskCancelled(BaseException):
    """任务被外部取消（cancel_task / 看板终止）。

    故意继承 BaseException：见模块文档，避免被重试 / fallback 逻辑吞掉。
    """


class CancelToken:
    """可主动中断阻塞型 I/O 的取消令牌。线程安全。"""

    def __init__(self, event: Optional[threading.Event] = None) -> None:
        # 允许复用调用方已有的 Event（SubAgent._cancel_event），保持旧代码
        # 里 `_cancel_event.is_set()` 的各处判断继续有效。
        self.event: threading.Event = event if event is not None else threading.Event()
        self._lock = threading.Lock()
        self._killers: dict[int, Callable[[], None]] = {}
        self._next_id = 0

    # ── 查询 ──────────────────────────────────────────────────────────────
    @property
    def is_cancelled(self) -> bool:
        return self.event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.event.is_set():
            raise TaskCancelled("task cancelled")

    # ── killer 登记 ───────────────────────────────────────────────────────
    def register_killer(self, killer: Callable[[], None]) -> int:
        """登记一个"被取消时要执行的动作"，返回句柄（用于 unregister）。

        如果登记时已经处于取消状态，立刻同步执行 killer——避免"检查取消 →
        启动子进程"之间的竞态导致取消信号被漏掉。
        """
        with self._lock:
            handle = self._next_id
            self._next_id += 1
            already = self.event.is_set()
            if not already:
                self._killers[handle] = killer
        if already:
            _safe_call(killer)
        return handle

    def unregister_killer(self, handle: int) -> None:
        with self._lock:
            self._killers.pop(handle, None)

    # ── 取消 ──────────────────────────────────────────────────────────────
    def cancel(self) -> None:
        """置位取消信号，并立即执行所有已登记的 killer。可重复调用。"""
        self.event.set()
        with self._lock:
            killers = list(self._killers.values())
            self._killers.clear()
        for k in killers:
            _safe_call(k)


def _safe_call(fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception as exc:  # killer 失败不能影响取消流程本身
        try:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.utils.cancel_scope._safe_call")
        except Exception:
            pass


_local = threading.local()


def current_token() -> Optional[CancelToken]:
    """当前线程绑定的取消令牌；没有则 None（调用点据此走旧逻辑）。"""
    return getattr(_local, "token", None)


@contextmanager
def cancel_scope(token: Optional[CancelToken]) -> Iterator[None]:
    """把 token 绑定到当前线程。token 为 None 时是空操作。"""
    prev = getattr(_local, "token", None)
    _local.token = token if token is not None else prev
    try:
        yield
    finally:
        _local.token = prev
