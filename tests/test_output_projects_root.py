"""tests/test_output_projects_root.py

覆盖 next_doc/output_projects_root_and_git_isolation_plan.md 阶段2：
evolution/output_projects_root.py 的 resolve_root/ensure_root/
maybe_append_gitignore/ensure_output_projects_root。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.output_projects_root import (
    ensure_output_projects_root,
    ensure_root,
    maybe_append_gitignore,
    resolve_root,
)


class _FakeConfig:
    def __init__(self, project_root: Path, output_projects_root: str = "./output_projects"):
        self.project_root = project_root
        self.output_projects_root = output_projects_root


class ResolveRootTests(unittest.TestCase):
    def test_relative_path_resolves_against_project_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _FakeConfig(root, "./output_projects")
            resolved = resolve_root(cfg)
            self.assertEqual(resolved, (root / "output_projects").resolve())

    def test_absolute_path_used_as_is(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as other:
            root = Path(td)
            abs_path = str(Path(other) / "somewhere")
            cfg = _FakeConfig(root, abs_path)
            resolved = resolve_root(cfg)
            self.assertEqual(resolved, Path(abs_path).resolve())

    def test_default_when_field_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            class _Bare:
                project_root = root

            resolved = resolve_root(_Bare())
            self.assertEqual(resolved, (root / "output_projects").resolve())

    def test_project_root_path_fallback_reads_agent_config_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "agent_config.json").write_text(
                '{"output_projects_root": "./my_outputs"}', encoding="utf-8"
            )
            resolved = resolve_root(root)
            self.assertEqual(resolved, (root / "my_outputs").resolve())

    def test_project_root_path_fallback_default_when_no_config_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            resolved = resolve_root(root)
            self.assertEqual(resolved, (root / "output_projects").resolve())


class EnsureRootTests(unittest.TestCase):
    def test_creates_directory_idempotently(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _FakeConfig(root)
            p1 = ensure_root(cfg)
            self.assertTrue(p1.is_dir())
            # 幂等：再次调用不报错、不清空
            (p1 / "marker.txt").write_text("x", encoding="utf-8")
            p2 = ensure_root(cfg)
            self.assertEqual(p1, p2)
            self.assertTrue((p2 / "marker.txt").exists())


class GitignoreTests(unittest.TestCase):
    def test_appends_when_inside_project_root_and_gitignore_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _FakeConfig(root, "./output_projects")
            gi = maybe_append_gitignore(cfg)
            self.assertIsNotNone(gi)
            content = gi.read_text(encoding="utf-8")
            self.assertIn("output_projects/", content)

    def test_idempotent_no_duplicate_line(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _FakeConfig(root, "./output_projects")
            maybe_append_gitignore(cfg)
            maybe_append_gitignore(cfg)
            content = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertEqual(content.count("output_projects/"), 1)

    def test_preserves_existing_gitignore_content(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
            cfg = _FakeConfig(root, "./output_projects")
            maybe_append_gitignore(cfg)
            content = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("node_modules/", content)
            self.assertIn("output_projects/", content)

    def test_skipped_when_outside_project_root(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as other:
            root = Path(td)
            cfg = _FakeConfig(root, str(Path(other) / "elsewhere"))
            result = maybe_append_gitignore(cfg)
            self.assertIsNone(result)
            self.assertFalse((root / ".gitignore").exists())


class EnsureOutputProjectsRootTests(unittest.TestCase):
    def test_combined_call_creates_dir_and_gitignore(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _FakeConfig(root)
            out = ensure_output_projects_root(cfg)
            self.assertTrue(out.is_dir())
            self.assertTrue((root / ".gitignore").exists())


if __name__ == "__main__":
    unittest.main()
