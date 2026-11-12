"""第三阶段：搜索模板校验、查询词选择、候选生成上限与去重、确认/忽略、看板渲染。"""
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from streamlit.testing.v1 import AppTest

from core import discovery, profile as prof
from core.settings import load_settings
from core.source_store import ID_RE
from server import api
from server.api import ApiError
from tests.helpers import make_project

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _tpl(tid="site_search", url="https://example.com/search?q={query}"):
    return {"id": tid, "name": "站内搜索",
            "source": {"kind": "feed", "adapter": "html_list", "category": "news",
                       "request": {"url": url}, "schedule": {"every": "6h"},
                       "parse": {"list_selector": "div.result", "fields": {"title": "a.title", "url": "a.title@href"}},
                       "policy": {"max_items": 20}, "tags": ["搜索"]}}


def write_discovery(root: Path, templates, daily=2, pending=5):
    (root / "config" / "discovery.yaml").write_text(
        yaml.safe_dump({"limits": {"daily": daily, "pending": pending}, "templates": templates},
                       allow_unicode=True), encoding="utf-8")


def learned_profile(terms, explicit_topics=(), exclude=(), avoid=()):
    p = prof.empty_profile()
    p["learned"]["keywords"] = {t: {"score": s, "evidence": e} for t, s, e in terms}
    p["explicit"] = {"topics": list(explicit_topics), "exclude": list(exclude)}
    p["llm"] = {"avoid": list(avoid)} if avoid else {}
    return p


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})

    def test_valid_template_loads(self):
        write_discovery(self.root, [_tpl()])
        self.assertEqual(discovery.load_templates(self.root)[0]["id"], "site_search")

    def test_missing_placeholder_rejected(self):
        write_discovery(self.root, [_tpl(url="https://example.com/search")])
        with self.assertRaises(discovery.DiscoveryError):
            discovery.load_templates(self.root)

    def test_placeholder_twice_rejected(self):
        write_discovery(self.root, [_tpl(url="https://e.com/{query}/{query}")])
        with self.assertRaises(discovery.DiscoveryError):
            discovery.load_templates(self.root)

    def test_duplicate_and_bad_ids_rejected(self):
        write_discovery(self.root, [_tpl("a1"), _tpl("a1")])
        with self.assertRaises(discovery.DiscoveryError):
            discovery.load_templates(self.root)
        write_discovery(self.root, [_tpl("Bad-Id")])
        with self.assertRaises(discovery.DiscoveryError):
            discovery.load_templates(self.root)

    def test_empty_default_config_has_no_templates(self):
        self.assertEqual(discovery.load_templates(self.root), [])


class QueryAndInstantiateTests(unittest.TestCase):
    def test_explicit_first_and_threshold_applied(self):
        p = learned_profile([("量子计算", 2.5, 3), ("弱词", 0.4, 5), ("单次", 3.0, 1)],
                            explicit_topics=["机器人"])
        queries = [q for q, _ in discovery.profile_queries(p)]
        self.assertEqual(queries[0], "机器人")
        self.assertIn("量子计算", queries)
        self.assertNotIn("弱词", queries)
        self.assertNotIn("单次", queries)

    def test_exclude_and_avoid_are_respected(self):
        p = learned_profile([("彩票开奖", 2.0, 3), ("芯片", 2.0, 3)], exclude=["彩票"], avoid=["芯片"])
        self.assertEqual(discovery.profile_queries(p), [])

    def test_instantiate_quotes_query_and_disables(self):
        tpl = {"id": "site_search", "name": "站内搜索", "source": _tpl()["source"]}
        src = discovery.instantiate(tpl, "大模型 推理")
        self.assertIn("%E5%A4%A7%E6%A8%A1%E5%9E%8B%20%E6%8E%A8%E7%90%86", src["request"]["url"])
        self.assertFalse(src["enabled"])
        self.assertTrue(ID_RE.match(src["id"]))
        self.assertIn("发现", src["tags"])


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.data = self.root / "data"
        self.templates = [{"id": "site_search", "name": "站内搜索", "source": _tpl()["source"]}]
        self.ok = lambda source: []
        self.p = learned_profile([("量子计算", 2.5, 3), ("大模型", 2.0, 2), ("机器人", 1.5, 2)])
        self.now = datetime(2026, 11, 12, 10, 0).astimezone()

    def test_daily_cap_limits_generation(self):
        out = discovery.generate(self.data, self.p, self.templates, self.ok, daily_cap=2, pending_cap=10, now=self.now)
        self.assertEqual(len(out["added"]), 2)
        again = discovery.generate(self.data, self.p, self.templates, self.ok, daily_cap=2, pending_cap=10, now=self.now)
        self.assertEqual(len(again["added"]), 0)

    def test_pending_cap_limits_generation(self):
        out = discovery.generate(self.data, self.p, self.templates, self.ok, daily_cap=10, pending_cap=1, now=self.now)
        self.assertEqual(len(out["added"]), 1)

    def test_no_repeat_after_reject_or_accept(self):
        out = discovery.generate(self.data, self.p, self.templates, self.ok, daily_cap=1, pending_cap=10, now=self.now)
        cid = out["added"][0]["id"]
        discovery.decide(self.data, cid, "rejected", now=self.now)
        tomorrow = self.now + timedelta(days=1)
        again = discovery.generate(self.data, self.p, self.templates, self.ok, daily_cap=5, pending_cap=10, now=tomorrow)
        self.assertNotIn(cid, [c["id"] for c in again["added"]])

    def test_invalid_candidates_skipped(self):
        out = discovery.generate(self.data, self.p, self.templates, lambda s: ["坏了"],
                                 daily_cap=5, pending_cap=10, now=self.now)
        self.assertEqual(out["added"], [])
        self.assertGreaterEqual(out["skipped"].get("invalid", 0), 1)

    def test_no_templates_or_queries(self):
        out = discovery.generate(self.data, self.p, [], self.ok, daily_cap=5, pending_cap=10, now=self.now)
        self.assertEqual(out["skipped"], {"no_template": 1})
        out = discovery.generate(self.data, prof.empty_profile(), self.templates, self.ok,
                                 daily_cap=5, pending_cap=10, now=self.now)
        self.assertEqual(out["skipped"], {"no_query": 1})

    def test_decide_twice_is_error(self):
        out = discovery.generate(self.data, self.p, self.templates, self.ok, daily_cap=1, pending_cap=10, now=self.now)
        cid = out["added"][0]["id"]
        discovery.decide(self.data, cid, "rejected", now=self.now)
        with self.assertRaises(discovery.DiscoveryError):
            discovery.decide(self.data, cid, "accepted", now=self.now)


class ApiDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                                             "adapter": "rss", "request": {"url": "https://n/feed"}}})
        write_discovery(self.root, [_tpl()], daily=3, pending=5)
        self.settings = load_settings(self.root)
        for _ in range(2):
            api.record_feedback(self.settings, {"action": "interested", "source_id": "news_a",
                                                "url": "https://n/1", "title": "量子计算突破"})

    def test_generate_then_accept_writes_enabled_source(self):
        out = api.generate_candidates(self.settings)
        self.assertGreaterEqual(out["added"], 1)
        listing = api.list_candidates_api(self.settings)
        cid = listing["candidates"][0]["id"]
        res = api.accept_candidate(self.settings, cid)
        target = self.root / res["path"]
        self.assertTrue((target / "source.yaml").exists())
        written = yaml.safe_load((target / "source.yaml").read_text(encoding="utf-8"))
        self.assertTrue(written["enabled"])
        self.assertNotIn("{query}", written["request"]["url"])
        self.assertEqual(api.list_candidates_api(self.settings)["candidates"][0]["status"], "accepted")
        with self.assertRaises(ApiError) as cm:
            api.accept_candidate(self.settings, cid)
        self.assertEqual(cm.exception.status, 409)

    def test_reject_marks_and_blocks_regeneration(self):
        api.generate_candidates(self.settings)
        cid = api.list_candidates_api(self.settings)["candidates"][0]["id"]
        api.reject_candidate(self.settings, cid)
        self.assertEqual(api.list_candidates_api(self.settings)["candidates"][0]["status"], "rejected")
        self.assertEqual(api.generate_candidates(self.settings)["added"], 0)

    def test_candidate_not_fetched_until_accepted(self):
        api.generate_candidates(self.settings)
        # 候选只存在于 state.json，sources/ 下不应出现新的源目录
        self.assertFalse(any(p.name.startswith("disc_") for p in (self.root / "sources").iterdir()))

    def test_bad_ids_rejected(self):
        with self.assertRaises(ApiError):
            api.accept_candidate(self.settings, "../etc")
        with self.assertRaises(ApiError):
            api.reject_candidate(self.settings, "disc_nope_000000")

    def test_bad_config_reported(self):
        (self.root / "config" / "discovery.yaml").write_text(
            "templates:\n  - id: site_search\n    name: x\n    source: {request: {url: 'https://e.com/s'}}\n",
            encoding="utf-8")
        settings = load_settings(self.root)
        self.assertIn("config_error", api.list_candidates_api(settings))
        with self.assertRaises(ApiError):
            api.generate_candidates(settings)


class DashboardDiscoveryTests(unittest.TestCase):
    def test_candidate_inbox_renders_and_accept_button_works(self):
        root = make_project({"news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                                        "adapter": "rss", "request": {"url": "https://n/feed"}}})
        write_discovery(root, [_tpl()], daily=3, pending=5)
        settings = load_settings(root)
        for _ in range(2):
            api.record_feedback(settings, {"action": "interested", "source_id": "news_a",
                                           "url": "https://n/1", "title": "量子计算突破"})
        api.generate_candidates(settings)
        import os
        os.environ["INFO_HUB_ROOT"] = str(root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertTrue(any("候选信息源" in e.label for e in at.expander))
        ok = [b for b in at.button if b.key and b.key.startswith("cand_ok_")]
        self.assertTrue(ok)
        ok[0].click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertTrue(any(p.name.startswith("disc_") for p in (root / "sources").iterdir()))


if __name__ == "__main__":
    unittest.main()
