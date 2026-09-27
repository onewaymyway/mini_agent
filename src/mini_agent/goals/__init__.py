"""goals/ — Phase 5 Sprint 5-2 新增：Gap 检测能力。

见 `next_doc/refactor_plan/06-phase5-goal-convergence-sprint-plan.md`
Sprint 5-2。这是一个新增的最小包，只放"根据 `core.goal.GoalState` +
`StateManager` 里的 World/Self 状态推导 problems/gap"这一件事，不是
`goal_mode/`（旧的、交互式单 Goal 执行循环）的替代品，也不涉及
`perception/goal_backlog.py`（Sprint 5.0.5 已确认暂缓，见同一份计划
文档"Sprint 5.0.5 执行记录"）。
"""

from .gap import detect_gap

__all__ = ["detect_gap"]
