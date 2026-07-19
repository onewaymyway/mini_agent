"""
tools/orchestration.py — 编排工具

将 spawn_agent / spawn_agents 注册为内置工具，
主 Agent 可以通过工具调用来创建并发 Sub-Agent。

与 TaskManager 的连接通过模块级单例完成：
  - 主程序启动时调用 init_task_manager(cfg)
  - 工具函数通过 get_task_manager() 获取实例

与"主 agent 当前激活的 skill 列表"的连接也走类似的模块级机制，但用
thread-local 而不是普通单例（Phase E，3.3，对应设计文档第 5 节
"SubAgent 信息继承"）：
  - Agent.__init__ 尾部调用 set_active_skills_provider(lambda: self.skill_loader.active)，
    注册到当前线程
  - spawn_agent / spawn_named_agent 工具通过 _get_active_skills() 读取当前线程的 provider，
    写入新建 Task 的 active_skills 字段，SubAgent 启动时据此激活同名 skill
  - 用 thread-local 是因为每个 SubAgent 在独立线程里构造自己的 Agent 实例，
    普通模块级变量会在并发场景下被互相覆盖（见下方实现注释）
"""

from __future__ import annotations

import json
import threading as _threading
from typing import Callable, Optional

from . import tool

# ── 模块级 TaskManager 单例 ───────────────────────────────────────────────────

_task_manager = None


def init_task_manager(cfg, max_workers: int = 4):
    """在主程序启动时调用，初始化全局 TaskManager。"""
    global _task_manager
    from mini_agent.orchestrator.task_manager import TaskManager
    from mini_agent.orchestrator.task_display import console
    from mini_agent.ui import renderer as R

    def _on_log(task_id: str, line: str) -> None:
        pass  # 日志写入 TaskRecord，不直接打印

    def _on_status(rec) -> None:
        from mini_agent.orchestrator.task import TaskStatus
        if rec.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            icon = rec.status_icon()
            color = rec.status_color()
            R.console.print(
                f"\n[{color}]{icon} Sub-agent [{rec.task_id}] {rec.status.value}:[/{color}] "
                f"{rec.task.name[:50]}"
            )

    _task_manager = TaskManager(
        base_cfg=cfg,
        max_workers=max_workers,
        on_log=_on_log,
        on_status_change=_on_status,
    )
    _task_manager.start()
    return _task_manager


def get_task_manager():
    """获取全局 TaskManager 实例。"""
    return _task_manager


# ── 模块级"当前激活 skill 列表"提供者（Phase E，3.3）─────────────────────────
#
# 用 threading.local 而非普通模块级变量：每个 SubAgent 在独立的 threading.Thread
# 中运行自己的 Agent 实例（见 orchestrator/sub_agent.py），如果 SubAgent 自己的
# Agent.__init__ 也调用 set_active_skills_provider()（递归 spawn 场景下确实可能），
# 普通模块级变量会被并发线程互相覆盖——主 agent 线程的 spawn_agent 调用可能读到
# 某个并发 SubAgent 线程刚刚注册的 provider，串台。thread-local 保证每个线程
# 看到的是"该线程所属 Agent 实例"注册的 provider，互不干扰。

_active_skills_local = _threading.local()


def set_active_skills_provider(provider: Optional[Callable[[], list[str]]]) -> None:
    """由 Agent.__init__ 调用，为当前线程注册一个返回"当前激活 skill 名称列表"的回调。"""
    _active_skills_local.provider = provider


def _get_active_skills() -> list[str]:
    """spawn_agent / spawn_named_agent 内部调用；当前线程未注册 provider 或调用失败时返回空列表。"""
    provider = getattr(_active_skills_local, "provider", None)
    if provider is None:
        return []
    try:
        return list(provider())
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.tools.orchestration._get_active_skills')
        return []


# ── spawn_agent 工具 ──────────────────────────────────────────────────────────

