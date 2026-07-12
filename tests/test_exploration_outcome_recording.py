"""
tests/test_exploration_outcome_recording.py — 方案三：探索结果回写记忆测试

覆盖：
  1. 探索失败时也生成 lesson memory 条目（而不是只有成功才写）。
  2. 探索成功时同样生成 lesson memory 条目。
  3. memory_backend=None 时不报错（静默跳过）。
"""

from __future__ import annotations

import time
import unittest

from mini_agent.perception.exploration_sandbox import ExplorationReport, ExplorationSandbox


class _FakeMemoryBackend:
    def __init__(self):
        self.added = []

    def add(self, entry):
        self.added.append(entry)


class TestExplorationOutcomeRecording(unittest.TestCase):
    def _make_sandbox(self, memory_backend):
        sandbox = ExplorationSandbox.__new__(ExplorationSandbox)
        sandbox._memory_backend = memory_backend
        return sandbox

    def test_failed_exploration_writes_lesson(self):
        mem = _FakeMemoryBackend()
        sandbox = self._make_sandbox(mem)
        report = ExplorationReport(
            sandbox_id="explore_1",
            capability_id="cap_x",
            goal="验证方案可行性",
            started_at=time.time(),
            ended_at=time.time(),
            success=False,
            error="方案不可行",
        )
        sandbox._record_exploration_outcome(report)
        self.assertEqual(len(mem.added), 1)
        entry = mem.added[0]
        self.assertEqual(entry.entry_type, "lesson")
        self.assertEqual(entry.source, "exploration")

    def test_successful_exploration_writes_lesson(self):
        mem = _FakeMemoryBackend()
        sandbox = self._make_sandbox(mem)
        report = ExplorationReport(
            sandbox_id="explore_2",
            capability_id="cap_y",
            goal="验证方案可行性",
            started_at=time.time(),
            ended_at=time.time(),
            success=True,
            finding="方案可行",
        )
        sandbox._record_exploration_outcome(report)
        self.assertEqual(len(mem.added), 1)
        self.assertEqual(mem.added[0].confidence, 0.7)

    def test_no_memory_backend_is_noop(self):
        sandbox = self._make_sandbox(None)
        report = ExplorationReport(
            sandbox_id="explore_3", capability_id="cap_z", goal="g",
        )
        # 不应抛出任何异常
        sandbox._record_exploration_outcome(report)


if __name__ == "__main__":
    unittest.main()
