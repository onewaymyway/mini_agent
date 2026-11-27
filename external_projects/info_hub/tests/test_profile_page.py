"""阶段 Pa：“我的画像”独立页面。

校验：页签存在且位于推荐之后；页面包含各分区；旧的折叠面板已从抓取结果页移除；
有反馈时学到的关键词能显示出来。
"""
import os
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from core import feedback
from tests.helpers import make_project

APP = str(Path(__file__).resolve().parents[1] / "app.py")
SRC = {"id": "news_a", "name": "新闻源A", "category": "news", "kind": "feed", "adapter": "rss",
       "request": {"url": "https://n/feed"}}
SECTIONS = ["画像概要", "显式偏好（你直接写的，优先级最高）", "从反馈中学到的偏好", "信息源偏好", "维护"]


class ProfilePageTests(unittest.TestCase):
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

    def test_profile_tab_exists_after_feed_with_all_sections(self):
        at = self._run()
        labels = [t.label for t in at.tabs]
        self.assertLess(labels.index("推荐"), labels.index("我的画像"))
        subs = [s.value for s in self._tab(at, "我的画像").subheader]
        for name in SECTIONS:
            self.assertIn(name, subs)

    def test_old_profile_expander_removed_from_results(self):
        at = self._run()
        results = self._tab(at, "抓取结果")
        self.assertFalse(any("我的兴趣画像" in e.label for e in results.expander))

    def test_learned_keywords_are_shown(self):
        for i in range(3):
            feedback.record_feedback(self.root / "data", {
                "action": "interested", "title": f"量子计算突破进展第{i}期", "source_id": "news_a",
                "url": f"https://n/q{i}"})
        at = self._run()
        self.assertFalse(at.exception)
        tables = self._tab(at, "我的画像").dataframe
        values = [str(v) for t in tables for col in t.value.columns for v in t.value[col].tolist()]
        self.assertTrue(any("量子计算" in v or "计算" in v for v in values))


if __name__ == "__main__":
    unittest.main()
