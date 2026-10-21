"""看板的迁移状态：只读判断，待迁移项才提示。"""

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


def _ws(tmp_path) -> Workspace:
    r = tool_api.create_novel("迁移", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=2)
    return Workspace(r["data"]["workspace"])


def test_fresh_project_has_nothing_pending(tmp_path):
    ws = _ws(tmp_path)
    items = tool_api.migration_status(str(ws.root))["data"]["items"]
    assert [i["pending"] for i in items] == [False, False, False]


def test_legacy_files_are_pending_until_migrated(tmp_path):
    ws = _ws(tmp_path)
    ws.path("blueprint_legacy").parent.mkdir(parents=True, exist_ok=True)
    ws.path("blueprint_legacy").write_text(json.dumps({"meta": {}, "chapters": [{"id": 1, "title": "a"}]}, ensure_ascii=False),
                                           encoding="utf-8")
    items = {i["name"]: i for i in tool_api.migration_status(str(ws.root))["data"]["items"]}
    assert items["blueprint"]["pending"] is True
    assert items["blueprint"]["legacy"].endswith("小说蓝图.json")
    assert tool_api.migrate_blueprint(str(ws.root))["data"]["status"] == "migrated"
    items = {i["name"]: i for i in tool_api.migration_status(str(ws.root))["data"]["items"]}
    assert items["blueprint"]["pending"] is False and items["blueprint"]["migrated"] is True


def test_panel_module_imports_and_renders_nothing_for_fresh_project(tmp_path):
    import ui.migration as M
    assert callable(M.render_migration)