@tool(
    name="spawn_agent",
    description=(
        "Spawn a sub-agent to execute a task concurrently. "
        "The sub-agent runs in the background with its own conversation history. "
        "Returns the task_id immediately — use get_task_status to check progress. "
        "Use this when tasks are independent and can run in parallel. "
        "Use depends_on to create task chains (task B waits for task A to finish)."
    ),
    schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The full instruction for the sub-agent to execute",
            },
            "name": {
                "type": "string",
                "description": "Optional human-readable name for this task (shown in dashboard)",
            },
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of task_ids that must complete before this task starts",
            },
            "model": {
                "type": "string",
                "description": "Override the model for this sub-agent (e.g. 'claude-haiku-4-5')",
            },
            "system_extra": {
                "type": "string",
                "description": "Additional system prompt instructions for the sub-agent",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for grouping/filtering tasks",
            },
        },
        "required": ["prompt"],
    },
    requires_approval=False,
)
def spawn_agent(
    prompt: str,
    name: str = "",
    depends_on: Optional[list] = None,
    model: Optional[str] = None,
    system_extra: str = "",
    tags: Optional[list] = None,
) -> str:
    mgr = get_task_manager()
    if mgr is None:
        return "[error: TaskManager not initialized. Call init_task_manager() first.]"

    from mini_agent.orchestrator.task import Task
    task = Task(
        prompt=prompt,
        name=name,
        depends_on=depends_on or [],
        model=model,
        system_extra=system_extra,
        tags=tags or [],
        active_skills=_get_active_skills(),
    )
    task_id = mgr.submit(task)
    return json.dumps({
        "task_id": task_id,
        "name": task.name,
        "status": "pending",
        "message": f"Sub-agent spawned. Use get_task_status('{task_id}') to check progress.",
    }, indent=2,ensure_ascii=False)


@tool(
    name="list_agent_profiles",
    description=(
        "List predefined custom sub-agent profiles available via spawn_named_agent, "
        "including each profile's description and required/optional input parameters."
    ),
    schema={"type": "object", "properties": {}},
    requires_approval=False,
)
def list_agent_profiles() -> str:
    from mini_agent.orchestrator.agent_profiles import get_profile_loader
    loader = get_profile_loader()
    if loader is None or not loader.available:
        return "[no custom agent profiles found]"
    return json.dumps(loader.get_catalog(), indent=2, ensure_ascii=False)


@tool(
    name="spawn_named_agent",
    description=(
        "Spawn a predefined, specialized sub-agent (see list_agent_profiles for available "
        "agent_type values and their input schema). Pass structured `inputs` matching the "
        "profile's declared input parameters, and optional free-form `context` "
        "(e.g. relevant file excerpts, prior findings, background info) that will be "
        "injected into the sub-agent's prompt. Runs asynchronously in the background; "
        "returns a task_id — use get_task_status to check progress and get_task_result "
        "to retrieve the output."
    ),
    schema={
        "type": "object",
        "properties": {
            "agent_type": {
                "type": "string",
                "description": "Name of the predefined agent profile (see list_agent_profiles)",
            },
            "inputs": {
                "type": "object",
                "description": "Key-value parameters matching the agent profile's declared inputs",
            },
            "context": {
                "type": "string",
                "description": "Free-form context/background info passed to the sub-agent",
            },
            "name": {
                "type": "string",
                "description": "Optional human-readable name for this task (shown in dashboard)",
            },
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of task_ids that must complete before this task starts",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for grouping/filtering tasks",
            },
        },
        "required": ["agent_type", "inputs"],
    },
    requires_approval=False,
)
def spawn_named_agent(
    agent_type: str,
    inputs: Optional[dict] = None,
    context: str = "",
    name: str = "",
    depends_on: Optional[list] = None,
    tags: Optional[list] = None,
) -> str:
    mgr = get_task_manager()
    if mgr is None:
        return "[error: TaskManager not initialized. Call init_task_manager() first.]"

    from mini_agent.orchestrator.agent_profiles import (
        get_profile_loader, render_profile_prompt, validate_inputs,
    )
    loader = get_profile_loader()
    if loader is None:
        return "[error: agent profiles not initialized. Call init_agent_profiles() first.]"

    profile = loader.get(agent_type)
    if profile is None:
        available = loader.available
        return (
            f"[error: unknown agent_type '{agent_type}'. "
            f"Available profiles: {available}]"
        )

    inputs = inputs or {}
    err = validate_inputs(profile, inputs)
    if err:
        return f"[error: {err}]"

    prompt = render_profile_prompt(profile, inputs, context)

    from mini_agent.orchestrator.task import Task
    task = Task(
        prompt=prompt,
        name=name or f"{agent_type}",
        depends_on=depends_on or [],
        model=profile.model,
        provider=profile.provider,
        allowed_tools=profile.tools or None,
        allowed_tool_groups=profile.tool_groups or None,
        active_skills=_get_active_skills(),
        tags=(tags or []) + [f"agent:{agent_type}"],
    )
    task_id = mgr.submit(task)
    return json.dumps({
        "task_id": task_id,
        "agent_type": agent_type,
        "name": task.name,
        "status": "pending",
        "message": f"Sub-agent '{agent_type}' spawned. Use get_task_status('{task_id}') to check progress.",
    }, indent=2, ensure_ascii=False)


