"""core/experience.py — 最小 Experience 定义。

`Goal → Action → Outcome → Experience` 链路（见
`next_doc/refactor_plan/02-executable-sprint-plan.md` Sprint 1 验收标准
第 1 条）里的最后一环：一次 Goal 执行结束后，把结果沉淀成一条可被检索的
Experience。

持久化/检索（`experience/store.py`、CLI 检索命令）是 Sprint 2
（见 `02-executable-sprint-plan.md` Sprint 2）的范围，本文件只定义结构，
不提前实现存储层。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .types import ExperienceSource


@dataclass
class Experience:
    """一次 Goal 执行结束后沉淀下来的经验记录。"""

    source: ExperienceSource
    goal_text: str
    status: str
    rounds_used: int
    final_report: str
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "goal_text": self.goal_text,
            "status": self.status,
            "rounds_used": self.rounds_used,
            "final_report": self.final_report,
            "created_at": self.created_at,
        }
