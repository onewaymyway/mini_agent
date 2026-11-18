"""pipeline/episodes.py — 长文多期拆分（设计第 10 节）：只在章边界拆分，单期不超过 episode_max_minutes。

每期独立成片；期首「上期回顾」、期末「下期预告」取相邻章的一句话（本章 one_line），不由模型生成。
"""

from __future__ import annotations

from typing import Any, Dict, List

DEFAULT_EPISODE_MAX = 15.0


def plan_episodes(parts: List[Dict[str, Any]], max_minutes: float = DEFAULT_EPISODE_MAX) -> List[List[str]]:
    """把章按顺序分组：每组总时长不超过 max_minutes（单章超限则独占一期）。"""
    groups: List[List[str]] = []
    cur: List[str] = []
    total = 0.0
    for p in parts:
        m = float(p.get("budget_minutes", 0.0))
        if cur and total + m > max_minutes:
            groups.append(cur)
            cur, total = [], 0.0
        cur.append(p["id"])
        total += m
    if cur:
        groups.append(cur)
    return groups


def episode_specs(parts: List[Dict[str, Any]], max_minutes: float = DEFAULT_EPISODE_MAX) -> List[Dict[str, Any]]:
    """每期的 {index, total, parts, recap, preview}。只有一期时 total=1，无回顾与预告。"""
    groups = plan_episodes(parts, max_minutes)
    by_id = {p["id"]: p for p in parts}
    n = len(groups)
    specs = []
    for i, ids in enumerate(groups):
        recap = None
        preview = None
        if i > 0:
            prev_last = by_id[groups[i - 1][-1]]
            text = str(prev_last.get("one_line") or prev_last.get("title") or "").strip()
            recap = f"上期回顾：{text}" if text else None
        if i < n - 1:
            next_first = by_id[groups[i + 1][0]]
            text = str(next_first.get("one_line") or next_first.get("title") or "").strip()
            preview = f"下期预告：{text}" if text else None
        specs.append({"index": i + 1, "total": n, "parts": ids, "recap": recap, "preview": preview})
    return specs
