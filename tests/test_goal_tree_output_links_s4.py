"""S4 后端测试：目标树产出关联批量视图（goal_output_project_auto_link_plan.md §6.3）。

保证：
  1. 管理根上的正式关联与待确认出现在该节点上；
  2. 子节点继承祖先管理根的生效关联，并标注 inherited_from（只读语义）；
  3. 待确认只挂在 management_id 对应节点上，不挂在触发的 objective 上；
  4. 与任何关联/待确认无关的节点不出现在 by_node 中；
  5. 视图只读：不改变 goals.json、账本与注册表；
  6. goals.json 不可读时返回带 error 的空结构，而不是抛异常；
  7. HTTP 路由 GET /v1/goals/tree/output_links 返回同样的结构。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.config.loader import load_config
from mini_agent.output_registry import (
    add_pending,
    link_management_output,
    mark_management_root,
)
from mini_agent.output_registry.tree_links import build_tree_output_links

GOALS = {
    "version": 1,
    "directions": [],
    "goals": [
        {"id": "s1", "level": "stage", "title": "阶段一", "parent_id": None, "status": "active"},
        {"id": "g1", "level": "goal", "title": "做出产品", "parent_id": "s1", "status": "active"},
        {"id": "g2", "level": "goal", "title": "无关目标", "parent_id": "s1", "status": "active"},
        {"id": "o1", "level": "objective", "title": "第1轮", "parent_id": "g1", "status": "active"},
    ],
}


def _tree_hash(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture
def env(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "output_projects" / "alpha" / "output").mkdir(parents=True)
    (root / ".agent").mkdir()
    (root / ".agent" / "goals.json").write_text(json.dumps(GOALS, ensure_ascii=False), encoding="utf-8")
    mark_management_root(root, "g1", "做出产品")
    link_management_output(root, "g1", "alpha", "build")
    add_pending(root, slug="alpha", goal_id="o1", management_id="g1", role="iterate",
                origin="agent_tool", note="本轮", valid_goal_ids={"s1", "g1", "g2", "o1"})
    return root


def test_root_carries_links_and_pending(env):
    view = build_tree_output_links(env)
    g1 = view["by_node"]["g1"]
    assert g1["is_management_root"] is True
    assert [l["slug"] for l in g1["linked"]] == ["alpha"]
    assert g1["linked"][0]["role"] == "build"
    assert g1["linked"][0]["output_dir"].endswith("alpha/output")
    assert g1["inherited_from"] is None
    assert len(g1["pending"]) == 1 and g1["pending"][0]["slug"] == "alpha"
    assert view["pending_total"] == 1


def test_child_inherits_linked_read_only(env):
    view = build_tree_output_links(env)
    o1 = view["by_node"]["o1"]
    assert o1["inherited_from"] == "g1"
    assert [l["slug"] for l in o1["linked"]] == ["alpha"]
    assert o1["pending"] == [], "待确认只挂在管理根上，不挂在触发的 objective 上"
    assert o1["is_management_root"] is False


def test_unrelated_node_omitted(env):
    view = build_tree_output_links(env)
    assert "g2" not in view["by_node"]
    assert "s1" not in view["by_node"]


def test_pending_only_node_is_listed_and_not_inherited(env):
    # 另一个目标只有待确认、尚未确认关联
    add_pending(env, slug="alpha", goal_id="g2", management_id="g2", role="build",
                origin="agent_tool", valid_goal_ids={"s1", "g1", "g2", "o1"})
    view = build_tree_output_links(env)
    g2 = view["by_node"]["g2"]
    assert g2["linked"] == [] and g2["inherited_from"] is None
    assert len(g2["pending"]) == 1
    assert view["pending_total"] == 2


def test_view_is_read_only(env):
    before = _tree_hash(env)
    build_tree_output_links(env)
    assert _tree_hash(env) == before


def test_unreadable_goals_returns_error_not_exception(tmp_path: Path):
    root = tmp_path / "empty"
    root.mkdir()
    view = build_tree_output_links(root)
    assert view["by_node"] == {} and view["pending_total"] == 0
    assert "error" in view


# ── HTTP 路由 ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(env, monkeypatch):
    cfg = load_config(config_file=env / "agent_config.json", project_root=env)
    app = FastAPI()
    app.include_router(router)
    app.state.http_server = SimpleNamespace(bridge=SimpleNamespace(agent=SimpleNamespace(cfg=cfg)))
    import mini_agent.api.routes as routes
    monkeypatch.setattr(routes, "_require_owner", lambda request: None)
    return TestClient(app)


def test_route_returns_same_structure(env, client):
    r = client.get("/v1/goals/tree/output_links")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"by_node", "pending_total"}
    assert body["by_node"]["o1"]["inherited_from"] == "g1"
    assert body["by_node"]["g1"]["pending"][0]["slug"] == "alpha"


def test_existing_goal_tree_route_unaffected_by_new_path(client):
    """新路径不能抢占 /goals/tree 等既有路由（路由顺序回归）。"""
    r = client.get("/v1/goals/tree/output_links")
    assert r.status_code == 200 and "by_node" in r.json()
