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
    monkeypatch.setattr(ns, "PROCESSED_MAX_SIZE", 5)
    monkeypatch.setattr(ns, "PENDING_MAX_SIZE", 5)
    old = [{"time": f"2026-10-18 10:00:0{i}", "content": f"o{i}", "hash": f"o{i}"} for i in range(5)]
    ns.save_processed(old, {n["hash"] for n in old}, 5)
    new = [{"time": "2026-10-20 07:00:00", "content": "n", "hash": "n"}]
    ns.move_to_processed(new)
    assert ns.load_processed()["news"][0]["time"] == "2026-10-20 07:00:00"

    ns.save_pending(old + new)
    assert ns.load_pending()["news"][0]["time"] == "2026-10-20 07:00:00"
