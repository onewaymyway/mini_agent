"""
workflow/runner.py — WorkflowRunner 工作流执行引擎

核心职责：
  1. 按依赖顺序（拓扑排序）执行步骤
  2. 步骤间数据传递：{step_id.output} / {step_id.score} 占位符替换
  3. 条件判断：condition 表达式决定步骤是否执行
  4. 角色 Agent 绑定：step.role 指定由哪个角色执行
  5. 动态参数注入：run_workflow(inputs={"code": "..."}) 替换 {code}
  6. 运行时状态追踪：每步状态、耗时、输出

执行流程：
  WorkflowRunner.run(workflow_def, inputs)
    → _topological_sort()
    → for each step:
        → _resolve_prompt()     # 替换占位符
        → _eval_condition()     # 判断是否执行
        → _execute_step()       # 调用主 Agent 或角色 Agent
        → 记录 StepResult
    → 返回 WorkflowRunResult
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from .schema import WorkflowDef, WorkflowStep, StepResult, StepStatus

if TYPE_CHECKING:
    from mini_agent.config import AppConfig


@dataclass
class WorkflowRunResult:
    """一次工作流执行的完整结果。"""
    workflow_name: str
    status: str              # "done" | "failed" | "partial"
    step_results: list[StepResult] = field(default_factory=list)
    total_duration: float = 0.0
    error: Optional[str] = None

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
        return "\n".join(lines)


class WorkflowRunner:
    """工作流执行引擎。"""

    def __init__(self, cfg: "AppConfig") -> None:
        self._cfg = cfg

    def run(
        self,
        wf: WorkflowDef,
        inputs: Optional[dict] = None,
    ) -> WorkflowRunResult:
        """
        执行一个工作流。

        inputs: 外部传入的动态参数，如 {"code": "...", "lang": "python"}
                会替换步骤 prompt 中的 {code} / {lang} 占位符
        """
        import mini_agent.ui.renderer as R

        inputs = inputs or {}
        t_start = time.monotonic()
        step_results: dict[str, StepResult] = {}

        R.print_info(f"[Workflow] 开始执行：{wf.name}（共 {len(wf.steps)} 步）")

        # 供 _run_step_with_gate_retry 引用步骤定义
        self._current_wf_steps = wf.steps

        # 拓扑排序
        try:
            ordered = self._topological_sort(wf)
        except ValueError as e:
            return WorkflowRunResult(
                workflow_name=wf.name,
                status="failed",
                error=str(e),
                total_duration=time.monotonic() - t_start,
            )

        for step in ordered:
            R.print_info(f"[Workflow] 步骤：{step.id}（{step.name}）")

            # 依赖步骤是否都完成
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
                continue

            # 条件判断
            if step.condition and not self._eval_condition(step.condition, step_results):
                R.print_info(f"[Workflow] 步骤 {step.id} 条件不满足，跳过：{step.condition!r}")
                step_results[step.id] = StepResult(
                    step_id=step.id,
                    status=StepStatus.SKIPPED,
                )
                continue

            # 替换 prompt 占位符
            try:
                resolved_prompt = self._resolve_prompt(step.prompt, step_results, inputs)
            except KeyError as e:
                step_results[step.id] = StepResult(
                    step_id=step.id,
                    status=StepStatus.FAILED,
                    error=f"Prompt 占位符缺失：{e}",
                )
                continue

            # 执行步骤（含质检门 retry 循环）
            sr = self._run_step_with_gate_retry(
                step, resolved_prompt, step_results, inputs
            )
            step_results[step.id] = sr

            status_icon = {"done": "✅", "skipped": "⏭️", "failed": "❌", "gate_failed": "🔄"}.get(
                sr.status.value, "❓"
            )
            R.print_info(
                f"[Workflow] {status_icon} 步骤 {step.id} 完成 "
                f"({sr.duration_seconds:.1f}s)"
                + (f" 评分：{int(sr.score * 100)}/100" if sr.score is not None else "")
            )

        total_duration = time.monotonic() - t_start
        all_results = list(step_results.values())

        # 判断整体状态
        if any(sr.status == StepStatus.FAILED for sr in all_results):
            status = "partial" if any(sr.status == StepStatus.DONE for sr in all_results) else "failed"
        else:
            status = "done"

        return WorkflowRunResult(
            workflow_name=wf.name,
            status=status,
            step_results=all_results,
            total_duration=total_duration,
        )

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
    ) -> StepResult:
        """
        执行步骤，如果是 evaluator 且质检不达标，
        根据 retry_on_gate_fail 对前序步骤进行重跑后再评估。

        重跑逻辑：
          1. evaluator 判定 GATE_FAILED
          2. 找到 evaluator 依赖的前序步骤（通常是被评估的那个步骤）
          3. 带着 evaluator 的反馈作为附加上下文重跑前序步骤
          4. 再次运行 evaluator，最多重跑 retry_on_gate_fail 次
        """
        import mini_agent.ui.renderer as R

        sr = self._execute_step(step, resolved_prompt, step_results)

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
                dep_sr = self._execute_step(dep_step_def, dep_prompt_with_feedback, step_results)
                step_results[dep_id] = dep_sr

            # 重新生成 evaluator 的 prompt（依赖步骤输出已更新）
            try:
                new_eval_prompt = self._resolve_prompt(step.prompt, step_results, inputs)
            except KeyError:
                break

            sr = self._execute_step(step, new_eval_prompt, step_results)
            if sr.status == StepStatus.DONE:
                R.print_info(f"[Workflow] ✅ 质检门通过（第 {retry} 次重试）")
                break
            R.print_warning(
                f"[Workflow] 第 {retry} 次重试后仍未通过，"
                + ("继续重试..." if retry < max_retry else "已达最大重试次数")
            )

        return sr

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
            if step.role:
                output = self._execute_with_role_agent(step, resolved_prompt)
            else:
                output = self._execute_with_main_agent(step, resolved_prompt)

            # 提取评分（只要 role 对应的 profile 是 evaluator 类型就提取）
            score = self._extract_step_score(step, output)

            # evaluator 质检门：评分不达标时标记为 GATE_FAILED
            gate_threshold = self._get_gate_threshold(step)
            if score is not None and gate_threshold is not None and score < gate_threshold:
                import mini_agent.ui.renderer as R
                R.print_warning(
                    f"[Workflow] ⚠️ 步骤 {step.id} 质检不达标："
                    f"{int(score*100)}/100 < {int(gate_threshold*100)}/100"
                )
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
            return StepResult(
                step_id=step.id,
                status=StepStatus.FAILED,
                error=str(e),
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
        except Exception:
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
        except Exception:
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
            debug_llm=False,
        )
        step_cfg.api_key = self._cfg.api_key
        step_cfg.max_turns = step.max_turns
        step_cfg.stream = False
        if step.timeout:
            step_cfg.request_timeout = step.timeout

        guard = PermissionGuard(
            auto_approve=True,
            sandbox=self._cfg.sandbox,
            project_root=self._cfg.project_root,
        )
        agent = Agent(cfg=step_cfg, guard=guard, registry=get_default_registry())
        return agent.run_turn(prompt)

    def _execute_with_role_agent(self, step: WorkflowStep, prompt: str) -> str:
        """用指定角色 Agent 执行步骤。"""
        from mini_agent.role_agents import get_dispatcher

        dispatcher = get_dispatcher()
        if dispatcher is None:
            # 没有 dispatcher（如单元测试环境），回退到主 Agent
            return self._execute_with_main_agent(step, prompt)

        # 从 dispatcher 中找到对应的 profile
        profile = dispatcher._loader.get(step.role)
        if profile is None:
            raise ValueError(f"找不到角色 Agent profile：{step.role!r}")

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
            )
        else:
            # custom：直接用 run_custom_role
            return dispatcher._run_custom_role(profile, prompt, f"工作流步骤：{step.name}")
