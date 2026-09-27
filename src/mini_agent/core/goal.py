"""core/goal.py — 新领域模型里的 GoalState。

注意与 `mini_agent.goal_mode.state.GoalState`（旧的、落盘用的运行时状态）
区分：本文件定义的是"新领域模型"里的 GoalState，字段更少，只保留跨子系统
（Goal / Experience）需要共享的最小信息，不包含旧 GoalState 里
`recent_progress_reasons` / `dead_ends` / `progress_scores` 等
goal_mode 内部实现细节字段——那些字段仍然只属于旧模块，不提升为领域概念。

两者的映射关系由 `core/adapter.py::GoalAdapter` 负责，调用方不应该直接
互相构造。

Phase 5 Sprint 5-2（见
`next_doc/refactor_plan/06-phase5-goal-convergence-sprint-plan.md`）
按原文 §8 补充 `current_state`/`ideal_state`/`problems`/`gap`/
`constraints`/`resources`/`priority`/`evidence`/`deadline` 九个字段，
供 `goals/gap.py::detect_gap()` 读写。全部给了不破坏现有构造方式的
默认值——`GoalAdapter.to_new()`/`to_old()`（Sprint 1）用关键字参数
显式构造，不受新增字段影响，无需同步修改。这几个新字段目前只是
"结构已就位"，`GoalAdapter` 暂不填充（`GoalSpec`（旧）里没有对应
概念，硬填会是编造数据），继续留空，等后续真正有 Goal 执行链路产出
这些数据时再考虑要不要接入 Adapter。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .types import GoalStatus


@dataclass
class GoalState:
    """新领域模型中的 Goal 状态快照（Sprint 1 最小版本 + Sprint 5-2 补充）。"""

    goal_text: str
    acceptance_criteria: list[str] = field(default_factory=list)
    status: GoalStatus = "running"
    round: int = 0
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # ── Sprint 5-2 新增（原文 §8："当前状态 + 理想状态 + 问题 + 差距 +
    # 计划 + 行动 + 结果"这套语义里的前几项）──────────────────────────
    current_state: str = ""
    ideal_state: str = ""
    problems: list[str] = field(default_factory=list)
    gap: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    priority: Optional[int] = None
    evidence: dict = field(default_factory=dict)
    deadline: Optional[float] = None

