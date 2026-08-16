"""v0.21 §13.2-f 大纲动态生长建议单元测试。

覆盖 next_doc/persona_capability_learning_design.md「后续计划（v0.21）」
第 2 项：`generate_outline_suggestion_from_answer()` / `accept_outline_
suggestion()` / `CapabilityOutlineSuggestionStore` 以及
`run_capability_learning_cycle(llm_helper=...)` 的接线。
"""
from __future__ import annotations

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.capability_learning import (
    CapabilityOutlineSuggestionStore,
    CapabilityQuestionStore,
    CapabilityTrackStore,
    OutlineTopic,
    accept_outline_suggestion,
    generate_outline_suggestion_from_answer,
    run_capability_learning_cycle,
)


@pytest.fixture
def paths(tmp_path):
    return AgentPaths(project_root=tmp_path)


def _track_with_topic(paths, title="股票分析"):
    store = CapabilityTrackStore(paths)
    track = store.create(title=title, persona_desc=title)
    store.update(track.track_id, outline=[OutlineTopic(topic_id="t1", name="技术分析基础")])
    return store.get(track.track_id)


def test_no_llm_helper_skips(paths):
    track = _track_with_topic(paths)
    q_store = CapabilityQuestionStore(paths)
    q = q_store.raise_question(track.track_id, "t1", "你更关心哪个市场？")
    q = q_store.answer(q.question_id, "我其实更关心港股")
    result = generate_outline_suggestion_from_answer(track, q, llm_helper=None)
    assert result is None


def test_llm_none_response_skips(paths):
    track = _track_with_topic(paths)
    q_store = CapabilityQuestionStore(paths)
    q = q_store.raise_question(track.track_id, "t1", "你更关心哪个市场？")
    q = q_store.answer(q.question_id, "美股就挺好")
    result = generate_outline_suggestion_from_answer(track, q, llm_helper=lambda p: "NONE")
    assert result is None


def test_llm_new_topic_generates_suggestion(paths):
    track = _track_with_topic(paths)
    q_store = CapabilityQuestionStore(paths)
    q = q_store.raise_question(track.track_id, "t1", "你更关心哪个市场？")
    q = q_store.answer(q.question_id, "我其实更关心港股")
    result = generate_outline_suggestion_from_answer(track, q, llm_helper=lambda p: "港股市场特点")
    assert result is not None
    assert result.suggested_name == "港股市场特点"
    assert result.track_id == track.track_id


def test_duplicate_of_existing_topic_skips(paths):
    track = _track_with_topic(paths)
    q_store = CapabilityQuestionStore(paths)
    q = q_store.raise_question(track.track_id, "t1", "?")
    q = q_store.answer(q.question_id, "还是想学技术分析基础")
    # LLM 建议的名字和已有大纲子主题高度相似 -> 应该被去重掉
    result = generate_outline_suggestion_from_answer(track, q, llm_helper=lambda p: "技术分析基础")
    assert result is None


def test_accept_outline_suggestion_adds_topic(paths):
    track = _track_with_topic(paths)
    sug_store = CapabilityOutlineSuggestionStore(paths)
    from mini_agent.evolution.capability_learning import OutlineSuggestion
    sug = OutlineSuggestion(
        suggestion_id="capsug_test1", track_id=track.track_id,
        source_question_id="q1", suggested_name="港股市场特点",
    )
    sug_store.add(sug)

    topic = accept_outline_suggestion(paths, "capsug_test1")
    assert topic is not None
    assert topic.name == "港股市场特点"

    updated_track = CapabilityTrackStore(paths).get(track.track_id)
    names = [t.name for t in updated_track.outline]
    assert "港股市场特点" in names

    # 建议本身应该被标记为 accepted，不再出现在 pending 列表
    pending = sug_store.list_suggestions(status="pending", track_id=track.track_id)
    assert all(s.suggestion_id != "capsug_test1" for s in pending)


def test_accept_unknown_suggestion_returns_none(paths):
    assert accept_outline_suggestion(paths, "does_not_exist") is None


def test_cycle_generates_suggestion_when_llm_helper_passed(paths):
    track = _track_with_topic(paths)
    q_store = CapabilityQuestionStore(paths)
    q = q_store.raise_question(track.track_id, "t1", "你更关心哪个市场？")
    q_store.answer(q.question_id, "我其实更关心港股")

    result = run_capability_learning_cycle(
        paths, llm_helper=lambda p: "港股市场特点",
    )
    assert result["outline_suggestions_generated"] == 1
    assert result["questions_consumed"] == 1

    pending = CapabilityOutlineSuggestionStore(paths).list_suggestions(status="pending")
    assert len(pending) == 1
    assert pending[0].suggested_name == "港股市场特点"


def test_cycle_without_llm_helper_generates_nothing(paths):
    track = _track_with_topic(paths)
    q_store = CapabilityQuestionStore(paths)
    q = q_store.raise_question(track.track_id, "t1", "你更关心哪个市场？")
    q_store.answer(q.question_id, "我其实更关心港股")

    result = run_capability_learning_cycle(paths)
    assert result["outline_suggestions_generated"] == 0
    assert result["questions_consumed"] == 1
