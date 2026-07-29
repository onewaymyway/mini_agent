"""
tests/test_cron_schedule_validation.py — validate_schedule() 格式校验

对应看板"新建 cron job"表单的前置校验（见
next_doc/cron_dedicated_execution_improvement_plan.md 剩余工作 #4）。
"""

from __future__ import annotations

import pytest

from mini_agent.evolution.cron_scheduler import validate_schedule


class TestValidateScheduleInterval:
    def test_valid_interval(self):
        assert validate_schedule("interval:3600") is None

    def test_valid_interval_with_float(self):
        assert validate_schedule("interval:1.5") is None

    def test_interval_non_numeric(self):
        assert validate_schedule("interval:abc") is not None

    def test_interval_zero_rejected(self):
        assert validate_schedule("interval:0") is not None

    def test_interval_negative_rejected(self):
        assert validate_schedule("interval:-100") is not None


class TestValidateScheduleCron:
    def test_valid_cron_all_wildcards(self):
        assert validate_schedule("cron:* * * * *") is None

    def test_valid_cron_daily_9am(self):
        assert validate_schedule("cron:0 9 * * *") is None

    def test_valid_cron_step(self):
        assert validate_schedule("cron:0 */6 * * *") is None

    def test_valid_cron_range(self):
        assert validate_schedule("cron:0 9-17 * * *") is None

    def test_valid_cron_list(self):
        assert validate_schedule("cron:0 9,12,18 * * *") is None

    def test_cron_wrong_field_count(self):
        assert validate_schedule("cron:0 9 * *") is not None

    def test_cron_invalid_field_char(self):
        assert validate_schedule("cron:0 9 * * mon") is not None


class TestValidateScheduleGeneral:
    def test_empty_string_rejected(self):
        assert validate_schedule("") is not None

    def test_whitespace_only_rejected(self):
        assert validate_schedule("   ") is not None

    def test_unknown_prefix_rejected(self):
        assert validate_schedule("daily:9am") is not None

    def test_leading_trailing_whitespace_tolerated(self):
        assert validate_schedule("  interval:3600  ") is None
