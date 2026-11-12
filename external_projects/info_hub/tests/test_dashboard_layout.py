"""看板标签页布局：顺序，以及合并后的内容是否落在正确的页签里。"""
import os
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from tests.helpers import make_project

APP = str(Path(__file__).resolve().parents[1] / "app.py")
SRC = {"id": "news_a", "name": "新闻源A", "category": "news", "kind": "feed", "adapter": "rss",
       "request": {"url": "https://n/feed"}}
ORDER = ["推荐", "抓取结果", "报告", "反馈记录", "信息源", "订阅", "添加信息源", "运行与调度", "设置与管理"]


class DashboardLayoutTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": SRC})
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"

    def tearDown(self):
        os.environ.pop("INFO_HUB_ROOT", None)

    def _run(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        return at

    def _tab(self, at, label):
        return next(t for t in at.tabs if t.label == label)

    def test_top_level_tab_order(self):
        at = self._run()
        self.assertEqual([t.label for t in at.tabs if t.label in ORDER], ORDER)  # 排除嵌套子页签

    def test_edit_section_lives_in_sources_tab(self):
        at = self._run()
        self.assertIn("编辑信息源", [s.value for s in self._tab(at, "信息源").subheader])
        self.assertNotIn("编辑信息源", [s.value for s in self._tab(at, "推荐").subheader])

    def test_ops_tab_has_status_and_runs(self):
        at = self._run()
        subs = [s.value for s in self._tab(at, "运行与调度").subheader]
        self.assertIn("调度状态", subs)
        self.assertIn("运行记录", subs)

    def test_settings_tab_has_login_and_services(self):
        at = self._run()
        tab = self._tab(at, "设置与管理")
        self.assertIn("登录", [s.value for s in tab.subheader])
        self.assertTrue(any("服务管理" in e.label for e in tab.expander))
        results = self._tab(at, "抓取结果")
        self.assertFalse(any("服务管理" in e.label for e in results.expander))


if __name__ == "__main__":
    unittest.main()
