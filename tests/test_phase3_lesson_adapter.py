"""Phase 3 遗留项：lesson 型 MemoryEntry -> Experience Adapter 的测试。"""

from __future__ import annotations

import pytest

from mini_agent.core import LessonAdapter, import_lessons
from mini_agent.core.experience_patterns import summarize_failures
from mini_agent.core.experience_retrieval import retrieve_similar_experiences
from mini_agent.core.experience_store import ExperienceStore
from mini_agent.perception.memory_store import MemoryEntry


def _lesson(**kw) -> MemoryEntry:
    base = dict(
        session_id="s1", summary="总结文本", key_outcomes=[], tags=["lesson", "x"],
        model="m", entry_type="lesson", trigger="部署脚本时权限被拒绝",
        outcome="脚本以 exit 1 结束", root_cause="缺少写权限",
        suggested_action="先检查目录权限再执行", confidence=0.8,
        occurrence_count=3, source="human_feedback",
    )
    base.update(kw)
    return MemoryEntry(**base)


def test_to_new_maps_fields():
    e = _lesson()
    exp = LessonAdapter.to_new(e)
    assert exp.id == f"lesson:{e.entry_id}"
    assert exp.source == "memory_lesson"
    assert exp.goal_text == "部署脚本时权限被拒绝"
    assert exp.final_report == "脚本以 exit 1 结束"
    assert exp.lesson == "先检查目录权限再执行"
    assert exp.causal_hypothesis == "缺少写权限"
    assert exp.confidence == pytest.approx(0.8)
    assert exp.evidence == {"memory_source": "human_feedback", "occurrence_count": 3}
    assert exp.context["session_id"] == "s1"
    assert exp.created_at == e.created_at


def test_empty_fields_fall_back_to_summary():
    exp = LessonAdapter.to_new(_lesson(trigger="", outcome="", suggested_action=""))
    assert exp.goal_text == exp.final_report == exp.lesson == "总结文本"


def test_non_lesson_rejected():
    with pytest.raises(ValueError):
        LessonAdapter.to_new(_lesson(entry_type="summary"))


def test_to_old_explicitly_unimplemented():
    with pytest.raises(NotImplementedError):
        LessonAdapter.to_old(LessonAdapter.to_new(_lesson()))


def test_import_is_idempotent_and_filters(tmp_path):
    store = ExperienceStore(path=tmp_path / "e.db")
    entries = [_lesson(), _lesson(trigger="另一个场景"), _lesson(entry_type="summary")]
    r1 = import_lessons(entries, store)
    assert (r1.scanned, r1.imported, r1.skipped, r1.failed) == (3, 2, 1, 0)
    r2 = import_lessons(entries, store)
    assert r2.imported == 2
    assert len(store.all()) == 2  # 重复导入不产生重复行


def test_import_single_failure_does_not_abort(tmp_path):
    class Boom:
        entry_type = "lesson"

    store = ExperienceStore(path=tmp_path / "e.db")
    r = import_lessons([Boom(), _lesson()], store)
    assert (r.imported, r.failed) == (1, 1)


def test_imported_lessons_do_not_dilute_goal_failure_rate(tmp_path):
    from mini_agent.core.experience import Experience

    store = ExperienceStore(path=tmp_path / "e.db")
    store.append(Experience(source="goal_mode", goal_text="部署脚本权限", status="failed",
                            rounds_used=2, final_report="x", lesson="L"))
    import_lessons([_lesson(), _lesson(trigger="部署脚本权限问题")], store)
    s = summarize_failures("部署脚本权限", store=store)
    assert (s.total_matched, s.failure_count, s.failure_rate) == (1, 1, 1.0)
    assert s.status_distribution == {"failed": 1}


def test_imported_lesson_retrievable(tmp_path):
    store = ExperienceStore(path=tmp_path / "e.db")
    import_lessons([_lesson()], store)
    hits = retrieve_similar_experiences("部署脚本时权限被拒绝", store=store)
    assert hits and hits[0].lesson == "先检查目录权限再执行"
