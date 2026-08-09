"""
tests/test_objective_completion_trend.py

覆盖 next_doc/kanban_perception_gaps_improvement_plan.md 方向 D.1
（Objective 完成率趋势）+ 方向 D.3 风险 1（通用每日快照存储小工具）
新增的 `perception/daily_snapshot.py` 与 `evolution/objective_trend.py`。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_objective_completion_trend.py -q
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.storage.paths import AgentPaths
from mini_agent.perception import daily_snapshot as ds
from mini_agent.evolution import objective_trend as ot


class TestDailySnapshotHelper(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "test_trend.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_and_read_series(self):
        ds.append_daily_snapshot(self.path, {"recorded_at": 100.0, "value": 1})
        ds.append_daily_snapshot(self.path, {"recorded_at": 200.0, "value": 2})
        series = ds.read_daily_snapshot_series(self.path, limit=10)
        self.assertEqual([r["value"] for r in series], [1, 2])

    def test_read_series_respects_limit_and_order(self):
        for i in range(5):
            ds.append_daily_snapshot(self.path, {"recorded_at": float(i), "value": i})
        series = ds.read_daily_snapshot_series(self.path, limit=2)
        self.assertEqual([r["value"] for r in series], [3, 4])

    def test_compact_no_old_rows_returns_zero(self):
        now = time.time()
        ds.append_daily_snapshot(self.path, {"recorded_at": now, "value": 1})
        removed = ds.compact_daily_snapshot_storage(self.path, raw_window_days=60, now=now)
        self.assertEqual(removed, 0)

    def test_compact_keeps_latest_per_day_bucket(self):
        now = time.time()
        old_day_start = (int(now // 86400) - 100) * 86400
        # 同一天两条旧记录，压缩后只应保留 recorded_at 更大的那条。
        ds.append_daily_snapshot(self.path, {"recorded_at": old_day_start + 10, "value": "first"})
        ds.append_daily_snapshot(self.path, {"recorded_at": old_day_start + 20, "value": "second"})
        ds.append_daily_snapshot(self.path, {"recorded_at": now, "value": "recent"})
        removed = ds.compact_daily_snapshot_storage(self.path, raw_window_days=60, now=now)
        self.assertEqual(removed, 1)
        series = ds.read_daily_snapshot_series(self.path, limit=10)
        values = [r["value"] for r in series]
        self.assertEqual(values, ["second", "recent"])

    def test_compact_is_idempotent(self):
        now = time.time()
        old_ts = now - 100 * 86400
        ds.append_daily_snapshot(self.path, {"recorded_at": old_ts, "value": 1})
        first = ds.compact_daily_snapshot_storage(self.path, raw_window_days=60, now=now)
        self.assertEqual(first, 0)  # 只有一条旧记录，桶内只有它自己，不构成"压缩掉"
        second = ds.compact_daily_snapshot_storage(self.path, raw_window_days=60, now=now)
        self.assertEqual(second, 0)

    def test_empty_file_returns_empty_series(self):
        self.assertEqual(ds.read_daily_snapshot_series(self.path), [])


class TestObjectiveCompletionSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(project_root=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _write_executions(self, executions: list[dict]):
        self.paths.workdir_dir.mkdir(parents=True, exist_ok=True)
        exec_path = self.paths.workdir_dir / "objective_executions.json"
        exec_path.write_text(json.dumps({"version": 1, "executions": executions}), encoding="utf-8")

    def test_no_file_returns_zero_counts(self):
        snap = ot.compute_objective_completion_snapshot(self.paths)
        self.assertEqual(snap["objectives_completed_today"], 0)
        self.assertEqual(snap["objectives_failed_today"], 0)
        self.assertEqual(snap["avg_retry_count"], 0.0)
        self.assertEqual(snap["active_goals_count"], 0)

    def test_counts_completed_and_failed_within_today(self):
        now = time.time()
        self._write_executions([
            {"execution_id": "e1", "status": "completed", "finished_at": now, "steps": []},
            {"execution_id": "e2", "status": "failed", "finished_at": now, "steps": []},
            {"execution_id": "e3", "status": "running", "finished_at": 0.0, "steps": []},
        ])
        snap = ot.compute_objective_completion_snapshot(self.paths, now=now)
        self.assertEqual(snap["objectives_completed_today"], 1)
        self.assertEqual(snap["objectives_failed_today"], 1)
        self.assertEqual(snap["active_goals_count"], 1)

    def test_completed_outside_today_not_counted(self):
        now = time.time()
        yesterday = now - 2 * 86400
        self._write_executions([
            {"execution_id": "e1", "status": "completed", "finished_at": yesterday, "steps": []},
        ])
        snap = ot.compute_objective_completion_snapshot(self.paths, now=now)
        self.assertEqual(snap["objectives_completed_today"], 0)

    def test_avg_retry_count_computed_across_all_steps(self):
        now = time.time()
        self._write_executions([
            {"execution_id": "e1", "status": "running", "finished_at": 0.0, "steps": [
                {"retry_count": 2}, {"retry_count": 4},
            ]},
        ])
        snap = ot.compute_objective_completion_snapshot(self.paths, now=now)
        self.assertEqual(snap["avg_retry_count"], 3.0)

    def test_active_goals_count_includes_paused_variants(self):
        now = time.time()
        self._write_executions([
            {"execution_id": "e1", "status": "pending", "finished_at": 0.0, "steps": []},
            {"execution_id": "e2", "status": "paused", "finished_at": 0.0, "steps": []},
            {"execution_id": "e3", "status": "paused_for_fairness", "finished_at": 0.0, "steps": []},
            {"execution_id": "e4", "status": "paused_by_user", "finished_at": 0.0, "steps": []},
            {"execution_id": "e5", "status": "cancelled", "finished_at": now, "steps": []},
        ])
        snap = ot.compute_objective_completion_snapshot(self.paths, now=now)
        self.assertEqual(snap["active_goals_count"], 4)

    def test_record_and_read_series_roundtrip(self):
        now = time.time()
        self._write_executions([
            {"execution_id": "e1", "status": "completed", "finished_at": now, "steps": []},
        ])
        row = ot.record_objective_completion_snapshot(self.paths)
        self.assertEqual(row["objectives_completed_today"], 1)
        series = ot.objective_completion_trend_series(self.paths, limit=10)
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["objectives_completed_today"], 1)

    def test_compact_delegates_to_daily_snapshot_helper(self):
        now = time.time()
        old_ts = now - 100 * 86400
        ds.append_daily_snapshot(self.paths.objective_completion_trend_path, {
            "recorded_at": old_ts, "objectives_completed_today": 1,
            "objectives_failed_today": 0, "avg_retry_count": 0.0, "active_goals_count": 0,
        })
        ds.append_daily_snapshot(self.paths.objective_completion_trend_path, {
            "recorded_at": old_ts + 10, "objectives_completed_today": 2,
            "objectives_failed_today": 0, "avg_retry_count": 0.0, "active_goals_count": 0,
        })
        removed = ot.compact_objective_completion_trend_storage(self.paths, now=now)
        self.assertEqual(removed, 1)


if __name__ == "__main__":
    unittest.main()
