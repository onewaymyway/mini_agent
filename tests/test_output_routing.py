"""P4 产出路由行为测试。

对应 next_doc/management_output_project_model_plan.md 第 8.3 节与第 9 章 P4。

保证：
  1. 绑定了产出项目（build / iterate）的目标，正式产出写入项目 output/；
  2. objective 沿目标树向上找到绑定的管理项目；
  3. 最近的祖先优先；同一管理项目 build 优先于 iterate；
  4. reference 关联不参与路由；
  5. 用户显式设置的 user_output_dir 优先于绑定；
  6. notes / spec / scratch 等内部目录不随绑定改变；
  7. 路由只读：不改 goals.json，不改注册表；
  8. goals.json 损坏或注册表缺失时退回默认路径，不抛异常。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from mini_agent.evolution import output_workspace as ow
from mini_agent.output_registry import (
    link_management_output,
    mark_management_root,
    register_project,
)
from mini_agent.output_registry.routing import resolve_bound_output_dir
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _hash_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for dirpath, _dn, fns in os.walk(root):
        for fn in fns:
            full = os.path.join(dirpath, fn)
            out[os.path.relpath(full, root)] = hashlib.sha256(Path(full).read_bytes()).hexdigest()
    return out


@pytest.fixture
def env(tmp_path: Path):
    proj = tmp_path / "proj"
    (proj / "output_projects" / "alpha" / "output").mkdir(parents=True)
    (proj / "output_projects" / "beta" / "output").mkdir(parents=True)
    paths = AgentPaths(project_root=proj)
    b = GoalBacklog(paths)
    top = b.get_root_node()
    domain = b.add_node("domain", "工作", parent_id=top.id)
    goal = b.add_node("goal", "做产品", parent_id=domain.id)
    obj = b.add_node("objective", "写原型", parent_id=goal.id)
    return {"proj": proj, "paths": paths, "backlog": b, "domain": domain, "goal": goal, "obj": obj}


def _default_dir(paths: AgentPaths, goal_id: str) -> Path:
    return ow.goal_output_base_dir(paths, goal_id) / "output"


# ── 1–2. 绑定与祖先查找 ───────────────────────────────────────────────────────

def test_build_bound_goal_routes_to_project_output(env):
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", "build")
    got = ow.goal_output_dir(env["paths"], env["goal"].id)
    assert got == env["proj"] / "output_projects" / "alpha" / "output"


def test_objective_routes_through_parent_goal(env):
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", "build")
    got = ow.goal_output_dir(env["paths"], env["obj"].id)
    assert got == env["proj"] / "output_projects" / "alpha" / "output"


def test_iterate_link_also_routes(env):
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", "iterate")
    assert resolve_bound_output_dir(env["proj"], env["goal"].id) == \
        env["proj"] / "output_projects" / "alpha" / "output"


# ── 3. 优先级 ─────────────────────────────────────────────────────────────────

def test_nearest_ancestor_wins(env):
    b = env["backlog"]
    inner = b.add_node("goal", "子产品", parent_id=env["goal"].id)
    mark_management_root(env["proj"], env["goal"].id)
    mark_management_root(env["proj"], inner.id)
    link_management_output(env["proj"], env["goal"].id, "alpha", "build")
    link_management_output(env["proj"], inner.id, "beta", "build")
    assert resolve_bound_output_dir(env["proj"], inner.id) == \
        env["proj"] / "output_projects" / "beta" / "output"
    # env["obj"] 挂在外层 goal 下：祖先链 obj → goal(alpha)，应解析到 alpha
    assert resolve_bound_output_dir(env["proj"], env["obj"].id) == \
        env["proj"] / "output_projects" / "alpha" / "output"
    # 挂在内层节点下的 objective：祖先链 obj2 → inner(beta)，应解析到 beta
    obj2 = b.add_node("objective", "子原型", parent_id=inner.id)
    assert resolve_bound_output_dir(env["proj"], obj2.id) == \
        env["proj"] / "output_projects" / "beta" / "output"


def test_build_preferred_over_iterate_on_same_management(env):
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", "iterate")
    link_management_output(env["proj"], env["goal"].id, "beta", "build")
    assert resolve_bound_output_dir(env["proj"], env["goal"].id) == \
        env["proj"] / "output_projects" / "beta" / "output"


# ── 4. reference 不参与路由 ───────────────────────────────────────────────────

def test_reference_only_does_not_route(env):
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", "reference")
    assert resolve_bound_output_dir(env["proj"], env["goal"].id) is None
    assert ow.goal_output_dir(env["paths"], env["goal"].id) == _default_dir(env["paths"], env["goal"].id)


# ── 5. 用户指定优先 ───────────────────────────────────────────────────────────

def test_explicit_user_output_dir_overrides_binding(env):
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", "build")
    got = ow.goal_output_dir(env["paths"], env["goal"].id, user_output_dir="research/manual")
    assert got == env["proj"] / "research/manual"


# ── 6. 内部目录不随绑定改变 ───────────────────────────────────────────────────

def test_internal_dirs_unchanged_by_binding(env):
    notes_before = ow.goal_notes_dir(env["paths"], env["goal"].id)
    spec_before = ow.goal_spec_dir(env["paths"], env["goal"].id)
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", "build")
    assert ow.goal_notes_dir(env["paths"], env["goal"].id) == notes_before
    assert ow.goal_spec_dir(env["paths"], env["goal"].id) == spec_before


# ── 7. 只读 ───────────────────────────────────────────────────────────────────

def test_routing_is_read_only(env):
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", "build")
    goals_path = env["proj"] / ".agent" / "goals.json"
    reg_dir = env["proj"] / "output_projects" / "_registry"
    goals_before = goals_path.read_bytes()
    reg_before = _hash_tree(reg_dir)

    for _ in range(3):
        ow.goal_output_dir(env["paths"], env["obj"].id)
        resolve_bound_output_dir(env["proj"], env["obj"].id)

    assert goals_path.read_bytes() == goals_before
    assert _hash_tree(reg_dir) == reg_before


# ── 8. 失败回退 ───────────────────────────────────────────────────────────────

def test_unbound_goal_uses_default(env):
    assert ow.goal_output_dir(env["paths"], env["goal"].id) == _default_dir(env["paths"], env["goal"].id)


def test_corrupt_goals_json_falls_back_to_default(env):
    (env["proj"] / ".agent" / "goals.json").write_text("{broken", encoding="utf-8")
    assert resolve_bound_output_dir(env["proj"], env["goal"].id) is None
    assert ow.goal_output_dir(env["paths"], env["goal"].id) == _default_dir(env["paths"], env["goal"].id)


def test_missing_registry_falls_back_to_default(env):
    assert resolve_bound_output_dir(env["proj"], env["goal"].id) is None
    assert ow.goal_output_dir(env["paths"], env["goal"].id) == _default_dir(env["paths"], env["goal"].id)


def test_unknown_goal_id_returns_none(env):
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", "build")
    assert resolve_bound_output_dir(env["proj"], "ghost") is None


def test_linked_project_dir_removed_still_returns_path_without_error(env):
    """注册表指向的项目目录被删后，路由仍返回路径（由调用方创建或报错），不抛异常。"""
    import shutil
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", "build")
    shutil.rmtree(env["proj"] / "output_projects" / "alpha")
    assert resolve_bound_output_dir(env["proj"], env["goal"].id) is not None
