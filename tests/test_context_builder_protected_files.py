"""
tests/test_context_builder_protected_files.py — 阶段 1 验证

对应 next_doc/protected_files_manifest_and_delete_guard_plan.md 阶段 1：
  ContextBuilder._build_protected_files_reminder() 及其在 build() 中的接入——
  system prompt 里注入"当前生效的受保护文件清单"提醒片段，纯信息展示，
  不改变任何执行逻辑。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mini_agent.config import AppConfig
from mini_agent.context_builder import ContextBuilder
from scripts.protected_files import MANIFEST_FILENAME  # noqa: E402


def make_builder(project_root: Path, enabled: bool = True) -> ContextBuilder:
    cfg = AppConfig(
        project_root=project_root,
        protected_files_reminder_enabled=enabled,
    )
    return ContextBuilder(cfg=cfg)


class TestBuildProtectedFilesReminder:

    def test_empty_when_no_manifest(self, tmp_path):
        builder = make_builder(tmp_path)
        assert builder._build_protected_files_reminder() == ""

    def test_disabled_returns_empty_even_with_manifest(self, tmp_path):
        (tmp_path / MANIFEST_FILENAME).write_text("a.txt\n", encoding="utf-8")
        builder = make_builder(tmp_path, enabled=False)
        assert builder._build_protected_files_reminder() == ""

    def test_includes_declared_entries(self, tmp_path):
        (tmp_path / MANIFEST_FILENAME).write_text(
            "important_notes/\na.txt\n", encoding="utf-8"
        )
        builder = make_builder(tmp_path)
        block = builder._build_protected_files_reminder()
        assert "Protected files" in block
        assert "important_notes" in block
        assert "a.txt" in block
        # 清单文件自身也应出现
        assert MANIFEST_FILENAME in block

    def test_truncates_when_many_entries(self, tmp_path):
        lines = "\n".join(f"file_{i}.txt" for i in range(30))
        (tmp_path / MANIFEST_FILENAME).write_text(lines + "\n", encoding="utf-8")
        builder = make_builder(tmp_path)
        block = builder._build_protected_files_reminder()
        assert "还有" in block
        assert "file_0.txt" in block
        # 超出摘要上限的条目不应逐条列出
        assert "file_29.txt" not in block

    def test_build_injects_reminder_into_full_prompt(self, tmp_path):
        (tmp_path / MANIFEST_FILENAME).write_text("a.txt\n", encoding="utf-8")
        builder = make_builder(tmp_path)
        full = builder.build(history=[])
        assert "Protected files" in full
        assert "a.txt" in full

    def test_build_no_reminder_when_no_manifest(self, tmp_path):
        builder = make_builder(tmp_path)
        full = builder.build(history=[])
        assert "Protected files" not in full
