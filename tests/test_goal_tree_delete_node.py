"""目标树删除节点测试：删除自身 / 连同全部子节点，任意层级（根节点除外）。

保证：
  1. 只删自身：叶子节点可删，且父节点的 children_ids 同步摘除；
  2. 有子节点时只删自身会被拒绝（409），不做任何修改；
  3. 连同子节点删除：整棵子树移除，兄弟节点不受影响；
  4. 根节点不允许删除；不存在的节点返回 404；
  5. 被删节点的 cron job 一并移除，无关 job 保留；
  6. goal 层级节点删除时清理 daemon_run_outputs 内部目录；
  7. current_focus_ids 不留悬空引用；原有 delete_goal 语义不变。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import mini_agent.api.routes as routes
from mini_agent.api.routes import router
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


@pytest.fixture
def tree(tmp_path: Path):
    (tmp_path / ".agent").mkdir()
    paths = AgentPaths(project_root=tmp_path)
    b = GoalBacklog(paths)
    b.load()
    top = b.get_root_node()
    dom = b.add_node("domain", "工作", parent_id=top.id)
    g1 = b.add_node("goal", "做出产品", parent_id=dom.id)
    o1 = b.add_node("objective", "调研", parent_id=g1.id)
    o2 = b.add_node("objective", "原型", parent_id=g1.id)
    g2 = b.add_node("goal", "学习英语", parent_id=dom.id)
    return tmp_path, paths, b, SimpleNamespace(top=top, dom=dom, g1=g1, o1=o1, o2=o2, g2=g2)


def _reload(paths):
    b = GoalBacklog(paths)
    b.load()
    return b


# ── 后端规则 ────────────────────────────────────────────────────────────

def test_delete_leaf_objective_updates_parent(tree):
    _, paths, b, n = tree
    deleted = b.delete_node(n.o1.id, include_descendants=False)
    assert deleted == [n.o1.id]
    b2 = _reload(paths)
    assert b2.get(n.o1.id) is None
    assert n.o1.id not in b2.get(n.g1.id).children_ids
    assert b2.get(n.o2.id) is not None


def test_self_only_refused_when_children_exist(tree):
    _, paths, b, n = tree
    with pytest.raises(ValueError):
        b.delete_node(n.g1.id, include_descendants=False)
    b2 = _reload(paths)
    assert b2.get(n.g1.id) is not None
    assert len(b2.get(n.g1.id).children_ids) == 2


def test_delete_subtree_removes_all_descendants_only(tree):
    _, paths, b, n = tree
    deleted = b.delete_node(n.g1.id, include_descendants=True)
    assert set(deleted) == {n.g1.id, n.o1.id, n.o2.id}
    b2 = _reload(paths)
    for nid in (n.g1.id, n.o1.id, n.o2.id):
        assert b2.get(nid) is None
    assert b2.get(n.g2.id) is not None  # 兄弟不受影响
    assert n.g1.id not in b2.get(n.dom.id).children_ids


def test_root_cannot_be_deleted(tree):
    _, paths, b, n = tree
    with pytest.raises(ValueError):
        b.delete_node(n.top.id, include_descendants=True)
    assert _reload(paths).get(n.top.id) is not None


def test_missing_node_returns_empty(tree):
    _, _, b, _ = tree
    assert b.delete_node("nope", include_descendants=True) == []


def test_focus_ids_cleaned_of_deleted_nodes(tree):
    _, paths, b, n = tree
    dom = b.get(n.dom.id)
    dom.current_focus_ids = [n.g1.id, n.g2.id]
    b.save()
    b.delete_node(n.g1.id, include_descendants=True)
    b2 = _reload(paths)
    assert b2.get(n.dom.id).current_focus_ids == [n.g2.id]


def test_legacy_delete_goal_still_removes_goal_and_parent_reference(tree):
    _, paths, b, n = tree
    deleted = b.delete_goal(n.g1.id)
    assert set(deleted) == {n.g1.id, n.o1.id, n.o2.id}
    b2 = _reload(paths)
    assert n.g1.id not in b2.get(n.dom.id).children_ids


def test_legacy_delete_goal_ignores_non_goal(tree):
    _, _, b, n = tree
    assert b.delete_goal(n.o1.id) == []


# ── 路由层：409 / cron / 产出目录清理 ──────────────────────────────────

class _FakeCron:
    def __init__(self, jobs):
        self._jobs = jobs
        self.removed = []

    def list_jobs(self):
        return list(self._jobs)

    def remove_job(self, job_id):
        self.removed.append(job_id)
        self._jobs = [j for j in self._jobs if j.id != job_id]
        return True


@pytest.fixture
def client(tree, monkeypatch):
    root, paths, b, n = tree
    monkeypatch.setattr(routes, "_require_owner", lambda request: None)
    cron = _FakeCron([
        SimpleNamespace(id="job_g1", goal_id=n.g1.id),
        SimpleNamespace(id="job_o1", goal_id=n.o1.id),
        SimpleNamespace(id="job_g2", goal_id=n.g2.id),
    ])
    http = SimpleNamespace(
        bridge=SimpleNamespace(agent=SimpleNamespace(cfg=SimpleNamespace(project_root=root)), _cron_scheduler=cron),
        autonomous_loop=None,
    )
    app = FastAPI()
    app.include_router(router)
    app.state.http_server = http
    return TestClient(app), root, paths, n, cron


def test_route_self_delete_leaf_ok(client):
    c, root, paths, n, cron = client
    r = c.delete(f"/v1/goal_tree/nodes/{n.o1.id}", params={"include_children": "false"})
    assert r.status_code == 200
    assert r.json()["deleted_node_ids"] == [n.o1.id]
    assert "job_o1" in r.json()["removed_cron_job_ids"]


def test_route_self_delete_with_children_is_409_and_changes_nothing(client):
    c, root, paths, n, cron = client
    r = c.delete(f"/v1/goal_tree/nodes/{n.g1.id}", params={"include_children": "false"})
    assert r.status_code == 409
    assert _reload(paths).get(n.g1.id) is not None
    assert cron.removed == []


def test_route_subtree_delete_removes_cron_and_goal_artifacts(client):
    c, root, paths, n, cron = client
    art = root / ".agent" / "daemon_run_outputs" / "goals" / n.g1.id
    art.mkdir(parents=True)
    (art / "x.txt").write_text("x", encoding="utf-8")
    r = c.delete(f"/v1/goal_tree/nodes/{n.g1.id}", params={"include_children": "true"})
    assert r.status_code == 200
    body = r.json()
    assert set(body["deleted_node_ids"]) == {n.g1.id, n.o1.id, n.o2.id}
    assert set(body["removed_cron_job_ids"]) == {"job_g1", "job_o1"}
    assert "job_g2" not in cron.removed
    assert not art.exists()
    assert _reload(paths).get(n.g2.id) is not None


def test_route_root_is_400(client):
    c, root, paths, n, cron = client
    r = c.delete(f"/v1/goal_tree/nodes/{n.top.id}", params={"include_children": "true"})
    assert r.status_code == 400


def test_route_missing_is_404(client):
    c, *_ = client
    r = c.delete("/v1/goal_tree/nodes/nope", params={"include_children": "true"})
    assert r.status_code == 404


# ── 只删除子节点、保留自身 ──────────────────────────────────────────────

def test_delete_children_keeps_self_and_siblings(tree):
    _, paths, b, n = tree
    deleted = b.delete_children(n.g1.id)
    assert set(deleted) == {n.o1.id, n.o2.id}
    b2 = _reload(paths)
    assert b2.get(n.g1.id) is not None          # 自身保留
    assert b2.get(n.g1.id).children_ids == []   # 子节点清空
    assert b2.get(n.o1.id) is None and b2.get(n.o2.id) is None
    assert b2.get(n.g2.id) is not None          # 兄弟不受影响
    assert n.g1.id in b2.get(n.dom.id).children_ids  # 自身仍挂在父节点下


def test_delete_children_without_children_is_noop(tree):
    _, paths, b, n = tree
    assert b.delete_children(n.o1.id) == []
    assert _reload(paths).get(n.o1.id) is not None


def test_delete_children_allowed_on_root_but_root_kept(tree):
    _, paths, b, n = tree
    deleted = b.delete_children(n.top.id)
    assert set(deleted) == {n.dom.id, n.g1.id, n.o1.id, n.o2.id, n.g2.id}
    b2 = _reload(paths)
    assert b2.get(n.top.id) is not None
    assert b2.get(n.top.id).children_ids == []


def test_route_delete_children_keeps_self_cron_and_artifacts(client):
    c, root, paths, n, cron = client
    own = root / ".agent" / "daemon_run_outputs" / "goals" / n.g1.id
    own.mkdir(parents=True)
    (own / "keep.txt").write_text("keep", encoding="utf-8")
    r = c.delete(f"/v1/goal_tree/nodes/{n.g1.id}/children")
    assert r.status_code == 200
    body = r.json()
    assert body["kept_node"] is True
    assert set(body["deleted_node_ids"]) == {n.o1.id, n.o2.id}
    assert body["removed_cron_job_ids"] == ["job_o1"]
    assert "job_g1" not in cron.removed          # 自身的 cron 保留
    assert own.exists()                          # 自身的内部数据保留
    b2 = _reload(paths)
    assert b2.get(n.g1.id) is not None and b2.get(n.g1.id).children_ids == []
    assert b2.get(n.g2.id) is not None


def test_route_delete_children_cleans_goal_child_artifacts(client):
    c, root, paths, n, cron = client
    # 再给 g1 挂一个 goal 层级的子目标，它的内部数据应随子树清理
    b = _reload(paths)
    child_goal = b.add_node("goal", "子目标", parent_id=n.g1.id)
    art = root / ".agent" / "daemon_run_outputs" / "goals" / child_goal.id
    art.mkdir(parents=True)
    r = c.delete(f"/v1/goal_tree/nodes/{n.g1.id}/children")
    assert r.status_code == 200
    assert child_goal.id in r.json()["deleted_node_ids"]
    assert not art.exists()


def test_route_delete_children_no_children_is_404(client):
    c, root, paths, n, cron = client
    r = c.delete(f"/v1/goal_tree/nodes/{n.o1.id}/children")
    assert r.status_code == 404
