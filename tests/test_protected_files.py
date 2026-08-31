"""
tests/test_protected_files.py — 阶段 0 验证

验证 scripts/protected_files.py：
  - 未声明任何清单文件时，判定始终为 False（不误伤）
  - 顶层 protected_files.txt 与 .agent/protected_files.txt 均生效，取并集
  - 精确文件 / 目录前缀两种条目形式
  - 相对路径以清单文件自身所在目录为基准解析（而非 project_root）
  - 注释行、空行被忽略
  - 清单文件自身无条件受保护，即使内容为空
  - 该模块不 import mini_agent 包内任何东西（保持自包含）
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.protected_files import (  # noqa: E402
    MANIFEST_FILENAME,
    ProtectedFilesGuard,
    is_protected_file,
    list_protected_files,
)


def test_no_manifest_means_nothing_protected(tmp_path):
    guard = ProtectedFilesGuard(tmp_path)
    assert guard.list_entries() == ()
    assert not guard.is_protected(tmp_path / "anything.txt")


def test_top_level_manifest_exact_file(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text(
        "important_notes.md\n", encoding="utf-8"
    )
    guard = ProtectedFilesGuard(tmp_path)
    assert guard.is_protected(tmp_path / "important_notes.md")
    assert not guard.is_protected(tmp_path / "other.md")


def test_directory_entry_protects_subtree(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text(
        "important_notes/\n", encoding="utf-8"
    )
    guard = ProtectedFilesGuard(tmp_path)
    assert guard.is_protected(tmp_path / "important_notes")
    assert guard.is_protected(tmp_path / "important_notes" / "a.md")
    assert guard.is_protected(tmp_path / "important_notes" / "sub" / "b.md")
    # 前缀匹配不应误伤同名前缀的其他目录
    assert not guard.is_protected(tmp_path / "important_notes_archive" / "c.md")


def test_dot_agent_manifest_also_scanned(tmp_path):
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / MANIFEST_FILENAME).write_text("runtime_state.db\n", encoding="utf-8")
    guard = ProtectedFilesGuard(tmp_path)
    # 相对路径以清单文件自身所在目录（.agent/）为基准解析
    assert guard.is_protected(agent_dir / "runtime_state.db")
    assert not guard.is_protected(tmp_path / "runtime_state.db")


def test_two_manifests_take_union(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text("a.txt\n", encoding="utf-8")
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / MANIFEST_FILENAME).write_text("b.txt\n", encoding="utf-8")
    guard = ProtectedFilesGuard(tmp_path)
    assert guard.is_protected(tmp_path / "a.txt")
    assert guard.is_protected(agent_dir / "b.txt")


def test_relative_path_resolved_against_manifest_dir_not_project_root(tmp_path):
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    # 用 ../ 引用到 project_root 层级的文件，验证基准是清单自身所在目录
    (agent_dir / MANIFEST_FILENAME).write_text(
        "../shared_configs/team_settings.json\n", encoding="utf-8"
    )
    guard = ProtectedFilesGuard(tmp_path)
    assert guard.is_protected(
        tmp_path / "shared_configs" / "team_settings.json"
    )


def test_absolute_path_entry(tmp_path):
    target = tmp_path / "elsewhere" / "cross_project_data.db"
    (tmp_path / MANIFEST_FILENAME).write_text(f"{target.as_posix()}\n", encoding="utf-8")
    guard = ProtectedFilesGuard(tmp_path)
    assert guard.is_protected(target)


def test_comments_and_blank_lines_ignored(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text(
        "# 这是注释\n\n   \na.txt\n# another comment\n",
        encoding="utf-8",
    )
    guard = ProtectedFilesGuard(tmp_path)
    entries = guard.list_entries()
    # 清单文件自身 + a.txt，共两条
    protected_paths = {e.path for e in entries}
    assert (tmp_path / MANIFEST_FILENAME).resolve().as_posix() in protected_paths
    assert (tmp_path / "a.txt").resolve().as_posix() in protected_paths
    assert len(entries) == 2


def test_manifest_file_itself_always_protected_even_if_empty(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text("", encoding="utf-8")
    guard = ProtectedFilesGuard(tmp_path)
    assert guard.is_protected(tmp_path / MANIFEST_FILENAME)


def test_module_level_convenience_functions(tmp_path):
    (tmp_path / MANIFEST_FILENAME).write_text("a.txt\n", encoding="utf-8")
    assert is_protected_file(tmp_path / "a.txt", project_root=tmp_path)
    assert not is_protected_file(tmp_path / "b.txt", project_root=tmp_path)
    entries = list_protected_files(tmp_path)
    assert len(entries) == 2  # 清单自身 + a.txt


def test_add_entry_creates_manifest_and_is_idempotent(tmp_path):
    from scripts.protected_files import add_entry

    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    line1 = add_entry(tmp_path, tmp_path / "a.txt", is_dir=False)
    assert line1 == "a.txt"

    guard = ProtectedFilesGuard(tmp_path)
    assert guard.is_protected(tmp_path / "a.txt")

    # 幂等：重复添加不产生第二行
    add_entry(tmp_path, tmp_path / "a.txt", is_dir=False)
    content = (tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8")
    assert content.count("a.txt") == 1


def test_add_entry_directory_and_workdir_manifest(tmp_path):
    from scripts.protected_files import add_entry

    notes = tmp_path / "notes"
    notes.mkdir()
    add_entry(tmp_path, notes, is_dir=True, manifest="workdir")

    guard = ProtectedFilesGuard(tmp_path)
    assert guard.is_protected(notes / "sub" / "n.md")
    assert (tmp_path / ".agent" / MANIFEST_FILENAME).is_file()


def test_remove_entry_deletes_declared_line(tmp_path):
    from scripts.protected_files import add_entry, remove_entry

    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y", encoding="utf-8")
    add_entry(tmp_path, tmp_path / "a.txt")
    add_entry(tmp_path, tmp_path / "b.txt")

    ok = remove_entry(tmp_path, tmp_path / "a.txt")
    assert ok is True

    guard = ProtectedFilesGuard(tmp_path)
    assert not guard.is_protected(tmp_path / "a.txt")
    assert guard.is_protected(tmp_path / "b.txt")


def test_remove_entry_cannot_remove_manifest_itself(tmp_path):
    from scripts.protected_files import add_entry, remove_entry

    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    add_entry(tmp_path, tmp_path / "a.txt")

    ok = remove_entry(tmp_path, tmp_path / MANIFEST_FILENAME)
    assert ok is False
    guard = ProtectedFilesGuard(tmp_path)
    assert guard.is_protected(tmp_path / MANIFEST_FILENAME)


def test_remove_entry_not_found_returns_false(tmp_path):
    from scripts.protected_files import remove_entry

    assert remove_entry(tmp_path, tmp_path / "nope.txt") is False


def test_module_is_self_contained():
    """
    本模块不应 import mini_agent 包，理由同 protected_paths.py：一旦
    mini_agent 包本身在演化过程中被改坏，这份兜底机制也应保持独立可用。
    """
    lines = (REPO_ROOT / "scripts" / "protected_files.py").read_text(
        encoding="utf-8"
    ).splitlines()
    import_lines = [
        ln.strip() for ln in lines
        if ln.strip().startswith("import ") or ln.strip().startswith("from ")
    ]
    assert not any("mini_agent" in ln for ln in import_lines), import_lines
