"""第四阶段：曝光/打开/反馈指标、模板采纳率与自动暂停、建议文案、API 与看板渲染。"""
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from streamlit.testing.v1 import AppTest

from core import evaluation, feedback
from core.settings import load_settings
from server import api
from server.api import ApiError
from tests.helpers import make_project
from tests.test_discovery import _tpl, write_discovery

APP = str(Path(__file__).resolve().parents[1] / "app.py")
NOW = datetime(2026, 11, 12, 12, 0).astimezone()


def _t(days=0):
    return (NOW - timedelta(days=days)).isoformat(timespec="seconds")


_SEQ = iter(range(10**6))


def _cand(tid, status, reason="显式关注主题"):
    n = next(_SEQ)
    return {"id": f"disc_{tid}_{n:06x}", "template_id": tid,
            "template_name": tid, "status": status, "reason": reason}


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.data = self.root / "data"

    def _fb(self, action, key, view, days=0):
        from core.store import append_jsonl
        append_jsonl(feedback.feedback_path(self.data),
                     {"id": "x", "time": _t(days), "action": action, "source_id": "s", "item_key": key,
                      "title": "t", "url": "", "reasons": [], "note": "", "open_mode": "", "view": view})

    def test_ctr_and_feedback_rates_per_view(self):
        evaluation.record_impression(self.data, source_id="s", snapshot_id="a", view="ranked",
                                     item_keys=["k1", "k2", "k3", "k4"], now=NOW)
        evaluation.record_impression(self.data, source_id="s", snapshot_id="a", view="plain",
                                     item_keys=["k1", "k2", "k3", "k4"], now=NOW)
        evaluation.record_open(self.data, source_id="s", item_key="k1", view="ranked", mode="reader", now=NOW)
        evaluation.record_open(self.data, source_id="s", item_key="k2", view="ranked", mode="reader", now=NOW)
        evaluation.record_open(self.data, source_id="s", item_key="k3", view="plain", mode="reader", now=NOW)
        self._fb("interested", "k1", "ranked")
        self._fb("not_interested", "k4", "ranked")
        self._fb("not_interested", "k4", "plain")
        out = evaluation.compute_metrics(self.data, [], now=NOW)
        r, p = out["views"]["ranked"], out["views"]["plain"]
        self.assertEqual(r["impressions"], 4)
        self.assertEqual(r["ctr"], 0.5)   # 2 / 4
        self.assertEqual(p["ctr"], 0.25)  # 1 / 4
        self.assertEqual(r["interested_rate"], 0.5)
        self.assertEqual(r["not_interested_rate"], 0.5)
        self.assertEqual(p["not_interested_rate"], 1.0)

    def test_old_events_outside_window_ignored(self):
        self._fb("interested", "k9", "ranked", days=90)
        out = evaluation.compute_metrics(self.data, [], days=30, now=NOW)
        self.assertEqual(out["views"]["ranked"]["feedback"], 0)
        self.assertIsNone(out["views"]["ranked"]["interested_rate"])

    def test_opens_on_unshown_items_not_counted(self):
        evaluation.record_impression(self.data, source_id="s", snapshot_id="a", view="plain",
                                     item_keys=["k1"], now=NOW)
        evaluation.record_open(self.data, source_id="s", item_key="zzz", view="plain", mode="reader", now=NOW)
        self.assertEqual(evaluation.compute_metrics(self.data, [], now=NOW)["views"]["plain"]["opened_items"], 0)

    def test_invalid_view_rejected(self):
        with self.assertRaises(ValueError):
            evaluation.record_impression(self.data, source_id="s", snapshot_id="a", view="bogus",
                                         item_keys=["k1"], now=NOW)

    def test_feedback_view_validated(self):
        e = feedback.validate_event({"action": "later", "source_id": "s", "url": "https://n/1", "view": "weird"})
        self.assertEqual(e["view"], "")
        e = feedback.validate_event({"action": "later", "source_id": "s", "url": "https://n/1", "view": "ranked"})
        self.assertEqual(e["view"], "ranked")


