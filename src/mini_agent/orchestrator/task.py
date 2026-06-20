"""
orchestrator/task.py — Task 数据模型

定义并发任务系统的核心数据结构：
  TaskStatus  — 任务状态枚举
  TaskResult  — 任务执行结果
  Task        — 单个任务的完整描述（不可变配置）
  TaskRecord  — 任务运行时状态（可变）
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class TaskStatus(str, Enum):
    PENDING   = "pending"    # 已提交，等待执行
    RUNNING   = "running"    # 正在执行
    DONE      = "done"       # 成功完成
    FAILED    = "failed"     # 执行失败
    CANCELLED = "cancelled"  # 已取消


@dataclass
class TaskResult:
    """任务执行结果。"""
    output: str                  # 最终文本输出（成功时）
    error: Optional[str] = None  # 错误信息（失败时）
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    turns: int = 0

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class Task:
    """
    单个任务的不可变配置。
    由用户或主 Agent 创建，提交到 TaskManager 执行。
    """
    prompt: str                          # 任务指令
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""                       # 可选名称（便于显示）
    system_extra: str = ""               # 追加到 system prompt 的额外指令
    model: Optional[str] = None          # None = 继承父 Agent 的模型
    provider: Optional[str] = None       # None = 继承父 Agent 的 provider
    auto_approve: bool = True            # sub-agent 默认自动批准工具调用
    max_turns: int = 30
    depends_on: list[str] = field(default_factory=list)  # 依赖的 task id 列表
    tags: list[str] = field(default_factory=list)        # 自由标签（用于过滤）
    allowed_tools: Optional[list[str]] = None            # 限制可用工具名（None=不限制）
    allowed_tool_groups: Optional[list[str]] = None      # 限制可用工具分组（None=不限制）
    active_skills: list[str] = field(default_factory=list)  # 主 agent 当前激活的 skill 名称（Phase E，3.3）
    created_at: float = field(default_factory=time.time)

    # ── manifest 相关字段（W1，对应设计文档 8.1 节）──────────────────────────
    # 全部带默认值，保证现有调用方零迁移成本继续工作。
    initiator: str = "agent"             # 任务发起方："user" / "agent"
    goal: str = ""                       # 任务目标的结构化描述；为空时回退到 prompt
    acceptance_criteria: list[str] = field(default_factory=list)  # 验收标准

    def __post_init__(self):
        if not self.name:
            # 自动从 prompt 截取前 40 字符作为名称
            self.name = self.prompt[:40].replace("\n", " ").strip()
            if len(self.prompt) > 40:
                self.name += "…"


@dataclass
class TaskRecord:
    """
    任务的运行时状态（可变）。
    TaskManager 持有所有 TaskRecord，外部通过 task_id 查询。
    """
    task: Task
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[TaskResult] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    log_lines: list[str] = field(default_factory=list)  # 实时日志

    # ── manifest 相关运行时状态（W1）────────────────────────────────────────
    # 由 update_task_progress 工具主动写入，而非从 events.jsonl 被动推导。
    current_step: str = ""
    steps_done: list[str] = field(default_factory=list)
    steps_remaining: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    decision_log: list[dict] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    # manifest 落盘路径；由 SubAgent/调用方在拿到 session_id 后注入
    _manifest_path: Optional[Path] = field(default=None, repr=False, compare=False)

    @property
    def task_id(self) -> str:
        return self.task.id

    @property
    def elapsed(self) -> Optional[float]:
        if self.started_at is None:
            return None
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1)

    @property
    def is_terminal(self) -> bool:
        return self.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)

    def append_log(self, line: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_lines.append(f"[{ts}] {line}")

    def status_icon(self) -> str:
        return {
            TaskStatus.PENDING:   "⏳",
            TaskStatus.RUNNING:   "⚡",
            TaskStatus.DONE:      "✓",
            TaskStatus.FAILED:    "✗",
            TaskStatus.CANCELLED: "⊘",
        }[self.status]

    def status_color(self) -> str:
        return {
            TaskStatus.PENDING:   "dim",
            TaskStatus.RUNNING:   "cyan",
            TaskStatus.DONE:      "green",
            TaskStatus.FAILED:    "red",
            TaskStatus.CANCELLED: "yellow",
        }[self.status]

    # ── manifest 持久化（W1，对应设计文档 8.1 节）────────────────────────────

    def bind_manifest_path(self, path: Path) -> None:
        """注入 manifest.json 的落盘路径（由 SubAgent 在获得 session_id 后调用）。"""
        self._manifest_path = path

    def to_manifest_dict(self) -> dict:
        """序列化为设计文档 8.1 节描述的 task_manifest.json schema。"""
        outcome: Optional[dict] = None
        if self.is_terminal:
            artifacts: list[dict] = []
            lessons_generated: list[str] = []
            token_cost = {"input": 0, "output": 0}
            summary = ""
            if self.result is not None:
                summary = (self.result.output or "")[:500]
                token_cost = {
                    "input": self.result.input_tokens,
                    "output": self.result.output_tokens,
                }
            outcome = {
                "status": self.status.value,
                "summary": summary,
                "artifacts": artifacts,
                "unresolved": list(self.unresolved),
                "lessons_generated": lessons_generated,
                "token_cost": token_cost,
            }

        return {
            "id": self.task.id,
            "name": self.task.name,
            "initiator": self.task.initiator,
            "goal": self.task.goal or self.task.prompt,
            "acceptance_criteria": list(self.task.acceptance_criteria),
            "context_snapshot": {
                "related_files": [],
                "related_lessons": [],
                "parent_goal_id": None,
                "parent_task_id": None,
            },
            "progress": {
                "current_step": self.current_step,
                "steps_done": list(self.steps_done),
                "steps_remaining": list(self.steps_remaining),
                "blockers": list(self.blockers),
                "last_updated": time.time(),
            },
            "decision_log": list(self.decision_log),
            "outcome": outcome,
        }

    def write_manifest(self) -> Optional[Path]:
        """将当前 manifest 状态写入 manifest.json；未绑定路径时静默跳过。"""
        if self._manifest_path is None:
            return None
        try:
            self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self._manifest_path.write_text(
                json.dumps(self.to_manifest_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return self._manifest_path
        except Exception:
            return None

    def update_progress(
        self,
        current_step: str = "",
        steps_done: Optional[list[str]] = None,
        steps_remaining: Optional[list[str]] = None,
        blockers: Optional[list[str]] = None,
        note: str = "",
    ) -> None:
        """供 update_task_progress 工具调用：主动更新进度并立即落盘。"""
        if current_step:
            self.current_step = current_step
        if steps_done is not None:
            self.steps_done = list(steps_done)
        if steps_remaining is not None:
            self.steps_remaining = list(steps_remaining)
        if blockers is not None:
            self.blockers = list(blockers)
        if note:
            self.decision_log.append({
                "at": time.time(),
                "decision": note,
                "rationale": "",
                "alternatives_considered": [],
            })
        self.write_manifest()
