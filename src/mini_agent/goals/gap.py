"""goals/gap.py — Phase 5 Sprint 5-2：Gap 检测能力（第一版：规则 +
可选 LLM 判断，见 `06-phase5-goal-convergence-sprint-plan.md` Sprint
5-2 任务表）。

只做一件事：给定一个 `core.goal.GoalState`，读取
`state_manager.get_state("world"/"self")` 作为输入（Phase 4 目前
`world`/`self` 里 `world` 还是占位——按 Sprint 4-2 的说明，即使内容
为空也不应该报错，这里的处理方式是：拿不到就当作"这份信息暂不可用"
记进 `evidence`，不阻塞 problems/gap 的生成，也不替占位 State 编造
内容），推导出 `problems`/`gap` 两个字段并写回同一个 `GoalState`。

第一版刻意保持"简单规则"（对应任务表止损备注"不需要复杂算法"）：
- `current_state`/`ideal_state` 缺一项 → 记一条对应的 problem，
  提示这项信息还没采集/定义，而不是凭空猜一个。
- 两者都有且相同 → 视为已经达成，`problems`/`gap` 都是空列表。
- 两者不同：优先用 `acceptance_criteria` 里每一条尚未验证达成的条目
  作为 `gap`；没有 `acceptance_criteria` 时退化为一条"需要细化"的
  提示，不编造具体差距内容。

`llm_judge` 是可选的替换点（对应任务表"简单规则 + LLM 判断"里的后半
部分）：传入时只接管 `problems` 的生成（`gap` 仍走规则），签名是
`(goal, self_state, world_state) -> list[str]`，第一版不提供默认
实现（不像规则那样能保证确定性/可测试），调用方需要自己接 LLM。
"""

from __future__ import annotations

from typing import Callable, Optional

from mini_agent.core.goal import GoalState
from mini_agent.core.state_manager import StateManager, get_state_manager

LLMJudge = Callable[[GoalState, Optional[object], Optional[object]], "list[str]"]


def _detect_problems_rule_based(goal: GoalState) -> list[str]:
    problems: list[str] = []
    if not goal.current_state.strip():
        problems.append(
            "当前状态未采集（current_state 为空），无法判断与理想状态的差距"
        )
    if not goal.ideal_state.strip():
        problems.append(
            "理想状态未定义（ideal_state 为空），Gap 检测无法给出目标方向"
        )
    return problems


def _detect_gap_rule_based(goal: GoalState) -> list[str]:
    current = goal.current_state.strip()
    ideal = goal.ideal_state.strip()
    if current and ideal and current == ideal:
        # 当前状态已经等于理想状态，视为没有差距。
        return []
    if goal.acceptance_criteria:
        return [f"未验证达成：{c}" for c in goal.acceptance_criteria]
    if ideal:
        return [
            f"从「{current or '未知现状'}」到「{ideal}」之间的具体差距尚未"
            "细化（没有 acceptance_criteria 可供参考，建议先补充）"
        ]
    return []


def detect_gap(
    goal: GoalState,
    state_manager: Optional[StateManager] = None,
    llm_judge: Optional[LLMJudge] = None,
) -> GoalState:
    """检测 `goal` 的 problems/gap，原地写回并返回同一个 `GoalState`。

    对应 Sprint 5-2 验收标准："给定一个新 Goal 输入，系统能自动生成
    `problems` 和 `gap` 字段，而不需要用户手动填写"——调用方只需要
    准备好 `goal_text`/`current_state`/`ideal_state`/
    `acceptance_criteria`，其余两个字段由本函数负责生成。
    """
    manager = state_manager or get_state_manager()
    self_state = manager.get_state("self")
    world_state = manager.get_state("world")

    if llm_judge is not None:
        problems = list(llm_judge(goal, self_state, world_state))
    else:
        problems = _detect_problems_rule_based(goal)

    gap_items = _detect_gap_rule_based(goal)

    goal.problems = problems
    goal.gap = gap_items
    goal.evidence = {
        **goal.evidence,
        "gap_detection": {
            "self_state_available": self_state is not None,
            "world_state_available": world_state is not None,
        },
    }
    return goal
