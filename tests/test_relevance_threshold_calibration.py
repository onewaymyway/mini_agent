"""tests/test_relevance_threshold_calibration.py — P3 阈值自校准测试。

覆盖：
  1. 状态文件不存在时返回默认阈值，不落盘
  2. 样本不足时跳过调整，但游标前移
  3. 仍在 warmup 期内时跳过调整，但游标前移
  4. relevant_rate 过低 → 阈值调高（收紧）
  5. relevant_rate 过高 → 阈值调低（放松）
  6. relevant_rate 处于健康区间 → 不调整
  7. 阈值调整遵守上下限
  8. 解析失败（无 relevant 字段）的候选不计入样本
  9. reset_relevance_threshold() 人工回滚逃生通道
  10. ensure_relevance_threshold_calibration_job() 注册与本地回调触发
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.evolution.relevance_threshold_calibration import (
    JOB_ID,
    MIN_SAMPLE_SIZE,
    MIN_WARMUP_SECONDS,
    THRESHOLD_MAX,
    THRESHOLD_MIN,
    ensure_relevance_threshold_calibration_job,
    load_calibrated_threshold,
    load_calibration_state,
    reset_relevance_threshold,
    run_relevance_threshold_calibration_once,
)
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.external_input.goal_relevance import DEFAULT_PREFILTER_THRESHOLD
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_candidates(paths: AgentPaths, records: list[dict]) -> None:
    p = paths.external_input_goal_relevance_candidates
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _make_records(n: int, relevant: bool, base_ts: float) -> list[dict]:
    return [
        {
            "id": f"cand:{i}",
            "judged": True,
            "relevant": relevant,
            "created_at": base_ts + i,
        }
        for i in range(n)
    ]


def _seed_warmed_up_state(paths: AgentPaths) -> None:
    """写一份已经过了 warmup 期的初始状态，供需要跳过 warmup 门槛的用例复用。"""
    p = paths.external_input_relevance_threshold_state
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "current_threshold": DEFAULT_PREFILTER_THRESHOLD,
        "created_at": time.time() - MIN_WARMUP_SECONDS - 3600,
        "last_calibrated_at": None,
        "last_reviewed_created_at": 0.0,
        "history": [],
    }), encoding="utf-8")


class TestRelevanceThresholdCalibration(unittest.TestCase):
    def test_missing_state_returns_default_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(load_calibrated_threshold(paths), DEFAULT_PREFILTER_THRESHOLD)
            self.assertFalse(paths.external_input_relevance_threshold_state.exists())

    def test_insufficient_samples_skips_adjustment_but_advances_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_warmed_up_state(paths)
            now = time.time()
            _write_candidates(paths, _make_records(MIN_SAMPLE_SIZE - 1, True, now))

            summary = run_relevance_threshold_calibration_once(paths)
            self.assertFalse(summary.adjusted)
            self.assertEqual(summary.reason, "insufficient_samples")

            state = load_calibration_state(paths)
            self.assertGreater(state.last_reviewed_created_at, 0.0)

    def test_within_warmup_period_skips_adjustment(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            # 不预置状态：首次运行 created_at = now，明显在 warmup 期内。
            _write_candidates(paths, _make_records(MIN_SAMPLE_SIZE + 5, False, now))

            summary = run_relevance_threshold_calibration_once(paths)
            self.assertFalse(summary.adjusted)
            self.assertEqual(summary.reason, "warmup")

    def test_low_relevant_rate_tightens_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_warmed_up_state(paths)
            now = time.time()
            # 全部 relevant=False → relevant_rate = 0 < LOW_HEALTHY_RATE
            _write_candidates(paths, _make_records(MIN_SAMPLE_SIZE, False, now))

            summary = run_relevance_threshold_calibration_once(paths)
            self.assertTrue(summary.adjusted)
            self.assertEqual(summary.reason, "relevant_rate_too_low_tighten")
            self.assertGreater(summary.new_threshold, summary.old_threshold)

            state = load_calibration_state(paths)
            self.assertEqual(state.current_threshold, summary.new_threshold)
            self.assertEqual(len(state.history), 1)

    def test_high_relevant_rate_loosens_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_warmed_up_state(paths)
            now = time.time()
            # 全部 relevant=True → relevant_rate = 1.0 > HIGH_HEALTHY_RATE
            _write_candidates(paths, _make_records(MIN_SAMPLE_SIZE, True, now))

            summary = run_relevance_threshold_calibration_once(paths)
            self.assertTrue(summary.adjusted)
            self.assertEqual(summary.reason, "relevant_rate_too_high_loosen")
            self.assertLess(summary.new_threshold, summary.old_threshold)

    def test_healthy_range_no_adjustment(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_warmed_up_state(paths)
            now = time.time()
            # 30% relevant，落在 [0.15, 0.5] 健康区间内。
            n_relevant = int(MIN_SAMPLE_SIZE * 0.3)
            records = _make_records(n_relevant, True, now) + _make_records(
                MIN_SAMPLE_SIZE - n_relevant, False, now + 1000,
            )
            _write_candidates(paths, records)

            summary = run_relevance_threshold_calibration_once(paths)
            self.assertFalse(summary.adjusted)
            self.assertEqual(summary.reason, "within_healthy_range")

    def test_threshold_respects_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            p = paths.external_input_relevance_threshold_state
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({
                "current_threshold": THRESHOLD_MAX,
                "created_at": time.time() - MIN_WARMUP_SECONDS - 3600,
                "last_calibrated_at": None,
                "last_reviewed_created_at": 0.0,
                "history": [],
            }), encoding="utf-8")
            now = time.time()
            _write_candidates(paths, _make_records(MIN_SAMPLE_SIZE, False, now))

            summary = run_relevance_threshold_calibration_once(paths)
            # 已经在上限：调整量被 clamp 抵消，new_threshold == old_threshold，
            # 因此不算作一次"发生了变化"的调整，但阈值本身始终不超过上限。
            self.assertFalse(summary.adjusted)
            self.assertLessEqual(load_calibration_state(paths).current_threshold, THRESHOLD_MAX)

            # 反方向也测一下下限
            p.write_text(json.dumps({
                "current_threshold": THRESHOLD_MIN,
                "created_at": time.time() - MIN_WARMUP_SECONDS - 3600,
                "last_calibrated_at": None,
                "last_reviewed_created_at": max(
                    r["created_at"] for r in _make_records(MIN_SAMPLE_SIZE, False, now)
                ),
                "history": [],
            }), encoding="utf-8")
            _write_candidates(paths, _make_records(MIN_SAMPLE_SIZE, True, now + 100000))
            summary2 = run_relevance_threshold_calibration_once(paths)
            self.assertFalse(summary2.adjusted)
            self.assertGreaterEqual(load_calibration_state(paths).current_threshold, THRESHOLD_MIN)

    def test_unjudged_or_unparsed_candidates_excluded_from_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_warmed_up_state(paths)
            now = time.time()
            records = _make_records(MIN_SAMPLE_SIZE - 1, True, now)
            # 未判定的候选
            records.append({"id": "cand:unjudged", "judged": False, "created_at": now + 9999})
            # 判定过但解析失败、没有 relevant 字段的候选
            records.append({"id": "cand:parse_failed", "judged": True, "created_at": now + 9998})
            _write_candidates(paths, records)

            summary = run_relevance_threshold_calibration_once(paths)
            self.assertEqual(summary.sample_size, MIN_SAMPLE_SIZE - 1)
            self.assertFalse(summary.adjusted)
            self.assertEqual(summary.reason, "insufficient_samples")

    def test_reset_relevance_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_warmed_up_state(paths)
            now = time.time()
            _write_candidates(paths, _make_records(MIN_SAMPLE_SIZE, False, now))
            run_relevance_threshold_calibration_once(paths)
            state_before = load_calibration_state(paths)
            self.assertNotEqual(state_before.current_threshold, DEFAULT_PREFILTER_THRESHOLD)

            reset_state = reset_relevance_threshold(paths)
            self.assertEqual(reset_state.current_threshold, DEFAULT_PREFILTER_THRESHOLD)
            self.assertEqual(len(reset_state.history), 1)
            self.assertEqual(reset_state.history[0]["reason"], "manual_reset")

            state_after = load_calibration_state(paths)
            self.assertEqual(state_after.current_threshold, DEFAULT_PREFILTER_THRESHOLD)

    def test_ensure_job_registers_and_handler_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _seed_warmed_up_state(paths)
            now = time.time()
            _write_candidates(paths, _make_records(MIN_SAMPLE_SIZE, False, now))

            scheduler = CronScheduler(paths)
            newly_added = ensure_relevance_threshold_calibration_job(paths, scheduler)
            self.assertTrue(newly_added)
            job = next(j for j in scheduler.list_jobs() if j.id == JOB_ID)
            self.assertTrue(job.enabled)
            self.assertEqual(job.schedule, "interval:604800")

            ok = scheduler.run_now(JOB_ID)
            self.assertTrue(ok)
            state = load_calibration_state(paths)
            self.assertNotEqual(state.current_threshold, DEFAULT_PREFILTER_THRESHOLD)

            newly_added_again = ensure_relevance_threshold_calibration_job(paths, scheduler)
            self.assertFalse(newly_added_again)


if __name__ == "__main__":
    unittest.main()
