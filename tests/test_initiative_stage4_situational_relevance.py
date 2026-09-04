"""[next_doc/initiative_systems_unification_plan.md 阶段四] 单元测试：

1. perception/situational_relevance.py —— 相关度打分（Jaccard 规则），
   空处境/空文本返回 0 分，命中处境信号时返回更高分数并能定位到具体
   信号。
2. initiative_inbox_snapshot(annotate_relevance=...) —— 只读标注，
   默认开启但不改变既有排序/字段，关闭时完全等价于阶段四之前的行为。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.growth_advisor import GrowthBacklog
from mini_agent.perception import situational_relevance as sr
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.perception.initiative_inbox import initiative_inbox_snapshot
from mini_agent.perception.workdir_knowledge import WorkThread, upsert_work_thread
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class TestSituationalRelevance(unittest.TestCase):
    def test_empty_context_returns_zero(self):
        ctx = sr.SituationalContext(signals=[])
        score, best = sr.score_relevance("随便什么标题", ctx)
        self.assertEqual(score, 0.0)
        self.assertIsNone(best)

    def test_empty_text_returns_zero(self):
        ctx = sr.SituationalContext(signals=[
            sr.SituationalSignal(kind="goal", signal_id="g1", title="学做饭", tokens=sr._tokens("学做饭")),
        ])
        score, best = sr.score_relevance("", ctx)
        self.assertEqual(score, 0.0)
        self.assertIsNone(best)

    def test_matching_signal_scores_higher_than_unrelated(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            upsert_work_thread(paths, WorkThread(
                id="wt1", title="学习股票技术分析", status="active",
                cumulative_progress="已经看完均线基础", next_suggested="继续研究MACD指标",
            ))
            ctx = sr.load_situational_context(paths)
            self.assertFalse(ctx.is_empty)

            related_score, related_signal = sr.score_relevance("股票技术分析 MACD指标详解", ctx)
            unrelated_score, unrelated_signal = sr.score_relevance("如何腌制泡菜", ctx)

            self.assertGreater(related_score, unrelated_score)
            self.assertIsNotNone(related_signal)
            self.assertEqual(related_signal.signal_id, "wt1")

    def test_active_goal_counts_as_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="学习Python数据分析", description="用pandas处理数据", source="user")

            ctx = sr.load_situational_context(paths)
            self.assertFalse(ctx.is_empty)
            score, best = sr.score_relevance("Python数据分析 pandas实战", ctx)
            self.assertGreater(score, 0.0)
            self.assertEqual(best.signal_id, goal.id)

    def test_non_active_goal_is_not_a_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="已完成的目标", description="x", source="user")
            backlog.load()
            backlog._nodes[goal.id].status = "done"
            backlog.save()

            ctx = sr.load_situational_context(paths)
            self.assertTrue(ctx.is_empty)


class TestInitiativeInboxRelevanceAnnotation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = _make_paths(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_annotate_relevance_default_on_with_no_context_leaves_fields_none(self):
        backlog = GrowthBacklog(self.paths)
        backlog.add_or_merge(
            title="学做饭", rationale="r", evidence_refs=["e1", "e2", "e3"],
            min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
        )
        snap = initiative_inbox_snapshot(self.paths)
        self.assertEqual(len(snap["items"]), 1)
        self.assertNotIn("situational_relevance", snap["items"][0])

    def test_annotate_relevance_attaches_score_when_context_present(self):
        upsert_work_thread(self.paths, WorkThread(
            id="wt1", title="学做饭", status="active", cumulative_progress="正在学习家常菜",
        ))
        backlog = GrowthBacklog(self.paths)
        backlog.add_or_merge(
            title="学做饭", rationale="r", evidence_refs=["e1", "e2", "e3"],
            min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
        )
        snap = initiative_inbox_snapshot(self.paths)
        item = snap["items"][0]
        self.assertIn("situational_relevance", item)
        self.assertGreater(item["situational_relevance"], 0.0)
        self.assertEqual(item["situational_relevance_source"], "学做饭")

    def test_annotate_relevance_false_never_reads_work_index_or_goals(self):
        upsert_work_thread(self.paths, WorkThread(id="wt1", title="学做饭", status="active"))
        backlog = GrowthBacklog(self.paths)
        backlog.add_or_merge(
            title="学做饭", rationale="r", evidence_refs=["e1", "e2", "e3"],
            min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
        )
        snap = initiative_inbox_snapshot(self.paths, annotate_relevance=False)
        self.assertNotIn("situational_relevance", snap["items"][0])

    def test_annotation_does_not_change_item_order_or_counts(self):
        upsert_work_thread(self.paths, WorkThread(id="wt1", title="学做饭", status="active"))
        backlog = GrowthBacklog(self.paths)
        backlog.add_or_merge(
            title="学做饭", rationale="r", evidence_refs=["e1", "e2", "e3"],
            min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
        )
        snap_on = initiative_inbox_snapshot(self.paths, annotate_relevance=True)
        snap_off = initiative_inbox_snapshot(self.paths, annotate_relevance=False)
        self.assertEqual(
            [it["item_id"] for it in snap_on["items"]],
            [it["item_id"] for it in snap_off["items"]],
        )
        self.assertEqual(snap_on["counts_by_domain"], snap_off["counts_by_domain"])
        self.assertEqual(snap_on["total"], snap_off["total"])


if __name__ == "__main__":
    unittest.main()
