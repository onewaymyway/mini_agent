"""
hybrid_exec/workflow_integration.py — 接入 workflow 的 hybrid_step 类型

对应 next_doc/hybrid_exec_design_plan.md §5。

**不修改 workflow 包任何源码**：通过 workflow/executors.py 已有的公开扩展点
`register_step_executor()`（与 myplugins/example_http_step.py 演示的机制
完全一致）注册一个新的 step 类型 `hybrid_step`。真正启用与否取决于
`myplugins/hybrid_step.py`（本次一并提供的薄插件文件）是否被扫描到——这与
`python_step`/`script` 靠 `cfg.workflow.python_step_enabled` 开关控制不同：
hybrid_step 的开关就是"插件文件在不在 myplugins/ 目录里"，删除该插件文件
即等效于禁用，不需要额外改 agent_config.json。

用法示例（workflow YAML）：

    steps:
      - id: extract_entities
        type: hybrid_step
        depends_on: [fetch_text]
        params:
          task_id: extract_entities_v1        # ScriptRepository 里的仓库 key，可跨 workflow 复用
          description: "从输入文本中抽取人名/机构名，返回 JSON 列表"
          input:                               # 可选：额外的字面量输入，会与上游 depends_on 输出合并
            hint: "只要中文人名"
          allow_tiers: [script, llm, agent]     # 可选，默认三层都允许
          max_script_repair_attempts: 2         # 可选，默认 2
          agent_fs_write_enabled: false         # 可选，默认 false（探索/修复用的 Agent 不允许写文件）
          result_required_keys: [entities]      # 可选：若脚本/LLM/Agent 应返回 dict，这里声明必须包含的顶层 key
          force_reexplore: false                # 可选：忽略仓库里已有脚本，强制重新探索

step 输出：HybridExecutor.run() 的 output（若是 dict 会被序列化成 JSON 文本
作为 step 的字符串输出，与其它 step 类型的输出形态一致，可用
`{extract_entities.output}` 占位符或下游 `ctx.input_json()` 消费）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mini_agent.workflow.executors import StepExecutor, register_step_executor
from mini_agent.workflow.schema import WorkflowStep

from .executor import HybridExecutor
from .explorer import AgentExplorer, LLMExplorer
from .fallback import FallbackExecutor
from .policy import ReexplorePolicy
from .recorder import RunRecorder
from .repairer import AgentRepairer, LLMRepairer
from .repository import ScriptRepository
from .runner import RunnerAppConfig, ScriptRunner
from .spec import ExecutionTier, TaskSpec


def _make_result_keys_validator(required_keys: "list[str]"):
    def _validator(output: Any) -> "tuple[bool, str]":
        if not isinstance(output, dict):
            return False, f"期望返回 dict（需包含 {required_keys}），实际是 {type(output).__name__}"
        missing = [k for k in required_keys if k not in output]
        if missing:
            return False, f"返回结果缺少必填字段：{missing}"
        return True, "字段齐全"

    return _validator


def _build_task_spec(step: WorkflowStep, upstream: dict) -> TaskSpec:
    params = step.params or {}
    task_id = params.get("task_id")
    if not task_id:
        raise ValueError(f"步骤 {step.id!r} 是 hybrid_step 类型但未在 params.task_id 指定任务标识")
    description = params.get("description") or step.prompt or step.name or step.id

    input_data: dict = {}
    if upstream:
        input_data["upstream"] = upstream
    extra_input = params.get("input")
    if isinstance(extra_input, dict):
        input_data.update(extra_input)

    allow_tiers_raw = params.get("allow_tiers") or ["script", "llm", "agent"]
    allow_tiers = tuple(ExecutionTier(t) for t in allow_tiers_raw)

    required_keys = params.get("result_required_keys") or []
    output_validator = _make_result_keys_validator(required_keys) if required_keys else None

    return TaskSpec(
        task_id=task_id,
        description=description,
        input_data=input_data,
        output_validator=output_validator,
        allow_tiers=allow_tiers,
        max_script_repair_attempts=int(params.get("max_script_repair_attempts", 2)),
        force_reexplore=bool(params.get("force_reexplore", False)),
        agent_fs_write_enabled=bool(params.get("agent_fs_write_enabled", False)),
        script_timeout_seconds=float(step.timeout or params.get("script_timeout_seconds", 60.0)),
    )


class HybridStepExecutor(StepExecutor):
    """type=hybrid_step：脚本优先、坏了先修脚本、修不好再降级 LLM/Agent
    的混合执行机制，见模块头部说明与设计文档 §4。"""

    def execute(self, runner: "Any", step: WorkflowStep, prompt: str) -> str:
        project_root = Path(runner._cfg.project_root)

        # 组装上游依赖的输出，供 TaskSpec.input_data["upstream"] 使用；
        # 与 PythonStepExecutor 一致地只暴露 depends_on 里声明过的 step。
        upstream_results = getattr(runner, "_current_step_results", None) or {}
        upstream = {}
        for sid in step.depends_on:
            r = upstream_results.get(sid)
            if r is None:
                continue
            text = getattr(r, "output", "")
            try:
                upstream[sid] = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                upstream[sid] = text

        task = _build_task_spec(step, upstream)

        app_cfg = RunnerAppConfig.from_mini_agent_config(runner._cfg)
        repo = ScriptRepository(project_root / ".agent" / "hybrid_exec" / "scripts")
        script_runner = ScriptRunner(app_cfg)
        # 全局 run 记录目录，跨 workflow/独立调用共享同一份统计口径
        # （对应 next_doc/hybrid_exec_design_plan.md §6）。
        run_recorder = RunRecorder(project_root / ".agent" / "hybrid_exec" / "runs")
        # [P4] 跨 run 主动重探索策略，默认不启用，需在 step.params 里显式
        # 打开（reexplore_enabled: true）。
        params = step.params or {}
        reexplore_policy = ReexplorePolicy(
            enabled=bool(params.get("reexplore_enabled", False)),
            min_samples=int(params.get("reexplore_min_samples", 5)),
            success_rate_threshold=float(params.get("reexplore_success_rate_threshold", 0.6)),
        )

        # 脚本执行的 session/output 目录挂到本次 workflow session 下，
        # 便于事后从 workflow 数据目录里找到这次 hybrid_step 的脚本产物
        # （脚本仓库本身是跨 workflow 共享的全局目录，这里只是单次执行的
        # 归档位置，两者不冲突）。
        from mini_agent.storage.paths import AgentPaths

        paths = getattr(runner, "_current_paths", None) or AgentPaths(project_root=project_root)
        wf_session = getattr(runner, "_current_wf_session", None)
        if wf_session is not None:
            session_dir = paths.workflow_session_dir(wf_session.workflow_session_id) / f"step_{step.id}_hybrid"
            output_dir = paths.ensure_workflow_session_output_dir(wf_session.workflow_session_id)
        else:
            session_dir = project_root / ".agent" / "hybrid_exec" / "runs" / task.task_id
            output_dir = session_dir

        original_run = script_runner.run

        def _run_with_dirs(script_path, t, **kwargs):
            kwargs.setdefault("session_dir", session_dir)
            kwargs.setdefault("output_dir", output_dir)
            return original_run(script_path, t, **kwargs)

        script_runner.run = _run_with_dirs  # type: ignore[method-assign]

        executor = HybridExecutor(
            repo=repo,
            script_runner=script_runner,
            llm_explorer=LLMExplorer(app_cfg),
            agent_explorer=AgentExplorer(app_cfg),
            llm_repairer=LLMRepairer(app_cfg),
            agent_repairer=AgentRepairer(app_cfg),
            fallback=FallbackExecutor(app_cfg),
            run_recorder=run_recorder,
            reexplore_policy=reexplore_policy,
        )

        result = executor.run(task)

        # 决策轨迹写一份到 output_dir，便于事后复盘（对应设计文档 §6）。
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / f"hybrid_step_{step.id}_trace.json").write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # 落盘失败不应该影响 step 本身的执行结果

        if not result.ok:
            raise RuntimeError(
                f"hybrid_step {step.id!r} 执行失败（task_id={task.task_id!r}，"
                f"最终尝试层级={result.tier_used.value}）：已耗尽 script/llm/agent 全部手段。"
                f"完整决策轨迹见 {output_dir / f'hybrid_step_{step.id}_trace.json'}"
            )

        output = result.output
        return output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, indent=2)

    def validate_step(self, step: WorkflowStep) -> "list[str]":
        errors = []
        params = step.params or {}
        if not params.get("task_id"):
            errors.append(f"步骤 {step.id!r} 是 hybrid_step 类型但未在 params.task_id 指定任务标识")
        allow_tiers_raw = params.get("allow_tiers")
        if allow_tiers_raw:
            valid = {t.value for t in ExecutionTier}
            bad = [t for t in allow_tiers_raw if t not in valid]
            if bad:
                errors.append(f"步骤 {step.id!r} 的 params.allow_tiers 包含非法值：{bad}（可选：{sorted(valid)}）")
        return errors


def register(cfg=None) -> None:
    """myplugins/ 插件统一入口，由 mini_agent.plugins.discover_and_register_plugins()
    调用；也可以在测试/脚本里直接 import 并调用完成注册。"""
    register_step_executor("hybrid_step", HybridStepExecutor())
