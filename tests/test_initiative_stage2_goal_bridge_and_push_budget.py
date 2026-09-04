"""[next_doc/initiative_systems_unification_plan.md 阶段二] 单元测试：

1. capability_learning.adopt_topic_as_goal() —— 子主题落地成 GoalNode，
   幂等复用、反向指针失效时能重建。
2. perception/initiative_push_budget —— 跨系统推送预算总闸：默认关闭
   时 no-op；开启后按共享总额节流、按 source 记账、跨天重置。
"""
from __future__ import annotations

import json

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.capability_learning import (
    CapabilityTrackStore,
    adopt_topic_as_goal,
)
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.perception import initiative_push_budget as ipb


@pytest.fixture
def paths(tmp_path):
    return AgentPaths(project_root=tmp_path)


# ── adopt_topic_as_goal ─────────────────────────────────────────────────


def test_adopt_topic_as_goal_creates_goal_and_links_back(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(
        title="股票分析能力",
        persona_desc="希望你具备强大的股票分析能力",
        outline_names=["技术分析基础", "基本面分析"],
    )
    topic = track.outline[0]
    assert topic.linked_goal_id is None

    goal_backlog = GoalBacklog(paths)
    goal = adopt_topic_as_goal(paths, track, topic, goal_backlog=goal_backlog, track_store=store)

    assert goal.id
    assert goal.source == "agent_derived"
    assert "capability_learning" in goal.tags
    assert track.title in goal.title and topic.name in goal.title
    assert topic.linked_goal_id == goal.id

    # 落盘确实写回了 linked_goal_id（不是只改了内存里的对象）。
    reloaded = store.get(track.track_id)
    reloaded_topic = next(t for t in reloaded.outline if t.topic_id == topic.topic_id)
    assert reloaded_topic.linked_goal_id == goal.id

    # 目标树里真的能查到这个 Goal。
    assert goal_backlog.get(goal.id) is not None


def test_adopt_topic_as_goal_is_idempotent(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="", outline_names=["子主题A"])
    topic = track.outline[0]
    goal_backlog = GoalBacklog(paths)

    goal1 = adopt_topic_as_goal(paths, track, topic, goal_backlog=goal_backlog, track_store=store)
    goal2 = adopt_topic_as_goal(paths, track, topic, goal_backlog=goal_backlog, track_store=store)

    assert goal1.id == goal2.id
    # 没有因为第二次调用多建一个 Goal。
    all_goals = goal_backlog.all_nodes()
    assert sum(1 for g in all_goals if g.id == goal1.id) == 1


def test_adopt_topic_as_goal_rebuilds_when_linked_goal_deleted(paths):
    store = CapabilityTrackStore(paths)
    track = store.create(title="T", persona_desc="", outline_names=["子主题A"])
    topic = track.outline[0]
    goal_backlog = GoalBacklog(paths)

    goal1 = adopt_topic_as_goal(paths, track, topic, goal_backlog=goal_backlog, track_store=store)

    # 模拟用户手动删除了这个 Goal（反向指针失效）。
    goal_backlog.delete_goal(goal1.id)

    goal2 = adopt_topic_as_goal(paths, track, topic, goal_backlog=goal_backlog, track_store=store)
    assert goal2.id != goal1.id
    assert goal_backlog.get(goal2.id) is not None


# ── initiative_push_budget ──────────────────────────────────────────────


def test_check_and_consume_for_project_default_disabled_is_noop(paths):
    # 没有 agent_config.json（或没有该字段）时默认关闭，永远放行，且不
    # 写任何状态文件。
    for _ in range(10):
        assert ipb.check_and_consume_for_project(paths, "growth_advisor") is True
    assert not paths.initiative_push_budget_path.exists()


def _write_agent_config(paths, enabled: bool, max_per_day: int) -> None:
    cfg_path = paths.project_root / "agent_config.json"
    cfg_path.write_text(
        json.dumps({
            "initiative_push_budget_enabled": enabled,
            "initiative_push_budget_max_per_day": max_per_day,
        }),
        encoding="utf-8",
    )


def test_check_and_consume_for_project_enforces_shared_budget(paths):
    _write_agent_config(paths, enabled=True, max_per_day=2)

    assert ipb.check_and_consume_for_project(paths, "growth_advisor") is True
    assert ipb.check_and_consume_for_project(paths, "capability_learning") is True
    # 第三条来自任意来源都应该被跨系统总闸拦下——预算是共享的，不是
    # 按来源各自独立的配额。
    assert ipb.check_and_consume_for_project(paths, "growth_advisor") is False
    assert ipb.check_and_consume_for_project(paths, "watchlist") is False


def test_try_consume_records_spend_by_source(paths):
    assert ipb.try_consume(paths, "growth_advisor", max_per_day=5) is True
    assert ipb.try_consume(paths, "capability_learning", max_per_day=5) is True

    state = json.loads(paths.initiative_push_budget_path.read_text(encoding="utf-8"))
    assert state["spent_today"] == 2
    assert state["spent_by_source"]["growth_advisor"] == 1
    assert state["spent_by_source"]["capability_learning"] == 1


def test_remaining_budget_is_read_only(paths):
    ipb.try_consume(paths, "growth_advisor", max_per_day=3)
    assert ipb.remaining_budget(paths, max_per_day=3) == 2
    # 多次只读查询不应该继续消耗预算。
    assert ipb.remaining_budget(paths, max_per_day=3) == 2
    assert ipb.remaining_budget(paths, max_per_day=3) == 2


def test_budget_resets_on_new_day(paths):
    ipb.try_consume(paths, "growth_advisor", max_per_day=1)
    assert ipb.try_consume(paths, "growth_advisor", max_per_day=1) is False

    # 手动把状态文件里的日期改成"昨天"，模拟跨天。
    state = json.loads(paths.initiative_push_budget_path.read_text(encoding="utf-8"))
    state["date"] = "2000-01-01"
    paths.initiative_push_budget_path.write_text(json.dumps(state), encoding="utf-8")

    assert ipb.try_consume(paths, "growth_advisor", max_per_day=1) is True