@tool(
    name="spawn_agents",
    description=(
        "Spawn multiple sub-agents concurrently in a single call. "
        "Each task runs independently (unless depends_on is set). "
        "Returns a list of task_ids. Use get_task_status or list_tasks to monitor."
    ),
    schema={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "List of task definitions",
                "items": {
                    "type": "object",
                    "properties": {
                        "prompt":      {"type": "string"},
                        "name":        {"type": "string"},
                        "depends_on":  {"type": "array", "items": {"type": "string"}},
                        "model":       {"type": "string"},
                        "system_extra":{"type": "string"},
                        "tags":        {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["prompt"],
                },
            },
        },
        "required": ["tasks"],
    },
    requires_approval=False,
)
def spawn_agents(tasks: list) -> str:
    mgr = get_task_manager()
    if mgr is None:
        return "[error: TaskManager not initialized.]"

    from mini_agent.orchestrator.task import Task
    results = []
    active_skills = _get_active_skills()
    for t in tasks:
        task = Task(
            prompt=t["prompt"],
            name=t.get("name", ""),
            depends_on=t.get("depends_on", []),
            model=t.get("model"),
            system_extra=t.get("system_extra", ""),
            tags=t.get("tags", []),
            active_skills=active_skills,
        )
        task_id = mgr.submit(task)
        results.append({"task_id": task_id, "name": task.name})

    return json.dumps({
        "spawned": len(results),
        "tasks": results,
        "message": "Use list_tasks to monitor all tasks.",
    }, indent=2,ensure_ascii=False)


# ── ensemble（多结果合并取优）工具 ────────────────────────────────────────────
#
# 两个粒度对应两个工具：
#   run_ensemble_llm       — 相同输入多次调用模型（粒度A）
#   run_ensemble_subagents — 多个 SubAgent 用不同上下文/提示词跑同一任务（粒度B）
# 是否真正执行受 cfg.ensemble.mode 与 granularity 开关控制：
#   - mode=off：直接拒绝，提示用户先开启配置
#   - mode=manual：只要 Agent 主动调用本工具，就视为显式触发，正常执行
#   - mode=auto / always：同样允许 Agent 主动调用（Agent 自己判断后调用本工具，
#     等价于规则/模型自判已经认为"值得"，工具内部不再重复判定一次）

