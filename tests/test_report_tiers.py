"""tests/test_report_tiers.py — 分级汇报 Report Tiers（P3）测试

覆盖：
  1. load_report_tiers_config：文件缺失返回空列表；单条缺字段跳过、其余照常加载；
     重复 id 只保留第一条
  2. consume_tier_once：无命中直接跳过（不发送空消息）；有命中时生成摘要、
     dispatch 到 kanban、并把对应记录标记为 consumed；不属于该 tier 的记录
     不受影响
  3. _build_summary_markdown：单个 watchlist_id 分组超过 MAX_ITEMS_PER_GROUP
     条时截断，显示"及其余 N 条"
  4. ensure_report_tier_jobs：缺失才补注册 sys:watchlist_report_<id> job，
     已存在不重复；本地 handler 被正确注册到 CronScheduler
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.external_input.report_tiers import (
    MAX_ITEMS_PER_GROUP,
    ReportTier,
    _build_summary_markdown,
    consume_tier_once,
    ensure_report_tier_jobs,
    load_report_tiers_config,
)
from mini_agent.storage.paths import AgentPaths


def _write_yaml(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_pending_hits(paths: AgentPaths, hits: list[dict]) -> None:
    p = paths.external_input_pending_hits
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for hit in hits:
            f.write(json.dumps(hit, ensure_ascii=False) + "\n")


class TestLoadReportTiersConfig(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            self.assertEqual(load_report_tiers_config(paths), [])

    def test_loads_valid_entries_and_skips_bad_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            _write_yaml(paths.notification_report_tiers_config, """
tiers:
  - id: minute_1
    schedule: "interval:60"
    notify_channels: [kanban]
  - schedule: "interval:30"
  - id: daily
    schedule: "cron:0 22 * * *"
""")
            tiers = load_report_tiers_config(paths)
            ids = [t.id for t in tiers]
            self.assertEqual(ids, ["minute_1", "daily"])
            self.assertEqual(tiers[1].notify_channels, ["kanban"])

    def test_duplicate_id_keeps_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            _write_yaml(paths.notification_report_tiers_config, """
tiers:
  - id: minute_1
    schedule: "interval:60"
  - id: minute_1
    schedule: "interval:3600"
""")
            tiers = load_report_tiers_config(paths)
            self.assertEqual(len(tiers), 1)
            self.assertEqual(tiers[0].schedule, "interval:60")


class TestBuildSummaryMarkdown(unittest.TestCase):
    def test_truncates_over_limit(self):
        hits = [
            {"watchlist_id": "wid1", "title": f"item {i}", "url": None}
            for i in range(MAX_ITEMS_PER_GROUP + 5)
        ]
        title, body = _build_summary_markdown("minute_1", hits)
        self.assertIn(f"共 {len(hits)} 条", title)
        self.assertIn("及其余 5 条", body)

    def test_groups_by_watchlist_id(self):
        hits = [
            {"watchlist_id": "a", "title": "x1"},
            {"watchlist_id": "b", "title": "x2"},
        ]
        _, body = _build_summary_markdown("minute_1", hits)
        self.assertIn("## a", body)
        self.assertIn("## b", body)


class TestConsumeTierOnce(unittest.TestCase):
    def test_no_hits_skips_without_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            tier = ReportTier(id="minute_1", schedule="interval:60")
            ok = consume_tier_once(paths, tier)
            self.assertTrue(ok)

    def test_consumes_matching_tier_and_marks_consumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            _write_pending_hits(paths, [
                {"id": "hit:1", "tier": "minute_1", "watchlist_id": "wid1",
                 "title": "t1", "detail": "d1", "url": None, "matched_at": 1.0,
                 "consumed": False},
                {"id": "hit:2", "tier": "hourly", "watchlist_id": "wid1",
                 "title": "t2", "detail": "d2", "url": None, "matched_at": 1.0,
                 "consumed": False},
            ])
            tier = ReportTier(id="minute_1", schedule="interval:60", notify_channels=["kanban"])
            ok = consume_tier_once(paths, tier)
            self.assertTrue(ok)

            records = [
                json.loads(line)
                for line in paths.external_input_pending_hits.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            by_id = {r["id"]: r for r in records}
            self.assertTrue(by_id["hit:1"]["consumed"])
            # hourly 档的记录不受 minute_1 消费影响
            self.assertFalse(by_id["hit:2"]["consumed"])

            # kanban 兜底渠道应该已经落地一条 alerts 记录
            alerts_text = paths.external_input_alerts.read_text(encoding="utf-8")
            self.assertIn("watchlist_report", alerts_text)

    def test_second_run_has_nothing_new_to_consume(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            _write_pending_hits(paths, [
                {"id": "hit:1", "tier": "minute_1", "watchlist_id": "wid1",
                 "title": "t1", "detail": "d1", "url": None, "matched_at": 1.0,
                 "consumed": False},
            ])
            tier = ReportTier(id="minute_1", schedule="interval:60")
            consume_tier_once(paths, tier)
            alerts_before = paths.external_input_alerts.read_text(encoding="utf-8")
            consume_tier_once(paths, tier)
            alerts_after = paths.external_input_alerts.read_text(encoding="utf-8")
            # 第二次没有新记录，不应该再追加新的 alert
            self.assertEqual(alerts_before, alerts_after)


class TestEnsureReportTierJobs(unittest.TestCase):
    def test_registers_missing_jobs_and_local_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            _write_yaml(paths.notification_report_tiers_config, """
tiers:
  - id: minute_1
    schedule: "interval:60"
    notify_channels: [kanban]
""")
            from mini_agent.evolution.cron_scheduler import CronScheduler
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()

            newly_added = ensure_report_tier_jobs(paths, scheduler)
            self.assertIn("sys:watchlist_report_minute_1", newly_added)
            job = scheduler.get("sys:watchlist_report_minute_1")
            self.assertIsNotNone(job)
            self.assertIn("sys:watchlist_report_minute_1", scheduler._local_handlers)

            # 再次调用不应该重复新增
            newly_added_2 = ensure_report_tier_jobs(paths, scheduler)
            self.assertEqual(newly_added_2, [])


if __name__ == "__main__":
    unittest.main()
