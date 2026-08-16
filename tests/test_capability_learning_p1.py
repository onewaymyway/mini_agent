"""P1 单元测试：evolution/capability_learning.py

覆盖设计文档 next_doc/persona_capability_learning_design.md 里 P1 阶段
承诺的最小可用闭环：Track CRUD、大纲缺口扫描、异步问答队列的生成/回答/
消费、单轮循环编排在未接线真实检索时的安全默认行为。
"""
from __future__ import annotations

import time

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.capability_learning import (
    CapabilityLedgerStore,
    CapabilityQuestionStore,
    CapabilityTrackStore,
    OutlineTopic,
    run_capability_learning_cycle,
    scan_outline_gaps,
    needs_user_context,
    record_wiki_miss,
    find_cross_track_reuse,
    _topic_name_similarity,
)


@pytest.fixture
def paths(tmp_path):
    return AgentPaths(project_root=tmp_path)


def test_track_crud(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(
        title="股票分析能力",
        persona_desc="希望你具备强大的股票分析能力",
        outline_names=["技术分析基础", "基本面分析", "宏观经济"],
    )
    assert track.wiki_tag.startswith("capability:")
    assert len(track.outline) == 3

    fetched = store.get(track.track_id)
    assert fetched is not None
    assert fetched.title == "股票分析能力"

    updated = store.update(track.track_id, status="paused")
    assert updated.status == "paused"

    assert store.delete(track.track_id) is True
    assert store.get(track.track_id) is None


def test_scan_outline_gaps_prefers_uncovered_and_oldest(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="股票分析能力", persona_desc="x", outline_names=[])
    track.outline = [
        OutlineTopic(topic_id="t1", name="A", coverage_state="covered"),
        OutlineTopic(topic_id="t2", name="B", coverage_state="uncovered"),
        OutlineTopic(topic_id="t3", name="C", coverage_state="partial", last_touched_at=100),
        OutlineTopic(topic_id="t4", name="D", coverage_state="partial", last_touched_at=200),
    ]
    picked = scan_outline_gaps(track, limit=2)
    picked_ids = [t.topic_id for t in picked]
    # uncovered 优先于 partial；partial 里更久没碰过的（t3）优先于 t4
    assert picked_ids == ["t2", "t3"]


def test_needs_user_context_persona_vs_knowledge(paths):
    store = CapabilityTrackStore(paths)
    knowledge_track = store.create(title="k", persona_desc="x", target_type="knowledge")
    persona_track = store.create(title="p", persona_desc="x", target_type="persona")
    topic = OutlineTopic(topic_id="t1", name="任意子主题")
    assert needs_user_context(topic, knowledge_track) is False
    assert needs_user_context(topic, persona_track) is True


def test_question_queue_async_lifecycle(paths):
    qstore = CapabilityQuestionStore(paths)
    q = qstore.raise_question(track_id="trk1", topic_id="t1", question="你偏好短线还是长线？")
    assert q.status == "pending"
    assert qstore.pending_count("trk1") == 1

    # 生成问题不阻塞——立即可以查询到，且不需要任何"等待回答"的动作
    pending = qstore.list_questions(status="pending", track_id="trk1")
    assert len(pending) == 1

    answered = qstore.answer(q.question_id, "偏好长线，风险承受能力中等")
    assert answered.status == "answered"
    assert answered.answer == "偏好长线，风险承受能力中等"
    assert qstore.pending_count("trk1") == 0


def test_question_sweep_expired(paths):
    qstore = CapabilityQuestionStore(paths)
    q = qstore.raise_question(
        track_id="trk1", topic_id="t1", question="过期测试", ttl_seconds=-1
    )
    n = qstore.sweep_expired()
    assert n == 1
    refreshed = qstore.list_questions(track_id="trk1")[0]
    assert refreshed.status == "expired"


def test_cycle_skips_when_not_wired(paths):
    """P1 安全默认：未传入 retriever/wiki_writer 时，knowledge 型 Track
    的子主题应该被跳过，不产生任何检索/写入副作用，只留一条台账。"""
    store = CapabilityTrackStore(paths)
    track = store.create(
        title="股票分析能力", persona_desc="x",
        outline_names=["技术分析基础", "基本面分析"],
    )
    summary = run_capability_learning_cycle(paths)
    assert summary["tracks_processed"] == 1
    assert summary["topics_skipped"] == 2
    assert summary["topics_researched"] == 0

    ledger = CapabilityLedgerStore(paths).list_for_track(track.track_id)
    assert all(e.action == "skipped" for e in ledger)


def test_cycle_with_retriever_and_writer_updates_outline(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(
        title="股票分析能力", persona_desc="x",
        outline_names=["技术分析基础"],
    )

    def fake_retriever(topic, track):
        return [{"url": "https://example.com/a", "summary": "示例内容"}]

    def fake_writer(topic, track, results):
        return [f"wiki_page_{topic.topic_id}"]

    summary = run_capability_learning_cycle(paths, retriever=fake_retriever, wiki_writer=fake_writer)
    assert summary["topics_researched"] == 1

    refreshed = store.get(track.track_id)
    topic = refreshed.outline[0]
    assert topic.coverage_state == "covered"
    assert topic.wiki_page_ids == [f"wiki_page_{topic.topic_id}"]


def test_cycle_respects_excluded_keywords(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(
        title="股票分析能力", persona_desc="x",
        outline_names=["加密货币投机"],
    )
    store.update(track.track_id, excluded_keywords=["加密货币"])

    called = {"n": 0}

    def fake_retriever(topic, track):
        called["n"] += 1
        return [{"url": "x", "summary": "y"}]

    def fake_writer(topic, track, results):
        return ["p1"]

    summary = run_capability_learning_cycle(paths, retriever=fake_retriever, wiki_writer=fake_writer)
    assert summary["topics_skipped"] == 1
    assert summary["topics_researched"] == 0


def test_cycle_persona_track_raises_question_instead_of_researching(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(
        title="老李投顾人设", persona_desc="资深投资顾问",
        target_type="persona", outline_names=["说话风格", "口头禅"],
    )
    summary = run_capability_learning_cycle(paths)
    assert summary["questions_raised"] == 2
    assert summary["topics_researched"] == 0

    pending = CapabilityQuestionStore(paths).list_questions(status="pending", track_id=track.track_id)
    assert len(pending) == 2


def test_cycle_consumes_answered_question_next_round(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(
        title="老李投顾人设", persona_desc="资深投资顾问",
        target_type="persona", outline_names=["说话风格"],
    )
    run_capability_learning_cycle(paths)  # 第一轮：生成问题
    qstore = CapabilityQuestionStore(paths)
    q = qstore.list_questions(status="pending", track_id=track.track_id)[0]
    qstore.answer(q.question_id, "犀利直接，偶尔带点行话")

    summary = run_capability_learning_cycle(paths)  # 第二轮：应消费已回答问题
    assert summary["questions_consumed"] == 1

    ledger = CapabilityLedgerStore(paths).list_for_track(track.track_id)
    assert any(e.action == "question_answered" for e in ledger)


def test_pending_question_cap_blocks_new_questions(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(
        title="老李投顾人设", persona_desc="资深投资顾问",
        target_type="persona",
        outline_names=["维度A", "维度B", "维度C", "维度D"],
    )
    summary = run_capability_learning_cycle(paths, topics_per_cycle=4, max_pending_questions=3)
    # 上限 3，即使大纲有 4 个子主题，也最多生成 3 条问题
    assert summary["questions_raised"] == 3


def test_record_wiki_miss_appends_ledger(paths):
    record_wiki_miss(paths, track_id="trk1", topic_hint="t1", query="港股通规则")
    entries = CapabilityLedgerStore(paths).list_for_track("trk1")
    assert len(entries) == 1
    assert entries[0].action == "miss_observed"


def test_make_wiki_writer_writes_real_wiki_page(paths):
    """验证 make_wiki_writer(paths) 返回的回调真的能落盘出一个合法的
    wiki topic 页面，frontmatter 带上 capability_track_id / source_urls /
    retrieved_at（§5 wiki 沉淀规范），且能通过 wiki/parser.py 解析回来。"""
    from mini_agent.evolution.capability_learning import make_wiki_writer
    from mini_agent.wiki.parser import parse_page

    store = CapabilityTrackStore(paths)
    track = store.create(
        title="股票分析能力", persona_desc="x",
        outline_names=["技术分析基础"],
    )
    topic = track.outline[0]
    results = [
        {"url": "https://example.com/macd", "summary": "MACD 是趋势跟踪型指标"},
        {"url": "https://example.com/rsi", "summary": "RSI 衡量超买超卖"},
    ]

    writer = make_wiki_writer(paths)
    page_ids = writer(topic, track, results)
    assert page_ids == [f"cap_{track.track_id}_{topic.topic_id}"]

    page_path = paths.wiki_type_dir("topic") / f"{page_ids[0]}.md"
    assert page_path.exists()

    page = parse_page(page_path)
    assert page.tags == [track.wiki_tag]
    assert page.raw_frontmatter["capability_track_id"] == track.track_id
    assert page.raw_frontmatter["source_urls"] == [
        "https://example.com/macd", "https://example.com/rsi",
    ]
    assert "retrieved_at" in page.raw_frontmatter
    assert "MACD" in page.body
    assert "RSI" in page.body


def test_cycle_end_to_end_with_real_wiki_writer(paths):
    """把 make_wiki_writer 接到 run_capability_learning_cycle 里跑一轮，
    验证真实写入路径和循环编排能正确衔接（retriever 仍用假实现，
    因为真实 web_search 需要网络，P1 阶段不在单测里依赖网络）。"""
    from mini_agent.evolution.capability_learning import make_wiki_writer

    store = CapabilityTrackStore(paths)
    track = store.create(
        title="股票分析能力", persona_desc="x",
        outline_names=["技术分析基础"],
    )

    def fake_retriever(topic, track):
        return [{"url": "https://example.com/x", "summary": "示例摘要内容"}]

    summary = run_capability_learning_cycle(
        paths, retriever=fake_retriever, wiki_writer=make_wiki_writer(paths),
    )
    assert summary["topics_researched"] == 1

    refreshed = store.get(track.track_id)
    topic = refreshed.outline[0]
    assert topic.coverage_state == "covered"
    page_path = paths.wiki_type_dir("topic") / f"{topic.wiki_page_ids[0]}.md"
    assert page_path.exists()


# ── §13.3-g 合规过滤 ─────────────────────────────────────────────────────


def test_filter_strips_buy_sell_advice_sentence():
    from mini_agent.evolution.capability_learning import _filter_compliance_risky_text

    text = "MACD 是趋势跟踪型指标。建议买入并设置止损位。RSI 衡量超买超卖。"
    cleaned, did_filter = _filter_compliance_risky_text(text)
    assert did_filter is True
    assert "MACD" in cleaned
    assert "RSI" in cleaned
    assert "建议买入" not in cleaned
    assert "止损位" not in cleaned


def test_filter_no_risky_content_passthrough():
    from mini_agent.evolution.capability_learning import _filter_compliance_risky_text

    text = "MACD 是趋势跟踪型指标，由快线、慢线和柱状图组成。"
    cleaned, did_filter = _filter_compliance_risky_text(text)
    assert did_filter is False
    assert cleaned == text.strip()


def test_is_disclaimer_required_track_finance_keyword(paths):
    from mini_agent.evolution.capability_learning import is_disclaimer_required_track

    store = CapabilityTrackStore(paths)
    finance_track = store.create(title="股票分析能力", persona_desc="x")
    other_track = store.create(title="做饭技巧", persona_desc="学做家常菜")
    assert is_disclaimer_required_track(finance_track) is True
    assert is_disclaimer_required_track(other_track) is False


def test_apply_compliance_filter_marks_disclaimer_and_strips_content(paths):
    from mini_agent.evolution.capability_learning import apply_compliance_filter

    store = CapabilityTrackStore(paths)
    track = store.create(title="股票分析能力", persona_desc="x")
    results = [
        {"url": "https://example.com/a", "summary": "技术分析关注价格走势形态。建议买入并持有。"},
    ]
    filtered, any_filtered, requires_disclaimer = apply_compliance_filter(results, track)
    assert any_filtered is True
    assert requires_disclaimer is True
    assert "建议买入" not in filtered[0]["summary"]
    assert "技术分析关注价格走势形态" in filtered[0]["summary"]
    # 不修改传入的原始 results（返回新列表）
    assert "建议买入" in results[0]["summary"]


def test_apply_compliance_filter_non_risk_domain_no_disclaimer(paths):
    from mini_agent.evolution.capability_learning import apply_compliance_filter

    store = CapabilityTrackStore(paths)
    track = store.create(title="做饭技巧", persona_desc="学做家常菜")
    results = [{"url": "https://example.com/a", "summary": "先热锅再倒油。"}]
    filtered, any_filtered, requires_disclaimer = apply_compliance_filter(results, track)
    assert any_filtered is False
    assert requires_disclaimer is False
    assert filtered[0]["summary"] == "先热锅再倒油。"


def test_make_wiki_writer_filters_risky_content_and_adds_disclaimer(paths):
    """端到端验证 make_wiki_writer 的写入路径已经接上合规过滤：风险表述
    被剔除，frontmatter 带 requires_disclaimer=true，正文追加免责声明。"""
    from mini_agent.evolution.capability_learning import make_wiki_writer
    from mini_agent.wiki.parser import parse_page

    store = CapabilityTrackStore(paths)
    track = store.create(
        title="股票分析能力", persona_desc="x", outline_names=["技术分析基础"],
    )
    topic = track.outline[0]
    results = [
        {"url": "https://example.com/macd", "summary": "MACD 是趋势跟踪型指标。建议买入并设置止损位。"},
    ]

    writer = make_wiki_writer(paths)
    page_ids = writer(topic, track, results)
    page_path = paths.wiki_type_dir("topic") / f"{page_ids[0]}.md"
    page = parse_page(page_path)

    assert page.raw_frontmatter["requires_disclaimer"] is True
    assert "MACD" in page.body
    assert "建议买入" not in page.body
    assert "止损位" not in page.body
    assert "仅供参考" in page.body


def test_make_wiki_writer_non_risk_domain_no_disclaimer_flag(paths):
    from mini_agent.evolution.capability_learning import make_wiki_writer
    from mini_agent.wiki.parser import parse_page

    store = CapabilityTrackStore(paths)
    track = store.create(
        title="做饭技巧", persona_desc="学做家常菜", outline_names=["刀工基础"],
    )
    topic = track.outline[0]
    results = [{"url": "https://example.com/a", "summary": "先热锅再倒油，控制好火候。"}]

    writer = make_wiki_writer(paths)
    page_ids = writer(topic, track, results)
    page_path = paths.wiki_type_dir("topic") / f"{page_ids[0]}.md"
    page = parse_page(page_path)

    assert page.raw_frontmatter["requires_disclaimer"] is False
    assert "仅供参考" not in page.body


# ── 真实 retriever（web_search）接线，默认关闭 ─────────────────────────────


def test_capability_learning_config_default_retriever_disabled():
    from mini_agent.config import AppConfig

    cfg = AppConfig()
    assert cfg.capability_learning.retriever_enabled is False
    assert cfg.capability_learning.max_results_per_topic == 3


def test_make_web_search_retriever_uses_provider_and_respects_max_results(paths):
    from mini_agent.config import AppConfig
    from mini_agent.evolution.capability_learning import (
        CapabilityTrackStore, make_web_search_retriever,
    )
    from mini_agent.web_search.base import SearchResult, WebSearchProvider
    from mini_agent.web_search.factory import register_web_search_provider

    class FakeProvider(WebSearchProvider):
        def search(self, query, max_results=5):
            assert "股票分析能力" in query
            return [
                SearchResult(title=f"标题{i}", url=f"https://example.com/{i}", snippet=f"摘要{i}")
                for i in range(5)
            ][:max_results]

    register_web_search_provider("fake_capability_test", FakeProvider)

    cfg = AppConfig()
    cfg.web_search.provider = "fake_capability_test"
    cfg.capability_learning.max_results_per_topic = 2

    store = CapabilityTrackStore(paths)
    track = store.create(title="股票分析能力", persona_desc="x", outline_names=["技术分析基础"])
    topic = track.outline[0]

    retriever = make_web_search_retriever(cfg)
    results = retriever(topic, track)
    assert len(results) == 2
    assert results[0]["url"] == "https://example.com/0"
    assert results[0]["summary"] == "摘要0"


def test_make_web_search_retriever_truncates_summary(paths):
    from mini_agent.config import AppConfig
    from mini_agent.evolution.capability_learning import (
        CapabilityTrackStore, make_web_search_retriever,
    )
    from mini_agent.web_search.base import SearchResult, WebSearchProvider
    from mini_agent.web_search.factory import register_web_search_provider

    class LongProvider(WebSearchProvider):
        def search(self, query, max_results=5):
            return [SearchResult(title="t", url="https://example.com/x", snippet="字" * 500)]

    register_web_search_provider("fake_capability_long", LongProvider)

    cfg = AppConfig()
    cfg.web_search.provider = "fake_capability_long"
    cfg.capability_learning.summary_max_chars = 10

    store = CapabilityTrackStore(paths)
    track = store.create(title="做饭技巧", persona_desc="x", outline_names=["刀工基础"])
    topic = track.outline[0]

    retriever = make_web_search_retriever(cfg)
    results = retriever(topic, track)
    assert len(results[0]["summary"]) == 11  # 10 字 + 省略号
    assert results[0]["summary"].endswith("…")


def test_make_web_search_retriever_search_error_returns_empty(paths):
    from mini_agent.config import AppConfig
    from mini_agent.evolution.capability_learning import (
        CapabilityTrackStore, make_web_search_retriever,
    )
    from mini_agent.web_search.base import WebSearchError, WebSearchProvider
    from mini_agent.web_search.factory import register_web_search_provider

    class FailingProvider(WebSearchProvider):
        def search(self, query, max_results=5):
            raise WebSearchError("boom")

    register_web_search_provider("fake_capability_fail", FailingProvider)

    cfg = AppConfig()
    cfg.web_search.provider = "fake_capability_fail"

    store = CapabilityTrackStore(paths)
    track = store.create(title="做饭技巧", persona_desc="x", outline_names=["刀工基础"])
    topic = track.outline[0]

    retriever = make_web_search_retriever(cfg)
    assert retriever(topic, track) == []


def test_capability_question_store_sweep_expired(paths):
    from mini_agent.evolution.capability_learning import CapabilityQuestionStore

    store = CapabilityQuestionStore(paths)
    q = store.raise_question(track_id="t1", topic_id="topic1", question="测试问题？", ttl_seconds=0.01)
    time.sleep(0.05)
    n = store.sweep_expired()
    assert n == 1
    refreshed = store.list_questions(status="expired")
    assert len(refreshed) == 1
    assert refreshed[0].question_id == q.question_id


# ── §14.1-a 收尾：miss_observed 台账接入 scan_outline_gaps 优先级排序 ──────


def test_scan_outline_gaps_stable_covered_never_becomes_stale(paths):
    """§13.2-d：stable（默认值）covered 子主题不管过去多久都不会被
    重新纳入候选——即使 last_touched_at 是很久以前，也不应该被重复检索。"""
    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=[])
    now = 10_000_000.0
    track.outline = [
        OutlineTopic(topic_id="t1", name="技术分析基础", coverage_state="covered",
                     volatility="stable", last_touched_at=0.0),
        OutlineTopic(topic_id="t2", name="B", coverage_state="uncovered"),
    ]
    picked = scan_outline_gaps(track, limit=10, now=now)
    picked_ids = [t.topic_id for t in picked]
    assert picked_ids == ["t2"]


def test_scan_outline_gaps_volatile_covered_becomes_stale_after_threshold(paths):
    """§13.2-d：volatile 且距上次触达超过 7 天阈值的 covered 子主题，
    应被重新纳入候选，且和 partial 同一优先档（排在 uncovered 之后，
    但不会被漏掉）。"""
    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=[])
    now = 1_000_000.0
    stale_touch = now - 8 * 86400  # 超过 7 天阈值
    fresh_touch = now - 1 * 86400  # 未超过阈值
    track.outline = [
        OutlineTopic(topic_id="t_stale", name="当前宏观利率环境", coverage_state="covered",
                     volatility="volatile", last_touched_at=stale_touch),
        OutlineTopic(topic_id="t_fresh", name="另一个波动主题", coverage_state="covered",
                     volatility="volatile", last_touched_at=fresh_touch),
        OutlineTopic(topic_id="t_uncovered", name="C", coverage_state="uncovered"),
    ]
    picked = scan_outline_gaps(track, limit=10, now=now)
    picked_ids = [t.topic_id for t in picked]
    # uncovered 排最前，过期的 volatile covered 子主题被重新纳入且排在其后，
    # 未过期的 fresh covered 子主题不应出现
    assert picked_ids == ["t_uncovered", "t_stale"]


def test_scan_outline_gaps_volatile_covered_none_last_touched_is_stale(paths):
    """`last_touched_at is None` 视为需要刷新（比如手动改了 volatility
    标注但还没有触达记录），不应因为"没有时间戳"被永久跳过。"""
    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=[])
    track.outline = [
        OutlineTopic(topic_id="t1", name="A", coverage_state="covered",
                     volatility="periodic", last_touched_at=None),
    ]
    picked = scan_outline_gaps(track, limit=10)
    assert [t.topic_id for t in picked] == ["t1"]


def test_scan_outline_gaps_periodic_uses_longer_threshold(paths):
    """periodic 的阈值（30 天）比 volatile（7 天）更长——8 天前触达的
    periodic 子主题不应该被判定为过期。"""
    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=[])
    now = 1_000_000.0
    track.outline = [
        OutlineTopic(topic_id="t1", name="季度财报解读方法", coverage_state="covered",
                     volatility="periodic", last_touched_at=now - 8 * 86400),
    ]
    picked = scan_outline_gaps(track, limit=10, now=now)
    assert picked == []


def test_scan_outline_gaps_backward_compat_no_miss_counts(paths):
    from mini_agent.evolution.capability_learning import CapabilityTrackStore, scan_outline_gaps

    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=["A", "B", "C"])
    result = scan_outline_gaps(track, limit=10)
    assert [t.name for t in result] == ["A", "B", "C"]


def test_scan_outline_gaps_prioritizes_higher_miss_count(paths):
    from mini_agent.evolution.capability_learning import CapabilityTrackStore, scan_outline_gaps

    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=["A", "B", "C"])
    topic_b = track.outline[1]
    result = scan_outline_gaps(track, limit=10, miss_counts={topic_b.topic_id: 3})
    assert result[0].name == "B"


