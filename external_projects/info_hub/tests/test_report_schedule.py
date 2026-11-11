"""报告计划可在看板修改：间隔/时刻/窗口覆盖生效、不能互换类型、非法值拒绝、与 tick 间隔互不覆盖。"""
import unittest
from dataclasses import replace
from datetime import datetime

from core.scheduler import is_due
from core.settings import load_settings
from server import api
from tests.helpers import make_project

PLANS = [
    {"id": "rolling", "every": "3h", "window": "3h", "categories": "all"},
    {"id": "daily", "at": "08:00", "window": "24h", "categories": "all"},
]


class ReportScheduleTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        cfg = load_settings(self.root)
        self.settings = replace(cfg, base_reports=[dict(p) for p in PLANS])

    def _plan(self, pid):
        return next(p for p in self.settings.reports if p["id"] == pid)

    def test_defaults_without_override(self):
        self.assertEqual(self._plan("rolling")["every"], "3h")
        self.assertEqual(self._plan("daily")["at"], "08:00")

    def test_interval_and_window_change_immediately(self):
        api.set_report_schedule(self.settings, "rolling", {"every": "2h", "window": "6h"})
        self.assertEqual(self._plan("rolling")["every"], "2h")
        self.assertEqual(self._plan("rolling")["window"], "6h")

    def test_daily_time_change(self):
        api.set_report_schedule(self.settings, "daily", {"at": "09:30", "window": "12h"})
        self.assertEqual(self._plan("daily")["at"], "09:30")
        self.assertEqual(self._plan("daily")["window"], "12h")

    def test_cannot_switch_between_interval_and_time(self):
        api.set_report_schedule(self.settings, "rolling", {"at": "10:00", "every": "1h"})
        self.assertNotIn("at", self._plan("rolling"))
        self.assertEqual(self._plan("rolling")["every"], "1h")

    def test_rejects_invalid_values(self):
        for body in ({"every": "soon"}, {"every": "0m"}, {"every": "30s"}, {"window": "bad"}):
            with self.subTest(body=body), self.assertRaises(api.ApiError):
                api.set_report_schedule(self.settings, "rolling", body)
        for body in ({"at": "25:00"}, {"at": "8am"}):
            with self.subTest(body=body), self.assertRaises(api.ApiError):
                api.set_report_schedule(self.settings, "daily", body)
        self.assertEqual(self._plan("rolling")["every"], "3h")

    def test_unknown_plan_404(self):
        with self.assertRaises(api.ApiError):
            api.set_report_schedule(self.settings, "nope", {"every": "1h"})

    def test_changed_interval_drives_is_due(self):
        now = datetime.now().astimezone()
        last = now.replace(microsecond=0).isoformat()
        api.set_report_schedule(self.settings, "rolling", {"every": "1h"})
        self.assertFalse(is_due(self._plan("rolling"), last, now))
        from datetime import timedelta
        self.assertTrue(is_due(self._plan("rolling"), (now - timedelta(hours=2)).isoformat(), now))

    def test_tick_interval_and_report_overrides_coexist(self):
        api.set_report_schedule(self.settings, "rolling", {"every": "2h"})
        api.set_tick_interval(self.settings, 9)
        self.assertEqual(self.settings.tick_interval_minutes, 9)
        self.assertEqual(self._plan("rolling")["every"], "2h")
        api.set_report_schedule(self.settings, "daily", {"at": "07:00"})
        self.assertEqual(self.settings.tick_interval_minutes, 9)


if __name__ == "__main__":
    unittest.main()
