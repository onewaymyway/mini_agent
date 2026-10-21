"""迁移面板在同一次页面运行中渲染两次（设定库 + 伏笔页）时，控件 key 不能重复。"""

from __future__ import annotations

import json

import pytest

from novel_writer import config, tool_api
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def test_panel_renders_twice_in_one_run_without_duplicate_keys(tmp_path):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    r = tool_api.create_novel("键", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=2)
    ws = Workspace(r["data"]["workspace"])
    ws.path("blueprint_legacy").parent.mkdir(parents=True, exist_ok=True)
    ws.path("blueprint_legacy").write_text(json.dumps({"meta": {}, "chapters": [{"id": 1, "title": "a"}]}),
                                           encoding="utf-8")
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(__import__('pathlib').Path(__file__).resolve().parents[1])!r})\n"
        "from ui.migration import render_migration\n"
        f"WS = {str(ws.root)!r}\n"
        "render_migration(WS, 'codex')\n"
        "render_migration(WS, 'threads')\n"
    )
    at = AppTest.from_string(script).run(timeout=60)
    assert not at.exception, at.exception
    keys = [c.key for c in at.checkbox]
    assert len(keys) == len(set(keys)) == 2
