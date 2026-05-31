"""
tools/plan.py — 执行计划工具集

Agent 通过这些工具在 agentic loop 中管理自己的执行计划。

工具列表：
  create_plan(goal, tasks)            — 创建计划（批量定义初始任务）
  add_task(id, title, ...)            — 动态追加任务（可在执行过程中调用）
  start_task(task_id)                 — 标记任务开始
  complete_task(task_id, result)      — 标记任务完成，记录结果
  fail_task(task_id, error)           — 标记任务失败
  get_plan_status()                   — 查看完整计划状态
  clear_plan()                        — 清除计划

关于 source（创建来源）：
  - "plan"  : 在 create_plan 时定义的初始任务
  - "task"  : 某个正在执行的任务发现需要额外步骤，动态追加的
  - "user"  : 用户通过命令行手动追加的

关于父子关系 vs 依赖关系：
  - parent_id  : 层级归属（子步骤属于哪个父任务，UI 缩进展示）
  - depends_on : 执行顺序（必须等哪些任务完成后才能开始）
  两者独立，可以单独使用，也可以组合使用。
"""

from __future__ import annotations

import json
from typing import Optional

from tools import tool
from orchestrator.plan import (
    ExecutionPlan, PlanTask, PlanTaskStatus, TaskSource,
    get_plan, set_plan, clear_plan,
)


# ── create_plan ───────────────────────────────────────────────────────────────

@tool(
    name="create_plan",
    description=(
        "Create a structured execution plan. "
        "Use this whenever a task involves 2 or more steps — even simple two-step "
        "workflows benefit from an explicit plan for visibility and error recovery. "
        "Define the goal and initial tasks upfront; you can always add more tasks "
        "later with add_task as you discover them during execution. "
        "After creating the plan, execute tasks in order: "
        "start_task → do the actual work → complete_task."
    ),
    schema={
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "Top-level goal (shown as plan header in the CLI)",
            },
            "tasks": {
                "type": "array",
                "description": (
                    "Initial task list. Each task has an id used for cross-referencing. "
                    "Use depends_on to enforce ordering; use parent_id to group sub-steps "
                    "under a parent task."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Short unique id, e.g. 'read', 't1', 'test'. Used in depends_on/parent_id.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Short task title shown in the CLI (50 chars max)",
                        },
                        "description": {
                            "type": "string",
                            "description": "What this task will do (shown when running)",
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "IDs of tasks that must be DONE before this one can start",
                        },
                        "parent_id": {
                            "type": "string",
                            "description": "ID of parent task — makes this a sub-step (indented in CLI)",
                        },
                    },
                    "required": ["id", "title"],
                },
            },
        },
        "required": ["goal", "tasks"],
    },
    requires_approval=False,
)
def create_plan(goal: str, tasks: list) -> str:
    plan = ExecutionPlan(goal=goal)
    for t in tasks:
        plan.add(PlanTask(
            id=t["id"],
            title=t["title"],
            description=t.get("description", ""),
            depends_on=t.get("depends_on", []),
            parent_id=t.get("parent_id"),
            source=TaskSource.PLAN,
            created_by=None,
        ))
    set_plan(plan)

    stats = plan.stats()
    next_task = plan.next_ready()
    return json.dumps({
        "created": True,
        "goal": goal,
        "task_count": stats["total"],
        "next_task": {"id": next_task.id, "title": next_task.title} if next_task else None,
        "message": (
            f"Plan created with {stats['total']} tasks. "
            + (f"Start with: start_task('{next_task.id}')" if next_task else "")
        ),
    }, ensure_ascii=False)


# ── add_task ──────────────────────────────────────────────────────────────────

@tool(
    name="add_task",
    description=(
        "Add a new task to the current execution plan. "
        "Call this at any point during execution when you discover additional steps "
        "that weren't known upfront. "
        "Set created_by to the currently running task's id so the plan tree shows "
        "where this task came from. "
        "Use parent_id to nest it under a parent task visually, and depends_on to "
        "enforce execution ordering."
    ),
    schema={
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Unique short id for this task",
            },
            "title": {
                "type": "string",
                "description": "Short task title",
            },
            "description": {
                "type": "string",
                "description": "What this task will do",
            },
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs of tasks that must complete before this one starts",
            },
            "parent_id": {
                "type": "string",
                "description": "ID of parent task for visual grouping (sub-steps)",
            },
            "created_by": {
                "type": "string",
                "description": (
                    "ID of the currently running task that is spawning this new task. "
                    "Leave empty if added from the top level or by the user."
                ),
            },
        },
        "required": ["id", "title"],
    },
    requires_approval=False,
)
def add_task(
    id: str,
    title: str,
    description: str = "",
    depends_on: Optional[list] = None,
    parent_id: Optional[str] = None,
    created_by: Optional[str] = None,
) -> str:
    plan = get_plan()
    if plan is None:
        return json.dumps({"error": "No active plan. Call create_plan first."})

    source = TaskSource.TASK if created_by else TaskSource.PLAN
    task = PlanTask(
        id=id,
        title=title,
        description=description,
        depends_on=depends_on or [],
        parent_id=parent_id,
        source=source,
        created_by=created_by,
    )
    plan.add(task)

    return json.dumps({
        "added": True,
        "task_id": id,
        "title": title,
        "source": source.value,
        "created_by": created_by,
        "parent_id": parent_id,
        "total_tasks": plan.stats()["total"],
    }, ensure_ascii=False)


# ── start_task ────────────────────────────────────────────────────────────────

