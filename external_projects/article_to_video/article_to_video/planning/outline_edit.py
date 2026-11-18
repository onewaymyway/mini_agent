"""planning/outline_edit.py — C0 大纲编辑的纯函数。每个函数返回新大纲，不修改入参。

编辑只改变章划分、标题、钩子、取舍与时长；小节 ID 不变，因此分段稿与分镜可以按 ID 对齐。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List

from .spine_math import recompute_budgets, renumber


def _finish(spine: Dict[str, Any]) -> Dict[str, Any]:
    renumber(spine)
    recompute_budgets(spine)
    spine["confirmed"] = False  # 任何编辑都使已确认的大纲失效，需重新确认
    return spine


def rename_part(spine: Dict[str, Any], idx: int, title: str) -> Dict[str, Any]:
    title = title.strip()
    if not title:
        raise ValueError("章标题不能为空")
    s = copy.deepcopy(spine)
    s["parts"][idx]["title"] = title
    return _finish(s)


def select_hook(spine: Dict[str, Any], idx: int) -> Dict[str, Any]:
    if not 0 <= idx < len(spine["hook_candidates"]):
        raise ValueError("开场钩子序号越界")
    s = copy.deepcopy(spine)
    s["selected_hook"] = idx
    return _finish(s)


def set_target_minutes(spine: Dict[str, Any], minutes: float) -> Dict[str, Any]:
    s = copy.deepcopy(spine)
    s["target_minutes"] = round(max(3.0, min(60.0, float(minutes))), 1)
    return _finish(s)


def merge_with_next(spine: Dict[str, Any], idx: int) -> Dict[str, Any]:
    parts = spine["parts"]
    if not 0 <= idx < len(parts) - 1:
        raise ValueError("最后一章没有下一章可合并")
    s = copy.deepcopy(spine)
    a, b = s["parts"][idx], s["parts"].pop(idx + 1)
    a["section_ids"] = a["section_ids"] + b["section_ids"]
    a["weight"] = float(a["weight"]) + float(b["weight"])
    if b.get("one_line") and not a.get("one_line"):
        a["one_line"] = b["one_line"]
    return _finish(s)


def split_part(spine: Dict[str, Any], idx: int, at: int) -> Dict[str, Any]:
    """在第 idx 章的第 at 个小节前拆开（1 ≤ at < 小节数）。权重按小节数比例分配。"""
    s = copy.deepcopy(spine)
    p = s["parts"][idx]
    ids = p["section_ids"]
    if not 1 <= at < len(ids):
        raise ValueError("拆分位置越界")
    left_ids, right_ids = ids[:at], ids[at:]
    ratio = len(left_ids) / len(ids)
    right = {
        "id": "",
        "title": f"{p['title']}（续）",
        "role": "argument",
        "one_line": "",
        "section_ids": right_ids,
        "weight": float(p["weight"]) * (1 - ratio),
        "budget_minutes": 0.0,
    }
    p["section_ids"] = left_ids
    p["weight"] = float(p["weight"]) * ratio
    s["parts"].insert(idx + 1, right)
    return _finish(s)


def restore_cut(spine: Dict[str, Any], cut_idx: int, part_idx: int,
                catalog: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把取舍报告中的第 cut_idx 条小节恢复到第 part_idx 章，并按原文顺序排好。"""
    s = copy.deepcopy(spine)
    cut = s["cuts"][cut_idx]
    order = {c["id"]: i for i, c in enumerate(catalog)}
    chars = {c["id"]: c["chars"] for c in catalog}
    p = s["parts"][part_idx]
    p["section_ids"] = sorted(p["section_ids"] + cut["section_ids"], key=lambda x: order.get(x, 10**9))
    p["weight"] = float(p["weight"]) + sum(max(1, chars.get(x, 1)) for x in cut["section_ids"])
    del s["cuts"][cut_idx]
    return _finish(s)
