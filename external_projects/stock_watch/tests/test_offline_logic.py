"""tests/test_offline_logic.py — 不需要网络的纯逻辑单元测试。

覆盖候选池合并/衰减/淘汰、报告渲染这些纯函数，用固定 mock 数据跑通，
不依赖 akshare/网络（见 PROJECT.md「已知限制」：本项目在无出网权限的
环境下构建，网络相关代码只做到语法正确 + 结构自洽，未做真实连通性
验证）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_watch.candidate_pool import (  # noqa: E402
    CandidateEntry,
    apply_decay,
    enforce_max_size,
    ensure_seeds,
    load_pool,
    merge_hot_items,
    save_pool,
)
from stock_watch.config import SeedStock  # noqa: E402
from stock_watch.data_sources import HotStockItem  # noqa: E402
from stock_watch.report import render_candidate_pool_report  # noqa: E402


def test_merge_hot_items_creates_and_scores_entries():
    pool = {}
    items = [
        HotStockItem(code="600519", name="贵州茅台", source="eastmoney_hot_rank", heat_score=90),
        HotStockItem(code="600519", name="贵州茅台", source="xueqiu_hot_stock", heat_score=50),
        HotStockItem(code="000001", name="平安银行", source="eastmoney_hot_rank", heat_score=10),
    ]
    pool = merge_hot_items(pool, items)

    assert set(pool) == {"600519", "000001"}
    assert pool["600519"].score == 140
    assert set(pool["600519"].sources) == {"eastmoney_hot_rank", "xueqiu_hot_stock"}


def test_ensure_seeds_keeps_seed_present():
    pool = {}
    seeds = [SeedStock(code="510300", name="沪深300ETF", market="sh", type="etf")]
    pool = ensure_seeds(pool, seeds)
    assert "510300" in pool
    assert pool["510300"].type == "etf"


def test_enforce_max_size_keeps_highest_scored():
    pool = {
        "a": CandidateEntry(code="a", name="A", score=10),
        "b": CandidateEntry(code="b", name="B", score=90),
        "c": CandidateEntry(code="c", name="C", score=50),
    }
    trimmed = enforce_max_size(pool, max_size=2)
    assert set(trimmed) == {"b", "c"}


def test_apply_decay_reduces_stale_scores():
    stale = CandidateEntry(
        code="x", name="X", score=100,
        last_seen="2000-01-01T00:00:00+00:00",
    )
    pool = {"x": stale}
    decayed = apply_decay(pool, decay_days=7, decay_rate=0.5)
    assert decayed["x"].score == 50


def test_pool_roundtrip_json(tmp_path):
    path = tmp_path / "pool.json"
    pool = {"a": CandidateEntry(code="a", name="A", score=1.0, sources=["seed"])}
    save_pool(path, pool)
    loaded = load_pool(path)
    assert loaded["a"].name == "A"
    assert loaded["a"].sources == ["seed"]


def test_load_pool_tolerates_corrupted_file(tmp_path):
    path = tmp_path / "pool.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert load_pool(path) == {}


def test_render_candidate_pool_report(tmp_path):
    entries = [CandidateEntry(code="600519", name="贵州茅台", score=42.0, sources=["seed"])]
    out = render_candidate_pool_report(entries, tmp_path / "report.md", generated_at="2026-08-26")
    text = out.read_text(encoding="utf-8")
    assert "600519" in text
    assert "贵州茅台" in text
    assert "42.0" in text
