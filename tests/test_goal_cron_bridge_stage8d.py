"""tests/test_goal_cron_bridge_stage8d.py

覆盖 next_doc/goal_output_directory_and_execution_phase_redesign_plan.md
Stage 8d：`hardening_target`/`sub_exploration` 接入 converge 搬迁行为 +
tidy `_experiments/` 转正提示区分固化目标。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import output_workspace as ow
from mini_agent.evolution import goal_cron_bridge as bridge
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(Path(tmp))


class _FakeGoal:
    def __init__(self, goal_id: str):
        self.id = goal_id


class TestConvergeBlockSurfacesHardeningTargetAndSubExploration(unittest.TestCase):
    def test_hardening_target_appears_in_converge_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.perception import goal_execution_spec as ges

            spec = ges.GoalExecutionSpec(goal_id="g1", hardening_target="skills/report_writer/")
            phase_info = {"effective_mode": "converge", "spec_confirmed": True, "spec": spec}

            desc = bridge._append_output_workspace_context(
                paths, _FakeGoal("g1"), 5, "写周报", phase_info=phase_info,
            )
            self.assertIn("hardening_target", desc)
            self.assertIn("skills/report_writer/", desc)

    def test_sub_exploration_appears_in_converge_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.perception import goal_execution_spec as ges

            spec = ges.GoalExecutionSpec(goal_id="g1", sub_exploration="信息源调研，独立生命周期")
            phase_info = {"effective_mode": "converge", "spec_confirmed": True, "spec": spec}

            desc = bridge._append_output_workspace_context(
                paths, _FakeGoal("g1"), 5, "写周报", phase_info=phase_info,
            )
            self.assertIn("子探索", desc)
            self.assertIn("信息源调研，独立生命周期", desc)
            self.assertIn("不参与本 Goal 的 spec_phase 判定", desc)

    def test_converge_prompt_unaffected_when_fields_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.perception import goal_execution_spec as ges

            spec = ges.GoalExecutionSpec(goal_id="g1")
            phase_info = {"effective_mode": "converge", "spec_confirmed": True, "spec": spec}

            desc = bridge._append_output_workspace_context(
                paths, _FakeGoal("g1"), 5, "写周报", phase_info=phase_info,
            )
            self.assertNotIn("hardening_target", desc)
            self.assertNotIn("子探索", desc)

    def test_converge_prompt_unaffected_when_spec_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            phase_info = {"effective_mode": "converge", "spec_confirmed": False, "spec": None}
            desc = bridge._append_output_workspace_context(
                paths, _FakeGoal("g1"), 5, "写周报", phase_info=phase_info,
            )
            self.assertNotIn("hardening_target", desc)


class TestTidyChecklistHardeningTargetAwarePromotionHint(unittest.TestCase):
    def test_promotion_hint_mentions_hardening_target_when_declared(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            experiments_dir = ow.goal_output_dir(paths, "g1") / "scripts" / "_experiments"
            (experiments_dir / "try_parse.py").write_text("pass", encoding="utf-8")
            ow.write_cycle_note(paths, "g1", 1, "try_parse.py 效果不错")
            ow.write_cycle_note(paths, "g1", 2, "继续沿用 try_parse.py")

            from mini_agent.perception import goal_execution_spec as ges
            spec = ges.GoalExecutionSpec(goal_id="g1", hardening_target="skills/report_writer/")

            checklist = bridge._build_tidy_problem_checklist(paths, "g1", spec=spec)
            self.assertIn("try_parse.py", checklist)
            self.assertIn("skills/report_writer/", checklist)

    def test_promotion_hint_falls_back_to_generic_wording_without_hardening_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            experiments_dir = ow.goal_output_dir(paths, "g1") / "scripts" / "_experiments"
            (experiments_dir / "try_parse.py").write_text("pass", encoding="utf-8")
            ow.write_cycle_note(paths, "g1", 1, "try_parse.py 效果不错")
            ow.write_cycle_note(paths, "g1", 2, "继续沿用 try_parse.py")

            checklist = bridge._build_tidy_problem_checklist(paths, "g1")
            self.assertIn("try_parse.py", checklist)
            self.assertIn("scripts/ 根目录", checklist)

    def test_default_spec_none_behaves_like_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            checklist = bridge._build_tidy_problem_checklist(paths, "g1")
            self.assertIn("本轮代码扫描未发现确定性问题", checklist)


if __name__ == "__main__":
    unittest.main()
