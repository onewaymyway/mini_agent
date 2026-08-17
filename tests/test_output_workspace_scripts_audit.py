"""tests/test_output_workspace_scripts_audit.py

覆盖 next_doc/goal_output_directory_and_execution_phase_redesign_plan.md
§4 / Stage 5：`output_workspace.py` 新增的两个 `output/scripts/` 专项核查
函数：
    check_scripts_requirements_consistency() —— 方案 §7.1 第 7 条
    detect_experiments_promotion_candidates() —— 方案 §7.1 第 9 条
以及它们接入 `goal_cron_bridge._build_tidy_problem_checklist()` 后的
呈现效果。
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


class TestCheckScriptsRequirementsConsistency(unittest.TestCase):
    def test_no_scripts_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(ow.check_scripts_requirements_consistency(paths, "g1"), [])

    def test_flags_missing_third_party_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            scripts_dir = ow.goal_output_dir(paths, "g1") / "scripts"
            (scripts_dir / "fetch_metrics.py").write_text(
                "import os\nimport requests\nfrom pandas import DataFrame\n", encoding="utf-8",
            )
            missing = ow.check_scripts_requirements_consistency(paths, "g1")
            self.assertIn("requests", missing)
            self.assertIn("pandas", missing)
            self.assertNotIn("os", missing)

    def test_stdlib_and_declared_packages_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            scripts_dir = ow.goal_output_dir(paths, "g1") / "scripts"
            (scripts_dir / "requirements.txt").write_text("requests>=2.0\npandas\n", encoding="utf-8")
            (scripts_dir / "fetch_metrics.py").write_text(
                "import os\nimport json\nimport requests\nfrom pandas import DataFrame\n", encoding="utf-8",
            )
            missing = ow.check_scripts_requirements_consistency(paths, "g1")
            self.assertEqual(missing, [])

    def test_ignores_experiments_subdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            scripts_dir = ow.goal_output_dir(paths, "g1") / "scripts"
            (scripts_dir / "_experiments" / "try_thing.py").write_text(
                "import numpy\n", encoding="utf-8",
            )
            missing = ow.check_scripts_requirements_consistency(paths, "g1")
            self.assertEqual(missing, [])


class TestDetectExperimentsPromotionCandidates(unittest.TestCase):
    def test_no_experiments_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(ow.detect_experiments_promotion_candidates(paths, "g1"), [])

    def test_flags_frequently_referenced_unpromoted_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            experiments_dir = ow.goal_output_dir(paths, "g1") / "scripts" / "_experiments"
            (experiments_dir / "try_parse.py").write_text("pass", encoding="utf-8")
            ow.write_cycle_note(paths, "g1", 1, "本轮继续用 try_parse.py 验证解析逻辑")
            ow.write_cycle_note(paths, "g1", 2, "try_parse.py 验证通过，效果不错")

            candidates = ow.detect_experiments_promotion_candidates(paths, "g1", min_mentions=2)
            self.assertEqual(candidates, ["try_parse.py"])

    def test_already_promoted_script_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            scripts_dir = ow.goal_output_dir(paths, "g1") / "scripts"
            (scripts_dir / "_experiments" / "try_parse.py").write_text("pass", encoding="utf-8")
            (scripts_dir / "try_parse.py").write_text("pass", encoding="utf-8")
            ow.write_cycle_note(paths, "g1", 1, "try_parse.py 已经搬迁转正")
            ow.write_cycle_note(paths, "g1", 2, "try_parse.py 持续沿用")

            candidates = ow.detect_experiments_promotion_candidates(paths, "g1", min_mentions=2)
            self.assertEqual(candidates, [])

    def test_below_mention_threshold_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            experiments_dir = ow.goal_output_dir(paths, "g1") / "scripts" / "_experiments"
            (experiments_dir / "try_once.py").write_text("pass", encoding="utf-8")
            ow.write_cycle_note(paths, "g1", 1, "只提了一次 try_once.py")

            candidates = ow.detect_experiments_promotion_candidates(paths, "g1", min_mentions=2)
            self.assertEqual(candidates, [])


class TestTidyChecklistIncludesScriptsAudit(unittest.TestCase):
    def test_checklist_surfaces_missing_requirements_and_promotion_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            scripts_dir = ow.goal_output_dir(paths, "g1") / "scripts"
            (scripts_dir / "fetch_metrics.py").write_text("import requests\n", encoding="utf-8")
            (scripts_dir / "_experiments" / "try_parse.py").write_text("pass", encoding="utf-8")
            ow.write_cycle_note(paths, "g1", 1, "try_parse.py 效果不错")
            ow.write_cycle_note(paths, "g1", 2, "继续沿用 try_parse.py")

            checklist = bridge._build_tidy_problem_checklist(paths, "g1")
            self.assertIn("requests", checklist)
            self.assertIn("try_parse.py", checklist)

    def test_checklist_clean_state_has_no_scripts_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.ensure_output_skeleton(paths, "g1")
            checklist = bridge._build_tidy_problem_checklist(paths, "g1")
            self.assertIn("本轮代码扫描未发现确定性问题", checklist)


if __name__ == "__main__":
    unittest.main()