class AdoptionTests(unittest.TestCase):
    def test_adoption_rate_by_template(self):
        cands = [_cand("a1", "accepted"), _cand("a1", "rejected"), _cand("a1", "rejected"), _cand("b2", "pending")]
        t = evaluation.template_adoption(cands)
        self.assertEqual(t["a1"]["adoption_rate"], round(1 / 3, 3))
        self.assertIsNone(t["b2"]["adoption_rate"])

    def test_pause_only_with_enough_decisions_and_low_rate(self):
        few = [_cand("a1", "rejected") for _ in range(4)]
        self.assertEqual(evaluation.paused_templates(few), {})
        low = [_cand("a1", "rejected") for _ in range(5)]
        self.assertIn("a1", evaluation.paused_templates(low))
        ok = [_cand("b2", "accepted") for _ in range(3)] + [_cand("b2", "rejected") for _ in range(2)]
        self.assertEqual(evaluation.paused_templates(ok), {})  # 60% ≥ 20%

    def test_min_rate_zero_disables_pausing(self):
        low = [_cand("a1", "rejected") for _ in range(10)]
        self.assertEqual(evaluation.paused_templates(low, min_rate=0.0), {})

    def test_recommendations_cover_sparse_data_and_paused(self):
        m = evaluation.compute_metrics(make_project({}) / "data", [_cand("a1", "rejected") for _ in range(5)],
                                       now=NOW)
        recs = evaluation.recommendations(m)
        self.assertTrue(any("样本不足" in r for r in recs))
        self.assertTrue(any("暂停" in r for r in recs))


class ApiEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                                             "adapter": "rss", "request": {"url": "https://n/feed"}}})
        write_discovery(self.root, [_tpl()], daily=5, pending=10)
        self.settings = load_settings(self.root)
        for _ in range(2):
            api.record_feedback(self.settings, {"action": "interested", "source_id": "news_a",
                                                "url": "https://n/1", "title": "量子计算突破"})

    def test_impression_and_open_validation(self):
        key = feedback.item_key("https://n/1", "甲")
        api.record_impression_api(self.settings, {"source_id": "news_a", "snapshot_id": "s1",
                                                  "view": "plain", "item_keys": [key]})
        api.record_open_api(self.settings, {"source_id": "news_a", "item_key": key, "view": "plain",
                                            "mode": "reader"})
        with self.assertRaises(ApiError):
            api.record_impression_api(self.settings, {"source_id": "news_a", "snapshot_id": "s1",
                                                      "view": "plain", "item_keys": ["bad key"]})
        with self.assertRaises(ApiError):
            api.record_open_api(self.settings, {"source_id": "../x", "item_key": key})

    def test_paused_template_skipped_on_generation(self):
        data = self.root / "data"
        # 人为造出 5 次忽略的历史候选，使模板 site_search 采纳率为 0
        from core import discovery
        from core.store import atomic_write_json
        state = {"candidates": {f"disc_site_search_{i:06x}": {
            "id": f"disc_site_search_{i:06x}", "template_id": "site_search", "template_name": "站内搜索",
            "status": "rejected", "reason": "显式关注主题", "created_at": _t(10), "query": f"q{i}",
            "url": "https://e/", "source": {}} for i in range(5)}}
        atomic_write_json(discovery.state_path(data), state)
        out = api.generate_candidates(self.settings)
        self.assertEqual(out["added"], 0)
        self.assertIn("paused_low_adoption", out["skipped"])

    def test_evaluation_report_shape(self):
        out = api.evaluation_report(self.settings, 30)
        self.assertIn("views", out)
        self.assertIn("recommendations", out)
        self.assertIn("ranked", out["views"])
        self.assertIsInstance(out["recommendations"], list)


class DashboardEvaluationTests(unittest.TestCase):
    def test_panel_renders_and_plain_impression_logged(self):
        root = make_project({"news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                                        "adapter": "rss", "request": {"url": "https://n/feed"}}})
        from adapters.base import FetchContext
        from core.runner import run_source
        from core.sources import load_sources
        settings = load_settings(root)
        rss = ('<?xml version="1.0"?><rss version="2.0"><channel>'
               '<item><title>看板甲</title><link>https://n/1</link></item></channel></rss>').encode()
        run_source(load_sources(settings).specs["news_a"], settings, FetchContext(http_get=lambda u, **k: rss))
        os.environ["INFO_HUB_ROOT"] = str(root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertTrue(any("推荐效果评估" in e.label for e in at.expander))
        exp = list(evaluation.exposure_path(root / "data").read_text(encoding="utf-8").splitlines()) \
            if evaluation.exposure_path(root / "data").exists() else []
        self.assertTrue(any('"view": "plain"' in line for line in exp))


if __name__ == "__main__":
    unittest.main()
