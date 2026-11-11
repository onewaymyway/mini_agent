import unittest
from datetime import datetime, timedelta

from core.scheduler import is_due, parse_at, parse_interval, source_is_due

TZ = datetime.now().astimezone().tzinfo
T0 = datetime(2026, 11, 11, 7, 0, tzinfo=TZ)


class ParseTests(unittest.TestCase):
    def test_parse_interval_units(self):
        self.assertEqual(parse_interval("60m"), timedelta(minutes=60))
        self.assertEqual(parse_interval("3h"), timedelta(hours=3))
        self.assertEqual(parse_interval("1d"), timedelta(days=1))

    def test_parse_interval_rejects_bad_input(self):
        for bad in ("cron", "3x", "0h", ""):
            with self.assertRaises(ValueError):
                parse_interval(bad)

    def test_parse_at(self):
        self.assertEqual(parse_at("08:00").hour, 8)
        with self.assertRaises(ValueError):
            parse_at("8am")


class DueTests(unittest.TestCase):
    def test_every_plan_first_run_is_due(self):
        self.assertTrue(is_due({"id": "r", "every": "3h"}, None, T0))

    def test_every_plan_respects_interval(self):
        last = (T0 - timedelta(hours=2)).isoformat()
        self.assertFalse(is_due({"id": "r", "every": "3h"}, last, T0))
        last = (T0 - timedelta(hours=3)).isoformat()
        self.assertTrue(is_due({"id": "r", "every": "3h"}, last, T0))

    def test_at_plan_not_due_before_time(self):
        plan = {"id": "d", "at": "08:00"}
        self.assertFalse(is_due(plan, None, T0))  # 07:00 < 08:00

    def test_at_plan_due_once_per_day(self):
        plan = {"id": "d", "at": "08:00"}
        later = T0.replace(hour=9)
        self.assertTrue(is_due(plan, None, later))
        ran = later.isoformat()
        self.assertFalse(is_due(plan, ran, later + timedelta(hours=1)))
        next_day = later + timedelta(days=1)
        self.assertTrue(is_due(plan, ran, next_day.replace(hour=9)))

    def test_plan_without_every_or_at_raises(self):
        with self.assertRaises(ValueError):
            is_due({"id": "x"}, None, T0)

    def test_source_due(self):
        self.assertTrue(source_is_due("60m", None, T0))
        self.assertFalse(source_is_due("60m", (T0 - timedelta(minutes=10)).isoformat(), T0))


if __name__ == "__main__":
    unittest.main()
