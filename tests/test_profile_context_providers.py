"""tests/test_profile_context_providers.py

覆盖 next_doc/profile_context_sources_completeness_plan.md：

  - `external_input/watchlist.py::build_watchlist_profile_snapshot()`
    （方向一）。
  - `wiki/stats.py::build_wiki_recent_updates_snapshot()`（方向 C，
    第一步）。回归测试：该函数此前依赖 `discover_pages()`，但
    `discover_pages()` 根本不扫描 `research/`/`growth/` 目录，导致
    这个函数在实际使用中永远返回空串——本次改为直接扫描
    `wiki_research_dir`/`wiki_growth_dir`，这里验证修复后确实能拿到
    条目。
  - `profile.py` 里的 `_profile_context_preferences` /
    `_profile_context_growth_focus` 两个 provider，以及
    `_collect_profile_context_blocks()` 的整体拼接行为（方向 D / A /
    E）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.external_input.watchlist import build_watchlist_profile_snapshot
from mini_agent.profile import (
    UserProfile,
    UserProfileManager,
    _collect_profile_context_blocks,
    _profile_context_growth_focus,
    _profile_context_preferences,
)
from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.stats import build_wiki_recent_updates_snapshot


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestWatchlistProfileSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_config_returns_empty(self):
        self.assertEqual(build_watchlist_profile_snapshot(self.paths), "")

    def test_enabled_items_included_disabled_excluded(self):
        _write(self.paths.external_input_watchlist_config, """
watchlist:
  - id: ai_watch
    keywords: ["Agent", "LLM"]
    report_tier: minute_1
    enabled: true
  - id: sports
    keywords: ["篮球"]
    report_tier: minute_1
    enabled: false
""")
        snapshot = build_watchlist_profile_snapshot(self.paths)
        self.assertIn("ai_watch", snapshot)
        self.assertIn("Agent", snapshot)
        self.assertNotIn("sports", snapshot)

    def test_max_items_limits_output(self):
        entries = "\n".join(
            f"  - id: topic_{i}\n    keywords: [\"kw{i}\"]\n    report_tier: minute_1"
            for i in range(15)
        )
        _write(self.paths.external_input_watchlist_config, f"watchlist:\n{entries}\n")
        snapshot = build_watchlist_profile_snapshot(self.paths, max_items=3)
        self.assertEqual(snapshot.count("\n- ["), 3)


class TestWikiRecentUpdatesSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _page(self, page_id: str, updated: str) -> str:
        return f"""---
id: {page_id}
type: topic
updated: {updated}
---

正文内容。
"""

    def test_empty_wiki_returns_empty_string(self):
        self.assertEqual(build_wiki_recent_updates_snapshot(self.paths), "")

    def test_research_and_growth_pages_included(self):
        """回归测试：修复前 discover_pages() 不扫描 research/growth，
        这里必须能拿到两边命名空间各自的条目。"""
        _write(
            self.paths.wiki_research_dir / "agent_and_ai.md",
            self._page("agent_and_ai", "2026-01-01"),
        )
        _write(
            self.paths.wiki_growth_dir / "photography.md",
            self._page("photography", "2026-01-02"),
        )
        snapshot = build_wiki_recent_updates_snapshot(self.paths)
        self.assertIn("agent_and_ai", snapshot)
        self.assertIn("photography", snapshot)

    def test_other_namespaces_excluded(self):
        """entities/decisions 等其它命名空间不应该被这个函数扫到——
        它扫的是 discover_pages() 完全不覆盖的 research/growth 两个
        目录，不该反过来把通用命名空间也纳入。"""
        _write(
            self.paths.wiki_entities_dir / "some_entity.md",
            self._page("some_entity", "2026-01-03"),
        )
        snapshot = build_wiki_recent_updates_snapshot(self.paths)
        self.assertEqual(snapshot, "")

    def test_sorted_most_recently_updated_first(self):
        _write(
            self.paths.wiki_research_dir / "old.md", self._page("old_topic", "2020-01-01")
        )
        _write(
            self.paths.wiki_research_dir / "new.md", self._page("new_topic", "2026-01-01")
        )
        snapshot = build_wiki_recent_updates_snapshot(self.paths)
        self.assertLess(snapshot.index("new_topic"), snapshot.index("old_topic"))

    def test_max_items_limits_output(self):
        for i in range(12):
            _write(
                self.paths.wiki_research_dir / f"t{i}.md",
                self._page(f"topic_{i}", f"2026-01-{i + 1:02d}"),
            )
        snapshot = build_wiki_recent_updates_snapshot(self.paths, max_items=4)
        self.assertEqual(snapshot.count("\n- "), 4)


class TestPreferencesProvider(unittest.TestCase):
    def test_empty_preferences_returns_empty_string(self):
        profile = UserProfile(user_id="default")
        self.assertEqual(_profile_context_preferences(None, profile), "")

    def test_preferences_rendered_as_ground_truth(self):
        profile = UserProfile(user_id="default")
        profile.preferences["reply_style"] = "简洁的结构化摘要"
        block = _profile_context_preferences(None, profile)
        self.assertIn("reply_style: 简洁的结构化摘要", block)
        self.assertIn("ground truth", block)


class TestGrowthFocusProvider(unittest.TestCase):
    def test_no_growth_focus_returns_empty_string(self):
        profile = UserProfile(user_id="default")
        self.assertEqual(_profile_context_growth_focus(None, profile), "")

    def test_topics_ranked_by_hit_count_and_capped_at_eight(self):
        profile = UserProfile(user_id="default")
        profile.derived["growth_focus_areas"] = {
            f"topic_{i}": ["e"] * (i + 1) for i in range(10)
        }
        block = _profile_context_growth_focus(None, profile)
        # 命中数最高的 topic_9 应该排在最前面。
        self.assertIn("topic_9", block)
        self.assertEqual(block.count("\n- topic_"), 8)


class TestCollectProfileContextBlocks(unittest.TestCase):
    """方向 E：统一注册表整体拼接行为——一个信息源异常不影响其它，
    全部为空时返回空串。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_all_empty_returns_empty_string(self):
        profile = UserProfile(user_id="default")
        self.assertEqual(_collect_profile_context_blocks(self.paths, profile), "")

    def test_preferences_included_when_other_sources_empty(self):
        profile = UserProfile(user_id="default")
        profile.preferences["tone"] = "直接一点"
        blocks = _collect_profile_context_blocks(self.paths, profile)
        self.assertIn("tone: 直接一点", blocks)

    def test_manager_generate_uses_registry(self):
        """确认 UserProfileManager.set_preference() 写入的数据能一路
        流到 _collect_profile_context_blocks() 里，不需要额外接线。"""
        mgr = UserProfileManager(self.paths)
        mgr.set_preference("语气", "轻松幽默")
        profile = mgr.load()
        blocks = _collect_profile_context_blocks(self.paths, profile)
        self.assertIn("语气: 轻松幽默", blocks)


if __name__ == "__main__":
    unittest.main()
