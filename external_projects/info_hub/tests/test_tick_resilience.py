"""tick 健壮性与运行状态：单源异常不中断整次 tick、逐源落盘、状态可见。"""
import json
import unittest
from datetime import datetime, timedelta
from unittest import mock

from adapters.base import FetchContext
from core import tick_status
from core.runner import run_source
from core.settings import load_settings
from core.store import atomic_write_json
from core.tick import tick
from tests.helpers import make_project
from tests.test_digest_and_tick import FEED, HOT, RSS_A1

TZ = datetime.now().astimezone().tzinfo
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=TZ)


class TickResilienceTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"hot_a": HOT, "feed_b": FEED})
        self.settings = load_settings(self.root)
        self.ctx = FetchContext(http_get=lambda u, **k: RSS_A1)

    def _sched(self):
        return json.loads((self.settings.paths.state_dir / "_scheduler.json").read_text(encoding="utf-8"))

    def test_one_source_exception_does_not_abort_tick(self):
        def flaky(spec, settings, ctx=None):
            if spec.id == "hot_a":
                raise RuntimeError("浏览器启动失败")
            return run_source(spec, settings, ctx)

        with mock.patch("core.tick.run_source", side_effect=flaky):
            rep = tick(self.settings, now=NOW, ctx=self.ctx)

        self.assertEqual(rep.ran_sources, ["feed_b"])
        self.assertEqual(rep.failed_sources, ["hot_a"])
        sched = self._sched()
        self.assertIn("feed_b", sched["sources"])     # 成功的源已落盘
        self.assertIn("hot_a", sched["sources"])      # 异常的源也记录时间，避免每 tick 重试
        st = tick_status.read(self.settings)
        self.assertFalse(st["running"])
        self.assertEqual(st["source_outcomes"]["hot_a"]["ok"], False)
        self.assertIn("浏览器启动失败", st["source_outcomes"]["hot_a"]["error"])
        self.assertTrue(any(e["where"] == "hot_a" for e in st["errors"]))

    def test_tick_exception_is_recorded_and_reraised(self):
        with mock.patch("core.tick._run_tick", side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                tick(self.settings, now=NOW, ctx=self.ctx)
        st = tick_status.read(self.settings)
        self.assertFalse(st["running"])
        self.assertIn("ValueError: boom", st["errors"][-1]["message"])

    def test_stalled_when_running_too_long(self):
        old = (datetime.now().astimezone() - timedelta(minutes=30)).isoformat(timespec="seconds")
        atomic_write_json(self.settings.paths.state_dir / "_tick_status.json",
                          {"running": True, "tick_started_at": old, "current_source": "zhihu_hot",
                           "current_source_started_at": old, "last_activity_at": old})
        s = tick_status.summary(self.settings, timedelta(minutes=5))
        self.assertTrue(s["stalled"])
        self.assertEqual(s["current_source"], "zhihu_hot")

    def test_loop_suspected_dead_when_idle_too_long(self):
        old = (datetime.now().astimezone() - timedelta(hours=3)).isoformat(timespec="seconds")
        atomic_write_json(self.settings.paths.state_dir / "_tick_status.json",
                          {"running": False, "last_activity_at": old})
        s = tick_status.summary(self.settings, timedelta(minutes=5))
        self.assertTrue(s["loop_suspected_dead"])
        self.assertFalse(s["stalled"])


if __name__ == "__main__":
    unittest.main()
