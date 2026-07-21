"""
workflow/registry.py — 进程内活跃 workflow 执行的控制注册表

背景：pause/cancel/approve/reject 这类控制信号，若只写 session.json，
foreground（同步）执行的 runner 主线程只会在"批次边界"这种少数几个点才
会去读盘检查，响应有延迟且无法在批次内部及时生效；而 background（后台
线程）执行时，控制类工具调用和 runner 本身运行在同一进程里，可以通过一个
共享的 threading.Event 组合实现近乎实时的响应。

本模块只维护"当前进程内正在运行/最近运行过"的 workflow_session_id ->
ControlState 映射，重启进程后该映射为空（此时只能依赖 session.json 里
落盘的最终状态，以及 resume_workflow_run 重新走一遍未完成的 step）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ControlState:
    """单次 workflow 执行的进程内控制状态，runner/watchdog 与控制类工具共享。"""
    pause_requested: threading.Event = field(default_factory=threading.Event)
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    # 当前正等待审批的 step_id（None=当前没有 step 在等审批）
    pending_approval_step: Optional[str] = None
    approved: threading.Event = field(default_factory=threading.Event)
    rejected: threading.Event = field(default_factory=threading.Event)
    rejection_reason: str = ""
    # 后台线程句柄，便于 join / 状态查询（不强制要求设置）
    thread: Optional[threading.Thread] = None

    # [workflow机制改进计划.md P5] human_input 类型 step：当前正等待人工
    # 输入的 step_id（None=当前没有 step 在等输入），与审批门是两套独立
    # 信号（同一个 step 不会同时处于两种等待状态）。
    pending_input_step: Optional[str] = None
    input_provided: threading.Event = field(default_factory=threading.Event)
    provided_input_text: str = ""

    def request_pause(self) -> None:
        self.pause_requested.set()

    def request_resume(self) -> None:
        self.pause_requested.clear()

    def request_cancel(self) -> None:
        self.cancel_requested.set()

    def request_approve(self, step_id: str) -> bool:
        if self.pending_approval_step != step_id:
            return False
        self.approved.set()
        return True

    def request_reject(self, step_id: str, reason: str = "") -> bool:
        if self.pending_approval_step != step_id:
            return False
        self.rejection_reason = reason
        self.rejected.set()
        return True

    def request_provide_input(self, step_id: str, text: str) -> bool:
        if self.pending_input_step != step_id:
            return False
        self.provided_input_text = text
        self.input_provided.set()
        return True


_lock = threading.Lock()
_ACTIVE: dict[str, ControlState] = {}


def register(workflow_session_id: str, state: Optional[ControlState] = None) -> ControlState:
    with _lock:
        st = state or ControlState()
        _ACTIVE[workflow_session_id] = st
        return st


def get(workflow_session_id: str) -> Optional[ControlState]:
    with _lock:
        return _ACTIVE.get(workflow_session_id)


def unregister(workflow_session_id: str) -> None:
    with _lock:
        _ACTIVE.pop(workflow_session_id, None)


def list_active_ids() -> list[str]:
    with _lock:
        return list(_ACTIVE.keys())
