"""单元测试：evolution/capability_learning.py 空大纲 Track 自动起草大纲兜底。

覆盖设计文档 next_doc/empty_outline_auto_draft_plan.md 承诺的行为，不重复
test_capability_learning_p1.py / test_capability_outline_revision_and_
suggestions.py 已有的覆盖。
"""
from __future__ import annotations

import time

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.capability_learning import (
    CapabilityLedgerStore,
    CapabilityTrackStore,
    run_capability_learning_cycle,
)


@pytest.fixture
def paths(tmp_path):
    return AgentPaths(project_root=tmp_path)


def _make_empty_track(paths, created_hours_ago: float = 25.0):
    store = CapabilityTrackStore(paths)
    track = store.create(title="金融数据智能采集", persona_desc="desc")
    assert track.outline == []
    data = track.to_dict()
    data["created_at"] = time.time() - created_hours_ago * 3600
    from mini_agent.evolution.capability_learning import CapabilityTrack
    tracks = store._load_all()
    for i, t in enumerate(tracks):
        if t.track_id == track.track_id:
            tracks[i] = CapabilityTrack.from_dict(data)
    store._save_all(tracks)
    return store.get(track.track_id)


def _llm_helper_returns(names_text):
    def _helper(prompt):
        return names_text
    return _helper


# ── 不触发的情况 ──────────────────────────────────────────────────────


def test_not_triggered_when_disabled(paths):
    track = _make_empty_track(paths, created_hours_ago=25.0)
    result = run_capability_learning_cycle(
        paths,
        llm_helper=_llm_helper_returns("行情监控\n财报解读\n风险预警\n宏观分析"),
        empty_outline_auto_draft_enabled=False,
    )
    assert result["outline_auto_drafted"] == 0
    reloaded = CapabilityTrackStore(paths).get(track.track_id)
    assert reloaded.outline == []


def test_not_triggered_without_llm_helper(paths):
    track = _make_empty_track(paths, created_hours_ago=25.0)
    result = run_capability_learning_cycle(
        paths,
        llm_helper=None,
        empty_outline_auto_draft_enabled=True,
    )
    assert result["outline_auto_drafted"] == 0
    reloaded = CapabilityTrackStore(paths).get(track.track_id)
    assert reloaded.outline == []


def test_not_triggered_before_timeout(paths):
    track = _make_empty_track(paths, created_hours_ago=1.0)
    result = run_capability_learning_cycle(
        paths,
        llm_helper=_llm_helper_returns("行情监控\n财报解读\n风险预警\n宏观分析"),
        empty_outline_auto_draft_enabled=True,
        empty_outline_auto_draft_after_hours=24.0,
    )
    assert result["outline_auto_drafted"] == 0
    reloaded = CapabilityTrackStore(paths).get(track.track_id)
    assert reloaded.outline == []


def test_not_triggered_when_outline_not_empty(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="D", outline_names=["已有子主题"])
    data = track.to_dict()
    data["created_at"] = time.time() - 25 * 3600
    from mini_agent.evolution.capability_learning import CapabilityTrack
    tracks = store._load_all()
    tracks[0] = CapabilityTrack.from_dict(data)
    store._save_all(tracks)

    result = run_capability_learning_cycle(
        paths,
        llm_helper=_llm_helper_returns("不应该被用到"),
        empty_outline_auto_draft_enabled=True,
    )
    assert result["outline_auto_drafted"] == 0
    reloaded = store.get(track.track_id)
    assert [t.name for t in reloaded.outline] == ["已有子主题"]


# ── 触发的情况 ────────────────────────────────────────────────────────


def test_triggered_drafts_and_persists_outline(paths):
    track = _make_empty_track(paths, created_hours_ago=25.0)
    result = run_capability_learning_cycle(
        paths,
        llm_helper=_llm_helper_returns("行情监控\n财报解读\n风险预警\n宏观分析"),
        empty_outline_auto_draft_enabled=True,
        empty_outline_auto_draft_after_hours=24.0,
    )
    assert result["outline_auto_drafted"] == 1
    assert result["outline_auto_draft_skipped"] == 0

    reloaded = CapabilityTrackStore(paths).get(track.track_id)
    assert len(reloaded.outline) == 4
    assert all(t.coverage_state == "uncovered" for t in reloaded.outline)

    ledger = CapabilityLedgerStore(paths).list_for_track(track.track_id)
    assert any(e.action == "outline_auto_drafted" for e in ledger)


def test_triggered_uses_new_outline_within_same_cycle(paths):
    """起草成功后本轮 scan_outline_gaps() 应该能立刻用上新大纲（不用等
    下一轮），体现为 topics_researched/topics_skipped 等统计不再是 0。"""
    _make_empty_track(paths, created_hours_ago=25.0)
    result = run_capability_learning_cycle(
        paths,
        llm_helper=_llm_helper_returns("行情监控\n财报解读\n风险预警\n宏观分析"),
        empty_outline_auto_draft_enabled=True,
        empty_outline_auto_draft_after_hours=24.0,
    )
    assert result["outline_auto_drafted"] == 1
    # 没传 retriever，本轮子主题会被记 skipped 台账而不是真正检索，但
    # 至少证明本轮确实尝试推进了新大纲里的子主题（不是 0）。
    assert result["topics_skipped"] >= 1


def test_llm_failure_records_skip_and_retries_next_cycle(paths):
    track = _make_empty_track(paths, created_hours_ago=25.0)
    result = run_capability_learning_cycle(
        paths,
        llm_helper=_llm_helper_returns(""),  # 空返回 → draft_outline_with_llm 降级为 []
        empty_outline_auto_draft_enabled=True,
        empty_outline_auto_draft_after_hours=24.0,
    )
    assert result["outline_auto_drafted"] == 0
    assert result["outline_auto_draft_skipped"] == 1

    reloaded = CapabilityTrackStore(paths).get(track.track_id)
    assert reloaded.outline == []  # 仍为空，created_at 未被改动

    ledger = CapabilityLedgerStore(paths).list_for_track(track.track_id)
    assert any(e.action == "outline_auto_draft_skipped" for e in ledger)

    # 下一轮（LLM 这次恢复正常）应该还会再尝试一次。
    result2 = run_capability_learning_cycle(
        paths,
        llm_helper=_llm_helper_returns("行情监控\n财报解读\n风险预警\n宏观分析"),
        empty_outline_auto_draft_enabled=True,
        empty_outline_auto_draft_after_hours=24.0,
    )
    assert result2["outline_auto_drafted"] == 1
