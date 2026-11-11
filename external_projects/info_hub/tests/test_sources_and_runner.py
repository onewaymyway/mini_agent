import json
import shutil
import unittest
from pathlib import Path

from adapters.base import FetchContext
from core.runner import run_source
from core.settings import load_settings
from core.sources import load_sources
from core import store
from tests.helpers import make_project

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>新闻A</title><link>https://n.example/a</link></item>
<item><title>新闻B</title><link>https://n.example/b</link></item>
</channel></rss>""".encode("utf-8")

GOOD = {"id": "news_a", "name": "新闻源A", "category": "news", "kind": "feed",
        "adapter": "rss", "request": {"url": "https://n.example/feed"}, "policy": {"max_items": 10}}


class SourceLoadTests(unittest.TestCase):
    def test_good_source_loads(self):
        root = make_project({"news_a": GOOD})
        loaded = load_sources(load_settings(root))
        self.assertIn("news_a", loaded.specs)
        self.assertEqual(loaded.errors, {})

    def test_unknown_category_is_reported(self):
        bad = dict(GOOD, id="bad", category="nope")
        root = make_project({"bad": bad})
        loaded = load_sources(load_settings(root))
        self.assertNotIn("bad", loaded.specs)
        self.assertTrue(any("分类" in e for e in loaded.errors["bad"]))

    def test_missing_required_field_and_unknown_adapter(self):
        bad = {"id": "x", "name": "x", "category": "news", "kind": "feed", "adapter": "ghost"}
        root = make_project({"x": bad})
        loaded = load_sources(load_settings(root))
        self.assertTrue(any("ghost" in e for e in loaded.errors["x"]))

    def test_one_bad_source_does_not_block_others(self):
        root = make_project({"news_a": GOOD, "bad": dict(GOOD, id="bad", kind="weird")})
        loaded = load_sources(load_settings(root))
        self.assertIn("news_a", loaded.specs)
        self.assertIn("bad", loaded.errors)


class RunnerTests(unittest.TestCase):
    def test_run_writes_snapshot_items_and_log(self):
        root = make_project({"news_a": GOOD})
        settings = load_settings(root)
        spec = load_sources(settings).specs["news_a"]
        ctx = FetchContext(http_get=lambda url, **kw: RSS)
        res = run_source(spec, settings, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.item_count, 2)
        self.assertEqual(res.new_count, 2)

        snapshots = list((settings.paths.raw_dir / "news_a").rglob("*.json"))
        self.assertEqual(len(snapshots), 1)
        snap = json.loads(snapshots[0].read_text(encoding="utf-8"))
        self.assertEqual(snap["item_count"], 2)
        self.assertEqual(snap["items"][0]["title"], "新闻A")

        item_files = list((settings.paths.items_dir / "news_a").glob("*.jsonl"))
        self.assertEqual(len(item_files), 1)
        self.assertEqual(len(list(store.read_jsonl(item_files[0]))), 2)

        logs = list(settings.paths.runs_dir.glob("*.log.md"))
        self.assertIn("news_a", logs[0].read_text(encoding="utf-8"))

    def test_failed_fetch_writes_no_items_but_logs_error(self):
        root = make_project({"news_a": GOOD})
        settings = load_settings(root)
        spec = load_sources(settings).specs["news_a"]

        def boom(url, **kw):
            raise OSError("timeout")
        res = run_source(spec, settings, FetchContext(http_get=boom))
        self.assertFalse(res.ok)
        self.assertFalse((settings.paths.items_dir / "news_a").exists())
        log_text = next(settings.paths.runs_dir.glob("*.log.md")).read_text(encoding="utf-8")
        self.assertIn("失败", log_text)


if __name__ == "__main__":
    unittest.main()
