"""tests/test_service_anatomy_api.py — 第二十四轮 A7：档案 / 证据 / 预测 / 简报 的服务接口（tool_api + HTTP + CLI）。

用真实落盘的实例（不 mock 业务），验证三层外壳行为一致、只读不写盘、错误类型正确。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_forecast_brief as tfb  # noqa: E402
import test_html_export_anatomy as the  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from world_simulator import service_cli, tool_api  # noqa: E402
from world_simulator.server import app  # noqa: E402

SIM = the.SIM
FAST = {"runs": 120, "seed": 5, "horizon_days": 3650, "time_budget_sec": 0}


@pytest.fixture()
def sim(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_api, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(tool_api, "_cfg_cache", None)
    store = the._seed(tmp_path, the._settings())
    return tmp_path, store


@pytest.fixture()
def client():
    return TestClient(app)


def _err(result, kind):
    assert result["ok"] is False and result["error"]["type"] == kind and result["error"]["message"]


def _files(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# ── tool_api ─────────────────────────────────────────────────────────


def test_get_anatomy_profile(sim):
    r = tool_api.get_anatomy_profile(SIM, "el")
    assert r["ok"] and r["data"]["profile"]["has_anatomy"] and r["data"]["profile"]["label"] == "固态电池"
    assert r["data"]["branch"] == "main" and "progress" in r["data"]
    _err(tool_api.get_anatomy_profile(SIM, "nope"), "validation_error")
    _err(tool_api.get_anatomy_profile(SIM, "  "), "validation_error")
    _err(tool_api.get_anatomy_profile("", "el"), "validation_error")
    _err(tool_api.get_anatomy_profile("missing_sim", "el"), "not_found")


def test_profile_when_anatomy_switch_off_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_api, "_DATA_DIR", tmp_path)
    s = the._settings()
    s["anatomy_enabled"] = False
    the._seed(tmp_path, s)
    r = tool_api.get_anatomy_profile(SIM, "el")
    assert r["ok"] and r["data"]["profile"] == {"enabled": False} and "progress" not in r["data"]


def test_get_evidence_all_and_per_element(sim):
    _, store = sim
    store.append_evidence([
        tfb.evm.normalize_record({"ev_id": "ev_0001", "element_id": "el", "claim": "c1", "source_url": "https://example.org/a", "retrieved_at": "2026-09-01T00:00:00+00:00"}),
    ])
    allr = tool_api.get_evidence(SIM)
    assert allr["ok"] and allr["data"]["total"] == 1 and allr["data"]["by_element"] == {"el": 1}
    one = tool_api.get_evidence(SIM, "el")
    assert one["ok"] and one["data"]["element"] == "el" and len(one["data"]["evidence"]["items"]) == 1
    _err(tool_api.get_evidence("missing_sim"), "not_found")
    _err(tool_api.get_evidence(""), "validation_error")


def test_run_forecast(sim):
    r = tool_api.run_forecast(SIM, **FAST)
    assert r["ok"] and r["data"]["forecast"]["ok"] and r["data"]["forecast"]["meta"]["runs_done"] == 120
    assert isinstance(r["data"]["watch"], list) and r["data"]["watch"]
    again = tool_api.run_forecast(SIM, **FAST)
    assert again["data"]["forecast"]["milestones"] == r["data"]["forecast"]["milestones"]  # 固定种子可复现
    pt = tool_api.run_forecast(SIM, point=True, **FAST)
    assert pt["ok"]
    one = tool_api.run_forecast(SIM, element="el", **FAST)
    assert one["ok"]
    _err(tool_api.run_forecast(SIM, element="nope", **FAST), "validation_error")
    _err(tool_api.run_forecast("missing_sim", **FAST), "not_found")
    _err(tool_api.run_forecast("", **FAST), "validation_error")
    json.dumps(r)


def test_run_forecast_without_anatomy_is_validation_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_api, "_DATA_DIR", tmp_path)
    the._seed(tmp_path, {})
    _err(tool_api.run_forecast(SIM, **FAST), "validation_error")
    _err(tool_api.get_forecast_brief(SIM, **FAST), "validation_error")


def test_run_what_if(sim):
    ok = tool_api.run_what_if(SIM, [{"kind": "assumption", "element": "el", "id": "a1", "p_true": 0}], **FAST)
    assert ok["ok"] and ok["data"]["applied"] and "diff" in ok["data"]
    bad = tool_api.run_what_if(SIM, [{"kind": "assumption", "element": "el", "id": "zzz", "p_true": 0}], **FAST)
    _err(bad, "validation_error")
    assert "没有任何有效的编辑" in bad["error"]["message"] and "zzz" in bad["error"]["message"]  # 带上每条无效原因
    _err(tool_api.run_what_if(SIM, [], **FAST), "validation_error")
    _err(tool_api.run_what_if(SIM, "x", **FAST), "validation_error")
    _err(tool_api.run_what_if(SIM, [1], **FAST), "validation_error")
    _err(tool_api.run_what_if("missing_sim", [{"kind": "assumption"}], **FAST), "not_found")


def test_get_forecast_brief(sim):
    r = tool_api.get_forecast_brief(SIM, runs=120, seed=5, horizon_days=3650, time_budget_sec=0)
    assert r["ok"] and r["data"]["brief"]["ok"] and r["data"]["brief"]["overall"]["level"] in ("high", "medium", "low")
    assert r["data"]["brief"]["elements"][0]["evidence"] is not None  # 服务层会带上证据库
    fast = tool_api.get_forecast_brief(SIM, with_sensitivity=False, **FAST)
    assert fast["ok"] and fast["data"]["brief"]["meta"]["sensitivity"] is False
    sel = tool_api.get_forecast_brief(SIM, elements=["el"], with_sensitivity=False, **FAST)
    assert sel["ok"] and sel["data"]["brief"]["meta"]["elements"] == ["el"]
    _err(tool_api.get_forecast_brief(SIM, elements=["nope"], with_sensitivity=False, **FAST), "validation_error")
    _err(tool_api.get_forecast_brief(SIM, elements="el", **FAST), "validation_error")
    _err(tool_api.get_forecast_brief(SIM, elements=[1], **FAST), "validation_error")
    _err(tool_api.get_forecast_brief("missing_sim", **FAST), "not_found")


def test_read_and_forecast_apis_never_write_to_disk(sim):
    tmp, _ = sim
    before = _files(tmp)
    tool_api.get_anatomy_profile(SIM, "el")
    tool_api.get_evidence(SIM)
    tool_api.run_forecast(SIM, **FAST)
    tool_api.run_what_if(SIM, [{"kind": "assumption", "element": "el", "id": "a1", "p_true": 1}], **FAST)
    tool_api.get_forecast_brief(SIM, with_sensitivity=False, **FAST)
    assert _files(tmp) == before


def test_export_html_options(sim):
    full = tool_api.export_html(SIM, forecast_runs=100, forecast_budget_sec=0)
    assert full["ok"] and "🔮 预测简报" in full["data"]["html"] and "🧬 固态电池" in full["data"]["html"]
    lean = tool_api.export_html(SIM, with_forecast=False)
    assert lean["ok"] and "🔮 预测简报" not in lean["data"]["html"] and "🧬 固态电池" in lean["data"]["html"]


# ── HTTP ─────────────────────────────────────────────────────────────


def test_http_routes(sim, client):
    r = client.get(f"/simulations/{SIM}/elements/el/anatomy")
    assert r.status_code == 200 and r.json()["data"]["profile"]["has_anatomy"]
    assert client.get(f"/simulations/{SIM}/elements/nope/anatomy").status_code == 400
    assert client.get("/simulations/missing_sim/elements/el/anatomy").status_code == 404
    assert client.get(f"/simulations/{SIM}/evidence").json()["data"]["total"] == 0
    assert client.get(f"/simulations/{SIM}/evidence", params={"element": "el"}).json()["data"]["element"] == "el"

    f = client.post(f"/simulations/{SIM}/forecast", json=FAST)
    assert f.status_code == 200 and f.json()["data"]["forecast"]["meta"]["runs_done"] == 120
    assert client.post(f"/simulations/{SIM}/forecast", json={**FAST, "element": "nope"}).status_code == 400

    w = client.post(f"/simulations/{SIM}/forecast/what-if", json={**FAST, "edits": [{"kind": "assumption", "element": "el", "id": "a1", "p_true": 0}]})
    assert w.status_code == 200 and w.json()["data"]["applied"]
    assert client.post(f"/simulations/{SIM}/forecast/what-if", json={**FAST, "edits": []}).status_code == 400
    assert client.post(f"/simulations/{SIM}/forecast/what-if", json=FAST).status_code == 422  # edits 必填

    b = client.post(f"/simulations/{SIM}/forecast/brief", json={**FAST, "with_sensitivity": False})
    assert b.status_code == 200 and b.json()["data"]["brief"]["ok"]
    assert client.post("/simulations/missing_sim/forecast/brief", json=FAST).status_code == 404


def test_http_export_passes_options_only_when_non_default(client, monkeypatch):
    seen = []
    monkeypatch.setattr(tool_api, "export_html", lambda sim_id, branch="main", **kw: seen.append(kw) or {"ok": True, "data": {"html": "<html/>"}})
    assert client.get("/simulations/x/export.html").status_code == 200
    assert client.get("/simulations/x/export.html", params={"with_forecast": "false", "forecast_runs": 50, "forecast_budget_sec": 5}).status_code == 200
    assert seen == [{}, {"with_forecast": False, "forecast_runs": 50, "forecast_budget_sec": 5.0}]


# ── CLI ──────────────────────────────────────────────────────────────


def _cli(capsys, *argv):
    code = service_cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_cli_subcommands(sim, capsys):
    code, out = _cli(capsys, "get-anatomy", "--sim-id", SIM, "--element", "el")
    assert code == 0 and out["data"]["profile"]["has_anatomy"]
    code, out = _cli(capsys, "get-anatomy", "--sim-id", SIM, "--element", "nope")
    assert code == 1 and out["error"]["type"] == "validation_error"
    code, out = _cli(capsys, "get-evidence", "--sim-id", SIM)
    assert code == 0 and out["data"]["total"] == 0
    common = ["--runs", "120", "--seed", "5", "--horizon-days", "3650", "--time-budget-sec", "0"]
    code, out = _cli(capsys, "forecast", "--sim-id", SIM, "--point", *common)
    assert code == 0 and out["data"]["forecast"]["ok"]
    edits = json.dumps([{"kind": "assumption", "element": "el", "id": "a1", "p_true": 0}])
    code, out = _cli(capsys, "what-if", "--sim-id", SIM, "--edits", edits, *common)
    assert code == 0 and out["data"]["applied"]
    code, out = _cli(capsys, "forecast-brief", "--sim-id", SIM, "--element", "el", "--no-sensitivity", *common)
    assert code == 0 and out["data"]["brief"]["meta"]["elements"] == ["el"] and out["data"]["brief"]["meta"]["sensitivity"] is False


def test_cli_what_if_rejects_bad_json(sim, capsys):
    with pytest.raises(SystemExit):
        service_cli.main(["what-if", "--sim-id", SIM, "--edits", "{not json"])
    assert '"ok": false' in capsys.readouterr().out.lower()


def test_cli_export_html_flags(sim, capsys, tmp_path):
    out_file = tmp_path / "o.html"
    code, out = _cli(capsys, "export-html", "--sim-id", SIM, "--no-forecast", "--output", str(out_file))
    assert code == 0 and out["data"]["output_file"] == str(out_file)
    text = out_file.read_text(encoding="utf-8")
    assert "🧬 固态电池" in text and "🔮 预测简报" not in text
    code, _ = _cli(capsys, "export-html", "--sim-id", SIM, "--forecast-runs", "100", "--forecast-budget-sec", "0", "--output", str(out_file))
    assert code == 0 and "🔮 预测简报" in out_file.read_text(encoding="utf-8")
