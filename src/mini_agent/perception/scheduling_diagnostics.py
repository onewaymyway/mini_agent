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
        "autonomous_loop_last_tick_at": 0.0,
        "autonomous_loop_tick_count": 0,
        "active_goal_count": 0,
        "active_objective_count": 0,
        "user_paused_objectives": [],
        "fairness_paused_objectives": [],
        "goals_missing_objective": [],
        "orphaned_active_goals": [],
        "pending_goal_proposals": [],
        "tree_expansion_candidates": [],
        "tree_pending_decompose_candidates": [],
        "has_blocking_issue": False,
    }


def scheduling_diagnostics_snapshot(
    goal_backlog: Optional["GoalBacklog"],
    objective_executor: Optional[Any],
    paths: Optional["AgentPaths"],
    autonomous_loop: Optional[Any] = None,
) -> dict[str, Any]:
    """聚合"为什么目标树里看不到进行中目标"的诊断信息。

    - `goal_backlog`：用于读 active 计数 + `goals_missing_objective()` +
      `agent_derived` 的 draft 候选（backlog 耗尽后由
      `AutonomousLoop._maybe_propose_goals_on_exhaustion()` 生成，等待
      用户 `/agent goals accept` 确认）。
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
            # [bugfix] 之前这里探测 `is_running()`/`_running`，但
            # `AutonomousLoop` 根本没有这两个成员——`getattr(..., None)`
            # 拿到的默认值必然是 falsy，导致这个字段无论循环是否真的在跑
            # 都固定返回 False，是诊断代码自己的 bug，不代表循环真的没
            # 在跑。真正可用的信号是 `get_digest_status()` 里的
            # `last_tick_at`/`tick_interval_seconds`——用"最近一次 tick
            # 距今是否明显超过一个正常 tick 间隔"来判断循环是否还在正常
            # 心跳，而不是去猜一个不存在的属性名。
            digest = autonomous_loop.get_digest_status()
            last_tick_at = float(digest.get("last_tick_at") or 0.0)
            tick_interval = float(digest.get("tick_interval_seconds") or 0.0)
            snapshot["autonomous_loop_last_tick_at"] = last_tick_at
            snapshot["autonomous_loop_tick_count"] = int(digest.get("tick_count") or 0)
            if last_tick_at <= 0:
                # 从未 tick 过：daemon 刚起来、或者这个 AutonomousLoop 实例
                # 压根没被真正启动（跟进程本身是否存活是两回事）。
                snapshot["autonomous_loop_running"] = False
            else:
                stale_after = max(tick_interval * 3, 300.0)
                snapshot["autonomous_loop_running"] = (time.time() - last_tick_at) < stale_after
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

            # [响应用户反馈] backlog 全部完成后，AutonomousLoop 在
            # maintenance 档位会写一批 status="draft" 的 agent_derived
            # Goal 作为"待确认的下一步建议"（见 _maybe_propose_goals_on_
            # exhaustion() 文档）。这些节点 is_active 为 False，不会出现
            # 在 active_goal_count 里，单独列出来才不会被面板忽略。
            proposals = [
                n for n in goal_backlog.all_nodes()
                if n.is_goal and n.status == "draft" and n.source == "agent_derived"
            ]
            snapshot["pending_goal_proposals"] = [
                {"id": g.id, "title": g.title, "description": g.description}
                for g in proposals
            ]

            # [响应用户反馈：想要的是"目标树该怎么继续扩展"，不是扁平的新
            # Goal] 目标树本来就有一整套阶段二/三写好、也已经在 daemon
            # 启动时接了 cron 的自动分解机制——`find_stale_nodes_for_scan()`
            # + `find_parent_needing_decompose_after_completion()` +
            # `sys:goal_tree_decompose_scan`（每 24 小时跑一次）。问题不是
            # "没有机制"，是这套机制有两道等待闸门：① 停滞判定要求节点
            # 超过 14 天没被 touch 过才算"该扩展了"；② 还要等下一次 cron
            # 巡检（最多 24 小时）才会真的触发。用户在看板上想立刻看到
            # "接下来能往哪扩展"，等不起这两道闸门。
            #
            # 这里复用同一套已经测试过的检测函数，把 `stale_days` 传 0
            # （不再要求 14 天，只看"活着但没有非终态子节点"这个结构性条件
            # 本身），并且把"完成态联动"的回看窗口从 25 小时放宽到
            # "不限"（直接扫全部 completed 节点），得到的就是"目标树里此刻
            # 结构性地卡住、值得继续往下拆的节点全集"——面板据此可以给一个
            # "立即生成扩展建议"按钮，直接调用同一个 `GoalTreeDecomposer.
            # decompose()`（仍然是"生成候选，等用户 accept/reject"的安全
            # 语义，不会绕过确认直接产生新的 active 节点）。
            from mini_agent.perception.goal_tree_decomposer import (
                find_stale_nodes_for_scan,
                find_parent_needing_decompose_after_completion,
            )

            stale_now = find_stale_nodes_for_scan(goal_backlog, stale_days=0)
            stale_ids = {n.id for n in stale_now if n.id in reachable}

            completed_in_tree = [
                n for n in goal_backlog.all_nodes()
                if n.id in reachable and n.status == "completed" and n.level != "objective"
            ]
            linked_ids: set = set()
            for cn in completed_in_tree:
                parent = find_parent_needing_decompose_after_completion(goal_backlog, cn.id)
                if parent is not None and parent.id in reachable:
                    linked_ids.add(parent.id)

            expandable_ids = stale_ids | linked_ids
            expandable_nodes = [n for n in goal_backlog.all_nodes() if n.id in expandable_ids]
            snapshot["tree_expansion_candidates"] = [
                {
                    "id": n.id,
                    "title": n.title,
                    "level": n.level,
                    "status": n.status,
                    "reason": "stale" if n.id in stale_ids else "completion_linked",
                }
                for n in sorted(expandable_nodes, key=lambda n: n.priority, reverse=True)
            ]

            snapshot["tree_pending_decompose_candidates"] = [
                {"id": n.id, "title": n.title, "candidate_count": len(n.decompose_candidates)}
                for n in goal_backlog.all_nodes()
                if n.id in reachable and n.decompose_candidates
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
        or snapshot["tree_expansion_candidates"]
        or snapshot["tree_pending_decompose_candidates"]
        or (snapshot["active_goal_count"] == 0 and snapshot["active_objective_count"] == 0)
    )
    return snapshot
