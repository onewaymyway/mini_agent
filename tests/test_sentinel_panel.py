"""
tests/test_sentinel_panel.py

覆盖 next_doc/kanban_perception_gaps_improvement_plan.md 第一期
（S1 LLM 故障转移状态暴露 / S2 wiki 隔离区暴露 / S3 哨兵聚合面板 /
S4 仲裁状态聚合统计）新增的：

- evolution/resource_arbiter.py::gating_ratio_summary()
- perception/sentinel.py 全部扫描函数 + sentinel_summary()

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_sentinel_panel.py -q
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.resource_arbiter import (
    record_gating_transition,
    gating_ratio_summary,
)
from mini_agent.perception.sentinel import (
    _scan_cron_consecutive_failures,
    _scan_objective_retry_hotspots,
    _scan_quarantine_backlog,
    read_llm_pool_snapshot,
    sentinel_summary,
)


class TestGatingRatioSummary(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(project_root=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_history_returns_zero_ratios(self):
        result = gating_ratio_summary(self.paths, window_days=7)
        self.assertEqual(result["ratios"], {"full": 0.0, "degraded": 0.0, "blocked": 0.0})
        self.assertFalse(result["incomplete"])

    def test_all_full_history_ratio_is_full(self):
        record_gating_transition(self.paths, "full", "正常")
        result = gating_ratio_summary(self.paths, window_days=7)
        self.assertAlmostEqual(result["ratios"]["full"], 1.0, places=2)
        self.assertEqual(result["ratios"]["degraded"], 0.0)
        self.assertEqual(result["ratios"]["blocked"], 0.0)

    def test_ratios_sum_to_approximately_one(self):
        record_gating_transition(self.paths, "full", "正常")
        record_gating_transition(self.paths, "degraded", "疲劳度上升")
        record_gating_transition(self.paths, "full", "恢复")
        result = gating_ratio_summary(self.paths, window_days=7)
        total = sum(result["ratios"].values())
        self.assertAlmostEqual(total, 1.0, places=2)
        for v in result["ratios"].values():
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_manually_crafted_history_degraded_ratio(self):
        # 手写一段跨度较大的历史，让 degraded 区间可控地占窗口的一半。
        now = time.time()
        window_days = 2.0
        window_seconds = window_days * 86400
        entries = [
            {"at": now - window_seconds, "at_str": "", "state": "full", "reason": "start"},
            {"at": now - window_seconds / 2, "at_str": "", "state": "degraded", "reason": "mid"},
        ]
        history_path = self.paths.gating_history_path
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        result = gating_ratio_summary(self.paths, window_days=window_days)
        self.assertAlmostEqual(result["ratios"]["full"], 0.5, delta=0.05)
        self.assertAlmostEqual(result["ratios"]["degraded"], 0.5, delta=0.05)


class TestCronConsecutiveFailuresScan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(project_root=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _write_job(self, job_id: str, consecutive_failures: int, enabled: bool = True, name: str = None):
        safe_id = job_id.replace(":", "_")
        job_dir = Path(self.paths.project_root) / ".agent" / "cron_jobs" / safe_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "state.json").write_text(
            json.dumps({"consecutive_failures": consecutive_failures, "last_error": "boom", "status": "needs_human_review"}),
            encoding="utf-8",
        )
        jobs_path = self.paths.workdir_dir / "cron_jobs.json"
        self.paths.workdir_dir.mkdir(parents=True, exist_ok=True)
        data = {"jobs": []}
        if jobs_path.exists():
            data = json.loads(jobs_path.read_text(encoding="utf-8"))
        data["jobs"].append({"id": job_id, "name": name or job_id, "enabled": enabled})
        jobs_path.write_text(json.dumps(data), encoding="utf-8")

    def test_below_threshold_not_reported(self):
        self._write_job("sys:foo", consecutive_failures=1)
        result = _scan_cron_consecutive_failures(self.paths, threshold=2)
        self.assertEqual(result, [])

    def test_at_or_above_threshold_reported_with_metadata(self):
        self._write_job("sys:foo", consecutive_failures=3, enabled=True, name="Foo Job")
        result = _scan_cron_consecutive_failures(self.paths, threshold=2)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["job_id"], "sys:foo")
        self.assertEqual(result[0]["name"], "Foo Job")
        self.assertTrue(result[0]["enabled"])
        self.assertEqual(result[0]["consecutive_failures"], 3)

    def test_no_cron_jobs_dir_returns_empty(self):
        result = _scan_cron_consecutive_failures(self.paths)
        self.assertEqual(result, [])


class TestObjectiveRetryHotspotScan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(project_root=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _write_executions(self, executions: list[dict]):
        self.paths.workdir_dir.mkdir(parents=True, exist_ok=True)
        exec_path = self.paths.workdir_dir / "objective_executions.json"
        exec_path.write_text(json.dumps({"version": 1, "executions": executions}), encoding="utf-8")

    def test_no_file_returns_empty(self):
        self.assertEqual(_scan_objective_retry_hotspots(self.paths), [])

    def test_running_execution_near_retry_limit_flagged(self):
        from mini_agent.evolution.objective_executor import MAX_STEP_RETRIES

        self._write_executions([{
            "execution_id": "ex1", "objective_id": "obj1", "title": "Test Objective",
            "status": "running",
            "steps": [
                {"step_id": "s1", "retry_count": MAX_STEP_RETRIES - 1, "description": "step one", "error_msg": "timeout"},
                {"step_id": "s2", "retry_count": 0, "description": "step two", "error_msg": ""},
            ],
        }])
        result = _scan_objective_retry_hotspots(self.paths)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["execution_id"], "ex1")
        self.assertEqual(result[0]["hot_step_count"], 1)

    def test_completed_execution_not_flagged(self):
        from mini_agent.evolution.objective_executor import MAX_STEP_RETRIES

        self._write_executions([{
            "execution_id": "ex1", "objective_id": "obj1", "title": "Done",
            "status": "completed",
            "steps": [{"step_id": "s1", "retry_count": MAX_STEP_RETRIES, "description": "x", "error_msg": ""}],
        }])
        self.assertEqual(_scan_objective_retry_hotspots(self.paths), [])

    def test_low_retry_count_not_flagged(self):
        self._write_executions([{
            "execution_id": "ex1", "objective_id": "obj1", "title": "Fine",
            "status": "running",
            "steps": [{"step_id": "s1", "retry_count": 0, "description": "x", "error_msg": ""}],
        }])
        self.assertEqual(_scan_objective_retry_hotspots(self.paths), [])


class TestQuarantineBacklogScan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(project_root=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_quarantine_returns_zero(self):
        result = _scan_quarantine_backlog(self.paths)
        self.assertEqual(result["pending_count"], 0)
        self.assertIsNone(result["earliest_first_seen_at"])

    def test_pending_records_counted_repaired_excluded(self):
        from mini_agent.wiki.quarantine import record_issue, resolve_if_present

        page1 = Path(self.paths.project_root) / "wiki" / "a.md"
        page2 = Path(self.paths.project_root) / "wiki" / "b.md"
        record_issue(self.paths, page1, ValueError("bad frontmatter"))
        record_issue(self.paths, page2, ValueError("bad links"))
        result = _scan_quarantine_backlog(self.paths)
        self.assertEqual(result["pending_count"], 2)

        # 修复其中一条后，积压数应该减少（自愈确认走 resolve_if_present）。
        resolve_if_present(self.paths, page1)
        result2 = _scan_quarantine_backlog(self.paths)
        self.assertEqual(result2["pending_count"], 1)


class TestLLMPoolSnapshot(unittest.TestCase):
    def test_none_pool_returns_none(self):
        self.assertIsNone(read_llm_pool_snapshot(None))

    def test_snapshot_reports_switched_from_preferred(self):
        class FakePool:
            def snapshot(self):
                return {
                    "current": 1,
                    "entries": [
                        {"label": "openai/gpt-x", "active": False},
                        {"label": "anthropic/claude-x", "active": True, "keys": [
                            {"key_suffix": "abcd1234", "available": True, "cooldown_remaining": 0.0, "fail_count": 2},
                        ]},
                    ],
                }
        snap = read_llm_pool_snapshot(FakePool())
        self.assertTrue(snap["switched_from_preferred"])
        self.assertEqual(snap["current"], 1)

    def test_snapshot_on_preferred_not_switched(self):
        class FakePool:
            def snapshot(self):
                return {"current": 0, "entries": [{"label": "openai/gpt-x", "active": True}]}
        snap = read_llm_pool_snapshot(FakePool())
        self.assertFalse(snap["switched_from_preferred"])


class TestSentinelSummary(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(project_root=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_state_returns_zero_total(self):
        result = sentinel_summary(self.paths)
        self.assertEqual(result["total_count"], 0)
        self.assertEqual(result["cron_jobs_with_failures"], [])
        self.assertEqual(result["stuck_objective_steps"], [])
        self.assertEqual(result["quarantine_backlog"]["pending_count"], 0)
        self.assertIsNone(result["llm_failover_state"])

    def test_total_count_aggregates_all_sources(self):
        # cron 失败
        job_dir = Path(self.paths.project_root) / ".agent" / "cron_jobs" / "sys_foo"
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "state.json").write_text(json.dumps({"consecutive_failures": 3}), encoding="utf-8")
        self.paths.workdir_dir.mkdir(parents=True, exist_ok=True)
        (self.paths.workdir_dir / "cron_jobs.json").write_text(
            json.dumps({"jobs": [{"id": "sys:foo", "name": "Foo", "enabled": True}]}), encoding="utf-8",
        )
        # wiki 隔离区
        from mini_agent.wiki.quarantine import record_issue
        record_issue(self.paths, Path(self.paths.project_root) / "wiki" / "a.md", ValueError("bad"))

        class FakePool:
            def snapshot(self):
                return {"current": 1, "entries": [{"label": "a/b", "active": False}, {"label": "c/d", "active": True}]}

        result = sentinel_summary(self.paths, client_pool=FakePool())
        self.assertEqual(len(result["cron_jobs_with_failures"]), 1)
        self.assertEqual(result["quarantine_backlog"]["pending_count"], 1)
        self.assertTrue(result["llm_failover_state"]["switched_from_preferred"])
        # 1 cron + 1 quarantine + 1 llm switched = 3
        self.assertEqual(result["total_count"], 3)


if __name__ == "__main__":
    unittest.main()
