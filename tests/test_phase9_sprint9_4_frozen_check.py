"""tests/test_phase9_sprint9_4_frozen_check.py

`scripts/check_frozen_evolution_modules.py`（Phase 9 完成标志第 3、4 条的核对工具）。
关键性质：**无法核对绝不能被当成通过**，且工作区未提交的改动也必须被发现。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_frozen_evolution_modules.py"
_spec = importlib.util.spec_from_file_location("check_frozen", _SCRIPT)
chk = importlib.util.module_from_spec(_spec)
sys.modules["check_frozen"] = chk
_spec.loader.exec_module(chk)


def _git(root: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    for rel in chk.FROZEN + chk.ADAPTED:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# {Path(rel).name}\n", encoding="utf-8")
    return root


@pytest.fixture
def gitproj(proj: Path) -> tuple[Path, str]:
    _git(proj, "init", "-q")
    _git(proj, "config", "user.email", "t@t"); _git(proj, "config", "user.name", "t")
    _git(proj, "add", "-A"); _git(proj, "commit", "-q", "-m", "base")
    return proj, _git(proj, "rev-parse", "HEAD")


# ── git 模式 ───────────────────────────────────────────────────────────

def test_git_unchanged_passes(gitproj):
    root, base = gitproj
    code, out = chk.check_git(root, base, chk.FROZEN)
    assert code == chk.EXIT_OK and all(l.startswith(("unchanged", "基线")) for l in out)


def test_git_detects_committed_change(gitproj):
    root, base = gitproj
    (root / chk.FROZEN[0]).write_text("# tampered\n", encoding="utf-8")
    _git(root, "commit", "-qam", "touch frozen")
    code, out = chk.check_git(root, base, chk.FROZEN)
    assert code == chk.EXIT_CHANGED
    assert f"MODIFIED   {chk.FROZEN[0]}" in out


def test_git_detects_uncommitted_working_tree_change(gitproj):
    root, base = gitproj
    (root / chk.FROZEN[2]).write_text("# dirty\n", encoding="utf-8")   # 未 commit
    code, out = chk.check_git(root, base, chk.FROZEN)
    assert code == chk.EXIT_CHANGED and f"MODIFIED   {chk.FROZEN[2]}" in out


def test_git_ignores_changes_to_non_frozen_files(gitproj):
    root, base = gitproj
    (root / "src/mini_agent/evolution/proposals.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A"); _git(root, "commit", "-qm", "new module")
    assert chk.check_git(root, base, chk.FROZEN)[0] == chk.EXIT_OK


def test_adapted_only_checked_when_asked(gitproj):
    root, base = gitproj
    (root / chk.ADAPTED[0]).write_text("# changed\n", encoding="utf-8")
    assert chk.main(["--root", str(root), "--base", base]) == chk.EXIT_OK
    assert chk.main(["--root", str(root), "--base", base, "--also-adapted"]) == chk.EXIT_CHANGED


def test_not_a_git_repo_is_unverifiable_not_pass(proj):
    code, out = chk.check_git(proj, "HEAD", chk.FROZEN)
    assert code == chk.EXIT_UNVERIFIABLE and "manifest" in out[0]


def test_unknown_base_is_unverifiable(gitproj):
    root, _ = gitproj
    code, _ = chk.check_git(root, "no-such-rev", chk.FROZEN)
    assert code == chk.EXIT_UNVERIFIABLE


def test_missing_file_everywhere_is_unverifiable(gitproj):
    root, base = gitproj
    code, out = chk.check_git(root, base, chk.FROZEN + ["src/mini_agent/evolution/typo.py"])
    assert code == chk.EXIT_UNVERIFIABLE and "typo.py" in out[0]


# ── manifest 模式 ──────────────────────────────────────────────────────

def test_manifest_roundtrip_then_detect_change(proj, tmp_path):
    m = tmp_path / "m.sha256"
    assert chk.main(["--root", str(proj), "--also-adapted", "--write-manifest", str(m)]) == chk.EXIT_OK
    assert chk.main(["--root", str(proj), "--check-manifest", str(m)]) == chk.EXIT_OK

    (proj / chk.FROZEN[1]).write_text("# tampered\n", encoding="utf-8")
    code, out = chk.check_manifest(proj, m)
    assert code == chk.EXIT_CHANGED and f"MODIFIED   {chk.FROZEN[1]}" in out


def test_manifest_detects_deleted_file(proj, tmp_path):
    m = tmp_path / "m.sha256"
    chk.write_manifest(proj, m, chk.FROZEN)
    (proj / chk.FROZEN[3]).unlink()
    code, out = chk.check_manifest(proj, m)
    assert code == chk.EXIT_CHANGED and f"MISSING    {chk.FROZEN[3]}" in out


@pytest.mark.parametrize("content", ["", "garbage-without-space\n"])
def test_manifest_bad_or_empty_is_unverifiable(proj, tmp_path, content):
    m = tmp_path / "m.sha256"
    m.write_text(content, encoding="utf-8")
    assert chk.check_manifest(proj, m)[0] == chk.EXIT_UNVERIFIABLE


def test_manifest_missing_is_unverifiable(proj, tmp_path):
    assert chk.check_manifest(proj, tmp_path / "nope")[0] == chk.EXIT_UNVERIFIABLE


def test_shipped_manifest_matches_current_tree():
    """随交付物提交的基线必须与仓库里当前的冻结文件一致。"""
    root = Path(__file__).resolve().parents[1]
    m = root / "docs/architecture_v2/phase9-frozen-modules.sha256"
    assert m.is_file()
    code, out = chk.check_manifest(root, m)
    assert code == chk.EXIT_OK, out
