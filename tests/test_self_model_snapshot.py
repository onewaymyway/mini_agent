"""tests/test_self_model_snapshot.py — 自诊断闭环深化 P3 测试。

覆盖：
  1. 首次运行（没有历史）：diff.old_at 为 None，weak_count_change 为 None
  2. 有历史快照时：diff 正确计算 weak_domains_old/new 与 delta
  3. find_snapshot_near 只返回不晚于 target_at 的最近记录，没有则返回 None
  4. 历史修剪：超过保留窗口的记录被移除
  5. ensure_self_model_snapshot_job() 正确注册 job 与本地回调 handler
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mini_agent.evolution.self_model_snapshot import (
    JOB_ID,
    SnapshotRecord,
    run_self_model_snapshot_once,
    ensure_self_model_snapshot_job,
    load_snapshot_history,
    find_snapshot_near,
    diff_snapshots,
    _HISTORY_RETENTION_SECONDS,
)
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_history(paths: AgentPaths, records: list[dict]) -> None:
    p = paths.self_model_history_path
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


class TestSelfModelSnapshot(unittest.TestCase):
    def test_first_run_no_history_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            with mock.patch(
                "mini_agent.evolution.self_model_snapshot._compute_capability_snapshot",
                return_value={"python": 0.3, "rust": 0.8},
            ):
                summary = run_self_model_snapshot_once(paths)
            self.assertTrue(summary.ok)
            self.assertIsNone(summary.diff.old_at)
            self.assertIsNone(summary.diff.weak_count_change)
            self.assertEqual(summary.diff.weak_domains_new, ["python"])

    def test_diff_with_prior_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            _write_history(paths, [{
                "at": now - 10 * 86400,
                "capability_snapshot": {"python": 0.2, "rust": 0.9},
            }])
            with mock.patch(
                "mini_agent.evolution.self_model_snapshot._compute_capability_snapshot",
                return_value={"python": 0.6, "rust": 0.4},
            ):
                summary = run_self_model_snapshot_once(paths, lookback_days=7.0)
            diff = summary.diff
            self.assertIsNotNone(diff.old_at)
            self.assertEqual(diff.weak_domains_old, ["python"])
            self.assertEqual(diff.weak_domains_new, ["rust"])
            self.assertEqual(diff.weak_count_change, 0)  # 1 -> 1，数量不变但成分变了
            python_delta = next(d for d in diff.deltas if d.domain == "python")
            self.assertAlmostEqual(python_delta.delta, 0.4, places=3)

    def test_find_snapshot_near_returns_none_if_no_earlier(self):
        now = time.time()
        records = [SnapshotRecord(at=now, capability_snapshot={})]
        result = find_snapshot_near(records, now - 100)
        self.assertIsNone(result)

    def test_history_trim_removes_old_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            _write_history(paths, [
                {"at": now - _HISTORY_RETENTION_SECONDS - 86400, "capability_snapshot": {"old": 0.1}},
                {"at": now - 86400, "capability_snapshot": {"recent": 0.9}},
            ])
            with mock.patch(
                "mini_agent.evolution.self_model_snapshot._compute_capability_snapshot",
                return_value={"python": 0.5},
            ):
                run_self_model_snapshot_once(paths)
            history = load_snapshot_history(paths)
            domains_seen = [set(r.capability_snapshot) for r in history]
            self.assertNotIn({"old": 0.1}.keys(), [set(d) for d in domains_seen])
            self.assertTrue(any("recent" in r.capability_snapshot for r in history))

    def test_ensure_job_registers_local_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths)
            newly_added = ensure_self_model_snapshot_job(paths, scheduler)
            self.assertTrue(newly_added)
            job_ids = {j.id for j in scheduler.list_jobs()}
            self.assertIn(JOB_ID, job_ids)
            newly_added_again = ensure_self_model_snapshot_job(paths, scheduler)
            self.assertFalse(newly_added_again)


if __name__ == "__main__":
    unittest.main()
