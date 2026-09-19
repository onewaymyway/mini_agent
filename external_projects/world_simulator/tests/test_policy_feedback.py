"""tests/test_policy_feedback.py — 阶段三十四（4.5 节第三批，用户反馈
反哺画像）单元测试。

对应 `next_doc/world_simulator_agent_preview_and_adaptive_policy_plan.md`
第 7 节验收标准：关键词分组统计触发提示的阈值逻辑、`acknowledged` 状态
的存取。不涉及任何 LLM 调用，不需要人工验证（同子方案 7 节说明）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import world_simulator.policy_feedback as pf


def test_record_feedback_requires_non_empty_reason(tmp_path):
    with pytest.raises(pf.PolicyFeedbackError):
        pf.record_feedback(tmp_path, "sim_a", branch="main", step=1, user_reason="   ")


def test_record_feedback_persists_and_loads_back(tmp_path):
    item = pf.record_feedback(
        tmp_path, "sim_a", branch="main", step=3, user_reason="太保守了，明明有更好的选择"
    )
    assert item.sim_id == "sim_a"
    assert item.branch == "main"
    assert item.step == 3
    assert item.acknowledged is False
    assert item.created_at

    loaded = pf.load_all(tmp_path, "sim_a")
    assert len(loaded) == 1
    assert loaded[0].id == item.id
    assert loaded[0].user_reason == "太保守了，明明有更好的选择"


def test_find_for_step_filters_by_branch_and_step(tmp_path):
    pf.record_feedback(tmp_path, "sim_a", branch="main", step=1, user_reason="r1")
    pf.record_feedback(tmp_path, "sim_a", branch="main", step=2, user_reason="r2")
    pf.record_feedback(tmp_path, "sim_a", branch="branch_b", step=1, user_reason="r3")

    assert [it.user_reason for it in pf.find_for_step(tmp_path, "sim_a", branch="main", step=1)] == ["r1"]
    assert pf.find_for_step(tmp_path, "sim_a", branch="main", step=99) == []


def test_acknowledge_marks_note_and_is_idempotent_on_unknown_id(tmp_path):
    item = pf.record_feedback(tmp_path, "sim_a", branch="main", step=1, user_reason="r1")

    updated = pf.acknowledge(tmp_path, "sim_a", item.id)
    assert updated is not None
    assert updated.acknowledged is True

    reloaded = pf.load_all(tmp_path, "sim_a")
    assert reloaded[0].acknowledged is True

    # 未知 id：不抛异常，返回 None，不影响已有记录
    assert pf.acknowledge(tmp_path, "sim_a", "not-a-real-id") is None
    assert pf.load_all(tmp_path, "sim_a")[0].acknowledged is True


def test_summarize_feedback_filters_by_branch_and_sorts_by_created_at_desc():
    items = [
        pf.PolicyFeedbackNote(
            id="id1", sim_id="sim_a", branch="main", step=1,
            user_reason="r1", created_at="2026-01-01T00:00:00",
        ),
        pf.PolicyFeedbackNote(
            id="id2", sim_id="sim_a", branch="main", step=2,
            user_reason="r2", created_at="2026-01-02T00:00:00",
        ),
        pf.PolicyFeedbackNote(
            id="id3", sim_id="sim_a", branch="branch_b", step=1,
            user_reason="r3", created_at="2026-01-03T00:00:00",
        ),
    ]

    summary = pf.summarize_feedback(items, branch="main")
    assert [it.user_reason for it in summary["unacknowledged"]] == ["r2", "r1"]


def test_summarize_feedback_no_suggestion_below_threshold():
    items = [
        pf.PolicyFeedbackNote(
            id=f"id{i}", sim_id="sim_a", branch="main", step=i,
            user_reason="太保守了", created_at=f"2026-01-0{i}T00:00:00",
        )
        for i in range(1, 3)  # 只有 2 条，阈值是 3
    ]
    summary = pf.summarize_feedback(items, branch="main")
    assert summary["suggestion"] is None


def test_summarize_feedback_suggestion_when_group_hits_threshold():
    items = [
        pf.PolicyFeedbackNote(
            id=f"id{i}", sim_id="sim_a", branch="main", step=i,
            user_reason="太保守了，完全没必要", created_at=f"2026-01-0{i}T00:00:00",
        )
        for i in range(1, 4)  # 3 条命中「太保守」分组，达到阈值
    ]
    summary = pf.summarize_feedback(items, branch="main")
    assert summary["suggestion"] is not None
    assert "太保守" in summary["suggestion"]


def test_summarize_feedback_aggressive_group_suggestion():
    items = [
        pf.PolicyFeedbackNote(
            id=f"id{i}", sim_id="sim_a", branch="main", step=i,
            user_reason="这次太冒险了", created_at=f"2026-01-0{i}T00:00:00",
        )
        for i in range(1, 4)
    ]
    summary = pf.summarize_feedback(items, branch="main")
    assert summary["suggestion"] is not None
    assert "太冒险" in summary["suggestion"]


def test_summarize_feedback_ignores_acknowledged_notes_for_threshold(tmp_path):
    for i in range(1, 4):
        pf.record_feedback(tmp_path, "sim_a", branch="main", step=i, user_reason="太保守了")
    items = pf.load_all(tmp_path, "sim_a")
    # 确认其中一条，未确认的只剩 2 条，不应该再触发阈值为 3 的提示
    pf.acknowledge(tmp_path, "sim_a", items[0].id)
    reloaded = pf.load_all(tmp_path, "sim_a")
    summary = pf.summarize_feedback(reloaded, branch="main")
    assert len(summary["unacknowledged"]) == 2
    assert summary["suggestion"] is None


def test_summarize_feedback_reason_with_no_keyword_match_has_no_suggestion():
    items = [
        pf.PolicyFeedbackNote(
            id=f"id{i}", sim_id="sim_a", branch="main", step=i,
            user_reason="这一步的理由写得不够清楚", created_at=f"2026-01-0{i}T00:00:00",
        )
        for i in range(1, 4)
    ]
    summary = pf.summarize_feedback(items, branch="main")
    assert len(summary["unacknowledged"]) == 3
    assert summary["suggestion"] is None
