"""world_forge/timeline.py — 时间线分支：分叉（回退即分叉）、切换、对比。

设计：
  * 事件只追加。分支保存自己的完整历史文件（fork 时复制 fork 点之前的事件）。
  * “回退”不删除任何事件：在更早的事件上开新分支并切换过去，原分支原样保留。
  * 设定库（canon / cast）是全局现行设定，不随分支分开（见方案 6.1）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import repo, store


class TimelineError(ValueError):
    pass


def list_branches(wid: str) -> List[Dict[str, Any]]:
    meta = repo.load_meta(wid)
    out = []
    for bid, info in repo.load_branches(wid).items():
        out.append({"id": bid, **info, "events": len(repo.load_events(wid, bid)),
                    "active": bid == meta["active_branch"]})
    return out


def fork(wid: str, name: str, from_event_id: Optional[str] = None, *, switch: bool = True) -> str:
    """从 from_event_id（含）之前的历史开新分支；None 表示从当前分支末尾开分支。"""
    meta = repo.load_meta(wid)
    src_bid = meta["active_branch"]
    events = repo.load_events(wid, src_bid)
    if from_event_id is None:
        kept = list(events)
    else:
        idx = next((i for i, e in enumerate(events) if e["id"] == from_event_id), None)
        if idx is None:
            raise TimelineError(f"分叉点不在当前分支中：{from_event_id}")
        kept = events[: idx + 1]
    bid = store.new_id("br")
    repo.write_events(wid, bid, kept)
    branches = repo.load_branches(wid)
    branches[bid] = {"name": name.strip() or f"分支 {len(branches)}", "parent": src_bid,
                     "fork_event_id": from_event_id, "created_at": store.now_iso()}
    repo.save_branches(wid, branches)
    if switch:
        meta["active_branch"] = bid
        repo.save_meta(meta)
    return bid


def switch(wid: str, bid: str) -> None:
    if bid not in repo.load_branches(wid):
        raise TimelineError(f"分支不存在：{bid}")
    meta = repo.load_meta(wid)
    meta["active_branch"] = bid
    repo.save_meta(meta)


def compare(wid: str, a: str, b: str) -> Dict[str, Any]:
    """对比两条分支：共同前缀长度、各自独有的事件。"""
    ea, eb = repo.load_events(wid, a), repo.load_events(wid, b)
    common = 0
    for x, y in zip(ea, eb):
        if x["id"] != y["id"]:
            break
        common += 1
    return {"common": common, "only_a": ea[common:], "only_b": eb[common:],
            "a": a, "b": b, "diverged_after": ea[common - 1]["id"] if common else None}
