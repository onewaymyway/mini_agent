"""灵感构思工作室 B0：工作目录、卡片校验与读写、定版快照、回退、列表。"""

import pytest

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace, list_brainstorms


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "雾港")
    w.create("雾港")
    return w


def test_create_writes_marker_and_layout(ws):
    assert ws.exists()
    meta = ws.meta()
    assert meta["kind"] == "brainstorm" and meta["current_version"] == 0 and meta["phase"] == "spark"
    assert ws.working.is_dir() and ws.versions.is_dir()


def test_create_rejects_duplicate_and_empty_title(tmp_path):
    w = BrainstormWorkspace(tmp_path / "a")
    with pytest.raises(BrainstormError):
        w.create("  ")
    w.create("A")
    with pytest.raises(BrainstormError):
        w.create("A")


def test_non_brainstorm_directory_is_not_a_project(tmp_path):
    w = BrainstormWorkspace(tmp_path / "plain")
    w.root.mkdir()
    assert not w.exists()
    with pytest.raises(BrainstormError):
        w.meta()


def test_defaults_when_modules_missing(ws):
    assert C.get_module(ws, "ideas") == []
    assert C.get_module(ws, "premise") == ""
    assert set(C.get_module(ws, "brief")) >= {"genre", "theme", "protagonist"}
    assert C.get_module(ws, "world") == {k: [] for k in C.WORLD_LISTS}


def test_ideas_roundtrip_and_validation(ws):
    C.put_module(ws, "ideas", [{"id": "i1", "text": "一座只在雾天出现的港口", "tags": ["悬疑"], "source": "author", "status": "keep"}])
    assert C.get_module(ws, "ideas")[0]["text"] == "一座只在雾天出现的港口"
    with pytest.raises(BrainstormError):
        C.put_module(ws, "ideas", [{"id": "i1", "text": "a"}, {"id": "i1", "text": "b"}])       # id 重复
    with pytest.raises(BrainstormError):
        C.put_module(ws, "ideas", [{"id": "i2", "text": "x", "status": "maybe"}])               # 状态不合法
    with pytest.raises(BrainstormError):
        C.put_module(ws, "ideas", [{"id": "i3", "text": "  "}])                                   # 空内容


def test_brief_only_accepts_intake_fields(ws):
    C.put_module(ws, "brief", {"genre": "悬疑", "theme": "记忆与代价"})
    assert C.get_module(ws, "brief")["genre"] == "悬疑"
    with pytest.raises(BrainstormError):
        C.put_module(ws, "brief", {"unknown_field": "x"})


def test_world_rules_need_text_and_unknown_keys_rejected(ws):
    C.put_module(ws, "world", {"rules": [{"text": "雾中可见死者", "cost": "每次代价一段记忆"}],
                               "taboos": ["不得在雾里点火"]})
    w = C.get_module(ws, "world")
    assert w["rules"][0]["cost"] == "每次代价一段记忆" and w["taboos"] == ["不得在雾里点火"]
    with pytest.raises(BrainstormError):
        C.put_module(ws, "world", {"rules": [{"cost": "没有正文"}]})
    with pytest.raises(BrainstormError):
        C.put_module(ws, "world", {"spells": []})


def test_cast_and_plot_and_questions(ws):
    C.put_module(ws, "cast", {"c1": {"name": "林", "role": "主角", "want": "找回弟弟", "relations": ["c2:弟弟"]}})
    assert C.get_module(ws, "cast")["c1"]["name"] == "林"
    with pytest.raises(BrainstormError):
        C.put_module(ws, "cast", {"c1": {"role": "无名字"}})
    C.put_module(ws, "plot", {"acts": [{"title": "第一幕", "beats": ["雾港出现"]}], "twists": [], "endings": ["开放"]})
    assert C.get_module(ws, "plot")["acts"][0]["title"] == "第一幕"
    C.put_module(ws, "open_questions", [{"id": "q1", "text": "弟弟是否还活着"}])
    assert C.get_module(ws, "open_questions")[0]["closed"] is False


def test_premise_is_text(ws):
    C.put_module(ws, "premise", "  一个用记忆换真相的侦探故事。  ")
    assert C.get_module(ws, "premise") == "一个用记忆换真相的侦探故事。"
    with pytest.raises(BrainstormError):
        C.put_module(ws, "premise", ["不是文字"])


def test_unknown_module_rejected(ws):
    with pytest.raises(BrainstormError):
        C.get_module(ws, "nope")
    with pytest.raises(BrainstormError):
        C.put_module(ws, "nope", {})


def test_snapshot_increments_and_is_immutable(ws):
    C.put_module(ws, "premise", "第一版命题")
    v1 = C.snapshot(ws, "初稿")
    C.put_module(ws, "premise", "第二版命题")
    v2 = C.snapshot(ws, "修改")
    assert (v1, v2) == (1, 2)
    assert (ws.version_dir(1) / "premise.md").read_text(encoding="utf-8").strip() == "第一版命题"
    assert ws.meta()["current_version"] == 2
    versions = C.list_versions(ws)
    assert [v["n"] for v in versions] == [1, 2] and versions[0]["note"] == "初稿"


def test_restore_creates_new_version_and_keeps_history(ws):
    C.put_module(ws, "premise", "A")
    C.snapshot(ws, "A")
    C.put_module(ws, "premise", "B")
    C.snapshot(ws, "B")
    new = C.restore(ws, 1)
    assert new == 3
    assert C.get_module(ws, "premise") == "A"
    assert len(C.list_versions(ws)) == 3                   # 历史保留，回退也记了版本
    assert C.list_versions(ws)[-1]["note"] == "回退到 v1"


def test_restore_removes_modules_absent_in_old_version(ws):
    C.snapshot(ws, "空白")                                   # v1：没有任何模块文件
    C.put_module(ws, "ideas", [{"id": "i1", "text": "后来加的灵感"}])
    C.restore(ws, 1)
    assert not (ws.working / "ideas.json").exists()
    assert C.get_module(ws, "ideas") == []


def test_restore_missing_version_fails(ws):
    with pytest.raises(BrainstormError):
        C.restore(ws, 9)


def test_content_hash_changes_with_content(ws):
    h0 = C.content_hash(ws)
    C.put_module(ws, "premise", "x")
    assert C.content_hash(ws) != h0


def test_list_brainstorms_skips_broken_and_sorts(tmp_path):
    base = tmp_path / "brainstorms"
    a = BrainstormWorkspace(base / "a")
    a.create("甲")
    b = BrainstormWorkspace(base / "b")
    b.create("乙")
    (base / "broken").mkdir()
    (base / "broken" / "_studio").mkdir(parents=True)
    (base / "broken" / "_studio" / "brainstorm.json").write_text("not json", encoding="utf-8")
    titles = [r["title"] for r in list_brainstorms(base)]
    assert set(titles) == {"甲", "乙"}
    assert list_brainstorms(tmp_path / "none") == []
