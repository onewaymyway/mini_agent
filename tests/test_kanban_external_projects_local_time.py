"""tests/test_kanban_external_projects_local_time.py — 看板「🗂️ 外部项目」tab
时间字段统一显示本地时间（`_to_local_time_str`）。

用例里通过设置 `TZ` + `time.tzset()` 固定本地时区，避免测试结果依赖
运行机器的时区。Windows 没有 `tzset`，整体跳过。
"""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

import app  # noqa: E402

pytestmark = pytest.mark.skipif(not hasattr(time, "tzset"), reason="需要 time.tzset")


@pytest.fixture(autouse=True)
def _shanghai_tz():
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Shanghai"  # UTC+8，无夏令时
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_utc_z_suffix_converted_to_local():
    assert app._to_local_time_str("2026-10-14T02:30:00Z") == "2026-10-14 10:30:00"


def test_utc_offset_zero_converted_to_local():
    assert app._to_local_time_str("2026-10-14T02:30:00+00:00") == "2026-10-14 10:30:00"


def test_other_offset_converted_to_local():
    # 东京 +09:00 的 11:30 == 上海 10:30
    assert app._to_local_time_str("2026-10-14T11:30:00+09:00") == "2026-10-14 10:30:00"


def test_local_iso_with_matching_offset_unchanged():
    assert app._to_local_time_str("2026-10-14T10:30:00+08:00") == "2026-10-14 10:30:00"


def test_naive_iso_treated_as_local():
    assert app._to_local_time_str("2026-10-14T10:30:00") == "2026-10-14 10:30:00"


def test_fractional_seconds_supported():
    assert app._to_local_time_str("2026-10-14T02:30:00.123456+00:00") == "2026-10-14 10:30:00"


def test_epoch_seconds_converted_to_local():
    # 2026-10-14T02:30:00Z
    assert app._to_local_time_str(1791945000) == "2026-10-14 10:30:00"


def test_custom_format():
    assert app._to_local_time_str("2026-10-14T02:30:00Z", "%m-%d %H:%M") == "10-14 10:30"


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_returns_empty_string(empty):
    assert app._to_local_time_str(empty) == ""


def test_unparseable_returned_as_is():
    assert app._to_local_time_str("昨天下午") == "昨天下午"
