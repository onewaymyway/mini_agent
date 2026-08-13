"""
perception/fairness_diagnostics.py — 调度公平性参数自诊断（只读快照）

（next_doc/goal_fairness_scheduling_diagnostics_plan.md）

不新增任何事件持久化，纯读取 `GoalBacklog.active_objectives_fair_ranked()`
+ `compute_aging_boost()` + `ObjectiveExecutor.fairness_paused_objective_ids()`
已经在内存/存储里现成可用的数据做一次快照聚合，回答"公平轮询/老化加成/
时间片抢占这几个 `goal_execution_fairness_improvement_plan.md` 里默认值
拍脑袋定的参数，现在实际状态是什么样"。

任何异常都返回全零/空结构，不抛异常，与既有只读聚合
（`sentinel.py::sentinel_summary()`、`goal_stuck_stats.py::stuck_stats_summary()`）
风格一致。
"""

from __future__ import annotations

import time
from typing import Any, Optional


def _empty_snapshot() -> dict[str, Any]:
    return {
        "generated_at": time.time(),
        "time_slicing_enabled": False,
        "config": {
            "aging_boost_per_day": 1.0,
            "aging_boost_max_days": 14.0,
            "stale_days": 7.0,
            "yield_after_steps": 3,
            "yield_after_seconds": 900.0,
        },
        "paused_for_fairness_count": 0,
        "paused_for_fairness_objective_ids": [],
        "active_objectives_count": 0,
        "goals_with_active_aging_boost": 0,
        "objectives": [],
    }


def fairness_diagnostics_snapshot(
    goal_backlog, objective_executor, cfg, *, max_objectives: int = 20,
) -> dict[str, Any]:
    """当前调度公平性参数的只读快照。见模块 docstring。"""
    if cfg is None:
        return _empty_snapshot()
    try:
        autonomy_cfg = getattr(cfg, "autonomy", None)
        time_slicing_enabled = bool(getattr(autonomy_cfg, "fairness_time_slicing_enabled", False))
        stale_days = float(getattr(cfg, "next_action_stale_days", 7.0))
        boost_per_day = float(getattr(autonomy_cfg, "fairness_aging_boost_per_day", 1.0))
        boost_max_days = float(getattr(autonomy_cfg, "fairness_aging_boost_max_days", 14.0))
        yield_after_steps = int(getattr(autonomy_cfg, "fairness_yield_after_steps", 3))
        yield_after_seconds = float(getattr(autonomy_cfg, "fairness_yield_after_seconds", 900.0))
    except Exception:
        return _empty_snapshot()

    paused_ids: list[str] = []
    if objective_executor is not None:
        try:
            paused_ids = list(objective_executor.fairness_paused_objective_ids())
        except Exception:
            paused_ids = []

    objectives: list[dict[str, Any]] = []
    goals_with_boost = 0
    if goal_backlog is not None:
        try:
            from mini_agent.perception.goal_backlog import compute_aging_boost

            now = time.time()
            ranked = goal_backlog.active_objectives_fair_ranked(
                stale_days=stale_days,
                aging_boost_per_day=boost_per_day,
                aging_boost_max_days=boost_max_days,
                now=now,
            )
            for node in ranked:
                boost = compute_aging_boost(
                    node, now, stale_days=stale_days,
                    boost_per_day=boost_per_day, max_boost_days=boost_max_days,
                )
                is_running = False
                if objective_executor is not None:
                    try:
                        is_running = bool(objective_executor.is_running(node.id))
                    except Exception:
                        is_running = False
                if boost > 0:
                    goals_with_boost += 1
                objectives.append({
                    "objective_id": node.id,
                    "goal_id": node.parent_id or node.id,
                    "priority": node.priority,
                    "aging_boost": round(boost, 2),
                    "effective_priority": round(node.priority + boost, 2),
                    "is_running": is_running,
                    "is_paused_for_fairness": node.id in paused_ids,
                })
        except Exception:
            objectives = []
            goals_with_boost = 0

    return {
        "generated_at": time.time(),
        "time_slicing_enabled": time_slicing_enabled,
        "config": {
            "aging_boost_per_day": boost_per_day,
            "aging_boost_max_days": boost_max_days,
            "stale_days": stale_days,
            "yield_after_steps": yield_after_steps,
            "yield_after_seconds": yield_after_seconds,
        },
        "paused_for_fairness_count": len(paused_ids),
        "paused_for_fairness_objective_ids": paused_ids,
        "active_objectives_count": len(objectives),
        "goals_with_active_aging_boost": goals_with_boost,
        "objectives": objectives[:max_objectives],
    }
