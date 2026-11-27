"""阶段 P17：产出驱动的预算分配、低产出降频、暂停、后台运行（默认关闭）、运行摘要。不访问网络。"""
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core import autoexplore, discovery
from core.autoexplore import (_is_low_yield, _window_open, is_paused, maybe_run_background, productivity,
                              set_paused)
from core.frontier import Frontier
from core.settings import load_settings
from server import api
from tests.helpers import make_project
from tests.test_autoexplore import FakeCtx, PAGE, SITE

NOW = datetime(2026, 11, 27, 12, 0).astimezone()
SITE2 = "https://second.example/"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        (self.root / "config" / "seed_sites.yaml").write_text(
            f"sites:\n  - url: {SITE}\n  - url: {SITE2}\n", encoding="utf-8")
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir

    def cfg(self, **kw):
        base = {"background": "false"}
        base.update(kw)
        (self.root / "config" / "discovery.yaml").write_text(
            "autoexplore:\n" + "".join(f"  {k}: {v}\n" for k, v in base.items()), encoding="utf-8")

    def routes(self):
        return {SITE: PAGE.encode(), SITE2: PAGE.encode()}


class RuleTests(unittest.TestCase):
    def test_productivity_prior_and_growth(self):
        self.assertAlmostEqual(productivity(None), 0.5)
        self.assertGreater(productivity({"found": 3, "accepted": 1, "checks": 2}),
                           productivity({"found": 0, "checks": 2}))

    def test_low_yield_needs_enough_checks_and_recent_check(self):
        cfg = {"low_yield_checks": 5, "low_yield_threshold": 0.1, "recheck_days": 30}
        recent = (NOW - timedelta(days=1)).isoformat()
        old = (NOW - timedelta(days=40)).isoformat()
        self.assertFalse(_is_low_yield({"checks": 2, "found": 0, "last": recent}, NOW, cfg))
        self.assertTrue(_is_low_yield({"checks": 10, "found": 0, "last": recent}, NOW, cfg))  # 产出分 1/12 < 0.1
        self.assertFalse(_is_low_yield({"checks": 10, "found": 0, "last": old}, NOW, cfg))   # 到期复查
        self.assertFalse(_is_low_yield({"checks": 8, "found": 5, "last": recent}, NOW, cfg))

    def test_window_including_overnight(self):
        self.assertTrue(_window_open("22:00-06:00", datetime(2026, 11, 27, 23, 30)))
        self.assertTrue(_window_open("22:00-06:00", datetime(2026, 11, 27, 3, 0)))
        self.assertFalse(_window_open("22:00-06:00", datetime(2026, 11, 27, 12, 0)))
        self.assertTrue(_window_open("00:00-24:00", datetime(2026, 11, 27, 12, 0)))
        self.assertTrue(_window_open("garbage", datetime(2026, 11, 27, 12, 0)))


class PauseTests(Base):
    def test_paused_run_does_not_fetch(self):
        self.cfg()
        set_paused(self.data, True, NOW)
        ctx = FakeCtx(self.routes())
        out = autoexplore.run(self.settings, None, ctx=ctx, now=NOW)
        self.assertTrue(out["paused"])
        self.assertEqual(ctx.fetched, [])

    def test_pause_during_run_stops_next_site(self):
        self.cfg()
        routes = self.routes()

        class PausingCtx(FakeCtx):
            def http_get(self, url, headers=None, timeout=20.0):
                if url == SITE:                       # 第一个站点的首页被取到时，用户暂停
                    set_paused(self.data_dir, True, NOW)
                return super().http_get(url, headers, timeout)

        ctx = PausingCtx(routes)
        ctx.data_dir = self.data
        autoexplore.run(self.settings, None, ctx=ctx, now=NOW)
        self.assertNotIn(SITE2, ctx.fetched)

    def test_pause_state_persists(self):
        set_paused(self.data, True, NOW)
        self.assertTrue(is_paused(self.data))
        set_paused(self.data, False, NOW)
        self.assertFalse(is_paused(self.data))


class BackgroundTests(Base):
    def test_off_by_default(self):
        self.cfg(background="false")
        self.assertIsNone(maybe_run_background(self.settings, NOW))

    def test_runs_once_per_interval_and_respects_pause_and_window(self):
        self.cfg(background="true", background_every_hours=6)
        ctx = FakeCtx(self.routes())
        from unittest import mock
        with mock.patch.object(autoexplore, "_default_llm", return_value=(None, "")), \
                mock.patch("adapters.base.FetchContext", return_value=ctx):
            first = maybe_run_background(self.settings, NOW)
            self.assertIsNotNone(first)
            self.assertIsNone(maybe_run_background(self.settings, NOW + timedelta(hours=1)))   # 间隔内
            set_paused(self.data, True, NOW)
            self.assertIsNone(maybe_run_background(self.settings, NOW + timedelta(hours=8)))   # 暂停
            set_paused(self.data, False, NOW)
        self.cfg(background="true", background_every_hours=6, background_window="22:00-06:00")
        self.assertIsNone(maybe_run_background(self.settings, datetime(2026, 11, 28, 12, 0).astimezone()))  # 窗口外


class YieldTests(Base):
    def test_summary_written_per_run(self):
        self.cfg()
        autoexplore.run(self.settings, None, ctx=FakeCtx(self.routes()), now=NOW)
        rows = [json.loads(x) for x in (self.data / "discovery" / "yield.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertIn("sites", rows[0])
        self.assertIn("top", rows[0])

    def test_low_yield_site_skipped(self):
        self.cfg()
        state = {"hosts": {}, "days": {}, "yield": {"good.example": {"checks": 10, "found": 0,
                                                                    "last": (NOW - timedelta(days=1)).isoformat()}}}
        autoexplore._save_state(self.data, state)
        ctx = FakeCtx(self.routes())
        out = autoexplore.run(self.settings, None, ctx=ctx, now=NOW)
        self.assertEqual(out["skipped"].get("low_yield"), 1)
        self.assertNotIn(SITE, ctx.fetched)

    def test_productive_site_processed_first(self):
        self.cfg()
        state = {"hosts": {}, "days": {}, "yield": {"second.example": {"checks": 4, "found": 6, "last": None}}}
        autoexplore._save_state(self.data, state)
        ctx = FakeCtx(self.routes())
        autoexplore.run(self.settings, None, ctx=ctx, now=NOW)
        site_fetches = [u for u in ctx.fetched if not u.endswith("robots.txt")]
        self.assertEqual(site_fetches[0], SITE2)

    def test_accepted_candidate_raises_productivity(self):
        self.cfg()
        base = {"template_id": "auto_explore", "template_name": "t", "query": "q", "reason": "r",
                "url": SITE2, "created_at": NOW.isoformat(timespec="seconds"), "decided_at": None}
        discovery.add_candidate(self.data, dict(base, id="auto:second.example", origin="auto_explore",
                                                status="accepted", source={"id": "auto_s", "enabled": True}))
        before = productivity({"checks": 0, "found": 0})
        out = autoexplore.top_yield(self.data)
        self.assertEqual(out, [])
        self.assertGreater(productivity({"checks": 0, "found": 0, "accepted": 1}), before)


class StatusApiTests(Base):
    def test_status_and_pause_api(self):
        self.cfg()
        status = api.autoexplore_status_api(self.settings)
        self.assertFalse(status["paused"])
        self.assertFalse(status["background"])
        status = api.autoexplore_pause_api(self.settings, {"paused": True})
        self.assertTrue(status["paused"])
        self.assertIn("queued", status)


if __name__ == "__main__":
    unittest.main()
