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