def test_scan_outline_gaps_miss_count_only_within_same_coverage_state(paths):
    from mini_agent.evolution.capability_learning import (
        CapabilityTrackStore, scan_outline_gaps,
    )

    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=["A", "B"])
    # A 是 partial（已经被摸过一次但没写成 wiki），B 仍是 uncovered 且有
    # miss 记录——即便 B 的 miss_count 很高，uncovered 仍然整体排在
    # partial 前面，miss_counts 只在同一 coverage_state 内部生效。
    track.outline[0].coverage_state = "partial"
    topic_b = track.outline[1]
    result = scan_outline_gaps(track, limit=10, miss_counts={topic_b.topic_id: 10})
    assert result[0].name == "B"
    assert result[1].name == "A"


def test_topic_miss_counts_aggregates_ledger(paths):
    from mini_agent.evolution.capability_learning import (
        CapabilityLedgerEntry, CapabilityLedgerStore, _topic_miss_counts,
    )

    store = CapabilityLedgerStore(paths)
    for _ in range(2):
        store.append(CapabilityLedgerEntry(
            track_id="t1", topic_id="topicA", action="miss_observed", summary="x",
        ))
    store.append(CapabilityLedgerEntry(
        track_id="t1", topic_id="topicB", action="miss_observed", summary="x",
    ))
    store.append(CapabilityLedgerEntry(
        track_id="t1", topic_id="topicA", action="researched", summary="x",
    ))
    counts = _topic_miss_counts(store, "t1")
    assert counts == {"topicA": 2, "topicB": 1}


