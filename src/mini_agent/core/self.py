"""core/self.py — 新领域模型里的 SelfState（Self 概念的最小 dataclass）。

见 `next_doc/refactor_plan/03-sprint1.5-memory-perception-coupling-assessment.md`
"三、迁移优先级建议"第 1 条：`perception/self_model.py`
（`AgentSelfModel`）inbound=5，未触发止损阈值，风险特征与 Goal 迁移链
相似，是 Sprint 1.5 之后第一条按同样模式（Adapter + 单点接入 + trace）
启动的迁移链。

只保留跨子系统需要共享的最小信息（当前能力快照、活跃 skill 数、
session 起始时间），不包含 `AgentSelfModel` 里 `affordance_summary`/
`user_presence`/`internal_state` 这些 `perception/` 内部实现细节
（`AffordanceMap`/`BehaviorContext`/`AgentInternalState` 这些类型本身
还没有对应的 core 概念，不提前定义，遵循"待对应 Phase 的迁移链启动时
再补，不提前占位"的原则，见 `core/__init__.py` 文档字符串）。

与 `core.goal.GoalState` 的关系一样：本文件定义的是"新领域模型"里的
`SelfState`，与 `perception.self_model.AgentSelfModel`（旧的、
session 内部使用的聚合视图）是两个不同的对象，转换关系由
`core/self_adapter.py::SelfAdapter` 负责，调用方不应该直接互相构造。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class SelfState:
    """新领域模型中的 Self 状态快照（Self 迁移链最小版本）。"""

    capability_snapshot: dict[str, float] = field(default_factory=dict)
    active_skill_count: int = 0
    session_start_at: float = field(default_factory=time.time)
