"""tests/test_output_workspace_migration_and_hints.py

覆盖两个针对"用户在 Goal 里指定了输出目录"和"已有 Goal 如何迁移到新
目录模型"提出的场景的新增能力：

1. `output_workspace.detect_user_specified_output_hint()` —— 检测 Goal
   description 里用户手写的"产出该放哪里"路径提示。
2. `output_workspace.has_legacy_cycle_dirs()` /
   `build_legacy_migration_summary()` —— 检测并汇总旧模型
   （每轮一个 `cycle_NNNN/` 目录）遗留下来的历史，供切换到新的固定四
   目录模型时生成一份迁移摘要，避免历史被无声丢弃。
3. 两者接入 `goal_cron_bridge._append_output_workspace_context()` 后的
   端到端效果。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import output_workspace as ow
from mini_agent.evolution import goal_cron_bridge as bridge
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(Path(tmp))


class _FakeGoal:
    def __init__(self, goal_id="g1", title="示例 Goal", description=""):
        self.id = goal_id
        self.title = title
        self.description = description


class TestDetectUserSpecifiedOutputHint(unittest.TestCase):
    def test_empty_description_returns_empty(self):
        self.assertEqual(ow.detect_user_specified_output_hint(""), [])
        self.assertEqual(ow.detect_user_specified_output_hint(None), [])

    def test_no_hint_keyword_returns_empty(self):
        self.assertEqual(ow.detect_user_specified_output_hint("每天汇总一次销售数据"), [])

    def test_detects_write_to_relative_path(self):
        hints = ow.detect_user_specified_output_hint("每周把周报写入 reports/weekly.md")
        self.assertTrue(any("reports/weekly.md" in h for h in hints))

    def test_detects_english_save_to(self):
        hints = ow.detect_user_specified_output_hint("Please save to data/raw/metrics.csv every run")
        self.assertTrue(any("data/raw/metrics.csv" in h for h in hints))

    def test_dedupes_repeated_hits(self):
        hints = ow.detect_user_specified_output_hint(
            "写入 reports/weekly.md，之后也保存到 reports/weekly.md 备份一份",
        )
        # 同一个路径片段只出现一次
        self.assertEqual(len([h for h in hints if "reports/weekly.md" in h]), 1)


class TestLegacyMigration(unittest.TestCase):
    def test_no_legacy_dirs_returns_false_and_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertFalse(ow.has_legacy_cycle_dirs(paths, "g1"))
            self.assertIsNone(ow.build_legacy_migration_summary(paths, "g1"))

    def test_legacy_dirs_without_manifest_returns_none_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            (base / "cycle_0001").mkdir(parents=True)
            self.assertTrue(ow.has_legacy_cycle_dirs(paths, "g1"))
            self.assertIsNone(ow.build_legacy_migration_summary(paths, "g1"))

    def test_legacy_dirs_with_manifest_produces_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            for i, note in enumerate(["第一轮完成", "第二轮完成"], start=1):
                d = base / f"cycle_{i:04d}"
                d.mkdir(parents=True)
                (d / "manifest.json").write_text(
                    json.dumps({"progress_note": note}), encoding="utf-8",
                )
            summary = ow.build_legacy_migration_summary(paths, "g1")
            self.assertIsNotNone(summary)
            self.assertIn("第一轮完成", summary)
            self.assertIn("第二轮完成", summary)
            self.assertIn("cycle_0001", summary)
            self.assertIn("cycle_0002", summary)


class TestAppendOutputWorkspaceContextMigrationAndHints(unittest.TestCase):
    def test_first_time_new_layout_writes_migration_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            d = base / "cycle_0001"
            d.mkdir(parents=True)
            (d / "manifest.json").write_text(
                json.dumps({"progress_note": "旧模型下已经跑过一轮"}), encoding="utf-8",
            )
            goal = _FakeGoal(description="常规巡检任务")
            bridge._append_output_workspace_context(paths, goal, 1, goal.description, phase_info=None)

            migration_note_path = ow.goal_notes_dir(paths, "g1") / "cycle_0000.md"
            self.assertTrue(migration_note_path.exists())
            content = migration_note_path.read_text(encoding="utf-8")
            self.assertIn("旧模型下已经跑过一轮", content)

    def test_no_legacy_dirs_no_migration_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal = _FakeGoal(description="常规巡检任务")
            bridge._append_output_workspace_context(paths, goal, 1, goal.description, phase_info=None)
            migration_note_path = ow.goal_notes_dir(paths, "g1") / "cycle_0000.md"
            self.assertFalse(migration_note_path.exists())

    def test_second_call_does_not_rewrite_migration_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            d = base / "cycle_0001"
            d.mkdir(parents=True)
            (d / "manifest.json").write_text(
                json.dumps({"progress_note": "旧模型摘要"}), encoding="utf-8",
            )
            goal = _FakeGoal(description="常规巡检任务")
            bridge._append_output_workspace_context(paths, goal, 1, goal.description, phase_info=None)
            migration_note_path = ow.goal_notes_dir(paths, "g1") / "cycle_0000.md"
            mtime_1 = migration_note_path.stat().st_mtime_ns

            # 第二轮再次调用，output/ 已经存在，不应重新判定/覆盖迁移说明
            bridge._append_output_workspace_context(paths, goal, 2, goal.description, phase_info=None)
            mtime_2 = migration_note_path.stat().st_mtime_ns
            self.assertEqual(mtime_1, mtime_2)

    def test_output_hint_in_description_surfaces_in_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal = _FakeGoal(description="每周把周报写入 reports/weekly.md")
            result = bridge._append_output_workspace_context(
                paths, goal, 1, goal.description, phase_info=None,
            )
            self.assertIn("reports/weekly.md", result)
            self.assertIn("自定义产出路径", result)

    def test_no_hint_no_extra_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal = _FakeGoal(description="常规巡检任务，无特殊路径要求")
            result = bridge._append_output_workspace_context(
                paths, goal, 1, goal.description, phase_info=None,
            )
            self.assertNotIn("自定义产出路径", result)


if __name__ == "__main__":
    unittest.main()