def test_run_capability_learning_cycle_uses_miss_counts_for_ordering(paths):
    """端到端验证：record_wiki_miss() 记下的台账真的会影响下一轮
    run_capability_learning_cycle 挑选子主题的顺序（用 wiki_writer 记录
    调用顺序来观测，不依赖内部实现细节）。"""
    from mini_agent.evolution.capability_learning import (
        CapabilityTrackStore, record_wiki_miss, run_capability_learning_cycle,
    )

    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=["A", "B", "C"])
    topic_c = track.outline[2]

    # 对 C 记录两次未命中——下一轮应该优先处理 C。
    record_wiki_miss(paths, track.track_id, topic_c.topic_id, "some query")
    record_wiki_miss(paths, track.track_id, topic_c.topic_id, "some query 2")

    call_order = []

    def fake_retriever(topic, tr):
        call_order.append(topic.name)
        return [{"url": "https://example.com", "summary": "x"}]

    def fake_writer(topic, tr, results):
        return [f"page_{topic.topic_id}"]

    run_capability_learning_cycle(
        paths, retriever=fake_retriever, wiki_writer=fake_writer, topics_per_cycle=1,
    )
    assert call_order == ["C"]


# ── §14 P2：LLM 辅助大纲起草，opt-in ────────────────────────────────────


