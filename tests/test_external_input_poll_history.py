"""tests/test_external_input_poll_history.py — §3 外部输入网关可观测性
（成功率/延迟趋势）测试。

覆盖：
  1. summarize_poll_history 对空文件的处理
  2. 单条记录的聚合结果
  3. 跨天分桶（timeline）
  4. since_days 边界过滤
  5. 滚动截断逻辑（超过上限只保留最近 N 条）
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.external_input.poll_history import (
    _MAX_LOG_LINES,
    append_poll_record,
    summarize_poll_history,
)
from mini_agent.storage.paths import AgentPaths


class TestPollHistory(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_empty_file_returns_empty_summary(self):
        result = summarize_poll_history(self.paths, source_id="hn_frontpage")
        self.assertEqual(result["total_polls"], 0)
        self.assertIsNone(result["success_rate"])
        self.assertEqual(result["timeline"], [])

    def test_single_record_aggregation(self):
        append_poll_record(
            self.paths, source_id="hn_frontpage", ok=True,
            duration_ms=842.3, event_count=3,
        )
        result = summarize_poll_history(self.paths, source_id="hn_frontpage")
        self.assertEqual(result["total_polls"], 1)
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["success_rate"], 1.0)
        self.assertAlmostEqual(result["avg_duration_ms"], 842.3, places=1)

    def test_cross_day_bucketing(self):
        now = time.time()
        append_poll_record(self.paths, source_id="s1", ok=True, duration_ms=100, ts=now - 86400)
        append_poll_record(self.paths, source_id="s1", ok=False, duration_ms=200, ts=now)
        result = summarize_poll_history(self.paths, source_id="s1", since_days=7)
        self.assertEqual(len(result["timeline"]), 2)
        self.assertEqual(result["total_polls"], 2)
        self.assertEqual(result["failure_count"], 1)

    def test_since_days_boundary_excludes_older_records(self):
        now = time.time()
        append_poll_record(self.paths, source_id="s1", ok=True, duration_ms=10, ts=now - 30 * 86400)
        append_poll_record(self.paths, source_id="s1", ok=True, duration_ms=10, ts=now)
        result = summarize_poll_history(self.paths, source_id="s1", since_days=7)
        self.assertEqual(result["total_polls"], 1)

        result_all = summarize_poll_history(self.paths, source_id="s1", since_days=90)
        self.assertEqual(result_all["total_polls"], 2)

    def test_no_source_id_groups_by_source(self):
        append_poll_record(self.paths, source_id="s1", ok=True, duration_ms=10)
        append_poll_record(self.paths, source_id="s2", ok=False, duration_ms=20, error="boom")
        result = summarize_poll_history(self.paths)
        self.assertIn("s1", result["sources"])
        self.assertIn("s2", result["sources"])
        self.assertEqual(result["sources"]["s2"]["failure_count"], 1)

    def test_rotation_truncates_to_max_lines(self):
        p = self.paths.external_input_poll_history
        p.parent.mkdir(parents=True, exist_ok=True)
        # 预先写入超过上限的行数，绕开节流检查点直接验证截断函数本身。
        lines = [
            json.dumps({"source_id": "s1", "ts": time.time(), "ok": True, "duration_ms": 1, "event_count": 0, "error": None})
            for _ in range(_MAX_LOG_LINES + 500)
        ]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

        from mini_agent.external_input.poll_history import _maybe_rotate, _rotate_counter
        _rotate_counter["n"] = 0
        _maybe_rotate(p, check_every=1)
        remaining = p.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(remaining), _MAX_LOG_LINES)


if __name__ == "__main__":
    unittest.main()
