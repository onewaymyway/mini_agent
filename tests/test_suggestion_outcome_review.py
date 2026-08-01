"""tests/test_suggestion_outcome_review.py — 自诊断闭环深化 P2 测试。

覆盖：
  1. 没有落在回看窗口内的 health_report 不产出任何回看结果
  2. 窗口内基线 + 当前无调用记录 → no_action_taken
  3. 窗口内基线 + 当前失败率明显下降 → improved
  4. 窗口内基线 + 当前失败率明显上升 → worse
  5. 同一基线不会被重复回看（去重状态生效）
  6. ensure_suggestion_outcome_review_job() 正确注册 job 与本地回调 handler
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.evolution.suggestion_outcome_review import (
    JOB_ID,
    REVIEW_MIN_AGE_DAYS,
    run_suggestion_outcome_review_once,
    ensure_suggestion_outcome_review_job,
)
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_health_report(paths: AgentPaths, at: float, tool_name: str, failure_rate: float) -> None:
    p = paths.workdir_dir / "activity_digest.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "health_report",
        "at": at,
        "stale_tools": [{"tool_name": tool_name, "failure_rate": failure_rate}],
        "stale_skills": [],
        "conflicting_lessons": [],
    }
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_traces(paths: AgentPaths, session_id: str, tool_name: str, calls: int, errors: int) -> None:
    sess_dir = paths.sessions_dir / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    traces_path = sess_dir / "traces.jsonl"
    lines = []
    for i in range(calls):
        lines.append(json.dumps({
            "phase": "tool_call",
            "tool_name": tool_name,
            "is_error": i < errors,
        }))
    traces_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


class TestSuggestionOutcomeReview(unittest.TestCase):
    def test_no_baselines_in_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            # 太新，不在窗口内
            _write_health_report(paths, at=now - 1 * 86400, tool_name="search_web", failure_rate=0.8)
            summary = run_suggestion_outcome_review_once(paths)
            self.assertTrue(summary.ok)
            self.assertEqual(summary.findings, [])

    def test_no_action_taken_when_not_called_since(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            baseline_at = now - (REVIEW_MIN_AGE_DAYS + 1) * 86400
            _write_health_report(paths, at=baseline_at, tool_name="search_web", failure_rate=0.8)
            summary = run_suggestion_outcome_review_once(paths)
            self.assertEqual(len(summary.findings), 1)
            self.assertEqual(summary.findings[0].verdict, "no_action_taken")

    def test_improved_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            baseline_at = now - (REVIEW_MIN_AGE_DAYS + 1) * 86400
            _write_health_report(paths, at=baseline_at, tool_name="search_web", failure_rate=0.8)
            _write_traces(paths, "s1", "search_web", calls=10, errors=1)  # 0.1 失败率
            summary = run_suggestion_outcome_review_once(paths)
            self.assertEqual(summary.findings[0].verdict, "improved")

    def test_worse_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            baseline_at = now - (REVIEW_MIN_AGE_DAYS + 1) * 86400
            _write_health_report(paths, at=baseline_at, tool_name="search_web", failure_rate=0.2)
            _write_traces(paths, "s1", "search_web", calls=10, errors=9)  # 0.9 失败率
            summary = run_suggestion_outcome_review_once(paths)
            self.assertEqual(summary.findings[0].verdict, "worse")

    def test_same_baseline_not_reviewed_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            baseline_at = now - (REVIEW_MIN_AGE_DAYS + 1) * 86400
            _write_health_report(paths, at=baseline_at, tool_name="search_web", failure_rate=0.8)
            first = run_suggestion_outcome_review_once(paths)
            self.assertEqual(len(first.findings), 1)
            second = run_suggestion_outcome_review_once(paths)
            self.assertEqual(len(second.findings), 0)

    def test_ensure_job_registers_local_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths)
            newly_added = ensure_suggestion_outcome_review_job(paths, scheduler)
            self.assertTrue(newly_added)
            job_ids = {j.id for j in scheduler.list_jobs()}
            self.assertIn(JOB_ID, job_ids)
            newly_added_again = ensure_suggestion_outcome_review_job(paths, scheduler)
            self.assertFalse(newly_added_again)


if __name__ == "__main__":
    unittest.main()