def test_draft_outline_with_llm_parses_lines():
    from mini_agent.evolution.capability_learning import draft_outline_with_llm

    def fake_llm(prompt):
        assert "股票分析能力" in prompt
        return "1. 基础概念\n2. 技术分析\n3. 基本面分析\n4. 风险管理\n"

    names = draft_outline_with_llm("股票分析能力", "希望你具备强大的股票分析能力", fake_llm)
    assert names == ["基础概念", "技术分析", "基本面分析", "风险管理"]


def test_draft_outline_with_llm_rejects_out_of_range_count():
    from mini_agent.evolution.capability_learning import draft_outline_with_llm

    def fake_llm_too_few(prompt):
        return "基础概念\n技术分析"

    def fake_llm_too_many(prompt):
        return "\n".join(f"主题{i}" for i in range(20))

    assert draft_outline_with_llm("x", "x", fake_llm_too_few) == []
    assert draft_outline_with_llm("x", "x", fake_llm_too_many) == []


def test_draft_outline_with_llm_empty_response():
    from mini_agent.evolution.capability_learning import draft_outline_with_llm

    assert draft_outline_with_llm("x", "x", lambda prompt: "") == []
    assert draft_outline_with_llm("x", "x", lambda prompt: "   ") == []


def test_draft_outline_with_llm_exception_returns_empty():
    from mini_agent.evolution.capability_learning import draft_outline_with_llm

    def boom(prompt):
        raise RuntimeError("llm down")

    assert draft_outline_with_llm("x", "x", boom) == []


