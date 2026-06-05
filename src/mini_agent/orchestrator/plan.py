"""
orchestrator/plan.py — Agent 执行计划（轻量级任务树）

这不是 SubAgent 调度器，而是 Agent 在 agentic loop 中的结构化"工作记忆"：
- Agent 可以在执行任何任务前先创建 task 树，哪怕只有 2 个步骤
- 执行中途随时可以动态追加 task（来自主任务、子任务、或用户手动）
- 每个 task 有明确的创建来源（source）、父子关系（parent_id）、依赖关系（depends_on）
- task 树持续注入 system prompt，让 LLM 始终知道全局进度
- CLI 实时展示带层级、依赖的 task 树

设计原则：
  - 纯内存结构，不启动任何线程或子进程
  - 所有状态变更由主 agent 的工具调用驱动
  - task 树在每次 LLM 调用时序列化进 system prompt
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PlanTaskStatus(str, Enum):
    PENDING   = "pending"    # 等待执行
    RUNNING   = "running"    # 当前正在执行
    DONE      = "done"       # 已完成
    FAILED    = "failed"     # 执行失败
    SKIPPED   = "skipped"    # 因依赖失败被跳过


class TaskSource(str, Enum):
    """task 的创建来源，让用户和 LLM 都能理解 task 树是怎么生长的。"""
    PLAN    = "plan"    # 在 create_plan 时批量创建
    USER    = "user"    # 用户通过 /plan add 手动追加
    TASK    = "task"    # 某个正在执行的 task 在运行时动态追加的子任务


@dataclass
class PlanTask:
    """
    执行计划中的一个任务节点（纯数据，无线程）。

    字段说明：
      id          — 短 id，在 depends_on 和 parent_id 中引用
      title       — 展示用简短标题
      description — 详细说明，running 时注入 prompt
      parent_id   — 父任务 id，形成层级（父子关系）
      depends_on  — 前置依赖 id 列表（依赖关系，不一定是父子）
      source      — 创建来源：plan / user / task
      created_by  — 若 source==task，记录是哪个 task id 创建了本 task
    """
    title: str
    description: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])
    parent_id: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)

    # 来源追踪
    source: TaskSource = TaskSource.PLAN
    created_by: Optional[str] = None   # task id（仅 source==TASK 时有值）

    # 运行时状态
    status: PlanTaskStatus = PlanTaskStatus.PENDING
    result: str = ""
    error: str = ""

    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    @property
    def elapsed(self) -> Optional[float]:
        if self.started_at is None:
            return None
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1)

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            PlanTaskStatus.DONE,
            PlanTaskStatus.FAILED,
            PlanTaskStatus.SKIPPED,
        )

    def status_icon(self) -> str:
        return {
            PlanTaskStatus.PENDING:  "○",
            PlanTaskStatus.RUNNING:  "◉",
            PlanTaskStatus.DONE:     "✓",
            PlanTaskStatus.FAILED:   "✗",
            PlanTaskStatus.SKIPPED:  "—",
        }[self.status]

    def status_color(self) -> str:
        return {
            PlanTaskStatus.PENDING:  "dim",
            PlanTaskStatus.RUNNING:  "cyan",
            PlanTaskStatus.DONE:     "green",
            PlanTaskStatus.FAILED:   "red",
            PlanTaskStatus.SKIPPED:  "yellow",
        }[self.status]

    def source_badge(self) -> str:
        """返回简短来源标注，用于 CLI 展示。"""
        if self.source == TaskSource.USER:
            return "[user]"
        if self.source == TaskSource.TASK and self.created_by:
            return f"[from:{self.created_by}]"
        return ""


class ExecutionPlan:
    """
    Agent 的执行计划——一棵 PlanTask 的有向无环图。

    关系语义：
      parent_id   — 层级归属：t2 是 t1 的子步骤（UI 缩进展示）
      depends_on  — 执行顺序：t2 必须等 t1 完成后才能开始（可跨层级）

    两者可以独立使用：
      - 只有 parent_id 但没有 depends_on → 组织归属，不强制顺序
      - 只有 depends_on 但没有 parent_id → 顺序依赖，平行节点
      - 两者都有 → 最完整的表达
    """

    def __init__(self, goal: str = "") -> None:
        self.goal = goal
        self._tasks: dict[str, PlanTask] = {}
        self.created_at = time.time()

    # ── 任务管理 ──────────────────────────────────────────────────────────────

    def add(self, task: PlanTask) -> str:
        self._tasks[task.id] = task
        return task.id

    def get(self, task_id: str) -> Optional[PlanTask]:
        return self._tasks.get(task_id)

    def all_tasks(self) -> list[PlanTask]:
        return list(self._tasks.values())

    def children_of(self, task_id: str) -> list[PlanTask]:
        return [t for t in self._tasks.values() if t.parent_id == task_id]

    def roots(self) -> list[PlanTask]:
        return [t for t in self._tasks.values() if not t.parent_id]

    # ── 状态推进 ──────────────────────────────────────────────────────────────

    def start(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.status != PlanTaskStatus.PENDING:
            return False
        task.status = PlanTaskStatus.RUNNING
        task.started_at = time.time()
        return True

    def complete(self, task_id: str, result: str = "") -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.status != PlanTaskStatus.RUNNING:
            return False
        task.status = PlanTaskStatus.DONE
        task.result = result
        task.finished_at = time.time()
        self._propagate_skip()
        return True

    def fail(self, task_id: str, error: str = "") -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.status != PlanTaskStatus.RUNNING:
            return False
        task.status = PlanTaskStatus.FAILED
        task.error = error
        task.finished_at = time.time()
        self._propagate_skip()
        return True

    def _propagate_skip(self) -> None:
        """将依赖了失败/跳过 task 的 PENDING task 标记为 SKIPPED。"""
        terminal_failed = {
            t.id for t in self._tasks.values()
            if t.status in (PlanTaskStatus.FAILED, PlanTaskStatus.SKIPPED)
        }
        changed = True
        while changed:
            changed = False
            for task in self._tasks.values():
                if task.status == PlanTaskStatus.PENDING:
                    if any(dep in terminal_failed for dep in task.depends_on):
                        task.status = PlanTaskStatus.SKIPPED
                        terminal_failed.add(task.id)
                        changed = True

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def next_ready(self) -> Optional[PlanTask]:
        """返回下一个可以开始的 PENDING task（依赖已全部满足）。"""
        done_ids = {t.id for t in self._tasks.values() if t.status == PlanTaskStatus.DONE}
        for task in self._tasks.values():
            if task.status != PlanTaskStatus.PENDING:
                continue
            if all(dep in done_ids for dep in task.depends_on):
                return task
        return None

    def is_complete(self) -> bool:
        return all(t.is_terminal for t in self._tasks.values())

    def has_failures(self) -> bool:
        return any(t.status == PlanTaskStatus.FAILED for t in self._tasks.values())

    def stats(self) -> dict:
        tasks = list(self._tasks.values())
        return {
            "total":   len(tasks),
            "pending": sum(1 for t in tasks if t.status == PlanTaskStatus.PENDING),
            "running": sum(1 for t in tasks if t.status == PlanTaskStatus.RUNNING),
            "done":    sum(1 for t in tasks if t.status == PlanTaskStatus.DONE),
            "failed":  sum(1 for t in tasks if t.status == PlanTaskStatus.FAILED),
            "skipped": sum(1 for t in tasks if t.status == PlanTaskStatus.SKIPPED),
        }

    # ── Prompt 序列化 ──────────────────────────────────────────────────────────

    def to_prompt_block(self) -> str:
        """
        将 task 树序列化为注入 system prompt 的文本块。
        LLM 通过这个文本感知当前执行进度、依赖关系、以及下一步。
        """
        if not self._tasks:
            return ""

        stats = self.stats()
        lines = [
            "## Current execution plan",
            f"Goal: {self.goal}" if self.goal else "",
            (
                f"Progress: {stats['done']}/{stats['total']} done"
                + (f", {stats['running']} running" if stats["running"] else "")
                + (f", {stats['failed']} failed" if stats["failed"] else "")
                + (f", {stats['skipped']} skipped" if stats["skipped"] else "")
            ),
            "",
        ]

        # 树形渲染（根节点 → 递归子节点）
        for root in self.roots():
            self._render_task_prompt(root, lines, depth=0)

        # 当前正在执行
        running = [t for t in self._tasks.values() if t.status == PlanTaskStatus.RUNNING]
        if running:
            rt = running[0]
            lines.append("")
            src = rt.source_badge()
            lines.append(f"**Currently executing**: [{rt.id}] {rt.title}{(' ' + src) if src else ''}")
            if rt.description:
                lines.append(f"  {rt.description}")

        # 下一步提示
        next_task = self.next_ready()
        if next_task and not running:
            src = next_task.source_badge()
            lines.append("")
            lines.append(f"**Next step**: [{next_task.id}] {next_task.title}{(' ' + src) if src else ''}")

        # 已完成任务的结果（供后续步骤参考）
        done_with_results = [t for t in self._tasks.values()
                             if t.status == PlanTaskStatus.DONE and t.result]
        if done_with_results:
            lines.append("")
            lines.append("**Completed results** (available for subsequent tasks):")
            for t in done_with_results:
                lines.append(f"  [{t.id}] {t.title}: {t.result[:200]}")

        return "\n".join(line for line in lines if line is not None)

    def _render_task_prompt(self, task: PlanTask, lines: list, depth: int) -> None:
        indent = "  " * depth
        icon = task.status_icon()

        # 依赖标注
        dep_str = f" (after: {', '.join(task.depends_on)})" if task.depends_on else ""
        # 来源标注
        src_str = (" " + task.source_badge()) if task.source_badge() else ""

        lines.append(f"{indent}{icon} [{task.id}] {task.title}{dep_str}{src_str}")

        for child in self.children_of(task.id):
            self._render_task_prompt(child, lines, depth + 1)

    # ── CLI 展示用 ─────────────────────────────────────────────────────────────

    def to_display_lines(self) -> list[tuple[int, PlanTask]]:
        """返回 (depth, task) 的扁平列表，用于 CLI 渲染。"""
        result: list[tuple[int, PlanTask]] = []
        for root in self.roots():
            self._collect_display(root, result, 0)
        return result

    def _collect_display(self, task: PlanTask, out: list, depth: int) -> None:
        out.append((depth, task))
        for child in self.children_of(task.id):
            self._collect_display(child, out, depth + 1)


# ── 模块级单例 ────────────────────────────────────────────────────────────────

_current_plan: Optional[ExecutionPlan] = None


def get_plan() -> Optional[ExecutionPlan]:
    return _current_plan


def set_plan(plan: Optional[ExecutionPlan]) -> None:
    global _current_plan
    _current_plan = plan


def clear_plan() -> None:
    global _current_plan
    _current_plan = None
