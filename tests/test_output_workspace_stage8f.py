"""tests/test_output_workspace_stage8f.py

覆盖 next_doc/goal_output_directory_and_execution_phase_redesign_plan.md
Stage 8f：三种 `output_mode` tidy 默认模板差异化：
    - default_promotion_mention_threshold()
    - detect_accretive_duplicate_candidates()
以及它们接入 `goal_cron_bridge._build_tidy_problem_checklist()` 后的效果。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import output_workspace as ow
from mini_agent.evolution import goal_cron_bridge as bridge
from mini_agent.perception import goal_execution_spec as ges
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(Path(tmp))


class TestDefaultPromotionMentionThreshold(unittest.TestCase):
    def test_capability_hardening_uses_lower_threshold(self):
        self.assertEqual(ow.default_promotion_mention_threshold("capability_hardening"), 1)

    def test_other_modes_use_default_threshold(self):
        self.assertEqual(ow.default_promotion_mention_threshold("converging"), 2)
        self.assertEqual(ow.default_promotion_mention_threshold("accretive"), 2)
        self.assertEqual(ow.default_promotion_mention_threshold("hybrid"), 2)
        self.assertEqual(ow.default_promotion_mention_threshold("not_a_real_mode"), 2)


class TestDetectAccretiveDuplicateCandidates(unittest.TestCase):
    def test_no_output_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(ow.detect_accretive_duplicate_candidates(paths, "no_such_goal"), {})

    def test_flags_versioned_duplicate_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            out_dir = ow.goal_output_dir(paths, "g1")
            (out_dir / "report.md").write_text("v1", encoding="utf-8")
            (out_dir / "report_v2.md").write_text("v2", encoding="utf-8")

            result = ow.detect_accretive_duplicate_candidates(paths, "g1")
            self.assertIn("report", result)
            self.assertEqual(set(result["report"]), {"report.md", "report_v2.md"})

    def test_readme_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            out_dir = ow.goal_output_dir(paths, "g1")
            (out_dir / "single_report.md").write_text("x", encoding="utf-8")

            result = ow.detect_accretive_duplicate_candidates(paths, "g1")
            self.assertEqual(result, {})

    def test_single_file_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            out_dir = ow.goal_output_dir(paths, "g1")
            (out_dir / "report.md").write_text("x", encoding="utf-8")

            result = ow.detect_accretive_duplicate_candidates(paths, "g1")
            self.assertEqual(result, {})


class TestTidyChecklistOutputModeAwareness(unittest.TestCase):
    def test_accretive_spec_surfaces_duplicate_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            out_dir = ow.goal_output_dir(paths, "g1")
            (out_dir / "wiki_entry.md").write_text("v1", encoding="utf-8")
            (out_dir / "wiki_entry_copy.md").write_text("v2", encoding="utf-8")

            spec = ges.GoalExecutionSpec(goal_id="g1", output_mode="accretive")
            checklist = bridge._build_tidy_problem_checklist(paths, "g1", spec=spec)
            self.assertIn("重复累积", checklist)
            self.assertIn("wiki_entry", checklist)

    def test_converging_spec_does_not_run_duplicate_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            out_dir = ow.goal_output_dir(paths, "g1")
            (out_dir / "wiki_entry.md").write_text("v1", encoding="utf-8")
            (out_dir / "wiki_entry_copy.md").write_text("v2", encoding="utf-8")

            spec = ges.GoalExecutionSpec(goal_id="g1", output_mode="converging")
            checklist = bridge._build_tidy_problem_checklist(paths, "g1", spec=spec)
            self.assertNotIn("重复累积", checklist)

    def test_capability_hardening_lowers_promotion_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            experiments_dir = ow.goal_output_dir(paths, "g1") / "scripts" / "_experiments"
            (experiments_dir / "try_parse.py").write_text("pass", encoding="utf-8")
            # 只提及一次——converging 默认阈值 2 不会命中，capability_hardening 阈值 1 会命中
            ow.write_cycle_note(paths, "g1", 1, "try_parse.py 效果不错")

            spec_hardening = ges.GoalExecutionSpec(goal_id="g1", output_mode="capability_hardening")
            checklist_hardening = bridge._build_tidy_problem_checklist(paths, "g1", spec=spec_hardening)
            self.assertIn("try_parse.py", checklist_hardening)

            spec_converging = ges.GoalExecutionSpec(goal_id="g1", output_mode="converging")
            checklist_converging = bridge._build_tidy_problem_checklist(paths, "g1", spec=spec_converging)
            self.assertNotIn("try_parse.py", checklist_converging)


if __name__ == "__main__":
    unittest.main()
