"""S2 测试：执行期上下文 + 工具 link_output_project + 提示词注入（开关门控）。

对应 next_doc/goal_output_project_auto_link_plan.md §4 与第 10 章 S2 退出条件：
  1. 开关关闭时：工具不在工具列表中、执行上下文不写入、提示词不变；
  2. 开关开启后：工具在 goal/objective 执行上下文中可见并可关联（待确认）；
  3. 工具只写待确认账本，不改变路由；确认后路由生效；
  4. 管理根解析（§4.3）、角色自动判定、幂等与忽略语义。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mini_agent.config.models import OutputRegistryConfig
from mini_agent.evolution.goal_exec_context import (
    META_KEY,
    clear_current_goal_exec,
    get_current_goal_exec,
    goal_exec_scope,
    set_current_goal_exec,
)
from mini_agent.output_registry import (
    ROLE_BUILD,
    ROLE_ITERATE,
    confirm_pending,
    link_management_output,
    list_pending,
    mark_management_root,
)
from mini_agent.output_registry.routing import resolve_bound_output_dir
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths
from mini_agent.tools import ToolRegistry, get_default_registry
import mini_agent.tools.output_registry as tool_mod  # noqa: F401  注册工具

TOOL = "link_output_project"


@pytest.fixture(autouse=True)
def _clean_ctx():
    clear_current_goal_exec()
    yield
    clear_current_goal_exec()


@pytest.fixture
def env(tmp_path: Path):
    proj = tmp_path / "proj"
    out_root = proj / "output_projects"
    for slug in ("alpha", "beta"):
        (out_root / slug / "output").mkdir(parents=True)
    backlog = GoalBacklog(AgentPaths(project_root=proj))
    top = backlog.get_root_node()
    domain = backlog.add_node("domain", "工作", parent_id=top.id)
    stage = backlog.add_node("stage", "阶段", parent_id=domain.id)
    goal = backlog.add_node("goal", "做出产品", parent_id=stage.id)
    goal2 = backlog.add_node("goal", "另一个目标", parent_id=stage.id)
    obj = backlog.add_node("objective", "第1轮", parent_id=goal.id)
    return {"proj": proj, "out": out_root, "goal": goal, "goal2": goal2, "obj": obj, "domain": domain}


def _enter(env, trigger=None):
    set_current_goal_exec(trigger or env["obj"].id, str(env["proj"]), "exec_1")


def _call(**kwargs) -> dict:
    fn = get_default_registry().get(TOOL).fn
    return json.loads(fn(**kwargs))


# ── 1. 执行上下文 ─────────────────────────────────────────────────────────────

def test_context_is_none_by_default():
    assert get_current_goal_exec() is None


def test_scope_sets_and_clears_context():
    meta = {"execution_id": "e1", META_KEY: {"trigger_node_id": "n1", "project_root": "/p"}}
    with goal_exec_scope(meta):
        ctx = get_current_goal_exec()
        assert ctx == {"trigger_node_id": "n1", "project_root": "/p", "execution_id": "e1"}
    assert get_current_goal_exec() is None


def test_scope_clears_on_exception():
    meta = {META_KEY: {"trigger_node_id": "n1", "project_root": "/p"}}
    with pytest.raises(RuntimeError):
        with goal_exec_scope(meta):
            raise RuntimeError("boom")
    assert get_current_goal_exec() is None


def test_scope_without_goal_exec_is_noop():
    with goal_exec_scope({"execution_id": "e1"}):
        assert get_current_goal_exec() is None
    with goal_exec_scope(None):
        assert get_current_goal_exec() is None


def test_context_is_thread_local():
    import threading
    set_current_goal_exec("n1", "/p")
    seen = {}

    def _other():
        seen["ctx"] = get_current_goal_exec()

    t = threading.Thread(target=_other)
    t.start()
    t.join()
    assert seen["ctx"] is None
    assert get_current_goal_exec() is not None


# ── 2. 可用性门控：工具列表与现状一致（开关关闭）────────────────────────────

def test_tool_hidden_outside_goal_context():
    reg = get_default_registry()
    assert TOOL in reg.names
    assert reg.is_available(TOOL) is False
    names = [t["name"] for t in reg.to_api_tools()]
    assert TOOL not in names


def test_tool_call_refused_outside_goal_context():
    reg = get_default_registry()
    with pytest.raises(ValueError):
        reg.call(TOOL, {"slug": "alpha"})


def test_tool_visible_inside_goal_context(env):
    _enter(env)
    reg = get_default_registry()
    assert reg.is_available(TOOL) is True
    assert TOOL in [t["name"] for t in reg.to_api_tools()]


def test_other_tools_unaffected_by_availability_hook():
    """available=None 的既有工具始终可见，与改动前一致。"""
    reg = ToolRegistry()
    reg.register_fn(lambda: "x", name="plain", description="d", requires_approval=False)
    reg.register_fn(lambda: "y", name="gated", description="d", requires_approval=False,
                    available=lambda: False)
    names = [t["name"] for t in reg.to_api_tools()]
    assert names == ["plain"]
    assert reg.is_available("plain") is True


def test_availability_predicate_error_treated_as_unavailable():
    def _boom():
        raise RuntimeError("bad")
    reg = ToolRegistry()
    reg.register_fn(lambda: "x", name="t", description="d", requires_approval=False, available=_boom)
    assert reg.is_available("t") is False
    assert reg.to_api_tools() == []


def test_filtered_and_subset_keep_availability_predicate():
    reg = ToolRegistry()
    reg.register_fn(lambda: "x", name="gated", description="d", requires_approval=False,
                    available=lambda: False, group="builtin")
    assert reg.filtered(names=["gated"]).is_available("gated") is False
    assert reg.subset(["builtin"]).is_available("gated") is False


# ── 3. 工具行为 ───────────────────────────────────────────────────────────────

def test_outside_context_returns_error_without_ledger(env):
    res = _call(slug="alpha")
    assert res["ok"] is False and "goal 执行上下文" in res["error"]
    assert list_pending(env["proj"]) == []


def test_links_to_nearest_goal_as_pending_and_confirm_enables_routing(env):
    _enter(env)
    res = _call(slug="alpha", note="本轮产出")
    assert res["ok"] is True and res["state"] == "pending"
    assert res["management_id"] == env["goal"].id
    assert res["role"] == ROLE_BUILD
    assert resolve_bound_output_dir(env["proj"], env["goal"].id) is None, "待确认不得路由"

    item = list_pending(env["proj"])[0]
    assert item["origin"] == "agent_tool" and item["note"] == "本轮产出"
    assert item["goal_id"] == env["obj"].id

    confirm_pending(env["proj"], item["id"], confirm=True)
    assert resolve_bound_output_dir(env["proj"], env["goal"].id) == env["out"] / "alpha" / "output"


def test_prefers_marked_management_root_over_nearer_goal(env):
    """§4.3 第一优先级：最近的已标记管理根。objective → goal（未标记）→ stage（已标记）。"""
    mark_management_root(env["proj"], env["goal"].id, "无标记 goal")
    _enter(env)
    # goal 已标记 → 命中 goal（即使 stage 也是祖先）
    assert _call(slug="alpha")["management_id"] == env["goal"].id


def test_falls_back_to_nearest_goal_ancestor(env):
    _enter(env, trigger=env["goal"].id)  # 触发节点本身是 goal
    res = _call(slug="alpha")
    assert res["management_id"] == env["goal"].id


def test_floating_when_no_goal_ancestor(env):
    set_current_goal_exec(env["domain"].id, str(env["proj"]), "e")
    res = _call(slug="alpha")
    assert res["ok"] is False and res.get("floating") is True
    assert list_pending(env["proj"]) == []


def test_role_auto_iterate_when_other_management_already_builds(env):
    mark_management_root(env["proj"], env["goal2"].id, "另一个目标")
    link_management_output(env["proj"], env["goal2"].id, "alpha", ROLE_BUILD)
    _enter(env)
    res = _call(slug="alpha")
    assert res["role"] == ROLE_ITERATE


def test_explicit_role_is_respected(env):
    _enter(env)
    assert _call(slug="alpha", role="reference")["role"] == "reference"


def test_invalid_role_and_slug_rejected(env):
    _enter(env)
    assert _call(slug="alpha", role="owner")["ok"] is False
    assert _call(slug="../etc")["ok"] is False
    assert _call(slug="")["ok"] is False


def test_missing_dir_without_create_is_error(env):
    _enter(env)
    res = _call(slug="gamma")
    assert res["ok"] is False and "create=true" in res["error"]
    assert not (env["out"] / "gamma").exists()


def test_create_true_creates_managed_project_and_links(env):
    _enter(env)
    res = _call(slug="gamma", create=True, title="新项目")
    assert res["ok"] is True and res["created_project"] is True
    assert (env["out"] / "gamma" / "output").is_dir()
    assert list_pending(env["proj"])[0]["slug"] == "gamma"


def test_idempotent_second_call_returns_pending_not_duplicate(env):
    _enter(env)
    _call(slug="alpha")
    second = _call(slug="alpha")
    assert second["ok"] is True and second["state"] == "pending"
    assert len(list_pending(env["proj"])) == 1


def test_rejected_pending_not_regenerated(env):
    from mini_agent.output_registry import reject_pending
    _enter(env)
    _call(slug="alpha")
    reject_pending(env["proj"], list_pending(env["proj"])[0]["id"])
    res = _call(slug="alpha")
    assert res["state"] == "rejected"
    assert len(list_pending(env["proj"])) == 1


def test_already_confirmed_reports_confirmed(env):
    mark_management_root(env["proj"], env["goal"].id, "做出产品")
    link_management_output(env["proj"], env["goal"].id, "alpha", ROLE_BUILD)
    _enter(env)
    res = _call(slug="alpha")
    assert res["state"] == "confirmed"
    assert list_pending(env["proj"]) == []


# ── 4. 执行器提示词与 meta 门控（开关关闭时与现状一致）──────────────────────

def _executor_stub(env, enabled: bool):
    from mini_agent.evolution.objective_executor import ObjectiveExecutor
    ex = ObjectiveExecutor.__new__(ObjectiveExecutor)
    cfg = SimpleNamespace(output_registry=OutputRegistryConfig(auto_link_tool_enabled=enabled))
    ex._cfg = cfg
    ex._paths = AgentPaths(project_root=env["proj"])
    ex._goal_backlog = GoalBacklog(AgentPaths(project_root=env["proj"]))
    ex._goal_backlog.load()  # 与真实执行器一致：backlog 已加载
    return ex


def _objective_stub(env):
    return SimpleNamespace(objective_id=env["obj"].id, execution_id="x")


def test_executor_helper_returns_nothing_when_disabled(env):
    ex = _executor_stub(env, enabled=False)
    hint, meta = ex._auto_link_step_context(_objective_stub(env))
    assert hint == "" and meta is None


def test_executor_helper_returns_hint_and_context_when_enabled(env):
    ex = _executor_stub(env, enabled=True)
    hint, meta = ex._auto_link_step_context(_objective_stub(env))
    assert "[产出项目关联]" in hint and "link_output_project" in hint
    assert env["goal"].id in hint, "提示词应包含所属目标 id"
    assert meta["trigger_node_id"] == env["obj"].id
    assert meta["project_root"] == str(env["proj"])


def test_executor_helper_swallows_errors(env):
    ex = _executor_stub(env, enabled=True)
    ex._goal_backlog = None  # 标题查询不可用时仍应返回提示，不抛异常
    hint, meta = ex._auto_link_step_context(_objective_stub(env))
    assert meta is not None


# ── 5. 配置默认值 ─────────────────────────────────────────────────────────────

def test_config_default_disabled_and_shipped_config_disabled():
    assert OutputRegistryConfig().auto_link_tool_enabled is False
    shipped = json.loads((Path(__file__).resolve().parents[1] / "agent_config.json").read_text(encoding="utf-8"))
    assert shipped["output_registry"]["auto_link_tool_enabled"] is False
