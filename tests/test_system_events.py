"""
tests/test_system_events.py — 跨子系统事件总线（perception/system_events.py）测试

覆盖：
  1. publish / poll_since 基本往返
  2. 游标推进：同一 consumer 二次 poll 读不到已消费事件
  3. 不同 consumer 游标互相独立
  4. tier / event_type 过滤
  5. advance_cursor=False 不推进游标（供 /diagnostics 只读查看场景）
  6. 非法 tier 拒绝发布
  7. 滚动归档：超过阈值大小后主文件被清空、内容进归档目录
  8. 并发写入不交错（多线程各发一批，行数与内容完整）
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from mini_agent.perception import system_events as se
from mini_agent.storage.paths import AgentPaths


class TestSystemEventsBasic(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_publish_and_poll_roundtrip(self):
        se.publish(
            self.paths, source="session:a", event_type="proprioception.frustration_spike",
            tier="instant", payload={"frustration": 0.6},
        )
        events = se.poll_since(self.paths, consumer_name="c1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "proprioception.frustration_spike")
        self.assertEqual(events[0].payload["frustration"], 0.6)

    def test_cursor_advances_and_does_not_reread(self):
        se.publish(self.paths, source="s", event_type="e1", tier="tick")
        first = se.poll_since(self.paths, consumer_name="c1")
        self.assertEqual(len(first), 1)

        second = se.poll_since(self.paths, consumer_name="c1")
        self.assertEqual(len(second), 0, "同一 consumer 二次 poll 不应读到已消费事件")

    def test_independent_consumers_have_independent_cursors(self):
        se.publish(self.paths, source="s", event_type="e1", tier="tick")
        se.publish(self.paths, source="s", event_type="e2", tier="cron")

        c1_first = se.poll_since(self.paths, consumer_name="c1")
        self.assertEqual(len(c1_first), 2)

        # c2 从未消费过，即便 c1 已经推进游标，c2 依然能读到全部历史事件
        c2_first = se.poll_since(self.paths, consumer_name="c2")
        self.assertEqual(len(c2_first), 2)

    def test_tier_filter(self):
        se.publish(self.paths, source="s", event_type="e1", tier="instant")
        se.publish(self.paths, source="s", event_type="e2", tier="cron")

        instant_only = se.poll_since(self.paths, consumer_name="c1", tiers=["instant"])
        self.assertEqual(len(instant_only), 1)
        self.assertEqual(instant_only[0].event_type, "e1")

    def test_event_type_filter(self):
        se.publish(self.paths, source="s", event_type="memory.sparse_region_detected", tier="tick")
        se.publish(self.paths, source="s", event_type="evolution.outcome_negative", tier="cron")

        filtered = se.poll_since(
            self.paths, consumer_name="c1", event_types=["evolution.outcome_negative"],
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].event_type, "evolution.outcome_negative")

    def test_advance_cursor_false_is_read_only(self):
        se.publish(self.paths, source="s", event_type="e1", tier="tick")

        peek = se.poll_since(self.paths, consumer_name="c1", advance_cursor=False)
        self.assertEqual(len(peek), 1)

        # 游标没被推进，真正的消费者应该还能读到同一条事件
        real_read = se.poll_since(self.paths, consumer_name="c1")
        self.assertEqual(len(real_read), 1)

    def test_invalid_tier_rejected(self):
        with self.assertRaises(ValueError):
            se.publish(self.paths, source="s", event_type="e1", tier="not_a_real_tier")

    def test_publish_failure_does_not_raise(self):
        """events.jsonl 所在目录不可写时，publish 应静默失败而不是抛异常
        （事件发布是旁路增强，不应拖垮调用方主流程）。"""
        import stat

        workdir = self.paths.workdir_dir
        workdir.mkdir(parents=True, exist_ok=True)
        # 让 workdir_dir 本身不可写，触发 mkdir/open 失败路径
        original_mode = workdir.stat().st_mode
        try:
            workdir.chmod(stat.S_IREAD)
            evt = se.publish(self.paths, source="s", event_type="e1", tier="tick")
            self.assertIsInstance(evt, se.SystemEvent)  # 返回值仍然是合法对象，只是没落盘成功
        finally:
            workdir.chmod(original_mode)


class TestSystemEventsRotation(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_rotation_triggered_by_size(self):
        # 临时调低滚动阈值，避免测试里真写 10MB 数据
        original_threshold = se._ROTATE_SIZE_BYTES
        se._ROTATE_SIZE_BYTES = 500  # 约几条事件就能触发
        try:
            for i in range(30):
                se.publish(
                    self.paths, source="s", event_type=f"e{i}", tier="tick",
                    payload={"padding": "x" * 50},
                )
            self.assertTrue(self.paths.system_events_archive_dir.exists())
            archived = list(self.paths.system_events_archive_dir.glob("*.jsonl"))
            self.assertGreater(len(archived), 0, "应该产生至少一个归档文件")
        finally:
            se._ROTATE_SIZE_BYTES = original_threshold


class TestSystemEventsConcurrency(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_concurrent_publish_no_interleaving(self):
        """多线程并发 publish，events.jsonl 每一行都必须是完整、可解析的 JSON
        ——验证跨平台文件锁确实生效，不会出现两个线程的内容写串行。"""
        n_threads = 8
        n_events_per_thread = 20

        def _worker(idx: int):
            for j in range(n_events_per_thread):
                se.publish(
                    self.paths, source=f"thread:{idx}", event_type="concurrency_test",
                    tier="tick", payload={"thread": idx, "seq": j},
                )

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = self.paths.system_events.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), n_threads * n_events_per_thread)
        seen = set()
        for line in lines:
            d = json.loads(line)  # 任何一行解析失败都说明发生了写入交错
            seen.add((d["payload"]["thread"], d["payload"]["seq"]))
        self.assertEqual(len(seen), n_threads * n_events_per_thread, "不应有丢失或重复的事件")


if __name__ == "__main__":
    unittest.main()
