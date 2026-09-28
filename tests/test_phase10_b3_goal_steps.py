"""Phase 10 B3：Objective 改称 Goal 步骤（/v1/objectives/* → /v1/goal_steps/*）+ GET /v1/goals/{id}/steps。

验收：新路径可用；旧路径仍可用（隐藏别名）；OpenAPI 只暴露新路径；
新只读端点返回 A5 投影；调用方已切到新名。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api import routes as routes_mod
from mini_agent.core import reset_event_bus, reset_objective_projection_cache
from mini_agent.evolution.objective_executor import ObjectiveExecutor
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths

ROOT = Path(__file__).resolve().parents[1]


class _FakeOE:
    """只记录调用，验证新旧路径打到同一个处理函数。"""

    def __init__(self):
        self.calls = []

    def cancel(self, eid):
        self.calls.append(("cancel", eid))
        return True

    def request_pause(self, eid):
        self.calls.append(("pause", eid))
        return True

    def resume_user_pause(self, eid):
        self.calls.append(("resume", eid))
        return True

    def retry_current_step(self, eid):
        self.calls.append(("retry", eid))
        return True

    def reset_step(self, eid, idx, reason):
        self.calls.append(("reset", eid, idx))
        return True

    def inject_guidance(self, eid, msg):
        self.calls.append(("guidance", eid, msg))
        return True


@pytest.fixture(autouse=True)
def _reset():
    reset_event_bus()
    reset_objective_projection_cache()
    yield
    reset_event_bus()
    reset_objective_projection_cache()


def _app(monkeypatch, oe, tmp_path) -> TestClient:
    monkeypatch.setattr(routes_mod, "_require_owner", lambda request: None)
    monkeypatch.setattr(routes_mod, "_get_paths_for_request", lambda request: AgentPaths(Path(tmp_path)))
    app = FastAPI()
    app.include_router(routes_mod.router)
    app.state.http_server = SimpleNamespace(
        autonomous_loop=SimpleNamespace(_objective_executor=oe), bridge=SimpleNamespace(),
    )
    return TestClient(app)


OPS = [
    ("post", "cancel", "cancel"), ("post", "pause", "pause"), ("post", "resume", "resume"),
    ("post", "retry", "retry"),
]


@pytest.mark.parametrize("verb,op,name", OPS)
def test_new_and_legacy_path_hit_same_handler(monkeypatch, tmp_path, verb, op, name):
    oe = _FakeOE()
    c = _app(monkeypatch, oe, tmp_path)
    assert getattr(c, verb)(f"/v1/goal_steps/e1/{op}").json() == {"ok": True}
    assert getattr(c, verb)(f"/v1/objectives/e1/{op}").json() == {"ok": True}
    assert oe.calls == [(name, "e1"), (name, "e1")]


def test_step_reset_and_guidance_both_names(monkeypatch, tmp_path):
    oe = _FakeOE()
    c = _app(monkeypatch, oe, tmp_path)
    for base in ("goal_steps", "objectives"):
        assert c.post(f"/v1/{base}/e1/steps/2/reset", json={}).status_code == 200
        assert c.post(f"/v1/{base}/e1/guidance", json={"message": "hi"}).status_code == 200
    assert [x[0] for x in oe.calls] == ["reset", "guidance", "reset", "guidance"]


def test_openapi_exposes_only_new_names(monkeypatch, tmp_path):
    c = _app(monkeypatch, _FakeOE(), tmp_path)
    paths = c.get("/openapi.json").json()["paths"]
    assert "/v1/goal_steps/{execution_id}/cancel" in paths
    assert "/v1/goals/{goal_id}/steps" in paths
    assert not any(p.startswith("/v1/objectives") for p in paths)


def test_every_legacy_route_has_a_new_twin():
    r = routes_mod.router.routes
    legacy = {(x.path, m) for x in r if x.path.startswith("/v1/objectives/") for m in x.methods}
    new = {(x.path, m) for x in r if x.path.startswith("/v1/goal_steps/") for m in x.methods}
    assert legacy and {(p.replace("/v1/objectives/", "/v1/goal_steps/", 1), m) for p, m in legacy} == new
    assert all(not x.include_in_schema for x in r if x.path.startswith("/v1/objectives/"))
    assert all(x.include_in_schema for x in r if x.path.startswith("/v1/goal_steps/"))


def test_trend_route_new_and_legacy_registered():
    paths = {x.path for x in routes_mod.router.routes}
    assert "/v1/goal_steps/completion_trend" in paths and "/v1/objectives/completion_trend" in paths


# ── GET /v1/goals/{goal_id}/steps ──────────────────────────────────────

@pytest.fixture
def real(tmp_path):
    paths = AgentPaths(Path(tmp_path))
    backlog = GoalBacklog(paths)
    n = {"i": 0}

    def submit(msg, initiator, meta):
        n["i"] += 1
        return f"turn_{n['i']}"

    oe = ObjectiveExecutor(paths=paths, submit_fn=submit,
                           llm_decompose_fn=lambda o: ["步骤A", "步骤B"], goal_backlog=backlog)
    goal = backlog.add_goal(title="G", priority=50)
    obj = backlog.add_objective(title="O", parent_id=goal.id, priority=60)
    other = backlog.add_goal(title="G2", priority=10)
    obj2 = backlog.add_objective(title="O2", parent_id=other.id, priority=10)
    oe.start(obj)
    oe.start(obj2)  # 路径冲突时会排队（blocked）并返回 None，但执行记录已创建
    e1 = oe.find_running_execution_by_objective(obj.id)
    e2 = oe.find_running_execution_by_objective(obj2.id)
    assert e1 and e2
    return oe, backlog, goal, obj, other, e1, e2


def test_goal_steps_lists_only_that_goals_executions(monkeypatch, tmp_path, real):
    oe, backlog, goal, obj, other, e1, e2 = real
    c = _app(monkeypatch, oe, tmp_path)
    body = c.get(f"/v1/goals/{goal.id}/steps").json()
    assert [e["execution_id"] for e in body["executions"]] == [e1]
    ex = body["executions"][0]
    assert ex["objective_id"] == obj.id
    assert [s["description"] for s in ex["steps"]] == ["步骤A", "步骤B"]
    assert ex["goal_state"]["goal_text"] == "O" and ex["goal_state"]["status"] == "running"
    assert ex["goal_state"]["evidence"]["parent_goal_id"] == goal.id


def test_goal_steps_accepts_objective_node_id(monkeypatch, tmp_path, real):
    oe, backlog, goal, obj, other, e1, e2 = real
    c = _app(monkeypatch, oe, tmp_path)
    body = c.get(f"/v1/goals/{obj.id}/steps").json()
    assert [e["execution_id"] for e in body["executions"]] == [e1]


def test_goal_steps_unknown_goal_404(monkeypatch, tmp_path, real):
    c = _app(monkeypatch, real[0], tmp_path)
    assert c.get("/v1/goals/nope/steps").status_code == 404


def test_goal_steps_is_read_only_and_publishes_projection_event(monkeypatch, tmp_path, real):
    from mini_agent.core import get_event_bus
    oe, backlog, goal, obj, other, e1, e2 = real
    got = []
    get_event_bus().subscribe("ObjectiveProjected", got.append)
    before = oe.get_execution(e1).to_dict()
    c = _app(monkeypatch, oe, tmp_path)
    c.get(f"/v1/goals/{goal.id}/steps")
    c.get(f"/v1/goals/{goal.id}/steps")
    assert oe.get_execution(e1).to_dict() == before
    # 两个 execution 都被投影，但重复读取不重复发事件
    assert len(got) == 2


def test_goal_steps_503_without_executor(monkeypatch, tmp_path):
    c = _app(monkeypatch, None, tmp_path)
    c.app.state.http_server.bridge = SimpleNamespace(_objective_executor=None)
    assert c.get("/v1/goals/x/steps").status_code == 503


# ── 调用方已切换 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "apps/mini_agent_kanban/client.py",
    "apps/mini_agent_kanban_x/src/api/endpoints.ts",
    "src/mini_agent/cli/commands/goals.py",
])
def test_callers_use_new_name(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert "objectives/" not in text.replace("goal_objectives/", "")
    assert "goal_steps/" in text
