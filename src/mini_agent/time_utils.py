"""time_utils.py — 统一本地时间工具

项目里此前存在大量 `datetime.now(timezone.utc)` / `datetime.utcnow()` 的用法，
导致日志、记录里保存的时间戳按 UTC 显示，与用户本地时间不一致。

本模块提供统一的本地时间函数，所有需要记录时间的地方都应通过这里获取：

- `now_ts()`      : 当前时间的 Unix 时间戳（float，与时区无关，可用于排序/计算）
- `now_dt()`      : 当前本地时间的 datetime（tz-naive，系统本地时区）
- `now_str()`     : 当前本地时间的可读字符串 "YYYY-MM-DD HH:MM:SS"
- `ts_to_str(ts)` : 把一个 Unix 时间戳转换为本地时间可读字符串
- `iso_local()`   : 当前本地时间的 ISO 8601 字符串（带本地时区偏移，如 +08:00）

约定：任何持久化保存时间戳（无论是 epoch float 还是 ISO 字符串）的地方，
都应该同时保存一个由 `now_str()` / `ts_to_str()` 生成的可读本地时间字符串，
字段名统一使用 `<字段名>_str`，例如 `ts` + `ts_str`，`created_at` + `created_at_str`。
"""

from __future__ import annotations

import time as _time
from datetime import datetime

STR_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_ts() -> float:
    """当前时间的 Unix 时间戳（epoch seconds），与时区无关。"""
    return _time.time()


def now_dt() -> datetime:
    """当前本地时间（tz-naive，使用系统本地时区）。"""
    return datetime.now()


def now_str() -> str:
    """当前本地时间的可读字符串，如 '2026-07-11 14:30:05'。"""
    return now_dt().strftime(STR_FORMAT)


def ts_to_str(ts: float | None) -> str:
    """把一个 Unix 时间戳转换为本地时间可读字符串。ts 为 None 时返回空字符串。"""
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime(STR_FORMAT)
    except (OverflowError, OSError, ValueError):
        return ""


def iso_local() -> str:
    """当前本地时间的 ISO 8601 字符串，带本地时区偏移（如 2026-07-11T14:30:05+08:00）。"""
    return datetime.now().astimezone().isoformat()


def iso_to_str(iso_ts: str | None) -> str:
    """把一个 ISO 8601 时间字符串转换为本地时间可读字符串，转换失败时原样返回。"""
    if not iso_ts:
        return ""
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime(STR_FORMAT)
    except ValueError:
        return iso_ts
