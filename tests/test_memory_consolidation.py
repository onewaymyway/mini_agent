"""
tests/test_memory_consolidation.py — 方案二：记忆巩固测试

覆盖：
  1. 构造 5 条相似 lesson + 触发淘汰，验证归纳后条目数减少但
     occurrence_count 总和不丢失。
  2. llm_call=None 时验证走"规则拼接"降级路径而非直接失败。
  3. 聚类规模不足 min_group_size 时验证走原有物理淘汰路径（回归不变）。
  4. consolidation_enabled=False 时验证 MemoryStore.add() 行为与改造前一致。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.memory_consolidation import consolidate_before_eviction
from mini_agent.perception.memory_store import MemoryEntry, MemoryStore


def _lesson(trigger: str, occurrence_count: int = 1, session_id: str = "s1") -> MemoryEntry:
    return MemoryEntry(
        session_id=session_id,
        summary=trigger,
        key_outcomes=[],
        tags=[],
        model="m",
        entry_type="lesson",
        trigger=trigger,
        outcome="失败",
        suggested_action="下次先确认",
        confidence=0.6,
        occurrence_count=occurrence_count,
        source="self_reflection",
    )


class TestConsolidateBeforeEviction(unittest.TestCase):
    def test_similar_group_consolidates_and_preserves_occurrence(self):
        entries = [
            _lesson("rm -rf 没确认就执行", occurrence_count=1, session_id=f"s{i}")
            for i in range(5)
        ]
        consolidated, truly_evicted = consolidate_before_eviction(entries, min_group_size=3)
        self.assertEqual(len(consolidated), 1)
        self.assertEqual(len(truly_evicted), 0)
        self.assertEqual(consolidated[0].occurrence_count, 5)
        self.assertEqual(consolidated[0].entry_type, "consolidated_lesson")
        self.assertEqual(consolidated[0].source, "consolidated")

    def test_llm_call_none_uses_rule_based_merge(self):
        entries = [_lesson("venv 版本错误", session_id=f"s{i}") for i in range(3)]
        consolidated, _ = consolidate_before_eviction(entries, llm_call=None, min_group_size=3)
        self.assertEqual(len(consolidated), 1)
        self.assertEqual(consolidated[0].occurrence_count, 3)

    def test_below_min_group_size_falls_back_to_eviction(self):
        entries = [_lesson("独立不相关的问题A"), _lesson("独立不相关的问题B")]
        consolidated, truly_evicted = consolidate_before_eviction(entries, min_group_size=3)
        self.assertEqual(consolidated, [])
        self.assertEqual(len(truly_evicted), 2)

    def test_summary_entries_never_consolidated(self):
        summary_entry = MemoryEntry(
            session_id="s1", summary="session summary", key_outcomes=[], tags=[], model="m",
        )
        consolidated, truly_evicted = consolidate_before_eviction([summary_entry], min_group_size=1)
        self.assertEqual(consolidated, [])
        self.assertEqual(truly_evicted, [summary_entry])


class TestMemoryStoreConsolidationIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "memory.jsonl"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_consolidation_disabled_matches_legacy_eviction(self):
        store = MemoryStore(path=self.path, max_entries=3, consolidation_enabled=False)
        for i in range(5):
            store.add(_lesson(f"不同的问题{i}", session_id=f"s{i}"))
        self.assertEqual(store.count, 3)

    def test_consolidation_enabled_reduces_but_keeps_signal(self):
        store = MemoryStore(path=self.path, max_entries=3, consolidation_enabled=True,
                             consolidation_min_group_size=3)
        for i in range(4):
            store.add(_lesson("rm -rf 没确认就执行", session_id=f"s{i}"))
        store.add(_lesson("完全不相关的新问题", session_id="s_new"))
        # 淘汰不应该报错，条目数应该被控制住
        self.assertLessEqual(store.count, 3)


if __name__ == "__main__":
    unittest.main()
