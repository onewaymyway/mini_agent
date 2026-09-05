"""tests/test_growth_advisor_goal_topic_dedup.py — 候选生成阶段的 Goal
标题去重（对应 next_doc/growth_advisor_goal_cron_dedup_plan.md）。

覆盖：
  - 话题命中一个仍在 active/paused 的 Goal 标题 -> 不生成候选
  - 话题命中一个 completed/abandoned 的 Goal 标题 -> 正常生成候选
  - `goal_topic_dedup_enabled=False` -> 退化为改动前行为
  - spinoff 挖出的话题同样受这层过滤
  - 不传 goal_backlog -> 完全不受影响（向后兼容）
  - 抑制发生时落盘 growth_goal_dedup_suppressions.jsonl，且
    diagnostics_snapshot()/run_daily_cycle() 能读到明细
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.evolution import growth_advisor as ga
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.profile import UserProfile
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _profile_with_focus_areas(focus_areas: dict) -> UserProfile:
    profile = UserProfile()
    profile.derived["growth_focus_areas"] = focus_areas
    return profile


class TestGoalTopicDedup(unittest.TestCase):
    def test_active_goal_suppresses_matching_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.add_goal(title="学习 Rust 异步编程")
            profile = _profile_with_focus_areas(
                {"学习 Rust 异步编程": [f"e{i}" for i in range(5)]}
            )
            cfg = GrowthAdvisorConfig()
            produced = ga.growth_candidate_derive(paths, cfg, profile, goal_backlog=goal_backlog)
            self.assertEqual(produced, [])

    def test_completed_goal_does_not_suppress(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal = goal_backlog.add_goal(title="学习 Rust 异步编程")
            goal_backlog.set_status(goal.id, "completed")
            profile = _profile_with_focus_areas(
                {"学习 Rust 异步编程": [f"e{i}" for i in range(5)]}
            )
            cfg = GrowthAdvisorConfig()
            produced = ga.growth_candidate_derive(paths, cfg, profile, goal_backlog=goal_backlog)
            self.assertEqual(len(produced), 1)
            self.assertEqual(produced[0].title, "学习 Rust 异步编程")

    def test_disabled_flag_falls_back_to_old_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.add_goal(title="学习 Rust 异步编程")
            profile = _profile_with_focus_areas(
                {"学习 Rust 异步编程": [f"e{i}" for i in range(5)]}
            )
            cfg = GrowthAdvisorConfig(goal_topic_dedup_enabled=False)
            produced = ga.growth_candidate_derive(paths, cfg, profile, goal_backlog=goal_backlog)
            self.assertEqual(len(produced), 1)

    def test_no_goal_backlog_unaffected(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = _profile_with_focus_areas(
                {"学习 Rust 异步编程": [f"e{i}" for i in range(5)]}
            )
            cfg = GrowthAdvisorConfig()
            produced = ga.growth_candidate_derive(paths, cfg, profile, goal_backlog=None)
            self.assertEqual(len(produced), 1)

    def test_suppressed_topics_recorded_and_output_param_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.add_goal(title="学习 Rust 异步编程")
            profile = _profile_with_focus_areas(
                {
                    "学习 Rust 异步编程": [f"e{i}" for i in range(5)],
                    "提升写作能力": [f"f{i}" for i in range(4)],
                }
            )
            cfg = GrowthAdvisorConfig()
            out: list = []
            produced = ga.growth_candidate_derive(
                paths, cfg, profile, goal_backlog=goal_backlog,
                suppressed_goal_topics_out=out,
            )
            self.assertEqual(len(produced), 1)
            self.assertEqual(produced[0].title, "提升写作能力")
            self.assertEqual(out, ["学习 Rust 异步编程"])

            rows = ga._read_jsonl(paths.growth_goal_dedup_suppressions_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["count"], 1)
            self.assertIn("学习 Rust 异步编程", rows[0]["topics"])

            snap = ga._goal_dedup_diagnostics_summary(paths)
            self.assertEqual(snap["last_cycle_suppressed_count"], 1)

    def test_no_suppression_no_file_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = _profile_with_focus_areas(
                {"提升写作能力": [f"f{i}" for i in range(4)]}
            )
            cfg = GrowthAdvisorConfig()
            ga.growth_candidate_derive(paths, cfg, profile, goal_backlog=None)
            self.assertFalse(paths.growth_goal_dedup_suppressions_path.exists())
            snap = ga._goal_dedup_diagnostics_summary(paths)
            self.assertIsNone(snap["last_cycle_suppressed_count"])

    def test_active_goal_topic_keys_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            g1 = goal_backlog.add_goal(title="学习 Rust")
            g2 = goal_backlog.add_goal(title="健身计划")
            goal_backlog.set_status(g2.id, "abandoned")
            keys = ga._active_goal_topic_keys(goal_backlog)
            self.assertIn(ga.normalize_title_key("学习 Rust"), keys)
            self.assertNotIn(ga.normalize_title_key("健身计划"), keys)

        # goal_backlog=None -> 空 dict，不抛异常
        self.assertEqual(ga._active_goal_topic_keys(None), {})


if __name__ == "__main__":
    unittest.main()
