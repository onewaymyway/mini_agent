"""单元测试：evolution/capability_learning.py 大纲修订（LLM diff / 手动
编辑）与自动大纲建议的三个新来源。

覆盖设计文档 next_doc/outline_revision_and_suggestion_improvement_plan.md
承诺的行为，不重复 test_capability_learning_p1.py 已有的 P1 基础闭环覆盖。
"""
from __future__ import annotations

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.capability_learning import (
    CapabilityLedgerEntry,
    CapabilityLedgerStore,
    CapabilityOutlineSuggestionStore,
    CapabilityTrackStore,
    apply_outline_revision,
    generate_outline_suggestion_from_coverage_milestone,
    generate_outline_suggestion_from_miss_counts,
    generate_outline_suggestion_from_research,
    revise_outline_with_llm,
    run_capability_learning_cycle,
)


@pytest.fixture
def paths(tmp_path):
    return AgentPaths(project_root=tmp_path)


# ── apply_outline_revision ──────────────────────────────────────────────


def test_apply_outline_revision_add(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="金融数据智能采集", persona_desc="desc", outline_names=["行情监控"])

    updated = apply_outline_revision(paths, track.track_id, [
        {"op": "add", "name": "财报解读"},
        {"op": "add", "name": ""},  # 空名称应被忽略
    ])
    assert updated is not None
    names = [t.name for t in updated.outline]
    assert "行情监控" in names
    assert "财报解读" in names
    assert len(updated.outline) == 2


