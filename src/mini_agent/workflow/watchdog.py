"""
workflow/watchdog.py — WorkflowWatchdog 看护线程（workflow机制改进计划.md P3）

职责：
  1. 心跳与卡死检测：每个正在跑的 step 定期上报心跳（heartbeat），若超过
     该 step 的 timeout 仍未产生心跳，标记为"疑似卡死"，供 runner 在下一次
     轮询点强制判 TIMEOUT（真正的硬中断仍由 runner 侧的 future.result(timeout)
     完成，watchdog 只负责判定与记录，不直接杀线程——Python 线程无法被安全
     强杀，这是已知限制，见 next_doc 的风险说明）。
  2. 资源/成本护栏：累计运行时长超过 max_total_duration 时，主动请求 cancel。
  3. 把关键事件写入 watchdog.jsonl，供事后审计。

不负责：控制信号（pause/cancel/approve/reject）的存储——那部分由
workflow/registry.py 的 ControlState + workflow/session.py 的
WorkflowSession.control_flags 负责，watchdog 只是其中一个读取/写入方。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from .registry import ControlState


class WorkflowWatchdog:
    def __init__(
        self,
        paths: "AgentPaths",
        workflow_session_id: str,
        control: "ControlState",
        poll_interval: float = 5.0,
        max_total_duration: Optional[float] = None,
    ) -> None:
        self._paths = paths
        self._wf_id = workflow_session_id
        self._control = control
        self._poll_interval = max(0.5, poll_interval)
        self._max_total_duration = max_total_duration
        self._t_start = time.monotonic()

        self._heartbeats: dict[str, float] = {}
        self._timeouts: dict[str, float] = {}
        self._timed_out_steps: set[str] = set()
        self._hb_lock = threading.Lock()

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── 心跳接口（由 runner 在 step 执行前后调用）───────────────────────────

    def register_step_start(self, step_id: str, timeout: Optional[float]) -> None:
        with self._hb_lock:
            self._heartbeats[step_id] = time.monotonic()
            if timeout:
                self._timeouts[step_id] = timeout

    def heartbeat(self, step_id: str) -> None:
        with self._hb_lock:
            self._heartbeats[step_id] = time.monotonic()

    def register_step_end(self, step_id: str) -> None:
        with self._hb_lock:
            self._heartbeats.pop(step_id, None)
            self._timeouts.pop(step_id, None)
            self._timed_out_steps.discard(step_id)

    def is_step_timed_out(self, step_id: str) -> bool:
        with self._hb_lock:
            return step_id in self._timed_out_steps

    # ── 生命周期 ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"wf-watchdog-{self._wf_id}")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    # ── 主循环 ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._check_heartbeats()
                self._check_resource_guard()
            except Exception as e:
                from mini_agent.errors import log_exception
                log_exception(e, where="mini_agent.workflow.watchdog.WorkflowWatchdog._loop")
            self._stop_event.wait(self._poll_interval)

    def _check_heartbeats(self) -> None:
        now = time.monotonic()
        with self._hb_lock:
            for step_id, last_hb in list(self._heartbeats.items()):
                timeout = self._timeouts.get(step_id)
                if timeout and (now - last_hb) > timeout and step_id not in self._timed_out_steps:
                    self._timed_out_steps.add(step_id)
                    self._log_event("heartbeat_timeout", {
                        "step_id": step_id,
                        "timeout": timeout,
                        "elapsed": now - last_hb,
                    })

    def _check_resource_guard(self) -> None:
        if not self._max_total_duration:
            return
        elapsed = time.monotonic() - self._t_start
        if elapsed > self._max_total_duration and not self._control.cancel_requested.is_set():
            self._control.request_cancel()
            self._log_event("max_total_duration_exceeded", {
                "elapsed": elapsed,
                "max_total_duration": self._max_total_duration,
            })

    # ── 日志 ────────────────────────────────────────────────────────────────

    def _log_event(self, event_type: str, data: dict) -> None:
        try:
            self._paths.ensure_workflow_session_dir(self._wf_id)
            p = self._paths.workflow_session_watchdog_log(self._wf_id)
            record = {"ts": time.time(), "event": event_type, **data}
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.workflow.watchdog.WorkflowWatchdog._log_event")
