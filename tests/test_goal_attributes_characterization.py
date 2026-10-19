"""P1 特征测试：锁定目标层级与执行语义的现有行为。

对应 next_doc/management_output_project_model_plan.md 第 9 章 P1：
"现有目标的执行行为与改造前一致（特征测试通过）"。

这些测试在改造前后都必须通过。它们只断言现有可观察行为，不断言新字段。
改造后新增的属性（verifiable / executable / deliverable）由
tests/test_goal_attributes.py 单独覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mini_agent.evolution.focus_research_trigger import (
    MIN_INTERVAL_SECONDS_GOAL,
    MIN_INTERVAL_SECONDS_STRUCTURAL,
    _min_interval_for,
)
from mini_agent.evolution.next_action_advisor import _collect_current_focus_node_ids
from mini_agent.perception.goal_backlog import (
    GoalBacklog,
    GoalNode,
    validate_node_hierarchy,
)
from mini_agent.storage.paths import AgentPaths


@pytest.fixture
def backlog(tmp_path: Path) -> GoalBacklog:
    return GoalBacklog(AgentPaths(project_root=tmp_path))


@pytest.fixture
def tree(backlog: GoalBacklog):
    """构造完整五层树：ultimate → domain → stage → goal → objective。"""
    ultimate = backlog.add_node("ultimate", "人生")
    domain = backlog.add_node("domain", "工作", parent_id=ultimate.id)
    stage = backlog.add_node("stage", "现阶段", parent_id=domain.id)
    goal = backlog.add_node("goal", "做出产品", parent_id=stage.id)
    objective = backlog.add_node("objective", "写原型", parent_id=goal.id)
    return {
        "ultimate": ultimate,
        "domain": domain,
        "stage": stage,
        "goal": goal,
        "objective": objective,
    }


# ── 1. 层级谓词：决定"是否参与执行" ───────────────────────────────────────────

@pytest.mark.parametrize(
    "level,expect_structural,expect_goal,expect_objective",
    [
        ("ultimate", True, False, False),
        ("domain", True, False, False),
        ("stage", True, False, False),
        ("goal", False, True, False),
        ("objective", False, False, True),
    ],
)
def test_level_predicates_table(tree, level, expect_structural, expect_goal, expect_objective):
    node = tree[level]
    assert node.is_structural is expect_structural
    assert node.is_goal is expect_goal
    assert node.is_objective is expect_objective


# ── 2. 焦点收集：结构节点的焦点子节点会被纳入 ────────────────────────────────

def test_focus_research_nodes_includes_pinned_structural_child(backlog, tree):
    """返回值 = 活跃叶子 Goal（照常包含）+ 结构焦点；结构节点排在叶子之后。"""
    backlog.set_focus_pin(tree["domain"].id, tree["stage"].id, True)
    ids = [n.id for n in backlog.focus_research_nodes()]
    assert tree["stage"].id in ids
    assert tree["goal"].id in ids, "活跃叶子 Goal 仍按原逻辑包含在结果中"
    assert ids.index(tree["goal"].id) < ids.index(tree["stage"].id), "结构节点排在叶子之后"


def test_collect_current_focus_node_ids_matches_structural_focus(backlog, tree):
    backlog.set_focus_pin(tree["domain"].id, tree["stage"].id, True)
    ids = _collect_current_focus_node_ids(backlog)
    assert tree["stage"].id in ids
    assert tree["goal"].id not in ids


# ── 3. 调研触发间隔：结构节点与叶子节点不同 ────────────────────────────────────

def test_min_interval_structural_vs_goal(tree):
    assert _min_interval_for(tree["stage"]) == MIN_INTERVAL_SECONDS_STRUCTURAL
    assert _min_interval_for(tree["domain"]) == MIN_INTERVAL_SECONDS_STRUCTURAL
    assert _min_interval_for(tree["goal"]) == MIN_INTERVAL_SECONDS_GOAL
    assert _min_interval_for(tree["objective"]) == MIN_INTERVAL_SECONDS_GOAL


# ── 4. 层级约束：父子 level 顺序 ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "child,parent,legal",
    [
        ("domain", "ultimate", True),
        ("stage", "domain", True),
        ("stage", "ultimate", True),
        ("goal", "stage", True),
        ("goal", "domain", True),
        ("goal", "goal", True),
        ("objective", "goal", True),
        ("objective", "stage", False),
        ("goal", "objective", False),
        ("domain", "goal", False),
        ("ultimate", "domain", False),
    ],
)
def test_validate_node_hierarchy_table(child, parent, legal):
    err = validate_node_hierarchy(child, parent)
    assert (err is None) is legal, f"{child} under {parent}: {err}"


# ── 5. 序列化：旧字段往返一致 ─────────────────────────────────────────────────

def test_goal_node_round_trip_is_stable(backlog):
    root = backlog.get_root_node()
    domain = backlog.add_node("domain", "领域", parent_id=root.id)
    node = backlog.add_node("goal", "往返测试", parent_id=domain.id,
                            description="d", priority=3, tags=["x"])
    d = node.to_dict()
    again = GoalNode.from_dict(d).to_dict()
    assert again == d


def test_legacy_goals_json_without_new_fields_loads(tmp_path: Path):
    """旧版 goals.json 没有任何 P1 新字段，必须能原样加载。"""
    legacy = {
        "version": 1,
        "goals": [
            {"id": "g1", "level": "goal", "title": "旧目标", "source": "user",
             "status": "active", "parent_id": None, "children_ids": [], "priority": 0},
            {"id": "o1", "level": "objective", "title": "旧子目标", "source": "user",
             "status": "active", "parent_id": "g1", "children_ids": [], "priority": 0},
        ],
        "directions": [],
    }
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "goals.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    b = GoalBacklog(AgentPaths(project_root=tmp_path))
    b.load()
    g1 = b.get("g1")
    o1 = b.get("o1")
    assert g1 is not None and g1.is_goal and not g1.is_structural
    assert o1 is not None and o1.is_objective and not o1.is_structural
    assert g1.title == "旧目标"
