"""tests/test_improvement_backlog_merge.py — 自诊断闭环深化 P1 测试。

覆盖：
  1. 四路信号源都为空时，返回空 backlog，不报错
  2. self_maintenance 的 health_report 记录能被正确解析为 BacklogItem
  3. 跨信号源命中同一 subject 时，分数比单一来源更高（跨源加分生效）
  4. 单个信号源读取抛异常不影响其它信号源正常产出（隔离性）
  5. 结果落盘为 improvement_backlog.json，可通过 load_improvement_backlog() 读回
  6. ensure_improvement_backlog_merge_job() 正确注册 job 与本地回调 handler
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mini_agent.evolution.improvement_backlog_merge import (
    JOB_ID,
    run_improvement_backlog_merge_once,
    ensure_improvement_backlog_merge_job,
    load_improvement_backlog,
)
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_health_report(paths: AgentPaths, stale_tools=None, stale_skills=None) -> None:
    p = paths.workdir_dir / "activity_digest.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "health_report",
        "at": 1_700_000_000.0,
        "stale_tools": stale_tools or [],
        "stale_skills": stale_skills or [],
        "conflicting_lessons": [],
    }
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


class TestImprovementBacklogMerge(unittest.TestCase):
    def test_all_sources_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            summary = run_improvement_backlog_merge_once(paths)
            self.assertTrue(summary.ok)
            self.assertEqual(summary.items, [])
            self.assertEqual(len(summary.sources_read), 4)

    def test_self_maintenance_findings_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_health_report(
                paths,
                stale_tools=[{"tool_name": "search_web", "failure_rate": 0.8}],
            )
            summary = run_improvement_backlog_merge_once(paths)
            subjects = [i.subject for i in summary.items]
            self.assertIn("tool:search_web", subjects)

    def test_cross_source_bonus_ranks_higher(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_health_report(
                paths,
                stale_tools=[{"tool_name": "search_web", "failure_rate": 0.8}],
                stale_skills=[{"skill_name": "docx", "last_used_days_ago": 40}],
            )
            with mock.patch(
                "mini_agent.evolution.improvement_backlog_merge._read_gap_scanner_findings",
                return_value=[],
            ), mock.patch(
                "mini_agent.evolution.improvement_backlog_merge._read_decommission_findings",
                return_value=[],
            ), mock.patch(
                "mini_agent.evolution.improvement_backlog_merge._read_self_model_findings",
                return_value=[
                    __import__(
                        "mini_agent.evolution.improvement_backlog_merge", fromlist=["BacklogItem"]
                    ).BacklogItem(
                        subject="tool:search_web",
                        source="self_model",
                        kind="weak_capability",
                        summary="重复命中测试",
                        detected_at=1_700_000_000.0,
                    )
                ],
            ):
                summary = run_improvement_backlog_merge_once(paths)
            by_subject = {i.subject: i for i in summary.items}
            self.assertGreater(
                by_subject["tool:search_web"].score,
                by_subject["skill:docx"].score,
            )

    def test_single_source_failure_does_not_block_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_health_report(
                paths, stale_tools=[{"tool_name": "search_web", "failure_rate": 0.8}]
            )
            with mock.patch(
                "mini_agent.evolution.improvement_backlog_merge._read_gap_scanner_findings",
                side_effect=RuntimeError("boom"),
            ):
                summary = run_improvement_backlog_merge_once(paths)
            self.assertFalse(summary.ok)
            self.assertTrue(any("gap_scanner_failed" in e for e in summary.errors))
            subjects = [i.subject for i in summary.items]
            self.assertIn("tool:search_web", subjects)

    def test_state_persisted_and_loadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_health_report(
                paths, stale_tools=[{"tool_name": "search_web", "failure_rate": 0.8}]
            )
            run_improvement_backlog_merge_once(paths)
            self.assertTrue(paths.improvement_backlog_path.exists())
            loaded = load_improvement_backlog(paths)
            self.assertTrue(any(i["subject"] == "tool:search_web" for i in loaded))

    def test_ensure_job_registers_local_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            scheduler = CronScheduler(paths)
            newly_added = ensure_improvement_backlog_merge_job(paths, scheduler)
            self.assertTrue(newly_added)
            job_ids = {j.id for j in scheduler.list_jobs()}
            self.assertIn(JOB_ID, job_ids)
            # 二次调用应识别为已存在，不重复视为新建
            newly_added_again = ensure_improvement_backlog_merge_job(paths, scheduler)
            self.assertFalse(newly_added_again)


if __name__ == "__main__":
    unittest.main()
