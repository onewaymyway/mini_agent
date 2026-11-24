"""world_forge/calendar.py — 世界时钟与历法。

时钟以“分钟”为基本单位，记录从世界起点经过的总分钟数（clock.minutes）。
推进步长 = settings.step_amount × settings.step_unit，单位可以是分钟、小时、天、星期、月、年。

架空历法（固定，便于换算与显示）：
  1 小时 = 60 分钟；1 天 = 24 小时；1 星期 = 7 天；1 月 = 30 天；1 年 = 12 月。
  显示格式：第 Y 年 M 月 D 日 周X HH:MM（年、月、日从 0/1 起编号，与既有“第 0 年”的约定一致）。
"""

from __future__ import annotations

from typing import Any, Dict

MINUTES_PER_UNIT = {
    "minute": 1,
    "hour": 60,
    "day": 60 * 24,
    "week": 60 * 24 * 7,
    "month": 60 * 24 * 30,
    "year": 60 * 24 * 30 * 12,
}
UNIT_LABELS = {"minute": "分钟", "hour": "小时", "day": "天", "week": "星期", "month": "月", "year": "年"}
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
MAX_STEP_AMOUNT = 100000
DEFAULT_STEP = {"step_amount": 10, "step_unit": "year"}


def step_minutes(settings: Dict[str, Any]) -> int:
    unit = settings.get("step_unit", "year")
    amount = int(settings.get("step_amount", 10))
    return amount * MINUTES_PER_UNIT[unit]


def step_label(amount: int, unit: str) -> str:
    return f"{amount} {UNIT_LABELS.get(unit, unit)}"


def validate_step(amount: Any, unit: Any) -> tuple:
    if unit not in MINUTES_PER_UNIT:
        raise ValueError(f"不支持的时间单位：{unit}")
    try:
        amount = int(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError("推进步长必须是整数") from exc
    if not 1 <= amount <= MAX_STEP_AMOUNT:
        raise ValueError(f"推进步长必须在 1–{MAX_STEP_AMOUNT} 之间")
    return amount, unit


def normalize_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """把旧版元信息转换为当前结构（原地修改并返回）。

    旧版：clock = {"year": N}，settings.years_per_step = N（单位为年）。
    新版：clock = {"minutes": M}，settings.step_amount / step_unit。
    """
    clock = meta.get("clock") or {}
    if "minutes" not in clock:
        clock = {"minutes": int(clock.get("year", 0)) * MINUTES_PER_UNIT["year"]}
    meta["clock"] = {"minutes": int(clock["minutes"])}
    settings = meta.setdefault("settings", {})
    if "years_per_step" in settings:
        settings.setdefault("step_amount", int(settings.pop("years_per_step")))
        settings.setdefault("step_unit", "year")
    for k, v in DEFAULT_STEP.items():
        settings.setdefault(k, v)
    return meta


def clock_minutes(meta: Dict[str, Any]) -> int:
    return int(meta["clock"]["minutes"])


def year_of(minutes: int) -> int:
    return minutes // MINUTES_PER_UNIT["year"]


def format_time(minutes: int) -> str:
    minutes = int(minutes)
    days, rem = divmod(minutes, 60 * 24)
    hh, mm = divmod(rem, 60)
    year = days // 360
    month = (days // 30) % 12 + 1
    day = days % 30 + 1
    return f"第{year}年{month}月{day}日 {WEEKDAYS[days % 7]} {hh:02d}:{mm:02d}"


def format_year(minutes: int) -> str:
    return f"第{year_of(minutes)}年"
