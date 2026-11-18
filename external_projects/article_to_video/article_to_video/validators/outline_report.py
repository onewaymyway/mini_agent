"""validators/outline_report.py — R8：大纲校验的分组报告（界面展示用）。

确认门禁仍以 validate_spine 为准（errors 非空则不能确认）；本模块只把同一批问题按类别整理，
并补充展示性的提示（时长偏差、被剔除的摘要条目）。不改变任何判断结果。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .coverage import validate_spine

CATEGORIES = [
    ("coverage", "覆盖", "每个小节都要出现在某一章，或列入取舍报告。可在「章的拆分与合并」或「取舍报告」中调整。"),
    ("duration", "时长", "各章预算之和应等于目标时长；预算过短的章可以与相邻章合并。"),
    ("fidelity", "忠实度", "金句必须能在原文中找到。被剔除的条目可在下方手动恢复为原文片段。"),
    ("structure", "结构", "建议 5～7 章，并至少提供一个开场钩子。"),
]

MIN_PART_MINUTES = 0.5
TARGET_TOLERANCE = 0.1


def _category(msg: str) -> str:
    if any(k in msg for k in ("金句", "逐字")):
        return "fidelity"
    if any(k in msg for k in ("权重", "预算", "时长")):
        return "duration"
    if any(k in msg for k in ("小节", "取舍")):
        return "coverage"
    return "structure"


def _dropped_warnings(digests: Optional[List[Dict[str, Any]]]) -> List[str]:
    out: List[str] = []
    for d in digests or []:
        n = len(d.get("dropped") or [])
        if n:
            out.append(f"摘要 {d.get('group_id', '?')} 有 {n} 条未能对齐原文，已剔除（可在下方恢复）")
    return out


def _duration_warnings(spine: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    target = float(spine.get("target_minutes") or 0)
    total = sum(float(p.get("budget_minutes") or 0) for p in spine.get("parts") or [])
    if target and abs(total - target) > TARGET_TOLERANCE:
        out.append(f"各章预算合计 {total:.1f} 分钟，与目标 {target:.1f} 分钟不一致")
    for p in spine.get("parts") or []:
        b = float(p.get("budget_minutes") or 0)
        if 0 < b < MIN_PART_MINUTES:
            out.append(f"{p.get('id', '?')} 预算仅 {b:.1f} 分钟，过短，建议与相邻章合并")
    return out


def outline_report(spine: Dict[str, Any], section_ids: List[str],
                   digests: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    base = validate_spine(spine, section_ids, digests)
    buckets: Dict[str, Dict[str, List[str]]] = {k: {"error": [], "warn": []} for k, _, _ in CATEGORIES}
    for msg in base["errors"]:
        buckets[_category(msg)]["error"].append(msg)
    for msg in base["warnings"]:
        buckets[_category(msg)]["warn"].append(msg)
    for msg in _duration_warnings(spine):
        buckets["duration"]["warn"].append(msg)
    for msg in _dropped_warnings(digests):
        buckets["fidelity"]["warn"].append(msg)

    categories = []
    for key, name, hint in CATEGORIES:
        b = buckets[key]
        status = "error" if b["error"] else ("warn" if b["warn"] else "ok")
        items = [{"level": "error", "text": m} for m in b["error"]] + \
                [{"level": "warn", "text": m} for m in b["warn"]]
        categories.append({"key": key, "name": name, "status": status, "hint": hint, "items": items})
    return {"ok": not base["errors"], "categories": categories,
            "dropped_total": sum(len(d.get("dropped") or []) for d in digests or [])}
