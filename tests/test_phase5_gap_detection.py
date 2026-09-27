"""tests/test_phase5_gap_detection.py

对应 `next_doc/refactor_plan/06-phase5-goal-convergence-sprint-plan.md`
Sprint 5-2 验收标准："给定一个新 Goal 输入，系统能自动生成 `problems`
和 `gap` 字段，而不需要用户手动填写"。
"""

from __future__ import annotations

import pytest

from mini_agent.core.goal import GoalState
from mini_agent.core.self import SelfState
from mini_agent.core.state_manager import StateManager, reset_state_manager
from mini_agent.core.world import WorldState
from mini_agent.goals.gap import detect_gap


@pytest.fixture(autouse=True)
def _reset_manager():
    reset_state_manager()
    yield
    reset_state_manager()


def test_missing_current_and_ideal_state_reports_two_problems_and_uses_acceptance_criteria_as_gap():
    goal = GoalState(goal_text="写周报", acceptance_criteria=["写完周报"])
    manager = StateManager()

    result = detect_gap(goal, state_manager=manager)

    assert result is goal  # 原地写回，返回同一个对象
    assert len(goal.problems) == 2
    assert any("current_state" in p for p in goal.problems)
    assert any("ideal_state" in p for p in goal.problems)
    assert goal.gap == ["未验证达成：写完周报"]


def test_current_equals_ideal_means_no_gap_no_problems():
    goal = GoalState(goal_text="写周报", current_state="已发送", ideal_state="已发送")
    manager = StateManager()

    detect_gap(goal, state_manager=manager)

    assert goal.problems == []
    assert goal.gap == []


def test_different_states_without_acceptance_criteria_falls_back_to_generic_gap_note():
    goal = GoalState(
        goal_text="写周报", current_state="草稿完成 50%", ideal_state="周报已发送"
    )
    manager = StateManager()

    detect_gap(goal, state_manager=manager)

    assert goal.problems == []
    assert len(goal.gap) == 1
    assert "草稿完成 50%" in goal.gap[0]
    assert "周报已发送" in goal.gap[0]


def test_different_states_with_acceptance_criteria_uses_criteria_as_gap():
    goal = GoalState(
        goal_text="写周报",
        current_state="草稿完成 50%",
        ideal_state="周报已发送",
        acceptance_criteria=["周报包含本周三个要点", "已发送给主管"],
    )
    manager = StateManager()

    detect_gap(goal, state_manager=manager)

    assert goal.gap == [
        "未验证达成：周报包含本周三个要点",
        "未验证达成：已发送给主管",
    ]


def test_llm_judge_overrides_problems_but_not_gap():
    goal = GoalState(goal_text="写周报", ideal_state="周报已发送")
    manager = StateManager()

    def fake_llm_judge(g, self_state, world_state):
        assert g is goal
        return ["LLM 判断出的问题 A", "LLM 判断出的问题 B"]

    detect_gap(goal, state_manager=manager, llm_judge=fake_llm_judge)

    assert goal.problems == ["LLM 判断出的问题 A", "LLM 判断出的问题 B"]
    # gap 仍然走规则（没有 acceptance_criteria，退化为通用提示）
    assert len(goal.gap) == 1


def test_reads_self_and_world_state_from_state_manager_without_error_when_placeholder():
    """接入 StateManager：即便 world 目前还是占位（空 dataclass），
    也不应该报错，且确实调用了 get_state("world"/"self")。
    """
    goal = GoalState(goal_text="写周报")
    manager = StateManager()
    manager.update_state("world", WorldState())
    manager.update_state("self", SelfState())

    detect_gap(goal, state_manager=manager)

    assert goal.evidence["gap_detection"] == {
        "self_state_available": True,
        "world_state_available": True,
    }


def test_missing_self_and_world_state_recorded_as_unavailable_not_error():
    goal = GoalState(goal_text="写周报")
    manager = StateManager()  # 什么都没托管

    detect_gap(goal, state_manager=manager)

    assert goal.evidence["gap_detection"] == {
        "self_state_available": False,
        "world_state_available": False,
    }


def test_uses_global_state_manager_when_not_explicitly_passed():
    from mini_agent.core.state_manager import get_state_manager

    goal = GoalState(goal_text="写周报")
    detect_gap(goal)  # 不传 state_manager，走全局单例

    assert "gap_detection" in goal.evidence
    assert get_state_manager() is not None