@tool(
    name="run_ensemble_llm",
    description=(
        "Best-of-N at the LLM-call level: invoke the model multiple times on the SAME prompt "
        "(temperature jittered for diversity), then judge/merge the candidates into one final answer. "
        "Use this for a single open-ended question/sub-task where you want to reduce the chance of "
        "a one-off bad answer, WITHOUT spawning full sub-agents (cheaper, faster, no tool use). "
        "Only available when ensemble is enabled in config (mode != off) and granularity allows 'llm_call'."
    ),
    schema={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The question/instruction to answer."},
            "system": {"type": "string", "description": "Optional system prompt context for the calls."},
            "n": {"type": "integer", "description": "Number of candidates (default from config, usually 3)."},
            "execution": {"type": "string", "enum": ["serial", "parallel"], "description": "Default from config."},
            "strategy": {
                "type": "string",
                "enum": ["llm_judge", "first_success", "vote", "merge"],
                "description": "Judging strategy. Default from config (llm_judge for open-ended tasks).",
            },
        },
        "required": ["prompt"],
    },
    requires_approval=False,
)
def run_ensemble_llm(
    prompt: str,
    system: str = "",
    n: Optional[int] = None,
    execution: Optional[str] = None,
    strategy: Optional[str] = None,
) -> str:
    mgr = get_task_manager()
    cfg = mgr.base_cfg if mgr is not None else None
    if cfg is None:
        return json.dumps({"error": "config not available; ensemble requires an initialized TaskManager."})

    ens_cfg = getattr(cfg, "ensemble", None)
    if ens_cfg is None or ens_cfg.mode == "off":
        return json.dumps({"error": "ensemble is disabled (ensemble.mode=off). Enable it in config first."})
    if ens_cfg.granularity not in ("llm_call", "both"):
        return json.dumps({"error": f"llm_call granularity is disabled (ensemble.granularity={ens_cfg.granularity})."})

    from mini_agent.ensemble import run_llm_ensemble, classify_task_type

    task_type = classify_task_type(prompt)
    effective_strategy = strategy or ("first_success" if task_type == "verifiable" else ens_cfg.judge_strategy)

    result = run_llm_ensemble(
        cfg,
        messages=[{"role": "user", "content": prompt}],
        system=system or "",
        n=n,
        execution=execution,
        strategy=effective_strategy,
    )
    return json.dumps({
        "final_content": result.final_content,
        "chosen_idx": result.chosen_idx,
        "judge_strategy": result.judge_strategy,
        "judge_reason": result.judge_reason,
        "execution": result.execution,
        "early_stopped": result.early_stopped,
        "n_candidates": len(result.candidates),
        "total_latency_s": round(result.total_latency_s, 2),
    }, indent=2, ensure_ascii=False)


@tool(
    name="run_ensemble_subagents",
    description=(
        "Best-of-N at the sub-agent level: spawn N sub-agents with DIFFERENT context/personas "
        "(e.g. conservative vs creative vs self-critique) to independently work on the SAME task "
        "(can use tools, multi-turn), then judge/merge their final outputs into one result. "
        "Use this for a full task where the approach itself matters (multiple valid strategies), "
        "not just a single answer. More expensive than run_ensemble_llm. "
        "Only available when ensemble is enabled in config (mode != off) and granularity allows 'subagent'."
    ),
    schema={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The task for each sub-agent to complete."},
            "n": {"type": "integer", "description": "Number of sub-agents (default from config, usually 3)."},
            "execution": {"type": "string", "enum": ["serial", "parallel"], "description": "Default from config."},
            "strategy": {
                "type": "string",
                "enum": ["llm_judge", "first_success", "vote", "merge"],
                "description": "Judging strategy. Default from config.",
            },
            "variant_prompts": {
                "type": "array", "items": {"type": "string"},
                "description": "Optional: give each sub-agent a fully different prompt instead of personas.",
            },
        },
        "required": ["prompt"],
    },
    requires_approval=False,
)
def run_ensemble_subagents(
    prompt: str,
    n: Optional[int] = None,
    execution: Optional[str] = None,
    strategy: Optional[str] = None,
    variant_prompts: Optional[list] = None,
) -> str:
    mgr = get_task_manager()
    if mgr is None:
        return json.dumps({"error": "TaskManager not initialized."})
    cfg = mgr.base_cfg

    ens_cfg = getattr(cfg, "ensemble", None)
    if ens_cfg is None or ens_cfg.mode == "off":
        return json.dumps({"error": "ensemble is disabled (ensemble.mode=off). Enable it in config first."})
    if ens_cfg.granularity not in ("subagent", "both"):
        return json.dumps({"error": f"subagent granularity is disabled (ensemble.granularity={ens_cfg.granularity})."})

    from mini_agent.ensemble import run_subagent_ensemble, classify_task_type

    task_type = classify_task_type(prompt)
    effective_strategy = strategy or ("first_success" if task_type == "verifiable" else ens_cfg.judge_strategy)

    result = run_subagent_ensemble(
        cfg,
        prompt,
        n=n,
        execution=execution,
        strategy=effective_strategy,
        variant_prompts=variant_prompts,
        active_skills=_get_active_skills(),
    )
    return json.dumps({
        "final_content": result.final_content,
        "chosen_idx": result.chosen_idx,
        "judge_strategy": result.judge_strategy,
        "judge_reason": result.judge_reason,
        "execution": result.execution,
        "early_stopped": result.early_stopped,
        "n_candidates": len(result.candidates),
        "candidate_personas": [c.meta.get("persona", "") for c in result.candidates],
        "total_latency_s": round(result.total_latency_s, 2),
    }, indent=2, ensure_ascii=False)


