"""core/goal_adapter.py — GoalAdapter：实现 `Adapter[Old, New]` 协议。

Old = `mini_agent.goal_mode.spec.GoalSpec`
New = `mini_agent.core.goal.GoalState`

按 `next_doc/refactor_plan/02-executable-sprint-plan.md` Sprint 1
"接入点选择"的要求，本模块**只**在 `goal_mode/runner.py` 的一个调用点
（`GoalRunner.run()` 的开始/结束）被使用，不改动 `executor.py` 内部逻辑，
也不在其它任何地方直接互相构造 `GoalSpec`/`GoalState`。

另外提供 `goal_run_result_to_experience`：把 `GoalRunResult`（`run()` 的
返回值，定义在 `goal_mode/runner.py`）转换成 `core.experience.Experience`，
对应验收标准第 1 条要求的完整链路
`Goal(old) → GoalAdapter → GoalState → 执行 → Outcome → Experience`。
这个转换函数不是 `Adapter[Old, New]` 协议的一部分（`GoalRunResult` 不是
"旧领域对象"，只是一次执行的结果），因此单独定义，不塞进 `GoalAdapter`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .adapter import Adapter
from .experience import Experience
from .goal import GoalState

if TYPE_CHECKING:
    from mini_agent.goal_mode.runner import GoalRunResult
    from mini_agent.goal_mode.spec import GoalSpec


class GoalAdapter(Adapter["GoalSpec", GoalState]):
    """`GoalSpec` <-> `GoalState` 的双向转换。"""

    @staticmethod
    def to_new(old: "GoalSpec") -> GoalState:
        return GoalState(
            goal_text=old.goal_text,
            acceptance_criteria=list(old.acceptance_criteria),
            status="running",
            round=0,
            version=old.version,
            created_at=old.created_at,
            updated_at=old.updated_at,
        )

    @staticmethod
    def to_old(new: GoalState) -> "GoalSpec":
        from mini_agent.goal_mode.spec import GoalSpec

        return GoalSpec(
            goal_text=new.goal_text,
            acceptance_criteria=list(new.acceptance_criteria),
            confirmed=True,
            version=new.version,
            created_at=new.created_at,
            updated_at=new.updated_at,
        )


def goal_run_result_to_experience(result: "GoalRunResult") -> Experience:
    """把 `GoalRunner.run()` 的返回值转换成一条 `Experience`。

    对应 Sprint 1 验收标准第 1 条链路里的 `Outcome → Experience` 一环。

    Phase 3 Sprint 3-1（见
    `next_doc/refactor_plan/04-phase3-experience-layer-sprint-plan.md`）
    补齐 `Experience` 新增的 §7 字段：`GoalRunResult` 目前只暴露
    `goal_spec/status/rounds_used/final_report/replan_proposal` 几个
    字段，能可靠填充的新字段只有 `action`/`reason`/`evidence`——
    `state_before`/`state_after`/`prediction`/`causal_hypothesis`/
    `confidence` 需要 Goal 执行过程中的中间状态快照，`GoalRunResult`
    这一层还拿不到，保持默认值（不臆造数据），留给后续 Sprint（比如
    Phase 4 统一 State 落地后）再补。
    """
    acceptance_criteria = (
        list(result.goal_spec.acceptance_criteria) if result.goal_spec else []
    )
    return Experience(
        source="goal_mode",
        goal_text=result.goal_spec.goal_text if result.goal_spec else "",
        status=result.status,
        rounds_used=result.rounds_used,
        final_report=result.final_report,
        action="goal_mode.run",
        reason=f"acceptance_criteria: {acceptance_criteria}" if acceptance_criteria else "",
        evidence={"replan_proposal": dict(result.replan_proposal)} if result.replan_proposal else {},
        lesson=result.final_report if result.status in ("stuck", "max_rounds_exhausted", "failed") else "",
    )
