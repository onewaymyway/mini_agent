"""tests/test_candidate_queue_triage.py — P1 候选队列过期巡检测试。

覆盖：
  1. 文件不存在时直接返回空摘要，不报错
  2. 超过 TTL 的 pending 候选被标记为 expired，写入 expired_at
  3. 未超过 TTL 的 pending 候选保持不变
  4. 已经是 confirmed/dismissed 状态的候选不受影响
  5. 单行损坏的记录原样保留，不中断整批处理
  6. ensure_candidate_queue_triage_job() 正确注册 job 与本地回调 handler，
     且 handler 触发后能实际执行一次巡检
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.evolution.candidate_queue_triage import (
    JOB_ID,
    STALE_PENDING_TTL_SECONDS,
    run_candidate_queue_triage_once,
    ensure_candidate_queue_triage_job,
)
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_candidates(paths: AgentPaths, records: list[dict]) -> None:
    p = paths.notification_novelty_candidates
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _read_candidates(paths: AgentPaths) -> list[dict]:
    p = paths.notification_novelty_candidates
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestCandidateQueueTriage(unittest.TestCase):
    def test_missing_file_returns_empty_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            summary = run_candidate_queue_triage_once(paths)
            self.assertEqual(summary.scanned, 0)
            self.assertEqual(summary.expired, 0)
            self.assertTrue(summary.ok)

    def test_stale_pending_marked_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            _write_candidates(paths, [
                {
                    "candidate_id": "c1",
                    "status": "pending",
                    "created_at": now - STALE_PENDING_TTL_SECONDS - 3600,
                },
            ])
            summary = run_candidate_queue_triage_once(paths)
            self.assertEqual(summary.scanned, 1)
            self.assertEqual(summary.expired, 1)
            records = _read_candidates(paths)
            self.assertEqual(records[0]["status"], "expired")
            self.assertIn("expired_at", records[0])

    def test_fresh_pending_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            _write_candidates(paths, [
                {"candidate_id": "c1", "status": "pending", "created_at": now - 3600},
            ])
            summary = run_candidate_queue_triage_once(paths)
            self.assertEqual(summary.expired, 0)
            records = _read_candidates(paths)
            self.assertEqual(records[0]["status"], "pending")
            self.assertNotIn("expired_at", records[0])

    def test_confirmed_and_dismissed_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            old = time.time() - STALE_PENDING_TTL_SECONDS - 3600
            _write_candidates(paths, [
                {"candidate_id": "c1", "status": "confirmed", "created_at": old},
                {"candidate_id": "c2", "status": "dismissed", "created_at": old},
            ])
            summary = run_candidate_queue_triage_once(paths)
            self.assertEqual(summary.expired, 0)
            records = _read_candidates(paths)
            statuses = {r["candidate_id"]: r["status"] for r in records}
            self.assertEqual(statuses["c1"], "confirmed")
            self.assertEqual(statuses["c2"], "dismissed")

    def test_corrupt_line_preserved_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            old = time.time() - STALE_PENDING_TTL_SECONDS - 3600
            p = paths.notification_novelty_candidates
            p.parent.mkdir(parents=True, exist_ok=True)
            good = json.dumps({"candidate_id": "c1", "status": "pending", "created_at": old})
            p.write_text(good + "\n" + "{not valid json\n", encoding="utf-8")
            summary = run_candidate_queue_triage_once(paths)
            self.assertEqual(summary.scanned, 1)
            self.assertEqual(summary.expired, 1)
            lines = p.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[1], "{not valid json")

    def test_ensure_job_registers_and_handler_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            old = time.time() - STALE_PENDING_TTL_SECONDS - 3600
            _write_candidates(paths, [
                {"candidate_id": "c1", "status": "pending", "created_at": old},
            ])
            scheduler = CronScheduler(paths)
            newly_added = ensure_candidate_queue_triage_job(paths, scheduler)
            self.assertTrue(newly_added)
            job = next(j for j in scheduler.list_jobs() if j.id == JOB_ID)
            self.assertTrue(job.enabled)
            self.assertEqual(job.schedule, "interval:86400")

            ok = scheduler.run_now(JOB_ID)
            self.assertTrue(ok)
            records = _read_candidates(paths)
            self.assertEqual(records[0]["status"], "expired")

            # 第二次调用应复用已存在的 job，不重复新建
            newly_added_again = ensure_candidate_queue_triage_job(paths, scheduler)
            self.assertFalse(newly_added_again)


if __name__ == "__main__":
    unittest.main()
