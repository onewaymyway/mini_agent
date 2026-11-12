"""看板阅读与反馈面板：AppTest 无头验证面板渲染、反馈写入、专用浏览器打开按钮（旧的阅读/内嵌/新标签页已移除）。"""
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

    def test_panel_renders_without_old_open_modes(self):
        at = self._app()
        self.assertTrue(any("阅读与反馈" in s.value for s in at.subheader))
        # 旧的打开方式单选与“设为默认”已移除
        self.assertFalse([r for r in at.radio if r.key and r.key.startswith("rd_mode_")])
        self.assertFalse([b for b in at.button if b.key and b.key.startswith("rd_default_")])
        self.assertFalse([b for b in at.button if b.label == "加载正文"])

    def test_every_result_row_has_dedicated_open_button(self):
        at = self._app()
        rows = [b for b in at.button if b.key and b.key.startswith("op_res_")]
        self.assertEqual(len(rows), 2)  # 两条新闻各一个按钮

    def test_result_rows_have_interest_buttons_that_record_feedback(self):
        at = self._app()
        ups = [b for b in at.button if b.key and b.key.startswith("fb_interested_")]
        downs = [b for b in at.button if b.key and b.key.startswith("fb_not_interested_")]
        self.assertEqual((len(ups), len(downs)), (2, 2))
        ups[0].click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        events = feedback.read_feedback(self.root / "data")
        self.assertEqual([e["action"] for e in events], ["interested"])
        self.assertEqual(events[0]["source_id"], "news_a")
        self.assertEqual(events[0]["view"], "plain")
        # 同一动作再点一次不重复记录；按钮高亮为当前状态
        ups = [b for b in at.button if b.key and b.key.startswith("fb_interested_")]
        self.assertEqual(ups[0].proto.type, "primary")
        ups[0].click().run()
        self.assertEqual(len(feedback.read_feedback(self.root / "data")), 1)
        # 改标不感兴趣：新增一条，状态切换
        downs = [b for b in at.button if b.key and b.key.startswith("fb_not_interested_")]
        downs[0].click().run()
        self.assertEqual([e["action"] for e in feedback.read_feedback(self.root / "data")],
                         ["interested", "not_interested"])
        downs = [b for b in at.button if b.key and b.key.startswith("fb_not_interested_")]
        self.assertEqual(downs[0].proto.type, "primary")
        # 另一条未受影响
        self.assertEqual(downs[1].proto.type, "secondary")

    def test_open_button_calls_dedicated_browser_with_item(self):
        from unittest import mock
        at = self._app()
        btn = [b for b in at.button if b.key and b.key.startswith("op_res_")][0]
        with mock.patch("core.dedicated_browser.open_url", return_value={"pid": 1}) as opener:
            btn.click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertTrue(opener.called)
        target = opener.call_args[0][2]
        self.assertIn("#ihk=", target)
        self.assertIn("ihs=news_a", target)
        # 服务端记录一次“打开”（只记一次）
        from core import evaluation
        from core.store import read_jsonl
        rows = [r for r in read_jsonl(evaluation.exposure_path(self.root / "data")) if r.get("kind") == "open"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mode"], "dedicated")

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

    def test_profile_panel_and_ranking_toggle(self):
        at = self._app()
        self.assertTrue(any("我的兴趣画像" in e.label for e in at.expander))
        toggles = [t for t in at.toggle if "按我的兴趣排序" in t.label]
        self.assertTrue(toggles, "应存在排序开关")
        toggles[0].set_value(True).run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertTrue(any(d for d in at.dataframe))


    def test_services_panel_renders_with_start_buttons(self):
        at = self._app()
        self.assertTrue(any("服务管理" in e.label for e in at.expander))
        self.assertTrue(any(b.key == "rcv_start" for b in at.button))
        self.assertTrue(any(b.key == "db_start" for b in at.button))


if __name__ == "__main__":
    unittest.main()
