"""执行单个信息源：调用适配器、去重与增量对比、写原始快照、追加新条目、记录运行日志。"""
from __future__ import annotations

import time
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from adapters.base import FetchContext, SourceSpec
from adapters.registry import get_adapter
from core import health
from core.normalize import normalize, now_iso
from core.pipeline import process
from core.settings import Settings
from core.store import append_jsonl, atomic_write_json, file_lock, read_json


@dataclass
class RunResult:
    source_id: str
    ok: bool
    item_count: int
    duration_sec: float
    error: Optional[str] = None
    raw_path: Optional[str] = None
    new_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def format_run_line(when: datetime, result: RunResult) -> str:
    """运行记录的固定格式（| 分隔），便于 digest 可靠解析。

    成功：- HH:MM:SS `源id` 成功 | 抓取 N 条 | 新增 M 条 | 耗时 Xs
    失败：- HH:MM:SS `源id` 失败 | 错误：<信息> | 耗时 Xs
    """
    if result.ok:
        body = f"成功 | 抓取 {result.item_count} 条 | 新增 {result.new_count} 条"
    else:
        body = f"失败 | 错误：{result.error}"
    return f"- {when:%H:%M:%S} `{result.source_id}` {body} | 耗时 {result.duration_sec:.2f}s\n"


def _write_run_log(settings: Settings, when: datetime, result: RunResult) -> None:
    path = settings.paths.runs_dir / f"{when:%Y-%m-%d}.log.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        if not path.exists():
            path.write_text(f"# 运行记录 {when:%Y-%m-%d}\n\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(format_run_line(when, result))


def _record_health(settings: Settings, result: "RunResult") -> None:
    """健康记录失败不能影响抓取结果本身。"""
    try:
        health.record_result(settings, result.source_id, result.ok, result.error)
    except Exception as exc:  # noqa: BLE001
        print(f"[health] 记录失败: {type(exc).__name__}: {exc}")


def default_context(settings: Settings) -> FetchContext:
    """构造默认运行上下文：共享浏览器会话（惰性启动）与站点配置。"""
    from core.dedicated_browser import make_browser_session
    from core.settings import load_sites

    return FetchContext(
        browser=make_browser_session(settings),
        sites=load_sites(settings.paths.root),
    )


DEBUG_KEEP = 30  # 每个信息源保留最近的失败调试文件数量


def debug_dir(settings: Settings, source_id: str) -> Path:
    return settings.paths.data_dir / "debug" / source_id


def _failure_extra(settings: Settings, source_id: str, when: datetime, error: Optional[str],
                   meta: dict[str, Any]) -> dict[str, Any]:
    """失败时的调试信息：完整写入 data/debug/<id>/<时间>.json（含原文），返回给看板的摘要（含原文截断版）。

    没有调试信息的适配器失败时返回空字典，保持原有行为。
    """
    debug = (meta or {}).get("debug")
    if not isinstance(debug, dict):
        return {}
    try:
        folder = debug_dir(settings, source_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{when:%Y%m%d-%H%M%S}.json"
        atomic_write_json(path, {"source_id": source_id, "at": when.isoformat(timespec="seconds"),
                                 "error": error, "debug": debug})
        for old in sorted(folder.glob("*.json"))[:-DEBUG_KEEP]:
            old.unlink(missing_ok=True)
        rel = str(path.relative_to(settings.paths.root))
    except OSError as exc:  # 调试文件写入失败不能影响抓取结果本身
        rel = f"（写入失败：{exc}）"
    return {"debug": debug, "debug_path": rel}


def run_source(spec: SourceSpec, settings: Settings, ctx: Optional[FetchContext] = None) -> RunResult:
    ctx = ctx or default_context(settings)
    when = datetime.now().astimezone()
    fetched_at = now_iso()
    started = time.monotonic()

    adapter = get_adapter(spec.adapter)
    if adapter is None:
        result = RunResult(spec.id, False, 0, 0.0, error=f"未找到适配器 {spec.adapter}")
        _write_run_log(settings, when, result)
        _record_health(settings, result)
        return result

    fetch = adapter.fetch(spec, ctx)
    duration = time.monotonic() - started
    if not fetch.ok:
        extra = _failure_extra(settings, spec.id, when, fetch.error, fetch.meta)
        result = RunResult(spec.id, False, 0, duration, error=fetch.error, extra=extra)
        _write_run_log(settings, when, result)
        _record_health(settings, result)
        return result

    items = [normalize(spec, raw, fetched_at) for raw in fetch.items]

    day = f"{when:%Y-%m-%d}"
    state_path = settings.paths.state_dir / f"{spec.id}.json"
    # 状态读取、整理、写回放在同一把锁内，防止定时任务重叠时状态错乱
    with file_lock(state_path):
        state = read_json(state_path, default=None)
        outcome = process(spec, items, state, fetched_at, now=when)
        atomic_write_json(state_path, outcome.state)

    snapshot_path = settings.paths.raw_dir / spec.id / day / f"{when:%H%M%S}.json"
    atomic_write_json(
        snapshot_path,
        {
            "source_id": spec.id,
            "fetched_at": fetched_at,
            "item_count": len(items),
            "new_count": len(outcome.new_items),
            "changes": outcome.changes,
            "meta": fetch.meta,
            "items": items,
        },
    )

    items_path = settings.paths.items_dir / spec.id / f"{day}.jsonl"
    with file_lock(items_path):
        for item in outcome.new_items:
            append_jsonl(items_path, item)

    result = RunResult(
        spec.id,
        True,
        len(items),
        duration,
        raw_path=str(snapshot_path.relative_to(settings.paths.root)),
        new_count=len(outcome.new_items),
        extra=fetch.meta,
    )
    _write_run_log(settings, when, result)
    _record_health(settings, result)
    return result
