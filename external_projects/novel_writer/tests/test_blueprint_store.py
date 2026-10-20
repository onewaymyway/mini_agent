"""第五阶段：蓝图拆分（_meta.md + 每章一个文件 + 索引），plan_blueprint 产出草稿，blueprint_split 拆分。全部使用桩。"""

from __future__ import annotations

import json

import pytest

from fakes import make_blueprint, put_blueprint, read_blueprint
from novel_writer import blueprint_store as BS
from novel_writer import chapter_ops as C
from novel_writer import config, llm_convert as LC, pipeline as P
from novel_writer import steps as S, tool_api
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _ws(tmp_path) -> Workspace:
    r = tool_api.create_novel("蓝图", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


def _paths(ws):
    return ws.path("blueprint_dir"), ws.path("blueprint")


def test_round_trip_and_unchanged_rewrite_does_not_write(tmp_path):
    ws = _ws(tmp_path)
    d, i = _paths(ws)
    bp = make_blueprint(3)
    assert BS.write_store(d, i, bp) is True
    assert BS.write_store(d, i, bp) is False
    back = BS.read_store(d)
    assert back["structure"] == bp["structure"] and back["meta"] == bp["meta"]
    assert [c["id"] for c in back["chapters"]] == [1, 2, 3]
    assert back["chapters"][0] == bp["chapters"][0]


def test_only_changed_chapter_file_is_rewritten_and_index_tracks_any_field(tmp_path):
    ws = _ws(tmp_path)
    d, i = _paths(ws)
    bp = make_blueprint(3)
    bp["chapters"][0]["pov"] = "李凌云"
    BS.write_store(d, i, bp)
    untouched = (d / "第002章.md").read_text(encoding="utf-8")
    before_index = i.read_text(encoding="utf-8")
    # 非索引列的字段（pov）改动：索引的内容指纹必须变化，否则下游不会过期
    bp["chapters"][0]["pov"] = "王道长"
    assert BS.write_store(d, i, bp) is True
    assert (d / "第002章.md").read_text(encoding="utf-8") == untouched
    assert i.read_text(encoding="utf-8") != before_index


def test_hand_edit_is_validated_and_reports_file(tmp_path):
    ws = _ws(tmp_path)
    d, i = _paths(ws)
    BS.write_store(d, i, make_blueprint(2))
    f = d / "第001章.md"
    f.write_text(f.read_text(encoding="utf-8").replace('title: "第1章 测试"', "title: 第1章 测试"), encoding="utf-8")
    with pytest.raises(BS.BlueprintStoreError) as e:
        BS.read_store(d)
    assert any("第001章.md" in p for p in e.value.problems)


def test_chapter_id_must_match_filename(tmp_path):
    ws = _ws(tmp_path)
    d, i = _paths(ws)
    BS.write_store(d, i, make_blueprint(2))
    (d / "第001章.md").write_text(
        (d / "第001章.md").read_text(encoding="utf-8").replace("id: 1\n", "id: 5\n"), encoding="utf-8")
    with pytest.raises(BS.BlueprintStoreError) as e:
        BS.read_store(d)
    assert any("与文件名章号 1 不一致" in p for p in e.value.problems)


def test_split_rejects_bad_chapter_table():
    with pytest.raises(BS.BlueprintStoreError):
        BS.split_parts({"meta": {}, "chapters": []})
    with pytest.raises(BS.BlueprintStoreError):
        BS.split_parts({"meta": {}, "chapters": [{"id": 1}, {"id": 1}]})


def test_migrate_healthy_legacy_writes_draft_copy_and_keeps_bak(tmp_path):
    ws = _ws(tmp_path)
    legacy = ws.path("blueprint_legacy")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    bp = make_blueprint(3)
    legacy.write_text(json.dumps(bp, ensure_ascii=False), encoding="utf-8")
    d, i = _paths(ws)
    dry = BS.migrate_legacy(legacy, d, i, dry_run=True)
    assert dry["status"] == "dry_run" and dry["chapters"] == 3 and not i.exists()
    res = BS.migrate_legacy(legacy, d, i, draft_path=ws.path("blueprint_draft"))
    assert res["status"] == "migrated"
    assert BS.read_store(d)["chapters"] == bp["chapters"]
    # 草稿副本存在，plan_blueprint 的产出不会被误判为缺失而整本重跑
    assert json.loads(ws.path("blueprint_draft").read_text(encoding="utf-8"))["chapters"] == bp["chapters"]
    assert legacy.with_name(legacy.name + ".bak").exists() and not legacy.exists()


def test_migrate_corrupt_legacy_needs_llm_and_leaves_original(tmp_path):
    ws = _ws(tmp_path)
    legacy = ws.path("blueprint_legacy")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    broken = '{"meta": {}, "chapters": [{"id": 1,}'
    legacy.write_text(broken, encoding="utf-8")
    d, i = _paths(ws)
    res = BS.migrate_legacy(legacy, d, i)
    assert res["status"] == "needs_llm"
    assert legacy.read_text(encoding="utf-8") == broken and not i.exists()


def test_migrate_corrupt_with_llm_requires_confirm(tmp_path):
    ws = _ws(tmp_path)
    legacy = ws.path("blueprint_legacy")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"meta": {}, "chapters": [{"id": 1,}', encoding="utf-8")
    d, i = _paths(ws)
    data = make_blueprint(2)
    fake = lambda src: LC.ConvertResult(ok=True, data=data, attempts=1, staging=tmp_path / "out")  # noqa: E731
    res = BS.migrate_legacy(legacy, d, i, convert=fake)
    assert res["status"] == "review_required" and res["via"] == "llm" and not i.exists()
    res2 = BS.migrate_legacy(legacy, d, i, convert=fake, confirm=True)
    assert res2["status"] == "migrated" and BS.read_store(d)["structure"] == data["structure"]


def test_llm_convert_failure_keeps_original(tmp_path):
    ws = _ws(tmp_path)
    legacy = ws.path("blueprint_legacy")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("{坏", encoding="utf-8")
    d, i = _paths(ws)
    fake = lambda src: LC.ConvertResult(ok=False, errors=["没有写出任何章节文件"], attempts=3)  # noqa: E731
    res = BS.migrate_legacy(legacy, d, i, convert=fake)
    assert res["status"] == "llm_failed" and legacy.read_text(encoding="utf-8") == "{坏" and not i.exists()


def test_split_handler_writes_store_and_reports_missing_draft(tmp_path):
    ws = _ws(tmp_path)
    step = S.blueprint_split_step()
    with pytest.raises(C.ChapterOpError):
        C.h_blueprint_split(ws, step, {})          # 没有草稿
    ws.path("blueprint_draft").parent.mkdir(parents=True, exist_ok=True)
    ws.path("blueprint_draft").write_text(json.dumps(make_blueprint(3), ensure_ascii=False), encoding="utf-8")
    res = C.h_blueprint_split(ws, step, {})
    assert "3 章" in res["summary"] and ws.path("blueprint").is_file()
    again = C.h_blueprint_split(ws, step, {})
    assert "无实质变化" in again["summary"]


def test_downstream_depends_on_split_and_no_self_dependency():
    plan = {s.id: s for s in P.build_plan(_fake_ws_for_plan())}
    split = plan["blueprint_split"]
    assert split.deps == ("plan_blueprint",)           # 不能依赖自己
    assert split.outputs == (S.rel("blueprint"),)
    assert plan["plan_blueprint"].outputs[0] == S.rel("blueprint_draft")
    assert plan["design_theme"].deps == ("blueprint_split",)
    assert plan["blueprint_split"].handler == "blueprint_split"


def _fake_ws_for_plan():
    # build_plan 需要真实工作区中的设置；这里只需要步骤定义，用真实临时工作区更稳
    import tempfile
    from pathlib import Path
    root = Path(tempfile.mkdtemp())
    r = tool_api.create_novel("计划", novels_dir=str(root / "n"), index_file=str(root / "s.json"),
                              gear="G3", total_chapters_planned=2)
    return Workspace(r["data"]["workspace"])


def test_author_save_changes_only_the_edited_chapter_file(tmp_path):
    from novel_writer import views as V
    ws = _ws(tmp_path)
    put_blueprint(ws, make_blueprint(3))
    rows = [dict(c) for c in V.planning_view(ws)["chapters"]]
    before = (ws.path("blueprint_dir") / "第002章.md").read_text(encoding="utf-8")
    rows[0]["title"] = "新的开篇"
    res = tool_api.save_chapters(str(ws.root), rows)
    assert res["ok"], res
    assert (ws.path("blueprint_dir") / "第002章.md").read_text(encoding="utf-8") == before
    assert read_blueprint(ws)["chapters"][0]["title"] == "新的开篇"


def test_migrate_blueprint_api_dry_run(tmp_path):
    ws = _ws(tmp_path)
    legacy = ws.path("blueprint_legacy")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps(make_blueprint(4), ensure_ascii=False), encoding="utf-8")
    r = tool_api.migrate_blueprint(str(ws.root), dry_run=True)
    assert r["data"]["status"] == "dry_run" and r["data"]["chapters"] == 4
    assert not ws.path("blueprint").exists()


def test_author_edit_survives_a_later_split(tmp_path):
    from novel_writer import views as V
    ws = _ws(tmp_path)
    put_blueprint(ws, make_blueprint(3))
    rows = [dict(c) for c in V.planning_view(ws)["chapters"]]
    rows[1]["title"] = "作者改的标题"
    assert tool_api.save_chapters(str(ws.root), rows)["ok"]
    C.h_blueprint_split(ws, S.blueprint_split_step(), {})     # 拆分不应把作者的编辑冲掉
    assert read_blueprint(ws)["chapters"][1]["title"] == "作者改的标题"
