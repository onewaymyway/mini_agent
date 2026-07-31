"""external_input/poll_history.py — 外部输入网关可观测性（成功率/延迟趋势）。

背景/设计见 next_doc/external_input_reliability_observability_archive_plan.md
§3。`poller.py::SourceHealth` 只是运行时内存快照，重启即清零，也不记录
耗时，无法回答"这个来源最近 N 天成功率/延迟是否在变差"这类趋势性问题。

本模块只做两件事：
  1. `append_poll_record()` — 每次 `source.poll()` 完成后追加一条精简记录
     到 `paths.external_input_poll_history`，只追加、有滚动上限（跟
     `notification/dispatcher.py::_append_dispatch_log` 一样的处理方式）。
  2. `summarize_poll_history()` — 纯读取聚合，不消费游标、不改变任何状态，
     可以被高频调用（看板刷新）而没有副作用。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

# 滚动上限：默认轮询间隔多在分钟到半小时级别，5000 条覆盖的时间窗口对
# 大多数 source 配置来说是数天到数周，具体取决于 interval。
_MAX_LOG_LINES = 5000


def append_poll_record(
    paths: "AgentPaths",
    *,
    source_id: str,
    ok: bool,
    duration_ms: float,
    event_count: int = 0,
    error: Optional[str] = None,
    ts: Optional[float] = None,
) -> None:
    """追加一条轮询结果记录。写入失败不影响轮询主流程（沿用项目一贯
    "诊断记录写入失败不该拖垮正常功能"的原则），超出 `_MAX_LOG_LINES`
    时整体截断只保留最近 N 条。"""
    p = paths.external_input_poll_history
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "source_id": source_id,
            "ts": ts if ts is not None else time.time(),
            "ok": ok,
            "duration_ms": round(duration_ms, 1),
            "event_count": event_count,
            "error": error,
        }
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # 滚动截断：粗略估计——不是每次都数行数（成本高），改成"每追加
        # 一条都检查一次文件是否明显超限"太昂贵；这里采用简单策略：
        # 每 200 次追加做一次实际截断检查，兼顾成本与"不会无限增长"。
        _maybe_rotate(p)
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.poll_history.append_poll_record")


_rotate_counter = {"n": 0}


def _maybe_rotate(p, *, check_every: int = 200, max_lines: int = _MAX_LOG_LINES) -> None:
    _rotate_counter["n"] += 1
    if _rotate_counter["n"] % check_every != 0:
        return
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) > max_lines:
            p.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _load_records(paths: "AgentPaths") -> list[dict]:
    p = paths.external_input_poll_history
    if not p.exists():
        return []
    records: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return records


def _percentile(sorted_values: list[float], pct: float) -> Optional[float]:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1))))
    return sorted_values[idx]


def _summarize_group(records: list[dict]) -> dict:
    total = len(records)
    success = [r for r in records if r.get("ok")]
    failure = [r for r in records if not r.get("ok")]
    durations = sorted(
        float(r["duration_ms"]) for r in records if isinstance(r.get("duration_ms"), (int, float))
    )

    # 按天分桶（本地时间，"MM-DD"），供看板画趋势折线图。
    buckets: dict[str, dict] = {}
    for r in records:
        ts = r.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        date_key = time.strftime("%m-%d", time.localtime(ts))
        b = buckets.setdefault(date_key, {"total": 0, "success": 0, "duration_sum": 0.0, "duration_n": 0})
        b["total"] += 1
        if r.get("ok"):
            b["success"] += 1
        d = r.get("duration_ms")
        if isinstance(d, (int, float)):
            b["duration_sum"] += float(d)
            b["duration_n"] += 1

    timeline = []
    for date_key in sorted(buckets.keys()):
        b = buckets[date_key]
        timeline.append({
            "date": date_key,
            "success_rate": round(b["success"] / b["total"], 4) if b["total"] else None,
            "avg_duration_ms": round(b["duration_sum"] / b["duration_n"], 1) if b["duration_n"] else None,
        })

    return {
        "total_polls": total,
        "success_count": len(success),
        "failure_count": len(failure),
        "success_rate": round(len(success) / total, 4) if total else None,
        "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
        "p50_duration_ms": _percentile(durations, 0.50),
        "p95_duration_ms": _percentile(durations, 0.95),
        "timeline": timeline,
    }


def summarize_poll_history(
    paths: "AgentPaths",
    *,
    source_id: Optional[str] = None,
    since_days: int = 7,
) -> dict:
    """按 source_id 分组（或只看某一个），返回聚合结果：
        - total_polls / success_count / failure_count / success_rate
        - avg_duration_ms / p50_duration_ms / p95_duration_ms
        - timeline: 按天分桶的时间序列（供看板画趋势折线图）

    纯读取聚合，不消费游标、不改变任何状态，可以被高频调用（看板刷新）
    而没有副作用。`source_id` 为 None 时返回全部 source 各自的聚合。
    """
    records = _load_records(paths)
    cutoff = time.time() - since_days * 86400 if since_days and since_days > 0 else None
    if cutoff is not None:
        records = [r for r in records if isinstance(r.get("ts"), (int, float)) and r["ts"] >= cutoff]

    if source_id is not None:
        records = [r for r in records if r.get("source_id") == source_id]
        return {"source_id": source_id, "since_days": since_days, **_summarize_group(records)}

    by_source: dict[str, list[dict]] = {}
    for r in records:
        by_source.setdefault(r.get("source_id", "?"), []).append(r)

    return {
        "since_days": since_days,
        "sources": {
            sid: _summarize_group(recs) for sid, recs in by_source.items()
        },
    }


__all__ = [
    "append_poll_record",
    "summarize_poll_history",
]
