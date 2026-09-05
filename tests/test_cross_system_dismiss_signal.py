"""tests/test_cross_system_dismiss_signal.py

[next_doc/personal_assistant_experience_improvement_directions.md
第 4 节，跨系统"不感兴趣"信号——只读标注，不改变候选生成/排序]

覆盖：
  1. 空状态不报错
  2. 单次 dismiss 不计入信号（未达 min_count）
  3. 达到 min_count 次 dismiss 才纳入信号来源
  4. `find_cross_system_match` 只匹配"另一个系统"的信号，不匹配同系统
  5. 相似度低于阈值不返回匹配
  6. `initiative_inbox_snapshot` 集成：跨系统相似标题被正确标注；
     `annotate_cross_dismiss=False` 时不标注；异常隔离不影响收件箱
     其余部分
"""

from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from mini_agent.evolution.capability_learning import (
    CapabilityOutlineSuggestionStore,
    OutlineSuggestion,
)
from mini_agent.evolution.growth_advisor import GrowthBacklog
from mini_agent.perception import cross_system_dismiss_signal as cds
from mini_agent.perception.initiative_inbox import initiative_inbox_snapshot
from mini_agent.storage.paths import AgentPaths


def _dismiss_growth_candidate(paths, title: str, times: int = 1):
    backlog = GrowthBacklog(paths)
    for _ in range(times):
        c = backlog.add_or_merge(
            title=title, rationale="r", evidence_refs=["e1", "e2", "e3"],
            min_evidence_count=1, max_pending=10, dismissed_cooldown_days=0,
        )
        backlog.set_status(c.candidate_id, "dismissed")


class TestLoadSignals(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_state_returns_empty_list(self):
        self.assertEqual(cds.load_cross_system_dismiss_signals(self.paths), [])

    def test_single_dismiss_below_min_count_excluded(self):
        _dismiss_growth_candidate(self.paths, "股票热点分析", times=1)
        signals = cds.load_cross_system_dismiss_signals(self.paths, min_count=2)
        self.assertEqual(signals, [])

    def test_repeated_dismiss_meets_min_count(self):
        _dismiss_growth_candidate(self.paths, "股票热点分析", times=2)
        signals = cds.load_cross_system_dismiss_signals(self.paths, min_count=2)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].source_system, "growth_advisor")
        self.assertEqual(signals[0].count, 2)

    def test_capability_dismissed_suggestions_counted(self):
        store = CapabilityOutlineSuggestionStore(self.paths)
        for i in range(2):
            s = OutlineSuggestion(
                suggestion_id=f"s{i}", track_id="t1", source_question_id="q1",
                suggested_name="小众编程语言语法",
            )
            store.add(s)
            store.dismiss(s.suggestion_id)
        signals = cds.load_cross_system_dismiss_signals(self.paths, min_count=2)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].source_system, "capability_learning")


class TestFindCrossSystemMatch(unittest.TestCase):
    def test_only_matches_other_system(self):
        sig = cds.DismissSignal(text="股票热点分析", count=3, source_system="growth_advisor",
                                 tokens=cds._tokens("股票热点分析"))
        # 同系统不匹配，即便标题完全相同
        self.assertIsNone(cds.find_cross_system_match("股票热点分析", "growth_advisor", [sig]))
        # 跨系统匹配
        match = cds.find_cross_system_match("股票热点分析相关工具", "capability_learning", [sig])
        self.assertIsNotNone(match)
        score, matched_sig = match
        self.assertGreater(score, 0.0)
        self.assertIs(matched_sig, sig)

    def test_below_similarity_threshold_returns_none(self):
        sig = cds.DismissSignal(text="股票热点分析", count=3, source_system="growth_advisor",
                                 tokens=cds._tokens("股票热点分析"))
        match = cds.find_cross_system_match("完全不相关的主题", "capability_learning", [sig],
                                             min_similarity=0.5)
        self.assertIsNone(match)


class TestInitiativeInboxIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_cross_dismiss_annotation_on_matching_item(self):
        _dismiss_growth_candidate(self.paths, "股票热点分析", times=2)

        store = CapabilityOutlineSuggestionStore(self.paths)
        store.add(OutlineSuggestion(
            suggestion_id="s1", track_id="t1", source_question_id="q1",
            suggested_name="股票热点分析工具",
        ))

        snap = initiative_inbox_snapshot(self.paths, annotate_cross_dismiss=True)
        items = [it for it in snap["items"] if it["native_id"] == "s1"]
        self.assertEqual(len(items), 1)
        self.assertIsNotNone(items[0].get("cross_dismiss_similarity"))
        self.assertEqual(items[0]["cross_dismiss_source_system"], "growth_advisor")

    def test_annotate_cross_dismiss_false_skips_annotation(self):
        _dismiss_growth_candidate(self.paths, "股票热点分析", times=2)
        store = CapabilityOutlineSuggestionStore(self.paths)
        store.add(OutlineSuggestion(
            suggestion_id="s1", track_id="t1", source_question_id="q1",
            suggested_name="股票热点分析工具",
        ))
        snap = initiative_inbox_snapshot(self.paths, annotate_cross_dismiss=False)
        items = [it for it in snap["items"] if it["native_id"] == "s1"]
        self.assertNotIn("cross_dismiss_similarity", items[0])

    def test_annotation_failure_does_not_break_snapshot(self):
        store = CapabilityOutlineSuggestionStore(self.paths)
        store.add(OutlineSuggestion(
            suggestion_id="s1", track_id="t1", source_question_id="q1",
            suggested_name="随便一个建议",
        ))
        with unittest.mock.patch(
            "mini_agent.perception.cross_system_dismiss_signal.load_cross_system_dismiss_signals",
            side_effect=RuntimeError("boom"),
        ):
            snap = initiative_inbox_snapshot(self.paths, annotate_cross_dismiss=True)
        self.assertEqual(snap["total"], 1)


if __name__ == "__main__":
    unittest.main()
