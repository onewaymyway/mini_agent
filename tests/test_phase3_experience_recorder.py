"""tests/test_phase3_experience_recorder.py

对应 `next_doc/refactor_plan/04-phase3-experience-layer-sprint-plan.md`
Sprint 3-1 验收标准：“一个 Goal 执行结束后，Experience 自动落库（不需要
手动调用 CLI），字段完整覆盖原文 §7 定义的 yaml 结构”。
"""

from __future__ import annotations

from pathlib import Path

from mini_agent.core.event_bus import Event, get_event_bus, reset_event_bus
from mini_agent.core.experience import Experience
from mini_agent.core.experience_recorder import (
    ExperienceRecorder,
    ensure_experience_recorder_subscribed,
    reset_experience_recorder_subscriptions,
)
from mini_agent.core.experience_store import ExperienceStore


def _reset():
    reset_event_bus()
    reset_experience_recorder_subscriptions()


def test_experience_dataclass_covers_all_proposal_fields():
    """原文 §7 字段：id/timestamp/context/state_before/goal/action/reason/
    prediction/outcome/state_after/evidence/lesson/causal_hypothesis/
    confidence（对应关系见 `core/experience.py` 顶部说明表）。"""
    exp = Experience(
        source="goal_mode",
        goal_text="写周报",
        status="done",
        rounds_used=1,
        final_report="完成",
    )
    d = exp.to_dict()
    for key in (
        "id",
        "created_at",  # timestamp
        "context",
        "state_before",
        "goal_text",  # goal
        "action",
        "reason",
        "prediction",
        "status",
        "final_report",  # outcome
        "state_after",
        "evidence",
        "lesson",
        "causal_hypothesis",
        "confidence",
    ):
        assert key in d


def test_experience_store_sqlite_round_trip_with_new_fields(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "experience_store.db")
    exp = Experience(
        source="goal_mode",
        goal_text="部署新版本",
        status="done",
        rounds_used=3,
        final_report="线上验证通过",
        action="goal_mode.run",
        reason="acceptance_criteria: ['上线成功']",
        evidence={"replan_proposal": {}},
        lesson="",
        context={"foo": "bar"},
        confidence=0.9,
    )
    store.append(exp)

    all_exp = store.all()
    assert len(all_exp) == 1
    got = all_exp[0]
    assert got.goal_text == "部署新版本"
    assert got.action == "goal_mode.run"
    assert got.context == {"foo": "bar"}
    assert got.confidence == 0.9
    assert got.id == exp.id


def test_experience_recorder_subscribes_and_persists_on_publish(tmp_path: Path):
    _reset()
    try:
        store = ExperienceStore(path=tmp_path / "experience_store.db")
        recorder = ExperienceRecorder(store=store)
        ensure_experience_recorder_subscribed(recorder)

        assert store.all() == []

        exp = Experience(
            source="goal_mode",
            goal_text="修复登录页 bug",
            status="done",
            rounds_used=2,
            final_report="已回归验证",
        )
        get_event_bus().publish(
            Event(kind="ExperienceCreated", payload=exp.to_dict())
        )

        persisted = store.all()
        assert len(persisted) == 1
        assert persisted[0].goal_text == "修复登录页 bug"
    finally:
        _reset()


def test_experience_recorder_subscription_is_idempotent(tmp_path: Path):
    _reset()
    try:
        store = ExperienceStore(path=tmp_path / "experience_store.db")
        recorder = ExperienceRecorder(store=store)
        ensure_experience_recorder_subscribed(recorder)
        ensure_experience_recorder_subscribed(recorder)  # 重复挂载

        exp = Experience(
            source="goal_mode",
            goal_text="目标 A",
            status="done",
            rounds_used=1,
            final_report="ok",
        )
        get_event_bus().publish(
            Event(kind="ExperienceCreated", payload=exp.to_dict())
        )

        # 重复挂载不应该导致同一事件被写两遍（只应有一条记录）。
        assert len(store.all()) == 1
    finally:
        _reset()


def test_experience_from_dict_tolerates_legacy_six_field_records():
    """Sprint 2 阶段写入的旧记录只有六个字段，`from_dict()` 必须能宽容
    解析（新增字段回退到默认值），这是迁移脚本能正常工作的前提。"""
    legacy = {
        "source": "goal_mode",
        "goal_text": "旧记录",
        "status": "done",
        "rounds_used": 1,
        "final_report": "完成",
        "created_at": 123.0,
    }
    exp = Experience.from_dict(legacy)
    assert exp.goal_text == "旧记录"
    assert exp.context == {}
    assert exp.lesson == ""
    assert exp.confidence is None
