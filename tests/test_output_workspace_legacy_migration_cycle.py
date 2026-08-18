"""tests/test_output_workspace_legacy_migration_cycle.py

覆盖 next_doc/goal_output_directory_and_execution_phase_redesign_plan.md
Stage 9："触发一次 tidy-like 的迁移轮"——把旧模型（每轮一个 cycle_NNNN/
目录）下遗留的历史数据，通过一次用户显式请求的一次性任务，搬迁进新的
固定四目录模型。

覆盖范围：
1. `output_workspace.list_legacy_cycle_dirs()` / `mark_legacy_cycle_dir_
   migrated()` —— 列出未迁移目录、标记迁移完成的幂等改名。
2. `output_workspace.build_legacy_migration_directive()` —— 生成迁移指令
   文本，含/不含 spec 两种情况。
3. `goal_cron_bridge._append_legacy_migration_directive()` —— 消费
   `goal.legacy_migration_requested` 一次性标记的胶水逻辑。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from mini_agent.evolution import output_workspace as ow
from mini_agent.evolution import goal_cron_bridge as bridge
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(Path(tmp))


class TestListAndMarkLegacyCycleDirs(unittest.TestCase):
    def test_no_base_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(ow.list_legacy_cycle_dirs(paths, "g1"), [])

    def test_lists_cycle_dirs_sorted_excluding_migrated_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            (base / "cycle_0002").mkdir(parents=True)
            (base / "cycle_0001").mkdir(parents=True)
            (base / "cycle_0003__migrated").mkdir(parents=True)
            dirs = ow.list_legacy_cycle_dirs(paths, "g1")
            self.assertEqual([d.name for d in dirs], ["cycle_0001", "cycle_0002"])

    def test_include_migrated_true_returns_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            (base / "cycle_0001").mkdir(parents=True)
            (base / "cycle_0002__migrated").mkdir(parents=True)
            dirs = ow.list_legacy_cycle_dirs(paths, "g1", include_migrated=True)
            self.assertEqual([d.name for d in dirs], ["cycle_0001", "cycle_0002__migrated"])

    def test_mark_migrated_renames_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            d = base / "cycle_0001"
            d.mkdir(parents=True)
            result = ow.mark_legacy_cycle_dir_migrated(d)
            self.assertEqual(result.name, "cycle_0001__migrated")
            self.assertTrue(result.exists())
            self.assertFalse(d.exists())

    def test_mark_migrated_idempotent_on_already_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            d = base / "cycle_0001__migrated"
            d.mkdir(parents=True)
            result = ow.mark_legacy_cycle_dir_migrated(d)
            self.assertEqual(result, d)

    def test_mark_migrated_target_exists_returns_target_without_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            src = base / "cycle_0001"
            src.mkdir(parents=True)
            (base / "cycle_0001__migrated").mkdir(parents=True)
            result = ow.mark_legacy_cycle_dir_migrated(src)
            self.assertEqual(result.name, "cycle_0001__migrated")
            # 源目录未被破坏性删除
            self.assertTrue(src.exists())


class TestBuildLegacyMigrationDirective(unittest.TestCase):
    def test_no_legacy_dirs_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertIsNone(ow.build_legacy_migration_directive(paths, "g1"))

    def test_all_already_migrated_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            (base / "cycle_0001__migrated").mkdir(parents=True)
            self.assertIsNone(ow.build_legacy_migration_directive(paths, "g1"))

    def test_directive_lists_pending_dirs_and_manifest_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            d = base / "cycle_0001"
            d.mkdir(parents=True)
            (d / "manifest.json").write_text(
                json.dumps({"progress_note": "抓取了一批数据"}), encoding="utf-8",
            )
            directive = ow.build_legacy_migration_directive(paths, "g1")
            self.assertIsNotNone(directive)
            self.assertIn("cycle_0001", directive)
            self.assertIn("抓取了一批数据", directive)
            self.assertIn("__migrated", directive)

    def test_directive_without_spec_points_to_misc(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            (base / "cycle_0001").mkdir(parents=True)
            directive = ow.build_legacy_migration_directive(paths, "g1", spec=None)
            self.assertIn("_misc/", directive)

    def test_directive_with_spec_lists_sub_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            (base / "cycle_0001").mkdir(parents=True)
            fake_sub_dir = MagicMock()
            fake_sub_dir.name = "reports"
            fake_spec = MagicMock()
            fake_spec.sub_directories = [fake_sub_dir]
            directive = ow.build_legacy_migration_directive(paths, "g1", spec=fake_spec)
            self.assertIn("reports", directive)


class _FakeGoal:
    def __init__(self, goal_id="g1", legacy_migration_requested=False):
        self.id = goal_id
        self.legacy_migration_requested = legacy_migration_requested


class _FakeGoalBacklog:
    def __init__(self):
        self.notes: list[tuple[str, str]] = []
        self.updates: list[dict] = []

    def append_progress_note(self, node_id: str, line: str) -> bool:
        self.notes.append((node_id, line))
        return True

    def update_fields(self, node_id: str, **kwargs):
        self.updates.append({"id": node_id, **kwargs})
        return None


class TestAppendLegacyMigrationDirective(unittest.TestCase):
    def test_noop_when_flag_not_set(self):
        goal = _FakeGoal(legacy_migration_requested=False)
        gb = _FakeGoalBacklog()
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = bridge._append_legacy_migration_directive(paths, gb, goal, 1, "desc")
        self.assertEqual(result, "desc")
        self.assertEqual(gb.updates, [])
        self.assertEqual(gb.notes, [])

    def test_clears_flag_and_appends_directive_when_legacy_dirs_exist(self):
        goal = _FakeGoal(legacy_migration_requested=True)
        gb = _FakeGoalBacklog()
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base = ow.goal_output_base_dir(paths, "g1")
            (base / "cycle_0001").mkdir(parents=True)
            result = bridge._append_legacy_migration_directive(paths, gb, goal, 3, "本轮任务描述")
        self.assertIn("本轮任务描述", result)
        self.assertIn("历史数据迁移", result)
        self.assertEqual(gb.updates, [{"id": "g1", "legacy_migration_requested": False}])
        self.assertTrue(any("迁移" in note for _id, note in gb.notes))

    def test_clears_flag_but_no_directive_when_no_legacy_dirs(self):
        goal = _FakeGoal(legacy_migration_requested=True)
        gb = _FakeGoalBacklog()
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = bridge._append_legacy_migration_directive(paths, gb, goal, 3, "本轮任务描述")
        self.assertEqual(result, "本轮任务描述")
        self.assertEqual(gb.updates, [{"id": "g1", "legacy_migration_requested": False}])
        self.assertTrue(any("未检测到" in note for _id, note in gb.notes))


if __name__ == "__main__":
    unittest.main()
