"""tests/test_archive_gc.py — §4 长期归档 / 回顾式查询测试

覆盖：
  1. 归档流程：热文件里混合"已处理超过 retention_hours"、"已处理但未超过
     retention_hours"、"未处理"三类记录，跑一次归档，断言热文件只剩后
     两类，归档文件里出现且仅出现第一类
  2. 归档文件跨月份正确分片
  3. 查询端点：since/until/keyword/分页组合
  4. 归档失败（模拟某个 target 归档目录不可写）不影响其它 target 继续归档
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.archive.gc import (
    ArchiveTarget,
    query_archive,
    run_archive_gc_all,
    run_archive_gc_once,
)
from mini_agent.storage.paths import AgentPaths


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


class TestArchiveGc(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_archive_mixed_records_splits_correctly(self):
        now = time.time()
        target = ArchiveTarget(
            "external_input_alerts", "external_input", "acknowledged",
            "alert_id", "created_at", retention_hours=24,
        )
        records = [
            {"alert_id": "a1", "acknowledged": True, "created_at": now - 48 * 3600, "title": "old settled"},
            {"alert_id": "a2", "acknowledged": True, "created_at": now - 2 * 3600, "title": "recent settled"},
            {"alert_id": "a3", "acknowledged": False, "created_at": now - 48 * 3600, "title": "unprocessed"},
        ]
        _write_jsonl(self.paths.external_input_alerts, records)

        summary = run_archive_gc_once(self.paths, target)
        self.assertTrue(summary.ok)
        self.assertEqual(summary.archived_count, 1)
        self.assertEqual(summary.kept_count, 2)

        hot = self.paths.external_input_alerts.read_text(encoding="utf-8")
        self.assertNotIn("old settled", hot)
        self.assertIn("recent settled", hot)
        self.assertIn("unprocessed", hot)

        ym = time.strftime("%Y-%m", time.localtime(now - 48 * 3600))
        archive_path = self.paths.archive_file("external_input", "alerts", ym)
        self.assertTrue(archive_path.exists())
        archived_text = archive_path.read_text(encoding="utf-8")
        self.assertIn("old settled", archived_text)
        self.assertNotIn("recent settled", archived_text)

    def test_archive_splits_across_months(self):
        now = time.time()
        target = ArchiveTarget(
            "external_input_alerts", "external_input", "acknowledged",
            "alert_id", "created_at", retention_hours=1,
        )
        this_month_ts = now - 3 * 3600
        # 构造上个月的时间戳（用 40 天前近似跨月，足够测试场景）
        last_month_ts = now - 40 * 86400
        records = [
            {"alert_id": "a1", "acknowledged": True, "created_at": this_month_ts, "title": "this month"},
            {"alert_id": "a2", "acknowledged": True, "created_at": last_month_ts, "title": "last month"},
        ]
        _write_jsonl(self.paths.external_input_alerts, records)

        summary = run_archive_gc_once(self.paths, target)
        self.assertEqual(summary.archived_count, 2)

        ym_this = time.strftime("%Y-%m", time.localtime(this_month_ts))
        ym_last = time.strftime("%Y-%m", time.localtime(last_month_ts))
        self.assertTrue(self.paths.archive_file("external_input", "alerts", ym_this).exists())
        self.assertTrue(self.paths.archive_file("external_input", "alerts", ym_last).exists())

    def test_query_archive_with_since_until_keyword_and_pagination(self):
        now = time.time()
        for i in range(5):
            ym = time.strftime("%Y-%m", time.localtime(now))
            p = self.paths.archive_file("external_input", "alerts", ym)
            rec = {"alert_id": f"a{i}", "created_at": now - i, "title": f"agent news {i}" if i % 2 == 0 else "weather update"}
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        ym = time.strftime("%Y-%m", time.localtime(now))
        result = query_archive(self.paths, category="external_input", since=ym, until=ym, keyword="agent")
        self.assertEqual(result["total"], 3)  # i=0,2,4

        paged = query_archive(self.paths, category="external_input", since=ym, until=ym, keyword="agent", limit=1, offset=1)
        self.assertEqual(len(paged["records"]), 1)
        self.assertTrue(paged["has_more"])

    def test_query_archive_out_of_range_returns_empty(self):
        result = query_archive(self.paths, category="external_input", since="2000-01", until="2000-01")
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["records"], [])

    def test_one_target_failure_does_not_affect_others(self):
        now = time.time()
        target_bad = ArchiveTarget(
            "external_input_alerts", "external_input", "acknowledged", "alert_id", "created_at",
        )
        target_good = ArchiveTarget(
            "notification_reports", "notification", "acknowledged", "report_id", "created_at",
        )
        _write_jsonl(self.paths.external_input_alerts, [
            {"alert_id": "a1", "acknowledged": True, "created_at": now - 48 * 3600, "title": "x"},
        ])
        _write_jsonl(self.paths.notification_reports, [
            {"report_id": "r1", "acknowledged": True, "created_at": now - 48 * 3600, "title": "y"},
        ])

        # 让 external_input 归档目标目录本身变成一个"不可写"的普通文件，
        # 模拟"归档目录建不出来"的失败场景。
        bad_dir = self.paths.archive_dir / "external_input"
        bad_dir.parent.mkdir(parents=True, exist_ok=True)
        bad_dir.write_text("not a directory", encoding="utf-8")

        results = run_archive_gc_all(self.paths, [target_bad, target_good])
        by_target = {r.target: r for r in results}
        self.assertFalse(by_target["external_input_alerts"].ok)
        self.assertTrue(by_target["notification_reports"].ok)
        self.assertEqual(by_target["notification_reports"].archived_count, 1)


if __name__ == "__main__":
    unittest.main()
