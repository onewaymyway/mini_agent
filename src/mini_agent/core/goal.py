"""core/goal.py — 新领域模型里的 GoalState。

注意与 `mini_agent.goal_mode.state.GoalState`（旧的、落盘用的运行时状态）
区分：本文件定义的是"新领域模型"里的 GoalState，字段更少，只保留跨子系统
（Goal / Experience）需要共享的最小信息，不包含旧 GoalState 里
`recent_progress_reasons` / `dead_ends` / `progress_scores` 等
goal_mode 内部实现细节字段——那些字段仍然只属于旧模块，不提升为领域概念。

两者的映射关系由 `core/adapter.py::GoalAdapter` 负责，调用方不应该直接
互相构造。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .types import GoalStatus


@dataclass
class GoalState:
    """新领域模型中的 Goal 状态快照（Sprint 1 最小版本）。"""

    goal_text: str
    acceptance_criteria: list[str] = field(default_factory=list)
    status: GoalStatus = "running"
    round: int = 0
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
