"""planning/spine_math.py — 大纲的纯计算：章划分、时长预算、编号。"""

from __future__ import annotations

from typing import Any, Dict, List


def partition(weights: List[float], k: int) -> List[List[int]]:
    """把 weights 按顺序划分为最多 k 段，尽量让每段权重接近 total/k（段内连续）。"""
    n = len(weights)
    if n == 0:
        return []
    k = max(1, min(k, n))
    total = float(sum(weights))
    parts: List[List[int]] = []
    cur: List[int] = []
    acc = 0.0
    for i, w in enumerate(weights):
        cur.append(i)
        acc += w
        remaining_parts = k - len(parts) - 1
        remaining_sections = n - i - 1
        if (len(parts) < k - 1 and acc >= total * (len(parts) + 1) / k
                and remaining_sections >= remaining_parts):
            parts.append(cur)
            cur = []
    if cur:
        parts.append(cur)
    return parts


def renumber(spine: Dict[str, Any]) -> None:
    for i, p in enumerate(spine["parts"]):
        p["id"] = f"P{i + 1:02d}"


def recompute_budgets(spine: Dict[str, Any]) -> None:
    total = sum(float(p.get("weight", 0) or 0) for p in spine["parts"]) or 1.0
    target = float(spine["target_minutes"])
    for p in spine["parts"]:
        p["budget_minutes"] = round(target * float(p.get("weight", 0) or 0) / total, 2)
