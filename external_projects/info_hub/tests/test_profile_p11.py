"""阶段 P11：长短期兴趣分层、快速跳过统计（只统计，不打分）。"""
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from streamlit.testing.v1 import AppTest

from core import evaluation, feedback, profile as prof
from core.settings import load_settings
from server import api
from server.api import ApiError
from tests.helpers import make_project

APP = str(Path(__file__).resolve().parents[1] / "app.py")
SRC = {"id": "news_a", "name": "新闻源A", "category": "news", "kind": "feed", "adapter": "rss",
       "request": {"url": "https://n/feed"}}
NOW = datetime(2026, 11, 27, 12, 0).astimezone()


def ev(title, days_ago, action="interested"):
    t = NOW - timedelta(days=days_ago)
    return {"action": action, "title": title, "source_id": "news_a", "reasons": [],
            "time": t.isoformat(timespec="seconds"), "item_key": f"k{abs(hash(title + str(days_ago)))}"}


class LayeringTests(unittest.TestCase):
    def test_single_week_keyword_gets_no_long_term_part(self):
        # 同一周内的两条反馈：关键词只计入短期部分
        events = [ev("冷门话题甲", 1), ev("冷门话题甲再现", 2)]
        out = prof.learn_from_events(events, NOW)
        kw = out["keywords"]
        self.assertIn("冷门话题", kw)
        self.assertAlmostEqual(kw["冷门话题"]["score"], out["keywords_short"]["冷门话题"]["score"], places=2)

    def test_sustained_keyword_gets_long_term_boost(self):
        # 跨越三周的同一关键词：合并分数应高于仅短期部分
        events = [ev("量子计算进展", 1), ev("量子计算新闻", 9), ev("量子计算报道", 17)]
        out = prof.learn_from_events(events, NOW)
        short = out["keywords_short"]["量子计算"]["score"]
        blended = out["keywords"]["量子计算"]["score"]
        self.assertGreater(blended, short)

    def test_short_decays_faster_than_long(self):
        # 30 天前的单次反馈：长期表仍高于剪枝阈值，短期表已被裁掉
        out = prof.learn_from_events([ev("旧热点话题", 30)], NOW)
        self.assertIn("旧热点", out["keywords_long"])
        self.assertNotIn("旧热点", out["keywords_short"])

    def test_params_are_recorded_and_clamped(self):
        root = make_project({"news_a": SRC})
        data = load_settings(root).paths.data_dir
        params = prof.profile_params(data)
        self.assertEqual(params["half_life_short_days"], 7)
        self.assertEqual(params["half_life_long_days"], 90)
        self.assertEqual(params["long_min_weeks"], 2)


class SkipTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": SRC})
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir

    def test_skip_recorded_as_statistics_only(self):
        feedback.record_feedback(self.data, {"action": "interested", "title": "开源大模型评测",
                                             "source_id": "news_a", "url": "https://n/a1"})
        prof.rebuild_learned(self.data)
        before = prof.score_item(prof.load_profile(self.data), "开源大模型评测新闻", "", "news_a")[0]
        api.record_skip_api(self.settings, {"source_id": "news_a", "url": "https://n/s1",
                                            "title": "芯片制造新突破", "view": "feed_v1"})
        api.record_skip_api(self.settings, {"source_id": "news_a", "url": "https://n/s2",
                                            "title": "芯片制造新闻", "view": "feed_v1"})
        profile = prof.ensure_fresh(self.data)
        self.assertEqual(profile["learned"]["skip_tokens"].get("芯片制造"), 2)
        after = prof.score_item(profile, "开源大模型评测新闻", "", "news_a")[0]
        self.assertEqual(before, after)  # 跳过不影响打分
        self.assertNotIn("芯片制造", profile["learned"]["keywords"])

    def test_skip_api_validation(self):
        with self.assertRaises(ApiError):
            api.record_skip_api(self.settings, {"source_id": "../x", "title": "t"})

    def test_skip_is_not_counted_as_feedback(self):
        api.record_skip_api(self.settings, {"source_id": "news_a", "url": "https://n/s3", "title": "某标题"})
        self.assertEqual(feedback.read_feedback(self.data), [])
        self.assertEqual(len(prof.skip_records(self.data)), 1)


class PagePTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": SRC})
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"

    def tearDown(self):
        os.environ.pop("INFO_HUB_ROOT", None)

    def test_profile_page_shows_layers_section(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        subs = [s.value for s in next(t for t in at.tabs if t.label == "我的画像").subheader]
        self.assertIn("兴趣的时间层次（阶段 P11）", subs)


if __name__ == "__main__":
    unittest.main()
