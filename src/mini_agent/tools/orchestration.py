"""
tools/orchestration.py — 编排工具

将 spawn_agent / spawn_agents 注册为内置工具，
主 Agent 可以通过工具调用来创建并发 Sub-Agent。

与 TaskManager 的连接通过模块级单例完成：
  - 主程序启动时调用 init_task_manager(cfg)
  - 工具函数通过 get_task_manager() 获取实例
"""

from __future__ import annotations

import json
from typing import Optional

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
    )
    task_id = mgr.submit(task)
    return json.dumps({
        "task_id": task_id,
        "name": task.name,
        "status": "pending",
        "message": f"Sub-agent spawned. Use get_task_status('{task_id}') to check progress.",
    }, indent=2)


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
    for t in tasks:
        task = Task(
            prompt=t["prompt"],
            name=t.get("name", ""),
            depends_on=t.get("depends_on", []),
            model=t.get("model"),
            system_extra=t.get("system_extra", ""),
            tags=t.get("tags", []),
        )
        task_id = mgr.submit(task)
        results.append({"task_id": task_id, "name": task.name})

    return json.dumps({
        "spawned": len(results),
        "tasks": results,
        "message": "Use list_tasks to monitor all tasks.",
    }, indent=2)


# ── get_task_status 工具 ──────────────────────────────────────────────────────

@tool(
    name="get_task_status",
    description=(
        "Get the current status and result of a sub-agent task by task_id. "
        "Returns status (pending/running/done/failed/cancelled), elapsed time, "
        "token usage, and the task output if completed."
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
        },
        "required": ["task_id"],
    },
    requires_approval=False,
)
def get_task_status(task_id: str, include_log: bool = True) -> str:
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
        data["output"] = rec.result.output[:3000]
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

    return json.dumps(data, indent=2)


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
    }, indent=2)


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
    }, indent=2)