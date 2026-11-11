"""扫描 external_projects/ 目录、列出未注册目录（看板"注册新项目"的选择入口）。

覆盖：registry.scan_unregistered 纯函数、GET /v1/external_projects/candidates 路由、
看板 UI（AppTest + 假 client）。
"""
import os
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.external_projects.registry import ExternalProjectRegistry, scan_unregistered

MANIFEST = 'name: {name}\nentrypoints:\n  scan:\n    cmd: "python -c \\"print(1)\\""\n'


def _mk(root: Path, dirname: str, manifest_name: str | None = None, raw: str | None = None):
    d = root / dirname
    d.mkdir(parents=True)
    if raw is not None:
        (d / "project.yaml").write_text(raw, encoding="utf-8")
    elif manifest_name is not None:
        (d / "project.yaml").write_text(MANIFEST.format(name=manifest_name), encoding="utf-8")
    return d


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "external_projects"
    root.mkdir()
    reg = ExternalProjectRegistry(tmp_path / "reg.json")
    return root, reg


def test_missing_scan_root_returns_empty(tmp_path):
    assert scan_unregistered(tmp_path / "nope", ExternalProjectRegistry(tmp_path / "r.json")) == []


def test_lists_only_unregistered_and_skips_hidden_and_files(env):
    root, reg = env
    done = _mk(root, "done_proj", "done_proj")
    _mk(root, "new_proj", "new_proj")
    _mk(root, ".hidden", "hidden")
    _mk(root, "_private", "priv")
    _mk(root, "__pycache__")
    (root / "README.md").write_text("x", encoding="utf-8")
    reg.register("done_proj", done)
    got = scan_unregistered(root, reg)
    assert [c["dir_name"] for c in got] == ["new_proj"]
    c = got[0]
    assert c["registrable"] and c["manifest_ok"] and c["suggested_name"] == "new_proj"
    assert Path(c["path"]).is_dir() and not c["name_conflict"]


def test_registered_by_path_even_if_renamed(env):
    root, reg = env
    d = _mk(root, "alpha", "alpha")
    reg.register("my_alias", d)          # 注册名与目录名不同
    assert scan_unregistered(root, reg) == []


def test_missing_and_invalid_manifest_are_listed_but_not_registrable(env):
    root, reg = env
    _mk(root, "no_manifest")
    _mk(root, "bad_manifest", raw="not: [valid")
    got = {c["dir_name"]: c for c in scan_unregistered(root, reg)}
    assert not got["no_manifest"]["registrable"] and "缺少 project.yaml" in got["no_manifest"]["manifest_error"]
    assert got["no_manifest"]["has_manifest"] is False
    assert not got["bad_manifest"]["registrable"] and got["bad_manifest"]["has_manifest"] is True
    assert got["bad_manifest"]["manifest_error"]
    assert got["bad_manifest"]["suggested_name"] == "bad_manifest"


def test_suggested_name_from_manifest_and_name_conflict(env, tmp_path):
    root, reg = env
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "project.yaml").write_text(MANIFEST.format(name="dup"), encoding="utf-8")
    reg.register("dup", other)           # 名字被另一个路径占用
    _mk(root, "dir_a", "dup")            # 目录名 dir_a，manifest.name=dup
    c = scan_unregistered(root, reg)[0]
    assert c["suggested_name"] == "dup" and c["name_conflict"] is True


def test_scan_is_read_only(env):
    root, reg = env
    _mk(root, "p", "p")
    before = sorted(str(p) for p in root.rglob("*"))
    scan_unregistered(root, reg)
    assert sorted(str(p) for p in root.rglob("*")) == before
    assert reg.list() == []


# ── 路由 ────────────────────────────────────────────────────────────────

def _client():
    app = FastAPI()
    app.include_router(router)
    app.state.http_server = None
    return TestClient(app)


@pytest.fixture
def route_env(tmp_path, monkeypatch):
    import mini_agent.external_projects.registry as registry_mod

    monkeypatch.setattr(registry_mod, "DEFAULT_REGISTRY_PATH", tmp_path / "reg.json")
    proj_root = tmp_path / "main"
    (proj_root / "external_projects").mkdir(parents=True)
    monkeypatch.chdir(proj_root)
    return proj_root / "external_projects"


