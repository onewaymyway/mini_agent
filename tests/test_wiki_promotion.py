"""
tests/test_wiki_promotion.py — wiki/promotion.py P4 转正评估测试

覆盖《wiki 式知识库改进计划》P4：每日快照记录（幂等）、A/B 对比记录、
三项标准的连续达标判断，以及 LibraryIndex 门面方法的接线。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.promotion import (
    evaluate_promotion_readiness,
    record_daily_snapshot,
    record_search_comparison,
)
from mini_agent.wiki.validator import ValidationIssue, ValidationReport
from mini_agent.wiki.writer import write_page


@pytest.fixture()
def wiki_paths(tmp_path):
    paths = AgentPaths(project_root=tmp_path)
    paths.ensure_wiki_dirs()
    return paths


# ── record_daily_snapshot ────────────────────────────────────────────────


def test_record_daily_snapshot_computes_ratio(wiki_paths):
    write_page(
        wiki_paths, page_id="e1", page_type="entity", body="x",
        extra_frontmatter={"source_kind": "world_model"},
    )
    write_page(
        wiki_paths, page_id="e2", page_type="entity", body="x",
        extra_frontmatter={"source_kind": "correction"},
    )
    rec = record_daily_snapshot(wiki_paths)
    assert rec is not None
    assert rec["total_pages"] == 2
    assert rec["target_ratio"] == 0.5
    assert rec["validation_errors"] == 0


def test_record_daily_snapshot_idempotent_per_day(wiki_paths):
    write_page(wiki_paths, page_id="e1", page_type="entity", body="x")
    first = record_daily_snapshot(wiki_paths)
    second = record_daily_snapshot(wiki_paths)
    assert first is not None
    assert second is None  # 同一天第二次记录应被跳过

    log = wiki_paths.wiki_promotion_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(log) == 1


def test_record_daily_snapshot_reuses_passed_validation(wiki_paths):
    write_page(wiki_paths, page_id="e1", page_type="entity", body="x")
    fake_validation = ValidationReport(
        issues=[ValidationIssue(severity="error", kind="dead_link", page_id="e1", detail="x")]
    )
    rec = record_daily_snapshot(wiki_paths, validation=fake_validation)
    assert rec["validation_errors"] == 1


def test_record_daily_snapshot_different_days_both_recorded(wiki_paths):
    write_page(wiki_paths, page_id="e1", page_type="entity", body="x")
    day1 = date(2026, 7, 1)
    day2 = date(2026, 7, 2)
    rec1 = record_daily_snapshot(wiki_paths, today=day1)
    rec2 = record_daily_snapshot(wiki_paths, today=day2)
    assert rec1 is not None and rec2 is not None
    log = wiki_paths.wiki_promotion_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(log) == 2


# ── record_search_comparison ─────────────────────────────────────────────


def test_record_search_comparison_appends(wiki_paths):
    record_search_comparison(wiki_paths, wiki_grounded=True, shelf_grounded=False, query="q1")
    record_search_comparison(wiki_paths, wiki_grounded=False, shelf_grounded=True, query="q2")
    log = wiki_paths.wiki_search_ab_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(log) == 2


# ── evaluate_promotion_readiness ─────────────────────────────────────────


def test_readiness_all_criteria_unmet_when_no_data(wiki_paths):
    readiness = evaluate_promotion_readiness(wiki_paths)
    assert readiness.ratio_ok is False
    assert readiness.validation_ok is False
    assert readiness.ab_ok is None
    assert readiness.overall_ready is False


def test_readiness_ratio_and_validation_streak(wiki_paths):
    write_page(
        wiki_paths, page_id="e1", page_type="entity", body="x",
        extra_frontmatter={"source_kind": "world_model"},
    )
    base = date(2026, 7, 18)
    # 连续 14 天都达标（占比 100%、0 错误）
    for i in range(14):
        d = base - timedelta(days=i)
        record_daily_snapshot(wiki_paths, today=d)

    readiness = evaluate_promotion_readiness(
        wiki_paths, ratio_streak_days=14, validation_streak_days=7,
    )
    assert readiness.ratio_ok is True
    assert readiness.ratio_days_observed >= 14
    assert readiness.validation_ok is True
    assert readiness.validation_days_observed >= 7


def test_readiness_streak_breaks_on_gap_day(wiki_paths):
    write_page(
        wiki_paths, page_id="e1", page_type="entity", body="x",
        extra_frontmatter={"source_kind": "world_model"},
    )
    base = date(2026, 7, 18)
    # 记 5 天，跳过 1 天，再记 5 天 —— 连续计数应该在跳过点断开，
    # 最新日期往前数只能数到跳过点为止
    for i in range(5):
        record_daily_snapshot(wiki_paths, today=base - timedelta(days=i))
    for i in range(6, 11):
        record_daily_snapshot(wiki_paths, today=base - timedelta(days=i))

    readiness = evaluate_promotion_readiness(wiki_paths, ratio_streak_days=14)
    assert readiness.ratio_ok is False
    assert readiness.ratio_days_observed == 5  # 从最新日期往前数，第 6 天缺记录后中断


def test_readiness_ab_insufficient_samples(wiki_paths):
    for _ in range(5):
        record_search_comparison(wiki_paths, wiki_grounded=True, shelf_grounded=False)
    readiness = evaluate_promotion_readiness(wiki_paths, ab_min_samples=20)
    assert readiness.ab_ok is None  # 样本不足，不下结论
    assert readiness.ab_sample_size == 5
    assert readiness.wiki_hit_rate == 1.0


def test_readiness_ab_ok_when_wiki_hit_rate_not_lower(wiki_paths):
    for _ in range(15):
        record_search_comparison(wiki_paths, wiki_grounded=True, shelf_grounded=True)
    for _ in range(10):
        record_search_comparison(wiki_paths, wiki_grounded=False, shelf_grounded=True)
    readiness = evaluate_promotion_readiness(wiki_paths, ab_min_samples=20)
    assert readiness.ab_sample_size == 25
    # wiki: 15/25=0.6, shelf: 25/25=1.0 -> wiki 命中率更低，不达标
    assert readiness.ab_ok is False


def test_readiness_overall_ready_requires_all_three(wiki_paths):
    write_page(
        wiki_paths, page_id="e1", page_type="entity", body="x",
        extra_frontmatter={"source_kind": "world_model"},
    )
    base = date(2026, 7, 18)
    for i in range(14):
        record_daily_snapshot(wiki_paths, today=base - timedelta(days=i))
    for _ in range(25):
        record_search_comparison(wiki_paths, wiki_grounded=True, shelf_grounded=False)

    readiness = evaluate_promotion_readiness(
        wiki_paths, ratio_streak_days=14, validation_streak_days=7, ab_min_samples=20,
    )
    assert readiness.overall_ready is True


# ── LibraryIndex 门面方法接线 ─────────────────────────────────────────────


def _make_library_index(tmp_path, wiki_paths=None):
    from mini_agent.perception.library_index import LibraryIndex

    return LibraryIndex(
        classification_tree_path=tmp_path / "tree.json",
        unclassified_candidates_path=tmp_path / "candidates.jsonl",
        entity_index_path=tmp_path / "entities.json",
        category_catalog_path=tmp_path / "catalog.json",
        knowledge_timeline_path=tmp_path / "timeline.jsonl",
        wiki_paths=wiki_paths,
    )


def test_library_index_promotion_status_without_wiki_paths(tmp_path):
    lib = _make_library_index(tmp_path, wiki_paths=None)
    status = lib.promotion_status()
    assert status.overall_ready is False


def test_library_index_record_search_comparison_and_status(tmp_path, wiki_paths):
    lib = _make_library_index(tmp_path, wiki_paths=wiki_paths)
    lib.record_search_comparison(wiki_grounded=True, shelf_grounded=False, query="q")
    status = lib.promotion_status()
    assert status.ab_sample_size == 1
