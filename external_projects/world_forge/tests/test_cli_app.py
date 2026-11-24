import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(*args, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([sys.executable, str(ROOT / "entrypoints" / "cli.py"), *args],
                          capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)


def test_cli_health_and_init_and_list(tmp_path):
    env = {"WORLD_FORGE_DATA_DIR": str(tmp_path / "d"), "WORLD_FORGE_PROVIDERS": "stub"}
    h = _run("health", env_extra=env)
    assert h.returncode == 0, h.stderr
    assert json.loads(h.stdout)["ok"] is True
    r = _run("init", "--concept", "测试世界", env_extra=env)
    assert r.returncode == 0, r.stderr
    wid = json.loads(r.stdout)["world_id"]
    lst = json.loads(_run("list", env_extra=env).stdout)
    assert any(w["id"] == wid for w in lst)


def test_cli_unknown_world_exits_nonzero(tmp_path):
    env = {"WORLD_FORGE_DATA_DIR": str(tmp_path / "d"), "WORLD_FORGE_PROVIDERS": "stub"}
    r = _run("advance", "--world", "wld_nope", env_extra=env)
    assert r.returncode == 1
    assert json.loads(r.stdout)["ok"] is False


def test_streamlit_app_smoke(tmp_path, monkeypatch, world):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("WORLD_FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WORLD_FORGE_PROVIDERS", "stub")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception, at.exception
    assert len(at.tabs) == 8


def test_create_world_from_form_then_rerun_no_session_state_error(tmp_path, monkeypatch):
    """回归：新建世界后切换当前世界不应触发 session_state 修改异常。"""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("WORLD_FORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WORLD_FORGE_PROVIDERS", "stub")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception
    at.text_area[0].set_value("回归测试概念")
    at.button[0].click().run()
    assert not at.exception, at.exception
    assert len(repo_list(tmp_path)) == 1


def repo_list(tmp_path):
    from pathlib import Path as _P
    return [p for p in (_P(tmp_path) / "data").iterdir() if (p / "world.json").is_file()]


def test_cli_delete_requires_matching_confirm(tmp_path):
    env = {"WORLD_FORGE_DATA_DIR": str(tmp_path / "d"), "WORLD_FORGE_PROVIDERS": "stub"}
    wid = json.loads(_run("init", "--concept", "待删除", env_extra=env).stdout)["world_id"]
    bad = _run("delete", "--world", wid, "--confirm", "wld_wrong", env_extra=env)
    assert bad.returncode == 1
    assert json.loads(_run("list", env_extra=env).stdout)  # 仍然存在
    ok = _run("delete", "--world", wid, "--confirm", wid, env_extra=env)
    assert ok.returncode == 0, ok.stderr
    assert not any(w["id"] == wid for w in json.loads(_run("list", env_extra=env).stdout))


def test_character_tab_shows_portrait_and_regenerate_button(tmp_path, monkeypatch, world, llm):
    """回归：有人设图的角色卡片要显示图片，并提供“重新生成人设图”按钮。"""
    from streamlit.testing.v1 import AppTest
    from world_forge import engine, proposals, repo
    from world_forge.providers.image import StubImageProvider

    cid = proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "次要"})["result"]["id"]
    engine.make_portrait(llm, StubImageProvider(), world, cid)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    assert not at.exception, at.exception
    def _find(node, typ):
        out = [node] if getattr(node, "type", None) == typ else []
        kids = getattr(node, "children", {})
        for k in (kids.values() if isinstance(kids, dict) else []):
            out += _find(k, typ)
        return out

    imgs = _find(at._tree, "image")
    assert imgs, "有人设图的角色应显示图片"
    labels = [b.label for b in at.button]
    assert "重新生成人设图" in labels
