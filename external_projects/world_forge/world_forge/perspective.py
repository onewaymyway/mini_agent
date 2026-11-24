"""world_forge/perspective.py — 主体视角时间线：把混在一起的事件按参与的主体（角色、实体）拆开。

一个事件属于它的每个参与者（involves），以及它的变更所针对的对象（changes.target）。
输出：
  subject_lanes()  每个主体自己的事件线（按时间顺序）
  lanes_svg()      泳道图：每行一个主体，事件沿同一条时间轴排列，主体的事件之间用线连起来
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

from . import repo
from .calendar import MINUTES_PER_UNIT, format_year
from .graph import KIND_COLORS


def _minute(ev: dict) -> int:
    if "minute" in ev:
        return int(ev["minute"])
    return int(ev.get("year", 0)) * MINUTES_PER_UNIT["year"]


def subject_lanes(wid: str, branch: Optional[str] = None) -> Dict[str, Any]:
    meta = repo.load_meta(wid)
    bid = branch or meta["active_branch"]
    events = repo.load_events(wid, bid)
    info: Dict[str, dict] = {}
    for e in repo.load_entities(wid):
        info[e["id"]] = {"id": e["id"], "name": e["name"], "kind": e["kind"]}
    for c in repo.load_characters(wid):
        info[c["id"]] = {"id": c["id"], "name": c["name"], "kind": "角色"}

    lanes: Dict[str, List[tuple]] = {}
    for idx, ev in enumerate(events, start=1):
        subs = [i for i in (ev.get("involves") or []) if i in info]
        for ch in ev.get("changes") or []:
            t = ch.get("target")
            if t in info and t not in subs:
                subs.append(t)
        for sid in subs:
            lanes.setdefault(sid, []).append((idx, ev))

    subjects = []
    for sid, items in lanes.items():
        subjects.append({**info[sid], "events": items, "count": len(items)})
    subjects.sort(key=lambda s: (-s["count"], s["name"]))
    return {"branch": bid, "events": events, "subjects": subjects, "names": {k: v["name"] for k, v in info.items()}}


def subject_detail(data: Dict[str, Any], sid: str) -> List[dict]:
    """某个主体的事件线：按时间顺序，每条附带同行的其它参与者。"""
    s = next((x for x in data["subjects"] if x["id"] == sid), None)
    if s is None:
        return []
    out = []
    for idx, ev in s["events"]:
        others = [data["names"].get(i, i) for i in (ev.get("involves") or []) if i != sid]
        changes = [ch for ch in (ev.get("changes") or []) if ch.get("target") == sid]
        out.append({"index": idx, "t": ev.get("t", ""), "title": ev["title"], "summary": ev.get("summary", ""),
                    "with": others, "changes": changes})
    return out


def lanes_svg(data: Dict[str, Any], width: int = 980, lane_h: int = 46, max_lanes: int = 30) -> str:
    subs = data["subjects"][:max_lanes]
    if not subs:
        return "<p style='color:#888'>还没有可以按主体展开的事件。</p>"
    left, right, top = 190, 24, 34
    all_minutes = [_minute(ev) for s in subs for _, ev in s["events"]]
    tmin, tmax = min(all_minutes), max(all_minutes)
    span = max(tmax - tmin, 1)
    plot_w = width - left - right

    def base_x(minute: int) -> float:
        if tmax == tmin:
            return left + plot_w / 2
        return left + (minute - tmin) / span * plot_w

    # 同一时间的事件（推进一步产生的多个事件）左右错开，按事件编号计算，各泳道中位置一致
    by_minute: Dict[int, List[int]] = {}
    seen_idx = set()
    for s_ in subs:
        for idx, ev in s_["events"]:
            if idx in seen_idx:
                continue
            seen_idx.add(idx)
            by_minute.setdefault(_minute(ev), []).append(idx)
    offset: Dict[int, float] = {}
    for minute, idxs in by_minute.items():
        idxs.sort()
        for rank, idx in enumerate(idxs):
            offset[idx] = (rank - (len(idxs) - 1) / 2) * 20

    height = top + len(subs) * lane_h + 46
    parts = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' width='100%' "
             f"style='background:#0f1424;border-radius:10px;font-family:sans-serif'>"]
    # 时间刻度
    ticks = sorted({tmin, tmax, *[tmin + span * k // 4 for k in range(1, 4)]})
    axis_y = top + len(subs) * lane_h + 14
    parts.append(f"<line x1='{left}' y1='{axis_y}' x2='{width - right}' y2='{axis_y}' stroke='#3b4664'/>")
    for t in ticks:
        x = base_x(t)
        parts.append(f"<line x1='{x:.1f}' y1='{top - 6}' x2='{x:.1f}' y2='{axis_y}' stroke='#222b44' stroke-dasharray='3 4'/>")
        parts.append(f"<text x='{x:.1f}' y='{axis_y + 18}' font-size='11' fill='#8d98b5' text-anchor='middle'>"
                     f"{html.escape(format_year(t))}</text>")
    for row, s in enumerate(subs):
        y = top + row * lane_h + lane_h / 2
        color = KIND_COLORS.get(s["kind"], "#888")
        parts.append(f"<line x1='{left}' y1='{y}' x2='{width - right}' y2='{y}' stroke='#1d2540'/>")
        parts.append(f"<circle cx='{left - 150 + 6}' cy='{y}' r='6' fill='{color}'/>")
        parts.append(f"<text x='{left - 136}' y='{y + 4}' font-size='13' fill='#f2f4fa'>"
                     f"{html.escape(s['name'][:12])}</text>")
        parts.append(f"<text x='{left - 136}' y='{y + 18}' font-size='10' fill='#7f8aa6'>"
                     f"{html.escape(s['kind'])} · {s['count']} 件</text>")
        pts = [(base_x(_minute(ev)) + offset.get(idx, 0), y, idx, ev) for idx, ev in s["events"]]
        if len(pts) > 1:
            poly = " ".join(f"{px:.1f},{py:.1f}" for px, py, _, _ in pts)
            parts.append(f"<polyline points='{poly}' fill='none' stroke='{color}' stroke-opacity='0.55' "
                         f"stroke-width='2'/>")
        for px, py, idx, ev in pts:
            tip = html.escape(f"#{idx} {ev.get('t', '')} · {ev['title']}")
            parts.append(f"<g><title>{tip}</title><circle cx='{px:.1f}' cy='{py:.1f}' r='11' fill='#0f1424' "
                         f"stroke='{color}' stroke-width='2'/><text x='{px:.1f}' y='{py + 4:.1f}' font-size='10' "
                         f"fill='#f2f4fa' text-anchor='middle'>{idx}</text></g>")
    parts.append("</svg>")
    return "".join(parts)
