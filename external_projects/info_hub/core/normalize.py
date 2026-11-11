"""把适配器产出的 RawItem 归一化为统一 Item 结构（纯文本可读的 dict）。"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from adapters.base import RawItem, SourceSpec


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def item_key(spec: SourceSpec, raw: RawItem) -> str:
    """稳定 id：优先用 url，没有 url 时用标题哈希。"""
    dedup_by = spec.policy.get("dedup_by", "url")
    if dedup_by == "title" or not raw.url:
        basis = raw.title.strip()
    else:
        basis = raw.url.strip()
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    return f"{spec.id}:{digest}"


def normalize(spec: SourceSpec, raw: RawItem, fetched_at: str) -> dict[str, Any]:
    return {
        "id": item_key(spec, raw),
        "source_id": spec.id,
        "category": spec.category,
        "title": raw.title,
        "url": raw.url,
        "summary": raw.summary,
        "rank": raw.rank,
        "heat": raw.heat,
        "published_at": raw.published_at,
        "fetched_at": fetched_at,
        "extra": raw.extra,
    }
