"""tests/test_output_workspace_new_layout.py

覆盖 next_doc/goal_output_directory_and_execution_phase_redesign_plan.md
Stage 1（output_workspace.py 目录模型改造）新增的一组函数：
    goal_output_dir / goal_notes_dir / goal_spec_dir / goal_scratch_dir
    ensure_output_skeleton / scan_output_structure / render_output_readme
    write_cycle_note / read_recent_notes / archive_old_notes
    scratch_is_empty

不覆盖旧的 allocate_cycle_dir()/write_manifest() 等 legacy 函数——那些行为
未变化，已有测试覆盖（test_goal_output_directory_onetime.py 等）。
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.evolution import output_workspace as ow
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(Path(tmp))


class TestPathHelpers(unittest.TestCase):
    def test_four_dirs_are_siblings_under_goal_base_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            self.assertEqual(ow.goal_output_dir(paths, "g1"), base / "output")
            self.assertEqual(ow.goal_notes_dir(paths, "g1"), base / "notes")
            self.assertEqual(ow.goal_spec_dir(paths, "g1"), base / "spec")
            self.assertEqual(ow.goal_scratch_dir(paths, "g1"), base / "scratch")


class TestEnsureOutputSkeleton(unittest.TestCase):
    def test_creates_reserved_dirs_and_files_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            out_dir = ow.ensure_output_skeleton(paths, "g1")
            self.assertTrue((out_dir / "_misc").is_dir())
            self.assertTrue((out_dir / "_archive").is_dir())
            self.assertTrue((out_dir / "scripts").is_dir())
            self.assertTrue((out_dir / "scripts" / "lib").is_dir())
            self.assertTrue((out_dir / "scripts" / "_run_logs").is_dir())
            self.assertTrue((out_dir / "scripts" / "_experiments").is_dir())
            self.assertTrue((out_dir / "scripts" / "requirements.txt").exists())
            self.assertTrue((out_dir / "scripts" / "CHANGELOG.md").exists())
            self.assertTrue((out_dir / "scripts" / "README.md").exists())
            self.assertTrue((out_dir / "README.md").exists())

            # 已存在的文件不覆盖
            (out_dir / "scripts" / "requirements.txt").write_text("requests\n", encoding="utf-8")
            ow.ensure_output_skeleton(paths, "g1")
            self.assertEqual(
                (out_dir / "scripts" / "requirements.txt").read_text(encoding="utf-8"), "requests\n"
            )


class TestScanOutputStructure(unittest.TestCase):
    def test_empty_output_dir_returns_zeroed_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            stats = ow.scan_output_structure(paths, "g1")
            self.assertEqual(stats["misc_count"], 0)
            self.assertEqual(stats["root_unexpected"], [])
            self.assertEqual(stats["sub_dirs"], {})

    def test_root_directories_are_not_flagged_but_stray_files_are(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            out_dir = ow.ensure_output_skeleton(paths, "g1")
            (out_dir / "_misc" / "orphan.txt").write_text("x", encoding="utf-8")
            (out_dir / "reports").mkdir()  # 合法业务子目录，不应被标记
            (out_dir / "reports" / "2026-07-20.md").write_text("r", encoding="utf-8")
            (out_dir / "stray.md").write_text("y", encoding="utf-8")  # 散落文件，应被标记

            stats = ow.scan_output_structure(paths, "g1")
            self.assertEqual(stats["misc_count"], 1)
            self.assertIn("orphan.txt", stats["misc_files"])
            self.assertNotIn("reports", stats["root_unexpected"])  # 目录一律不算未分类
            self.assertIn("stray.md", stats["root_unexpected"])     # 散落文件才算
            self.assertEqual(stats["sub_dirs"]["reports"]["file_count"], 1)

    def test_detects_temp_named_scripts_in_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            out_dir = ow.ensure_output_skeleton(paths, "g1")
            scripts_dir = out_dir / "scripts"
            (scripts_dir / "fetch_metrics.py").write_text("# ok", encoding="utf-8")
            (scripts_dir / "test_parse.py").write_text("# temp", encoding="utf-8")
            (scripts_dir / "_experiments" / "try_1.py").write_text("# scratch", encoding="utf-8")
            (scripts_dir / "_run_logs" / "fetch_metrics_2026.log").write_text("log", encoding="utf-8")

            stats = ow.scan_output_structure(paths, "g1")
            self.assertIn("fetch_metrics.py", stats["scripts"]["root_files"])
            self.assertIn("test_parse.py", stats["scripts"]["unexpected_root_files"])
            self.assertNotIn("fetch_metrics.py", stats["scripts"]["unexpected_root_files"])
            self.assertEqual(stats["scripts"]["experiments_count"], 1)
            self.assertEqual(stats["scripts"]["run_logs_count"], 1)


class TestRenderOutputReadme(unittest.TestCase):
    def test_generates_readme_reflecting_actual_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            out_dir = ow.ensure_output_skeleton(paths, "g1")
            (out_dir / "reports").mkdir()
            (out_dir / "reports" / "a.md").write_text("x", encoding="utf-8")
            (out_dir / "_misc" / "orphan.txt").write_text("x", encoding="utf-8")

            text = ow.render_output_readme(paths, "g1", cycle_no=3)
            self.assertIn("第 3 轮", text)
            self.assertIn("reports/", text)
            self.assertIn("⚠️", text)  # _misc 非空应该有警告标记
            # 确认真的写入了文件，不只是返回值
            self.assertEqual((out_dir / "README.md").read_text(encoding="utf-8"), text)


class TestNotes(unittest.TestCase):
    def test_write_and_read_recent_notes_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.write_cycle_note(paths, "g1", 1, "第一轮：探索了方案 A")
            ow.write_cycle_note(paths, "g1", 2, "第二轮：探索了方案 B")
            ow.write_cycle_note(paths, "g1", 3, "第三轮：选定方案 B")

            recent = ow.read_recent_notes(paths, "g1", limit=2)
            self.assertEqual([n["cycle_no"] for n in recent], [3, 2])
            self.assertIn("选定方案 B", recent[0]["content"])

    def test_read_recent_notes_empty_when_no_notes_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(ow.read_recent_notes(paths, "g1"), [])

    def test_archive_old_notes_moves_oldest_beyond_keep_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            for i in range(1, 6):
                ow.write_cycle_note(paths, "g1", i, f"第 {i} 轮")

            moved = ow.archive_old_notes(paths, "g1", keep_recent=3)
            self.assertEqual(moved, 2)
            notes_dir = ow.goal_notes_dir(paths, "g1")
            remaining = sorted(f.name for f in notes_dir.iterdir() if f.is_file())
            self.assertEqual(remaining, ["cycle_0003.md", "cycle_0004.md", "cycle_0005.md"])
            archived = sorted(f.name for f in (notes_dir / "archive").iterdir())
            self.assertEqual(archived, ["cycle_0001.md", "cycle_0002.md"])

    def test_archive_old_notes_noop_when_under_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ow.write_cycle_note(paths, "g1", 1, "第一轮")
            moved = ow.archive_old_notes(paths, "g1", keep_recent=10)
            self.assertEqual(moved, 0)


class TestScratchIsEmpty(unittest.TestCase):
    def test_true_when_dir_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertTrue(ow.scratch_is_empty(paths, "g1"))

    def test_false_when_has_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scratch = ow.goal_scratch_dir(paths, "g1")
            scratch.mkdir(parents=True)
            (scratch / "attempt.py").write_text("x", encoding="utf-8")
            self.assertFalse(ow.scratch_is_empty(paths, "g1"))

    def test_true_after_removing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scratch = ow.goal_scratch_dir(paths, "g1")
            (scratch / "sub").mkdir(parents=True)
            self.assertTrue(ow.scratch_is_empty(paths, "g1"))  # 只有空目录，没有文件


if __name__ == "__main__":
    unittest.main()