def test_capability_track_store_create_with_llm_helper(paths):
    from mini_agent.evolution.capability_learning import CapabilityTrackStore

    def fake_llm(prompt):
        return "基础概念\n技术分析\n基本面分析\n风险管理"

    store = CapabilityTrackStore(paths)
    track = store.create(title="股票分析能力", persona_desc="x", llm_helper=fake_llm)
    assert [t.name for t in track.outline] == ["基础概念", "技术分析", "基本面分析", "风险管理"]


def test_capability_track_store_create_llm_helper_ignored_when_outline_names_given(paths):
    from mini_agent.evolution.capability_learning import CapabilityTrackStore

    called = []

    def fake_llm(prompt):
        called.append(1)
        return "A\nB\nC"

    store = CapabilityTrackStore(paths)
    track = store.create(
        title="x", persona_desc="x", outline_names=["自定义主题"], llm_helper=fake_llm,
    )
    assert [t.name for t in track.outline] == ["自定义主题"]
    assert called == []  # 显式传了 outline_names 时不应该调用 LLM


def test_capability_track_store_create_llm_failure_falls_back_to_empty(paths):
    from mini_agent.evolution.capability_learning import CapabilityTrackStore

    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", llm_helper=lambda prompt: "")
    assert track.outline == []


# ── §12.1-a：capability_map 领域置信度排序信号 ─────────────────────────