# ── get_task_status 工具 ──────────────────────────────────────────────────────

@tool(
    name="get_task_status",
    description=(
        "Get the current status and result of a sub-agent task by task_id. "
        "Returns status (pending/running/done/failed/cancelled), elapsed time, "
        "token usage, and the task output if completed. "
        "If the output exceeds 3000 chars and full=False, the response includes "
        "truncated=true and full_length — call again with full=True to get everything."
    ),
    schema={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task_id returned by spawn_agent",
            },
            "include_log": {
                "type": "boolean",
                "description": "Include the last 10 log lines (default: false)",
            },
            "full": {
                "type": "boolean",
                "description": (
                    "If true, return the full task output without truncation "
                    "(default: false, output truncated to 3000 chars)"
                ),
            },
        },
        "required": ["task_id"],
    },
    requires_approval=False,
)
def get_task_status(task_id: str, include_log: bool = True, full: bool = False) -> str:
    mgr = get_task_manager()
    if mgr is None:
        return "[error: TaskManager not initialized.]"

    rec = mgr.get(task_id)
    if rec is None:
        return json.dumps({"error": f"Task '{task_id}' not found."})

    from mini_agent.orchestrator.task import TaskStatus as TS

    data: dict = {
        "task_id": rec.task_id,
        "name": rec.task.name,
        "status": rec.status.value,
        "elapsed_s": rec.elapsed,
    }
    if rec.result:
        output = rec.result.output or ""
        truncated = (not full) and len(output) > 3000
        data["output"] = output if full else output[:3000]
        if truncated:
            data["truncated"] = True
            data["full_length"] = len(output)
            data["hint"] = f"Output truncated to 3000 chars (full length: {len(output)}). Call get_task_status(task_id='{task_id}', full=True) to retrieve the complete output."
        if rec.result.error:
            data["error"] = rec.result.error   # 现在包含完整 traceback
        data["tokens"] = {
            "input": rec.result.input_tokens,
            "output": rec.result.output_tokens,
        }
        data["tool_calls"] = rec.result.tool_calls
        data["turns"] = rec.result.turns
    # failed 状态强制附带日志；其他状态由 include_log 控制
    if include_log or rec.status == TS.FAILED:
        data["log"] = rec.log_lines[-50:]

    return json.dumps(data, indent=2,ensure_ascii=False)


# ── list_tasks 工具 ───────────────────────────────────────────────────────────

@tool(
    name="list_tasks",
    description=(
        "List all sub-agent tasks and their current statuses. "
        "Optionally filter by status or tag."
    ),
    schema={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["pending", "running", "done", "failed", "cancelled"],
                "description": "Filter by status (optional)",
            },
            "tag": {
                "type": "string",
                "description": "Filter by tag (optional)",
            },
        },
    },
    requires_approval=False,
)
def list_tasks(status: Optional[str] = None, tag: Optional[str] = None) -> str:
    mgr = get_task_manager()
    if mgr is None:
        return "[error: TaskManager not initialized.]"

    from mini_agent.orchestrator.task import TaskStatus as TS
    status_filter = TS(status) if status else None
    records = mgr.list_records(status=status_filter, tag=tag)

    tasks = []
    for rec in records:
        entry: dict = {
            "task_id": rec.task_id,
            "name": rec.task.name,
            "status": rec.status.value,
            "elapsed_s": rec.elapsed,
        }
        if rec.result:
            entry["success"] = rec.result.success
            if rec.result.error:
                entry["error"] = rec.result.error
        tasks.append(entry)

    stats = mgr.stats()
    return json.dumps({
        "stats": stats,
        "tasks": tasks,
    }, indent=2,ensure_ascii=False)


