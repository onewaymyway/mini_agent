"""tests/test_notification_reports_batch_and_category.py — [看板"关注与
通知"待处理汇报 批量处理 + 分类展示] 覆盖 notification/reports_store.py
新增的 categorize_report()/acknowledge_reports()/count_pending_reports_
by_category() 以及既有函数在新增 category 参数后的行为。
"""
from __future__ import annotations

import json

import pytest

from mini_agent.notification.reports_store import (
    append_report,
    acknowledge_report,
    acknowledge_reports,
    categorize_report,
    count_pending_reports,
    count_pending_reports_by_category,
    list_pending_reports,
    ALL_CATEGORIES,
    CATEGORY_OTHER,
)
from mini_agent.storage.paths import AgentPaths


@pytest.fixture
def paths(tmp_path):
    return AgentPaths(tmp_path)


def _mk(report_id, source, acknowledged=False, created_at=1000.0):
    return {
        "report_id": report_id,
        "source": source,
        "title": f"title-{report_id}",
        "detail": f"detail-{report_id}",
        "created_at": created_at,
        "acknowledged": acknowledged,
    }


class TestCategorizeReport:
    def test_known_failure_sources_map_to_execution_failure(self):
        for src in ["objective_failed", "goal_cycle", "objective_circuit_breaker",
                    "cron_circuit_breaker", "workflow_circuit_breaker",
                    "recovery_burst", "cron_skip_alert", "scheduler_heartbeat_stuck"]:
            assert categorize_report({"source": src}) == "执行失败"

    def test_known_watch_report_sources_map_to_watch_digest(self):
        for src in ["watchlist_report", "growth_weekly_digest", "growth_report",
                    "cycle_patrol", "capability_learning"]:
            assert categorize_report({"source": src}) == "关注汇报"

    def test_unknown_source_maps_to_other(self):
        assert categorize_report({"source": "some_future_source"}) == CATEGORY_OTHER

    def test_missing_source_maps_to_other(self):
        assert categorize_report({}) == CATEGORY_OTHER


class TestListAndCountByCategory:
    def test_list_pending_reports_attaches_category(self, paths):
        append_report(paths, _mk("r1", "objective_failed"))
        append_report(paths, _mk("r2", "watchlist_report"))
        rows = list_pending_reports(paths)
        by_id = {r["report_id"]: r["category"] for r in rows}
        assert by_id == {"r1": "执行失败", "r2": "关注汇报"}

    def test_list_pending_reports_filters_by_category(self, paths):
        append_report(paths, _mk("r1", "objective_failed"))
        append_report(paths, _mk("r2", "watchlist_report"))
        append_report(paths, _mk("r3", "growth_report"))
        rows = list_pending_reports(paths, category="关注汇报")
        assert {r["report_id"] for r in rows} == {"r2", "r3"}

    def test_count_pending_reports_with_category(self, paths):
        append_report(paths, _mk("r1", "objective_failed"))
        append_report(paths, _mk("r2", "watchlist_report"))
        assert count_pending_reports(paths) == 2
        assert count_pending_reports(paths, category="执行失败") == 1
        assert count_pending_reports(paths, category="关注汇报") == 1
        assert count_pending_reports(paths, category="其他") == 0

    def test_count_pending_reports_by_category_covers_all_categories(self, paths):
        append_report(paths, _mk("r1", "objective_failed"))
        counts = count_pending_reports_by_category(paths)
        assert set(counts.keys()) == set(ALL_CATEGORIES)
        assert counts["执行失败"] == 1
        assert counts["关注汇报"] == 0

    def test_acknowledged_reports_excluded_from_counts(self, paths):
        append_report(paths, _mk("r1", "objective_failed", acknowledged=True))
        append_report(paths, _mk("r2", "objective_failed"))
        assert count_pending_reports(paths, category="执行失败") == 1


class TestAcknowledgeReports:
    def test_batch_ack_marks_multiple_and_returns_count(self, paths):
        append_report(paths, _mk("r1", "objective_failed"))
        append_report(paths, _mk("r2", "watchlist_report"))
        append_report(paths, _mk("r3", "growth_report"))
        n = acknowledge_reports(paths, {"r1", "r3"})
        assert n == 2
        remaining = {r["report_id"] for r in list_pending_reports(paths)}
        assert remaining == {"r2"}

    def test_batch_ack_skips_already_acknowledged_and_unknown_ids(self, paths):
        append_report(paths, _mk("r1", "objective_failed", acknowledged=True))
        append_report(paths, _mk("r2", "watchlist_report"))
        n = acknowledge_reports(paths, {"r1", "r2", "does-not-exist"})
        assert n == 1  # only r2 was actually pending

    def test_batch_ack_empty_ids_is_noop(self, paths):
        append_report(paths, _mk("r1", "objective_failed"))
        assert acknowledge_reports(paths, set()) == 0
        assert count_pending_reports(paths) == 1

    def test_batch_ack_no_file_yet_returns_zero(self, paths):
        assert acknowledge_reports(paths, {"r1"}) == 0

    def test_single_acknowledge_report_still_works_via_batch_helper(self, paths):
        append_report(paths, _mk("r1", "objective_failed"))
        append_report(paths, _mk("r2", "watchlist_report"))
        assert acknowledge_report(paths, "r1") is True
        assert acknowledge_report(paths, "r1") is False  # already acked, no-op
        assert acknowledge_report(paths, "does-not-exist") is False
        remaining = {r["report_id"] for r in list_pending_reports(paths)}
        assert remaining == {"r2"}

    def test_file_content_preserved_for_non_matched_rows(self, paths):
        append_report(paths, _mk("r1", "objective_failed"))
        append_report(paths, _mk("r2", "watchlist_report"))
        acknowledge_reports(paths, {"r1"})
        lines = paths.notification_reports.read_text(encoding="utf-8").splitlines()
        docs = [json.loads(l) for l in lines if l.strip()]
        by_id = {d["report_id"]: d for d in docs}
        assert by_id["r1"]["acknowledged"] is True
        assert by_id["r2"]["acknowledged"] is False
        # "category" is computed on read, never persisted to disk.
        assert "category" not in by_id["r1"]
        assert "category" not in by_id["r2"]
