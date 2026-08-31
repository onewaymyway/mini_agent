"""tests/test_protected_files_restore.py — 阶段 4 手动恢复入口测试。

对应 next_doc/protected_files_manifest_and_delete_guard_plan.md 阶段 4。

覆盖：
  1. restore_from_snapshot() 恢复全部路径（文件 + 目录）
  2. restore_from_snapshot() 只恢复指定的单个路径
  3. 快照不存在 / 路径不在快照清单里时返回明确错误，不抛异常
  4. handle_protected_cmd 的 restore 子命令：不加 --force 时只打印不执行，
     加 --force 才真正写盘（覆盖式恢复），status/list 子命令能正常跑通
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.protected_files import MANIFEST_FILENAME  # noqa: E402
from mini_agent.evolution.protected_files_backup import (  # noqa: E402
    run_backup_once,
    restore_from_snapshot,
)
from mini_agent.storage.paths import AgentPaths  # noqa: E402
from mini_agent.cli.commands.protected_cmd import handle_protected_cmd  # noqa: E402


class _FakeAgent:
    def __init__(self, paths):
        self._paths = paths


class TestRestoreFromSnapshot(unittest.TestCase):

    def _setup_project(self, tmp: Path):
        (tmp / MANIFEST_FILENAME).write_text("a.txt\nnotes/\n", encoding="utf-8")
        (tmp / "a.txt").write_text("original a", encoding="utf-8")
        notes = tmp / "notes"
        notes.mkdir()
        (notes / "n1.md").write_text("original note", encoding="utf-8")
        return run_backup_once(tmp)

    def test_restore_all_paths(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            summary = self._setup_project(root)

            # 模拟数据被破坏
            (root / "a.txt").write_text("corrupted", encoding="utf-8")
            (root / "notes" / "n1.md").unlink()

            restore_summary = restore_from_snapshot(root, summary.generation_id)
            self.assertEqual(restore_summary.errors, [])
            self.assertEqual(len(restore_summary.restored), 3)  # manifest 自身 + a.txt + notes/
            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "original a")
            self.assertEqual(
                (root / "notes" / "n1.md").read_text(encoding="utf-8"), "original note"
            )

    def test_restore_single_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            summary = self._setup_project(root)

            (root / "a.txt").write_text("corrupted", encoding="utf-8")
            target = str((root / "a.txt").resolve())

            restore_summary = restore_from_snapshot(root, summary.generation_id, paths=[target])
            self.assertEqual(restore_summary.restored, [target])
            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "original a")

    def test_restore_unknown_generation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            self._setup_project(root)
            restore_summary = restore_from_snapshot(root, "does_not_exist")
            self.assertTrue(restore_summary.errors)
            self.assertEqual(restore_summary.restored, [])

    def test_restore_unknown_path_in_snapshot(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            summary = self._setup_project(root)
            restore_summary = restore_from_snapshot(
                root, summary.generation_id, paths=["/not/in/manifest.txt"]
            )
            self.assertTrue(restore_summary.errors)
            self.assertEqual(restore_summary.restored, [])


class TestHandleProtectedCmd(unittest.TestCase):

    def test_status_and_list_do_not_raise(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            (root / MANIFEST_FILENAME).write_text("a.txt\n", encoding="utf-8")
            (root / "a.txt").write_text("hi", encoding="utf-8")
            run_backup_once(root)

            paths = AgentPaths(project_root=root)
            agent = _FakeAgent(paths)
            handle_protected_cmd(["status"], agent)
            handle_protected_cmd(["list"], agent)

    def test_restore_without_force_does_not_write(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            (root / MANIFEST_FILENAME).write_text("a.txt\n", encoding="utf-8")
            (root / "a.txt").write_text("original", encoding="utf-8")
            summary = run_backup_once(root)

            (root / "a.txt").write_text("corrupted", encoding="utf-8")

            paths = AgentPaths(project_root=root)
            agent = _FakeAgent(paths)
            handle_protected_cmd(["restore", summary.generation_id], agent)

            # 没有 --force，不应该真的恢复
            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "corrupted")

    def test_restore_with_force_writes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_s:
            root = Path(tmp_s)
            (root / MANIFEST_FILENAME).write_text("a.txt\n", encoding="utf-8")
            (root / "a.txt").write_text("original", encoding="utf-8")
            summary = run_backup_once(root)

            (root / "a.txt").write_text("corrupted", encoding="utf-8")

            paths = AgentPaths(project_root=root)
            agent = _FakeAgent(paths)
            handle_protected_cmd(["restore", summary.generation_id, "--force"], agent)

            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "original")


if __name__ == "__main__":
    unittest.main()
