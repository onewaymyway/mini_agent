"""推荐信息流 P1：聚合去重、多信号打分、过滤与分区、一句话理由、曝光记录，以及 API 与看板冒烟。

对应 next_doc/info_hub_recommendation_feed_design.md 第 3、4 节。
"""
import hashlib
import math
import os
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from streamlit import cache_data
from streamlit.testing.v1 import AppTest

from core import evaluation, feed, merged, profile as prof
from core.feedback import item_key, read_feedback, record_feedback, validate_event
from core.settings import load_settings
from core.store import atomic_write_json, read_jsonl
from server import api
from server.api import ApiError
from tests.helpers import make_project

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _src(sid, name, category="news", kind="feed"):
    return {"id": sid, "name": name, "category": category, "kind": kind, "adapter": "rss",
            "request": {"url": f"https://{sid}/feed"}}


def _item(sid, title, rank=None, url=None, summary=""):
    digest = hashlib.md5(title.encode("utf-8")).hexdigest()[:10]
    return {"id": f"{sid}:{title}", "source_id": sid, "title": title,
            "url": url or f"https://{sid}.example/{digest}",
            "summary": summary, "rank": rank, "heat": None, "published_at": None}


def _snap(settings, sid, when, items, new_titles=()):
    new_ids = {f"{sid}:{t}" for t in new_titles}
    path = settings.paths.raw_dir / sid / f"{when:%Y-%m-%d}" / f"{when:%H%M%S}-{uuid.uuid4().hex[:8]}.json"
    atomic_write_json(path, {"source_id": sid, "fetched_at": when.isoformat(timespec="seconds"),
                             "item_count": len(items), "new_count": len(new_ids),
                             "changes": {"new": [{"id": i} for i in new_ids]}, "items": items})


def _sources_of(settings):
    from core.sources import load_sources
    loaded = load_sources(settings)
    return [{"id": sid, "name": s.name, "kind": s.kind} for sid, s in sorted(loaded.specs.items())]


class ClusterTests(unittest.TestCase):
    """聚合：同一事件聚在一起，不同事件不误并（宁可漏聚，不要误并）。"""

    def _titles_to_groups(self, titles, urls=None):
        items = [{"title": t, "url": (urls or {}).get(t, "")} for t in titles]
        groups = feed.cluster_items(items, threshold=0.45)
        return [sorted(titles[i] for i in g) for g in groups]

    def test_same_event_variants_are_clustered(self):
        pairs = [("央行发布三季度货币政策执行报告", "央行三季度货币政策执行报告发布"),
                 ("三季度GDP同比增长4.5%", "国家统计局：前三季度GDP同比增长4.5%"),
                 ("苹果发布新款M5芯片MacBook", "新款MacBook Pro搭载M5芯片发布")]
        for a, b in pairs:
            with self.subTest(a=a):
                groups = self._titles_to_groups([a, b])
                self.assertEqual(len(groups), 1, groups)

    def test_different_events_are_not_merged(self):
        pairs = [("央行发布三季度货币政策…", "央行宣布降准0.5个百分点"),
                 ("苹果发布新款M5芯片MacBook", "苹果公司回应数据泄露传闻")]
        for a, b in pairs:
            with self.subTest(a=a):
                self.assertEqual(len(self._titles_to_groups([a, b])), 2)

    def test_weak_paraphrase_is_left_apart_by_design(self):
        # 设计文档样例：措辞差异大的同一事件（Jaccard≈0.14）按“宁可漏聚”保持分开
        groups = self._titles_to_groups(["英伟达财报超预期，股价盘后大涨", "英伟达发布财报 营收创新高"])
        self.assertEqual(len(groups), 2)

    def test_identical_normalized_url_merges_even_with_different_titles(self):
        groups = self._titles_to_groups(["甲标题完全不同", "乙标题毫不相干"],
                                        urls={"甲标题完全不同": "https://x.example/a/",
                                              "乙标题毫不相干": "https://X.example/a"})
        self.assertEqual(len(groups), 1)

    def test_common_bigrams_do_not_prevent_true_duplicates(self):
        # 大量标题都含“发布”“新闻”等高频二元组，真正的重复仍应被找到，且不会因为性能而漏掉
        noise = [f"今日发布第{i}条行业动态{i * 7919 % 997}" for i in range(120)]
        dup = ["央行发布三季度货币政策执行报告", "央行三季度货币政策执行报告发布"]
        items = [{"title": t, "url": ""} for t in noise + dup]
        groups = feed.cluster_items(items, threshold=0.45)
        i0, i1 = len(noise), len(noise) + 1
        group = next(g for g in groups if i0 in g)
        self.assertIn(i1, group, "高频二元组被跳过后，真正的重复仍应聚在一起")


