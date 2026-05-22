"""
orchestrator/task.py — Task 数据模型

定义并发任务系统的核心数据结构：
  TaskStatus  — 任务状态枚举
  TaskResult  — 任务执行结果
  Task        — 单个任务的完整描述（不可变配置）
  TaskRecord  — 任务运行时状态（可变）
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
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
    created_at: float = field(default_factory=time.time)

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