def test_route_lists_candidates_and_register_removes_them(route_env):
    _mk(route_env, "stock_x", "stock_x")
    _mk(route_env, "broken")
    c = _client()
    r = c.get("/v1/external_projects/candidates")
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is True
    by = {x["dir_name"]: x for x in body["candidates"]}
    assert by["stock_x"]["registrable"] and not by["broken"]["registrable"]
    # 用候选里的 path 走既有注册接口，注册后不再出现在候选里
    rr = c.post("/v1/external_projects/register", json={"path": by["stock_x"]["path"], "name": "stock_x"})
    assert rr.status_code == 200
    again = {x["dir_name"] for x in c.get("/v1/external_projects/candidates").json()["candidates"]}
    assert again == {"broken"}


def test_route_when_directory_missing(tmp_path, monkeypatch):
    import mini_agent.external_projects.registry as registry_mod

    monkeypatch.setattr(registry_mod, "DEFAULT_REGISTRY_PATH", tmp_path / "reg.json")
    monkeypatch.chdir(tmp_path)
    body = _client().get("/v1/external_projects/candidates").json()
    assert body["exists"] is False and body["candidates"] == []


# ── 看板 UI ─────────────────────────────────────────────────────────────

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_DIR = str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban")

UI_SCRIPT = '''
import sys
sys.path.insert(0, {app_dir!r})
import app

RESULT = {result!r}


class FakeClient:
    registered = []

    def external_project_candidates(self):
        return RESULT

    def register_external_project(self, path, name="", validate=True):
        app.st.session_state["registered_calls"] = app.st.session_state.get("registered_calls", []) + [(path, name)]
        return {{"project": {{"name": name}}}}

    def external_projects_status(self):
        return {{"projects": []}}


app.render_external_projects_tab(FakeClient())
'''

CANDS = {"scan_root": "/m/external_projects", "exists": True, "candidates": [
    {"dir_name": "good", "path": "/m/external_projects/good", "has_manifest": True, "manifest_ok": True,
     "manifest_error": "", "suggested_name": "good_name", "name_conflict": False, "registrable": True},
    {"dir_name": "bad", "path": "/m/external_projects/bad", "has_manifest": False, "manifest_ok": False,
     "manifest_error": "缺少 project.yaml", "suggested_name": "bad", "name_conflict": False,
     "registrable": False},
    {"dir_name": "clash", "path": "/m/external_projects/clash", "has_manifest": True, "manifest_ok": True,
     "manifest_error": "", "suggested_name": "taken", "name_conflict": True, "registrable": True},
]}


def _ui(result):
    return AppTest.from_string(UI_SCRIPT.format(app_dir=APP_DIR, result=result), default_timeout=30).run()


def _btn(at, key):
    b = [x for x in at.button if x.key == key]
    assert b, [x.key for x in at.button]
    return b[0]


def test_ui_nothing_listed_before_scan():
    at = _ui(CANDS)
    assert not at.exception
    assert not [b for b in at.button if b.key.startswith("ext_scan_reg_")]


def test_ui_scan_lists_candidates_and_register_uses_edited_name():
    at = _ui(CANDS)
    _btn(at, "ext_scan_btn").click().run()
    assert not at.exception
    md = " ".join(m.value for m in at.markdown)
    assert "good" in md and "bad" in md
    assert any("缺少 project.yaml" in w.value for w in at.warning)          # 不可注册项给出原因
    assert any("已被另一个路径占用" in w.value for w in at.warning)
    assert not [b for b in at.button if b.key == "ext_scan_reg_bad"]        # 不可注册项没有注册按钮
    ti = [t for t in at.text_input if t.key == "ext_scan_name_good"][0]
    assert ti.value == "good_name"
    ti.set_value("renamed").run()
    _btn(at, "ext_scan_reg_good").click().run()
    assert not at.exception
    assert at.session_state["registered_calls"] == [("/m/external_projects/good", "renamed")]
    assert "ext_scan_result" not in at.session_state      # 注册后清掉扫描结果，下次扫描会重新拉取


def test_ui_all_registered_and_missing_dir_messages():
    at = _ui({"scan_root": "/m/external_projects", "exists": True, "candidates": []})
    _btn(at, "ext_scan_btn").click().run()
    assert any("都已注册" in s.value for s in at.success)
    at = _ui({"scan_root": "/m/external_projects", "exists": False, "candidates": []})
    _btn(at, "ext_scan_btn").click().run()
    assert any("未找到目录" in i.value for i in at.info)


def test_ui_scan_error_is_shown():
    at = _ui({"_error": "boom"})
    _btn(at, "ext_scan_btn").click().run()
    assert any("扫描失败" in e.value for e in at.error)
