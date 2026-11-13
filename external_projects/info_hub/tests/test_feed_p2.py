"""推荐信息流 P2：曝光疲劳、多样性与来源上限、探索位、不再显示 / 屏蔽主题、离线回放评估、快速分诊。

对应 next_doc/info_hub_recommendation_feed_design.md 第 5、6、7.1 节。P1 的测试见 tests/test_feed.py。
"""
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from streamlit import cache_data
from streamlit.testing.v1 import AppTest

from core import feed, feed_eval
from core.feedback import item_key, record_feedback
from core.settings import load_settings
from core.store import atomic_write_json
from server import api
from server.api import ApiError
from tests.helpers import make_project
from tests.test_feed import _item, _snap, _src, _sources_of

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _distinct_titles(n, seed=3):
    """生成彼此字符二元组几乎不重叠的标题，保证每条都是独立事件。"""
    import random
    rng = random.Random(seed)
    pool = [chr(c) for c in range(0x4E00, 0x4E00 + 3000)]
    out = []
    for _ in range(n):
        out.append("".join(rng.choice(pool) for _ in range(12)))
    return out


def _card(key, source_id, score, matches=(), importance=0.5, freshness=0.5):
    return {"key": key, "source_id": source_id, "score": score, "matches": list(matches),
            "components": {"importance": importance, "freshness": freshness}}


class FatigueAndDiversityUnitTests(unittest.TestCase):
    def test_fatigue_value_starts_at_shows_and_caps_at_one(self):
        self.assertEqual(feed.fatigue_value(2, 3), 0.0)
        self.assertAlmostEqual(feed.fatigue_value(3, 3), 1 / 3, places=3)
        self.assertEqual(feed.fatigue_value(50, 3), 1.0)
        self.assertEqual(feed.fatigue_value(10, 0), 0.0)

    def test_pick_featured_caps_each_source(self):
        ranked = [_card(f"a{i}", "a", 1.0 - i * 0.01) for i in range(6)] + [
            _card("b1", "b", 0.5), _card("c1", "c", 0.4)]
        chosen = feed._pick_featured(ranked, 5, source_cap=2, topic_penalty=0.0)
        srcs = [c["source_id"] for c in chosen]
        self.assertLessEqual(srcs.count("a"), 2)
        self.assertIn("b", srcs)
        self.assertIn("c", srcs)

    def test_pick_featured_penalises_repeated_topic(self):
        ranked = [_card("x1", "a", 1.0, ["大模型"]), _card("x2", "b", 0.95, ["大模型"]),
                  _card("y1", "c", 0.9, ["天气"])]
        chosen = feed._pick_featured(ranked, 2, source_cap=0, topic_penalty=0.2)
        self.assertEqual([c["key"] for c in chosen], ["x1", "y1"])

    def test_explore_size_is_direct_count(self):
        self.assertEqual(feed._explore_size({"explore_size": 5}), 5)
        self.assertEqual(feed._explore_size({"explore_size": 0}), 0)
        self.assertEqual(feed._explore_size({"explore_size": -3}), 0)
        self.assertEqual(feed._explore_size({"explore_size": "x"}), 0)
        self.assertEqual(feed.DEFAULT_CONFIG["explore_size"], 5)

    def test_config_nested_override_keeps_defaults(self):
        cfg = feed.load_config({"feed": {"diversity": {"source_cap": 1}, "explore_size": 7}})
        self.assertEqual(cfg["diversity"]["source_cap"], 1)
        self.assertEqual(cfg["diversity"]["topic_repeat_penalty"], 0.1)
        self.assertEqual(cfg["explore_size"], 7)


