import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from adapters.base import FetchContext
from core import store
from core.normalize import normalize
from core.pipeline import process
from core.runner import run_source
from core.settings import load_settings
from core.sources import load_sources
from adapters.base import RawItem, SourceSpec
from tests.helpers import make_project

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=datetime.now().astimezone().tzinfo)


def spec(kind="feed", **policy):
    return SourceSpec(id="s", name="s", category="news", kind=kind, adapter="rss", enabled=True,
                      request={}, parse={}, policy=policy, schedule={}, browser={}, tags=[],
                      dir=Path("."))


def items_from(sp, titles_urls):
    raws = [RawItem(title=t, url=u, rank=i + 1) for i, (t, u) in enumerate(titles_urls)]
    return [normalize(sp, r, "2026-11-11T12:00:00+08:00") for r in raws]


class PipelineUnitTests(unittest.TestCase):
    def test_first_run_all_new_and_no_rank_changes(self):
        sp = spec(kind="hot_list")
        items = items_from(sp, [("A", "u1"), ("B", "u2")])
        out = process(sp, items, None, "t1", now=NOW)
        self.assertEqual(len(out.new_items), 2)
        self.assertTrue(out.changes["first_snapshot"])

    def test_feed_dedups_against_seen(self):
        sp = spec(kind="feed")
        first = process(sp, items_from(sp, [("A", "u1")]), None, "t1", now=NOW)
        second = process(sp, items_from(sp, [("A", "u1"), ("B", "u2")]), first.state, "t2", now=NOW)
        self.assertEqual([i["title"] for i in second.new_items], ["B"])
        self.assertEqual(second.changes["new"][0]["title"], "B")

    def test_hot_list_rising_falling_new_dropped(self):
        sp = spec(kind="hot_list")
        r1 = process(sp, items_from(sp, [("A", "a"), ("B", "b"), ("C", "c")]), None, "t1", now=NOW)
        # 新顺序：B 上升到第1，A 下降到第2，C 掉出，D 新进
        r2 = process(sp, items_from(sp, [("B", "b"), ("A", "a"), ("D", "d")]), r1.state, "t2", now=NOW)
        self.assertEqual([c["title"] for c in r2.changes["rising"]], ["B"])
        self.assertEqual(r2.changes["rising"][0]["from"], 2)
        self.assertEqual([c["title"] for c in r2.changes["falling"]], ["A"])
        self.assertEqual([c["title"] for c in r2.changes["new"]], ["D"])
        self.assertEqual([d["from"] for d in r2.changes["dropped"]], [3])

    def test_seen_pruned_after_retain_days(self):
        sp = spec(kind="feed", retain_days=2)
        old = (NOW - timedelta(days=5)).isoformat()
        state = {"source_id": "s", "seen": {"x": old}, "last_snapshot": None}
        out = process(sp, items_from(sp, [("A", "u1")]), state, "t", now=NOW)
        self.assertNotIn("x", out.state["seen"])


class RunnerIntegrationTests(unittest.TestCase):
    RSS_1 = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>A</title><link>https://n/a</link></item><item><title>B</title><link>https://n/b</link></item>
</channel></rss>"""
    RSS_2 = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>A</title><link>https://n/a</link></item><item><title>B</title><link>https://n/b</link></item>
<item><title>C</title><link>https://n/c</link></item>
</channel></rss>"""

    def test_second_run_only_writes_new_items(self):
        cfg = {"id": "news_a", "name": "n", "category": "news", "kind": "feed", "adapter": "rss",
               "request": {"url": "https://n/feed"}}
        root = make_project({"news_a": cfg})
        settings = load_settings(root)
        spec_ = load_sources(settings).specs["news_a"]
        run_source(spec_, settings, FetchContext(http_get=lambda u, **k: self.RSS_1))
        res2 = run_source(spec_, settings, FetchContext(http_get=lambda u, **k: self.RSS_2))
        self.assertEqual(res2.item_count, 3)
        self.assertEqual(res2.new_count, 1)
        items_files = sorted((settings.paths.items_dir / "news_a").glob("*.jsonl"))
        all_items = [i for f in items_files for i in store.read_jsonl(f)]
        self.assertEqual(sorted(i["title"] for i in all_items), ["A", "B", "C"])  # 没有重复写入
        state = store.read_json(settings.paths.state_dir / "news_a.json")
        self.assertEqual(len(state["seen"]), 3)


if __name__ == "__main__":
    unittest.main()
