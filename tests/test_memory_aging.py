"""
tests/test_memory_aging.py — [具身改进 C2] 时间加权记忆激活测试

覆盖：
  1. compute_half_life_days：按 source 区分基准半衰期（human_feedback 最慢，
     revert_record 最快），occurrence_count 加成，非 lesson 条目走默认值
  2. compute_decay_factor：age=0 时接近 1.0，age 增加时单调递减
  3. MemoryStore._score_all 集成：human_feedback lesson 比 revert_record
     lesson 衰减得慢（同样的 age，前者排序应更靠前/分数更高）
"""

from __future__ import annotations

import math
import time
import unittest

from mini_agent.evolution.memory_aging import (
    DEFAULT_HALF_LIFE_DAYS,
    compute_decay_factor,
    compute_half_life_days,
)
from mini_agent.perception.memory_store import MemoryEntry


def _lesson(source: str, occurrence_count: int = 1, age_days: float = 0.0) -> MemoryEntry:
    e = MemoryEntry(
        session_id="s1",
        summary="",
        key_outcomes=[],
        tags=[],
        model="m",
        entry_type="lesson",
        source=source,
        occurrence_count=occurrence_count,
        trigger="测试触发",
        outcome="测试结果",
    )
    e.created_at = time.time() - age_days * 86400.0
    return e


class TestComputeHalfLifeDays(unittest.TestCase):
    def test_human_feedback_has_longest_base_half_life(self):
        hf = compute_half_life_days(_lesson("human_feedback"))
        sr = compute_half_life_days(_lesson("self_reflection"))
        rr = compute_half_life_days(_lesson("revert_record"))
        self.assertGreater(hf, sr)
        self.assertGreater(sr, rr)

    def test_occurrence_count_extends_half_life(self):
        single = compute_half_life_days(_lesson("self_reflection", occurrence_count=1))
        repeated = compute_half_life_days(_lesson("self_reflection", occurrence_count=5))
        self.assertGreater(repeated, single)

    def test_occurrence_multiplier_is_capped(self):
        huge = compute_half_life_days(_lesson("self_reflection", occurrence_count=1000))
        moderate = compute_half_life_days(_lesson("self_reflection", occurrence_count=50))
        # 封顶倍数后，二者应该相等（都达到 _MAX_OCCURRENCE_MULTIPLIER）
        self.assertEqual(huge, moderate)

    def test_non_lesson_entry_uses_default(self):
        summary_entry = MemoryEntry(
            session_id="s1", summary="x", key_outcomes=[], tags=[], model="m",
            entry_type="summary",
        )
        self.assertEqual(compute_half_life_days(summary_entry), DEFAULT_HALF_LIFE_DAYS)

    def test_unknown_source_falls_back_to_default(self):
        e = _lesson("totally_unknown_source")
        self.assertEqual(compute_half_life_days(e), DEFAULT_HALF_LIFE_DAYS)


class TestComputeDecayFactor(unittest.TestCase):
    def test_zero_age_decay_near_one(self):
        e = _lesson("human_feedback", age_days=0.0)
        self.assertAlmostEqual(compute_decay_factor(e), 1.0, places=2)

    def test_decay_monotonically_decreases_with_age(self):
        fresh = compute_decay_factor(_lesson("self_reflection", age_days=1.0))
        old = compute_decay_factor(_lesson("self_reflection", age_days=60.0))
        self.assertGreater(fresh, old)

    def test_half_life_age_gives_half_decay(self):
        # self_reflection 基准半衰期 30 天（occurrence=1，无加成）
        e = _lesson("self_reflection", occurrence_count=1, age_days=30.0)
        self.assertAlmostEqual(compute_decay_factor(e), 0.5, places=2)

    def test_human_feedback_decays_slower_than_revert_record(self):
        same_age = 20.0
        hf_decay = compute_decay_factor(_lesson("human_feedback", age_days=same_age))
        rr_decay = compute_decay_factor(_lesson("revert_record", age_days=same_age))
        self.assertGreater(hf_decay, rr_decay)


class TestMemoryStoreIntegration(unittest.TestCase):
    """验证 MemoryStore._score_all 集成路径（通过 search() 间接测试）。"""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from mini_agent.perception.memory_store import MemoryStore

        self._tmpdir = tempfile.mkdtemp()
        self.store = MemoryStore(path=Path(self._tmpdir) / "memory.jsonl")

    def test_human_feedback_lesson_outranks_revert_record_at_same_age(self):
        old_age = 25.0  # 接近 revert_record 的半衰期(14d)的近两倍，远小于 human_feedback 的(90d)

        hf = _lesson("human_feedback", age_days=old_age)
        hf.trigger = "处理大文件读取"
        hf.outcome = "读取大文件导致 context 溢出"

        rr = _lesson("revert_record", age_days=old_age)
        rr.trigger = "处理大文件读取"
        rr.outcome = "读取大文件导致 context 溢出"

        self.store.add(hf)
        self.store.add(rr)

        results = self.store.search("大文件读取 context 溢出", k=2)
        self.assertEqual(len(results), 2)
        # human_feedback 衰减更慢，相同检索文本下应该排在前面
        self.assertEqual(results[0].source, "human_feedback")


if __name__ == "__main__":
    unittest.main()
