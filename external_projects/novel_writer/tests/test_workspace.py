"""workspace.py：初始化幂等性、路径查询、命名规范、恢复、体检。"""

import json

import pytest

from novel_writer import workspace as W
from novel_writer.workspace import Workspace, WorkspaceError


def test_init_creates_standard_layout(tmp_path):
    ws = Workspace(tmp_path / "青云传")
    res = ws.init("青云传")
    assert not res["already_initialized"]
    for rel in W.STANDARD_DIRS:
        assert (ws.root / rel).is_dir(), rel
    for sub in W.STUDIO_SUBDIRS:
        assert (ws.studio / sub).is_dir()
    assert ws.is_initialized()
    cfg = ws.workspace_config()
    assert cfg["novel_title"] == "青云传" and cfg["chapters_completed"] == 0
    assert (ws.root / "章节总结/全书章节索引.md").read_text(encoding="utf-8").startswith("# 全书章节索引")


def test_init_is_idempotent_and_does_not_overwrite(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init("书")
    idx = ws.path("chapter_index")
    idx.write_text("用户改过", encoding="utf-8")
    again = ws.init("书")
    assert again["already_initialized"] is True
    assert idx.read_text(encoding="utf-8") == "用户改过"


def test_init_refuses_nonempty_foreign_dir(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.txt").write_text("x")
    with pytest.raises(WorkspaceError):
        Workspace(d).init("书")


def test_init_allows_empty_existing_dir(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    Workspace(d).init("书")
    assert Workspace(d).is_initialized()


def test_init_rejects_bad_title(tmp_path):
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path / "x").init('?*:/\\')


def test_settings_defaults_conservative(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init("书")
    s = ws.settings()
    assert s["gear"] == "G0" and s["quality"] == "standard"
    assert s["checkpoints"]["waive_blueprint_confirm"] is False


def test_path_lookup_and_unknown_key(tmp_path):
    ws = Workspace(tmp_path / "w")
    assert ws.path("blueprint") == ws.root / "大纲规划/蓝图/_index.md"
    assert ws.path("review_report", nn=3) == ws.root / "一致性检查/第03章_检查报告.md"
    assert ws.path("memory_summary", n=8) == ws.root / "项目状态/memory/ch_8.json"
    with pytest.raises(KeyError):
        ws.path("nope")
    with pytest.raises(KeyError):
        ws.path("review_report")  # 缺 nn


def test_chapter_filename_rules():
    assert W.chapter_filename(1, "外门弟子", 40) == "第01章 外门弟子.md"
    assert W.chapter_filename(7, "a/b:c?", 40) == "第07章 abc.md"
    assert W.chapter_filename(7, "标题", 120) == "第007章 标题.md"


def test_count_words_ignores_headings_and_whitespace():
    assert W.count_words("# 第一章\n\n你好 世界\n") == 4
    assert W.count_words("") == 0


def test_list_chapters_and_recover(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init("书")
    (ws.root / "正文/第01章 开端.md").write_text("# 第01章\n一二三四五", encoding="utf-8")
    (ws.root / "正文/第02章 继续.md").write_text("六七八", encoding="utf-8")
    (ws.root / "正文/随手记.md").write_text("x", encoding="utf-8")
    from fakes import put_blueprint
    put_blueprint(ws, {"structure": {"total_chapters": 10}, "meta": {"target_length": 30000},
                       "chapters": [{"id": i, "title": f"第{i}章"} for i in range(1, 11)]})
    rec = ws.recover()
    assert rec["chapters_written"] == 2 and rec["last_chapter"] == 2
    assert rec["total_words"] == 8 and rec["total_chapters_planned"] == 10
    assert rec["progress_pct"] == 20.0 and rec["blueprint_exists"]
    assert {f["key"]: f["present"] for f in rec["files"]}["characters"] is False


def test_recover_requires_initialized(tmp_path):
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path / "nothing").recover()


def test_health_ok_and_detects_problems(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init("书")
    assert ws.health().ok
    (ws.root / "正文/第01章 a.md").write_text("x", encoding="utf-8")
    (ws.root / "正文/第03章 c.md").write_text("x", encoding="utf-8")
    (ws.root / "正文/乱命名.md").write_text("x", encoding="utf-8")
    rep = ws.health()
    assert rep.ok and any("缺失章号" in w for w in rep.warnings) and any("命名规范" in w for w in rep.warnings)
    ws.path("studio_settings").write_text("{bad", encoding="utf-8")
    assert not ws.health().ok  # 设置文件损坏 => 问题
    (ws.root / "项目状态").rename(ws.root / "x_state")
    assert not ws.health().ok


def test_health_missing_workspace(tmp_path):
    rep = Workspace(tmp_path / "ghost").health()
    assert not rep.ok


def test_paths_table_overrides_conflicting_conventions(tmp_path):
    ws = Workspace(tmp_path / "w")
    table = ws.paths_table_markdown()
    assert str(ws.root) in table
    assert "大纲规划/小说蓝图.json" in table and "章节总结/第{nn}章_总结.md" in table
    assert "idea_bank" in table and "推演记录" in table
    assert "studio_" not in table  # 系统文件不暴露给 skill
    assert "project/summaries" in table  # 作为"禁止写法"出现在说明里
