"""tests/test_outcomes_and_source_health.py — 阶段 3 离线单元测试。

覆盖候选池快照归档/回溯、source_health 记录、outcomes 汇总——均用
固定数据/临时目录，不需要网络（对应
`next_doc/stock_watch_continuous_improvement_plan.md` 阶段 3）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_watch.candidate_pool import (  # noqa: E402
    CandidateEntry,
    list_snapshot_dates,
    load_pool_snapshot,
    save_pool_snapshot,
)
from stock_watch.outcomes import (  # noqa: E402
    build_outcome_records,
    notable_outcomes,
    summarize_by_score_bucket,
)
from stock_watch.source_health import (  # noqa: E402
    failure_rate_by_source,
    read_source_health,
    record,
    tracked_source,
)


def test_save_and_load_pool_snapshot_roundtrip(tmp_path):
    pool = {"600519": CandidateEntry(code="600519", name="贵州茅台", score=42.0)}
    save_pool_snapshot(tmp_path, pool, on="20260819")
    loaded = load_pool_snapshot(tmp_path, "20260819")
    assert loaded["600519"].name == "贵州茅台"
    assert loaded["600519"].score == 42.0


def test_load_pool_snapshot_missing_date_returns_empty(tmp_path):
    assert load_pool_snapshot(tmp_path, "20200101") == {}


def test_list_snapshot_dates_sorted(tmp_path):
    save_pool_snapshot(tmp_path, {}, on="20260820")
    save_pool_snapshot(tmp_path, {}, on="20260810")
    assert list_snapshot_dates(tmp_path) == ["20260810", "20260820"]


def test_build_outcome_records_mixes_success_and_error():
    snapshot = {
        "600519": CandidateEntry(code="600519", name="贵州茅台", score=90.0),
        "000001": CandidateEntry(code="000001", name="平安银行", score=5.0),
    }
    records = build_outcome_records(
        snapshot,
        change_pcts={"600519": 18.5},
        errors={"000001": "停牌"},
        snapshot_date="20260819",
    )
    by_code = {r.code: r for r in records}
    assert by_code["600519"].ok
    assert by_code["600519"].change_pct == 18.5
    assert not by_code["000001"].ok
    assert by_code["000001"].error == "停牌"


def test_notable_outcomes_filters_by_threshold():
    snapshot = {
        "a": CandidateEntry(code="a", name="A", score=10),
        "b": CandidateEntry(code="b", name="B", score=10),
    }
    records = build_outcome_records(
        snapshot, change_pcts={"a": 20.0, "b": 2.0}, errors={}, snapshot_date="20260819",
    )
    notable = notable_outcomes(records, threshold_pct=15.0)
    assert [r.code for r in notable] == ["a"]


def test_summarize_by_score_bucket():
    snapshot = {
        "low": CandidateEntry(code="low", name="低分", score=5),
        "mid": CandidateEntry(code="mid", name="中分", score=30),
        "high": CandidateEntry(code="high", name="高分", score=80),
    }
    records = build_outcome_records(
        snapshot,
        change_pcts={"low": -5.0, "mid": 3.0, "high": 12.0},
        errors={},
        snapshot_date="20260819",
    )
    summary = summarize_by_score_bucket(records)
    assert summary["<10"]["count"] == 1
    assert summary["10-50"]["avg_change_pct"] == 3.0
    assert summary[">=50"]["avg_change_pct"] == 12.0


def test_source_health_record_and_read(tmp_path):
    path = tmp_path / "source_health.jsonl"
    record(path, source="eastmoney_hot_rank", entrypoint="hotlist_scan", ok=True, duration_sec=1.2, item_count=50)
    record(path, source="xueqiu_hot_stock", entrypoint="hotlist_scan", ok=False, duration_sec=0.5, error="超时")

    records = read_source_health(path)
    assert len(records) == 2
    assert records[0].item_count == 50
    assert records[1].error == "超时"


def test_tracked_source_records_success_and_reraises_on_failure(tmp_path):
    path = tmp_path / "source_health.jsonl"

    with tracked_source(path, source="eastmoney_hot_rank", entrypoint="hotlist_scan") as h:
        h.item_count = 10

    import pytest

    with pytest.raises(RuntimeError):
        with tracked_source(path, source="xueqiu_hot_stock", entrypoint="hotlist_scan"):
            raise RuntimeError("boom")

    records = read_source_health(path)
    assert records[0].ok is True and records[0].item_count == 10
    assert records[1].ok is False and "boom" in records[1].error


def test_failure_rate_by_source(tmp_path):
    path = tmp_path / "source_health.jsonl"
    record(path, source="a", entrypoint="e", ok=True, duration_sec=1.0)
    record(path, source="a", entrypoint="e", ok=False, duration_sec=1.0, error="x")
    record(path, source="a", entrypoint="e", ok=False, duration_sec=1.0, error="x")
    record(path, source="b", entrypoint="e", ok=True, duration_sec=1.0)

    rates = failure_rate_by_source(read_source_health(path))
    assert rates["a"]["total"] == 3
    assert rates["a"]["failed"] == 2
    assert abs(rates["a"]["failure_rate"] - (2 / 3)) < 1e-9
    assert rates["b"]["failure_rate"] == 0.0
