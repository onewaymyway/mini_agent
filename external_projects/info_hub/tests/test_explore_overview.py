"""“自动探索”页签：总览接口（只读聚合）与页签内容。"""
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from streamlit.testing.v1 import AppTest

from core import autoexplore, discovery
from core.frontier import Frontier
from core.settings import load_settings
from server import api
from tests.helpers import make_project
from tests.test_autoexplore import FakeCtx, FakeLLM, PAGE, SITE

APP = str(Path(__file__).resolve().parents[1] / "app.py")
NOW = datetime.now().astimezone()


class OverviewApiTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        (self.root / "config" / "seed_sites.yaml").write_text(f"sites:\n  - url: {SITE}\n", encoding="utf-8")
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir

    def test_empty_state_is_well_formed(self):
        ov = api.autoexplore_overview_api(self.settings)
        self.assertEqual(ov["runs"], [])
        self.assertEqual(ov["sites"], [])
        self.assertEqual(ov["log"], [])
        self.assertEqual(ov["today"]["sites_checked"], 0)
        self.assertFalse(ov["status"]["paused"])
        self.assertFalse(ov["config"]["background"])           # 默认关闭

    def test_reflects_a_real_run(self):
        autoexplore.run(self.settings, FakeLLM(SITE), ctx=FakeCtx({SITE: PAGE.encode()}), now=NOW)
        ov = api.autoexplore_overview_api(self.settings)
        self.assertEqual(ov["today"]["sites_checked"], 1)
        self.assertEqual(ov["runs"][0]["added"], 1)
        self.assertEqual(ov["candidates"]["by_status"], {"pending": 1})
        self.assertEqual(ov["sites"][0]["host"], "good.example")
        self.assertTrue(any(r.get("step") == "candidate" for r in ov["log"]))
        self.assertEqual(ov["today"]["host_pages"].get("good.example"), 1)

    def test_pause_queue_lowyield_and_denylist_are_shown(self):
        autoexplore.set_paused(self.data, True, NOW)
        Frontier(self.data).enqueue("https://good.example/news/", 1, SITE, 2.0, NOW.isoformat())
        state = {"hosts": {}, "days": {}, "yield": {"bad.example": {
            "checks": 10, "found": 0, "last": (NOW - timedelta(days=1)).isoformat()}}}
        autoexplore._save_state(self.data, state)
        (self.root / "config" / "denylist.yaml").write_text("hosts:\n  - bad.example\n", encoding="utf-8")
        ov = api.autoexplore_overview_api(self.settings)
        self.assertTrue(ov["status"]["paused"])
        self.assertEqual(ov["frontier"]["counts"], {"queued": 1})
        self.assertEqual(len(ov["frontier"]["top_queued"]), 1)
        site = ov["sites"][0]
        self.assertTrue(site["low_yield"])
        self.assertTrue(site["denylisted"])
        self.assertEqual(ov["denylist"], ["bad.example"])

    def test_log_is_limited_and_newest_first(self):
        for i in range(5):
            autoexplore._log(self.data, {"time": f"2026-11-27T10:0{i}:00", "host": "h", "step": "fetch",
                                         "result": "ok", "n": i})
        ov = api.autoexplore_overview_api(self.settings, log_limit=3)
        self.assertEqual([r["n"] for r in ov["log"]], [4, 3, 2])

    def test_read_only(self):
        before = sorted(p.name for p in self.data.rglob("*") if p.is_file()) if self.data.exists() else []
        api.autoexplore_overview_api(self.settings)
        after = sorted(p.name for p in self.data.rglob("*") if p.is_file()) if self.data.exists() else []
        self.assertEqual(before, after)


class TabTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"

    def tearDown(self):
        os.environ.pop("INFO_HUB_ROOT", None)

    def test_tab_renders_sections_without_error(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        tab = next(t for t in at.tabs if t.label == "自动探索")
        subs = [s.value for s in tab.subheader]
        for name in ("今日用量", "最近几轮", "站点产出"):
            self.assertIn(name, subs)
        self.assertTrue(any("信息源复查与退役" in e.label for e in at.expander))   # 复查面板随之移到此页签
        results = next(t for t in at.tabs if t.label == "抓取结果")
        self.assertFalse(any("信息源复查与退役" in e.label for e in results.expander))


if __name__ == "__main__":
    unittest.main()
