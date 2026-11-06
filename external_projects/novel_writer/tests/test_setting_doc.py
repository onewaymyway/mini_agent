"""新建小说时可选的设定文档：全文保存、全文注入每一步提示词（仅供参考），字段可以全部留空。"""

from __future__ import annotations

import pytest

from novel_writer import config, runner, tool_api
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)


def _new_book(tmp_path, title="设定文档测试"):
    r = tool_api.create_novel(title, novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"))
    assert r["ok"], r
    return r["data"]["workspace"]


def test_setting_doc_saved_in_full_and_normalized(tmp_path):
    path = _new_book(tmp_path)
    doc = "# 世界观\r\n\r\n" + ("一段很长的设定。\n" * 2000)
    r = tool_api.save_setting_doc(path, doc)
    assert r["ok"] and r["data"]["saved"] is True
    saved = Workspace(path).path("setting_doc").read_text(encoding="utf-8")
    assert "\r" not in saved
    assert saved.count("一段很长的设定。") == 2000          # 全文，没有截断


def test_empty_setting_doc_removes_file(tmp_path):
    path = _new_book(tmp_path)
    tool_api.save_setting_doc(path, "# 设定")
    assert Workspace(path).path("setting_doc").exists()
    r = tool_api.save_setting_doc(path, "   \n ")
    assert r["ok"] and r["data"]["saved"] is False
    assert not Workspace(path).path("setting_doc").exists()


def test_oversized_setting_doc_is_rejected_not_truncated(tmp_path):
    path = _new_book(tmp_path)
    r = tool_api.save_setting_doc(path, "字" * (tool_api.SETTING_DOC_MAX_CHARS + 1))
    assert r["ok"] is False and r["error"]["type"] == "validation_error"
    assert not Workspace(path).path("setting_doc").exists()


def test_intake_block_injects_full_setting_doc_as_reference(tmp_path):
    path = _new_book(tmp_path)
    doc = "# 设定\n" + "细节段落。" * 800 + "\n结尾标记：END-OF-DOC"
    tool_api.save_setting_doc(path, doc)
    block = runner._intake_block(Workspace(path))
    assert "作者设定文档（仅供参考" in block
    assert "结尾标记：END-OF-DOC" in block                 # 全文注入，末尾也在


def test_setting_doc_is_independent_of_brainstorm_seed(tmp_path):
    path = _new_book(tmp_path)
    tool_api.save_setting_doc(path, "# 设定A")
    ws = Workspace(path)
    assert ws.path("setting_doc") != ws.path("brainstorm_seed_note")
    assert "构思种子" not in runner._intake_block(ws)      # 没有灵感构思交接时，不出现构思种子


def test_all_intake_fields_may_be_left_empty(tmp_path):
    path = _new_book(tmp_path)
    r = tool_api.save_intake(path, {})
    assert r["ok"], r
    tool_api.save_setting_doc(path, "# 只有设定文档")
    assert "只有设定文档" in runner._intake_block(Workspace(path))
