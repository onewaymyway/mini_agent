"""core/types.py — 跨 core/ 各模块共用的最小公共类型。

Sprint 1 范围内只定义 Goal 迁移链真正用到的类型别名/枚举，不提前定义
Self / World / Capability / Action / Simulation / Runtime 等尚未启动
迁移的概念所需的类型（见 `next_doc/refactor_plan/02-executable-sprint-plan.md`）。
"""

from __future__ import annotations

from typing import Literal

# Goal 生命周期状态。取值刻意与 `goal_mode/state.py::GoalState.status`
# 及 `goal_mode/runner.py::GoalRunResult.status` 的既有取值集合对齐，
# 避免 Adapter 转换时出现"新旧状态词不一一对应"的情况。
GoalStatus = Literal[
    "running",
    "done",
    "cancelled",
    "failed",
    "stuck",
    "max_rounds_exhausted",
]

# Experience 的来源标记：区分"这条经验来自哪条迁移链/子系统"，
# 便于 Sprint 2 检索时过滤，也便于未来 Memory 迁移链复用同一个
# Experience 存储时不与 Goal 链路产生的记录混淆。
ExperienceSource = Literal["goal_mode"]