@tool(
    name="start_task",
    description=(
        "Mark a plan task as started (pending → running). "
        "Call this immediately before beginning work on a task. "
        "The CLI will highlight it as the active step. "
        "You can only start tasks whose depends_on tasks are all done."
    ),
    schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The task id to start"},
        },
        "required": ["task_id"],
    },
    requires_approval=False,
)
def start_task(task_id: str) -> str:
    plan = get_plan()
    if plan is None:
        return json.dumps({"error": "No active plan."})

    task = plan.get(task_id)
    if task is None:
        return json.dumps({"error": f"Task '{task_id}' not found."})

    done_ids = {t.id for t in plan.all_tasks() if t.status == PlanTaskStatus.DONE}
    unmet = [dep for dep in task.depends_on if dep not in done_ids]
    if unmet:
        return json.dumps({
            "error": f"Cannot start '{task_id}': dependencies not yet done: {unmet}",
            "hint": "Complete prerequisite tasks first.",
        })

    ok = plan.start(task_id)
    if not ok:
        return json.dumps({"error": f"Cannot start '{task_id}' (status: {task.status.value})"})

    return json.dumps({
        "started": True,
        "task_id": task_id,
        "title": task.title,
        "description": task.description or None,
    }, ensure_ascii=False)


# ── complete_task ─────────────────────────────────────────────────────────────

@tool(
    name="complete_task",
    description=(
        "Mark a plan task as completed (running → done). "
        "Write a concise result summary — it will be visible to you in all subsequent "
        "LLM turns so later tasks can reference what was found or produced. "
        "Call this before moving to the next task."
    ),
    schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The task id to complete"},
            "result": {
                "type": "string",
                "description": "Brief summary of what was accomplished (visible in plan context for all subsequent steps)",
            },
        },
        "required": ["task_id"],
    },
    requires_approval=False,
)
def complete_task(task_id: str, result: str = "") -> str:
    plan = get_plan()
    if plan is None:
        return json.dumps({"error": "No active plan."})

    task = plan.get(task_id)
    if task is None:
        return json.dumps({"error": f"Task '{task_id}' not found."})

    ok = plan.complete(task_id, result=result)
    if not ok:
        return json.dumps({"error": f"Cannot complete '{task_id}' (status: {task.status.value})"})

    stats = plan.stats()
    next_task = plan.next_ready()
    return json.dumps({
        "completed": True,
        "task_id": task_id,
        "elapsed_s": task.elapsed,
        "plan_progress": f"{stats['done']}/{stats['total']}",
        "next_task": {"id": next_task.id, "title": next_task.title} if next_task else None,
        "plan_complete": plan.is_complete(),
    }, ensure_ascii=False)


# ── fail_task ─────────────────────────────────────────────────────────────────

@tool(
    name="fail_task",
    description=(
        "Mark a plan task as failed (running → failed). "
        "Tasks that depend on this task will be automatically skipped. "
        "Provide a clear error description."
    ),
    schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "error": {"type": "string", "description": "Reason for failure"},
        },
        "required": ["task_id"],
    },
    requires_approval=False,
)
def fail_task(task_id: str, error: str = "") -> str:
    plan = get_plan()
    if plan is None:
        return json.dumps({"error": "No active plan."})

    task = plan.get(task_id)
    if task is None:
        return json.dumps({"error": f"Task '{task_id}' not found."})

    ok = plan.fail(task_id, error=error)
    if not ok:
        return json.dumps({"error": f"Cannot fail '{task_id}' (status: {task.status.value})"})

    skipped = [t.id for t in plan.all_tasks() if t.status == PlanTaskStatus.SKIPPED]
    return json.dumps({
        "failed": True,
        "task_id": task_id,
        "error": error,
        "auto_skipped": skipped,
    }, ensure_ascii=False)


# ── get_plan_status ───────────────────────────────────────────────────────────

@tool(
    name="get_plan_status",
    description=(
        "Get the full current execution plan: all tasks, statuses, dependencies, "
        "parent-child relationships, sources, and results. "
        "Use this to review progress or decide what to work on next."
    ),
    schema={"type": "object", "properties": {}},
    requires_approval=False,
)
def get_plan_status() -> str:
    plan = get_plan()
    if plan is None:
        return json.dumps({"error": "No active plan. Call create_plan to start one."})

    tasks = []
    for task in plan.all_tasks():
        entry: dict = {
            "id": task.id,
            "title": task.title,
            "status": task.status.value,
            "source": task.source.value,
        }
        if task.created_by:
            entry["created_by"] = task.created_by
        if task.parent_id:
            entry["parent_id"] = task.parent_id
        if task.depends_on:
            entry["depends_on"] = task.depends_on
        if task.result:
            entry["result"] = task.result[:300]
        if task.error:
            entry["error"] = task.error
        if task.elapsed is not None:
            entry["elapsed_s"] = task.elapsed
        tasks.append(entry)

    next_task = plan.next_ready()
    return json.dumps({
        "goal": plan.goal,
        "stats": plan.stats(),
        "tasks": tasks,
        "next_ready": {"id": next_task.id, "title": next_task.title} if next_task else None,
        "is_complete": plan.is_complete(),
        "has_failures": plan.has_failures(),
    }, ensure_ascii=False, indent=2)


# ── clear_plan ────────────────────────────────────────────────────────────────

@tool(
    name="clear_plan",
    description="Clear the current execution plan. Use when starting an unrelated new task.",
    schema={"type": "object", "properties": {}},
    requires_approval=False,
)
def clear_plan_tool() -> str:
    clear_plan()
    return json.dumps({"cleared": True})
