"""推荐信息流 P4：LLM 增强。全部使用假模型（fake ask），不依赖真实服务。

对应 next_doc/info_hub_recommendation_feed_design.md 第 6 节 P4。
"""
import json
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from streamlit import cache_data
from streamlit.testing.v1 import AppTest

from core import feed, feed_llm
from core.feedback import item_key
from core.settings import load_settings
from server import api
from server.api import ApiError
from tests.helpers import make_project
from tests.test_feed import _item, _snap, _src, _sources_of
from tests.test_feed_p2 import _distinct_titles

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def fake_ask(prompt, system=""):
    """按提示词类型返回固定结构的 JSON：摘要为每条给出主题与摘要；补聚判断一律回答“同一事件”。"""
    lines = [ln for ln in prompt.split("\n") if ln[:1].isdigit()]
    if "主题标签" in prompt:
        return json.dumps([{"id": i, "topic": f"话题{i}", "summary": f"摘要{i}"} for i in range(len(lines))],
                          ensure_ascii=False)
    return json.dumps([{"id": i, "same": True} for i in range(len(lines))])


class CountingAsk:
    def __init__(self, fn=fake_ask):
        self.fn = fn
        self.calls = 0

    def __call__(self, prompt, system=""):
        self.calls += 1
        return self.fn(prompt, system=system)


class LlmCoreTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A"), "b": _src("b", "源B")})
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir
        self.now = datetime.now().astimezone().replace(microsecond=0)
        self.sources = _sources_of(self.settings)
        titles = _distinct_titles(6)
        _snap(self.settings, "a", self.now - timedelta(minutes=5), [_item("a", t) for t in titles])
        self.cfg = feed.load_config(self.settings.raw)
        self.llm_cfg = feed_llm.llm_config(self.settings.raw)

    def _sections(self):
        return feed.build_feed(data_dir=self.data, raw_dir=self.settings.paths.raw_dir, sources=self.sources,
                               hours=24, cfg=self.cfg, now=self.now)["sections"]

    def test_defaults_are_off_and_config_overrides(self):
        self.assertFalse(feed_llm.llm_config(None)["enabled"])
        cfg = feed_llm.llm_config({"feed": {"llm": {"daily_limit": 5, "pair_enabled": False}}})
        self.assertEqual(cfg["daily_limit"], 5)
        self.assertFalse(cfg["pair_enabled"])
        self.assertFalse(feed.load_prefs(self.data)["llm_enabled"])

    def test_run_writes_summaries_and_second_run_is_free(self):
        ask = CountingAsk()
        rep = feed_llm.run(self.data, ask, self._sections(), self.llm_cfg, self.cfg["cluster_threshold"], self.now)
        self.assertGreater(rep["summaries_done"], 0)
        self.assertGreater(ask.calls, 0)
        cache = feed_llm.load_cache(self.data)
        self.assertTrue(all("topic" in v for v in cache["items"].values()))
        before = ask.calls
        plan = feed_llm.plan(self._sections(), self.llm_cfg, cache, self.cfg["cluster_threshold"], self.data, self.now)
        self.assertEqual(plan["summary_targets"], [])
        self.assertEqual(plan["pending_pairs"], [])
        rep2 = feed_llm.run(self.data, ask, self._sections(), self.llm_cfg, self.cfg["cluster_threshold"], self.now)
        self.assertEqual(rep2["calls"], 0, "已有结论的条目与条目对不得重复调用模型")
        self.assertEqual(ask.calls, before)

    def test_daily_limit_stops_calls_and_is_reported(self):
        ask = CountingAsk()
        cfg = dict(self.llm_cfg, daily_limit=1, batch_size=1)
        rep = feed_llm.run(self.data, ask, self._sections(), cfg, self.cfg["cluster_threshold"], self.now)
        self.assertEqual(rep["calls"], 1)
        self.assertEqual(ask.calls, 1)
        self.assertTrue(rep["stopped_by_limit"])
        self.assertEqual(feed_llm.used_today(self.data, self.now), 1)
        rep2 = feed_llm.run(self.data, CountingAsk(), self._sections(), cfg, self.cfg["cluster_threshold"], self.now)
        self.assertEqual(rep2["calls"], 0)
        self.assertTrue(rep2["stopped_by_limit"])

    def test_usage_is_per_day(self):
        feed_llm.charge(self.data, 3, self.now)
        self.assertEqual(feed_llm.used_today(self.data, self.now), 3)
        self.assertEqual(feed_llm.used_today(self.data, self.now + timedelta(days=1)), 0)

    def test_plan_estimates_calls_without_calling_model(self):
        plan = feed_llm.plan(self._sections(), dict(self.llm_cfg, batch_size=2), feed_llm.load_cache(self.data),
                             self.cfg["cluster_threshold"], self.data, self.now)
        n = len(plan["summary_targets"])
        self.assertGreater(n, 0)
        self.assertGreaterEqual(plan["calls_estimate"], -(-n // 2))
        self.assertEqual(plan["used_today"], 0)

    def test_bad_model_output_is_reported_not_raised(self):
        ask = CountingAsk(lambda p, system="": "这不是 JSON")
        rep = feed_llm.run(self.data, ask, self._sections(), self.llm_cfg, self.cfg["cluster_threshold"], self.now)
        self.assertEqual(rep["summaries_done"], 0)
        self.assertTrue(rep["errors"])

    def test_cached_merge_judge_merges_only_same_event_pairs(self):
        pool = [{"item_key": "k%d" % i, "title": f"t{i}"} for i in range(3)]
        groups = [[0], [1], [2]]
        feed_llm.save_cache(self.data, {"items": {}, "pairs": {
            feed_llm.pair_key("k0", "k1"): {"same": True, "at": "x"},
            feed_llm.pair_key("k1", "k2"): {"same": False, "at": "x"}}})
        merged = feed_llm.cached_merge_judge(self.data)(pool, groups)
        self.assertEqual(sorted(sorted(g) for g in merged), [[0, 1], [2]])

    def test_near_pairs_respects_range_and_cap(self):
        cards = [{"key": f"c{i}", "title": t} for i, t in enumerate(_distinct_titles(4))]
        self.assertEqual(feed_llm.near_pairs(cards, self.llm_cfg, 0.45), [])  # 完全不相似，不在区间内


class LlmApiTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A"), "b": _src("b", "源B")})
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir
        self.now = datetime.now().astimezone().replace(microsecond=0)
        # 二元组 Jaccard≈0.14：不会被聚合阶段合并，只能靠 LLM 的“同一事件”结论合并
        ta, tb = "英伟达财报超预期，股价盘后大涨", "英伟达发布财报 营收创新高"
        self.ua, self.ub = "https://a.example/x1", "https://b.example/x2"
        _snap(self.settings, "a", self.now - timedelta(minutes=5), [_item("a", ta, url=self.ua)])
        _snap(self.settings, "b", self.now - timedelta(minutes=5), [_item("b", tb, url=self.ub)])
        self.ka, self.kb = item_key(self.ua, ta), item_key(self.ub, tb)

    @staticmethod
    def _cards(res):
        sec = res["sections"]
        out = list(sec["featured"]) + list(sec["explore"]) + list(sec["hot"]) + list(sec["others"])
        for g in sec["topics"]:
            out += g["cards"]
        return out

    def test_merge_applies_only_when_enabled(self):
        feed_llm.save_cache(self.data, {"items": {}, "pairs": {
            feed_llm.pair_key(self.ka, self.kb): {"same": True, "at": "x"}}})
        self.assertEqual(len(self._cards(api.feed_view(self.settings, hours=24, now=self.now))), 2)
        api.feed_set_llm_api(self.settings, {"enabled": True})
        cards = self._cards(api.feed_view(self.settings, hours=24, now=self.now))
        self.assertEqual(len(cards), 1)
        self.assertEqual(len(cards[0]["sources"]), 2)

    def test_labels_attach_only_when_enabled(self):
        feed_llm.save_cache(self.data, {"items": {self.ka: {"topic": "芯片", "summary": "新品"}}, "pairs": {}})
        off = api.feed_view(self.settings, hours=24, now=self.now)
        self.assertFalse(any("llm_topic" in c for c in off["sections"]["featured"] + off["sections"]["others"]))
        api.feed_set_llm_api(self.settings, {"enabled": True})
        on = api.feed_view(self.settings, hours=24, now=self.now)
        labelled = [c for c in on["sections"]["featured"] + on["sections"]["others"] if c.get("llm_topic")]
        self.assertEqual(labelled[0]["llm_topic"], "芯片")

    def test_run_refused_when_disabled(self):
        with self.assertRaises(ApiError) as ctx:
            api.feed_llm_run_api(self.settings, hours=24)
        self.assertEqual(ctx.exception.status, 400)

    def test_run_reports_unavailable_provider_as_503(self):
        api.feed_set_llm_api(self.settings, {"enabled": True})
        with self.assertRaises(ApiError) as ctx:
            api.feed_llm_run_api(self.settings, hours=24)
        self.assertEqual(ctx.exception.status, 503)

    def test_status_and_plan_do_not_call_model(self):
        api.feed_set_llm_api(self.settings, {"enabled": True})
        st = api.feed_llm_status_api(self.settings)
        self.assertTrue(st["enabled"])
        self.assertEqual(st["used_today"], 0)
        plan = api.feed_llm_plan_api(self.settings, hours=24)
        self.assertIn("calls_estimate", plan)
        self.assertEqual(feed_llm.used_today(self.data, datetime.now().astimezone()), 0)


class LlmAppSmokeTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A")})
        self.settings = load_settings(self.root)
        _snap(self.settings, "a", datetime.now().astimezone() - timedelta(minutes=5),
              [_item("a", "页面冒烟测试条目")])
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        cache_data.clear()

    def test_llm_panel_renders_and_offers_run_only_when_enabled(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertFalse([b for b in at.button if b.key == "feed_llm_run"])
        at.toggle(key="feed_llm_on").set_value(True).run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertTrue([b for b in at.button if b.key == "feed_llm_run"])


if __name__ == "__main__":
    unittest.main()
