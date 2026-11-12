"""按时间窗口合并多个信息源的抓取结果（看板“抓取结果 → 分类合并”用）。

读取 data/raw/<源>/<日期>/<时分秒>.json 快照，只保留窗口内的快照；同一信息源里同一条目（按条目 id）在多次快照中出现只算一条：
- first_seen：窗口内首次出现的抓取时间（用于“最新出现在前”排序与显示）；last_seen：最近一次出现；
- rank / heat / summary 等取最近一次快照里的值；
- is_new：窗口内某次快照把它标为“新增”（真正第一次出现的条目），区别于一直挂在榜上的老条目；
- seen_count：窗口内出现过的快照次数；first_rank：首次出现时的排名（与 rank 比较可得排名变化）。
只读文件，不修改任何数据。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from core.store import read_json

MAX_SNAPSHOTS_PER_SOURCE = 600  # 约 12 天（30 分钟一次）；超过时只取窗口内最新的，并标记 truncated


def _parse(value: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.astimezone()


def _snapshot_files(raw_dir: Path, source_id: str, since: datetime) -> list[Path]:
    """窗口可能覆盖的快照文件（按日期目录粗筛，升序）。精确的时间判断在读取内容后进行。"""
    base = Path(raw_dir) / source_id
    if not base.is_dir():
        return []
    first_day = since.strftime("%Y-%m-%d")
    files: list[Path] = []
    for day_dir in sorted(p for p in base.iterdir() if p.is_dir() and p.name >= first_day):
        files.extend(sorted(day_dir.glob("*.json")))
    return files


def merge_source(raw_dir: Path, source_id: str, since: datetime) -> dict[str, Any]:
    files = _snapshot_files(raw_dir, source_id, since)
    truncated = len(files) > MAX_SNAPSHOTS_PER_SOURCE
    if truncated:
        files = files[-MAX_SNAPSHOTS_PER_SOURCE:]
    items: dict[str, dict[str, Any]] = {}
    snapshots = 0
    for f in files:
        try:
            data = read_json(f, default=None)
        except (OSError, ValueError):
            continue  # 损坏的快照跳过，不影响其余
        if not isinstance(data, dict):
            continue
        fetched = _parse(data.get("fetched_at"))
        if fetched is None or fetched < since:
            continue
        snapshots += 1
        new_ids = {e.get("id") for e in ((data.get("changes") or {}).get("new") or []) if isinstance(e, dict)}
        for it in data.get("items") or []:
            if not isinstance(it, dict) or not it.get("id"):
                continue
            cur = items.get(it["id"])
            when = data.get("fetched_at")
            if cur is None:
                # first_rank：窗口内首次出现时的排名（用于计算“排名上升”）
                cur = {"first_seen": when, "first_rank": it.get("rank"), "seen_count": 0, "is_new": False}
                items[it["id"]] = cur
            cur.update({k: it.get(k) for k in ("id", "title", "url", "summary", "rank", "heat", "published_at")})
            cur["source_id"] = source_id
            cur["last_seen"] = when
            cur["seen_count"] += 1
            if it["id"] in new_ids:
                cur["is_new"] = True
    return {"items": list(items.values()), "snapshots": snapshots, "truncated": truncated}


def merge_sources(raw_dir: Path, source_ids: list[str], hours: float,
                  now: Optional[datetime] = None) -> dict[str, Any]:
    current = now or datetime.now().astimezone()
    since = current - timedelta(hours=hours)
    all_items: list[dict[str, Any]] = []
    per_source: dict[str, dict[str, Any]] = {}
    for sid in source_ids:
        res = merge_source(raw_dir, sid, since)
        per_source[sid] = {"snapshots": res["snapshots"], "items": len(res["items"]), "truncated": res["truncated"]}
        all_items.extend(res["items"])
    return {"items": all_items, "since": since.isoformat(timespec="seconds"), "sources": per_source}
