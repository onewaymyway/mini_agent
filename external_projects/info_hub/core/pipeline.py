"""整理流水线：去重、增量对比、热榜排名变化。

状态文件 data/state/<source_id>.json（纯文本 json）结构：
{
  "source_id": "...",
  "seen": {"<item_id>": "<首次见到的时间>"},
  "last_snapshot": {"fetched_at": "...", "ranks": {"<item_id>": 1, ...}}
}

changes 字段说明（仅 hot_list 有排名类变化）：
- new：本次首次出现的条目
- rising / falling：与上一快照相比排名上升 / 下降，附带 from / to
- dropped：上一快照存在、本次掉出榜单的条目
对于 feed / recommend / custom，只输出 new，去重后的条目才写入 items。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from adapters.base import SourceSpec


@dataclass
class PipelineResult:
    new_items: list[dict[str, Any]]
    changes: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)


def empty_state(source_id: str) -> dict[str, Any]:
    return {"source_id": source_id, "seen": {}, "last_snapshot": None}


def _prune_seen(seen: dict[str, str], retain_days: int, now: datetime) -> dict[str, str]:
    cutoff = now - timedelta(days=retain_days)
    kept: dict[str, str] = {}
    for item_id, first_seen in seen.items():
        try:
            ts = datetime.fromisoformat(first_seen)
        except (TypeError, ValueError):
            kept[item_id] = first_seen
            continue
        if ts >= cutoff:
            kept[item_id] = first_seen
    return kept


def _hot_list_changes(items: list[dict[str, Any]], last: Optional[dict[str, Any]]) -> dict[str, Any]:
    if last is None:
        return {"first_snapshot": True, "new": [], "rising": [], "falling": [], "dropped": []}
    prev_ranks: dict[str, int] = last.get("ranks", {})
    cur_ids = {it["id"] for it in items}
    new, rising, falling = [], [], []
    for it in items:
        rank = it.get("rank")
        if it["id"] not in prev_ranks:
            new.append({"id": it["id"], "title": it["title"], "rank": rank})
            continue
        prev = prev_ranks[it["id"]]
        if rank is None:
            continue
        if rank < prev:
            rising.append({"id": it["id"], "title": it["title"], "from": prev, "to": rank})
        elif rank > prev:
            falling.append({"id": it["id"], "title": it["title"], "from": prev, "to": rank})
    dropped = [
        {"id": iid, "from": rank}
        for iid, rank in prev_ranks.items()
        if iid not in cur_ids
    ]
    return {"first_snapshot": False, "new": new, "rising": rising, "falling": falling, "dropped": dropped}


def process(
    spec: SourceSpec,
    items: list[dict[str, Any]],
    state: Optional[dict[str, Any]],
    fetched_at: str,
    now: Optional[datetime] = None,
) -> PipelineResult:
    """对本次抓取的条目做去重与变化计算，返回新状态（不写盘）。"""
    now = now or datetime.now().astimezone()
    state = state or empty_state(spec.id)
    retain_days = int(spec.policy.get("retain_days", 14))
    seen = _prune_seen(dict(state.get("seen", {})), retain_days, now)

    new_items = [it for it in items if it["id"] not in seen]
    for it in new_items:
        seen[it["id"]] = fetched_at

    if spec.kind == "hot_list":
        changes = _hot_list_changes(items, state.get("last_snapshot"))
    else:
        changes = {"new": [{"id": it["id"], "title": it["title"]} for it in new_items]}

    new_state = {
        "source_id": spec.id,
        "seen": seen,
        "last_snapshot": {
            "fetched_at": fetched_at,
            "ranks": {it["id"]: it["rank"] for it in items if it.get("rank") is not None},
        },
    }
    return PipelineResult(new_items=new_items, changes=changes, state=new_state)
