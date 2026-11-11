"""执行单个信息源：调用适配器、写原始快照、追加条目、记录运行日志。

P1 阶段只做"写入"；去重与增量对比在 P2 的 pipeline 中加入。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from adapters.base import FetchContext, SourceSpec
from adapters.registry import get_adapter
from core.normalize import normalize, now_iso
from core.settings import Settings
from core.store import append_jsonl, atomic_write_json, file_lock


@dataclass
class RunResult:
    source_id: str
    ok: bool
    item_count: int
    duration_sec: float
    error: Optional[str] = None
    raw_path: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


def _write_run_log(settings: Settings, when: datetime, result: RunResult) -> None:
    path = settings.paths.runs_dir / f"{when:%Y-%m-%d}.log.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    status = "成功" if result.ok else f"失败：{result.error}"
    line = (
        f"- {when:%H:%M:%S} `{result.source_id}` {status}，"
        f"{result.item_count} 条，耗时 {result.duration_sec:.2f}s\n"
    )
    with file_lock(path):
        if not path.exists():
            path.write_text(f"# 运行记录 {when:%Y-%m-%d}\n\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)


def run_source(spec: SourceSpec, settings: Settings, ctx: Optional[FetchContext] = None) -> RunResult:
    ctx = ctx or FetchContext()
    when = datetime.now().astimezone()
    fetched_at = now_iso()
    started = time.monotonic()

    adapter = get_adapter(spec.adapter)
    if adapter is None:
        result = RunResult(spec.id, False, 0, 0.0, error=f"未找到适配器 {spec.adapter}")
        _write_run_log(settings, when, result)
        return result

    fetch = adapter.fetch(spec, ctx)
    duration = time.monotonic() - started
    if not fetch.ok:
        result = RunResult(spec.id, False, 0, duration, error=fetch.error)
        _write_run_log(settings, when, result)
        return result

    items = [normalize(spec, raw, fetched_at) for raw in fetch.items]

    day = f"{when:%Y-%m-%d}"
    snapshot_path = settings.paths.raw_dir / spec.id / day / f"{when:%H%M%S}.json"
    atomic_write_json(
        snapshot_path,
        {
            "source_id": spec.id,
            "fetched_at": fetched_at,
            "item_count": len(items),
            "meta": fetch.meta,
            "items": items,
        },
    )

    items_path = settings.paths.items_dir / spec.id / f"{day}.jsonl"
    with file_lock(items_path):
        for item in items:
            append_jsonl(items_path, item)

    result = RunResult(
        spec.id,
        True,
        len(items),
        duration,
        raw_path=str(snapshot_path.relative_to(settings.paths.root)),
        extra=fetch.meta,
    )
    _write_run_log(settings, when, result)
    return result
