"""
tests/test_protected_files_guard_integration.py — 阶段 2 验证

对应 next_doc/protected_files_manifest_and_delete_guard_plan.md 阶段 2：
逐一验证"实施范围"表格列出的删除点接入了统一的受保护文件 guard——
命中受保护路径时跳过删除、不中断整体维护任务。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.protected_files import MANIFEST_FILENAME  # noqa: E402
from mini_agent.utils.protected_files_guard import is_protected  # noqa: E402


# ── utils/protected_files_guard.py 本身 ──────────────────────────────────────

class TestIsProtectedWrapper:
    def test_not_protected_without_manifest(self, tmp_path):
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert not is_protected(target, tmp_path)

    def test_protected_with_manifest(self, tmp_path):
        (tmp_path / MANIFEST_FILENAME).write_text("a.txt\n", encoding="utf-8")
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert is_protected(target, tmp_path)


# ── session_cleanup.py ───────────────────────────────────────────────────────

class TestSessionCleanupGuard:
    def test_protected_session_dir_skipped(self, tmp_path, monkeypatch):
        from mini_agent.evolution import session_cleanup as sc

        session_dir = tmp_path / "orphan_session"
        session_dir.mkdir()
        (session_dir / "dummy.txt").write_text("x", encoding="utf-8")
        (tmp_path / MANIFEST_FILENAME).write_text("orphan_session/\n", encoding="utf-8")

        item = sc.OrphanItem(
            dir_name="orphan_session", last_activity="", size_bytes=0,
            action="delete", reason="test",
        )

        class _FakeSessionManager:
            pass

        monkeypatch.setattr(
            sc, "scan_orphan_session_dirs",
            lambda *a, **k: ([item], [session_dir]),
        )

        kept, deleted, failed = sc.cleanup_orphan_session_dirs(
            _FakeSessionManager(), tmp_path, dry_run=False,
        )
        assert session_dir.exists()  # 未被删除
        assert deleted == []
        assert len(failed) == 1
        assert "受保护" in failed[0].reason


# ── raw_result_cleanup.py ───────────────────────────────────────────────────

class TestRawResultCleanupGuard:
    def test_protected_session_dir_skipped(self, tmp_path):
        from mini_agent.perception import raw_result_cleanup as rrc

        raw_dir = tmp_path / rrc._RAW_RESULTS_SUBDIR
        session_dir = raw_dir / "sess_1"
        session_dir.mkdir(parents=True)
        (session_dir / "f.json").write_text("{}", encoding="utf-8")

        (tmp_path / MANIFEST_FILENAME).write_text(
            f"{rrc._RAW_RESULTS_SUBDIR}/sess_1/\n", encoding="utf-8"
        )

        report = rrc.run_cleanup(
            str(tmp_path), retention_days=-1, apply_cleanup=True,
        )
        assert session_dir.exists()
        assert "sess_1" not in report.cleaned_sessions
        assert any(f.kind == "protected_skipped" for f in report.findings)


# ── cycle_tuning.py ──────────────────────────────────────────────────────────

class TestCycleTuningGuard:
    def test_protected_proposal_dir_skipped(self, tmp_path):
        from mini_agent.perception import cycle_tuning as ct
        from mini_agent.storage.paths import AgentPaths

        paths = AgentPaths(project_root=tmp_path)
        goal_id = "goal_1"
        d = ct._proposal_dir(paths, goal_id)
        d.mkdir(parents=True)
        (d / "p1.json").write_text("{}", encoding="utf-8")

        rel = d.relative_to(tmp_path).as_posix() + "/"
        (tmp_path / MANIFEST_FILENAME).write_text(f"{rel}\n", encoding="utf-8")

        ok = ct.delete_proposals(paths, goal_id)
        assert ok is False
        assert d.exists()


# ── exploration_sandbox.py ──────────────────────────────────────────────────

class TestExplorationSandboxGuard:
    def test_protected_worktree_skipped(self, tmp_path):
        from mini_agent.perception.exploration_sandbox import ExplorationSandbox
        from mini_agent.storage.paths import AgentPaths

        paths = AgentPaths(project_root=tmp_path)
        worktree = tmp_path / "sandbox_wt"
        worktree.mkdir()
        (worktree / "f.txt").write_text("x", encoding="utf-8")

        (tmp_path / MANIFEST_FILENAME).write_text("sandbox_wt/\n", encoding="utf-8")

        sandbox = ExplorationSandbox.__new__(ExplorationSandbox)
        sandbox._paths = paths
        sandbox._cleanup_worktree(worktree)
        assert worktree.exists()  # 未被删除


# ── wiki/quarantine.py ──────────────────────────────────────────────────────

class TestWikiQuarantineGuard:
    def test_protected_page_skipped(self, tmp_path):
        from mini_agent.wiki import quarantine as q
        from mini_agent.storage.paths import AgentPaths

        paths = AgentPaths(project_root=tmp_path)
        page_path = tmp_path / "wiki" / "broken_page.md"
        page_path.parent.mkdir(parents=True)
        page_path.write_text("broken", encoding="utf-8")

        rel = page_path.relative_to(tmp_path).as_posix()
        (tmp_path / MANIFEST_FILENAME).write_text(f"{rel}\n", encoding="utf-8")

        rec = q.QuarantineRecord(
            page_path=str(page_path), error_type="PageParseError",
            error_message="test", status="needs_human",
        )
        q._save_quarantine(paths, {str(page_path): rec})

        report = q.purge_quarantined(paths, dry_run=False)
        assert page_path.exists()
        assert report.protected_skipped == 1
        assert report.deleted == 0
