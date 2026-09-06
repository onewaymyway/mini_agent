"""perception/daily_digest.py — 每日简报只读聚合视图
（next_doc/personal_ai_alignment_upgrade_plan.md 阶段四 §4.4）。

方案原文的四段式结构：
  今天最重要的事：Top N 活跃 Goal 的下一步动作
  AI 已完成：近期成功执行的 Goal / 建议采纳记录
  需要你决定：initiative_inbox 中 confidence 较低或标记需要确认的候选
  风险：进度偏差项

不提供写操作（与 `initiative_inbox.py` 现有原则一致，写操作留在各自
原生 tab），纯合成展示层，不侵入任何现有模块，也不新增采集点：全部
数据来自阶段二的 `personal_state_snapshot()`、已有的
`initiative_inbox_snapshot()`、`goal_backlog.py` 已落盘的 Goal 状态。

与 `personal_state_snapshot()` 的关系：本模块**消费**它产出的
`active_goals`/`progress`/`pending_initiatives` 三块，不重复读取
`execution_phase`/`goal_stuck_stats` 等更底层的数据源——避免同一份
"进度偏差"判断在两个只读聚合层里各自维护一份、口径逐渐分叉。

与 `initiative_inbox_snapshot()` 的关系：Daily Digest 的"需要你决定"
不是"候选收件箱"的替代——收件箱是给用户逐条处理建议用的完整列表，
本模块只挑其中"置信度较低、需要用户确认"的一小部分做简报式摘要
（`urgent_confidence_threshold`，与 `personal_state_snapshot.py`
`urgent_count` 用同一套阈值语义，不发明第二套标准）。

不落盘、不追加历史——每次调用都是从源数据实时重新计算的结果，与
`personal_state_snapshot()`/`fairness_diagnostics_snapshot()` 同一
"State 而非 Memory"风格：任一子聚合异常都不影响其它子聚合，各自
try/except，失败时该部分退化为空列表，不让一路异常搞坏整份简报。
"""

from __future__ import annotations

import time
from typing import Any

DEFAULT_TOP_N = 5
DEFAULT_RECENT_COMPLETED_LIMIT = 5
DEFAULT_URGENT_CONFIDENCE_THRESHOLD = 0.4


def _empty_digest() -> dict[str, Any]:
    return {
        "generated_at": time.time(),
        "top_priorities": [],
        "ai_completed": [],
        "needs_your_decision": [],
        "risks": [],
    }


def _collect_top_priorities(snapshot: dict, *, top_n: int) -> list[dict]:
    """[阶段四 §1]"今天最重要的事" —— 直接取 `personal_state_snapshot()`
    已经按优先级降序排好的 `active_goals` 前 N 条，不重新排序、不重新
    读取 goal_backlog（避免与快照口径不一致）。"""
    active_goals = (snapshot or {}).get("active_goals", [])
    out = []
    for g in active_goals[:top_n]:
        out.append({
            "goal_id": g.get("id"),
            "title": g.get("title", ""),
            "level": g.get("level", ""),
            "priority": g.get("priority", 0),
        })
    return out


def _collect_ai_completed(paths, *, limit: int) -> list[dict]:
    """[阶段四 §2]"AI 已完成" —— 读 `goal_backlog.py` 中 status=="completed"
    的节点，按 `last_touched_at` 降序取最近 N 条。这是"近期成功执行的
    Goal"这一半；"建议采纳记录"目前只有 `suggestion_feedback_ledger.py`
    按 category 聚合的 accepted/rejected 计数，没有可展示的具体标题，
    如实只呈现 Goal 完成这一半，不臆造建议采纳的展示内容（已知限制，
    见实施记录）。"""
    try:
        from mini_agent.perception.goal_backlog import load_goal_backlog

        backlog = load_goal_backlog(paths)
        completed = [n for n in backlog.all_nodes() if n.status == "completed"]
        completed.sort(key=lambda n: n.last_touched_at, reverse=True)
        return [
            {
                "goal_id": n.id,
                "title": n.title,
                "level": n.level,
                "completed_at": n.last_touched_at,
            }
            for n in completed[:limit]
        ]
    except Exception:
        return []


def _collect_needs_your_decision(
    paths, *, limit: int, urgent_confidence_threshold: float
) -> list[dict]:
    """[阶段四 §3]"需要你决定" —— 从 `initiative_inbox_snapshot()` 的
    全量候选中挑出 confidence 低于阈值的一部分，按 confidence 升序（越
    不确定越靠前）取前 N 条。阈值与 `personal_state_snapshot.py`
    `urgent_count` 同一口径，避免本模块另立标准。"""
    try:
        from mini_agent.perception.initiative_inbox import initiative_inbox_snapshot

        snapshot = initiative_inbox_snapshot(paths, annotate_relevance=False, annotate_cross_dismiss=False)
        items = snapshot.get("items", [])
    except Exception:
        items = []

    candidates = []
    for item in items:
        confidence = item.get("confidence")
        if isinstance(confidence, (int, float)) and confidence < urgent_confidence_threshold:
            candidates.append(item)
    candidates.sort(key=lambda it: (it.get("confidence") if isinstance(it.get("confidence"), (int, float)) else 1.0))

    return [
        {
            "item_id": it.get("item_id"),
            "domain": it.get("domain"),
            "title": it.get("title", ""),
            "confidence": it.get("confidence"),
        }
        for it in candidates[:limit]
    ]


def _collect_risks(snapshot: dict) -> list[dict]:
    """[阶段四 §4]"风险" —— 直接取 `personal_state_snapshot()` 的
    `progress` 字段，不重新计算，只做展示形态转换。"""
    progress = (snapshot or {}).get("progress", {})
    risks: list[dict] = []
    for alert in progress.get("goals_with_health_alert", []):
        risks.append({
            "kind": "health_alert",
            "goal_id": alert.get("goal_id"),
            "detail": alert.get("alert_kind", ""),
        })
    stuck_ratio = progress.get("stuck_ratio", 0.0)
    if stuck_ratio:
        risks.append({
            "kind": "stuck_ratio",
            "detail": f"近期卡住比例 {stuck_ratio:.0%}",
        })
    return risks


def daily_digest(
    paths,
    *,
    top_n: int = DEFAULT_TOP_N,
    recent_completed_limit: int = DEFAULT_RECENT_COMPLETED_LIMIT,
    urgent_confidence_threshold: float = DEFAULT_URGENT_CONFIDENCE_THRESHOLD,
) -> dict[str, Any]:
    """合成每日简报（方案 §4.4）。纯只读聚合，不提供写操作，不落盘、不
    追加历史——每次调用都是从源数据重新计算的结果。`paths` 为 None 或
    任一子聚合异常都不向上抛出异常，各自退化为空列表。
    """
    if paths is None:
        return _empty_digest()

    try:
        from mini_agent.perception.personal_state_snapshot import personal_state_snapshot

        snapshot = personal_state_snapshot(paths)
    except Exception:
        snapshot = {}

    try:
        top_priorities = _collect_top_priorities(snapshot, top_n=top_n)
    except Exception:
        top_priorities = []

    ai_completed = _collect_ai_completed(paths, limit=recent_completed_limit)
    needs_your_decision = _collect_needs_your_decision(
        paths, limit=top_n, urgent_confidence_threshold=urgent_confidence_threshold,
    )

    try:
        risks = _collect_risks(snapshot)
    except Exception:
        risks = []

    return {
        "generated_at": time.time(),
        "top_priorities": top_priorities,
        "ai_completed": ai_completed,
        "needs_your_decision": needs_your_decision,
        "risks": risks,
    }
