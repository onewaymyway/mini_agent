"""阶段 P10：画像变化日志、关键词依据、“这是/不是我的兴趣”纠错。"""
import os
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from core import feedback, profile as prof
from core.settings import load_settings
from server import api
from server.api import ApiError
from tests.helpers import make_project

APP = str(Path(__file__).resolve().parents[1] / "app.py")
SRC = {"id": "news_a", "name": "新闻源A", "category": "news", "kind": "feed", "adapter": "rss",
       "request": {"url": "https://n/feed"}}


class DiffTests(unittest.TestCase):
    def test_appeared_gone_changed_and_threshold(self):
        old = {"a": {"score": 1.0}, "b": {"score": 2.0}, "c": {"score": 0.4}}
        new = {"b": {"score": 2.2}, "c": {"score": 0.5}, "d": {"score": -1.0}, "a": {"score": 0.0}}
        out = {c["term"]: c["kind"] for c in prof.diff_keywords(old, new)}
        self.assertEqual(out.get("a"), "gone")
        self.assertEqual(out.get("d"), "appeared")
        self.assertNotIn("b", out)  # 变化 0.2 < 阈值
        self.assertNotIn("c", out)  # 变化 0.1 < 阈值

    def test_changed_when_delta_reaches_threshold(self):
        out = prof.diff_keywords({"x": {"score": 1.0}}, {"x": {"score": 1.6}})
        self.assertEqual(out[0]["kind"], "changed")


class P10DataTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": SRC})
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir

    def _feed(self, title, action="interested", url=None):
        feedback.record_feedback(self.data, {"action": action, "title": title, "source_id": "news_a",
                                             "url": url or f"https://n/{abs(hash(title))}"})

    def test_rebuild_logs_changes_and_recent_changes_returns_them(self):
        self._feed("量子计算突破进展")
        prof.rebuild_learned(self.data)
        rows = prof.recent_changes(self.data)
        self.assertTrue(rows)
        self.assertTrue(any(r["term"] == "量子计算" or "计算" in r["term"] for r in rows))
        self.assertIn("time", rows[0])

    def test_no_change_no_log(self):
        self._feed("仅一条反馈的标题")
        prof.rebuild_learned(self.data)
        n = len(prof.recent_changes(self.data, limit=1000))
        prof.rebuild_learned(self.data)
        self.assertEqual(len(prof.recent_changes(self.data, limit=1000)), n)

    def test_keyword_evidence_lists_recent_titles(self):
        self._feed("深度学习框架对比")
        self._feed("深度学习入门指南")
        ev = prof.keyword_evidence(self.data, "深度学习")
        self.assertEqual(len(ev), 2)
        self.assertTrue(all("深度学习" in e["title"] for e in ev))

    def test_explicit_correction_is_exclusive_and_revocable(self):
        prof.set_explicit_term(self.data, "加密货币", "topic")
        self.assertIn("加密货币", prof.load_profile(self.data)["explicit"]["topics"])
        prof.set_explicit_term(self.data, "加密货币", "exclude")
        p = prof.load_profile(self.data)["explicit"]
        self.assertNotIn("加密货币", p["topics"])
        self.assertIn("加密货币", p["exclude"])
        prof.set_explicit_term(self.data, "加密货币", "none")
        p = prof.load_profile(self.data)["explicit"]
        self.assertNotIn("加密货币", p["topics"] + p["exclude"])

    def test_invalid_mode_rejected(self):
        with self.assertRaises(prof.ProfileError):
            prof.set_explicit_term(self.data, "x", "bogus")


class P10ApiTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": SRC})
        self.settings = load_settings(self.root)

    def test_api_detail_and_set(self):
        feedback.record_feedback(self.settings.paths.data_dir,
                                 {"action": "interested", "title": "开源大模型评测", "source_id": "news_a",
                                  "url": "https://n/x1"})
        detail = api.profile_term_detail(self.settings, "大模型")
        self.assertTrue(detail["evidence"])
        self.assertEqual(detail["explicit_state"], "none")
        out = api.set_profile_term(self.settings, {"term": "大模型", "mode": "topic"})
        self.assertIn("大模型", out["profile"]["explicit"]["topics"])
        self.assertEqual(api.profile_term_detail(self.settings, "大模型")["explicit_state"], "topic")

    def test_api_rejects_bad_mode_and_empty_term(self):
        with self.assertRaises(ApiError):
            api.set_profile_term(self.settings, {"term": "x", "mode": "bogus"})
        with self.assertRaises(ApiError):
            api.profile_term_detail(self.settings, "  ")


class P10PageTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": SRC})
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        feedback.record_feedback(Path(self.root) / "data", {"action": "interested", "title": "机器人手术进展",
                                                            "source_id": "news_a", "url": "https://n/r1"})

    def tearDown(self):
        os.environ.pop("INFO_HUB_ROOT", None)

    def test_page_shows_evidence_and_changes_sections(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        subs = [s.value for s in next(t for t in at.tabs if t.label == "我的画像").subheader]
        self.assertIn("关键词依据与纠错（阶段 P10）", subs)
        self.assertIn("最近变化", subs)


if __name__ == "__main__":
    unittest.main()
