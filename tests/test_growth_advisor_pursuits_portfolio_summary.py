"""tests/test_growth_advisor_pursuits_portfolio_summary.py

覆盖 next_doc/growth_advisor_ideal_advisor_gap_and_roadmap_plan.md
方向 4：跨方向全局视角摘要。

  pursuits_portfolio_summary() —— 聚合饱和度信号（方向 B2）+ 参与度
  信号（方向 1），回答"该先看哪几个方向"，纯只读聚合、不产生新持久化。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import growth_advisor as ga
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.evolution.goal_cron_bridge import make_goal_recurring
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _make_pursuing_goal(paths, goal_backlog, title: str):
    """走真实的 adopt_candidate_as_goal + make_goal_recurring 落地一个
    "正在自主推进"的方向，返回 (candidate, goal)。"""
    backlog = ga.GrowthBacklog(paths)
    cand = backlog.add_or_merge(
        title=title,
        rationale="r",
        evidence_refs=[f"e{i}" for i in range(5)],
        min_evidence_count=3,
        max_pending=10,
        dismissed_cooldown_days=30,
    )
    candidate = backlog.get(cand.candidate_id)
    report = ga.generate_growth_report(paths, candidate)
    backlog.attach_report(cand.candidate_id, report.report_id)
    candidate = backlog.get(cand.candidate_id)
    goal = ga.adopt_candidate_as_goal(paths, candidate, goal_backlog=goal_backlog)
    cron_scheduler = CronScheduler(paths, submit_fn=None)
    make_goal_recurring(goal_backlog, cron_scheduler, goal.id, "interval:86400")
    return backlog.get(cand.candidate_id), goal_backlog.get(goal.id)


class TestPursuitsPortfolioSummary(unittest.TestCase):
    def test_empty_when_no_pursuits(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            summary = ga.pursuits_portfolio_summary(paths, goal_backlog)
            self.assertEqual(summary["total"], 0)
            self.assertEqual(summary["attention_needed"], [])
            self.assertEqual(summary["normal_count"], 0)

    def test_normal_pursuit_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            _make_pursuing_goal(paths, goal_backlog, "方向A")
            summary = ga.pursuits_portfolio_summary(paths, goal_backlog)
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["attention_needed"], [])
            self.assertEqual(summary["normal_count"], 1)
            self.assertEqual(summary["saturated_count"], 0)
            self.assertEqual(summary["long_unviewed_count"], 0)

    def test_saturated_pursuit_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            _, goal = _make_pursuing_goal(paths, goal_backlog, "方向B")
            for _ in range(3):
                ga.record_pursuit_cycle_signal(paths, goal.id, True)
            summary = ga.pursuits_portfolio_summary(paths, goal_backlog)
            self.assertEqual(summary["saturated_count"], 1)
            self.assertEqual(len(summary["attention_needed"]), 1)
            self.assertIn("saturated", summary["attention_needed"][0]["reasons"])
            self.assertEqual(summary["attention_needed"][0]["goal_id"], goal.id)

    def test_long_unviewed_pursuit_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            _, goal = _make_pursuing_goal(paths, goal_backlog, "方向C")
            # 手动推高 cycle_count 模拟"已经跑了很多轮但从没查看过"
            goal.cycle_count = 6
            goal_backlog.save()
            summary = ga.pursuits_portfolio_summary(paths, goal_backlog, long_unviewed_threshold=5)
            self.assertEqual(summary["long_unviewed_count"], 1)
            self.assertIn("long_unviewed", summary["attention_needed"][0]["reasons"])

    def test_freshly_viewed_not_flagged_as_long_unviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            _, goal = _make_pursuing_goal(paths, goal_backlog, "方向D")
            goal.cycle_count = 6
            goal_backlog.save()
            ga.record_pursuit_material_view(paths, goal.id, cycle_count=6)
            summary = ga.pursuits_portfolio_summary(paths, goal_backlog, long_unviewed_threshold=5)
            self.assertEqual(summary["long_unviewed_count"], 0)
            self.assertEqual(summary["attention_needed"], [])

    def test_paused_pursuit_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            _, goal = _make_pursuing_goal(paths, goal_backlog, "方向E")
            for _ in range(3):
                ga.record_pursuit_cycle_signal(paths, goal.id, True)
            # 暂停：recurring 置为 False
            goal.recurring = False
            goal_backlog.save()
            summary = ga.pursuits_portfolio_summary(paths, goal_backlog)
            self.assertEqual(summary["total"], 0)
            self.assertEqual(summary["attention_needed"], [])

    def test_same_pursuit_flagged_for_both_reasons_counts_once_in_attention(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            _, goal = _make_pursuing_goal(paths, goal_backlog, "方向F")
            for _ in range(3):
                ga.record_pursuit_cycle_signal(paths, goal.id, True)
            goal.cycle_count = 6
            goal_backlog.save()
            summary = ga.pursuits_portfolio_summary(paths, goal_backlog, long_unviewed_threshold=5)
            self.assertEqual(len(summary["attention_needed"]), 1)
            reasons = summary["attention_needed"][0]["reasons"]
            self.assertIn("saturated", reasons)
            self.assertIn("long_unviewed", reasons)
            self.assertEqual(summary["normal_count"], 0)


if __name__ == "__main__":
    unittest.main()
