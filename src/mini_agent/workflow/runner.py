"""
workflow/runner.py — WorkflowRunner 工作流执行引擎

核心职责：
  1. 按依赖顺序（拓扑分层）执行步骤，同一层内互不依赖的步骤默认并发执行
     （[具身改进 B3]，依赖 `cfg.workflow.parallel_enabled`/`max_parallel`，
     单步骤可用 `WorkflowStep.allow_parallel=False` 强制串行）
  2. 步骤间数据传递：{step_id.output} / {step_id.score} 占位符替换
  3. 条件判断：condition 表达式决定步骤是否执行
  4. 角色 Agent 绑定：step.role 指定由哪个角色执行
  5. 动态参数注入：run_workflow(inputs={"code": "..."}) 替换 {code}
  6. 运行时状态追踪：每步状态、耗时、输出

并发安全性说明（B3）：`_execute_with_main_agent()` 给每个步骤创建独立的
`Agent` 实例（独立 history/独立 PermissionGuard），步骤之间不共享可变 Agent
状态，这是"同层并发是安全的"的前提。唯一的跨线程共享可变状态是
`step_results` dict，所有读写都通过 `_run_one_step()`/`_run_step_with_gate_retry()`
里的 `results_lock` 保护。

执行流程（workflow机制改进计划.md P2/P3/P4 之后）：
  WorkflowRunner.run(workflow_def, inputs, workflow_session_id=None)
    → 创建/加载 WorkflowSession（.agent/workflow_sessions/<wf_session_id>/），
      resume 时跳过已 DONE 的 step
    → 在 workflow/registry.py 注册本次执行的 ControlState（pause/cancel/
      approve/reject 信号载体），启动 WorkflowWatchdog 看护线程
    → _compute_parallel_batches()    # 拓扑分层，得到可并发执行的 batch 列表
    → for each batch:
        → 批次边界检查 pause/cancel 信号
        → 层内并发执行（ThreadPoolExecutor，allow_parallel=False 的步骤单独串行跑）
          → _run_one_step()
              → _resolve_prompt()     # 替换占位符
              → _eval_condition()     # 判断是否执行
              → 若 step.require_approval：置 AWAITING_APPROVAL，阻塞等待
                approve/reject/cancel 信号
              → _run_step_with_gate_retry() → _execute_step_with_retries()
                → _execute_step()     # 调用主 Agent 或角色 Agent，
                                      # 普通异常按 retry_on_error 重试
              → 记录 StepResult，增量写回 WorkflowSession
    → 停止看护线程，注销 ControlState
    → 返回 WorkflowRunResult（附带 workflow_session_id，供后续 resume/查询）

后台执行：本模块的 run() 本身始终是同步阻塞调用；"后台执行"由
workflow/tools.py 的 run_workflow(background=True) 在独立线程里调用 run()
实现，run() 内部不感知自己是否处于后台线程。
"""

from __future__ import annotations

import re
import threading
import time
import traceback as _traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from .schema import WorkflowDef, WorkflowStep, StepResult, StepStatus
from .session import WorkflowSession, WorkflowRunStatus
from . import registry as wf_registry
from .watchdog import WorkflowWatchdog

if TYPE_CHECKING:
    from mini_agent.config import AppConfig


def step_requires_approval(step: "WorkflowStep", wf_cfg: Any = None) -> bool:
    """
    [workflow机制改进计划.md P5] 模块级辅助函数：判定某个 step 是否需要人工
    审批门。逻辑与 WorkflowRunner._effective_require_approval 完全一致，
    抽成独立函数是因为 workflow/tools.py 和 cli/commands/workflow_cmd.py
    需要在真正调用 runner.run() 之前（决定是否强制 background 执行）就
    做同样的判断，那两处没有 WorkflowRunner 实例可用。
    """
    if getattr(step, "require_approval", False):
        return True
    if getattr(step, "effective_type", None) == "tool_call":
        return not bool(getattr(wf_cfg, "tool_call_step_auto_approve", False))
    return False


@dataclass
class WorkflowRunResult:
    """一次工作流执行的完整结果。"""
    workflow_name: str
    status: str              # "done" | "failed" | "partial" | "paused" | "cancelled"
    step_results: list[StepResult] = field(default_factory=list)
    total_duration: float = 0.0
    error: Optional[str] = None
    workflow_session_id: str = ""
    # 本次执行的默认落盘输出目录（.agent/workflow_sessions/<id>/output/），
    # 用户未指定输出路径时，任何"要保存为文件"的产出都应写到这里，而不是
    # 触发本次工作流的主 Agent 自己的 session output 目录。
    output_dir: str = ""

    @property
    def final_output(self) -> str:
        """返回最后一个成功步骤的输出。"""
        for sr in reversed(self.step_results):
            if sr.status == StepStatus.DONE and sr.output:
                return sr.output
        return ""

    def to_summary(self) -> str:
        """生成人类可读的执行摘要。"""
        lines = [f"## 工作流执行结果：{self.workflow_name}"]
        lines.append(f"状态：{self.status}  耗时：{self.total_duration:.1f}s")
        lines.append("")
        for sr in self.step_results:
            icon = {"done": "✅", "skipped": "⏭️", "failed": "❌", "pending": "⏳", "gate_failed": "🔄"}.get(
                sr.status.value, "❓"
            )
            score_str = f"  评分：{int(sr.score * 100)}/100" if sr.score is not None else ""
            lines.append(f"{icon} **{sr.step_id}**{score_str}  ({sr.duration_seconds:.1f}s)")
            if sr.status == StepStatus.DONE and sr.output:
                preview = sr.output[:200].replace("\n", " ")
                if len(sr.output) > 200:
                    preview += "..."
                lines.append(f"   {preview}")
            elif sr.status == StepStatus.FAILED and sr.error:
                lines.append(f"   错误：{sr.error}")
        if self.final_output:
            lines.append("")
            lines.append("---")
            lines.append("### 最终输出")
            lines.append(self.final_output)
        if self.output_dir:
            lines.append("")
            lines.append(
                f"📁 本次工作流的默认输出目录：`{self.output_dir}`\n"
                f"若需要把以上内容保存为文件，且用户没有另外指定路径，请写入此目录，"
                f"不要写入你自己（主 Agent）的 session output 目录。"
            )
        return "\n".join(lines)