class FeedP2Tests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A"), "b": _src("b", "源B", kind="feed")})
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir
        self.now = datetime.now().astimezone().replace(microsecond=0)
        self.sources = _sources_of(self.settings)

    def _build(self, **kw):
        cfg = feed.load_config(self.settings.raw)
        for k, v in (kw.pop("cfg", {}) or {}).items():
            cfg[k] = v
        return feed.build_feed(data_dir=self.data, raw_dir=self.settings.paths.raw_dir, sources=self.sources,
                               hours=24, cfg=cfg, now=self.now, **kw)

    def _all(self, res):
        out = list(res["sections"]["featured"]) + list(res["sections"]["explore"]) + list(res["sections"]["hot"])
        for g in res["sections"]["topics"]:
            out += g["cards"]
        return out + list(res["sections"]["others"])

    def test_fatigue_lowers_score_of_repeatedly_shown_item(self):
        t = self.now - timedelta(minutes=10)
        urls = {"old": "https://a.example/old", "new": "https://a.example/new"}
        _snap(self.settings, "a", t, [_item("a", "旧闻反复出现的条目", url=urls["old"]),
                                     _item("a", "全新出现的消息条目", url=urls["new"])])
        key_old = item_key(urls["old"], "旧闻反复出现的条目")
        res = self._build(shown={key_old: 6})
        by_title = {c["title"]: c for c in self._all(res)}
        self.assertGreater(by_title["旧闻反复出现的条目"]["components"]["fatigue_penalty"], 0)
        self.assertEqual(by_title["全新出现的消息条目"]["components"]["fatigue_penalty"], 0)
        self.assertLess(by_title["旧闻反复出现的条目"]["score"], by_title["全新出现的消息条目"]["score"])

    def test_shown_counts_read_from_feed_log_within_window(self):
        feed.record_impressions(self.data, window_hours=24, now=self.now - timedelta(days=1),
                                impressions=[{"item_key": "k" * 16, "position": 0}])
        feed.record_impressions(self.data, window_hours=24, now=self.now,
                                impressions=[{"item_key": "k" * 16, "position": 0}])
        feed.record_impressions(self.data, window_hours=24, now=self.now - timedelta(days=30),
                                impressions=[{"item_key": "k" * 16, "position": 0}])
        self.assertEqual(feed.shown_counts(self.data, self.now, 7).get("k" * 16), 2)

    def test_explore_section_holds_unmatched_items_and_is_exclusive(self):
        t = self.now - timedelta(minutes=5)
        _snap(self.settings, "a", t, [_item("a", x) for x in _distinct_titles(16)])
        res = self._build()
        explore = res["sections"]["explore"]
        self.assertEqual(len(explore), min(feed.DEFAULT_CONFIG["explore_size"], len(explore)))
        self.assertGreater(len(explore), 0)
        self.assertTrue(all(c["section"] == "explore" for c in explore))
        keys = [c["key"] for c in self._all(res)]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(any(x["section"] == "explore" for x in res["impressions"]))

    def test_explore_disabled_by_zero_count(self):
        t = self.now - timedelta(minutes=5)
        _snap(self.settings, "a", t, [_item("a", x) for x in _distinct_titles(16)])
        self.assertEqual(self._build(cfg={"explore_size": 0})["sections"]["explore"], [])

    def test_explore_count_follows_setting(self):
        t = self.now - timedelta(minutes=5)
        _snap(self.settings, "a", t, [_item("a", x) for x in _distinct_titles(30)])
        res = self._build(cfg={"explore_size": 3})
        self.assertLessEqual(len(res["sections"]["explore"]), 3)
        self.assertGreater(len(res["sections"]["explore"]), 0)

    def test_hidden_source_is_excluded_and_counted(self):
        t = self.now - timedelta(minutes=5)
        _snap(self.settings, "a", t, [_item("a", "来自甲源的新闻条目")])
        _snap(self.settings, "b", t, [_item("b", "来自乙源的新闻条目")])
        res = self._build(hidden_sources={"b"})
        titles = [c["title"] for c in self._all(res)]
        self.assertIn("来自甲源的新闻条目", titles)
        self.assertNotIn("来自乙源的新闻条目", titles)
        self.assertEqual(res["stats"]["hidden_sources"], 1)

    def test_prefs_roundtrip_and_undo(self):
        feed.set_source_hidden(self.data, "b", True)
        self.assertEqual(feed.load_prefs(self.data)["hidden_sources"], ["b"])
        feed.set_source_hidden(self.data, "b", False)
        self.assertEqual(feed.load_prefs(self.data)["hidden_sources"], [])

    def test_api_hide_source_and_block_topic(self):
        out = api.feed_set_source_hidden_api(self.settings, {"source_id": "b", "hidden": True})
        self.assertEqual(out["hidden_sources"], ["b"])
        with self.assertRaises(ApiError):
            api.feed_set_source_hidden_api(self.settings, {"source_id": "../x"})
        out2 = api.feed_block_topic_api(self.settings, {"topic": "广告"})
        self.assertIn("广告", out2["exclude"])
        with self.assertRaises(ApiError):
            api.feed_block_topic_api(self.settings, {"topic": "  "})
        # 屏蔽后，含该词的条目不再进入推荐
        _snap(self.settings, "a", self.now - timedelta(minutes=5), [_item("a", "广告植入的消息条目")])
        res = api.feed_view(self.settings, hours=24, now=self.now)
        self.assertEqual(res["stats"]["blocked"], 1)

    def test_replay_rewards_order_that_puts_positives_first(self):
        batch_time = self.now - timedelta(hours=2)
        # 展示顺序：正例在前；按时间倒序：正例（最早出现）在后
        titles = [f"回放测试条目编号{i}号" for i in range(10)]
        urls = {t: f"https://a.example/r{i}" for i, t in enumerate(titles)}
        items = []
        for pos, t in enumerate(titles):
            items.append({"item_key": item_key(urls[t], t), "position": pos,
                          "first_seen": (self.now - timedelta(hours=pos + 1)).isoformat(timespec="seconds")})
        # 前 3 条（推荐顺序中的正例）之前出现得最早
        for pos in range(3):
            items[pos]["first_seen"] = (self.now - timedelta(hours=20 + pos)).isoformat(timespec="seconds")
        feed.record_impressions(self.data, window_hours=24, impressions=items, now=batch_time)
        for t in titles[:3]:
            record_feedback(self.data, {"action": "interested", "source_id": "a", "title": t, "url": urls[t]})
        rep = feed_eval.replay(self.data, now=self.now, min_labeled=2)
        self.assertEqual(rep["batches"], 1)
        self.assertTrue(rep["sufficient"])
        self.assertGreater(rep["recommended"]["ndcg"], rep["baseline"]["ndcg"])
        self.assertEqual(rep["recommended"]["negative_rate"], 0.0)

    def test_replay_reports_insufficient_sample(self):
        rep = feed_eval.replay(self.data, now=self.now)
        self.assertFalse(rep["sufficient"])
        self.assertIn("样本不足", rep["message"])

    def test_replay_api_validates_days(self):
        with self.assertRaises(ApiError):
            api.feed_replay_api(self.settings, days=0)
        self.assertIn("recommended", api.feed_replay_api(self.settings, days=30))

    def test_impression_keeps_first_seen(self):
        api.record_feed_impressions_api(self.settings, {"window_hours": 24, "impressions": [
            {"item_key": "1" * 16, "position": 0, "section": "featured", "first_seen": "2026-11-12T10:00:00+08:00"}]})
        from core.store import read_jsonl
        row = list(read_jsonl(feed.shown_path(self.data)))[-1]
        self.assertEqual(row["items"][0]["first_seen"], "2026-11-12T10:00:00+08:00")


class TriageAppTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A"), "b": _src("b", "源B")})
        self.settings = load_settings(self.root)
        now = datetime.now().astimezone()
        # 两条标题必须互不相似，否则会聚成同一事件，分诊队列只剩一条
        _snap(self.settings, "a", now - timedelta(minutes=5), [_item("a", "今日天气晴朗适合出游")])
        _snap(self.settings, "b", now - timedelta(minutes=6), [_item("b", "芯片产业链出口新规解读")])
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        cache_data.clear()

    def test_triage_shows_one_card_and_skip_advances(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        at.toggle(key="feed_triage").set_value(True).run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        captions = [c.value for c in at.caption]
        self.assertTrue(any(c.startswith("快速分诊 1 /") for c in captions), captions)
        skip = [b for b in at.button if b.key and b.key.startswith("triage_skip_")]
        self.assertTrue(skip)
        skip[0].click().run()
        captions = [c.value for c in at.caption]
        self.assertTrue(any(c.startswith("快速分诊 2 /") for c in captions), captions)


if __name__ == "__main__":
    unittest.main()
