"""tests/test_signals.py — 阶段3自算信号（不依赖外部网站结论）的纯逻辑
单元测试，用固定 mock 数据跑通（K线走 pandas DataFrame，公告/新闻走
list[dict]），不依赖 akshare/网络。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from stock_watch.announcement_signals import classify_announcements  # noqa: E402
from stock_watch.candidate_pool import CandidateEntry, merge_signals  # noqa: E402
from stock_watch.indicators import compute_price_signals  # noqa: E402
from stock_watch.news_signals import compute_news_signals  # noqa: E402
from stock_watch.signals import Signal, summarize_signal_source  # noqa: E402


def _kline_df(closes, volumes=None):
    volumes = volumes or [1_000_000] * len(closes)
    return pd.DataFrame({"收盘": closes, "成交量": volumes})


def test_ma_golden_cross_detected():
    # 构造一段先跌后急涨的收盘价序列，让 MA5 在最后一天上穿 MA20
    closes = [10.0] * 20 + [9.0, 9.3, 9.8, 10.5, 15.0]
    df = _kline_df(closes)
    signals = compute_price_signals(df)
    names = [s.name for s in signals]
    assert "ma_golden_cross" in names


def test_volume_spike_detected():
    closes = [10.0] * 20 + [10.5]
    volumes = [1_000_000] * 20 + [5_000_000]  # 最后一天放量5倍
    df = _kline_df(closes, volumes)
    signals = compute_price_signals(df)
    spike = [s for s in signals if s.name == "volume_spike"]
    assert len(spike) == 1
    assert spike[0].score > 0  # 放量且上涨 -> 正分


def test_insufficient_data_returns_no_signals():
    df = _kline_df([10.0, 10.1, 10.2])  # 数据量太少，任何指标都算不出来
    signals = compute_price_signals(df)
    assert signals == []


def test_classify_announcements_hits_buyback_and_risk():
    announcements = [
        {"公告标题": "关于回购公司股份的公告", "公告日期": "2026-08-01"},
        {"公告标题": "关于收到问询函的公告", "公告日期": "2026-08-05"},
        {"公告标题": "无关公告标题", "公告日期": "2026-08-06"},
    ]
    signals = classify_announcements(announcements)
    names = {s.name for s in signals}
    assert "announcement_buyback" in names
    assert "announcement_risk_warning" in names
    assert len(signals) == 2  # 第三条不命中任何关键词


def test_classify_announcements_respects_weight_override():
    announcements = [{"公告标题": "公司发布业绩预增公告", "公告日期": "2026-08-01"}]
    signals = classify_announcements(announcements, weights={"earnings_beat": 20.0})
    assert signals[0].score == 20.0


def test_classify_announcements_max_one_signal_per_category():
    announcements = [
        {"公告标题": "关于回购股份的公告一"},
        {"公告标题": "关于回购股份的公告二"},
    ]
    signals = classify_announcements(announcements)
    assert len(signals) == 1


def test_news_volume_spike_and_sentiment():
    news = [{"新闻标题": f"公司订单增长利好消息{i}"} for i in range(16)]
    signals = compute_news_signals(news, volume_threshold=15)
    names = {s.name for s in signals}
    assert "news_volume_spike" in names
    assert "news_sentiment_positive" in names


def test_news_signals_empty_when_no_news():
    assert compute_news_signals([]) == []


def test_merge_signals_creates_entry_and_accumulates_score():
    pool = {}
    signals = [
        Signal(name="ma_golden_cross", category="price", score=8.0, reason="金叉"),
        Signal(name="announcement_buyback", category="announcement", score=5.0, reason="回购"),
    ]
    pool = merge_signals(pool, "600519", "贵州茅台", signals)
    entry = pool["600519"]
    assert entry.score == 13.0
    assert entry.state == "watching"
    assert len(entry.state_history) == 1
    assert summarize_signal_source("ma_golden_cross", "price") in entry.sources


def test_merge_signals_accumulates_on_existing_entry():
    pool = {"600519": CandidateEntry(code="600519", name="贵州茅台", score=10.0)}
    signals = [Signal(name="news_volume_spike", category="news", score=3.0, reason="关注度高")]
    pool = merge_signals(pool, "600519", "贵州茅台", signals)
    assert pool["600519"].score == 13.0


def test_merge_signals_noop_when_no_signals():
    pool = {}
    pool = merge_signals(pool, "600519", "贵州茅台", [])
    assert pool == {}
