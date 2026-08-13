"""
perception/cross_goal_reference.py — 跨 Goal 经验复用：相似历史 Goal
执行规范推荐（next_doc/cross_goal_experience_reuse_plan.md）

不引入新的向量检索基础设施，用 `difflib.SequenceMatcher` 做轻量文本
相似度匹配（与 `execution_phase.py::compute_progress_trend_signal` 的
Stage D 兜底逻辑同一套思路），在已确认 `GoalExecutionSpec` 的历史 Goal
里找出跟新 Goal 标题/描述相似的候选，附上对方的执行规范摘要，供用户在
创建 Goal 时自愿参考——不做任何自动应用。
"""

from __future__ import annotations

import difflib
from typing import Any, Optional


def find_similar_confirmed_goals(
    goal_backlog, title: str, description: str = "", *,
    top_k: int = 3, min_similarity: float = 0.35, paths=None,
    exclude_goal_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """在已确认执行规范的历史 Goal 里，找出跟 (title, description) 相似的
    候选，按相似度降序返回最多 `top_k` 条。

    候选池：`goal_backlog.all_nodes()` 中 `is_goal` 且
    `execution_spec_confirmed=True` 的节点——只推荐"已经趟出来、被用户
    确认过"的规范。`goal_backlog` 为 None、查询文本为空、或任何环节异常
    都返回空列表，不抛异常。

    每条返回结构：
    {"goal_id": str, "title": str, "similarity": float,
     "spec_summary": str}
    低于 `min_similarity` 的候选被丢弃；找不到对应 `GoalExecutionSpec`
    （已被归档/删除）的候选也会被跳过，不展示一个读不到内容的推荐。
    """
    if goal_backlog is None:
        return []
    query = f"{title or ''} {description or ''}".strip()
    if not query:
        return []
    try:
        from mini_agent.perception.goal_execution_spec import load_spec

        nodes = goal_backlog.all_nodes()
    except Exception:
        return []

    scored: list[tuple[float, Any]] = []
    try:
        for node in nodes:
            if not getattr(node, "is_goal", False):
                continue
            if not getattr(node, "execution_spec_confirmed", False):
                continue
            if exclude_goal_id and node.id == exclude_goal_id:
                continue
            candidate_text = f"{node.title or ''} {node.description or ''}".strip()
            if not candidate_text:
                continue
            ratio = difflib.SequenceMatcher(None, query, candidate_text).ratio()
            if ratio >= min_similarity:
                scored.append((ratio, node))
    except Exception:
        return []

    scored.sort(key=lambda t: t[0], reverse=True)

    results: list[dict[str, Any]] = []
    for ratio, node in scored[: max(top_k, 0)]:
        try:
            spec = load_spec(paths, node.id) if paths is not None else None
        except Exception:
            spec = None
        if spec is None:
            continue
        try:
            summary = spec.render_summary_for_user()
        except Exception:
            summary = ""
        if not summary:
            continue
        results.append({
            "goal_id": node.id,
            "title": node.title,
            "similarity": round(ratio, 3),
            "spec_summary": summary,
        })
    return results
