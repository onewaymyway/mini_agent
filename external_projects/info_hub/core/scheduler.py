"""定时判定：信息源抓取间隔与报告计划的到期判断。

时间表达式：
- every: "60m" / "3h" / "1d"（仅支持单位 m/h/d，cron 表达式待后续阶段支持）
- at:    "08:00"（每天固定时刻）

调度状态保存在 data/state/_scheduler.json（纯文本 json）：
{"sources": {"<id>": "<上次运行时间>"}, "reports": {"<plan_id>": "<上次生成时间>"}}
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Any, Optional

_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*([mhd])\s*$")


def parse_interval(text: str) -> timedelta:
    m = _INTERVAL_RE.match(str(text))
    if not m:
        raise ValueError(f"不支持的间隔表达式: {text!r}，应为 Nm / Nh / Nd")
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        raise ValueError(f"间隔必须大于 0: {text!r}")
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


def parse_at(text: str) -> time:
    try:
        hh, mm = str(text).split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"不支持的时刻表达式: {text!r}，应为 HH:MM") from exc


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def is_due(plan: dict[str, Any], last_run: Optional[str], now: datetime) -> bool:
    """判断报告计划在 now 时是否到期。

    every 类计划：距离上次运行已达到间隔即到期。
    at 类计划：当天的 at 时刻已经过去，且上次运行早于当天的 at 时刻。
    """
    last = _parse_ts(last_run)
    if "every" in plan:
        interval = parse_interval(plan["every"])
        return last is None or now - last >= interval
    if "at" in plan:
        at_today = datetime.combine(now.date(), parse_at(plan["at"]), tzinfo=now.tzinfo)
        if now < at_today:
            return False
        return last is None or last < at_today
    raise ValueError(f"报告计划 {plan.get('id')} 需要 every 或 at 之一")


def source_is_due(every: str, last_run: Optional[str], now: datetime) -> bool:
    last = _parse_ts(last_run)
    return last is None or now - last >= parse_interval(every)
