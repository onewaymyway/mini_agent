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
