"""地点分组（纯函数，供全书素材页使用）：把区域与其场景点组织成一个内容块。

结构：区域（level=area）→ 其下的场景点（level=spot，parent_id 指向区域）。
没有有效区域的场景点归入“未归属区域”。只包含在审核列表里出现过的地点（row_ids）。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def group_locations(locs: List[Dict[str, Any]], row_ids: Iterable[str]) -> Dict[str, Any]:
    row_ids = set(row_ids)
    by_id = {l["id"]: l for l in locs}
    is_area = lambda l: (l.get("level") or "area") == "area"
    areas = [l for l in locs if is_area(l) and l["id"] in row_ids]
    spots: Dict[str, List[Dict[str, Any]]] = {a["id"]: [] for a in areas}
    orphans: List[Dict[str, Any]] = []
    for l in locs:
        if is_area(l) or l["id"] not in row_ids:
            continue
        pid = l.get("parent_id")
        if pid in spots:
            spots[pid].append(l)
        else:
            orphans.append(l)
    groups = [{"area": a, "spots": spots[a["id"]]} for a in areas]
    return {"groups": groups, "orphans": orphans, "by_id": by_id}


def sheet_counts(loc: Dict[str, Any], pdir_has_file) -> tuple:
    """(已生成张数, 总张数)。pdir_has_file(rel) -> bool。"""
    sheets = loc.get("sheets") or []
    done = sum(1 for s in sheets if s.get("asset_path") and pdir_has_file(s["asset_path"]))
    return done, len(sheets)
