"""S1 测试：待确认关联账本（goal_output_project_auto_link_plan.md 第 3 章）。

保证：
  1. 账本只写 _registry/pending_links.json，不改动产出项目目录与 goals.json；
  2. add_pending 幂等；已有正式关联时返回 confirmed 且不写账本；
  3. 忽略（rejected）后，同 (slug, goal_id) 不再重复生成；
  4. 确认前待确认关联不影响路由；确认后路由生效，账本条目删除；
  5. 账本可删除：正式关联不丢失，路由结果不变；
  6. confirm 必须显式 confirm=True；失败时账本条目保留可重试；
  7. 账本损坏视为空账本，不抛异常。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from mini_agent.output_registry import (
    ROLE_BUILD,
    ROLE_ITERATE,
    RegistryError,
    add_pending,
    confirm_pending,
    get_pending,
    link_management_output,
    list_pending,
    load_index,
    mark_management_root,
    reject_pending,
)
from mini_agent.output_registry.pending_links import (
    OUTCOME_CONFIRMED,
    OUTCOME_CREATED,
    OUTCOME_EXISTING,
    OUTCOME_REJECTED,
    ORIGIN_AGENT_TOOL,
    ORIGIN_AUTO_DETECT,
    STATE_PENDING,
    STATE_REJECTED,
    ledger_path,
)
from mini_agent.output_registry.registry import load_record, list_links
from mini_agent.output_registry.routing import resolve_bound_output_dir
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
    goal2 = backlog.add_node("goal", "另一个目标", parent_id=stage.id)
    valid_ids = {n.id for n in (top, domain, stage, goal, goal2)}
    return {
        "proj": proj,
        "out": out_root,
        "goal": goal,
        "goal2": goal2,
        "valid_ids": valid_ids,
    }


def _ledger_file(env) -> Path:
    return ledger_path(env["proj"])


# ── 1. 只写账本 ───────────────────────────────────────────────────────────────

def test_add_and_confirm_do_not_touch_project_dirs_or_goals(env):
    goals_path = env["proj"] / ".agent" / "goals.json"
    goals_raw = goals_path.read_bytes()
    before = _tree_hash(env["out"])

    add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                management_id=env["goal"].id, role=ROLE_BUILD,
                origin=ORIGIN_AGENT_TOOL, valid_goal_ids=env["valid_ids"])
    item = list_pending(env["proj"])[0]
    confirm_pending(env["proj"], item["id"], confirm=True)

    assert _tree_hash(env["out"]) == before, "账本与确认不得修改产出项目目录"
    assert goals_path.read_bytes() == goals_raw, "账本与确认不得修改 goals.json"


# ── 2. 登记与幂等 ─────────────────────────────────────────────────────────────

def test_add_creates_pending_item(env):
    res = add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                      management_id=env["goal"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AGENT_TOOL, note="一句话理由")
    assert res["outcome"] == OUTCOME_CREATED
    item = res["item"]
    assert item["state"] == STATE_PENDING
    assert item["origin"] == ORIGIN_AGENT_TOOL
    assert item["note"] == "一句话理由"
    assert item["id"].startswith("pl_")
    assert _ledger_file(env).is_file()


def test_add_is_idempotent_for_same_slug_goal_role(env):
    kwargs = dict(slug="alpha", goal_id=env["goal"].id, management_id=env["goal"].id,
                  role=ROLE_BUILD, origin=ORIGIN_AGENT_TOOL)
    first = add_pending(env["proj"], **kwargs)
    second = add_pending(env["proj"], **kwargs)
    assert second["outcome"] == OUTCOME_EXISTING
    assert second["item"]["id"] == first["item"]["id"]
    assert len(list_pending(env["proj"])) == 1


def test_add_with_different_role_is_separate_item(env):
    add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                management_id=env["goal"].id, role=ROLE_BUILD, origin=ORIGIN_AGENT_TOOL)
    add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                management_id=env["goal"].id, role=ROLE_ITERATE, origin=ORIGIN_AGENT_TOOL)
    assert len(list_pending(env["proj"], state=STATE_PENDING)) == 2


def test_add_when_formal_link_exists_returns_confirmed_without_ledger_write(env):
    mark_management_root(env["proj"], env["goal"].id, "做出产品")
    link_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)
    res = add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                      management_id=env["goal"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AGENT_TOOL)
    assert res["outcome"] == OUTCOME_CONFIRMED
    assert res["item"] is None
    assert not _ledger_file(env).exists()


def test_add_rejects_bad_role_origin_and_unknown_slug(env):
    base = dict(goal_id=env["goal"].id, management_id=env["goal"].id)
    with pytest.raises(RegistryError):
        add_pending(env["proj"], slug="alpha", role="owner", origin=ORIGIN_AGENT_TOOL, **base)
    with pytest.raises(RegistryError):
        add_pending(env["proj"], slug="alpha", role=ROLE_BUILD, origin="manual", **base)
    with pytest.raises(RegistryError):
        add_pending(env["proj"], slug="nope", role=ROLE_BUILD, origin=ORIGIN_AGENT_TOOL, **base)
    with pytest.raises(RegistryError):
        add_pending(env["proj"], slug="alpha", role=ROLE_BUILD, origin=ORIGIN_AGENT_TOOL,
                    goal_id="", management_id=env["goal"].id)


def test_add_validates_goal_ids_when_tree_provided(env):
    with pytest.raises(RegistryError):
        add_pending(env["proj"], slug="alpha", goal_id="ghost", management_id=env["goal"].id,
                    role=ROLE_BUILD, origin=ORIGIN_AGENT_TOOL, valid_goal_ids=env["valid_ids"])


def test_add_does_not_mark_management_root(env):
    """管理根标记延后到确认时才发生。"""
    add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                management_id=env["goal"].id, role=ROLE_BUILD, origin=ORIGIN_AGENT_TOOL)
    assert env["goal"].id not in load_index(env["proj"])["management_roots"]
    assert load_record(env["proj"], "alpha") is None or not load_record(env["proj"], "alpha")["links"]


# ── 3. 忽略与重复生成 ─────────────────────────────────────────────────────────

def test_rejected_blocks_regeneration_for_same_slug_goal(env):
    res = add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                      management_id=env["goal"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AUTO_DETECT)
    reject_pending(env["proj"], res["item"]["id"])
    again = add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                        management_id=env["goal"].id, role=ROLE_BUILD,
                        origin=ORIGIN_AUTO_DETECT)
    assert again["outcome"] == OUTCOME_REJECTED
    assert len(list_pending(env["proj"])) == 1, "忽略后不得新增条目"
    assert len(list_pending(env["proj"], state=STATE_REJECTED)) == 1


def test_rejected_does_not_block_other_goal(env):
    res = add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                      management_id=env["goal"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AUTO_DETECT)
    reject_pending(env["proj"], res["item"]["id"])
    other = add_pending(env["proj"], slug="alpha", goal_id=env["goal2"].id,
                        management_id=env["goal2"].id, role=ROLE_BUILD,
                        origin=ORIGIN_AUTO_DETECT)
    assert other["outcome"] == OUTCOME_CREATED


def test_reject_is_idempotent_and_unknown_id_raises(env):
    res = add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                      management_id=env["goal"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AUTO_DETECT)
    first = reject_pending(env["proj"], res["item"]["id"])
    second = reject_pending(env["proj"], res["item"]["id"])
    assert first["state"] == second["state"] == STATE_REJECTED
    with pytest.raises(RegistryError):
        reject_pending(env["proj"], "pl_missing")


# ── 4. 确认 ───────────────────────────────────────────────────────────────────

def test_confirm_requires_explicit_true(env):
    res = add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                      management_id=env["goal"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AGENT_TOOL)
    with pytest.raises(RegistryError):
        confirm_pending(env["proj"], res["item"]["id"], confirm=False)
    with pytest.raises(RegistryError):
        confirm_pending(env["proj"], res["item"]["id"], confirm="yes")
    assert get_pending(env["proj"], res["item"]["id"]) is not None


def test_confirm_writes_formal_link_marks_root_and_removes_item(env):
    res = add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                      management_id=env["goal"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AGENT_TOOL)
    rec = confirm_pending(env["proj"], res["item"]["id"], confirm=True, title="做出产品")

    assert {"management_id": env["goal"].id, "role": ROLE_BUILD} in rec["links"]
    assert env["goal"].id in load_index(env["proj"])["management_roots"]
    assert get_pending(env["proj"], res["item"]["id"]) is None
    assert list_pending(env["proj"]) == []


def test_confirm_role_override(env):
    res = add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                      management_id=env["goal"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AGENT_TOOL)
    rec = confirm_pending(env["proj"], res["item"]["id"], confirm=True, role=ROLE_ITERATE)
    assert {"management_id": env["goal"].id, "role": ROLE_ITERATE} in rec["links"]
    assert {"management_id": env["goal"].id, "role": ROLE_BUILD} not in rec["links"]


def test_confirm_rejects_invalid_role_override_and_keeps_item(env):
    res = add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                      management_id=env["goal"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AGENT_TOOL)
    with pytest.raises(RegistryError):
        confirm_pending(env["proj"], res["item"]["id"], confirm=True, role="owner")
    assert get_pending(env["proj"], res["item"]["id"]) is not None


def test_confirm_rejected_item_raises(env):
    res = add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                      management_id=env["goal"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AUTO_DETECT)
    reject_pending(env["proj"], res["item"]["id"])
    with pytest.raises(RegistryError):
        confirm_pending(env["proj"], res["item"]["id"], confirm=True)


def test_confirm_failure_keeps_item_for_retry(env):
    """确认过程中项目目录被删除 → 正式关联失败 → 账本条目保留。"""
    res = add_pending(env["proj"], slug="beta", goal_id=env["goal"].id,
                      management_id=env["goal"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AGENT_TOOL)
    import shutil
    shutil.rmtree(env["out"] / "beta")
    with pytest.raises(RegistryError):
        confirm_pending(env["proj"], res["item"]["id"], confirm=True)
    assert get_pending(env["proj"], res["item"]["id"]) is not None


# ── 5. 路由不受待确认影响（路由特征测试） ────────────────────────────────────

def test_pending_does_not_affect_routing_until_confirmed(env):
    mark_management_root(env["proj"], env["goal"].id, "做出产品")
    before = resolve_bound_output_dir(env["proj"], env["goal"].id)
    assert before is None, "未关联时不路由"

    res = add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                      management_id=env["goal"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AGENT_TOOL)
    assert resolve_bound_output_dir(env["proj"], env["goal"].id) is None, "待确认不得参与路由"

    confirm_pending(env["proj"], res["item"]["id"], confirm=True)
    got = resolve_bound_output_dir(env["proj"], env["goal"].id)
    assert got == env["out"] / "alpha" / "output", "确认后路由生效"


def test_pending_on_sibling_goal_routes_only_after_confirm(env):
    """待确认关联只在确认后影响路由；兄弟节点 goal 的关联不会被 goal2 继承。"""
    mark_management_root(env["proj"], env["goal"].id, "做出产品")
    link_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)
    res = add_pending(env["proj"], slug="beta", goal_id=env["goal2"].id,
                      management_id=env["goal2"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AGENT_TOOL)
    assert resolve_bound_output_dir(env["proj"], env["goal2"].id) is None
    assert resolve_bound_output_dir(env["proj"], env["goal"].id) == env["out"] / "alpha" / "output"

    confirm_pending(env["proj"], res["item"]["id"], confirm=True)
    assert resolve_bound_output_dir(env["proj"], env["goal2"].id) == env["out"] / "beta" / "output"
    assert resolve_bound_output_dir(env["proj"], env["goal"].id) == env["out"] / "alpha" / "output"


# ── 6. 账本可删除、可重建 ─────────────────────────────────────────────────────

def test_deleting_ledger_keeps_formal_links_and_routing(env):
    mark_management_root(env["proj"], env["goal"].id, "做出产品")
    link_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)
    res = add_pending(env["proj"], slug="beta", goal_id=env["goal2"].id,
                      management_id=env["goal2"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AUTO_DETECT)
    assert res["outcome"] == OUTCOME_CREATED

    routing_before = resolve_bound_output_dir(env["proj"], env["goal"].id)
    links_before = list_links(env["proj"])

    _ledger_file(env).unlink()

    assert list_pending(env["proj"]) == []
    assert list_links(env["proj"]) == links_before
    assert resolve_bound_output_dir(env["proj"], env["goal"].id) == routing_before


# ── 7. 账本损坏 ───────────────────────────────────────────────────────────────

def test_corrupt_ledger_treated_as_empty(env):
    _ledger_file(env).parent.mkdir(parents=True, exist_ok=True)
    _ledger_file(env).write_text("{not json", encoding="utf-8")
    assert list_pending(env["proj"]) == []
    res = add_pending(env["proj"], slug="alpha", goal_id=env["goal"].id,
                      management_id=env["goal"].id, role=ROLE_BUILD,
                      origin=ORIGIN_AGENT_TOOL)
    assert res["outcome"] == OUTCOME_CREATED
    assert json.loads(_ledger_file(env).read_text(encoding="utf-8"))["items"][0]["slug"] == "alpha"
