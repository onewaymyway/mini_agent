"""tests/test_session_cleanup_scope_expansion.py — 会话清理范围扩展
（next_doc/session_cleanup_scope_expansion_plan.md）。

覆盖：
  1. 产出保护：会话 output/ 有文件时不删除，无文件照常删除；dry-run 也统计产出保护数。
  2. 工作流运行目录：终结且超窗口的删除（含子 Agent raw_results 联动）；
     running / 含产出 / 最近 N 个 / 窗口内 保留。
  3. 无 session.json 的运行目录：新目录保留、旧且无产出删除、旧但有产出保留。
  4. 孤立 raw_results：仅 include_orphans=True 时清理；被工作流子 Agent 引用的不算孤立。
  5. dry-run 不删除任何东西。
  6. include_workflow_runs=False 时工作流目录不被触碰。
  7. 外部项目根路径（独立 root）同样生效。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from mini_agent.evolution.session_cleanup import cleanup_sessions
from mini_agent.session import SessionManager
from mini_agent.storage.paths import AgentPaths


OLD_DAYS = 100
OLD_TS = time.time() - OLD_DAYS * 86400


def _iso(days_ago: float) -> str:
    return (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _age_tree(path: Path, ts: float) -> None:
    """把目录内所有文件与目录的 mtime 统一改到 ts（模拟"很久没动"）。"""
    for p in [path, *path.rglob("*")]:
        try:
            os.utime(p, (ts, ts))
        except OSError:
            pass


def _make_chat_session(sessions_dir: Path, sid: str, *, days_ago: float, with_output: bool = False) -> Path:
    d = sessions_dir / sid
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": sid, "title": f"t-{sid}",
        "created_at": _iso(days_ago), "updated_at": _iso(days_ago),
        "provider": "p", "model": "m",
        "stats": {"turns": 1, "input_tokens": 0, "output_tokens": 0, "tool_calls": 0},
        "knowledge_extracted": True,
    }
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / "history.json").write_text("[]", encoding="utf-8")
    if with_output:
        (d / "output").mkdir(exist_ok=True)
        (d / "output" / "report.md").write_text("deliverable", encoding="utf-8")
    _age_tree(d, OLD_TS)
    return d


def _make_run(wf_base: Path, wid: str, *, status: str, days_ago: float,
              with_output: bool = False, nested: tuple = ()) -> Path:
    d = wf_base / wid
    d.mkdir(parents=True, exist_ok=True)
    (d / "session.json").write_text(json.dumps({
        "workflow_session_id": wid, "workflow_name": "wf", "status": status,
        "updated_at": time.time() - days_ago * 86400,
    }), encoding="utf-8")
    (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    for n in nested:
        (d / n).mkdir(parents=True, exist_ok=True)
        (d / n / "raw_history.jsonl").write_text("{}\n", encoding="utf-8")
    if with_output:
        (d / "output").mkdir(exist_ok=True)
        (d / "output" / "result.md").write_text("deliverable", encoding="utf-8")
    _age_tree(d, time.time() - days_ago * 86400)
    return d


def _make_raw(project_root: Path, name: str, *, old: bool = True) -> Path:
    d = project_root / ".agent" / "raw_results" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "tool_1.txt").write_text("raw output " * 10, encoding="utf-8")
    if old:
        _age_tree(d, OLD_TS)
    return d


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        self.paths = AgentPaths(self.root)
        self.sessions_dir = self.paths.sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.mgr = SessionManager(session_dir=self.sessions_dir)
        self.wf_base = self.paths.workflow_sessions_dir
        self.wf_base.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _cleanup(self, **kw):
        kw.setdefault("keep_recent_count", 0)
        kw.setdefault("keep_recent_days", 30)
        kw.setdefault("dry_run", False)
        return cleanup_sessions(self.mgr, self.root, **kw)


class OutputProtectionTests(_Base):
    def test_session_with_output_is_kept_others_deleted(self):
        _make_chat_session(self.sessions_dir, "s_plain", days_ago=OLD_DAYS)
        _make_chat_session(self.sessions_dir, "s_out", days_ago=OLD_DAYS, with_output=True)
        report = self._cleanup(include_workflow_runs=False)
        deleted_ids = {i.session_id for i in report.deleted}
        self.assertIn("s_plain", deleted_ids)
        self.assertNotIn("s_out", deleted_ids)
        self.assertTrue((self.sessions_dir / "s_out" / "output" / "report.md").exists())
        self.assertEqual(report.output_protected_count, 1)

    def test_dry_run_counts_protection_and_deletes_nothing(self):
        _make_chat_session(self.sessions_dir, "s_out", days_ago=OLD_DAYS, with_output=True)
        report = self._cleanup(dry_run=True, include_workflow_runs=False)
        self.assertEqual(report.output_protected_count, 1)
        self.assertTrue((self.sessions_dir / "s_out").exists())


class WorkflowRunCleanupTests(_Base):
    def test_terminal_old_run_deleted_with_nested_raw_results(self):
        _make_run(self.wf_base, "wf_old", status="done", days_ago=OLD_DAYS,
                  nested=("agent_a",))
        _make_raw(self.root, "agent_a")
        report = self._cleanup()
        self.assertEqual([i.run_id for i in report.workflow_deleted], ["wf_old"])
        self.assertFalse((self.wf_base / "wf_old").exists())
        self.assertFalse((self.root / ".agent" / "raw_results" / "agent_a").exists())

    def test_running_paused_and_awaiting_are_kept(self):
        for i, st in enumerate(["running", "paused", "awaiting_approval"]):
            _make_run(self.wf_base, f"wf_{st}", status=st, days_ago=OLD_DAYS)
        report = self._cleanup()
        self.assertEqual(report.workflow_deleted, [])
        self.assertEqual(len(report.workflow_kept), 3)

    def test_run_with_output_is_protected(self):
        _make_run(self.wf_base, "wf_out", status="done", days_ago=OLD_DAYS, with_output=True)
        report = self._cleanup()
        self.assertEqual(report.workflow_deleted, [])
        self.assertTrue((self.wf_base / "wf_out" / "output" / "result.md").exists())
        self.assertEqual(report.output_protected_count, 1)

    def test_recent_days_window_and_recent_count_are_kept(self):
        _make_run(self.wf_base, "wf_recent", status="done", days_ago=2)
        _make_run(self.wf_base, "wf_old", status="failed", days_ago=OLD_DAYS)
        report = self._cleanup(keep_recent_count=1)
        kept = {i.run_id for i in report.workflow_kept}
        self.assertIn("wf_recent", kept)
        self.assertEqual([i.run_id for i in report.workflow_deleted], ["wf_old"])

    def test_orphan_run_dir_without_session_json(self):
        young = self.wf_base / "wf_young"
        young.mkdir()
        (young / "events.jsonl").write_text("{}", encoding="utf-8")
        old = self.wf_base / "wf_old_orphan"
        old.mkdir()
        (old / "events.jsonl").write_text("{}", encoding="utf-8")
        _age_tree(old, OLD_TS)
        old_out = self.wf_base / "wf_old_with_output"
        (old_out / "output").mkdir(parents=True)
        (old_out / "output" / "x.md").write_text("x", encoding="utf-8")
        _age_tree(old_out, OLD_TS)

        report = self._cleanup()
        self.assertFalse(old.exists())
        self.assertTrue(young.exists())
        self.assertTrue(old_out.exists())
        self.assertEqual([i.run_id for i in report.workflow_deleted], ["wf_old_orphan"])

    def test_dry_run_deletes_nothing(self):
        _make_run(self.wf_base, "wf_old", status="done", days_ago=OLD_DAYS,
                  nested=("agent_a",))
        raw = _make_raw(self.root, "agent_a")
        report = self._cleanup(dry_run=True)
        self.assertEqual([i.run_id for i in report.workflow_deleted], ["wf_old"])
        self.assertGreater(report.workflow_deleted[0].raw_results_bytes, 0)
        self.assertTrue((self.wf_base / "wf_old").exists())
        self.assertTrue(raw.exists())

    def test_include_workflow_runs_false_leaves_them_alone(self):
        _make_run(self.wf_base, "wf_old", status="done", days_ago=OLD_DAYS)
        report = self._cleanup(include_workflow_runs=False)
        self.assertEqual(report.workflow_total_scanned, 0)
        self.assertTrue((self.wf_base / "wf_old").exists())


class OrphanRawResultsTests(_Base):
    def test_unreferenced_raw_is_deleted_only_with_include_orphans(self):
        ghost = _make_raw(self.root, "ghost_session")
        report = self._cleanup(include_workflow_runs=False, include_orphans=False)
        self.assertTrue(ghost.exists())
        self.assertEqual(report.raw_orphan_deleted, [])

        report = self._cleanup(include_workflow_runs=False, include_orphans=True)
        self.assertFalse(ghost.exists())
        self.assertEqual([i.dir_name for i in report.raw_orphan_deleted], ["ghost_session"])

    def test_raw_referenced_by_workflow_child_agent_is_not_orphan(self):
        _make_run(self.wf_base, "wf_live", status="running", days_ago=OLD_DAYS,
                  nested=("child_agent",))
        child_raw = _make_raw(self.root, "child_agent")
        report = self._cleanup(include_orphans=True)
        self.assertTrue(child_raw.exists())
        self.assertNotIn("child_agent", [i.dir_name for i in report.raw_orphan_deleted])

    def test_fresh_raw_is_kept_even_if_unreferenced(self):
        fresh = _make_raw(self.root, "fresh_ghost", old=False)
        report = self._cleanup(include_orphans=True)
        self.assertTrue(fresh.exists())
        self.assertEqual(report.raw_orphan_deleted, [])


class ExternalRootTests(_Base):
    def test_external_project_root_gets_same_rules(self):
        # cleanup_sessions 接收的 project_root 决定 workflow_sessions 位置，
        # 外部项目传入自己的根目录即可，与主项目互不影响。
        ext = Path(self._tmp.name) / "ext_proj"
        ext_paths = AgentPaths(ext)
        ext_paths.sessions_dir.mkdir(parents=True, exist_ok=True)
        ext_wf = ext_paths.workflow_sessions_dir
        ext_wf.mkdir(parents=True, exist_ok=True)
        _make_run(ext_wf, "wf_ext_old", status="cancelled", days_ago=OLD_DAYS)
        _make_run(self.wf_base, "wf_main_old", status="done", days_ago=OLD_DAYS)

        mgr = SessionManager(session_dir=ext_paths.sessions_dir)
        report = cleanup_sessions(
            mgr, ext, keep_recent_count=0, keep_recent_days=30, dry_run=False,
        )
        self.assertEqual([i.run_id for i in report.workflow_deleted], ["wf_ext_old"])
        self.assertFalse((ext_wf / "wf_ext_old").exists())
        # 主项目的工作流目录不受外部项目清理影响
        self.assertTrue((self.wf_base / "wf_main_old").exists())


if __name__ == "__main__":
    unittest.main()
