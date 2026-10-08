"""cron 星期字段必须按标准语义（0/7=周日，1=周一）匹配，而不是 Python 的 tm_wday（周一=0）。"""
import datetime as dt

from mini_agent.evolution import cron_scheduler as cs


def _next(expr, y, m, d, hh=0, mm=0):
    base = dt.datetime(y, m, d, hh, mm).timestamp()
    return dt.datetime.fromtimestamp(cs._next_cron(expr, after=base))


def test_weekday_range_1_5_is_monday_to_friday():
    # 2026-10-05 是周一
    assert _next("30 15 * * 1-5", 2026, 10, 5, 9).strftime("%a %H:%M") == "Mon 15:30"
    # 周五 15:30 之后跳过周末 → 下周一
    assert _next("30 15 * * 1-5", 2026, 10, 9, 16).strftime("%a %F") == "Mon 2026-10-12"


def test_weekday_never_fires_on_saturday_or_sunday():
    seen = set()
    base = dt.datetime(2026, 10, 5, 0, 0).timestamp()
    for _ in range(10):
        n = cs._next_cron("0 9 * * 1-5", after=base)
        seen.add(dt.datetime.fromtimestamp(n).weekday())
        base = n
    assert seen == {0, 1, 2, 3, 4}


def test_sunday_accepts_0_and_7():
    assert _next("0 8 * * 0", 2026, 10, 5).strftime("%a") == "Sun"
    assert _next("0 8 * * 7", 2026, 10, 5).strftime("%a") == "Sun"
    assert _next("0 8 * * 6,7", 2026, 10, 5).strftime("%a") == "Sat"