class WorkflowRunner:
    """工作流执行引擎。"""

    def __init__(self, cfg: "AppConfig") -> None:
        self._cfg = cfg
        # [workflow机制改进计划.md P5] sub_workflow 递归深度，由
        # SubWorkflowStepExecutor 在创建嵌套 WorkflowRunner 时设置；
        # 顶层 run() 始终为 0。
        self._sub_workflow_depth: int = 0

    # ── [workflow机制改进计划.md P5] 生命周期 Hook 对称化 ─────────────────────

    def _emit_hook(self, event: str, payload: dict) -> None:
        """触发 workflow 生命周期 Hook（WorkflowStart/StepStart/StepEnd/GateFailed/WorkflowEnd）。

        复用项目现有的 hooks 注册体系（mini_agent.hooks），定制方无需改
        runner 源码，通过 .agent/hooks.json 声明命令即可挂钩。受
        cfg.workflow.hooks_enabled 开关控制（默认开启）；触发失败不影响
        主流程（吞异常，只记录日志）。
        """
        wf_cfg = getattr(self._cfg, "workflow", None)
        if not bool(getattr(wf_cfg, "hooks_enabled", True)):
            return
        try:
            from mini_agent.hooks.loader import get_hook_manager
            mgr = get_hook_manager()
            if mgr is not None:
                mgr.run(event, payload)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where=f"mini_agent.workflow.runner.WorkflowRunner._emit_hook[{event}]")

    def run(
        self,
        wf: WorkflowDef,
        inputs: Optional[dict] = None,
        workflow_session_id: Optional[str] = None,
    ) -> WorkflowRunResult:
        """
        执行一个工作流。

        inputs: 外部传入的动态参数，如 {"code": "...", "lang": "python"}
                会替换步骤 prompt 中的 {code} / {lang} 占位符
        workflow_session_id: 若指定且对应的 session.json 已存在，则视为
                resume——跳过已 DONE 的 step，只重跑未完成部分；否则视为
                新建一次执行（新建时若传入的 id 尚不存在也会用它作为新
                session 的 id，方便调用方提前分配好 id）。
        """
        import mini_agent.ui.renderer as R
        from mini_agent.storage.paths import AgentPaths

        inputs = inputs or {}
        t_start = time.monotonic()

        paths = AgentPaths(project_root=self._cfg.project_root)
        wf_cfg = getattr(self._cfg, "workflow", None)

        wf_session_id = workflow_session_id or f"wfs_{uuid.uuid4().hex[:12]}"
        loaded = WorkflowSession.load(paths, wf_session_id)
        if loaded is not None:
            wf_session = loaded
            wf_session.status = WorkflowRunStatus.RUNNING
            R.print_info(f"[Workflow] 恢复执行：{wf_session.summary_line()}")
        else:
            wf_session = WorkflowSession(
                workflow_session_id=wf_session_id,
                workflow_name=wf.name,
                inputs=inputs,
            )
            paths.ensure_workflow_session_dir(wf_session_id)
            # 保存本次执行使用的工作流定义快照，防止运行中途原 YAML 被改动
            #（下面顺带把 output/ 目录也建好——见 workflow_session_output_dir
            # 的说明：本次执行的落盘产出默认应该去这里，而不是主 Agent 自己
            # 的 session output 目录）。
            try:
                import yaml  # type: ignore
                snap = yaml.dump(wf.to_dict(), allow_unicode=True, sort_keys=False)
            except ImportError:
                import json as _json
                snap = _json.dumps(wf.to_dict(), ensure_ascii=False, indent=2)
            paths.workflow_session_def_snapshot(wf_session_id).write_text(snap, encoding="utf-8")

        wf_output_dir = paths.ensure_workflow_session_output_dir(wf_session_id)

        step_results: dict[str, StepResult] = dict(wf_session.step_results)
        wf_session.step_results = step_results
        wf_session.save(paths)
        wf_session.append_event(paths, "workflow_start", {"workflow_name": wf.name, "resumed": loaded is not None})

        R.print_info(
            f"[Workflow] 开始执行：{wf.name}（共 {len(wf.steps)} 步，"
            f"workflow_session_id={wf_session_id}）"
        )
        self._emit_hook("WorkflowStart", {
            "workflow_name": wf.name,
            "workflow_session_id": wf_session_id,
            "resumed": loaded is not None,
            "step_count": len(wf.steps),
        })

        # 供 _run_step_with_gate_retry 引用步骤定义
        self._current_wf_steps = wf.steps
        self._current_wf_session = wf_session
        self._current_paths = paths

        # [workflow_directory_mode_design.md 阶段3] 文件夹模式 workflow
        # （wf.source_dir 不为 None）构造一次本地 agent/skill 资源包，供
        # 本次运行内各 step 使用；单文件模式 wf.source_dir 为 None 时为 None，
        # 各处使用方需判空回退到原有全局资源查找逻辑。
        from .resource_bundle import build_resource_bundle
        self._current_resource_bundle = build_resource_bundle(self._cfg, wf)

        control = wf_registry.register(wf_session_id)
        max_total_duration = wf.max_total_duration or getattr(wf_cfg, "max_total_duration_seconds", None)
        watchdog_enabled = bool(getattr(wf_cfg, "watchdog_enabled", True))
        watchdog: Optional[WorkflowWatchdog] = None
        if watchdog_enabled:
            watchdog = WorkflowWatchdog(
                paths=paths,
                workflow_session_id=wf_session_id,
                control=control,
                poll_interval=float(getattr(wf_cfg, "heartbeat_check_interval_seconds", 5.0)),
                max_total_duration=max_total_duration,
            )
            watchdog.start()
        self._current_watchdog = watchdog
        self._current_control = control

        def _finish(status: str, error: Optional[str] = None) -> WorkflowRunResult:
            if watchdog is not None:
                watchdog.stop()
            wf_session.status = {
                "done": WorkflowRunStatus.DONE,
                "failed": WorkflowRunStatus.FAILED,
                "partial": WorkflowRunStatus.PARTIAL,
                "paused": WorkflowRunStatus.PAUSED,
                "cancelled": WorkflowRunStatus.CANCELLED,
            }.get(status, WorkflowRunStatus.FAILED)
            wf_session.error = error
            wf_session.save(paths)
            wf_session.append_event(paths, "workflow_end", {"status": status, "error": error})
            self._emit_hook("WorkflowEnd", {
                "workflow_name": wf.name,
                "workflow_session_id": wf_session_id,
                "status": status,
                "error": error,
                "total_duration": time.monotonic() - t_start,
            })
            wf_registry.unregister(wf_session_id)
            return WorkflowRunResult(
                workflow_name=wf.name,
                status=status,
                step_results=list(step_results.values()),
                total_duration=time.monotonic() - t_start,
                error=error,
                workflow_session_id=wf_session_id,
                output_dir=str(wf_output_dir),
            )

        # [具身改进 B3] 拓扑分层：同一层内互不依赖的步骤可以并发执行。
        # 层与层之间仍然严格按依赖顺序推进（下一层开始前，上一层已全部完成），
        # 不破坏 depends_on 语义，只是把"同层内"的串行遍历换成并发。
        try:
            batches = self._compute_parallel_batches(wf)
        except ValueError as e:
            return _finish("failed", error=str(e))

        # step_results 在并发批次内会被多个线程同时读写（gate-retry 重跑依赖步骤时
        # 会写回 step_results[dep_id]），用一把锁保护写操作，避免极端情况下的竞态。
        results_lock = threading.Lock()

        for batch_index, batch in enumerate(batches):
            if batch_index < wf_session.current_batch_index:
                continue  # resume：跳过已经跑过的批次

            if control.cancel_requested.is_set():
                self._mark_remaining_cancelled(batches[batch_index:], step_results)
                wf_session.append_event(paths, "cancelled", {"at_batch": batch_index})
                return _finish("cancelled", error="收到 cancel 信号")

            if control.pause_requested.is_set():
                wf_session.current_batch_index = batch_index
                wf_session.save(paths)
                wf_session.append_event(paths, "paused", {"at_batch": batch_index})
                R.print_info(f"[Workflow] ⏸️ 已暂停（第 {batch_index} 批次前），可通过 resume_workflow_run 续跑")
                return _finish("paused")

            # 只需要重跑本批次里还没有 DONE/SKIPPED/GATE_FAILED(已放弃) 的 step
            # （resume 场景下，一个批次可能部分完成——尽管当前实现里批次内
            # 中止只会发生在 pause/cancel，正常情况下一个批次要么整体跑完
            # 才推进 current_batch_index，要么整体没跑；这里的过滤是为了
            # 兼容未来"批次内单独暂停"的扩展，保持幂等）。
            pending_in_batch = [
                s for s in batch
                if step_results.get(s.id) is None or step_results[s.id].status not in (
                    StepStatus.DONE, StepStatus.SKIPPED, StepStatus.CANCELLED, StepStatus.REJECTED,
                )
            ]

            parallel_steps = [s for s in pending_in_batch if s.allow_parallel]
            serial_steps = [s for s in pending_in_batch if not s.allow_parallel]

            for step in serial_steps:
                self._run_one_step(step, step_results, inputs, results_lock)
                self._persist_progress(paths, wf_session, step_results)

            if parallel_steps:
                use_concurrency = (
                    getattr(wf_cfg, "parallel_enabled", True)
                    and len(parallel_steps) > 1
                    and getattr(wf_cfg, "max_parallel", 4) > 1
                )
                if not use_concurrency:
                    for step in parallel_steps:
                        self._run_one_step(step, step_results, inputs, results_lock)
                        self._persist_progress(paths, wf_session, step_results)
                else:
                    max_workers = min(
                        len(parallel_steps),
                        max(1, getattr(wf_cfg, "max_parallel", 4)),
                    )
                    R.print_info(
                        f"[Workflow] 并发执行本层 {len(parallel_steps)} 个步骤"
                        f"（worker={max_workers}）：{[s.id for s in parallel_steps]}"
                    )
                    with ThreadPoolExecutor(max_workers=max_workers) as pool:
                        futures = {
                            pool.submit(self._run_one_step, step, step_results, inputs, results_lock): step
                            for step in parallel_steps
                        }
                        for future in as_completed(futures):
                            # _run_one_step 内部已经把结果写进 step_results 并吞掉了异常
                            # （转成 FAILED StepResult），这里不需要再处理返回值/异常；
                            # 用 as_completed 只是为了等待本层全部完成再进入下一层。
                            future.result()
                    self._persist_progress(paths, wf_session, step_results)

            wf_session.current_batch_index = batch_index + 1
            wf_session.save(paths)

            if control.cancel_requested.is_set():
                self._mark_remaining_cancelled(batches[batch_index + 1:], step_results)
                wf_session.append_event(paths, "cancelled", {"at_batch": batch_index + 1})
                return _finish("cancelled", error="收到 cancel 信号")

        all_results = list(step_results.values())

        # 判断整体状态
        if any(sr.status == StepStatus.FAILED for sr in all_results):
            status = "partial" if any(sr.status == StepStatus.DONE for sr in all_results) else "failed"
        else:
            status = "done"

        return _finish(status)

    # ── Session/看护辅助 ──────────────────────────────────────────────────

    def _persist_progress(
        self,
        paths: "AgentPaths",
        wf_session: WorkflowSession,
        step_results: dict[str, StepResult],
    ) -> None:
        """把当前 step_results 快照写回 WorkflowSession 并落盘（增量持久化）。"""
        wf_session.step_results = dict(step_results)
        wf_session.save(paths)

    def _mark_remaining_cancelled(
        self,
        remaining_batches: list[list[WorkflowStep]],
        step_results: dict[str, StepResult],
    ) -> None:
        for batch in remaining_batches:
            for step in batch:
                if step.id not in step_results or step_results[step.id].status == StepStatus.PENDING:
                    step_results[step.id] = StepResult(step_id=step.id, status=StepStatus.CANCELLED)

    def _run_one_step(
        self,
        step: WorkflowStep,
        step_results: dict[str, StepResult],
        inputs: dict,
        results_lock: "threading.Lock",
    ) -> None:
        """
        执行单个步骤的完整流程（依赖检查 → 条件判断 → prompt 解析 → 执行 →
        写回 step_results），抽出来是因为并发批次和串行批次现在共用同一套逻辑
        （之前这些步骤直接摊在 run() 的 for 循环体里）。

        写回 step_results 时加锁：并发批次里多个线程会同时调用本方法，
        且 gate-retry 重跑前序步骤时会再次写 step_results[dep_id]，没有锁的话
        理论上存在竞态（dict 本身线程安全，但"先读 status 判断依赖是否完成，
        再写自己的结果"这个复合操作不是原子的）。
        """
        import mini_agent.ui.renderer as R

        with results_lock:
            R.print_info(f"[Workflow] 步骤：{step.id}（{step.name}）")
            dep_failed = [
                d for d in step.depends_on
                if step_results.get(d, StepResult(d, StepStatus.PENDING)).status
                   in (StepStatus.FAILED, StepStatus.PENDING)
            ]
            if dep_failed:
                R.print_warning(f"[Workflow] 步骤 {step.id} 因依赖失败被跳过：{dep_failed}")
                step_results[step.id] = StepResult(
                    step_id=step.id,
                    status=StepStatus.SKIPPED,
                    error=f"依赖步骤未完成：{dep_failed}",
                )
                return

            if step.condition and not self._eval_condition(step.condition, step_results):
                R.print_info(f"[Workflow] 步骤 {step.id} 条件不满足，跳过：{step.condition!r}")
                step_results[step.id] = StepResult(
                    step_id=step.id,
                    status=StepStatus.SKIPPED,
                )
                return

            try:
                resolved_prompt = self._resolve_prompt(step.prompt, step_results, inputs)
            except KeyError as e:
                step_results[step.id] = StepResult(
                    step_id=step.id,
                    status=StepStatus.FAILED,
                    error=f"Prompt 占位符缺失：{e}",
                )
                return

        # [workflow机制改进计划.md P4/P5] 人工审批门：在锁外阻塞等待，避免长时间
        # 占用 results_lock。前台同步执行时没有其它线程能调用 approve/reject，
        # 会一直等到 poll_timeout 后自动判 REJECTED（避免永久挂死）。
        if self._effective_require_approval(step):
            approved = self._await_step_approval(step)
            if approved is False:
                with results_lock:
                    step_results[step.id] = StepResult(
                        step_id=step.id,
                        status=StepStatus.REJECTED,
                        error="人工审批未通过或超时自动拒绝",
                    )
                return
            if approved is None:  # cancel
                with results_lock:
                    step_results[step.id] = StepResult(step_id=step.id, status=StepStatus.CANCELLED)
                return

        watchdog = getattr(self, "_current_watchdog", None)
        if watchdog is not None:
            watchdog.register_step_start(step.id, step.timeout)

        self._emit_hook("WorkflowStepStart", {
            "step_id": step.id, "step_name": step.name, "type": step.effective_type,
        })

        # 实际执行（LLM 调用等耗时操作）故意放在锁外：并发批次的核心收益就是
        # 让多个步骤的 LLM 调用真正同时在跑，锁只保护 step_results 的读写。
        try:
            sr = self._run_step_with_gate_retry(step, resolved_prompt, step_results, inputs, results_lock)
        finally:
            if watchdog is not None:
                watchdog.register_step_end(step.id)

        with results_lock:
            step_results[step.id] = sr
            status_icon = {"done": "✅", "skipped": "⏭️", "failed": "❌", "gate_failed": "🔄"}.get(
                sr.status.value, "❓"
            )
            R.print_info(
                f"[Workflow] {status_icon} 步骤 {step.id} 完成 "
                f"({sr.duration_seconds:.1f}s)"
                + (f" 评分：{int(sr.score * 100)}/100" if sr.score is not None else "")
            )
            wf_session = getattr(self, "_current_wf_session", None)
            paths = getattr(self, "_current_paths", None)
            if wf_session is not None and paths is not None:
                wf_session.append_event(paths, "step_end", {
                    "step_id": step.id,
                    "status": sr.status.value,
                    "duration_seconds": sr.duration_seconds,
                    "retries_used": sr.retries_used,
                })
            self._emit_hook("WorkflowStepEnd", {
                "step_id": step.id,
                "status": sr.status.value,
                "duration_seconds": sr.duration_seconds,
                "score": sr.score,
            })

    def _effective_require_approval(self, step: "WorkflowStep") -> bool:
        """
        [workflow机制改进计划.md P5] 计算某个 step 实际是否需要人工审批。

        step.require_approval=True 时始终生效（用户显式声明的意愿优先）。
        额外地，tool_call 类型 step 涉及外部副作用（直接调用工具，而非在
        独立 Agent 沙箱里执行），属于设计文档 3.3 节里说的"高风险 step"，
        默认也要求审批——除非 cfg.workflow.tool_call_step_auto_approve=True
        （显式打开"tool_call 自动放行"开关）。
        """
        wf_cfg = getattr(self._cfg, "workflow", None)
        return step_requires_approval(step, wf_cfg)

    def _await_step_approval(self, step: "WorkflowStep") -> Optional[bool]:
        """
        阻塞等待人工审批门放行。返回 True=通过，False=拒绝/超时自动拒绝，
        None=期间收到 cancel 信号。

        轮询间隔与超时均来自 cfg.workflow（approval_poll_interval_seconds /
        approval_wait_timeout_seconds），超时后默认判 REJECTED 而不是无限
        阻塞，避免前台同步执行（没有其它线程可以调用 approve）时永久挂死。
        """
        import mini_agent.ui.renderer as R

        control = getattr(self, "_current_control", None)
        wf_session = getattr(self, "_current_wf_session", None)
        paths = getattr(self, "_current_paths", None)
        wf_cfg = getattr(self._cfg, "workflow", None)
        poll_interval = float(getattr(wf_cfg, "approval_poll_interval_seconds", 3.0))
        wait_timeout = getattr(wf_cfg, "approval_wait_timeout_seconds", None)

        if control is None:
            # 没有 registry 上下文（如单测直接调用），跳过审批直接放行，
            # 避免破坏现有测试对同步调用的假设。
            return True

        control.pending_approval_step = step.id
        control.approved.clear()
        control.rejected.clear()
        if wf_session is not None and paths is not None:
            wf_session.status = WorkflowRunStatus.AWAITING_APPROVAL
            wf_session.pending_approval_step = step.id
            wf_session.save(paths)
            wf_session.append_event(paths, "approval_requested", {"step_id": step.id})

        R.print_warning(
            f"[Workflow] ⏳ 步骤 {step.id} 需要人工审批，等待 approve_workflow_step / "
            f"reject_workflow_step（workflow_session_id={getattr(wf_session, 'workflow_session_id', '?')}）"
        )

        waited = 0.0
        result: Optional[bool] = False
        while True:
            if control.cancel_requested.is_set():
                result = None
                break
            if control.approved.is_set():
                result = True
                break
            if control.rejected.is_set():
                result = False
                break
            if wait_timeout and waited >= wait_timeout:
                R.print_warning(f"[Workflow] 步骤 {step.id} 审批等待超时，自动判定为拒绝")
                result = False
                break
            time.sleep(poll_interval)
            waited += poll_interval

        control.pending_approval_step = None
        if wf_session is not None and paths is not None:
            wf_session.status = WorkflowRunStatus.RUNNING
            wf_session.pending_approval_step = None
            wf_session.save(paths)
            wf_session.append_event(paths, "approved" if result is True else ("cancelled" if result is None else "rejected"), {
                "step_id": step.id,
                "reason": control.rejection_reason if result is False else "",
            })
        return result

    # ── [具身改进 B3] 并行批次计算 ──────────────────────────────────────────────

    def _compute_parallel_batches(self, wf: WorkflowDef) -> list[list[WorkflowStep]]:
        """
        Kahn 算法的分层版本：每一层（batch）包含当前所有"依赖已全部完成"的
        步骤，层内步骤互相之间没有依赖边——这正是可以安全并发执行的条件
        （层与层之间仍然严格按依赖顺序推进）。

        和 _topological_sort() 的区别只是返回结构：那个返回扁平列表（单一
        全局顺序），这个返回"层"的列表，保留了"哪些步骤理论上互不依赖、
        可以同时跑"这个信息。循环依赖检测逻辑与 _topological_sort 一致。
        """
        step_map = {s.id: s for s in wf.steps}
        in_degree: dict[str, int] = {s.id: 0 for s in wf.steps}
        for step in wf.steps:
            for dep in step.depends_on:
                if dep not in step_map:
                    raise ValueError(f"步骤 {step.id!r} 依赖不存在的步骤 {dep!r}")
                in_degree[step.id] += 1

        dependents: dict[str, list[str]] = {s.id: [] for s in wf.steps}
        for step in wf.steps:
            for dep in step.depends_on:
                dependents[dep].append(step.id)

        ready = [s for s in wf.steps if in_degree[s.id] == 0]
        batches: list[list[WorkflowStep]] = []
        completed = 0

        while ready:
            batches.append(ready)
            completed += len(ready)
            next_ready: list[WorkflowStep] = []
            for step in ready:
                for dep_id in dependents[step.id]:
                    in_degree[dep_id] -= 1
                    if in_degree[dep_id] == 0:
                        next_ready.append(step_map[dep_id])
            ready = next_ready

        if completed != len(wf.steps):
            raise ValueError("工作流存在循环依赖，无法执行")
        return batches

    # ── 拓扑排序 ────────────────────────────────────────────────────────────

    def _topological_sort(self, wf: WorkflowDef) -> list[WorkflowStep]:
        """Kahn 算法拓扑排序，检测循环依赖。"""
        step_map = {s.id: s for s in wf.steps}
        in_degree = {s.id: 0 for s in wf.steps}
        for step in wf.steps:
            for dep in step.depends_on:
                if dep not in step_map:
                    raise ValueError(f"步骤 {step.id!r} 依赖不存在的步骤 {dep!r}")
                in_degree[step.id] = in_degree.get(step.id, 0) + 1

        # 入度为 0 的先入队
        from collections import deque
        queue = deque(s for s in wf.steps if in_degree[s.id] == 0)
        result: list[WorkflowStep] = []

        # 建立反向依赖图
        dependents: dict[str, list[str]] = {s.id: [] for s in wf.steps}
        for step in wf.steps:
            for dep in step.depends_on:
                dependents[dep].append(step.id)

        while queue:
            step = queue.popleft()
            result.append(step)
            for dep_id in dependents[step.id]:
                in_degree[dep_id] -= 1
                if in_degree[dep_id] == 0:
                    queue.append(step_map[dep_id])

        if len(result) != len(wf.steps):
            raise ValueError("工作流存在循环依赖，无法执行")
        return result

    # ── Prompt 占位符替换 ────────────────────────────────────────────────────

    def _resolve_prompt(
        self,
        prompt_template: str,
        step_results: dict[str, StepResult],
        inputs: dict,
    ) -> str:
        """
        替换 prompt 中的占位符：
          {step_id.output}  → 该步骤的输出文本
          {step_id.score}   → 该步骤的评分（0-100 整数字符串）
          {variable}        → inputs 中的对应值
        """
        def replacer(m: re.Match) -> str:
            key = m.group(1)
            # step_id.field 形式
            if "." in key:
                step_id, field = key.split(".", 1)
                sr = step_results.get(step_id)
                if sr is None:
                    raise KeyError(f"{step_id}.{field}")
                if field == "output":
                    return sr.output
                elif field == "score":
                    return str(int(sr.score * 100)) if sr.score is not None else "N/A"
                else:
                    raise KeyError(key)
            # 外部 inputs
            if key in inputs:
                return str(inputs[key])
            # 找不到：保持原样（不抛异常，避免破坏 prompt 中本来就有的大括号）
            return m.group(0)

        return re.sub(r'\{([^}]+)\}', replacer, prompt_template)

    # ── 条件判断 ────────────────────────────────────────────────────────────

    def _eval_condition(
        self,
        condition: str,
        step_results: dict[str, StepResult],
    ) -> bool:
        """
        执行条件表达式，如 "evaluate.score >= 6"。
        构建一个安全的局部变量命名空间，只暴露步骤结果的属性。
        """
        # 构建命名空间：step_id → SimpleNamespace(output=..., score=...)
        import types
        ns: dict[str, Any] = {}
        for step_id, sr in step_results.items():
            ns[step_id] = types.SimpleNamespace(
                output=sr.output,
                score=int(sr.score * 100) if sr.score is not None else 0,
                status=sr.status.value,
                passed=sr.status == StepStatus.DONE,
            )
        try:
            return bool(eval(condition, {"__builtins__": {}}, ns))  # noqa: S307
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.workflow.runner.WorkflowRunner._eval_condition')
            import mini_agent.ui.renderer as R
            R.print_warning(f"[Workflow] 条件表达式执行失败 {condition!r}: {e}，默认跳过步骤")
            return False

    # ── 步骤执行 ────────────────────────────────────────────────────────────

    def _run_step_with_gate_retry(
        self,
        step: WorkflowStep,
        resolved_prompt: str,
        step_results: dict[str, StepResult],
        inputs: dict,
        results_lock: "Optional[threading.Lock]" = None,
    ) -> StepResult:
        """
        执行步骤，如果是 evaluator 且质检不达标，
        根据 retry_on_gate_fail 对前序步骤进行重跑后再评估。

        重跑逻辑：
          1. evaluator 判定 GATE_FAILED
          2. 找到 evaluator 依赖的前序步骤（通常是被评估的那个步骤）
          3. 带着 evaluator 的反馈作为附加上下文重跑前序步骤
          4. 再次运行 evaluator，最多重跑 retry_on_gate_fail 次

        [具身改进 B3] results_lock：并发批次下，对 step_results 的写入（重跑
        依赖步骤后写回 dep 的新结果）需要加锁——传 None 时表示调用方明确知道
        不存在并发（如单测直接调用本方法），跳过加锁。
        """
        import mini_agent.ui.renderer as R

        sr = self._execute_step_with_error_retry(step, resolved_prompt, step_results)

        # 非 GATE_FAILED，或没有配置 retry，直接返回
        max_retry = step.retry_on_gate_fail
        if sr.status != StepStatus.GATE_FAILED or max_retry <= 0:
            return sr

        for retry in range(1, max_retry + 1):
            R.print_info(
                f"[Workflow] 🔄 质检门重试 {retry}/{max_retry}：重跑依赖步骤..."
            )

            # 重跑所有直接依赖步骤（带上评估反馈作为改进提示）
            evaluator_feedback = sr.output  # 上一次评估的意见
            for dep_id in step.depends_on:
                dep_step_def = next(
                    (s for s in self._current_wf_steps if s.id == dep_id), None
                )
                if dep_step_def is None:
                    continue

                # 在原 prompt 基础上追加评估反馈
                dep_prompt_base = self._resolve_prompt(
                    dep_step_def.prompt, step_results, inputs
                )
                dep_prompt_with_feedback = (
                    dep_prompt_base
                    + f"\n\n---\n**上一版本的质检意见（请针对性改进）：**\n{evaluator_feedback}"
                )

                R.print_info(f"[Workflow] 🔄 重跑步骤 {dep_id}（含反馈）")
                dep_sr = self._execute_step_with_error_retry(dep_step_def, dep_prompt_with_feedback, step_results)
                if results_lock is not None:
                    with results_lock:
                        step_results[dep_id] = dep_sr
                else:
                    step_results[dep_id] = dep_sr

            # 重新生成 evaluator 的 prompt（依赖步骤输出已更新）
            try:
                new_eval_prompt = self._resolve_prompt(step.prompt, step_results, inputs)
            except KeyError:
                break

            sr = self._execute_step_with_error_retry(step, new_eval_prompt, step_results)
            if sr.status == StepStatus.DONE:
                R.print_info(f"[Workflow] ✅ 质检门通过（第 {retry} 次重试）")
                break
            R.print_warning(
                f"[Workflow] 第 {retry} 次重试后仍未通过，"
                + ("继续重试..." if retry < max_retry else "已达最大重试次数")
            )

        return sr

    def _execute_step_with_error_retry(
        self,
        step: WorkflowStep,
        resolved_prompt: str,
        step_results: dict[str, StepResult],
    ) -> StepResult:
        """
        [workflow机制改进计划.md P4] 在 _execute_step_bounded（硬超时封装）
        外面再包一层"普通异常重试"：FAILED（非 GATE_FAILED）时，若
        step.retry_on_error > 0，按指数退避重跑，最多 retry_on_error 次。

        与 _run_step_with_gate_retry 的质检门重试是两套独立机制：那个只在
        evaluator 判定 GATE_FAILED 时触发，且是"重跑前序依赖步骤"；这里是
        "同一个 step 本身失败了就重跑自己"，处理网络超时/工具报错等瞬时故障。
        """
        import mini_agent.ui.renderer as R

        wf_cfg = getattr(self._cfg, "workflow", None)
        backoff = float(getattr(wf_cfg, "retry_on_error_backoff_seconds", 5.0))
        max_retry = max(0, step.retry_on_error)

        sr = self._execute_step_bounded(step, resolved_prompt, step_results)
        retries_used = 0
        while sr.status == StepStatus.FAILED and retries_used < max_retry:
            retries_used += 1
            wait_s = backoff * retries_used
            R.print_warning(
                f"[Workflow] 步骤 {step.id} 执行失败（{sr.error}），"
                f"{wait_s:.0f}s 后重试（{retries_used}/{max_retry}）"
            )
            time.sleep(wait_s)
            sr = self._execute_step_bounded(step, resolved_prompt, step_results)
        sr.retries_used = retries_used
        return sr

    def _execute_step_bounded(
        self,
        step: WorkflowStep,
        resolved_prompt: str,
        step_results: dict[str, StepResult],
    ) -> StepResult:
        """
        [workflow机制改进计划.md P3] 硬超时封装：在独立线程里跑 _execute_step，
        用 future.result(timeout=step.timeout) 强制不再等待。

        已知限制：Python 线程无法被安全强杀，超时后底层线程可能仍在后台
        跑完（比如卡在一次很慢的工具调用），只是 runner 不再等待其结果、
        把该 step 标记为 TIMEOUT 并继续推进后续批次。这是纯 Python 线程模型
        的固有限制，见改进计划文档"风险与兼容性说明"一节。
        """
        if not step.timeout:
            return self._execute_step(step, resolved_prompt, step_results)

        from concurrent.futures import ThreadPoolExecutor as _TPE
        import concurrent.futures as _cf

        t_start = time.monotonic()
        with _TPE(max_workers=1) as pool:
            future = pool.submit(self._execute_step, step, resolved_prompt, step_results)
            try:
                return future.result(timeout=step.timeout)
            except _cf.TimeoutError:
                import mini_agent.ui.renderer as R
                R.print_warning(f"[Workflow] ⏱️ 步骤 {step.id} 超过 {step.timeout}s 未完成，强制标记 TIMEOUT")
                return StepResult(
                    step_id=step.id,
                    status=StepStatus.TIMEOUT,
                    error=f"步骤执行超过 timeout={step.timeout}s",
                    duration_seconds=time.monotonic() - t_start,
                )

    def _build_error_context(
        self,
        step: WorkflowStep,
        resolved_prompt: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> dict:
        """
        [问题定位改进] 出错时快照 step/workflow 的关键上下文，与 error/traceback
        一起写进 StepResult.context，方便事后翻 session.json 就能定位"哪个
        workflow、哪个 step、什么类型、什么配置、prompt 大致长什么样"，不用
        再去反查代码或重新复现。只放排查有用、体积可控的信息：
          - resolved_prompt 只截断保留前 500 字符（避免超长 prompt 把
            session.json 撑得难以阅读，output/traceback 已经够长了）
          - 不收集 api_key 等敏感配置
        """
        wf_session = getattr(self, "_current_wf_session", None)
        prompt_preview = None
        if resolved_prompt:
            prompt_preview = resolved_prompt[:500]
            if len(resolved_prompt) > 500:
                prompt_preview += "...(截断)"

        ctx: dict = {
            "workflow_name": getattr(wf_session, "workflow_name", None),
            "workflow_session_id": getattr(wf_session, "workflow_session_id", None),
            "step_id": step.id,
            "step_name": step.name,
            "step_type": step.effective_type,
            "role": step.role,
            "skill_name": step.skill_name,
            "workflow_name_ref": step.workflow_name,   # sub_workflow 专用，避免与顶层 workflow_name 重名混淆
            "tool_name": step.tool_name,
            "model": step.model,
            "depends_on": list(step.depends_on),
            "allow_parallel": step.allow_parallel,
            "timeout": step.timeout,
            "retry_on_error": step.retry_on_error,
            "retry_on_gate_fail": step.retry_on_gate_fail,
            "prompt_file": step.prompt_file,
            "prompt_preview": prompt_preview,
        }
        if extra:
            ctx.update(extra)
        return ctx

    def _execute_step(
        self,
        step: WorkflowStep,
        resolved_prompt: str,
        step_results: dict[str, StepResult],
    ) -> StepResult:
        """
        执行单个步骤，返回 StepResult。

        evaluator 质检门逻辑：
          - 执行后提取评分
          - 若评分 < pass_threshold，标记 gate_failed=True（通过 error 字段传递）
          - 调用方（run()）若发现 gate_failed，可根据 retry_on_gate_fail 重跑前序步骤
        """
        t_start = time.monotonic()

        try:
            from . import executors as _executors
            executor = _executors.get_executor(step.effective_type)
            output = executor.execute(self, step, resolved_prompt)

            # 提取评分（只要 role 对应的 profile 是 evaluator 类型就提取；
            # sub_workflow/tool_call/human_input/script 均无 role，天然跳过）
            score = self._extract_step_score(step, output)

            # evaluator 质检门：评分不达标时标记为 GATE_FAILED
            gate_threshold = self._get_gate_threshold(step)
            if score is not None and gate_threshold is not None and score < gate_threshold:
                import mini_agent.ui.renderer as R
                R.print_warning(
                    f"[Workflow] ⚠️ 步骤 {step.id} 质检不达标："
                    f"{int(score*100)}/100 < {int(gate_threshold*100)}/100"
                )
                self._emit_hook("WorkflowGateFailed", {
                    "step_id": step.id, "score": score, "gate_threshold": gate_threshold,
                })
                return StepResult(
                    step_id=step.id,
                    status=StepStatus.GATE_FAILED,
                    output=output,
                    score=score,
                    error=f"质检评分不达标：{int(score*100)}/100（阈值 {int(gate_threshold*100)}/100）",
                    duration_seconds=time.monotonic() - t_start,
                )

            return StepResult(
                step_id=step.id,
                status=StepStatus.DONE,
                output=output,
                score=score,
                duration_seconds=time.monotonic() - t_start,
            )
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.workflow.runner.WorkflowRunner._execute_step')
            return StepResult(
                step_id=step.id,
                status=StepStatus.FAILED,
                error=str(e),
                error_type=type(e).__name__,
                traceback=_traceback.format_exc(),
                context=self._build_error_context(step, resolved_prompt),
                duration_seconds=time.monotonic() - t_start,
            )

    def _extract_step_score(self, step: WorkflowStep, output: str) -> "Optional[float]":
        """从步骤输出中提取评分（仅对 evaluator 类型角色）。"""
        if not step.role:
            return None
        from mini_agent.role_agents.feedback import extract_score
        try:
            from mini_agent.role_agents import get_dispatcher
            dispatcher = get_dispatcher()
            if dispatcher and dispatcher._loader:
                profile = dispatcher._loader.get(step.role)
                if profile and profile.role_type == "evaluator":
                    return extract_score(output)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.workflow.runner')
            pass
        # fallback：如果 role 名字包含 evaluator 也提分
        if "evaluator" in (step.role or "").lower():
            return extract_score(output)
        return None

    def _get_gate_threshold(self, step: WorkflowStep) -> "Optional[float]":
        """获取步骤的质检阈值（来自 profile.pass_threshold）。"""
        if not step.role:
            return None
        try:
            from mini_agent.role_agents import get_dispatcher
            dispatcher = get_dispatcher()
            if dispatcher and dispatcher._loader:
                profile = dispatcher._loader.get(step.role)
                if profile and profile.role_type == "evaluator":
                    return profile.pass_threshold
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.workflow.runner')
            pass
        return None

    def _execute_with_main_agent(self, step: WorkflowStep, prompt: str) -> str:
        """用独立的主 Agent 实例执行步骤（隔离历史，避免污染主会话）。"""
        from mini_agent.config import load_config
        from mini_agent.agent import Agent
        from mini_agent.permissions import PermissionGuard
        from mini_agent.tools import get_default_registry

        step_cfg = load_config(
            project_root=self._cfg.project_root,
            verbose=self._cfg.verbose,
            sandbox=self._cfg.sandbox,
            auto_approve=True,
            model=step.model or self._cfg.model,
            llm_provider=self._cfg.llm_provider,
            llm_base_url=self._cfg.llm_base_url,
            # [BUGFIX] 同 evaluator.py：继承 self._cfg 的 --debug-llm，而不是硬编码 False。
            debug_llm=getattr(self._cfg, "debug_llm", False),
            debug_llm_console=getattr(self._cfg, "debug_llm_console", False),
        )
        step_cfg.api_key = self._cfg.api_key
        step_cfg.max_turns = step.max_turns
        step_cfg.stream = False
        if step.timeout:
            step_cfg.request_timeout = step.timeout

        # [workflow机制改进计划.md P2] 数据聚合：把该 step 对应 Agent 的
        # session 数据（history/traces/temp/output/artifacts）绑到
        # .agent/workflow_sessions/<wf_session_id>/<agent_session_id>/ 下，
        # 而不是散落在全局 .agent/sessions/ 里。cfg.session_dir 是
        # SessionManager 已有的覆盖点（session_dir=None 时才走默认
        # AgentPaths().sessions_dir），这里传入 workflow_step_agent_dir 的
        # 上一级（workflow_session_dir），SessionManager 会自己在其下再建
        # 一层 <session.id>/——为了让目录名可预期、便于排查，把 step.id
        # 也编码进 session_dir 的路径里（agent_session_id 前缀），实际
        # session.id 仍由 SessionManager 随机生成，最终落盘路径形如
        # workflow_sessions/<wf_id>/step_<step.id>/<random_session_id>/。
        wf_session = getattr(self, "_current_wf_session", None)
        if wf_session is not None:
            from mini_agent.storage.paths import AgentPaths as _AP
            paths = getattr(self, "_current_paths", None) or _AP(project_root=self._cfg.project_root)
            agent_session_root = paths.workflow_step_agent_dir(
                wf_session.workflow_session_id, f"step_{step.id}"
            )
            # [BUGFIX] AppConfig.session_dir 是只读 property（代理
            # self.session.dir），没有 setter，直接赋值会抛
            # AttributeError: property 'session_dir' of 'AppConfig' object
            # has no setter。真正可写的字段是 step_cfg.session.dir。
            step_cfg.session.dir = agent_session_root

        guard = PermissionGuard(
            auto_approve=True,
            sandbox=self._cfg.sandbox,
            project_root=self._cfg.project_root,
        )
        # [workflow_directory_mode_design.md 阶段3] 若本次运行有本地资源包
        # （文件夹模式 workflow），把本地 skill_loader / agent_profile_loader
        # 传给该 step 的独立主 Agent 实例，使其在执行期间能加载 workflow
        # 私有的 skill（触发/skill_activate 工具）与 agent profile
        # （spawn_named_agent）。bundle 为 None（单文件模式）时行为与改动前
        # 完全一致（不传 skill_loader，agent profile 走全局单例）。
        bundle = getattr(self, "_current_resource_bundle", None)
        agent = Agent(
            cfg=step_cfg,
            guard=guard,
            registry=get_default_registry(),
            skill_loader=bundle.skill_loader if bundle else None,
            agent_profile_loader=bundle.agent_loader if bundle else None,
        )
        return agent.run_turn(prompt)

    def _execute_with_role_agent(self, step: WorkflowStep, prompt: str) -> str:
        """用指定角色 Agent 执行步骤。"""
        from mini_agent.role_agents import get_dispatcher

        dispatcher = get_dispatcher()
        if dispatcher is None:
            # 没有 dispatcher（如单元测试环境），回退到主 Agent
            return self._execute_with_main_agent(step, prompt)

        # [workflow_directory_mode_design.md 阶段3] 优先查 workflow 本地
        # agents/ 目录（文件夹模式），查不到再退回全局 dispatcher 的
        # profile loader——这样 step.role 既可以指向 workflow 私有的
        # agents/<role>.md，也兼容引用全局角色，不需要新增 step 类型。
        bundle = getattr(self, "_current_resource_bundle", None)
        profile = bundle.get_agent_profile(step.role) if bundle else None
        if profile is None:
            profile = dispatcher._loader.get(step.role)
        if profile is None:
            raise ValueError(f"找不到角色 Agent profile：{step.role!r}")

        # [workflow机制改进计划.md P2] 数据聚合：role agent 内部各执行函数
        # 均已支持 parent_session_dir 覆盖点（子 agent session 嵌套机制），
        # 直接复用，把该 step 的角色 Agent 数据也落到
        # workflow_sessions/<wf_id>/step_<step.id>/ 下。
        parent_session_dir = None
        wf_session = getattr(self, "_current_wf_session", None)
        if wf_session is not None:
            from mini_agent.storage.paths import AgentPaths as _AP
            paths = getattr(self, "_current_paths", None) or _AP(project_root=self._cfg.project_root)
            parent_session_dir = paths.workflow_step_agent_dir(
                wf_session.workflow_session_id, f"step_{step.id}"
            )

        # 根据 role_type 调用对应的执行函数
        if profile.role_type == "evaluator":
            from mini_agent.role_agents.evaluator import run_evaluator
            # workflow 步骤中 evaluator 只跑一次（循环由 runner 的上层逻辑控制）
            return run_evaluator(
                profile=profile,
                base_cfg=self._cfg,
                original_request=f"[工作流步骤：{step.name}]",
                agent_output=prompt,
                iteration=1,
                parent_session_dir=parent_session_dir,
            )
        elif profile.role_type == "coach":
            from mini_agent.role_agents.coach import run_coach
            return run_coach(
                profile=profile,
                base_cfg=self._cfg,
                tool_name="workflow_step",
                tool_input={"step_id": step.id, "step_name": step.name},
                tool_output=prompt,
                context=f"工作流步骤：{step.name}",
                parent_session_dir=parent_session_dir,
            )
        else:
            # custom：直接用 run_custom_role
            return dispatcher._run_custom_role(
                profile, prompt, f"工作流步骤：{step.name}",
                parent_session_dir=parent_session_dir,
            )