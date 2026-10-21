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


def test_migrate_button_follows_the_llm_checkbox(tmp_path, monkeypatch):
    """勾选「允许 LLM 转换」后，损坏的旧蓝图应走 LLM 转换并进入复核（review_required），而不是 needs_llm。"""
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from novel_writer import blueprint_store as BS
    from novel_writer import llm_convert as LC
    import pathlib

    r = tool_api.create_novel("LLM", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=2)
    ws = Workspace(r["data"]["workspace"])
    ws.path("blueprint_legacy").parent.mkdir(parents=True, exist_ok=True)
    ws.path("blueprint_legacy").write_text('{"meta": {}, "chapters": [{"id": 1,}', encoding="utf-8")   # 损坏

    fake_data = {"meta": {}, "chapters": [{"id": 1, "title": "转换后"}]}
    monkeypatch.setattr(BS, "llm_convert_legacy",
                        lambda legacy, workdir, invoker, **kw: LC.ConvertResult(
                            ok=True, data=fake_data, attempts=1, staging=tmp_path / "out"))
    monkeypatch.setattr("novel_writer.runner.FrameworkInvoker", lambda *a, **k: object())

    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(pathlib.Path(__file__).resolve().parents[1])!r})\n"
        "from ui.migration import render_migration\n"
        f"render_migration({str(ws.root)!r}, 'codex')\n"
    )
    at = AppTest.from_string(script).run(timeout=60)
    assert not at.exception, at.exception
    at.checkbox(key="codex_mig_use_llm").check().run()
    at.button(key="codex_mig_run_blueprint").click().run()
    assert not at.exception, at.exception
    texts = [w.value for w in at.warning]
    assert any("LLM 已生成" in t for t in texts), texts
    assert not ws.path("blueprint").exists()      # 复核前不提交
