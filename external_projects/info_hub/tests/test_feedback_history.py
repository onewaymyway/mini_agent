"""看板“反馈记录”页签：历史反馈的查询接口与页面展示。"""
import json
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

from core import feedback
from core.settings import load_settings
from server import api
from server.api import ApiError
from tests.helpers import make_project

APP = str(Path(__file__).resolve().parents[1] / "app.py")
SRC = {"id": "news_a", "name": "新闻源A", "category": "news", "kind": "feed", "adapter": "rss",
       "request": {"url": "https://n/feed"}}


def _rec(settings, **kw):
    body = {"action": "interested", "source_id": "news_a", "url": "https://n/1", "title": "标题一", **kw}
    return feedback.record_feedback(settings.paths.data_dir, body)


class HistoryApiTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": SRC})
        self.settings = load_settings(self.root)

    def test_empty(self):
        out = api.list_feedback_history(self.settings)
        self.assertEqual((out["events"], out["total"], out["sources"]), ([], 0, []))

    def test_newest_first_and_counts(self):
        _rec(self.settings, title="甲", url="https://n/1")
        _rec(self.settings, title="乙", url="https://n/2", action="not_interested", reasons=["off_topic"])
        _rec(self.settings, title="丙", url="https://n/3", action="later", source_id="manual")
        out = api.list_feedback_history(self.settings)
        self.assertEqual([e["title"] for e in out["events"]], ["丙", "乙", "甲"])
        self.assertEqual(out["counts"], {"interested": 1, "not_interested": 1, "later": 1})
        self.assertEqual(out["sources"], ["manual", "news_a"])

    def test_filters(self):
        _rec(self.settings, title="苹果发布会", url="https://n/1")
        _rec(self.settings, title="香蕉", url="https://n/2", action="not_interested", note="太浅了")
        _rec(self.settings, title="手动页", url="https://x/9", source_id="manual")
        titles = lambda **k: sorted(e["title"] for e in api.list_feedback_history(self.settings, **k)["events"])  # noqa: E731
        self.assertEqual(titles(action="not_interested"), ["香蕉"])
        self.assertEqual(titles(source_id="manual"), ["手动页"])
        self.assertEqual(titles(keyword="苹果"), ["苹果发布会"])
        self.assertEqual(titles(keyword="太浅"), ["香蕉"])  # 备注也可搜
        self.assertEqual(titles(keyword="X/9"), ["手动页"])  # 链接，忽略大小写
        self.assertEqual(titles(keyword="苹果", action="not_interested"), [])  # 条件取交集

    def test_days_filter_and_bad_input(self):
        _rec(self.settings, title="新")
        old = _rec(self.settings, title="旧")
        path = feedback.feedback_path(self.settings.paths.data_dir)
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        for r in rows:
            if r["id"] == old["id"]:
                r["time"] = (datetime.now().astimezone() - timedelta(days=40)).isoformat(timespec="seconds")
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        self.assertEqual([e["title"] for e in api.list_feedback_history(self.settings, days=7)["events"]], ["新"])
        self.assertEqual(len(api.list_feedback_history(self.settings, days=0)["events"]), 2)
        with self.assertRaises(ApiError):
            api.list_feedback_history(self.settings, action="nope")
        with self.assertRaises(ApiError):
            api.list_feedback_history(self.settings, days="abc")


class HistoryTabTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": SRC})
        self.settings = load_settings(self.root)
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"

    def _app(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def test_empty_state(self):
        at = self._app()
        self.assertTrue(any("还没有反馈记录" in i.value for i in at.info))
        self.assertIn("反馈记录", [t.label for t in at.tabs])

    def test_events_are_listed_with_open_buttons_and_filter_works(self):
        _rec(self.settings, title="苹果发布会", url="https://n/1")
        _rec(self.settings, title="香蕉", url="https://n/2", action="not_interested", reasons=["shallow"], note="没营养")
        _rec(self.settings, title="手动页", url="https://x/9", source_id="manual", action="later")
        at = self._app()
        text = " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)
        for needle in ("苹果发布会", "香蕉", "手动页", "内容太浅", "没营养", "手动反馈"):
            self.assertIn(needle, text)
        self.assertEqual(len([b for b in at.button if b.key and b.key.startswith("op_fh_")]), 3)
        at.selectbox(key="fh_action").set_value("not_interested").run()
        self.assertFalse(at.exception)
        self.assertEqual(len([b for b in at.button if b.key and b.key.startswith("op_fh_")]), 1)

    def test_open_button_item_vs_manual(self):
        _rec(self.settings, title="条目", url="https://n/1")
        _rec(self.settings, title="手动页", url="https://x/9", source_id="manual")
        at = self._app()
        buttons = [b for b in at.button if b.key and b.key.startswith("op_fh_")]  # 新的在前：手动页、条目
        with mock.patch("core.dedicated_browser.open_url", return_value={"pid": 1}) as opener:
            buttons[0].click().run()
            manual_target = opener.call_args[0][2]
            buttons = [b for b in at.button if b.key and b.key.startswith("op_fh_")]
            buttons[1].click().run()
            item_target = opener.call_args[0][2]
        self.assertEqual(manual_target, "https://x/9")  # 手动反馈只打开原始链接
        self.assertIn("#ihk=", item_target)
        self.assertIn("ihs=news_a", item_target)


if __name__ == "__main__":
    unittest.main()
