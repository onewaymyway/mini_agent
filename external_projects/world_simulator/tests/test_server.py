"""tests/test_server.py — `world_simulator/server.py` 单测。

`fastapi`/`uvicorn` 是可选依赖（见 `server.py` 顶部的 docstring），
本机没装时整个文件用 `pytest.importorskip` 跳过，不让"要不要跑 HTTP
服务"这个可选能力拖累主测试套件必须装这两个包才能跑通。

只测 HTTP 适配层自己的职责（路由 → `tool_api.py` 参数传递、
`ok=False` 时的状态码映射），全部对 `tool_api.py` 的函数用
monkeypatch 换成固定返回值，不重复测业务逻辑本身（`test_tool_api.py`
已覆盖）。
"""

from __future__ import annotations

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient

from world_simulator import tool_api  # noqa: E402
from world_simulator.server import app  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(app)


def test_health_does_not_touch_tool_api(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "up"


def test_list_simulations_ok(client, monkeypatch):
    monkeypatch.setattr(tool_api, "list_simulations", lambda: {"ok": True, "data": {"simulations": []}})
    resp = client.get("/simulations")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "data": {"simulations": []}}


def test_get_simulation_not_found_returns_404(client, monkeypatch):
    monkeypatch.setattr(
        tool_api, "get_simulation",
        lambda sim_id, **kw: {"ok": False, "error": {"type": "not_found", "message": "没有这个实例"}},
    )
    resp = client.get("/simulations/nope")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["type"] == "not_found"


def test_create_simulation_validation_error_returns_400(client, monkeypatch):
    monkeypatch.setattr(
        tool_api, "create_simulation",
        lambda **kw: {"ok": False, "error": {"type": "validation_error", "message": "intent 不能为空"}},
    )
    resp = client.post("/simulations", json={"intent": ""})
    assert resp.status_code == 400


def test_create_simulation_engine_error_returns_409(client, monkeypatch):
    monkeypatch.setattr(
        tool_api, "create_simulation",
        lambda **kw: {"ok": False, "error": {"type": "engine_error", "message": "boom"}},
    )
    resp = client.post("/simulations", json={"intent": "一个意图"})
    assert resp.status_code == 409


def test_create_simulation_success_passes_body_fields_through(client, monkeypatch):
    captured = {}

    def _fake_create(*, template, intent, settings):
        captured["template"] = template
        captured["intent"] = intent
        captured["settings"] = settings
        return {"ok": True, "data": {"sim_id": "sim_x"}}

    monkeypatch.setattr(tool_api, "create_simulation", _fake_create)
    resp = client.post("/simulations", json={
        "template": "group_evolution", "intent": "一个意图", "settings": {"options_count": 2},
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["sim_id"] == "sim_x"
    assert captured == {
        "template": "group_evolution", "intent": "一个意图", "settings": {"options_count": 2},
    }


def test_advance_simulation_passes_body_fields_through(client, monkeypatch):
    captured = {}

    def _fake_advance(sim_id, **kwargs):
        captured["sim_id"] = sim_id
        captured["kwargs"] = kwargs
        return {"ok": True, "data": {"step": 1}}

    monkeypatch.setattr(tool_api, "advance_simulation", _fake_advance)
    resp = client.post("/simulations/sim_x/advance", json={"choice_option_id": "opt_a"})
    assert resp.status_code == 200
    assert captured["sim_id"] == "sim_x"
    assert captured["kwargs"]["choice_option_id"] == "opt_a"
    assert captured["kwargs"]["chosen_by"] == "agent"  # 请求体未传时走模型默认值


def test_delete_simulation_success(client, monkeypatch):
    monkeypatch.setattr(
        tool_api, "delete_simulation",
        lambda sim_id: {"ok": True, "data": {"sim_id": sim_id, "deleted": True}},
    )
    resp = client.delete("/simulations/sim_x")
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] is True


def test_export_html_returns_raw_html_content_type(client, monkeypatch):
    monkeypatch.setattr(
        tool_api, "export_html",
        lambda sim_id, branch="main": {"ok": True, "data": {"html": "<html>hi</html>"}},
    )
    resp = client.get("/simulations/sim_x/export.html")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert resp.text == "<html>hi</html>"


def test_export_html_not_found_returns_404(client, monkeypatch):
    monkeypatch.setattr(
        tool_api, "export_html",
        lambda sim_id, branch="main": {"ok": False, "error": {"type": "not_found", "message": "没有这个实例"}},
    )
    resp = client.get("/simulations/sim_x/export.html")
    assert resp.status_code == 404
