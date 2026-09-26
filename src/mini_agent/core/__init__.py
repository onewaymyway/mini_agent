"""mini_agent.core — 架构收敛后的最小领域模型。

见 `next_doc/refactor_plan/02-executable-sprint-plan.md`：
Sprint 1 只放 Goal 迁移链当前真正用得上的 4 个文件
（`types.py` / `events.py` / `goal.py` / `experience.py`）+
`adapter.py`（Adapter 协议）+ `goal_adapter.py`（GoalAdapter 实现）；
Sprint 2 新增 `experience_store.py`（Experience 的最小 JSONL 持久化 +
检索），对应原方案 §52 列的 11 个文件里，本包仍只建当前迁移链真正
用得上的部分，不提前补全。

其它核心概念（Self / World / Capability / Action / Simulation / Runtime）
的最小 dataclass，待对应 Phase 的迁移链启动时再补，不提前占位。
"""

from .adapter import Adapter
from .events import Event
from .experience import Experience
from .experience_store import ExperienceStore
from .goal import GoalState
from .goal_adapter import GoalAdapter, goal_run_result_to_experience

__all__ = [
    "Adapter",
    "Event",
    "Experience",
    "ExperienceStore",
    "GoalState",
    "GoalAdapter",
    "goal_run_result_to_experience",
]
