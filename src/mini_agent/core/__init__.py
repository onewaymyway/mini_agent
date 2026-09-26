"""mini_agent.core — 架构收敛后的最小领域模型。

见 `next_doc/refactor_plan/02-executable-sprint-plan.md` Sprint 1：
本包只放 Goal 迁移链当前真正用得上的 4 个文件
（`types.py` / `events.py` / `goal.py` / `experience.py`）+
`adapter.py`（Adapter 协议），不是原方案 §52 列的全部 11 个文件。

其它核心概念（Self / World / Capability / Action / Simulation / Runtime）
的最小 dataclass，待对应 Phase 的迁移链启动时再补，不提前占位。
"""

from .adapter import Adapter
from .events import Event
from .experience import Experience
from .goal import GoalState
from .goal_adapter import GoalAdapter, goal_run_result_to_experience

__all__ = [
    "Adapter",
    "Event",
    "Experience",
    "GoalState",
    "GoalAdapter",
    "goal_run_result_to_experience",
]
