"""stock_watch/source_health.py — 数据源级别的成败记录（细粒度信号）。

对应 `next_doc/stock_watch_continuous_improvement_plan.md` 第 3.1 节：
`run_status.jsonl`（框架账本）只记"entrypoint 整体成功/失败"，掩盖了
"某个数据源持续半失效但还没拖累整体判定"这类趋势。本模块是 stock_watch
自己的补充账本，记录每次抓取尝试里，每个数据源本身是否成功、耗时、
返回条数——这是股票系统特有的"多数据源"结构，不需要框架理解，因此
放在项目自己的库代码里，而不是上收进 `mini_agent.external_projects`。

写法沿用框架 `ledger.py`/`backlog.py` 已经验证过的模式：JSONL 追加、
损坏行跳过、路径不存在时返回空列表。
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional


@dataclass
class SourceHealthRecord:
    source: str
    entrypoint: str
    ok: bool
    duration_sec: float
    item_count: int = 0
    error: Optional[str] = None
    recorded_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SourceHealthRecord":
        return cls(
            source=data.get("source", ""),
            entrypoint=data.get("entrypoint", ""),
            ok=bool(data.get("ok", False)),
            duration_sec=float(data.get("duration_sec", 0.0)),
            item_count=int(data.get("item_count", 0)),
            error=data.get("error"),
            recorded_at=data.get("recorded_at", ""),
        )


def record(
    path: Path,
    *,
    source: str,
    entrypoint: str,
    ok: bool,
    duration_sec: float,
    item_count: int = 0,
    error: Optional[str] = None,
) -> SourceHealthRecord:
    rec = SourceHealthRecord(
        source=source, entrypoint=entrypoint, ok=ok,
        duration_sec=round(duration_sec, 3), item_count=item_count,
        error=error, recorded_at=datetime.now(timezone.utc).isoformat(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
    return rec


@contextmanager
def tracked_source(
    path: Path, *, source: str, entrypoint: str
) -> Iterator["_SourceHandle"]:
    """用法同 `ledger.track_run`，但记的是单个数据源一次抓取尝试：

        with tracked_source(path, source="eastmoney_hot_rank", entrypoint="hotlist_scan") as h:
            items = fetch_eastmoney_hot_rank()
            h.item_count = len(items)

    正常退出记一条成功；块内抛 `DataSourceError`（或任意异常）记一条
    失败并把异常继续向外抛出——不吞异常，调用方（entrypoint）自己决定
    要不要捕获、跳过该数据源继续跑其它数据源。
    """
    handle = _SourceHandle()
    start = time.monotonic()
    try:
        yield handle
        handle.ok = True
    except Exception as exc:  # noqa: BLE001
        handle.ok = False
        handle.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        duration = time.monotonic() - start
        record(
            path, source=source, entrypoint=entrypoint, ok=handle.ok,
            duration_sec=duration, item_count=handle.item_count, error=handle.error,
        )


class _SourceHandle:
    def __init__(self) -> None:
        self.ok = False
        self.item_count = 0
        self.error: Optional[str] = None


def read_source_health(path: Path, *, limit: Optional[int] = None) -> List[SourceHealthRecord]:
    if not path.exists():
        return []
    records: List[SourceHealthRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(SourceHealthRecord.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue
    if limit is not None:
        records = records[-limit:]
    return records


def failure_rate_by_source(records: List[SourceHealthRecord]) -> dict:
    """按数据源汇总失败率，供 review session/人工快速判断"哪个源经常挂"。"""
    tally: dict = {}
    for rec in records:
        bucket = tally.setdefault(rec.source, {"total": 0, "failed": 0})
        bucket["total"] += 1
        if not rec.ok:
            bucket["failed"] += 1
    return {
        source: {
            **counts,
            "failure_rate": (counts["failed"] / counts["total"]) if counts["total"] else 0.0,
        }
        for source, counts in tally.items()
    }
