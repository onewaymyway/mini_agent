"""
tests/test_lesson_review.py — Stage 3.1 验证（Phase C 之一）

对应 self_evolution_implementation_plan.md Stage 3.1：
  lesson 阈值扫描（perception/lesson_review.py）—— 按 trigger 关键词相似度
  分组、occurrence_count 聚合、T1/T2/T3 证据门槛判定（设计文档 6.7 节）。
"""

from __future__ import annotations

import pytest

from mini_agent.perception.memory_store import MemoryEntry
from mini_agent.perception.lesson_review import (
    LessonGroup,
    group_lessons,
    scan_for_proposals,
    T1_MIN_OCCURRENCE,
    T1_MIN_SESSIONS,
    T2_T3_MIN_OCCURRENCE,
)


def make_lesson(
    session_id="s1",
    trigger="example trigger",
    occurrence_count=1,
    source="self_reflection",
    entry_type="lesson",
    **overrides,
) -> MemoryEntry:
    kwargs = dict(
        session_id=session_id, summary="", key_outcomes=[], tags=[], model="test-model",
        entry_type=entry_type, trigger=trigger, outcome="some outcome",
        suggested_action="some action", occurrence_count=occurrence_count, source=source,
    )
    kwargs.update(overrides)
    return MemoryEntry(**kwargs)


# ── group_lessons() ───────────────────────────────────────────────────────────

def test_group_lessons_empty_input():
    assert group_lessons([]) == []


def test_group_lessons_ignores_non_lesson_entries():
    entries = [
        MemoryEntry(session_id="s1", summary="a summary", key_outcomes=[], tags=[],
                    model="m", entry_type="summary"),
        MemoryEntry(session_id="s1", summary="", key_outcomes=[], tags=[], model="m",
                    entry_type="capability_map"),
    ]
    assert group_lessons(entries) == []


def test_group_lessons_ignores_empty_trigger():
    entries = [make_lesson(trigger="")]
    assert group_lessons(entries) == []


def test_group_lessons_similar_triggers_merge():
    entries = [
        make_lesson(session_id="s1", trigger="forgot to run tests before commit"),
        make_lesson(session_id="s2", trigger="did not run tests before commit"),
    ]
    groups = group_lessons(entries)
    assert len(groups) == 1
    assert len(groups[0].entries) == 2


def test_group_lessons_unrelated_triggers_stay_separate():
    entries = [
        make_lesson(session_id="s1", trigger="forgot to run tests before commit"),
        make_lesson(session_id="s1", trigger="used wrong python version in virtualenv"),
    ]
    groups = group_lessons(entries)
    assert len(groups) == 2


def test_group_lessons_min_group_size_filters_singletons():
    entries = [
        make_lesson(session_id="s1", trigger="forgot to run tests before commit"),
        make_lesson(session_id="s1", trigger="completely unrelated topic about docker networking"),
    ]
    groups = group_lessons(entries, min_group_size=2)
    assert groups == []


# ── LessonGroup properties ────────────────────────────────────────────────────

def test_lesson_group_total_occurrence_sums_entries():
    g = LessonGroup(key="x")
    g.entries = [make_lesson(occurrence_count=2), make_lesson(occurrence_count=3)]
    assert g.total_occurrence == 5


def test_lesson_group_session_ids_deduplicates():
    g = LessonGroup(key="x")
    g.entries = [
        make_lesson(session_id="s1"),
        make_lesson(session_id="s1"),
        make_lesson(session_id="s2"),
    ]
    assert g.session_ids == {"s1", "s2"}


def test_lesson_group_session_ids_ignores_empty():
    g = LessonGroup(key="x")
    g.entries = [make_lesson(session_id=""), make_lesson(session_id="s1")]
    assert g.session_ids == {"s1"}


def test_lesson_group_has_human_feedback_true():
    g = LessonGroup(key="x")
    g.entries = [make_lesson(source="self_reflection"), make_lesson(source="human_feedback")]
    assert g.has_human_feedback is True


def test_lesson_group_has_human_feedback_false():
    g = LessonGroup(key="x")
    g.entries = [make_lesson(source="self_reflection")]
    assert g.has_human_feedback is False


def test_lesson_group_meets_t1_threshold_requires_both_conditions():
    # 满足 occurrence 但只有一个 session -> 不达标
    g = LessonGroup(key="x")
    g.entries = [make_lesson(session_id="s1", occurrence_count=T1_MIN_OCCURRENCE)]
    assert g.meets_t1_threshold is False

    # 满足 session 数但 occurrence 不够 -> 不达标
    g2 = LessonGroup(key="x")
    g2.entries = [make_lesson(session_id="s1", occurrence_count=1),
                  make_lesson(session_id="s2", occurrence_count=1)]
    assert g2.total_occurrence < T1_MIN_OCCURRENCE
    assert g2.meets_t1_threshold is False

    # 两者都满足 -> 达标
    g3 = LessonGroup(key="x")
    g3.entries = [make_lesson(session_id="s1", occurrence_count=2),
                  make_lesson(session_id="s2", occurrence_count=1)]
    assert g3.total_occurrence >= T1_MIN_OCCURRENCE
    assert len(g3.session_ids) >= T1_MIN_SESSIONS
    assert g3.meets_t1_threshold is True


