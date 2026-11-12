"""看板阅读与反馈面板：AppTest 无头验证面板渲染、反馈写入、默认打开方式切换。"""
import os
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from adapters.base import FetchContext
from core import feedback
from core.runner import run_source
from core.settings import load_settings
from core.sources import load_sources
from tests.helpers import make_project

APP = str(Path(__file__).resolve().parents[1] / "app.py")
RSS = ('<?xml version="1.0"?><rss version="2.0"><channel>'
       '<item><title>看板新闻甲</title><link>https://n/1</link></item>'
       '<item><title>看板新闻乙</title><link>https://n/2</link></item>'
       '</channel></rss>').encode("utf-8")
SRC = {"id": "news_a", "name": "新闻源A", "category": "news", "kind": "feed", "adapter": "rss",
       "request": {"url": "https://n/feed"}}


class DashboardFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": SRC})
        settings = load_settings(self.root)
        spec = load_sources(settings).specs["news_a"]
        run_source(spec, settings, FetchContext(http_get=lambda u, **k: RSS))
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"

    def _app(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def test_panel_renders_with_mode_switch_and_feedback_form(self):
        at = self._app()
        self.assertTrue(any("阅读与反馈" in s.value for s in at.subheader))
        modes = [r for r in at.radio if r.key and r.key.startswith("rd_mode_")]
        self.assertTrue(modes)
        self.assertEqual(list(modes[0].options), ["阅读模式（当前默认）", "内嵌页面", "新标签页"])

    def test_feedback_submit_writes_event_with_reason_optional(self):
        at = self._app()
        # 默认不选原因、不填备注，直接点“感兴趣”
        buttons = [b for b in at.button if "感兴趣" in b.label]
        self.assertTrue(buttons, "应存在“感兴趣”提交按钮")
        buttons[0].click().run()
        self.assertFalse(at.exception)
        events = feedback.read_feedback(self.root / "data")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "interested")
        self.assertEqual(events[0]["reasons"], [])
        self.assertEqual(events[0]["note"], "")

    def test_set_default_mode_persists(self):
        at = self._app()
        [b for b in at.button if b.key and b.key.startswith("rd_default_")][0]
        radio = [r for r in at.radio if r.key and r.key.startswith("rd_mode_")][0]
        radio.set_value("新标签页").run()
        [b for b in at.button if b.key and b.key.startswith("rd_default_")][0].click().run()
        self.assertFalse(at.exception)
        runtime = (self.root / "data" / "state" / "runtime_config.json").read_text(encoding="utf-8")
        self.assertIn('"open_mode": "newtab"', runtime)


    def test_profile_panel_and_ranking_toggle(self):
        at = self._app()
        self.assertTrue(any("我的兴趣画像" in e.label for e in at.expander))
        toggles = [t for t in at.toggle if "按我的兴趣排序" in t.label]
        self.assertTrue(toggles, "应存在排序开关")
        toggles[0].set_value(True).run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertTrue(any(d for d in at.dataframe))


if __name__ == "__main__":
    unittest.main()
