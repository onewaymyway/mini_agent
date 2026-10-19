"""产出项目管理 HTTP 路由测试（P7 阶段二）。

对应 next_doc/management_output_project_model_impl_p7.md 阶段二。

保证：
  1. 写操作（adopt / publish）请求体缺 confirm=true 时返回 400，且不产生任何写入；
  2. 只登记 / 受管接入 / 升级通过路由可用，结果与函数一致；
  3. 巡检 run 返回报告摘要；latest 在无报告时返回 report=null；
  4. candidates / projects 只读。

运行：PYTHONPATH=src python3 -m pytest tests/test_output_registry_routes.py -q
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


@pytest.fixture
def env(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "output_projects" / "legacy" / "output").mkdir(parents=True)
    (root / "output_projects" / "legacy" / "output" / "a.md").write_text("x", encoding="utf-8")
    (root / ".agent").mkdir()
    (root / ".agent" / "goals.json").write_text(json.dumps({"goals": []}), encoding="utf-8")
    cfg = load_config(config_file=root / "agent_config.json", project_root=root)
    app = FastAPI()
    app.include_router(router)
    app.state.http_server = SimpleNamespace(bridge=SimpleNamespace(agent=SimpleNamespace(cfg=cfg)))
    return root, TestClient(app)


def _owner_headers(monkeypatch):
    # 路由层的 _require_owner 在测试里放行
    import mini_agent.api.routes as routes
    monkeypatch.setattr(routes, "_require_owner", lambda request: None)


def test_candidates_and_projects_are_readonly_listings(env, monkeypatch):
    root, client = env
    _owner_headers(monkeypatch)
    r = client.get("/v1/output_registry/candidates")
    assert r.status_code == 200
    assert [c["slug"] for c in r.json()["candidates"]] == ["legacy"]
    assert client.get("/v1/output_registry/projects").json()["projects"] == []


def test_adopt_without_confirm_is_rejected_and_writes_nothing(env, monkeypatch):
    root, client = env
    _owner_headers(monkeypatch)
    r = client.post("/v1/output_registry/adopt", json={"slug": "legacy", "mode": "managed"})
    assert r.status_code == 400
    assert not (root / "output_projects" / "_registry").exists()
    assert not (root / "output_projects" / "legacy" / "PROJECT.json").exists()


def test_adopt_registered_then_upgrade_via_routes(env, monkeypatch):
    root, client = env
    _owner_headers(monkeypatch)
    r = client.post("/v1/output_registry/adopt",
                    json={"slug": "legacy", "mode": "registered", "confirm": True})
    assert r.status_code == 200
    assert r.json()["managed"] is False
    assert not (root / "output_projects" / "legacy" / "PROJECT.json").exists()

    projects = client.get("/v1/output_registry/projects").json()["projects"]
    assert [p["slug"] for p in projects] == ["legacy"]
    assert projects[0]["managed"] is False

    r = client.post("/v1/output_registry/adopt",
                    json={"slug": "legacy", "mode": "managed", "confirm": True})
    assert r.json()["upgraded"] is True
    assert (root / "output_projects" / "legacy" / "PROJECT.json").is_file()


def test_publish_requires_confirm_and_semver(env, monkeypatch):
    root, client = env
    _owner_headers(monkeypatch)
    client.post("/v1/output_registry/adopt", json={"slug": "legacy", "mode": "managed", "confirm": True})
    r = client.post("/v1/output_registry/publish", json={"slug": "legacy", "version": "0.1.0"})
    assert r.status_code == 400
    r = client.post("/v1/output_registry/publish",
                    json={"slug": "legacy", "version": "v0.1", "confirm": True})
    assert r.status_code == 400  # 非语义化版本被拒绝
    r = client.post("/v1/output_registry/publish",
                    json={"slug": "legacy", "version": "0.1.0", "confirm": True})
    assert r.status_code == 200
    assert r.json()["version"] == "0.1.0"


def test_patrol_run_and_latest(env, monkeypatch):
    root, client = env
    _owner_headers(monkeypatch)
    assert client.get("/v1/output_registry/patrol/latest").json() == {"report": None}
    r = client.post("/v1/output_registry/patrol/run")
    assert r.status_code == 200
    body = r.json()
    assert body["projects_scanned"] == 1
    assert body["thresholds"]["stale_days"] == 3.0
    kinds = {f["kind"] for f in body["findings"]}
    assert "unregistered" in kinds
    latest = client.get("/v1/output_registry/patrol/latest").json()["report"]
    assert latest["findings_total"] == body["findings_total"]
