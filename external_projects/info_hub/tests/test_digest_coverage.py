"""报告覆盖：每个启用的源都出现（含无新增、失败、暂停），截断与失败原因可见。"""
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core import store
from core.digest import build_digest, render_markdown
from core.settings import load_settings
from tests.helpers import make_project
from tests.test_digest_and_tick import FEED, HOT

TZ = datetime.now().astimezone().tzinfo
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=TZ)
QUIET = {"id": "quiet_c", "name": "安静订阅源", "category": "news", "kind": "feed", "adapter": "rss",
         "request": {"url": "https://x/q"}, "schedule": {"every": "1h"}}
PAUSED = {"id": "paused_d", "name": "已暂停源", "category": "news", "kind": "feed", "adapter": "rss",
          "request": {"url": "https://x/p"}, "schedule": {"every": "1h"}}


def add_item(root, sid, category, n, minutes_ago=5):
    ts = (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    for i in range(n):
        item = {"id": f"{sid}:{i}", "source_id": sid, "category": category, "title": f"{sid}-{i}",
                "url": f"https://x/{sid}/{i}", "summary": "", "rank": i + 1, "heat": None,
                "published_at": None, "fetched_at": ts, "extra": {}}
        store.append_jsonl(root / "data" / "items" / sid / f"{NOW:%Y-%m-%d}.jsonl", item)


class DigestCoverageTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"hot_a": HOT, "feed_b": FEED, "quiet_c": QUIET, "paused_d": PAUSED})
        self.settings = load_settings(self.root)
        self.plan = {"id": "rolling", "window": "3h", "categories": "all"}

    def _digest(self):
        return build_digest(self.settings, self.plan, NOW)

    def _block(self, digest, sid):
        for sec in digest["sections"]:
            for b in sec["sources"]:
                if b["source_id"] == sid:
                    return b
        return None

    def test_source_without_new_items_still_listed(self):
        add_item(self.root, "feed_b", "news", 1)
        d = self._digest()
        b = self._block(d, "quiet_c")
        self.assertIsNotNone(b, "无新增的启用源必须出现在报告中")
        self.assertEqual(b["status"], "never")  # 从未抓取过
        md = render_markdown(d, "t")
        self.assertIn("安静订阅源", md)

    def test_failed_source_shows_reason(self):
        from core import runner
        with_error = self.root / "data" / "runs" / f"{NOW:%Y-%m-%d}.log.md"
        with_error.parent.mkdir(parents=True, exist_ok=True)
        with_error.write_text(f"- {NOW:%H:%M:%S} `quiet_c` 失败 | 错误：HTTP 503 | 耗时 1.0s\n", encoding="utf-8")
        d = self._digest()
        b = self._block(d, "quiet_c")
        self.assertEqual(b["status"], "failed")
        self.assertIn("HTTP 503", b["status_text"])
        self.assertIn("抓取失败", render_markdown(d, "t"))

    def test_paused_source_marked(self):
        from core import health
        from dataclasses import replace
        self.settings = replace(self.settings, raw={**(self.settings.raw or {}),
                                                    "health": {"enabled": True, "pause_after": 3}})
        for _ in range(3):
            health.record_result(self.settings, "paused_d", False, "boom")
        d = self._digest()
        self.assertEqual(self._block(d, "paused_d")["status"], "paused")
        self.assertGreaterEqual(d["summary"]["paused_count"], 1)

    def test_truncation_is_reported(self):
        add_item(self.root, "feed_b", "news", 25)
        d = self._digest()
        b = self._block(d, "feed_b")
        self.assertEqual(b["new_count"], 25)
        self.assertEqual(len(b["items"]), 20)
        self.assertEqual(b["omitted"], 5)
        self.assertIn("另有 5 条未展示", render_markdown(d, "t"))
        self.assertEqual(d["summary"]["omitted_items"], 5)

    def test_summary_counts_cover_all_enabled_sources(self):
        add_item(self.root, "feed_b", "news", 1)
        d = self._digest()
        s = d["summary"]
        self.assertEqual(s["source_count"], 4)
        self.assertEqual(s["active_source_count"], 1)


if __name__ == "__main__":
    unittest.main()
