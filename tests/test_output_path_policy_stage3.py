"""tests/test_output_path_policy_stage3.py

覆盖 next_doc/output_projects_root_and_git_isolation_plan.md 阶段3：
output_path_policy.py 新增第6条规则 + {{output_projects_root}} 占位符渲染
+ 老用户已有 policy 文件的追加式迁移。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.output_path_policy import (
    DEFAULT_POLICY,
    ensure_policy_file,
    load_policy,
    policy_path,
)


class _FakePaths:
    def __init__(self, project_root: Path):
        self.project_root = project_root


class _FakeConfig:
    def __init__(self, project_root: Path, output_projects_root: str = "./output_projects"):
        self.project_root = project_root
        self.output_projects_root = output_projects_root


class NewProjectTests(unittest.TestCase):
    def test_default_policy_contains_rule6(self):
        self.assertIn("调研类、产出类项目", DEFAULT_POLICY)
        self.assertIn("{{output_projects_root}}", DEFAULT_POLICY)

    def test_fresh_project_creates_file_with_rule6(self):
        with tempfile.TemporaryDirectory() as td:
            paths = _FakePaths(Path(td))
            path = ensure_policy_file(paths)
            content = path.read_text(encoding="utf-8")
            self.assertIn("调研类、产出类项目", content)

    def test_load_policy_renders_placeholder_with_cfg(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = _FakePaths(root)
            cfg = _FakeConfig(root, "./my_outputs")
            text = load_policy(paths, cfg=cfg)
            self.assertNotIn("{{output_projects_root}}", text)
            self.assertIn(str((root / "my_outputs").resolve()), text)

    def test_load_policy_renders_placeholder_without_cfg(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = _FakePaths(root)
            text = load_policy(paths)
            self.assertNotIn("{{output_projects_root}}", text)
            self.assertIn(str((root / "output_projects").resolve()), text)


class LegacyMigrationTests(unittest.TestCase):
    def test_old_policy_without_rule6_gets_migrated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = _FakePaths(root)
            old_content = (
                "# 产出路径规范\n\n"
                "1. 禁止把产出的代码写入主项目 `src/` 目录。\n"
                "2. 用户自定义的第二条规则，测试不应被改动。\n"
            )
            p = policy_path(paths)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(old_content, encoding="utf-8")

            ensure_policy_file(paths)
            new_content = p.read_text(encoding="utf-8")

            self.assertIn("用户自定义的第二条规则，测试不应被改动。", new_content)
            self.assertIn("调研类、产出类项目", new_content)
            self.assertIn("自动追加的新规则", new_content)

    def test_policy_already_containing_rule6_not_duplicated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = _FakePaths(root)
            p = policy_path(paths)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(DEFAULT_POLICY, encoding="utf-8")

            ensure_policy_file(paths)
            content = p.read_text(encoding="utf-8")
            self.assertEqual(content.count("调研类、产出类项目"), 1)


if __name__ == "__main__":
    unittest.main()
