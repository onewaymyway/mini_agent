"""entrypoints 可独立运行（不依赖 daemon）+ Streamlit 页面冒烟（AppTest）。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _run(*args, data_dir=None):
    env = dict(os.environ)
    if data_dir is not None:
        env["NOVEL_WRITER_DATA_DIR"] = str(data_dir)  # 隔离：不污染真实 data/
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=60, env=env)


def test_health_entrypoint(tmp_path):
    p = _run("entrypoints/health.py", data_dir=tmp_path)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "ok"


def test_create_and_list_entrypoints(tmp_path):
    ws = tmp_path / "ep_book"
    data = tmp_path / "data"
    p = _run("entrypoints/create_novel.py", "--title", "入口测试", "--workspace", str(ws), "--gear", "G1", data_dir=data)
    assert p.returncode == 0, p.stdout + p.stderr
    assert f"workspace={ws}" in p.stdout
    assert (ws / "_studio/settings.json").is_file()
    p2 = _run("entrypoints/list_novels.py", data_dir=data)
    assert p2.returncode == 0 and "入口测试" in p2.stdout and "gear=G1" in p2.stdout
    assert not (ROOT / "data" / "shelf.json").exists()


def test_create_rejects_bad_gear():
    assert _run("entrypoints/create_novel.py", "--title", "x", "--gear", "G9", data_dir=ROOT / "data").returncode != 0


def test_streamlit_pages_smoke(tmp_path, monkeypatch):
    at_mod = pytest.importorskip("streamlit.testing.v1")
    from novel_writer import config
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    at = at_mod.AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception, at.exception
    assert any("书架" in h.value for h in at.header)
    at.sidebar.radio[0].set_value("🧩 Skill 库").run()
    assert not at.exception, at.exception
    assert any("26" in c.value for c in at.caption)
    at.sidebar.radio[0].set_value("➕ 新建小说").run()
    assert not at.exception, at.exception
