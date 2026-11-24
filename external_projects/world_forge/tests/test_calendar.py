import json

import pytest

from world_forge import calendar, engine, repo, store
from world_forge.calendar import format_time, step_minutes, validate_step


def test_unit_conversion_is_consistent():
    m = calendar.MINUTES_PER_UNIT
    assert m["hour"] == 60 * m["minute"]
    assert m["day"] == 24 * m["hour"]
    assert m["week"] == 7 * m["day"]
    assert m["month"] == 30 * m["day"]
    assert m["year"] == 12 * m["month"]


def test_step_minutes_for_each_unit():
    for unit, factor in calendar.MINUTES_PER_UNIT.items():
        assert step_minutes({"step_amount": 3, "step_unit": unit}) == 3 * factor


def test_format_time_shows_date_weekday_and_clock():
    # 第 0 年 第 1 月 第 1 日 周一 00:00
    assert format_time(0) == "第0年1月1日 周一 00:00"
    # 第 1 天 周二 13:05
    assert format_time(calendar.MINUTES_PER_UNIT["day"] + 13 * 60 + 5) == "第0年1月2日 周二 13:05"
    # 第 1 年 第 1 月 第 1 日
    assert format_time(calendar.MINUTES_PER_UNIT["year"]).startswith("第1年1月1日")


def test_legacy_year_clock_and_settings_migrate():
    meta = {"clock": {"year": 3}, "settings": {"years_per_step": 5, "auto_minor": True}}
    calendar.normalize_meta(meta)
    assert meta["clock"] == {"minutes": 3 * calendar.MINUTES_PER_UNIT["year"]}
    assert meta["settings"]["step_amount"] == 5
    assert meta["settings"]["step_unit"] == "year"
    assert "years_per_step" not in meta["settings"]


def test_validate_step_rejects_bad_input():
    assert validate_step("4", "day") == (4, "day")
    with pytest.raises(ValueError):
        validate_step(0, "day")
    with pytest.raises(ValueError):
        validate_step(1, "decade")
    with pytest.raises(ValueError):
        validate_step("abc", "day")


def test_advance_in_days_uses_step_amount_and_unit(world, llm):
    engine.advance(llm, world, 1, amount=3, unit="day")
    meta = repo.load_meta(world)
    assert meta["clock"]["minutes"] == 3 * calendar.MINUTES_PER_UNIT["day"]
    # 设置没有被临时覆盖改写
    assert meta["settings"]["step_unit"] == "year"


def test_advance_per_run_override_does_not_change_settings(world, llm):
    engine.advance(llm, world, 2, amount=6, unit="hour")
    meta = repo.load_meta(world)
    assert meta["clock"]["minutes"] == 2 * 6 * 60
    assert meta["settings"]["step_amount"] == 10 and meta["settings"]["step_unit"] == "year"


def test_events_record_minute_year_and_label(world, llm):
    engine.advance(llm, world, 1, amount=2, unit="week")
    ev = repo.load_events(world, "main")[-1]
    assert ev["minute"] == 0
    assert ev["year"] == 0
    assert ev["t"].startswith("第0年1月1日")


def test_each_unit_moves_clock_by_expected_amount(world, llm):
    for unit in calendar.MINUTES_PER_UNIT:
        before = repo.load_meta(world)["clock"]["minutes"]
        engine.advance(llm, world, 1, amount=1, unit=unit)
        after = repo.load_meta(world)["clock"]["minutes"]
        assert after - before == calendar.MINUTES_PER_UNIT[unit]


def test_advance_log_mentions_time_scale(world, llm):
    out = engine.advance(llm, world, 1, amount=2, unit="month")
    start = next(e for e in out["log"] if e["stage"] == "start" and "每步" in e["msg"])
    assert "2 月" in start["msg"]


def test_settings_persist_step_unit(world):
    meta = repo.load_meta(world)
    meta["settings"].update(step_amount=7, step_unit="week")
    repo.save_meta(meta)
    again = repo.load_meta(world)
    assert again["settings"]["step_amount"] == 7 and again["settings"]["step_unit"] == "week"
