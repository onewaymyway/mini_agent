"""灵感构思 → 小说设定文档（Markdown）导出：结构完整、不截断、已丢弃/已关闭的内容不出现。"""

import json

import pytest

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import export as EX
from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm.workspace import BrainstormWorkspace


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "雾港")
    w.create("雾港")
    wk = w.working
    wk.mkdir(parents=True, exist_ok=True)
    (wk / C.MODULE_FILES["brief"]).write_text(json.dumps(
        {"genre": "悬疑", "theme": "记忆与代价", "title": "雾港旧事"}, ensure_ascii=False), encoding="utf-8")
    (wk / C.MODULE_FILES["premise"]).write_text("一座常年大雾的港口，每个人都在忘记一件事。", encoding="utf-8")
    (wk / C.MODULE_FILES["ideas"]).write_text(json.dumps([
        {"text": "钟楼会在雾里报时", "status": "keep", "source": "author"},
        {"text": "被丢弃的灵感不应出现", "status": "drop", "source": "ai"},
        {"text": "搁置的想法", "status": "park", "source": "ai"},
    ], ensure_ascii=False), encoding="utf-8")
    (wk / C.MODULE_FILES["world"]).write_text(json.dumps({
        "rules": ["雾会带走一段记忆"], "locations": [{"text": "钟楼", "note": "港口最高处"}],
        "factions": [], "timeline": [], "taboos": ["不能直视雾心"]}, ensure_ascii=False), encoding="utf-8")
    (wk / C.MODULE_FILES["cast"]).write_text(json.dumps({
        "lin": {"name": "林远", "role": "主角", "motive": "找回母亲的名字"}}, ensure_ascii=False), encoding="utf-8")
    (wk / C.MODULE_FILES["plot"]).write_text(json.dumps({
        "acts": [{"title": "入港", "beats": ["林远抵达", "发现钟楼"]}],
        "twists": ["钟楼是记忆的收集处"], "endings": ["林远选择遗忘"]}, ensure_ascii=False), encoding="utf-8")
    (wk / C.MODULE_FILES["open_questions"]).write_text(json.dumps([
        {"text": "母亲去了哪里？", "closed": False},
        {"text": "已经回答的问题", "closed": True},
    ], ensure_ascii=False), encoding="utf-8")
    return w


def test_markdown_has_all_sections_in_order(ws):
    md = EX.build_markdown(ws)
    heads = [l for l in md.splitlines() if l.startswith("## ")]
    assert heads == ["## 题材卡", "## 核心命题", "## 灵感池", "## 世界观", "## 角色",
                     "## 剧情骨架", "## 备选方案", "## 开放问题"]
    assert md.startswith("# 小说设定文档：雾港旧事")


def test_content_is_rendered_from_each_module(ws):
    md = EX.build_markdown(ws)
    assert "**题材**：悬疑" in md and "**主题（一句话）**：记忆与代价" in md
    assert "一座常年大雾的港口" in md
    assert "[已搁置] 搁置的想法" in md or "[搁置] 搁置的想法" in md
    assert "雾会带走一段记忆" in md and "不能直视雾心" in md
    assert "钟楼（note：港口最高处）" in md
    assert "### 林远（主角）" in md and "**motive**：找回母亲的名字" in md
    assert "### 第1幕 入港" in md and "林远抵达" in md
    assert "钟楼是记忆的收集处" in md and "林远选择遗忘" in md
    assert "母亲去了哪里？" in md


def test_dropped_ideas_and_closed_questions_are_excluded(ws):
    md = EX.build_markdown(ws)
    assert "被丢弃的灵感不应出现" not in md
    assert "已经回答的问题" not in md


def test_empty_workspace_still_produces_full_document(tmp_path):
    w = BrainstormWorkspace(tmp_path / "空项目")
    w.create("空项目")
    md = EX.build_markdown(w)
    assert md.count("（暂无内容）") >= 5
    assert "## 备选方案" in md and "## 开放问题" in md


def test_long_content_is_not_truncated(ws):
    long = "很长的设定段落。" * 3000
    (ws.working / C.MODULE_FILES["premise"]).write_text(long, encoding="utf-8")
    md = EX.build_markdown(ws)
    assert md.count("很长的设定段落。") == 3000


def test_api_returns_markdown_and_filename_and_save_writes_copy(ws):
    res = BA.setting_doc_markdown(str(ws.root))
    assert res["ok"]
    assert res["data"]["filename"].startswith("设定文档_雾港旧事_") and res["data"]["filename"].endswith(".md")
    assert "## 题材卡" in res["data"]["markdown"]
    saved = BA.setting_doc_save(str(ws.root))
    assert saved["ok"]
    from pathlib import Path
    p = Path(saved["data"]["path"])
    assert p.parent.name == "exports" and p.is_file()
    assert p.read_text(encoding="utf-8") == EX.build_markdown(ws)