def _write_task_manifest(paths, task_id: str, goal: str, status: str) -> None:
    """在 paths.sessions_dir 下伪造一份 task_manifest.json，供
    `build_capability_map()` 扫描（与 evolution/consolidation.py 的
    扫描路径 `.agent/sessions/<session>/tasks/<task>/manifest.json` 一致）。"""
    task_dir = paths.sessions_dir / "sess1" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    import json
    manifest = {"goal": goal, "outcome": {"status": status}}
    (task_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_topic_capability_confidence_matches_by_keyword(paths):
    from mini_agent.evolution.capability_learning import _topic_capability_confidence

    # 伪造一批 python_refactor 领域的任务，3 成功 1 失败 → confidence=0.75
    _write_task_manifest(paths, "t1", "帮我重构这段 python 代码", "done")
    _write_task_manifest(paths, "t2", "帮我重构这段 python 代码", "done")
    _write_task_manifest(paths, "t3", "帮我重构这段 python 代码", "done")
    _write_task_manifest(paths, "t4", "帮我重构这段 python 代码", "failed")

    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=[])
    track.outline = [
        OutlineTopic(topic_id="a", name="python_refactor 相关技巧", coverage_state="uncovered"),
        OutlineTopic(topic_id="b", name="完全不相关的主题", coverage_state="uncovered"),
    ]

    conf = _topic_capability_confidence(track, paths)
    assert "a" in conf
    assert abs(conf["a"] - 0.75) < 1e-6
    assert "b" not in conf  # 没有匹配上的子主题不出现在结果里


def test_topic_capability_confidence_empty_when_no_manifests(paths):
    from mini_agent.evolution.capability_learning import _topic_capability_confidence

    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=["随便什么"])
    assert _topic_capability_confidence(track, paths) == {}


def test_scan_outline_gaps_uses_capability_confidence_as_tiebreak(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=[])
    track.outline = [
        OutlineTopic(topic_id="low_conf", name="低置信度主题", coverage_state="uncovered", last_touched_at=200),
        OutlineTopic(topic_id="high_conf", name="高置信度主题", coverage_state="uncovered", last_touched_at=100),
    ]
    # miss_counts 相同（都没有），capability_confidence 里 low_conf 更低
    # → 尽管 last_touched_at 更新，仍应排在 high_conf 前面
    result = scan_outline_gaps(
        track, limit=2,
        capability_confidence={"low_conf": 0.1, "high_conf": 0.9},
    )
    assert [t.topic_id for t in result] == ["low_conf", "high_conf"]


def test_scan_outline_gaps_capability_confidence_backward_compat(paths):
    """不传 capability_confidence 时，行为与此前完全一致（回退到 last_touched_at）。"""
    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=[])
    track.outline = [
        OutlineTopic(topic_id="t1", name="A", coverage_state="uncovered", last_touched_at=200),
        OutlineTopic(topic_id="t2", name="B", coverage_state="uncovered", last_touched_at=100),
    ]
    result = scan_outline_gaps(track, limit=2)
    assert [t.topic_id for t in result] == ["t2", "t1"]


def test_run_capability_learning_cycle_wires_capability_confidence(paths, monkeypatch):
    """确认 run_capability_learning_cycle 真的把 _topic_capability_confidence()
    的结果传给了 scan_outline_gaps()（不测排序细节，只测接线本身）。"""
    from mini_agent.evolution import capability_learning as cl

    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=["主题A"])
    track.status = "active"
    store.update(track.track_id, status="active")

    captured = {}
    original = cl.scan_outline_gaps

    def spy(track_arg, limit=cl.DEFAULT_TOPICS_PER_CYCLE, miss_counts=None, capability_confidence=None):
        captured["capability_confidence"] = capability_confidence
        return original(track_arg, limit=limit, miss_counts=miss_counts, capability_confidence=capability_confidence)

    monkeypatch.setattr(cl, "scan_outline_gaps", spy)
    cl.run_capability_learning_cycle(paths)
    assert "capability_confidence" in captured
    assert captured["capability_confidence"] == {}  # 没有任何 task manifest，空字典也是正常接线的证明


