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
    draft_persona_markdown,
    persona_draft_completeness,
    detect_real_person_reference,
    save_persona_draft,
    load_persona_draft,
    publish_persona_draft,
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


# ── 真实 retriever（web_search）接线，默认开启 ─────────────────────────────


def test_capability_learning_config_default_retriever_enabled():
    from mini_agent.config import AppConfig

    cfg = AppConfig()
    assert cfg.capability_learning.retriever_enabled is True
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


# ── [v0.22 §14.4] agent 检索模式：make_agent_retriever ─────────────────────


def test_capability_learning_config_default_retriever_mode_is_web_search():
    from mini_agent.config import AppConfig

    cfg = AppConfig()
    assert cfg.capability_learning.retriever_mode == "web_search"
    assert cfg.capability_learning.agent_retriever_max_turns == 6
    assert cfg.capability_learning.agent_retriever_timeout_seconds == 240


def _install_fake_sub_agent(monkeypatch, *, output: str, status=None, allowed_tools_capture=None):
    from mini_agent.orchestrator.task import TaskResult, TaskStatus
    import mini_agent.orchestrator.sub_agent as sub_agent_mod

    final_status = status if status is not None else TaskStatus.DONE

    class FakeSubAgent:
        def __init__(self, record, base_cfg, **kwargs):
            self.record = record
            if allowed_tools_capture is not None:
                allowed_tools_capture.append(list(record.task.allowed_tools or []))

        def start(self):
            self.record.status = final_status
            if final_status == TaskStatus.DONE:
                self.record.result = TaskResult(output=output)
            else:
                self.record.result = TaskResult(output="", error="boom")

        def join(self, timeout=None):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(sub_agent_mod, "SubAgent", FakeSubAgent)
    return sub_agent_mod


def test_make_agent_retriever_uses_restricted_tools_and_returns_summary(paths, monkeypatch):
    from mini_agent.config import AppConfig
    from mini_agent.evolution.capability_learning import (
        CapabilityTrackStore, make_agent_retriever,
    )

    captured_tools = []
    _install_fake_sub_agent(
        monkeypatch,
        output="调研摘要：xxx 来源：https://example.com",
        allowed_tools_capture=captured_tools,
    )

    cfg = AppConfig()
    cfg.capability_learning.retriever_mode = "agent"

    store = CapabilityTrackStore(paths)
    track = store.create(title="股票分析能力", persona_desc="x", outline_names=["技术分析基础"])
    topic = track.outline[0]

    retriever = make_agent_retriever(cfg)
    results = retriever(topic, track)

    assert len(results) == 1
    assert results[0]["summary"] == "调研摘要：xxx 来源：https://example.com"
    assert results[0]["source"] == "agent_research"
    # 只能用只读/检索类工具，不能有文件写入/命令执行类工具
    assert captured_tools[0]
    assert "write_file" not in captured_tools[0]
    assert "bash" not in captured_tools[0]
    assert "web_search" in captured_tools[0]
    assert "skill_activate" in captured_tools[0]


def test_make_agent_retriever_truncates_summary(paths, monkeypatch):
    from mini_agent.config import AppConfig
    from mini_agent.evolution.capability_learning import (
        CapabilityTrackStore, make_agent_retriever,
    )

    _install_fake_sub_agent(monkeypatch, output="字" * 500)

    cfg = AppConfig()
    cfg.capability_learning.retriever_mode = "agent"
    cfg.capability_learning.summary_max_chars = 10

    store = CapabilityTrackStore(paths)
    track = store.create(title="做饭技巧", persona_desc="x", outline_names=["刀工基础"])
    topic = track.outline[0]

    retriever = make_agent_retriever(cfg)
    results = retriever(topic, track)
    assert len(results[0]["summary"]) == 11  # 10 字 + 省略号
    assert results[0]["summary"].endswith("…")


def test_make_agent_retriever_failed_task_returns_empty(paths, monkeypatch):
    from mini_agent.config import AppConfig
    from mini_agent.orchestrator.task import TaskStatus
    from mini_agent.evolution.capability_learning import (
        CapabilityTrackStore, make_agent_retriever,
    )

    _install_fake_sub_agent(monkeypatch, output="", status=TaskStatus.FAILED)

    cfg = AppConfig()
    cfg.capability_learning.retriever_mode = "agent"

    store = CapabilityTrackStore(paths)
    track = store.create(title="做饭技巧", persona_desc="x", outline_names=["刀工基础"])
    topic = track.outline[0]

    retriever = make_agent_retriever(cfg)
    assert retriever(topic, track) == []


def test_capability_cmd_selects_agent_retriever_by_mode(monkeypatch):
    """确认 /capability cycle 按 retriever_mode 选择正确的 retriever 工厂函数
    （只验证选择分支本身，不实际跑一轮循环/启动真实 SubAgent）。"""
    from mini_agent.config import AppConfig
    import mini_agent.evolution.capability_learning as cl

    cfg = AppConfig()
    cfg.capability_learning.retriever_mode = "agent"
    cfg.capability_learning.retriever_enabled = True

    called = {}

    def fake_make_agent_retriever(cfg_arg):
        called["mode"] = "agent"
        return lambda topic, track: []

    def fake_make_web_search_retriever(cfg_arg):
        called["mode"] = "web_search"
        return lambda topic, track: []

    monkeypatch.setattr(cl, "make_agent_retriever", fake_make_agent_retriever)
    monkeypatch.setattr(cl, "make_web_search_retriever", fake_make_web_search_retriever)

    retriever_mode = str(getattr(cfg.capability_learning, "retriever_mode", "web_search") or "web_search")
    if retriever_mode == "agent":
        cl.make_agent_retriever(cfg)
    else:
        cl.make_web_search_retriever(cfg)

    assert called["mode"] == "agent"