def test_lesson_group_meets_t2_t3_threshold_requires_human_feedback():
    # occurrence 够但没有 human_feedback -> 不达标
    g = LessonGroup(key="x")
    g.entries = [make_lesson(occurrence_count=T2_T3_MIN_OCCURRENCE, source="self_reflection")]
    assert g.meets_t2_t3_threshold is False

    # 有 human_feedback 但 occurrence 不够 -> 不达标
    g2 = LessonGroup(key="x")
    g2.entries = [make_lesson(occurrence_count=1, source="human_feedback")]
    assert g2.meets_t2_t3_threshold is False

    # 两者都满足 -> 达标
    g3 = LessonGroup(key="x")
    g3.entries = [
        make_lesson(occurrence_count=T2_T3_MIN_OCCURRENCE - 1, source="self_reflection"),
        make_lesson(occurrence_count=1, source="human_feedback"),
    ]
    assert g3.total_occurrence >= T2_T3_MIN_OCCURRENCE
    assert g3.meets_t2_t3_threshold is True


def test_lesson_group_to_dict_structure():
    g = LessonGroup(key="some key")
    g.entries = [make_lesson(session_id="s1", trigger="x", occurrence_count=3)]
    d = g.to_dict()
    assert d["group_key"] == "some key"
    assert d["total_occurrence"] == 3
    assert d["session_count"] == 1
    assert d["has_human_feedback"] is False
    assert len(d["entries"]) == 1
    entry = d["entries"][0]
    assert entry["session_id"] == "s1"
    assert entry["trigger"] == "x"
    assert "entry_id" in entry
    assert "suggested_action" in entry


# ── scan_for_proposals() ──────────────────────────────────────────────────────

def test_scan_for_proposals_t1_default():
    entries = [
        make_lesson(session_id="s1", trigger="forgot to run tests before commit", occurrence_count=2),
        make_lesson(session_id="s2", trigger="did not run tests before commit", occurrence_count=1),
    ]
    groups = scan_for_proposals(entries)  # default tier="T1"
    assert len(groups) == 1
    assert groups[0].total_occurrence == 3


def test_scan_for_proposals_filters_out_non_qualifying_groups():
    entries = [
        # qualifies
        make_lesson(session_id="s1", trigger="forgot to run tests before commit", occurrence_count=2),
        make_lesson(session_id="s2", trigger="did not run tests before commit", occurrence_count=1),
        # does not qualify (single session, single occurrence)
        make_lesson(session_id="s1", trigger="used wrong python version in virtualenv", occurrence_count=1),
    ]
    groups = scan_for_proposals(entries, tier="T1")
    assert len(groups) == 1
    assert "tests" in groups[0].key or "commit" in groups[0].key


def test_scan_for_proposals_sorted_by_occurrence_descending():
    entries = [
        make_lesson(session_id="s1", trigger="alpha topic one alpha", occurrence_count=3),
        make_lesson(session_id="s2", trigger="alpha topic one alpha repeated", occurrence_count=2),
        make_lesson(session_id="s1", trigger="beta topic two beta", occurrence_count=10),
        make_lesson(session_id="s2", trigger="beta topic two beta repeated", occurrence_count=10),
    ]
    groups = scan_for_proposals(entries, tier="T1")
    assert len(groups) == 2
    assert groups[0].total_occurrence >= groups[1].total_occurrence


def test_scan_for_proposals_t2_tier():
    entries = [
        make_lesson(session_id="s1", trigger="forgot to run tests before commit",
                    occurrence_count=2, source="self_reflection"),
        make_lesson(session_id="s2", trigger="did not run tests before commit",
                    occurrence_count=2, source="self_reflection"),
        make_lesson(session_id="s3", trigger="user said run tests before commit",
                    occurrence_count=1, source="human_feedback"),
    ]
    groups_t1 = scan_for_proposals(entries, tier="T1")
    groups_t2 = scan_for_proposals(entries, tier="T2")
    assert len(groups_t1) == 1
    assert len(groups_t2) == 1
    assert groups_t2[0].has_human_feedback


def test_scan_for_proposals_t2_without_human_feedback_fails():
    entries = [
        make_lesson(session_id="s1", trigger="forgot to run tests before commit",
                    occurrence_count=3, source="self_reflection"),
        make_lesson(session_id="s2", trigger="did not run tests before commit",
                    occurrence_count=3, source="self_reflection"),
    ]
    assert scan_for_proposals(entries, tier="T1")  # qualifies for T1
    assert scan_for_proposals(entries, tier="T2") == []  # but not T2 (no human_feedback)


def test_scan_for_proposals_unknown_tier_falls_back_to_t1():
    entries = [
        make_lesson(session_id="s1", trigger="forgot to run tests before commit", occurrence_count=2),
        make_lesson(session_id="s2", trigger="did not run tests before commit", occurrence_count=1),
    ]
    groups = scan_for_proposals(entries, tier="bogus")
    assert len(groups) == 1  # 视为 T1


def test_scan_for_proposals_empty_entries():
    assert scan_for_proposals([]) == []