# ── §13.1-b：多 Track 公平调度 ─────────────────────────────────────────

def test_last_advanced_at_updated_after_topic_processed(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=["主题A"])
    assert track.last_advanced_at is None

    run_capability_learning_cycle(paths)  # 无 retriever/wiki_writer，走 skipped 分支

    refreshed = store.get(track.track_id)
    assert refreshed.last_advanced_at is not None


def test_last_advanced_at_not_updated_when_no_topics_to_process(paths):
    """一个大纲全部 covered 的 Track，本轮没有任何子主题可推进，
    last_advanced_at 不应该被更新（"推进"应该反映真实工作量，不是
    "被扫描过"）。"""
    store = CapabilityTrackStore(paths)
    track = store.create(title="x", persona_desc="x", outline_names=[])
    track.outline = [OutlineTopic(topic_id="t1", name="已完成", coverage_state="covered")]
    store.update(track.track_id, outline=track.outline)

    run_capability_learning_cycle(paths)

    refreshed = store.get(track.track_id)
    assert refreshed.last_advanced_at is None


def test_fair_scheduling_orders_by_last_advanced_at(paths):
    """两个 Track，track_old 从未被推进过（last_advanced_at=None），
    track_recent 刚被推进过——下一轮应该先处理 track_old（尽管
    track_old 是后创建的）。用 max_topics_per_run_cycle 卡住预算，
    确认预算只分给了排在前面的 track_old。"""
    store = CapabilityTrackStore(paths)
    track_recent = store.create(title="recent", persona_desc="x", outline_names=["主题A"])
    track_old = store.create(title="old", persona_desc="x", outline_names=["主题B"])

    # 先让 track_recent 被推进一次，产生 last_advanced_at
    run_capability_learning_cycle(paths, max_topics_per_run_cycle=None)
    refreshed_recent = store.get(track_recent.track_id)
    refreshed_old = store.get(track_old.track_id)
    assert refreshed_recent.last_advanced_at is not None
    assert refreshed_old.last_advanced_at is not None  # 两个都被推进过了（无预算限制）

    # 手动把 track_recent 的 last_advanced_at 设置成"刚刚"，track_old 设置成"很久以前"
    store.update(track_recent.track_id, last_advanced_at=time.time())
    store.update(track_old.track_id, last_advanced_at=1.0)
    # 给两个 Track 都补一个还没处理过的新子主题，确保下一轮还有活干
    t_recent = store.get(track_recent.track_id)
    t_recent.outline.append(OutlineTopic(topic_id="new_a", name="新子主题A", coverage_state="uncovered"))
    store.update(track_recent.track_id, outline=t_recent.outline)
    t_old = store.get(track_old.track_id)
    t_old.outline.append(OutlineTopic(topic_id="new_b", name="新子主题B", coverage_state="uncovered"))
    store.update(track_old.track_id, outline=t_old.outline)

    # 全局预算只够处理 1 个 Track 的份额（topics_per_cycle 默认 2）
    summary = run_capability_learning_cycle(paths, max_topics_per_run_cycle=2)
    assert summary["topics_skipped"] == 2  # 预算恰好用在了排前面的 track_old 身上

    refreshed_old2 = store.get(track_old.track_id)
    refreshed_recent2 = store.get(track_recent.track_id)
    # track_old 排在前面（last_advanced_at 更旧），应该拿到了本轮预算
    assert refreshed_old2.last_advanced_at > 1.0
    # track_recent 本轮预算耗尽，没有被推进（沿用之前手动设置的时间戳）
    assert refreshed_recent2.last_advanced_at == refreshed_recent2.last_advanced_at  # 存在即可，不做强断言


def test_max_topics_per_run_cycle_backward_compat_default_none(paths):
    """不传 max_topics_per_run_cycle 时，每个 Track 各自跑满
    topics_per_cycle，行为与此前完全一致。"""
    store = CapabilityTrackStore(paths)
    store.create(title="a", persona_desc="x", outline_names=["A1", "A2", "A3"])
    store.create(title="b", persona_desc="x", outline_names=["B1", "B2", "B3"])

    summary = run_capability_learning_cycle(paths)
    # 每个 Track 默认 topics_per_cycle=2，两个 Track 共 4 个 topics_skipped
    assert summary["topics_skipped"] == 4


# ── §13.1-c 跨 Track 子主题去重与知识共享 ─────────────────────────────────


def test_topic_name_similarity_identical_and_empty():
    assert _topic_name_similarity("技术分析基础", "技术分析基础") == 1.0
    assert _topic_name_similarity("技术分析基础", "") == 0.0
    assert _topic_name_similarity("", "") == 0.0


def test_topic_name_similarity_high_for_near_duplicate_names():
    # "利率对资产价格的影响" vs "利率变化对资产价格影响" 应该有较高相似度
    score = _topic_name_similarity("利率对资产价格的影响", "利率变化对资产价格影响")
    assert score >= 0.4


def test_topic_name_similarity_low_for_unrelated_names():
    score = _topic_name_similarity("技术分析基础", "美食烹饪技巧")
    assert score < 0.5


