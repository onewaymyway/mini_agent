"""tests/test_watchlist_matcher.py — WatchlistMatcher（P2）测试

覆盖：
  1. load_watchlist_config：文件缺失返回空列表；单条缺字段跳过、其余照常加载
  2. WatchlistItem.matches：关键词命中（大小写不敏感）、source_channels 过滤、
     禁用条目不匹配
  3. run_watchlist_matcher_once：命中写入 pending_hits.jsonl，tier 取自
     watchlist 项；不命中的事件不写入；游标正确推进（第二次调用不重复处理）
  4. 去重：同一 (watchlist_id, 归一化标题) 在 dedup_window_seconds 内只写一次
  5. watchlist 为空：仍然推进游标，避免以后配置了 watchlist 之后突然
     "回放"历史事件
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.external_input.gateway import publish_event
from mini_agent.external_input.source import ExternalInputEvent
from mini_agent.external_input.watchlist import (
    WatchlistItem,
    load_watchlist_config,
    run_watchlist_matcher_once,
)
from mini_agent.storage.paths import AgentPaths


def _write_yaml(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestWatchlistItem(unittest.TestCase):
    def test_matches_keyword_case_insensitive(self):
        item = WatchlistItem(id="x", keywords=["CompetitorA"], report_tier="minute_1")
        evt = ExternalInputEvent(id="1", source_id="rss1", source_type="rss", signal="new_item",
                                  title="competitora release announced", channel="rss")
        self.assertTrue(item.matches(evt))

    def test_disabled_item_never_matches(self):
        item = WatchlistItem(id="x", keywords=["foo"], report_tier="minute_1", enabled=False)
        evt = ExternalInputEvent(id="1", source_id="rss1", source_type="rss", signal="new_item", title="foo bar")
        self.assertFalse(item.matches(evt))

    def test_source_channels_filter(self):
        item = WatchlistItem(id="x", keywords=["foo"], report_tier="minute_1", source_channels=["weather"])
        evt = ExternalInputEvent(id="1", source_id="rss1", source_type="rss", signal="new_item",
                                  title="foo bar", channel="rss")
        self.assertFalse(item.matches(evt))


class TestLoadWatchlistConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_returns_empty(self):
        self.assertEqual(load_watchlist_config(self.paths), [])

    def test_entry_missing_report_tier_is_skipped(self):
        _write_yaml(self.paths.external_input_watchlist_config, """
watchlist:
  - id: bad
    keywords: ["x"]
  - id: good
    keywords: ["y"]
    report_tier: minute_1
""")
        items = load_watchlist_config(self.paths)
        self.assertEqual([i.id for i in items], ["good"])


class TestRunWatchlistMatcherOnce(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))
        _write_yaml(self.paths.external_input_watchlist_config, """
watchlist:
  - id: competitor_launch
    keywords: ["CompetitorA"]
    report_tier: minute_1
    enabled: true
""")

    def tearDown(self):
        self._tmp.cleanup()

    def _publish(self, event_id, title, source_id=None):
        publish_event(self.paths, ExternalInputEvent(
            id=event_id, source_id=source_id or f"rss-{self._testMethodName}", source_type="rss",
            signal="new_item", title=title, channel="rss",
        ))

    def test_hit_written_with_correct_tier(self):
        self._publish("e1", "CompetitorA released a new product")
        summary = run_watchlist_matcher_once(self.paths)
        self.assertEqual(summary.matched, 1)
        self.assertEqual(summary.written, 1)
        lines = self.paths.external_input_pending_hits.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        import json
        rec = json.loads(lines[0])
        self.assertEqual(rec["tier"], "minute_1")
        self.assertEqual(rec["watchlist_id"], "competitor_launch")
        self.assertFalse(rec["consumed"])

    def test_non_matching_event_not_written(self):
        self._publish("e1", "totally unrelated news")
        summary = run_watchlist_matcher_once(self.paths)
        self.assertEqual(summary.matched, 0)
        self.assertFalse(self.paths.external_input_pending_hits.exists())

    def test_cursor_advances_no_reprocessing(self):
        self._publish("e1", "CompetitorA released")
        run_watchlist_matcher_once(self.paths)
        summary2 = run_watchlist_matcher_once(self.paths)
        self.assertEqual(summary2.scanned, 0)

    def test_dedup_within_window(self):
        self._publish("e1", "CompetitorA released v1")
        self._publish("e2", "CompetitorA released v1")  # 同一归一化标题
        summary = run_watchlist_matcher_once(self.paths)
        self.assertEqual(summary.matched, 2)
        self.assertEqual(summary.written, 1)
        self.assertEqual(summary.deduped, 1)

    def test_empty_watchlist_still_advances_cursor(self):
        _write_yaml(self.paths.external_input_watchlist_config, "watchlist: []\n")
        self._publish("e1", "CompetitorA released")
        run_watchlist_matcher_once(self.paths)
        # 之后补上真正的 watchlist 配置，历史事件不应该被回放命中
        _write_yaml(self.paths.external_input_watchlist_config, """
watchlist:
  - id: competitor_launch
    keywords: ["CompetitorA"]
    report_tier: minute_1
""")
        summary = run_watchlist_matcher_once(self.paths)
        self.assertEqual(summary.scanned, 0)
        self.assertEqual(summary.written, 0)


if __name__ == "__main__":
    unittest.main()
