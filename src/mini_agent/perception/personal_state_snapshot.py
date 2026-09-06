"""perception/personal_state_snapshot.py — 用户当前处境的物化快照
（next_doc/personal_ai_alignment_upgrade_plan.md 阶段二 §4.2）。

回答"现在是什么"，与 Memory（记录"发生过什么"）明确分离：本模块只读
聚合现有分散数据源，**不做任何新增采集、不落盘为历史记录**——每次调用
都是从源数据实时重新计算的结果，与 `fairness_diagnostics_snapshot()`/
`sentinel.py::sentinel_summary()` 等既有只读聚合模块同一风格：任一数据
源读取失败都不应影响其它数据源，各自 try/except，失败时该部分退化为
空/默认值，不让一路异常搞坏整个快照。

聚合的四类信号（均已在其它模块落盘，本模块不新增采集点）：
  1. 当前活跃 Goal 及其状态 —— 读 `GoalBacklog.active_goals()`。
  2. 当前进度 vs 计划的偏差 —— 读每个活跃 Goal 的
     `execution_phase.py::ExecutionPhaseState`（当前 mode/连续停留轮数/
     是否已有未冷却的健康告警）+ 全局 `goal_stuck_stats.py` 统计。
     [已知限制] `execution_phase.check_phase_health()` 需要先算出
     `effective_mode`（依赖 `resolve_effective_mode()` 的完整调用链，
     牵涉 cfg/routine 信号），只读快照阶段不重新跑一遍该链路以免引入
     和 AutonomousLoop 主循环不一致的判断结果；这里只读取阶段状态里
     已经存在的字段（mode/cycles_in_mode/last_health_alert_kind），
     不重新计算健康判定本身。
  3. 当前待处理的主动建议数量与紧急度 —— 读
     `initiative_inbox.initiative_inbox_snapshot()`。
  4. 当前 Personal Model 中标记为 active 的约束摘要 —— 读
     `profile.py::UserProfile.derived["constraints"]`（阶段一新增）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional


def _empty_snapshot() -> dict[str, Any]:
    return {
        "generated_at": time.time(),
        "active_goals": [],
        "progress": {
            "goals_with_health_alert": [],
            "recent_stuck_count": 0,
            "stuck_ratio": 0.0,
        },
        "pending_initiatives": {
            "total": 0,
            "by_domain": {},
            "urgent_count": 0,
        },
        "constraints": [],
    }


def _collect_active_goals(paths) -> list[dict]:
    """[阶段二 §1] 当前活跃 Goal 摘要，按优先级降序，最多取前 20 条——
    快照是给各子系统/Context Pack 读的摘要视图，不是完整 goals.json 的
    替代，过长的列表本身就失去了"快照"应有的一览性。"""
    try:
        from mini_agent.perception.goal_backlog import load_goal_backlog

        backlog = load_goal_backlog(paths)
        goals = backlog.active_goals()[:20]
        return [
            {
                "id": g.id,
                "title": g.title,
                "level": g.level,
                "priority": g.priority,
                "source": g.source,
                "last_touched_at": g.last_touched_at,
            }
            for g in goals
        ]
    except Exception:
        return []


def _collect_progress_signals(paths, active_goal_ids: list[str]) -> dict[str, Any]:
    """[阶段二 §2] 进度 vs 计划的偏差信号。只读取已落盘的阶段状态字段，
    不重新触发 `resolve_effective_mode()`/`check_phase_health()` 的完整
    判定链路（见模块 docstring 已知限制）。"""
    goals_with_alert: list[dict] = []
    try:
        from mini_agent.perception.execution_phase import load_phase

        for goal_id in active_goal_ids:
            try:
                state = load_phase(paths, goal_id)
            except Exception:
                continue
            if state.last_health_alert_kind:
                goals_with_alert.append({
                    "goal_id": goal_id,
                    "mode": state.mode,
                    "cycles_in_mode": state.cycles_in_mode,
                    "alert_kind": state.last_health_alert_kind,
                })
    except Exception:
        pass

    recent_stuck_count = 0
    stuck_ratio = 0.0
    try:
        from mini_agent.perception.goal_stuck_stats import stuck_stats_summary

        project_root = getattr(paths, "project_root", None)
        stuck_summary = stuck_stats_summary(project_root)
        recent_stuck_count = int(stuck_summary.get("recent_stuck_count", 0) or 0)
        stuck_ratio = float(stuck_summary.get("stuck_ratio", 0.0) or 0.0)
    except Exception:
        pass

    return {
        "goals_with_health_alert": goals_with_alert,
        "recent_stuck_count": recent_stuck_count,
        "stuck_ratio": stuck_ratio,
    }


def _collect_pending_initiatives(paths, *, urgent_confidence_threshold: float = 0.4) -> dict[str, Any]:
    """[阶段二 §3] 当前待处理主动建议数量与紧急度。直接消费阶段一之前
    已经跑通的 `initiative_inbox_snapshot()`，不重复采集。`urgent_count`
    是"低置信度候选需要用户确认"这一类的粗粒度计数——`confidence` 越低
    代表 AI 自己也不确定，越需要用户来判断，不是"越紧急"的时间语义，
    命名沿用方案 §4.4 Daily Digest 草图里"需要你决定"这一类的口径，
    避免阶段四再发明一套不同的计数标准。计算失败时按 0 处理，与调用方
    `initiative_inbox_snapshot()` 本身"单路异常不搞坏整个视图"的一贯
    容错风格一致，不重复捕获细粒度异常。"""
    try:
        from mini_agent.perception.initiative_inbox import initiative_inbox_snapshot

        snapshot = initiative_inbox_snapshot(paths, annotate_relevance=False, annotate_cross_dismiss=False)
        items = snapshot.get("items", [])
    except Exception:
        items = []

    by_domain: dict[str, int] = {}
    urgent_count = 0
    for item in items:
        domain = item.get("domain", "unknown")
        by_domain[domain] = by_domain.get(domain, 0) + 1
        confidence = item.get("confidence")
        if isinstance(confidence, (int, float)) and confidence < urgent_confidence_threshold:
            urgent_count += 1

    return {
        "total": len(items),
        "by_domain": by_domain,
        "urgent_count": urgent_count,
    }


def _collect_active_constraints(paths) -> list[dict]:
    """[阶段二 §4] Personal Model 中标记为 active 的约束摘要——阶段一
    `constraints` 目前没有独立的"是否 active"状态字段（用户 add 的即视
    为生效中，没有 pause/expire 概念），因此这里直接返回全部已记录的
    constraints；只截取 `text`/`last_confirmed_at` 两个字段供其它子系统
    读取，不带 `evidence_refs`/`confidence` 等治理细节——那是画像本身
    的展示需求，不是"现在处境"快照该关心的粒度。"""
    try:
        from mini_agent.profile import UserProfileManager

        manager = UserProfileManager(paths)
        items = manager.list_constraints()
        return [
            {"text": it.get("text", ""), "last_confirmed_at": it.get("last_confirmed_at", 0.0)}
            for it in items
        ]
    except Exception:
        return []


def personal_state_snapshot(paths) -> dict[str, Any]:
    """聚合当前"用户处境"的物化快照，供各子系统（Context Pack 组装器、
    Daily Digest 等）统一读取，减少各条线各自扫描、互不感知的问题。

    不落盘、不追加历史——每次调用都是从源数据重新计算的结果，这是与
    Memory 的关键区别（见模块 docstring）。`paths` 为 None 或任何一步
    异常都返回全零结构，不向上抛出异常。
    """
    if paths is None:
        return _empty_snapshot()

    try:
        active_goals = _collect_active_goals(paths)
        active_goal_ids = [g["id"] for g in active_goals]
        progress = _collect_progress_signals(paths, active_goal_ids)
        pending_initiatives = _collect_pending_initiatives(paths)
        constraints = _collect_active_constraints(paths)
        return {
            "generated_at": time.time(),
            "active_goals": active_goals,
            "progress": progress,
            "pending_initiatives": pending_initiatives,
            "constraints": constraints,
        }
    except Exception:
        return _empty_snapshot()
