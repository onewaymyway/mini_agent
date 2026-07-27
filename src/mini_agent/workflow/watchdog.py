"""
workflow/watchdog.py — WorkflowWatchdog 看护线程（workflow机制改进计划.md P3）

职责：
  1. 心跳与卡死检测：每个正在跑的 step 定期上报心跳（heartbeat），若超过
     该 step 的 timeout 仍未产生心跳，标记为"疑似卡死"，供 runner 在下一次
     轮询点强制判 TIMEOUT（真正的硬中断仍由 runner 侧的 future.result(timeout)
     完成，watchdog 只负责判定与记录，不直接杀线程——Python 线程无法被安全
     强杀，这是已知限制，见 next_doc 的风险说明）。
  2. 资源/成本护栏：累计运行时长超过 max_total_duration 时，主动请求 cancel；
     [P7-②1] 累计 token 用量（由 runner 回填）超过 max_total_tokens 时同样
     主动请求 cancel。
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
        max_total_tokens: Optional[int] = None,
        circuit_breaker_distinct_step_threshold: Optional[int] = None,
    ) -> None:
        self._paths = paths
        self._wf_id = workflow_session_id
        self._control = control
        self._poll_interval = max(0.5, poll_interval)
        self._max_total_duration = max_total_duration
        self._max_total_tokens = max_total_tokens
        self._t_start = time.monotonic()

        self._heartbeats: dict[str, float] = {}
        self._timeouts: dict[str, float] = {}
        self._timed_out_steps: set[str] = set()
        self._hb_lock = threading.Lock()

        # [P7-②1 workflow_mechanism_improvement_plan.md] 累计 token 用量，
        # 由 runner._report_step_tokens() 在每个 step 的 Agent 跑完后回填。
        self._total_tokens = 0
        self._token_lock = threading.Lock()

        # [workflow_mechanism_improvement_plan_p10.md §3] per-step 连续失败
        # 追踪（同 error_type 计数），由 runner._execute_step_with_error_retry
        # 在每次 attempt 失败时回填（report_attempt_failure）。复用同一把
        # 心跳节拍所在线程模型，不新增轮询频率——这是纯计数状态，不需要
        # watchdog 主循环参与，达阈值时由 runner 侧直接短路重试循环。
        self._consecutive_failures: dict[str, tuple[str, int]] = {}
        self._failure_lock = threading.Lock()

        # [workflow_mechanism_improvement_plan_p14.md Phase 2] 跨 step 熔断：
        # error_type -> 曾经因该 error_type 失败过至少一次的 step_id 集合
        # （本次运行全程累计，不做滑动窗口/不做失败后清零——即便某个 step
        # 后来重试成功了，它"确实失败过一次"这个事实仍然计入，因为熔断
        # 关心的是"这类问题在系统里出现的广度"，不是"当前还有多少 step
        # 处于失败状态"）。复用 _failure_lock，同一把锁保护两类计数，避免
        # 引入新的锁顺序风险。
        self._circuit_breaker_threshold = circuit_breaker_distinct_step_threshold
        self._error_type_step_ids: dict[str, set[str]] = {}
        self._circuit_breaker_tripped = False
        self._circuit_breaker_reason: Optional[str] = None

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

    # ── token 预算护栏接口（由 runner 在每个 step 的 Agent 跑完后调用）──────

    def register_step_tokens(self, step_id: str, tokens_used: int) -> None:
        """累加本次工作流执行的 token 用量。跨线程安全（同层并发多个 step）。"""
        if tokens_used <= 0:
            return
        with self._token_lock:
            self._total_tokens += tokens_used

    @property
    def total_tokens(self) -> int:
        with self._token_lock:
            return self._total_tokens

    # ── 连续同类失败追踪（由 runner 在每次 attempt 失败时调用）──────────────

    def report_attempt_failure(self, step_id: str, error_type: str, threshold: int = 2) -> bool:
        """
        [workflow_mechanism_improvement_plan_p10.md §3] 记录一次 step 执行
        尝试失败。若与上一次记录的 error_type 相同，连续计数 +1；否则重新
        从 1 开始计数（"连续"要求中间没有出现过不同类型的失败）。

        返回 True 表示已达到 threshold（默认 2），调用方（runner）应据此
        提前把该 step 判定为 NEEDS_FIX、跳过剩余的 retry_on_error 预算，
        不必等重试次数耗尽——这类信号本身就在证伪"重试大概率会不一样"的
        前提。跨线程安全（并发批次下多个 step 各自独立计数，同一 step_id
        理论上不会跨线程并发调用，但仍加锁保证原子性）。
        """
        with self._failure_lock:
            prev_type, prev_count = self._consecutive_failures.get(step_id, (None, 0))
            count = prev_count + 1 if prev_type == error_type else 1
            self._consecutive_failures[step_id] = (error_type, count)
            escalate = count >= max(1, threshold)
        if escalate:
            self._log_event("consecutive_failure_escalated", {
                "step_id": step_id, "error_type": error_type,
                "count": count, "threshold": threshold,
            })
        return escalate

    # ── [workflow_mechanism_improvement_plan_p14.md Phase 2] 跨 step 熔断 ────

    @property
    def circuit_breaker_tripped(self) -> bool:
        with self._failure_lock:
            return self._circuit_breaker_tripped

    @property
    def circuit_breaker_reason(self) -> Optional[str]:
        with self._failure_lock:
            return self._circuit_breaker_reason

    def report_workflow_level_failure(self, step_id: str, error_type: str) -> bool:
        """
        记录一次"某个 step 因 error_type 失败"，用于跨 step 的系统性故障
        识别。若启用了 circuit_breaker_distinct_step_threshold 且累计因
        同一 error_type 失败过的**不同** step 数达到阈值，主动
        request_cancel() 并把详情记进 watchdog.jsonl，返回 True（本次调用
        触发了熔断）；未启用/未达阈值/已经触发过，返回 False。

        与 report_attempt_failure（同一 step 连续同类失败）是两套独立
        判断——那个管"这个 step 重试大概率没用"，这个管"这类错误在系统里
        出现的广度已经超出单个 step 的偶发范围"。
        """
        if not self._circuit_breaker_threshold:
            return False
        with self._failure_lock:
            if self._circuit_breaker_tripped:
                return False
            ids = self._error_type_step_ids.setdefault(error_type, set())
            ids.add(step_id)
            distinct_count = len(ids)
            tripped = distinct_count >= max(1, self._circuit_breaker_threshold)
            if tripped:
                self._circuit_breaker_tripped = True
                self._circuit_breaker_reason = (
                    f"error_type={error_type!r} 已在 {distinct_count} 个不同步骤"
                    f"（{sorted(ids)}）上失败，达到熔断阈值 "
                    f"{self._circuit_breaker_threshold}，判定为系统性问题"
                )
        if tripped:
            if not self._control.cancel_requested.is_set():
                self._control.request_cancel()
            self._log_event("circuit_breaker_tripped", {
                "error_type": error_type,
                "distinct_step_ids": sorted(ids),
                "threshold": self._circuit_breaker_threshold,
            })
        return tripped

    # ── [P11 §6.4] 依赖声明与实际引用不一致 ──────────────────────────────────

    def report_dependency_mismatch(self, step_id: str, undeclared_ids: list[str]) -> None:
        """
        [workflow_input_passing_and_debug_logging_improvement_plan.md §6.4]
        某个 step 实际引用到的上游 step_id（从占位符/python_step ctx.inputs
        解析得到，见 runner.py::_scan_prompt_placeholders）里，出现了未在
        该 step 的 depends_on 中声明的 id。正常情况下这条路径已经被
        WorkflowDef.validate() 的静态检查（§1/§4）拦在保存阶段之前，只有
        用户显式关闭了 placeholder_depends_on_check_enabled /
        python_step_inputs_filtered_by_depends_on 才可能在运行期出现。

        这里只做记录（写进 watchdog 事件日志，供
        get_workflow_run_status(verbose=True) 或后续分析工具查阅），不改变
        当前 step 的执行结果/重试逻辑——是否要因此升级为 NEEDS_FIX，留给
        用户或更上层的分析工具基于这条记录自行判断，watchdog 本身不代为
        决定"这次不一致算不算致命"。
        """
        self._log_event("dependency_declaration_mismatch", {
            "step_id": step_id, "undeclared_ids": undeclared_ids,
        })

    def reset_step_failures(self, step_id: str) -> None:
        """该 step 成功或最终结束后清空连续失败计数，避免影响同一次运行中
        （gate-retry 场景）后续对该 step 的重新计数。"""
        with self._failure_lock:
            self._consecutive_failures.pop(step_id, None)

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
        if self._max_total_duration:
            elapsed = time.monotonic() - self._t_start
            if elapsed > self._max_total_duration and not self._control.cancel_requested.is_set():
                self._control.request_cancel()
                self._log_event("max_total_duration_exceeded", {
                    "elapsed": elapsed,
                    "max_total_duration": self._max_total_duration,
                })

        if self._max_total_tokens:
            total = self.total_tokens
            if total > self._max_total_tokens and not self._control.cancel_requested.is_set():
                self._control.request_cancel()
                self._log_event("max_total_tokens_exceeded", {
                    "total_tokens": total,
                    "max_total_tokens": self._max_total_tokens,
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
