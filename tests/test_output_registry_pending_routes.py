"""S3 测试：待确认关联 REST 端点（goal_output_project_auto_link_plan.md §7）。

保证：
  1. 所有会改变正式关联的写操作（确认 / 手动关联 / 解除）缺 confirm=true 时返回 400，
     且不产生任何写入；
  2. 忽略（reject）不要求 confirm，但不会让条目重新出现为 pending；
  3. 列表端点返回目标标题，state 参数校验；
  4. 确认后路由生效、待确认期间路由不变；解除后回到无路由；
  5. 不存在的条目/节点返回 404/400，而不是 500。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.config.loader import load_config
from mini_agent.output_registry import add_pending, list_pending, link_management_output, mark_management_root
from mini_agent.output_registry.routing import resolve_bound_output_dir

GOALS = {
    "version": 1,
    "directions": [],
    "goals": [
        {"id": "s1", "level": "stage", "title": "阶段一", "parent_id": None, "status": "active"},
        {"id": "g1", "level": "goal", "title": "做出产品", "parent_id": "s1", "status": "active"},
        {"id": "g2", "level": "goal", "title": "另一个目标", "parent_id": "s1", "status": "active"},
        {"id": "o1", "level": "objective", "title": "第1轮", "parent_id": "g1", "status": "active"},
    ],
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    root = tmp_path / "proj"
    (root / "output_projects" / "alpha" / "output").mkdir(parents=True)
    (root / ".agent").mkdir()
    (root / ".agent" / "goals.json").write_text(json.dumps(GOALS, ensure_ascii=False), encoding="utf-8")
    cfg = load_config(config_file=root / "agent_config.json", project_root=root)
    app = FastAPI()
    app.include_router(router)
    app.state.http_server = SimpleNamespace(bridge=SimpleNamespace(agent=SimpleNamespace(cfg=cfg)))
    import mini_agent.api.routes as routes
    monkeypatch.setattr(routes, "_require_owner", lambda request: None)
    return root, TestClient(app)


def _seed_pending(root: Path, *, slug="alpha", goal="o1", management="g1", role="build") -> str:
    res = add_pending(
        root, slug=slug, goal_id=goal, management_id=management, role=role,
        origin="agent_tool", valid_goal_ids={"s1", "g1", "g2", "o1"},
    )
    return res["item"]["id"]


# ── 1. 列表 ───────────────────────────────────────────────────────────────────

def test_list_returns_pending_with_goal_titles(env):
    root, client = env
    pid = _seed_pending(root)
    r = client.get("/v1/output_registry/pending_links")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["id"] for i in items] == [pid]
    assert items[0]["goal_title"] == "第1轮"
    assert items[0]["management_title"] == "做出产品"


def test_list_rejects_unknown_state(env):
    _, client = env
    assert client.get("/v1/output_registry/pending_links", params={"state": "bogus"}).status_code == 400


def test_list_rejected_and_all_states(env):
    root, client = env
    pid = _seed_pending(root)
    client.post(f"/v1/output_registry/pending_links/{pid}/reject", json={})
    assert client.get("/v1/output_registry/pending_links").json()["items"] == []
    assert [i["state"] for i in client.get("/v1/output_registry/pending_links", params={"state": "rejected"}).json()["items"]] == ["rejected"]
    assert len(client.get("/v1/output_registry/pending_links", params={"state": "all"}).json()["items"]) == 1


# ── 2. 确认 ───────────────────────────────────────────────────────────────────

def test_confirm_without_confirm_flag_writes_nothing(env):
    root, client = env
    pid = _seed_pending(root)
    r = client.post(f"/v1/output_registry/pending_links/{pid}/confirm", json={})
    assert r.status_code == 400
    assert len(list_pending(root)) == 1
    assert resolve_bound_output_dir(root, "g1") is None


def test_confirm_writes_formal_link_marks_root_and_enables_routing(env):
    root, client = env
    pid = _seed_pending(root)
    assert resolve_bound_output_dir(root, "o1") is None, "待确认期间不得路由"
    r = client.post(f"/v1/output_registry/pending_links/{pid}/confirm", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert {"management_id": "g1", "role": "build"} in r.json()["record"]["links"]
    assert list_pending(root) == []
    assert resolve_bound_output_dir(root, "o1") == root / "output_projects" / "alpha" / "output"


def test_confirm_role_override(env):
    root, client = env
    pid = _seed_pending(root)
    r = client.post(f"/v1/output_registry/pending_links/{pid}/confirm", json={"confirm": True, "role": "iterate"})
    assert r.status_code == 200
    assert {"management_id": "g1", "role": "iterate"} in r.json()["record"]["links"]


def test_confirm_invalid_role_is_400_and_keeps_item(env):
    root, client = env
    pid = _seed_pending(root)
    r = client.post(f"/v1/output_registry/pending_links/{pid}/confirm", json={"confirm": True, "role": "owner"})
    assert r.status_code == 400
    assert len(list_pending(root)) == 1


def test_confirm_unknown_id_is_404(env):
    _, client = env
    r = client.post("/v1/output_registry/pending_links/pl_missing/confirm", json={"confirm": True})
    assert r.status_code == 404


# ── 3. 忽略 ───────────────────────────────────────────────────────────────────

def test_reject_needs_no_confirm_and_stops_pending(env):
    root, client = env
    pid = _seed_pending(root)
    r = client.post(f"/v1/output_registry/pending_links/{pid}/reject", json={})
    assert r.status_code == 200
    assert r.json()["item"]["state"] == "rejected"
    assert list_pending(root, state="pending") == []


def test_reject_unknown_id_is_400_not_500(env):
    _, client = env
    r = client.post("/v1/output_registry/pending_links/pl_missing/reject", json={})
    assert r.status_code == 400


# ── 4. 手动关联 / 解除 ────────────────────────────────────────────────────────

def test_manual_link_requires_confirm(env):
    root, client = env
    r = client.post("/v1/output_registry/links", json={"slug": "alpha", "management_id": "g1", "role": "build"})
    assert r.status_code == 400
    assert resolve_bound_output_dir(root, "o1") is None


def test_manual_link_marks_node_with_tree_title_and_routes(env):
    root, client = env
    r = client.post("/v1/output_registry/links",
                    json={"confirm": True, "slug": "alpha", "management_id": "g1", "role": "build"})
    assert r.status_code == 200, r.text
    from mini_agent.output_registry import load_index
    assert load_index(root)["management_roots"]["g1"]["title"] == "做出产品"
    assert resolve_bound_output_dir(root, "o1") == root / "output_projects" / "alpha" / "output"


def test_manual_link_unknown_node_is_400(env):
    _, client = env
    r = client.post("/v1/output_registry/links",
                    json={"confirm": True, "slug": "alpha", "management_id": "ghost", "role": "build"})
    assert r.status_code == 400


def test_manual_link_invalid_role_is_400(env):
    _, client = env
    r = client.post("/v1/output_registry/links",
                    json={"confirm": True, "slug": "alpha", "management_id": "g1", "role": "owner"})
    assert r.status_code == 400


def test_unlink_requires_confirm(env):
    root, client = env
    client.post("/v1/output_registry/links",
                json={"confirm": True, "slug": "alpha", "management_id": "g1", "role": "build"})
    r = client.request("DELETE", "/v1/output_registry/links",
                       json={"slug": "alpha", "management_id": "g1", "role": "build"})
    assert r.status_code == 400
    assert resolve_bound_output_dir(root, "o1") is not None


def test_unlink_with_confirm_removes_route(env):
    root, client = env
    client.post("/v1/output_registry/links",
                json={"confirm": True, "slug": "alpha", "management_id": "g1", "role": "build"})
    r = client.request("DELETE", "/v1/output_registry/links",
                       json={"confirm": True, "slug": "alpha", "management_id": "g1", "role": "build"})
    assert r.status_code == 200, r.text
    assert resolve_bound_output_dir(root, "o1") is None


def test_unlink_nonexistent_link_is_400(env):
    _, client = env
    r = client.request("DELETE", "/v1/output_registry/links",
                       json={"confirm": True, "slug": "alpha", "management_id": "g1", "role": "build"})
    assert r.status_code == 400


# ── 5. 与 S1 账本的一致性 ─────────────────────────────────────────────────────

def test_confirm_via_route_matches_direct_registry_result(env):
    """路由确认与直接调用注册表得到相同的正式关联结构。"""
    root, client = env
    pid = _seed_pending(root)
    r = client.post(f"/v1/output_registry/pending_links/{pid}/confirm", json={"confirm": True})
    route_links = r.json()["record"]["links"]
    # 回滚到未关联状态后用直接函数复现
    client.request("DELETE", "/v1/output_registry/links",
                   json={"confirm": True, "slug": "alpha", "management_id": "g1", "role": "build"})
    mark_management_root(root, "g1", "做出产品")
    direct = link_management_output(root, "g1", "alpha", "build")["links"]
    assert route_links == direct
