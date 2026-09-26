"""core/events.py 的单元测试。

对应 `next_doc/refactor_plan/03-phase2-event-model-sprint-plan.md`
Sprint 2-1："`core/events.py` 定义 `Event` dataclass"任务的验证。

覆盖两点：
1. 新增字段（`id`/`actor`/`context`/`causation_id`/`correlation_id`）有
   合理默认值，且 `to_dict()` 包含全部字段。
2. Sprint 1 已有的旧调用方式 `Event(kind=..., payload=...)` 不改一行
   仍然可以工作（对应止损条件"不应改变原有业务逻辑"）。
"""

from __future__ import annotations

from mini_agent.core import EVENT_KINDS, Event


def test_event_legacy_construction_still_works():
    """Sprint 1 遗留调用方式：只传 kind/payload，其余字段用默认值。"""
    ev = Event(kind="goal_mode.adapter.to_new", payload={"a": 1})

    assert ev.kind == "goal_mode.adapter.to_new"
    assert ev.payload == {"a": 1}
    assert ev.actor is None
    assert ev.context == {}
    assert ev.causation_id is None
    assert ev.correlation_id is None
    assert isinstance(ev.id, str) and ev.id


def test_event_to_dict_contains_all_fields():
    ev = Event(
        kind="GoalCreated",
        payload={"goal_text": "写周报"},
        actor="goal_mode.runner",
        context={"round": 0},
        causation_id="parent-id",
        correlation_id="corr-id",
    )

    d = ev.to_dict()

    assert d["kind"] == "GoalCreated"
    assert d["payload"] == {"goal_text": "写周报"}
    assert d["actor"] == "goal_mode.runner"
    assert d["context"] == {"round": 0}
    assert d["causation_id"] == "parent-id"
    assert d["correlation_id"] == "corr-id"
    assert d["id"] == ev.id
    assert "at" in d


def test_each_event_gets_a_distinct_id():
    a = Event(kind="ActionStarted")
    b = Event(kind="ActionStarted")
    assert a.id != b.id


def test_event_kinds_covers_sprint_2_1_minimal_set():
    """Sprint 2-1 范围内先落地的最小事件类型集合。"""
    assert set(EVENT_KINDS) == {
        "GoalCreated",
        "GoalUpdated",
        "ActionStarted",
        "ActionCompleted",
        "ActionFailed",
        "ExperienceCreated",
    }