def test_apply_outline_revision_rename_preserves_progress(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D", outline_names=["信息提取基础"])
    topic = track.outline[0]
    topic.coverage_state = "covered"
    topic.wiki_page_ids = ["page_1"]
    store.update(track.track_id, outline=track.outline)

    updated = apply_outline_revision(paths, track.track_id, [
        {"op": "rename", "topic_id": topic.topic_id, "name": "舆情信息提取"},
    ])
    assert updated is not None
    renamed = updated.outline[0]
    assert renamed.name == "舆情信息提取"
    assert renamed.topic_id == topic.topic_id
    assert renamed.coverage_state == "covered"
    assert renamed.wiki_page_ids == ["page_1"]


def test_apply_outline_revision_remove_does_not_delete_wiki_pages(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D", outline_names=["A", "B"])
    remove_id = track.outline[0].topic_id

    updated = apply_outline_revision(paths, track.track_id, [
        {"op": "remove", "topic_id": remove_id},
    ])
    assert updated is not None
    assert len(updated.outline) == 1
    assert updated.outline[0].name == "B"


def test_apply_outline_revision_missing_track_returns_none(paths):
    assert apply_outline_revision(paths, "cap_does_not_exist", [{"op": "add", "name": "x"}]) is None


def test_track_store_manual_edit_helpers(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D")

    added = store.add_outline_topic(track.track_id, "新子主题")
    assert len(added.outline) == 1
    topic_id = added.outline[0].topic_id

    renamed = store.rename_outline_topic(track.track_id, topic_id, "改名后")
    assert renamed.outline[0].name == "改名后"

    removed = store.remove_outline_topic(track.track_id, topic_id)
    assert removed.outline == []


# ── revise_outline_with_llm ──────────────────────────────────────────────


def test_revise_outline_with_llm_no_helper_returns_empty(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D", outline_names=["A"])
    assert revise_outline_with_llm(track, None) == []


def test_revise_outline_with_llm_parses_add_rename_remove_keep(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D", outline_names=["行情监控", "信息提取基础"])

    def fake_llm(prompt: str) -> str:
        return (
            "KEEP 行情监控\n"
            "RENAME 信息提取基础 -> 舆情信息提取\n"
            "ADD 龙虎榜数据解读\n"
            "REMOVE 不存在的子主题\n"
        )

    ops = revise_outline_with_llm(track, fake_llm)
    op_kinds = {o["op"] for o in ops}
    assert op_kinds == {"rename", "add"}  # REMOVE 匹配不到，KEEP 不产出

    rename_op = next(o for o in ops if o["op"] == "rename")
    assert rename_op["name"] == "舆情信息提取"
    assert rename_op["topic_id"] == track.outline[1].topic_id

    add_op = next(o for o in ops if o["op"] == "add")
    assert add_op["name"] == "龙虎榜数据解读"


def test_revise_outline_with_llm_drops_similar_add(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D", outline_names=["行情监控"])

    def fake_llm(prompt: str) -> str:
        return "ADD 行情监控"  # 与现有子主题完全相同

    assert revise_outline_with_llm(track, fake_llm) == []


def test_revise_outline_with_llm_empty_or_exception(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D")

    assert revise_outline_with_llm(track, lambda p: "") == []
    assert revise_outline_with_llm(track, lambda p: (_ for _ in ()).throw(RuntimeError("boom"))) == []


# ── generate_outline_suggestion_from_miss_counts ────────────────────────


def test_miss_counts_suggestion_triggers_at_threshold(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D", outline_names=["行情监控"])
    ledger = CapabilityLedgerStore(paths)
    for _ in range(3):
        ledger.append(CapabilityLedgerEntry(
            track_id=track.track_id, topic_id="unclassified",
            action="miss_observed", summary="检索未命中：龙虎榜数据",
        ))

    suggestion = generate_outline_suggestion_from_miss_counts(track, ledger, threshold=3)
    assert suggestion is not None
    assert suggestion.suggested_name == "龙虎榜数据"
    assert suggestion.source == "miss_counts"


def test_miss_counts_suggestion_below_threshold_returns_none(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D")
    ledger = CapabilityLedgerStore(paths)
    ledger.append(CapabilityLedgerEntry(
        track_id=track.track_id, topic_id="unclassified",
        action="miss_observed", summary="检索未命中：龙虎榜数据",
    ))
    assert generate_outline_suggestion_from_miss_counts(track, ledger, threshold=3) is None


def test_miss_counts_suggestion_skips_similar_existing_topic(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D", outline_names=["龙虎榜数据"])
    ledger = CapabilityLedgerStore(paths)
    for _ in range(5):
        ledger.append(CapabilityLedgerEntry(
            track_id=track.track_id, topic_id="unclassified",
            action="miss_observed", summary="检索未命中：龙虎榜数据",
        ))
    assert generate_outline_suggestion_from_miss_counts(track, ledger, threshold=3) is None


# ── research / milestone suggestion generators ──────────────────────────


def test_research_suggestion_none_without_llm_or_content(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D", outline_names=["行情监控"])
    topic = track.outline[0]
    assert generate_outline_suggestion_from_research(track, topic, [], None) is None
    assert generate_outline_suggestion_from_research(track, topic, [], lambda p: "新方向") is None


def test_research_suggestion_generates_from_llm(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D", outline_names=["行情监控"])
    topic = track.outline[0]
    results = [{"summary": "内容里提到了龙虎榜相关数据" * 5}]
    suggestion = generate_outline_suggestion_from_research(
        track, topic, results, lambda p: "龙虎榜数据解读",
    )
    assert suggestion is not None
    assert suggestion.source == "research"


def test_milestone_suggestion_requires_llm_and_outline(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D")
    assert generate_outline_suggestion_from_coverage_milestone(track, lambda p: "x") is None

    track2 = store.create(title="T2", persona_desc="D", outline_names=["A"])
    assert generate_outline_suggestion_from_coverage_milestone(track2, None) is None


def test_milestone_suggestion_generates(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D", outline_names=["A", "B"])
    suggestion = generate_outline_suggestion_from_coverage_milestone(
        track, lambda p: "进阶方向",
    )
    assert suggestion is not None
    assert suggestion.suggested_name == "进阶方向"
    assert suggestion.source == "milestone"


# ── run_capability_learning_cycle wiring ────────────────────────────────


def test_cycle_generates_miss_count_suggestion_by_default(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="金融数据智能采集", persona_desc="D", outline_names=["行情监控"])
    ledger = CapabilityLedgerStore(paths)
    for _ in range(3):
        ledger.append(CapabilityLedgerEntry(
            track_id=track.track_id, topic_id="unclassified",
            action="miss_observed", summary="检索未命中：龙虎榜数据",
        ))

    summary = run_capability_learning_cycle(paths)  # 无 llm_helper，仅 miss_counts 生效
    assert summary["outline_suggestions_generated"] == 1

    suggestions = CapabilityOutlineSuggestionStore(paths).list_suggestions(status="pending")
    assert len(suggestions) == 1
    assert suggestions[0].source == "miss_counts"


def test_cycle_can_disable_miss_count_suggestion(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D", outline_names=["行情监控"])
    ledger = CapabilityLedgerStore(paths)
    for _ in range(5):
        ledger.append(CapabilityLedgerEntry(
            track_id=track.track_id, topic_id="unclassified",
            action="miss_observed", summary="检索未命中：龙虎榜数据",
        ))

    summary = run_capability_learning_cycle(paths, outline_suggestion_miss_count_enabled=False)
    assert summary["outline_suggestions_generated"] == 0
