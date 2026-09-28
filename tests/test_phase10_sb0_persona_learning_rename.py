"""Phase 10 S-B0：人设学习改名（/capability → /persona-learning）的测试。

验收（见 next_doc/refactor_plan/13-phase10-post-decision-execution-plan.md）：
新路径可用；旧路径仍可用；OpenAPI 只暴露新路径；CLI 新旧名都能分发；
定时任务提示词使用新名。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.capability_routes import mount_persona_learning_routers


def _client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    mount_persona_learning_routers(app)
    app.state.bridge = SimpleNamespace(agent=SimpleNamespace(cfg=SimpleNamespace(project_root=str(tmp_path))))
    return TestClient(app)


def test_new_and_legacy_paths_share_behavior(tmp_path):
    c = _client(tmp_path)
    assert c.get("/v1/persona_learning/tracks").json() == {"tracks": []}
    assert c.get("/v1/capability/tracks").json() == {"tracks": []}
    # 经新路径创建，旧路径可见（同一份数据）
    tid = c.post("/v1/persona_learning/tracks", json={"title": "T", "persona_desc": "d"}).json()["track_id"]
    assert c.get(f"/v1/capability/tracks/{tid}").json()["title"] == "T"


def test_openapi_only_exposes_new_paths(tmp_path):
    paths = _client(tmp_path).get("/openapi.json").json()["paths"]
    assert any(p.startswith("/v1/persona_learning/") for p in paths)
    assert not any(p.startswith("/v1/capability") for p in paths)


def test_persona_candidates_new_and_legacy(tmp_path):
    c = _client(tmp_path)
    assert c.get("/v1/persona_learning/persona_candidates").status_code == 200
    assert c.get("/v1/capability/persona_candidates").status_code == 200


def test_alias_covers_every_primary_route():
    from mini_agent.api.capability_routes import (
        LEGACY_CAPABILITY_PREFIX, PERSONA_LEARNING_PREFIX, capability_router, make_legacy_alias_router,
    )
    from mini_agent.api.persona_candidate_routes import persona_candidate_router

    for primary, new_p, old_p in (
        (capability_router, PERSONA_LEARNING_PREFIX, LEGACY_CAPABILITY_PREFIX),
        (persona_candidate_router, PERSONA_LEARNING_PREFIX + "/persona_candidates",
         LEGACY_CAPABILITY_PREFIX + "/persona_candidates"),
    ):
        alias = make_legacy_alias_router(primary, new_p, old_p)
        want = {(r.path.replace(new_p, old_p, 1), m) for r in primary.routes for m in r.methods}
        got = {(r.path, m) for r in alias.routes for m in r.methods}
        assert want == got and want
        assert all(r.include_in_schema is False for r in alias.routes)


def test_routes_py_adopt_goal_has_new_and_legacy_paths():
    src = Path("src/mini_agent/api/routes.py").read_text(encoding="utf-8")
    assert '@router.post("/persona_learning/tracks/{track_id}/topics/{topic_id}/adopt_goal")' in src
    assert '"/capability/tracks/{track_id}/topics/{topic_id}/adopt_goal", include_in_schema=False' in src


def test_repl_dispatches_new_and_legacy_names_and_hints_on_legacy(monkeypatch):
    import mini_agent.cli.repl as repl

    calls, infos = [], []
    monkeypatch.setattr(repl, "handle_capability_cmd", lambda args, agent: calls.append(args))
    monkeypatch.setattr(repl.R, "print_info", lambda m, *a, **k: infos.append(m))
    agent = SimpleNamespace(cfg=SimpleNamespace(), skill_loader=None)
    for cmd in ("/persona-learning list", "/persona_learning list", "/capability list"):
        try:
            repl._handle_slash(cmd, agent, None)
        except Exception:
            pass
    assert len(calls) == 3
    assert sum("已更名为 /persona-learning" in m for m in infos) == 1  # 只有旧名提示


def test_cron_bridge_prompt_uses_new_name():
    import mini_agent.evolution.cron_agent_bridge as b

    src = inspect.getsource(b)
    assert "/persona-learning cycle" in src and "/capability cycle" not in src


def test_help_text_uses_new_name_only():
    src = Path("src/mini_agent/cli/parser.py").read_text(encoding="utf-8")
    assert "/persona-learning" in src
    assert "/capability" not in src.replace("formerly /capability", "")
