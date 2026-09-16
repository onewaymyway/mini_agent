"""perception/scheduling_diagnostics.py — 调度/推进诊断（只读快照）

看板"目标树"曾出现过"全部目标显示为暂停/已完成，没有任何进行中"的
困惑：`active` 是节点状态里唯一等价于"进行中"的状态（并没有单独的
`in_progress`），所以这种展示如果不是数据本身如此，通常对应下面三类
根因之一：

1. 全局调度开关 `operating_state.scheduling_paused=True`（看板"停止调度"
   按钮），AutonomousLoop 整体不再 tick。
2. Objective 处于 `paused_by_user`（用户显式暂停），只能靠
   `resume_user_pause()` 手动恢复，调度器不会自动拉回来——与
   `paused_for_fairness`（资源公平性让出，会自动 `resume_fairness()`）
   是两回事，看板此前没有区分展示。
3. Goal 本身是 active，但底下没有任何 active 的 Objective 子节点
   （`GoalBacklog.goals_missing_objective()`），也就是"还能继续往下
   拆解/深入，但还没有人去拆"的候选。

本模块把这三类信息汇总成一份纯只读快照，不修改任何状态，任何异常都
返回空结构而不是抛异常，风格与 `fairness_diagnostics.py` 一致。
"""

from __future__ import annotations

import time
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.perception.goal_backlog import GoalBacklog
    from mini_agent.storage.paths import AgentPaths


def _empty_snapshot() -> dict[str, Any]:
    return {
        "generated_at": time.time(),
        "scheduling_paused": False,
        "scheduling_paused_reason": "",
        "scheduling_paused_at": 0.0,
        "autonomous_loop_running": None,
        "active_goal_count": 0,
        "active_objective_count": 0,
        "user_paused_objectives": [],
        "fairness_paused_objectives": [],
        "goals_missing_objective": [],
        "orphaned_active_goals": [],
        "has_blocking_issue": False,
    }


def scheduling_diagnostics_snapshot(
    goal_backlog: Optional["GoalBacklog"],
    objective_executor: Optional[Any],
    paths: Optional["AgentPaths"],
    autonomous_loop: Optional[Any] = None,
) -> dict[str, Any]:
    """聚合"为什么目标树里看不到进行中目标"的诊断信息。

    - `goal_backlog`：用于读 active 计数 + `goals_missing_objective()`。
    - `objective_executor`：用于区分 `paused_by_user` / `paused_for_fairness`。
    - `paths`：用于读全局 `scheduling_paused` 开关。
    - `autonomous_loop`：可选，用于附带 tick 是否在运行（best-effort，
      拿不到就返回 None，不影响其余字段）。
    """
    snapshot = _empty_snapshot()

    try:
        if paths is not None:
            from mini_agent.perception.global_knowledge import load_self_profile

            profile = load_self_profile(paths)
            if profile is not None:
                state = profile.operating_state
                snapshot["scheduling_paused"] = bool(state.scheduling_paused)
                snapshot["scheduling_paused_reason"] = state.scheduling_paused_reason or ""
                snapshot["scheduling_paused_at"] = float(state.scheduling_paused_at or 0.0)
    except Exception:
        pass

    try:
        if autonomous_loop is not None:
            snapshot["autonomous_loop_running"] = bool(
                getattr(autonomous_loop, "is_running", lambda: None)()
                if callable(getattr(autonomous_loop, "is_running", None))
                else getattr(autonomous_loop, "_running", None)
            )
    except Exception:
        pass

    objective_info: dict[str, dict[str, Any]] = {}
    try:
        if goal_backlog is not None:
            snapshot["active_goal_count"] = len(goal_backlog.active_goals())
            snapshot["active_objective_count"] = len(goal_backlog.active_objectives())

            missing = goal_backlog.goals_missing_objective()
            snapshot["goals_missing_objective"] = [
                {
                    "id": g.id,
                    "title": g.title,
                    "priority": g.priority,
                    "last_touched_at": g.last_touched_at,
                }
                for g in missing
            ]

            for n in goal_backlog.all_nodes():
                if n.is_objective:
                    objective_info[n.id] = {"title": n.title, "goal_id": n.parent_id}
    except Exception:
        pass

    try:
        if goal_backlog is not None:
            # [核心根因排查] `add_goal()`（CLI `/agent goals add`、看板"新建
            # 目标"表单走的都是这条legacy路径）创建的 Goal 不会被挂到
            # "ultimate"根节点下面——它是一套独立于"🌳 目标树"层级体系
            # （ultimate/domain/stage/goal/objective，通过 add_node()/
            # decompose 建立）之外的扁平列表，只能被 active_goals() 这类
            # 全量扫描到，`get_tree()` 从 ultimate 出发按 children_ids
            # 遍历永远碰不到它。这类 Goal 明明 active、也在被
            # AutonomousLoop 正常调度推进，但在"🌳 目标树"视图里彻底不可见
            # ——表现出来就是"目标树里全是暂停/已完成的旧节点，看不到任何
            # 进行中的"，而实际的进行中目标其实在"📋 列表/看板视图"里。
            reachable: set = set()
            tree = goal_backlog.get_tree(None)
            if tree is not None:
                def _walk(t: dict) -> None:
                    n = t.get("node")
                    if n is not None:
                        reachable.add(n.id)
                    for c in t.get("children") or []:
                        _walk(c)
                _walk(tree)

            orphaned = [
                n for n in goal_backlog.all_nodes()
                if n.is_active and n.is_goal and n.id not in reachable
            ]
            snapshot["orphaned_active_goals"] = [
                {"id": g.id, "title": g.title, "priority": g.priority}
                for g in sorted(orphaned, key=lambda n: n.priority, reverse=True)
            ]
    except Exception:
        pass

    try:
        if objective_executor is not None:
            user_paused_ids = objective_executor.user_paused_objective_ids()
            fairness_paused_ids = objective_executor.fairness_paused_objective_ids()
            snapshot["user_paused_objectives"] = [
                {
                    "id": oid,
                    "title": objective_info.get(oid, {}).get("title", oid),
                    "goal_id": objective_info.get(oid, {}).get("goal_id"),
                }
                for oid in user_paused_ids
            ]
            snapshot["fairness_paused_objectives"] = [
                {
                    "id": oid,
                    "title": objective_info.get(oid, {}).get("title", oid),
                    "goal_id": objective_info.get(oid, {}).get("goal_id"),
                }
                for oid in fairness_paused_ids
            ]
    except Exception:
        pass

    snapshot["has_blocking_issue"] = bool(
        snapshot["scheduling_paused"]
        or snapshot["user_paused_objectives"]
        or snapshot["orphaned_active_goals"]
        or (snapshot["active_goal_count"] == 0 and snapshot["active_objective_count"] == 0)
    )
    return snapshot
