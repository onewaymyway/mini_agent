"""tests/test_telegraph_llm_batch.py — 7x24 新闻 LLM 分批分析的纯逻辑测试。

用假 LLM 替身，不依赖网络/真实模型。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_watch import news_signals as ns  # noqa: E402
from stock_watch.config import WatchlistConfig  # noqa: E402


class _FakeLLM:
    """每次调用记录收到的条数；fail_calls 中的调用序号（从0起）抛异常。"""

    def __init__(self, fail_calls=()):
        self.calls = []
        self.fail_calls = set(fail_calls)

    def ask(self, prompt, **kwargs):
        idx = len(self.calls)
        lines = [ln for ln in prompt.splitlines() if ln.startswith("[") and "] 新闻" in ln]
        self.calls.append(len(lines))
        if idx in self.fail_calls:
            raise RuntimeError("boom")
        times = [ln[1:ln.index("]")] for ln in lines]
        return json.dumps([{"news_time": t, "direction": "positive"} for t in times])


def _news(n):
    return [{"time": f"10:{i:02d}:00", "content": f"新闻{i}", "hash": f"h{i}"} for i in range(n)]


def _patch_llm(monkeypatch, llm):
    monkeypatch.setattr(ns, "_get_llm_helper", lambda: llm)


def test_batches_split_with_remainder(monkeypatch):
    llm = _FakeLLM()
    _patch_llm(monkeypatch, llm)
    results, done = ns.analyze_telegraph_news_batched(_news(23), batch_size=10)
    assert llm.calls == [10, 10, 3]
    assert len(done) == 23 and len(results) == 23
    assert all(r.get("news_content") for r in results)


def test_default_batch_size_is_25_and_no_30_truncation(monkeypatch):
    llm = _FakeLLM()
    _patch_llm(monkeypatch, llm)
    _, done = ns.analyze_telegraph_news_batched(_news(50))
    assert llm.calls == [25, 25]
    assert len(done) == 50


def test_failed_batch_not_marked_done(monkeypatch):
    llm = _FakeLLM(fail_calls={1})
    _patch_llm(monkeypatch, llm)
    results, done = ns.analyze_telegraph_news_batched(_news(25), batch_size=10)
    assert llm.calls == [10, 10, 5]
    assert len(done) == 15 and len(results) == 15
    assert {d["hash"] for d in done}.isdisjoint({f"h{i}" for i in range(10, 20)})


def test_invalid_batch_size_falls_back(monkeypatch):
    llm = _FakeLLM()
    _patch_llm(monkeypatch, llm)
    ns.analyze_telegraph_news_batched(_news(30), batch_size=0)
    assert llm.calls == [25, 5]


def test_unparseable_response_counts_as_failure(monkeypatch):
    class _Bad:
        def ask(self, prompt, **kw):
            return "抱歉无法分析"

    _patch_llm(monkeypatch, _Bad())
    results, done = ns.analyze_telegraph_news_batched(_news(5), batch_size=10)
    assert results == [] and done == []


def test_config_batch_size_default_and_invalid():
    assert WatchlistConfig(telegraph={}).telegraph_llm_batch_size == 25
    assert WatchlistConfig(telegraph={"llm_batch_size": 10}).telegraph_llm_batch_size == 10
    assert WatchlistConfig(telegraph={"llm_batch_size": 0}).telegraph_llm_batch_size == 25
    assert WatchlistConfig(telegraph={"llm_batch_size": "x"}).telegraph_llm_batch_size == 25


def test_archive_trim_keeps_newest(monkeypatch, tmp_path):
    """归档/待处理队列超限时应保留最新的，而不是最旧的（回归测试）。"""
    import stock_watch.config as cfg

    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ns, "_telegraph_limit", lambda prop, default: 5)
    old = [{"time": f"2026-10-18 10:00:0{i}", "content": f"o{i}", "hash": f"o{i}"} for i in range(5)]
    ns.save_processed(old, {n["hash"] for n in old}, 5)
    new = [{"time": "2026-10-20 07:00:00", "content": "n", "hash": "n"}]
    ns.move_to_processed(new)
    assert ns.load_processed()["news"][0]["time"] == "2026-10-20 07:00:00"

    ns.save_pending(old + new)
    assert ns.load_pending()["news"][0]["time"] == "2026-10-20 07:00:00"


def test_backlog_includes_pending_outside_fetch_window(monkeypatch, tmp_path):
    """滚出抓取窗口的待处理积压也要被分析；已归档的、重复的不要。"""
    import stock_watch.config as cfg

    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    old = [{"time": "2026-10-18 09:00:00", "content": "旧积压", "hash": "old1"}]
    ns.save_pending(old)
    # 本次抓取只抓到新的两条，旧积压不在其中
    fetched = [
        {"time": "2026-10-20 07:00:00", "content": "A"},
        {"time": "2026-10-20 07:01:00", "content": "B"},
    ]
    ns.filter_dedup_news(fetched)
    ns.filter_dedup_news(fetched)  # 重复抓取不应让队列里出现重复项
    backlog = ns.get_analysis_backlog()
    assert [n["content"] for n in backlog] == ["B", "A", "旧积压"]
    # 限制条数时最新优先
    assert [n["content"] for n in ns.get_analysis_backlog(2)] == ["B", "A"]
    # 归档后不再出现
    ns.move_to_processed(backlog[:2])
    assert [n["content"] for n in ns.get_analysis_backlog()] == ["旧积压"]


def test_add_dates_crosses_midnight_and_multi_day(monkeypatch):
    from datetime import datetime
    import types

    # data_sources 顶层会导入需要 CDP/浏览器依赖的 browser_manager，纯逻辑测试里用桩替代
    if "stock_watch.browser_manager" not in sys.modules:
        stub = types.ModuleType("stock_watch.browser_manager")
        stub.__getattr__ = lambda name: (lambda *a, **k: None)
        monkeypatch.setitem(sys.modules, "stock_watch.browser_manager", stub)
    from stock_watch.data_sources import _add_dates_to_telegraph

    now = datetime(2026, 10, 20, 8, 0, 0)
    rows = [{"time": t} for t in ["07:30:00", "00:10:00", "23:50:00", "12:00:00", "00:30:00", "22:00:00"]]
    _add_dates_to_telegraph(rows, now)
    assert [r["time"] for r in rows] == [
        "2026-10-20 07:30:00", "2026-10-20 00:10:00",
        "2026-10-19 23:50:00", "2026-10-19 12:00:00",
        "2026-10-19 00:30:00", "2026-10-18 22:00:00",
    ]
    # 凌晨扫描：最新一条晚于扫描时刻 => 属于前一天
    now2 = datetime(2026, 10, 20, 0, 10, 0)
    rows2 = [{"time": "23:59:00"}, {"time": "23:00:00"}]
    _add_dates_to_telegraph(rows2, now2)
    assert [r["time"] for r in rows2] == ["2026-10-19 23:59:00", "2026-10-19 23:00:00"]


def test_config_new_props():
    assert WatchlistConfig(telegraph={"max_news": 300, "top_n": 50}).telegraph_max_news == 300
    assert WatchlistConfig(telegraph={"top_n": 50}).telegraph_max_news == 50
    assert WatchlistConfig(telegraph={}).telegraph_llm_max_news_per_run == 100
    assert WatchlistConfig(telegraph={"llm_max_news_per_run": 0}).telegraph_llm_max_news_per_run == 0


def test_retention_limits_from_config(monkeypatch, tmp_path):
    """三个保留上限都从配置读取；非法值回落默认；分析结果保留最新的 N 条。"""
    import stock_watch.config as cfg

    assert WatchlistConfig(telegraph={}).telegraph_analysis_max_records == 2000
    assert WatchlistConfig(telegraph={}).telegraph_pending_max_size == 1000
    assert WatchlistConfig(telegraph={}).telegraph_processed_max_size == 5000
    c = WatchlistConfig(telegraph={"analysis_max_records": 3, "pending_max_size": "x", "processed_max_size": 0})
    assert c.telegraph_analysis_max_records == 3
    assert c.telegraph_pending_max_size == 1000
    assert c.telegraph_processed_max_size == 5000

    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: c)
    state_path = tmp_path / "state.json"
    ns.save_analysis_state(state_path, 0, [{"news_time": f"t{i}"} for i in range(5)])
    kept = [a["news_time"] for a in ns.load_analysis_state(state_path)["analyses"]]
    assert kept == ["t2", "t3", "t4"]


def _write_state(path, analyses):
    path.write_text(json.dumps({"last_analysis_index": 0, "analyses": analyses}, ensure_ascii=False), encoding="utf-8")


def test_summary_tolerates_dict_industries_and_dirty_fields(tmp_path):
    """回归：industry_opportunities 里出现 dict 时曾导致 unhashable type: 'dict'。"""
    from datetime import datetime

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    p = tmp_path / "state.json"
    _write_state(p, [
        {"news_time": now, "direction": "positive",
         "industry_opportunities": [{"name": "AI算力"}, "半导体", {"industry": "半导体"}, {}, None],
         "related_stocks": [{"code": "SZ000001", "name": "平安银行"}, "无代码字符串", {"code": "SZ000001", "name": {"x": 1}}]},
        {"news_time": now, "direction": "negative", "industry_opportunities": "半导体",
         "related_stocks": None, "related_etfs": ["芯片ETF"]},
    ])
    summary = ns.get_all_analyses_summary(p, days=7, top_n=10)
    assert summary["total_analyses"] == 2
    inds = dict(summary["top_industries"])
    assert inds["半导体"]["count"] == 2 and inds["AI算力"]["count"] == 1
    assert summary["top_stocks"][0]["code"] == "SZ000001" and summary["top_stocks"][0]["count"] == 2
    assert len(ns.get_news_by_industry(p, "半导体", 7)) == 2
    assert len(ns.get_news_by_stock(p, "SZ000001", 7)) == 1


def test_recent_filter_uses_local_date_and_keeps_undated(tmp_path):
    from datetime import datetime, timedelta

    fmt = "%Y-%m-%d %H:%M:%S"
    p = tmp_path / "state.json"
    _write_state(p, [
        {"news_time": (datetime.now() - timedelta(days=1)).strftime(fmt), "direction": "positive"},
        {"news_time": (datetime.now() - timedelta(days=30)).strftime(fmt), "direction": "positive"},
        {"news_time": "21:02:18", "direction": "positive"},   # 旧格式无日期：保留
    ])
    assert ns.get_all_analyses_summary(p, days=7)["total_analyses"] == 2


def test_new_results_are_normalized():
    r = ns._normalize_analysis({"industry_opportunities": [{"name": "AI"}], "related_stocks": ["x"],
                                "direction": " Positive ", "confidence": None})
    assert r["industry_opportunities"] == ["AI"]
    assert r["related_stocks"] == [{"code": "", "name": "x"}]
    assert r["direction"] == "positive" and r["confidence"] == ""
