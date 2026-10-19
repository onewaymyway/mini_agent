"""P2 注册表测试：管理项目标记、管理-产出关联、游离绑定、派生、重建。

对应 next_doc/management_output_project_model_plan.md 第 8.5 节与第 9 章 P2。

保证：
  1. 所有写操作只发生在 output_projects_root/_registry/ 与（派生时）目标树中；
     产出项目目录内容逐文件哈希不变；
  2. 关联规则：角色合法、管理项目已标记、产出项目存在；build 决定 owner；
  3. 操作幂等；解除最后一个关联后回到游离状态；
  4. 游离项目可以绑定到已有管理项目，或派生新的管理项目；
  5. 删除 index.json 后可以从分表重建；
  6. _registry 不出现在产出项目列表中。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from mini_agent.evolution.output_projects_root import list_projects
from mini_agent.output_registry import (
    ROLE_BUILD,
    ROLE_ITERATE,
    ROLE_REFERENCE,
    RegistryError,
    bind_floating_project,
    derive_management_from_project,
    fill_management_titles,
    link_management_output,
    list_links,
    load_index,
    mark_management_root,
    rebuild_index,
    register_project,
    unlink_management_output,
)
from mini_agent.output_registry.registry import load_record, registry_dir
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _tree_hash(root: Path, skip: str = "_registry") -> dict[str, str]:
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != skip]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            out[os.path.relpath(full, root)] = hashlib.sha256(Path(full).read_bytes()).hexdigest()
    return out


@pytest.fixture
def env(tmp_path: Path):
    proj = tmp_path / "proj"
    out_root = proj / "output_projects"
    for slug in ("alpha", "beta"):
        (out_root / slug / "output").mkdir(parents=True)
        (out_root / slug / "output" / "f.md").write_text(slug, encoding="utf-8")
    backlog = GoalBacklog(AgentPaths(project_root=proj))
    top = backlog.get_root_node()
    domain = backlog.add_node("domain", "工作", parent_id=top.id)
    stage = backlog.add_node("stage", "阶段", parent_id=domain.id)
    goal = backlog.add_node("goal", "做出产品", parent_id=stage.id)
    return {"proj": proj, "out": out_root, "backlog": backlog, "domain": domain, "stage": stage, "goal": goal}


# ── 1. 只写 _registry，不改动项目目录 ─────────────────────────────────────────

def test_register_does_not_touch_project_dirs(env):
    before = _tree_hash(env["out"])
    register_project(env["proj"], "alpha")
    mark_management_root(env["proj"], env["goal"].id, "做出产品")
    link_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)
    assert _tree_hash(env["out"]) == before, "注册与关联不得修改产出项目目录"


def test_goals_json_untouched_by_link_operations(env):
    goals_path = env["proj"] / ".agent" / "goals.json"
    raw = goals_path.read_bytes()
    register_project(env["proj"], "alpha")
    mark_management_root(env["proj"], env["goal"].id, "t")
    link_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)
    assert goals_path.read_bytes() == raw


# ── 2. 注册 ───────────────────────────────────────────────────────────────────

def test_register_creates_legacy_floating_record(env):
    rec = register_project(env["proj"], "alpha")
    assert rec["legacy"] is True and rec["managed"] is False
    assert rec["status"] == "floating" and rec["owner_goal_id"] is None
    assert load_index(env["proj"])["projects"]["alpha"]["status"] == "floating"


def test_register_unknown_project_rejected(env):
    with pytest.raises(RegistryError):
        register_project(env["proj"], "nope")


def test_register_is_idempotent(env):
    register_project(env["proj"], "alpha")
    first = load_record(env["proj"], "alpha")
    register_project(env["proj"], "alpha")
    assert load_record(env["proj"], "alpha")["created_at"] == first["created_at"]


# ── 3. 管理项目标记 ───────────────────────────────────────────────────────────

def test_mark_management_root_validates_goal_id(env):
    with pytest.raises(RegistryError):
        mark_management_root(env["proj"], "ghost", valid_goal_ids={env["goal"].id})
    mark_management_root(env["proj"], env["goal"].id, "t", valid_goal_ids={env["goal"].id})
    assert env["goal"].id in load_index(env["proj"])["management_roots"]


# ── 4. 关联规则 ───────────────────────────────────────────────────────────────

def test_build_link_sets_owner_and_iterate_adds_ref(env):
    mid_build = env["goal"].id
    mid_iter = env["stage"].id
    mark_management_root(env["proj"], mid_build)
    mark_management_root(env["proj"], mid_iter)
    rec = link_management_output(env["proj"], mid_build, "alpha", ROLE_BUILD)
    assert rec["owner_goal_id"] == mid_build and rec["status"] == "active"
    rec = link_management_output(env["proj"], mid_iter, "alpha", ROLE_ITERATE)
    assert rec["owner_goal_id"] == mid_build
    assert mid_iter in rec["goal_refs"]


def test_link_is_idempotent(env):
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)
    rec = link_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)
    assert len(rec["links"]) == 1
    assert len(list_links(env["proj"])) == 1


def test_invalid_role_rejected(env):
    mark_management_root(env["proj"], env["goal"].id)
    with pytest.raises(RegistryError):
        link_management_output(env["proj"], env["goal"].id, "alpha", "owner")


def test_link_requires_marked_management_root(env):
    with pytest.raises(RegistryError):
        link_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)


def test_link_requires_existing_project_dir(env):
    mark_management_root(env["proj"], env["goal"].id)
    with pytest.raises(RegistryError):
        link_management_output(env["proj"], env["goal"].id, "ghost", ROLE_BUILD)


# ── 5. 解除关联 ───────────────────────────────────────────────────────────────

def test_unlink_last_link_returns_to_floating(env):
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)
    rec = unlink_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)
    assert rec["status"] == "floating"
    assert rec["owner_goal_id"] is None
    assert rec["links"] == []


def test_unlink_nonexistent_rejected(env):
    register_project(env["proj"], "alpha")
    with pytest.raises(RegistryError):
        unlink_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)


# ── 6. 游离绑定与派生 ─────────────────────────────────────────────────────────

def test_bind_floating_project_to_existing_management(env):
    mark_management_root(env["proj"], env["goal"].id)
    rec = bind_floating_project(env["proj"], "beta", env["goal"].id, ROLE_REFERENCE)
    assert rec["status"] == "active"
    assert rec["links"] == [{"management_id": env["goal"].id, "role": ROLE_REFERENCE}]


def test_derive_management_creates_goal_and_link(env):
    before_nodes = len(env["backlog"].all_nodes())
    new_id = derive_management_from_project(
        env["proj"], "beta", env["backlog"], env["domain"].id, "维护 beta", role=ROLE_BUILD,
    )
    assert len(env["backlog"].all_nodes()) == before_nodes + 1
    node = env["backlog"].get(new_id)
    assert node is not None and node.parent_id == env["domain"].id and node.is_goal
    rec = load_record(env["proj"], "beta")
    assert rec["owner_goal_id"] == new_id


def test_derive_rejects_parent_that_violates_tree_hierarchy(env):
    """goal 不能直接挂在 ultimate 根下：应给出 RegistryError，而不是裸 ValueError。"""
    with pytest.raises(RegistryError):
        derive_management_from_project(env["proj"], "beta", env["backlog"],
                                       env["backlog"].get_root_node().id, "x")


def test_derive_rejects_unknown_parent(env):
    with pytest.raises(RegistryError):
        derive_management_from_project(env["proj"], "beta", env["backlog"], "ghost", "x")


# ── 7. 列表与重建 ─────────────────────────────────────────────────────────────

def test_registry_dir_excluded_from_project_list(env):
    register_project(env["proj"], "alpha")
    names = {p["name"] for p in list_projects(env["proj"])}
    assert "_registry" not in names
    assert {"alpha", "beta"} <= names


def test_rebuild_index_after_deleting_index_file(env):
    mark_management_root(env["proj"], env["goal"].id, "做出产品")
    link_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)
    link_management_output(env["proj"], env["goal"].id, "beta", ROLE_ITERATE)
    index_path = registry_dir(env["proj"]) / "index.json"
    index_path.unlink()

    rebuilt = rebuild_index(env["proj"])
    assert set(rebuilt["projects"]) == {"alpha", "beta"}
    assert env["goal"].id in rebuilt["management_roots"]
    assert rebuilt["projects"]["alpha"]["owner_goal_id"] == env["goal"].id
    assert len(list_links(env["proj"])) == 2

    # 标题无法从注册表恢复，只能由目标树补齐
    assert rebuilt["management_roots"][env["goal"].id]["title"] == ""
    fill_management_titles(env["proj"], {env["goal"].id: "做出产品"})
    assert load_index(env["proj"])["management_roots"][env["goal"].id]["title"] == "做出产品"


def test_rebuild_does_not_change_project_dirs(env):
    before = _tree_hash(env["out"])
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)
    (registry_dir(env["proj"]) / "index.json").unlink()
    rebuild_index(env["proj"])
    assert _tree_hash(env["out"]) == before


def test_index_json_is_valid_json_after_operations(env):
    mark_management_root(env["proj"], env["goal"].id)
    link_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)
    data = json.loads((registry_dir(env["proj"]) / "index.json").read_text(encoding="utf-8"))
    assert data["schema"] == "output_registry.index.v1"