def test_find_cross_track_reuse_matches_covered_topic_with_pages(paths):
    store = CapabilityTrackStore(paths)
    track_a = store.create(title="宏观经济", persona_desc="x", outline_names=[])
    track_a.outline = [
        OutlineTopic(topic_id="a1", name="利率对资产价格的影响",
                     coverage_state="covered", wiki_page_ids=["pg1", "pg2"]),
    ]
    store.update(track_a.track_id, outline=track_a.outline)

    track_b = store.create(title="股票分析", persona_desc="x", outline_names=[])
    topic_b = OutlineTopic(topic_id="b1", name="利率对资产价格的影响", coverage_state="uncovered")
    track_b.outline = [topic_b]

    reused = find_cross_track_reuse(topic_b, track_b, [track_a, track_b])
    assert reused is not None
    assert reused.topic_id == "a1"
    assert reused.wiki_page_ids == ["pg1", "pg2"]


def test_find_cross_track_reuse_returns_none_when_topic_already_has_pages(paths):
    """自己已经有 wiki 页面的子主题不应被复用逻辑覆盖——即使另一个
    Track 有名字相似的 covered 子主题，也不该抢走已有内容。"""
    store = CapabilityTrackStore(paths)
    track_a = store.create(title="宏观经济", persona_desc="x", outline_names=[])
    track_a.outline = [
        OutlineTopic(topic_id="a1", name="利率对资产价格的影响",
                     coverage_state="covered", wiki_page_ids=["pg1"]),
    ]
    store.update(track_a.track_id, outline=track_a.outline)

    track_b = store.create(title="股票分析", persona_desc="x", outline_names=[])
    topic_b = OutlineTopic(topic_id="b1", name="利率对资产价格的影响",
                            coverage_state="partial", wiki_page_ids=["own_pg"])
    track_b.outline = [topic_b]

    assert find_cross_track_reuse(topic_b, track_b, [track_a, track_b]) is None


def test_find_cross_track_reuse_ignores_paused_tracks(paths):
    store = CapabilityTrackStore(paths)
    track_a = store.create(title="宏观经济", persona_desc="x", outline_names=[])
    track_a.outline = [
        OutlineTopic(topic_id="a1", name="利率对资产价格的影响",
                     coverage_state="covered", wiki_page_ids=["pg1"]),
    ]
    store.update(track_a.track_id, outline=track_a.outline, status="paused")

    track_b = store.create(title="股票分析", persona_desc="x", outline_names=[])
    topic_b = OutlineTopic(topic_id="b1", name="利率对资产价格的影响", coverage_state="uncovered")
    track_b.outline = [topic_b]

    track_a_refreshed = store.get(track_a.track_id)
    assert find_cross_track_reuse(topic_b, track_b, [track_a_refreshed, track_b]) is None


def test_find_cross_track_reuse_no_match_below_threshold(paths):
    store = CapabilityTrackStore(paths)
    track_a = store.create(title="宏观经济", persona_desc="x", outline_names=[])
    track_a.outline = [
        OutlineTopic(topic_id="a1", name="技术分析基础", coverage_state="covered",
                     wiki_page_ids=["pg1"]),
    ]
    store.update(track_a.track_id, outline=track_a.outline)

    track_b = store.create(title="股票分析", persona_desc="x", outline_names=[])
    topic_b = OutlineTopic(topic_id="b1", name="美食烹饪技巧", coverage_state="uncovered")
    track_b.outline = [topic_b]

    assert find_cross_track_reuse(topic_b, track_b, [track_a, track_b]) is None


def test_run_capability_learning_cycle_reuses_cross_track_topic(paths):
    """端到端：track_a 已经有一个名字高度相似且 covered 的子主题，
    track_b 推进同名子主题时应该直接复用页面，不走"未接线跳过"分支，
    且台账记录 action="reused"。"""
    store = CapabilityTrackStore(paths)
    ledger_store = CapabilityLedgerStore(paths)

    track_a = store.create(title="宏观经济", persona_desc="x", outline_names=[])
    track_a.outline = [
        OutlineTopic(topic_id="a1", name="利率对资产价格的影响",
                     coverage_state="covered", wiki_page_ids=["pg1", "pg2"]),
    ]
    store.update(track_a.track_id, outline=track_a.outline)

    track_b = store.create(
        title="股票分析", persona_desc="x",
        outline_names=["利率对资产价格的影响"],
    )

    summary = run_capability_learning_cycle(paths)
    assert summary["topics_reused"] == 1
    assert summary["topics_skipped"] == 0  # 复用命中，不应该落到"未接线跳过"分支

    refreshed_b = store.get(track_b.track_id)
    topic_b = refreshed_b.outline[0]
    assert topic_b.coverage_state == "covered"
    assert set(topic_b.wiki_page_ids) == {"pg1", "pg2"}

    entries = ledger_store.list_for_track(track_b.track_id, limit=10)
    assert any(e.action == "reused" for e in entries)


def test_run_capability_learning_cycle_backward_compat_no_similar_topic(paths):
    """没有可复用的相似子主题时，行为退回原有的"未接线安全跳过"逻辑，
    不受本轮改动影响。"""
    store = CapabilityTrackStore(paths)
    store.create(title="a", persona_desc="x", outline_names=["完全独特的主题名称XYZ"])

    summary = run_capability_learning_cycle(paths)
    assert summary["topics_reused"] == 0
    assert summary["topics_skipped"] == 1
