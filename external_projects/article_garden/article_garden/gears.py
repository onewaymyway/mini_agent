"""gears.py — 挡位与自动化闸门（方案 §9）。

挡位只决定“允许自动做到哪一步”，每一项自动行为还要有对应开关打开才会发生。
  G0 全手动（cycle 不运行）；G1 自动整理与选题（选题需人工放行）；
  G2 自动放行 + 调研 + 成文（发布前需人工审核）；G3 自动审阅到 reviewed（发布仍需人工，除非 auto_publish）。
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

RANK = {"G0": 0, "G1": 1, "G2": 2, "G3": 3}


def _gear(eff: Dict[str, Dict[str, Any]]) -> int:
    return RANK.get(eff["automation"]["gear"], 0)


def allowed(eff: Dict[str, Dict[str, Any]], action: str) -> Tuple[bool, str]:
    a = eff["automation"]
    g = _gear(eff)
    if action == "cycle":
        return (g >= 1, "G0 为全手动，请逐步在看板或命令行触发各步骤" if g == 0 else "")
    if action in ("digest", "wiki", "tree", "topics"):
        return (g >= 1, "需要挡位 G1 或以上")
    if action == "auto_approve":
        return (g >= 2 and bool(a["auto_approve"]), "需要 G2 以上且打开「自动放行选题」")
    if action == "research":
        return (g >= 2 and bool(a["auto_research"]), "需要 G2 以上且打开「自动调研」")
    if action == "write":
        return (g >= 2 and bool(a["auto_write"]), "需要 G2 以上且打开「自动成文」")
    if action == "review":
        return (g >= 3, "需要 G3 挡位")
    if action == "publish":
        return (g >= 3 and bool(a["auto_publish"]), "需要 G3 且打开「自动发布」")
    if action == "scheduled":
        return (bool(a["scheduled_cycle"]), "需要打开「定时运行一轮」")
    raise ValueError(f"未知动作：{action}")
