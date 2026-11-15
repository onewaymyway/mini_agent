"""修改小说名与章节名：只改显示名，不动剧本/素材/审核；队列运行中拒绝；章节封面按新名字重绘。"""
import json
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import chapter_io as cio
from novel_to_video import project_store as ps
from novel_to_video import runtime as rt
from novel_to_video import tool_api
from novel_to_video.steps import chapter_cover as CC


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1, 2, 3))
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="旧书名")
    assert res["ok"], res
    return Path(res["data"]["project_dir"])


def test_rename_project_title(proj):
    r = tool_api.rename_project(str(proj), "  新书名  ")
    assert r["ok"] and r["data"]["title"] == "新书名"
    assert ps.read_json(proj / "project.json")["title"] == "新书名"
    assert CC.chapter_cover_settings(proj)["book_title"] == "新书名"      # 章节封面默认跟随书名
    assert not tool_api.rename_project(str(proj), "   ")["ok"]
    assert not tool_api.rename_project(str(proj), "字" * 81)["ok"]
    assert ps.read_json(proj / "project.json")["title"] == "新书名"


def test_rename_chapters_only_changes_names(proj):
    before = ps.read_yaml(proj / "chapters" / "index.yaml") if hasattr(ps, "read_yaml") else cio.load_index(proj)
    r = tool_api.rename_chapters(str(proj), {1: "新的第一章", 2: cio.chapter_row(proj, 2)["title"]})
    assert r["ok"] and [c["no"] for c in r["data"]["changed"]] == [1]       # 没变化的章不写
    after = cio.load_index(proj)
    rows = {c["no"]: c for c in after["chapters"]}
    assert rows[1]["title"] == "新的第一章"
    old = {c["no"]: c for c in before["chapters"]}
    for no in (1, 2, 3):                                                      # 除章名外一切不变
        assert {k: v for k, v in rows[no].items() if k != "title"} == {k: v for k, v in old[no].items() if k != "title"}
    assert not tool_api.rename_chapters(str(proj), {99: "x"})["ok"]
    assert not tool_api.rename_chapters(str(proj), {1: ""})["ok"]
    assert cio.chapter_row(proj, 1)["title"] == "新的第一章"                  # 失败不写入任何改动


def test_rename_refused_while_queue_running(proj):
    rt.update(proj, state="running")
    assert not tool_api.rename_project(str(proj), "x")["ok"]
    assert not tool_api.rename_chapters(str(proj), {1: "x"})["ok"]
    assert ps.read_json(proj / "project.json")["title"] == "旧书名"
    rt.update(proj, state="idle")
    assert tool_api.rename_project(str(proj), "x")["ok"]
