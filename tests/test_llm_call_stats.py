"""
tests/test_llm_call_stats.py

覆盖 next_doc/kanban_perception_gaps_improvement_plan.md 方向 B.2
（轻量 LLM 调用计数）新增的 `llm/call_stats.py`。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_llm_call_stats.py -q
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.storage.paths import AgentPaths
from mini_agent.llm import call_stats


class TestRecordCallAndFlush(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(project_root=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_below_batch_size_not_flushed_until_forced(self):
        for _ in range(3):
            call_stats.record_call(self.paths, provider="anthropic", model="claude-x", outcome="success")
        # 未达到 _BATCH_SIZE（10），也未超过 flush 间隔，此时文件应该还没有写入。
        self.assertFalse(self.paths.llm_call_stats_path.exists())
        call_stats.flush_now(self.paths)
        self.assertTrue(self.paths.llm_call_stats_path.exists())
        rows = call_stats._read_jsonl(self.paths.llm_call_stats_path)
        self.assertEqual(len(rows), 3)

    def test_batch_size_triggers_auto_flush(self):
        for _ in range(call_stats._BATCH_SIZE):
            call_stats.record_call(self.paths, provider="anthropic", model="claude-x", outcome="success")
        rows = call_stats._read_jsonl(self.paths.llm_call_stats_path)
        self.assertEqual(len(rows), call_stats._BATCH_SIZE)

    def test_record_call_never_raises_on_bad_paths(self):
        class BrokenPaths:
            @property
            def project_root(self):
                raise RuntimeError("boom")
        # 不应该抛出异常，静默失败。
        call_stats.record_call(BrokenPaths(), provider="x", model="y")


class TestAggregationAndCompaction(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(project_root=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _write_raw_rows(self, rows: list[dict]):
        call_stats._write_jsonl(self.paths.llm_call_stats_path, rows)

    def test_call_stats_series_empty_when_no_file(self):
        self.assertEqual(call_stats.call_stats_series(self.paths), [])

    def test_call_stats_series_aggregates_by_day(self):
        now = time.time()
        rows = [
            {"ts": now, "provider": "a", "model": "m", "input_tokens": 10, "output_tokens": 5, "duration_ms": 100, "outcome": "success"},
            {"ts": now, "provider": "a", "model": "m", "input_tokens": 20, "output_tokens": 8, "duration_ms": 200, "outcome": "success"},
            {"ts": now, "provider": "a", "model": "m", "input_tokens": 0, "output_tokens": 0, "duration_ms": 50, "outcome": "error"},
        ]
        self._write_raw_rows(rows)
        series = call_stats.call_stats_series(self.paths, days=7)
        self.assertEqual(len(series), 1)
        day = series[0]
        self.assertEqual(day["call_count"], 3)
        self.assertEqual(day["success_count"], 2)
        self.assertEqual(day["error_count"], 1)
        self.assertEqual(day["total_input_tokens"], 30)
        self.assertEqual(day["total_output_tokens"], 13)
        self.assertAlmostEqual(day["avg_duration_ms"], (100 + 200 + 50) / 3, places=1)

    def test_series_outside_window_excluded(self):
        old_ts = time.time() - 30 * 86400
        self._write_raw_rows([
            {"ts": old_ts, "provider": "a", "model": "m", "outcome": "success"},
        ])
        series = call_stats.call_stats_series(self.paths, days=7)
        self.assertEqual(series, [])

    def test_compact_moves_old_rows_into_daily_aggregate(self):
        now = time.time()
        old_ts = now - (call_stats._RAW_WINDOW_DAYS + 2) * 86400
        recent_ts = now
        self._write_raw_rows([
            {"ts": old_ts, "provider": "a", "model": "m", "input_tokens": 1, "output_tokens": 1, "duration_ms": 10, "outcome": "success"},
            {"ts": old_ts + 60, "provider": "a", "model": "m", "input_tokens": 2, "output_tokens": 2, "duration_ms": 20, "outcome": "error"},
            {"ts": recent_ts, "provider": "a", "model": "m", "input_tokens": 3, "output_tokens": 3, "duration_ms": 30, "outcome": "success"},
        ])
        removed = call_stats.compact_call_stats_storage(self.paths, now=now)
        self.assertEqual(removed, 2)
        rows = call_stats._read_jsonl(self.paths.llm_call_stats_path)
        # 2 条旧记录压缩成 1 条汇总行，1 条最近记录保留原样，共 2 行。
        self.assertEqual(len(rows), 2)
        aggregate_rows = [r for r in rows if r.get("is_daily_aggregate")]
        self.assertEqual(len(aggregate_rows), 1)
        self.assertEqual(aggregate_rows[0]["call_count"], 2)
        self.assertEqual(aggregate_rows[0]["success_count"], 1)
        self.assertEqual(aggregate_rows[0]["error_count"], 1)

    def test_compact_is_idempotent(self):
        now = time.time()
        old_ts = now - (call_stats._RAW_WINDOW_DAYS + 1) * 86400
        self._write_raw_rows([
            {"ts": old_ts, "provider": "a", "model": "m", "outcome": "success"},
        ])
        first = call_stats.compact_call_stats_storage(self.paths, now=now)
        self.assertEqual(first, 1)
        second = call_stats.compact_call_stats_storage(self.paths, now=now)
        self.assertEqual(second, 0)

    def test_no_old_rows_returns_zero_without_rewrite(self):
        now = time.time()
        self._write_raw_rows([{"ts": now, "provider": "a", "model": "m", "outcome": "success"}])
        removed = call_stats.compact_call_stats_storage(self.paths, now=now)
        self.assertEqual(removed, 0)

    def test_series_after_compaction_matches_pre_compaction(self):
        """降采样压缩前后，同一个 7 天窗口的聚合结果应该一致（近似，因为
        压缩只影响窗口外的旧数据，不应该改变窗口内的查询结果）。"""
        now = time.time()
        recent_ts = now - 1 * 86400
        self._write_raw_rows([
            {"ts": recent_ts, "provider": "a", "model": "m", "input_tokens": 5, "output_tokens": 2, "duration_ms": 40, "outcome": "success"},
        ])
        before = call_stats.call_stats_series(self.paths, days=7)
        call_stats.compact_call_stats_storage(self.paths, now=now)
        after = call_stats.call_stats_series(self.paths, days=7)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
