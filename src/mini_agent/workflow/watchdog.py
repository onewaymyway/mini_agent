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

from mini_agent.evolution.circuit_breaker_core import CircuitBreakerCore

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

        # [workflow_mechanism_improvement_plan_p10.md §3 / P2-1] per-step
        # 连续失败追踪（同 error_type 计数，由 runner 每次 attempt 失败时
        # 回填）与 [workflow_mechanism_improvement_plan_p14.md Phase 2]
        # 跨 step 熔断（同一 error_type 在多个不同 step 上出现即触发），
        # 都委托给通用的 `CircuitBreakerCore`（见
        # daemon_stability_and_ux_improvement_plan.md 第 1 项）——这套判定
        # 逻辑现在与 ObjectiveExecutor/CronJobRunner 共用同一份实现，watchdog
        # 这里只是其中一个持有者，外部接口（report_attempt_failure /
        # report_workflow_level_failure / circuit_breaker_tripped /
        # circuit_breaker_reason / reset_step_failures）保持不变，行为与
        # 重构前完全一致。
        self._circuit_breaker_threshold = circuit_breaker_distinct_step_threshold
        self._breaker = CircuitBreakerCore(
            distinct_scope_threshold=circuit_breaker_distinct_step_threshold,
            on_trip=self._on_circuit_breaker_tripped,
            log_fn=self._log_event,
        )

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
        return self._breaker.report_attempt_failure(step_id, error_type, threshold=threshold)

    # ── [workflow_mechanism_improvement_plan_p14.md Phase 2] 跨 step 熔断 ────

    @property
    def circuit_breaker_tripped(self) -> bool:
        return self._breaker.tripped

    @property
    def circuit_breaker_reason(self) -> Optional[str]:
        return self._breaker.trip_reason

    def report_workflow_level_failure(self, step_id: str, error_type: str) -> bool:
        """
        记录一次"某个 step 因 error_type 失败"，用于跨 step 的系统性故障
        识别。若启用了 circuit_breaker_distinct_step_threshold 且累计因
        同一 error_type 失败过的**不同** step 数达到阈值，主动
        request_cancel() 并把详情记进 watchdog.jsonl，返回 True（本次调用
        触发了熔断）；未启用/未达阈值/已经触发过，返回 False。

        与 report_attempt_failure（同一 step 连续同类失败）是两套独立
        判断——那个管"这个 step 重试大概率没用"，这个管"这类错误在系统里
        出现的广度已经超出单个 step 的偶发范围"。判定逻辑委托给
        `CircuitBreakerCore`（P2-1），这里只负责 workflow 特有的熔断后果
        （request_cancel + 主动告警），见 `_on_circuit_breaker_tripped`。
        """
        return self._breaker.report_breadth_failure(step_id, error_type)

    def _on_circuit_breaker_tripped(self, error_type: str, distinct_step_ids: list[str]) -> None:
        """`CircuitBreakerCore.on_trip` 回调：workflow 语义下熔断即主动
        request_cancel + 推送告警（与重构前行为一致）。"""
        if not self._control.cancel_requested.is_set():
            self._control.request_cancel()
        self._notify_circuit_breaker_tripped(error_type, distinct_step_ids)

    def _notify_circuit_breaker_tripped(self, error_type: str, distinct_step_ids: list[str]) -> None:
        """[daemon_stability_and_ux_improvement_plan.md P0-8] 熔断触发是
        "系统性问题"信号，值得主动推送，不能只写进 watchdog.jsonl 等用户
        翻日志才发现。复用 notification/dispatcher.py，异常整体吞掉，不
        影响熔断本身（request_cancel 已经在上面完成）。
        """
        try:
            from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
            NotificationDispatcher(self._paths).dispatch(NotificationMessage(
                title=f"工作流「{self._wf_id}」触发熔断",
                body=(
                    f"error_type={error_type!r} 已在 {len(distinct_step_ids)} 个不同步骤"
                    f"（{distinct_step_ids}）上失败，判定为系统性问题，已请求取消"
                )[:200],
                source="workflow_circuit_breaker",
                meta={"workflow_session_id": self._wf_id, "error_type": error_type},
            ))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.workflow.watchdog._notify_circuit_breaker_tripped")

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
        self._breaker.reset_scope_failures(step_id)

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