class ScoringTests(unittest.TestCase):
    NOW = datetime(2026, 11, 12, 12, 0).astimezone()

    def test_freshness_halves_per_half_life_and_is_monotonic(self):
        f0 = feed.freshness(self.NOW.isoformat(), self.NOW, 12)
        f12 = feed.freshness((self.NOW - timedelta(hours=12)).isoformat(), self.NOW, 12)
        f24 = feed.freshness((self.NOW - timedelta(hours=24)).isoformat(), self.NOW, 12)
        self.assertEqual(f0, 1.0)
        self.assertAlmostEqual(f12, 0.5, places=3)
        self.assertLess(f24, f12)

    def test_freshness_unknown_time_is_zero(self):
        self.assertEqual(feed.freshness(None, self.NOW, 12), 0.0)

    def test_importance_grows_with_sources_and_rank(self):
        self.assertLess(feed.importance(1, 10, 0), feed.importance(3, 10, 0))
        self.assertLess(feed.importance(1, 10, 0), feed.importance(1, 1, 0))
        self.assertGreater(feed.importance(1, 1, 8), feed.importance(1, 1, 0))
        # 无排名、无上升时只剩多源分量：0.4 * log2(1+1)/log2(1+4)
        self.assertEqual(feed.importance(1, None, 0), round(0.4 * math.log2(2) / math.log2(5), 4))

    def test_persistence_penalty_only_for_never_new_long_standing_items(self):
        old = [{"is_new": False, "seen_count": 30}]
        fresh = [{"is_new": True, "seen_count": 30}]
        short = [{"is_new": False, "seen_count": 3}]
        self.assertGreater(feed.persistence_penalty(old, 12), 0)
        self.assertEqual(feed.persistence_penalty(fresh, 12), 0)
        self.assertEqual(feed.persistence_penalty(short, 12), 0)

    def test_quantile_normalize_handles_ties_and_flat_input(self):
        self.assertEqual(feed.quantile_normalize([3, 3, 3]), [0.0, 0.0, 0.0])
        out = feed.quantile_normalize([0, 0, 5, 10])
        self.assertEqual(out[0], out[1])
        self.assertLess(out[0], out[2])
        self.assertEqual(out[3], 1.0)

    def test_source_affinity_neutral_without_data_and_rises_with_positive_feedback(self):
        self.assertAlmostEqual(feed.source_affinity("s", positives={}, negatives={}, learned_sources={}), 0.5)
        up = feed.source_affinity("s", positives={"s": 10}, negatives={}, learned_sources={})
        down = feed.source_affinity("s", positives={}, negatives={"s": 10}, learned_sources={})
        self.assertGreater(up, 0.5)
        self.assertLess(down, 0.5)

    def test_config_partial_override_and_invalid_values_fall_back(self):
        cfg = feed.load_config({"feed": {"featured_size": 3, "weights": {"interest": 0.9},
                                         "cluster_threshold": "bad"}})
        self.assertEqual(cfg["featured_size"], 3)
        self.assertEqual(cfg["weights"]["interest"], 0.9)
        self.assertEqual(cfg["weights"]["freshness"], feed.DEFAULT_CONFIG["weights"]["freshness"])
        self.assertEqual(cfg["cluster_threshold"], feed.DEFAULT_CONFIG["cluster_threshold"])
        self.assertEqual(feed.load_config(None)["featured_size"], feed.DEFAULT_CONFIG["featured_size"])

    def test_reason_mentions_topic_sources_age_and_rise(self):
        card = {"matches": ["大模型"], "n_sources": 3, "age": "30 分钟前出现", "rise": 5,
                "components": {"persistence_penalty": 0}}
        reason = feed.build_reason(card)
        for part in ("命中关注主题『大模型』", "3 个来源报道", "30 分钟前出现", "排名上升 5 位"):
            self.assertIn(part, reason)
        self.assertEqual(feed.build_reason({"n_sources": 1, "components": {}}), "综合排序靠前")


class BuildFeedTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({
            "a": _src("a", "源A", kind="feed"),
            "b": _src("b", "源B", kind="feed"),
            "c": _src("c", "源C", kind="hot_list", category="hot_community"),
            "d": _src("d", "源D", kind="feed"),
        })
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir
        self.now = datetime.now().astimezone().replace(microsecond=0)
        self.sources = _sources_of(self.settings)

    def _build(self, **kw):
        cfg = feed.load_config(self.settings.raw)
        for k, v in (kw.pop("cfg", {}) or {}).items():
            cfg[k] = v
        return feed.build_feed(data_dir=self.data, raw_dir=self.settings.paths.raw_dir, sources=self.sources,
                               hours=kw.pop("hours", 24), cfg=cfg, now=self.now, **kw)

    def _all_cards(self, res):
        out = list(res["sections"]["featured"]) + list(res["sections"]["hot"])
        for g in res["sections"]["topics"]:
            out += g["cards"]
        return out + list(res["sections"]["others"])

    def test_processed_items_are_hidden_and_later_goes_to_queue(self):
        t = self.now - timedelta(minutes=30)
        urls = {k: f"https://a.example/{k}" for k in ("x1", "x2", "x3", "x4")}
        _snap(self.settings, "a", t, [
            _item("a", "甲甲甲新闻一", url=urls["x1"]), _item("a", "乙乙乙新闻二", url=urls["x2"]),
            _item("a", "丙丙丙新闻三", url=urls["x3"]), _item("a", "丁丁丁新闻四", url=urls["x4"])])
        record_feedback(self.data, {"action": "interested", "source_id": "a", "title": "甲甲甲新闻一",
                                    "url": urls["x1"]})
        record_feedback(self.data, {"action": "not_interested", "source_id": "a", "title": "乙乙乙新闻二",
                                    "url": urls["x2"]})
        record_feedback(self.data, {"action": "later", "source_id": "a", "title": "丙丙丙新闻三",
                                    "url": urls["x3"]})
        # 打开过的条目也算已处理（打开记录的 item_key 与快照里的 URL 对齐）
        evaluation.record_open(self.data, source_id="a", item_key=item_key(urls["x4"], "丁丁丁新闻四"),
                               view="feed_v1", mode="dedicated", now=self.now)
        res = self._build()
        titles = [c["title"] for c in self._all_cards(res)]
        self.assertNotIn("甲甲甲新闻一", titles)
        self.assertNotIn("乙乙乙新闻二", titles)
        self.assertNotIn("丁丁丁新闻四", titles)
        self.assertEqual([c["title"] for c in res["sections"]["later"]], ["丙丙丙新闻三"])
        self.assertNotIn("丙丙丙新闻三", titles)

    def test_include_processed_marks_them_instead_of_hiding(self):
        _snap(self.settings, "a", self.now - timedelta(minutes=5),
              [_item("a", "甲甲甲新闻一", url="https://a.example/y")])
        record_feedback(self.data, {"action": "interested", "source_id": "a", "title": "甲甲甲新闻一",
                                    "url": "https://a.example/y"})
        res = self._build(include_processed=True)
        cards = self._all_cards(res)
        self.assertEqual(len(cards), 1)
        self.assertTrue(cards[0]["processed"])
        self.assertEqual(cards[0]["processed_by"], "interested")

    def test_blocked_terms_are_removed_before_clustering(self):
        prof.save_explicit(self.data, topics=[], exclude=["广告"])
        _snap(self.settings, "a", self.now - timedelta(minutes=5),
              [_item("a", "广告植入的新品发布会"), _item("a", "真正的新品发布会报道")])
        res = self._build()
        self.assertEqual(res["stats"]["blocked"], 1)
        self.assertEqual([c["title"] for c in self._all_cards(res)], ["真正的新品发布会报道"])

    def _seed_hot_scenario(self):
        t = self.now - timedelta(minutes=20)
        same = "央行发布三季度货币政策执行报告"
        for sid in ("a", "b", "c"):
            _snap(self.settings, sid, t, [_item(sid, same)])
        _snap(self.settings, "a", t, [_item("a", "今天天气很好适合出游", rank=1)])
        for i in range(12):
            _snap(self.settings, "d", t, [_item("d", f"独立无关条目编号{i}号消息内容{i * 13}")])
        return same

    def test_multi_source_event_lands_in_hot_section(self):
        same = self._seed_hot_scenario()
        res = self._build(cfg={"featured_size": 0, "hot_min_sources": 3})
        hot = res["sections"]["hot"]
        self.assertEqual([c["title"] for c in hot], [same])
        self.assertEqual(hot[0]["n_sources"], 3)
        self.assertEqual(len(hot[0]["sources"]), 3)

    def test_each_cluster_appears_in_exactly_one_section(self):
        same = self._seed_hot_scenario()
        res = self._build(cfg={"featured_size": 2, "hot_min_sources": 3})
        self.assertEqual(len(res["sections"]["featured"]), 2)
        keys = [c["key"] for c in self._all_cards(res)]
        self.assertEqual(len(keys), len(set(keys)), "同一簇不应出现在两个分区")
        self.assertIn(same, [c["title"] for c in res["sections"]["featured"]],
                      "多源报道的事件重要性最高，应排在精选首位")

    def test_featured_is_top_scored_and_impressions_are_positional(self):
        t = self.now - timedelta(minutes=10)
        _snap(self.settings, "a", t, [_item("a", f"普通资讯标题第{i}篇内容{i * 31}") for i in range(15)])
        res = self._build(cfg={"featured_size": 3, "others_limit": 5})
        imps = res["impressions"]
        self.assertEqual([x["position"] for x in imps], list(range(len(imps))))
        self.assertEqual(imps[0]["section"], "featured")
        self.assertTrue(all(set(x["components"]) >= {"interest", "freshness", "importance"} for x in imps))
        others_shown = [x for x in imps if x["section"] == "others"]
        self.assertLessEqual(len(others_shown), 5)

    def test_topic_grouping_uses_explicit_interest_terms(self):
        prof.save_explicit(self.data, topics=["大模型"], exclude=[])
        t = self.now - timedelta(minutes=10)
        _snap(self.settings, "a", t, [_item("a", "大模型推理成本下降三成"), _item("a", "大模型训练新方法发布"),
                                     _item("a", "天气晴朗适合郊游出行")])
        # 精选优先于主题：这里把精选设为 0，让主题分区有内容可分
        res = self._build(cfg={"featured_size": 0, "topic_min_group": 2})
        groups = {g["topic"]: [c["title"] for c in g["cards"]] for g in res["sections"]["topics"]}
        self.assertIn("大模型", groups)
        self.assertEqual(len(groups["大模型"]), 2)
        card = groups["大模型"]
        self.assertTrue(card)
        reasons = [c["reason"] for g in res["sections"]["topics"] for c in g["cards"]]
        self.assertTrue(any("命中关注主题『大模型』" in r for r in reasons))

    def test_rank_rise_and_reason_text(self):
        t1 = self.now - timedelta(hours=2)
        t2 = self.now - timedelta(minutes=10)
        _snap(self.settings, "a", t1, [_item("a", "排名变化的热点话题", rank=9)])
        _snap(self.settings, "a", t2, [_item("a", "排名变化的热点话题", rank=2)])
        res = self._build()
        card = self._all_cards(res)[0]
        self.assertEqual(card["rise"], 7)
        self.assertEqual(card["rank"], 2)
        self.assertIn("排名上升 7 位", card["reason"])

    def test_long_standing_item_is_penalised_but_still_present(self):
        base = self.now - timedelta(hours=10)
        for i in range(20):
            _snap(self.settings, "c", base + timedelta(minutes=30 * i), [_item("c", "一直挂在榜上的老条目")])
        _snap(self.settings, "a", self.now - timedelta(minutes=5), [_item("a", "刚出现的新消息条目")])
        res = self._build()
        by_title = {c["title"]: c for c in self._all_cards(res)}
        self.assertGreater(by_title["一直挂在榜上的老条目"]["components"]["persistence_penalty"], 0)
        self.assertEqual(by_title["刚出现的新消息条目"]["components"]["persistence_penalty"], 0)
        self.assertLess(by_title["一直挂在榜上的老条目"]["score"], by_title["刚出现的新消息条目"]["score"])

    def test_cold_start_flag_when_profile_empty(self):
        _snap(self.settings, "a", self.now - timedelta(minutes=5), [_item("a", "随便一条新闻内容")])
        res = self._build()
        self.assertTrue(res["stats"]["cold_start"])

    def test_record_impressions_appends_feed_row_and_keeps_old_format(self):
        _snap(self.settings, "a", self.now - timedelta(minutes=5), [_item("a", "随便一条新闻内容")])
        res = self._build()
        feed.record_impressions(self.data, window_hours=24, impressions=res["impressions"], now=self.now)
        rows = list(read_jsonl(feed.shown_path(self.data)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["view"], "feed_v1")
        self.assertEqual(rows[0]["items"][0]["position"], 0)
        # 旧的曝光记录（ranked/plain）不受影响
        evaluation.record_impression(self.data, source_id="a", snapshot_id="s", view="plain", item_keys=["k"],
                                     now=self.now)
        self.assertEqual(len(list(read_jsonl(evaluation.exposure_path(self.data)))), 1)

    def test_merged_items_carry_first_rank_for_rise(self):
        t1 = self.now - timedelta(hours=1)
        t2 = self.now - timedelta(minutes=5)
        _snap(self.settings, "a", t1, [_item("a", "甲排名", rank=4)])
        _snap(self.settings, "a", t2, [_item("a", "甲排名", rank=1)])
        res = merged.merge_sources(self.settings.paths.raw_dir, ["a"], 24, now=self.now)
        row = res["items"][0]
        self.assertEqual(row["first_rank"], 4)
        self.assertEqual(row["rank"], 1)


class FeedApiTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A"), "c": _src("c", "源C", category="hot_community")})
        self.settings = load_settings(self.root)
        self.now = datetime.now().astimezone()
        _snap(self.settings, "a", self.now - timedelta(minutes=5), [_item("a", "测试条目甲甲")])

    def test_feed_view_returns_sections_and_respects_category_filter(self):
        res = api.feed_view(self.settings, hours=24, category="news")
        self.assertIn("sections", res)
        self.assertIn("weights", res["config"])
        self.assertEqual(res["stats"]["raw_items"], 1)
        res2 = api.feed_view(self.settings, hours=24, category="hot_community")
        self.assertEqual(res2["stats"]["raw_items"], 0)

    def test_feed_view_validates_window(self):
        for bad in (0, -1, 24 * 31, "abc"):
            with self.subTest(hours=bad), self.assertRaises(ApiError):
                api.feed_view(self.settings, hours=bad)

    def test_record_feed_impressions_api_validates_payload(self):
        with self.assertRaises(ApiError):
            api.record_feed_impressions_api(self.settings, {"impressions": [{"item_key": "xyz", "position": 0}]})
        with self.assertRaises(ApiError):
            api.record_feed_impressions_api(self.settings, {"impressions": [
                {"item_key": "0" * 16, "position": -1}]})
        out = api.record_feed_impressions_api(self.settings, {"window_hours": 24, "impressions": [
            {"item_key": "0" * 16, "position": 0, "section": "featured", "score": 0.5, "components": {}}]})
        self.assertEqual(out["recorded"], 1)

    def test_feedback_and_open_accept_feed_view(self):
        ev = validate_event({"action": "later", "source_id": "a", "title": "x", "view": "feed_v1"})
        self.assertEqual(ev["view"], "feed_v1")
        ev2 = validate_event({"action": "later", "source_id": "a", "title": "x", "view": "bogus"})
        self.assertEqual(ev2["view"], "")
        evaluation.record_open(self.settings.paths.data_dir, source_id="a", item_key="0" * 16, view="feed_v1",
                               mode="dedicated")
        opens = [r for r in read_jsonl(evaluation.exposure_path(self.settings.paths.data_dir)) if r["kind"] == "open"]
        self.assertEqual(opens[-1]["view"], "feed_v1")


class FeedAppSmokeTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A"), "b": _src("b", "源B"), "c": _src("c", "源C", kind="hot_list")})
        self.settings = load_settings(self.root)
        now = datetime.now().astimezone()
        same = "央行发布三季度货币政策执行报告"
        for sid in ("a", "b", "c"):
            _snap(self.settings, sid, now - timedelta(minutes=20), [_item(sid, same)])
        _snap(self.settings, "a", now - timedelta(minutes=10), [_item("a", "今天的一条普通新闻内容")])
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        cache_data.clear()

    def test_feed_tab_renders_sections_and_records_impression(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertIn("推荐", [t.label for t in at.tabs])
        subheaders = [s.value for s in at.subheader]
        self.assertTrue(any("今日精选" in s for s in subheaders), subheaders)
        imp = list(read_jsonl(feed.shown_path(self.settings.paths.data_dir)))
        self.assertGreaterEqual(len(imp), 1)
        self.assertEqual(imp[0]["view"], "feed_v1")

    def test_feedback_button_writes_events_for_every_member(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        buttons = [b for b in at.button if b.key and b.key.startswith("feed_fb_interested_")]
        self.assertTrue(buttons, "应存在 👍 按钮")
        self.assertFalse([b for b in at.button if b.key and b.key.startswith("fb_interested_")],
                         "推荐页按钮不应占用抓取结果页的 fb_* 键名")
        buttons[0].click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        events = read_feedback(self.settings.paths.data_dir)
        self.assertTrue(all(e["view"] == "feed_v1" for e in events))
        self.assertGreaterEqual(len(events), 3, "同一事件的三条报道应各记录一次")


if __name__ == "__main__":
    unittest.main()
