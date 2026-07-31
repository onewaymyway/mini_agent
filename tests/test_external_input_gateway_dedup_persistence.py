"""tests/test_external_input_gateway_dedup_persistence.py — §1 兜底去重
缓存持久化测试。

覆盖：
  1. to_list/from_list 往返一致性
  2. 写入 N 条 → 重建缓存 → load() → 缓存命中之前写入的 key
  3. 节流逻辑：连续 add() 少于 _SAVE_EVERY_N 次且未超时不应该触发文件写
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.external_input.gateway import _RecentIdCache
from mini_agent.storage.paths import AgentPaths


class TestRecentIdCachePersistence(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_to_list_from_list_round_trip(self):
        cache = _RecentIdCache(maxlen=10)
        for i in range(5):
            cache.add(f"key:{i}")
        keys = cache.to_list()
        self.assertEqual(keys, [f"key:{i}" for i in range(5)])

        restored = _RecentIdCache(maxlen=10)
        restored.from_list(keys)
        for k in keys:
            self.assertTrue(restored.seen(k))
        self.assertEqual(restored.to_list(), keys)

    def test_from_list_respects_maxlen(self):
        cache = _RecentIdCache(maxlen=3)
        cache.from_list([f"key:{i}" for i in range(10)])
        # 只保留最近 3 个（更早写入的视为丢弃）
        self.assertEqual(cache.to_list(), ["key:7", "key:8", "key:9"])

    def test_load_after_rebuild_hits_previous_keys(self):
        cache = _RecentIdCache(maxlen=50)
        for i in range(30):
            cache.add(f"src:evt{i}")
        cache.save(self.paths)

        rebuilt = _RecentIdCache(maxlen=50)
        rebuilt.load(self.paths)
        for i in range(30):
            self.assertTrue(rebuilt.seen(f"src:evt{i}"))
        self.assertFalse(rebuilt.seen("src:evt_never_seen"))

    def test_load_missing_or_corrupt_file_is_empty_cache(self):
        cache = _RecentIdCache(maxlen=10)
        cache.load(self.paths)  # 文件不存在
        self.assertEqual(cache.to_list(), [])

        p = self.paths.external_input_gateway_dedup_cache
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not a valid json{{{", encoding="utf-8")
        cache2 = _RecentIdCache(maxlen=10)
        cache2.load(self.paths)  # 解析失败也不抛异常
        self.assertEqual(cache2.to_list(), [])

    def test_maybe_save_throttles_writes_below_threshold_and_interval(self):
        cache = _RecentIdCache(maxlen=100)
        p = self.paths.external_input_gateway_dedup_cache

        # 连续 add 少于 _SAVE_EVERY_N（20）次，且时间上没有超过
        # _SAVE_INTERVAL_SECONDS（30s，测试内不会真的等待），不应该落盘。
        for i in range(5):
            cache.add(f"k{i}")
            cache.maybe_save(self.paths)
        self.assertFalse(p.exists())

        # 强制 save() 无论如何都会落盘。
        cache.save(self.paths)
        self.assertTrue(p.exists())

    def test_maybe_save_writes_after_reaching_count_threshold(self):
        cache = _RecentIdCache(maxlen=100)
        p = self.paths.external_input_gateway_dedup_cache
        for i in range(25):
            cache.add(f"k{i}")
            cache.maybe_save(self.paths)
        # 达到 _SAVE_EVERY_N=20 次新增后应该已经触发过落盘
        self.assertTrue(p.exists())


if __name__ == "__main__":
    unittest.main()