# ── cancel_task 工具 ──────────────────────────────────────────────────────────

@tool(
    name="cancel_task",
    description="Cancel a pending or running sub-agent task.",
    schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task ID to cancel"},
        },
        "required": ["task_id"],
    },
    requires_approval=False,
)
def cancel_task(task_id: str) -> str:
    mgr = get_task_manager()
    if mgr is None:
        return "[error: TaskManager not initialized.]"
    success = mgr.cancel(task_id)
    if success:
        return json.dumps({"cancelled": True, "task_id": task_id})
    return json.dumps({"cancelled": False, "task_id": task_id,
                       "reason": "Task already completed or not found."})


# ── wait_for_tasks 工具 ───────────────────────────────────────────────────────

@tool(
    name="wait_for_tasks",
    description=(
        "Wait for one or more tasks to complete before continuing. "
        "Use this to synchronize after spawning parallel tasks."
    ),
    schema={
        "type": "object",
        "properties": {
            "task_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of task_ids to wait for (wait for all to complete)",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "Maximum seconds to wait (default: 300)",
            },
        },
        "required": ["task_ids"],
    },
    requires_approval=False,
)
def wait_for_tasks(task_ids: list, timeout_seconds: float = 300) -> str:
    mgr = get_task_manager()
    if mgr is None:
        return "[error: TaskManager not initialized.]"

    import time
    deadline = time.time() + timeout_seconds
    results = {}

    while True:
        all_done = True
        for tid in task_ids:
            rec = mgr.get(tid)
            if rec is None:
                results[tid] = {"error": "not found"}
            elif rec.is_terminal:
                results[tid] = {
                    "status": rec.status.value,
                    "elapsed_s": rec.elapsed,
                    "output": rec.result.output[:1000] if rec.result else "",
                    "success": rec.result.success if rec.result else False,
                }
            else:
                all_done = False

        if all_done or time.time() > deadline:
            break
        time.sleep(0.5)

    timed_out = time.time() > deadline and not all_done
    return json.dumps({
        "timed_out": timed_out,
        "results": results,
    }, indent=2,ensure_ascii=False)


# ── update_task_progress 工具 ─────────────────────────────────────────────────

@tool(
    name="update_task_progress",
    description=(
        "Actively record progress on a long-running task into its manifest.json "
        "(task narrative file). Call this periodically during multi-step sub-agent "
        "tasks to record what step you're on, what's been done, what remains, and "
        "any blockers. This is a deliberate checkpoint — pausing to reflect on "
        "progress improves execution quality on long tasks. The note (if provided) "
        "is appended to the task's decision_log."
    ),
    schema={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task_id to update (same as returned by spawn_agent)",
            },
            "current_step": {
                "type": "string",
                "description": "Short description of what's currently being worked on",
            },
            "steps_done": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Steps completed so far (replaces previous list if provided)",
            },
            "steps_remaining": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Steps still remaining (replaces previous list if provided)",
            },
            "blockers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Anything currently blocking progress (replaces previous list if provided)",
            },
            "note": {
                "type": "string",
                "description": (
                    "Optional free-form note about a decision made or observation, "
                    "appended to the task's decision_log"
                ),
            },
        },
        "required": ["task_id"],
    },
    requires_approval=False,
)
def update_task_progress(
    task_id: str,
    current_step: str = "",
    steps_done: Optional[list] = None,
    steps_remaining: Optional[list] = None,
    blockers: Optional[list] = None,
    note: str = "",
) -> str:
    mgr = get_task_manager()
    if mgr is None:
        return "[error: TaskManager not initialized.]"

    rec = mgr.get(task_id)
    if rec is None:
        return json.dumps({"error": f"Task '{task_id}' not found."})

    rec.update_progress(
        current_step=current_step,
        steps_done=steps_done,
        steps_remaining=steps_remaining,
        blockers=blockers,
        note=note,
    )

    return json.dumps({
        "updated": True,
        "task_id": task_id,
        "current_step": rec.current_step,
        "steps_done": rec.steps_done,
        "steps_remaining": rec.steps_remaining,
        "blockers": rec.blockers,
        "manifest_written": rec._manifest_path is not None,
    }, ensure_ascii=False)