"""
tests/test_hybrid_memory_backend.py — 方案一：HybridMemoryBackend 测试

覆盖：
  1. embed_call=None 时 search() 结果与纯 MemoryStore.search() 逐条一致（回归保证）。
  2. mock embedding 调用，验证语义召回能找到 TF-IDF 召回不到的条目。
  3. embedding 调用抛异常时自动降级，不影响返回结果。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.perception.memory_store import MemoryEntry, MemoryStore
from mini_agent.perception.hybrid_memory_backend import HybridMemoryBackend


def _lesson(trigger: str, outcome: str = "", session_id: str = "s1") -> MemoryEntry:
    return MemoryEntry(
        session_id=session_id,
        summary=trigger,
        key_outcomes=[],
        tags=[],
        model="m",
        entry_type="lesson",
        trigger=trigger,
        outcome=outcome,
        suggested_action="",
        confidence=0.6,
        occurrence_count=1,
        source="self_reflection",
    )


class TestHybridMemoryBackendRegression(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "memory.jsonl"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_no_embed_call_matches_plain_store(self):
        store = MemoryStore(path=self.path, consolidation_enabled=False)
        store.add(_lesson("数据库连接失败"))
        store.add(_lesson("接口超时"))

        hybrid = HybridMemoryBackend(inner=store, embed_call=None)

        plain_results = store.search("数据库连接失败", k=3)
        hybrid_results = hybrid.search("数据库连接失败", k=3)
        self.assertEqual(
            [e.entry_id for e in plain_results],
            [e.entry_id for e in hybrid_results],
        )

    def test_semantic_recall_finds_tfidf_miss(self):
        store = MemoryStore(path=self.path, consolidation_enabled=False)
        e1 = _lesson("接口超时")
        e2 = _lesson("API 调用挂起")
        store.add(e1)
        store.add(e2)

        # 构造一个假的 embedding：让 "接口超时" 的 query 向量与
        # "API 调用挂起" 条目的向量高度相似（模拟语义相近但字面不同）。
        def fake_embed(text: str):
            if "接口超时" in text or "API 调用挂起" in text:
                return [1.0, 0.0]
            return [0.0, 1.0]

        hybrid = HybridMemoryBackend(
            inner=store, embed_call=fake_embed, tfidf_weight=0.0, embedding_weight=1.0
        )
        results = hybrid.search("接口超时", k=2)
        result_ids = {e.entry_id for e in results}
        self.assertIn(e2.entry_id, result_ids)

    def test_embedding_failure_degrades_gracefully(self):
        store = MemoryStore(path=self.path, consolidation_enabled=False)
        store.add(_lesson("数据库连接失败"))

        def broken_embed(text: str):
            raise RuntimeError("model not loaded")

        hybrid = HybridMemoryBackend(inner=store, embed_call=broken_embed)
        results = hybrid.search("数据库连接失败", k=3)
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
