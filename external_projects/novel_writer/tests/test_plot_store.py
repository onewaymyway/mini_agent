"""伏笔账本小文件存储（M9 第二阶段）：往返一致、局部写入、损坏定位、迁移安全。"""

import json

import pytest

from novel_writer import chapter_ops as C
from novel_writer import config, plot_delta as PD, plot_store as PS, steps as S, tool_api
from novel_writer.fsutil import atomic_write_json
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _ws(tmp_path):
    r = tool_api.create_novel("伏笔存储", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=5)
    return Workspace(r["data"]["workspace"])


def _sample():
    d = {"foreshadowing": [
        {"id": "F10", "name": "玉佩 | 甲", "status": "advanced", "plant_chapter": 3,
         "progress": [{"chapter": 4, "note": "出现"}]},
        {"id": "F2", "name": "信", "status": "planted", "plant_chapter": 1},
    ]}
    return PD.apply_delta(d, {"resolved": [{"id": "F2", "note": "揭晓"}]}, 5)


def test_round_trip_is_exact(tmp_path):
    base = tmp_path / "伏笔"
    data = _sample()
    PS.write_store(base, data)
    back = PS.read_store(base)
    assert PD.canonical(PS._normalized(back)) == PD.canonical(PS._normalized(data))


def test_unchanged_write_touches_nothing_and_single_change_touches_one_file(tmp_path):
    base = tmp_path / "伏笔"
    data = _sample()
    PS.write_store(base, data)
    assert PS.write_store(base, data) is False                 # 没有变化：一个文件都不写
    f10 = base / "F10.md"
    other = base / "F2.md"
    before_other = other.read_text(encoding="utf-8")
    changed = PD.apply_delta(data, {"advanced": [{"id": "F10", "note": "新进展"}]}, 6)
    assert PS.write_store(base, changed) is True
    assert other.read_text(encoding="utf-8") == before_other   # 另一条伏笔的文件不受影响
    assert "新进展" in f10.read_text(encoding="utf-8")


def test_index_is_compact_and_reports_counts(tmp_path):
    base = tmp_path / "伏笔"
    PS.write_store(base, _sample())
    idx = (base / "_index.md").read_text(encoding="utf-8")
    assert "共 2 条" in idx and "已兑现 1" in idx and "推进中 1" in idx
    assert "玉佩 ／ 甲" in idx                                   # 名称中的竖线已替换，不破坏表格
    assert "progress" not in idx                                 # 索引不包含细节


def test_corrupt_item_file_is_named_in_error(tmp_path):
    base = tmp_path / "伏笔"
    PS.write_store(base, _sample())
    (base / "F2.md").write_text("---\nid: \"F2\"\nstatus: 这不是JSON\n---\n", encoding="utf-8")
    with pytest.raises(PS.LedgerError) as exc:
        PS.read_store(base)
    assert "F2.md" in exc.value.problems[0]


def test_file_id_must_match_file_name(tmp_path):
    base = tmp_path / "伏笔"
    PS.write_store(base, _sample())
    (base / "F2.md").write_text((base / "F10.md").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(PS.LedgerError) as exc:
        PS.read_store(base)
    assert "文件名不一致" in exc.value.problems[0]


def test_illegal_ids_are_rejected(tmp_path):
    with pytest.raises(PS.LedgerError):
        PS.write_store(tmp_path / "伏笔", {"foreshadowing": [{"id": "../evil"}]})


def test_migration_dry_run_changes_nothing(tmp_path):
    legacy = tmp_path / "plot_tracker.json"
    atomic_write_json(legacy, _sample())
    base = tmp_path / "伏笔"
    r = PS.migrate_legacy(legacy, base, dry_run=True)
    assert r["status"] == "dry_run" and r["items"] == 2
    assert legacy.exists() and not base.exists()


def test_migration_converts_verifies_and_keeps_backup(tmp_path):
    legacy = tmp_path / "plot_tracker.json"
    atomic_write_json(legacy, _sample())
    base = tmp_path / "伏笔"
    r = PS.migrate_legacy(legacy, base)
    assert r["status"] == "migrated"
    assert not legacy.exists() and (tmp_path / "plot_tracker.json.bak").exists()
    assert PS.is_store(base)
    assert PD.canonical(PS._normalized(PS.read_store(base))) == PD.canonical(PS._normalized(_sample()))
    assert PS.migrate_legacy(legacy, base)["status"] in ("nothing", "already")   # 重复迁移是安全的


def test_migration_of_corrupt_legacy_aborts_and_keeps_original(tmp_path):
    legacy = tmp_path / "plot_tracker.json"
    legacy.write_text("{坏掉的", encoding="utf-8")
    with pytest.raises(PD.PlotDeltaError):
        PS.migrate_legacy(legacy, tmp_path / "伏笔")
    assert legacy.read_text(encoding="utf-8") == "{坏掉的"
    assert not (tmp_path / "伏笔").exists()


def test_tool_api_migration_wrapper(tmp_path):
    ws = _ws(tmp_path)
    atomic_write_json(ws.path("plot_legacy"), _sample())
    r = tool_api.migrate_plot_ledger(str(ws.root), dry_run=True)
    assert r["ok"] and r["data"]["status"] == "dry_run"
    r = tool_api.migrate_plot_ledger(str(ws.root))
    assert r["ok"] and r["data"]["status"] == "migrated"


def test_merge_refuses_unmigrated_legacy_ledger(tmp_path):
    ws = _ws(tmp_path)
    atomic_write_json(ws.path("plot_legacy"), _sample())
    atomic_write_json(ws.path("plot_delta", nn=3), {"planted": [{"id": "F9", "name": "新"}]})
    step = {s.id: s for s in __import__("novel_writer.pipeline", fromlist=["build_plan"]).build_plan(ws)}["ch03.plot_merge"]
    with pytest.raises(C.ChapterOpError) as exc:
        C.h_plot_merge(ws, step, {})
    assert "迁移" in str(exc.value)


def test_import_from_register_creates_store(tmp_path):
    ws = _ws(tmp_path)
    atomic_write_json(ws.path("plot_register"), {"foreshadowing": [{"id": "F1", "name": "玉佩", "plant_chapter": 1}]})
    step = {s.id: s for s in __import__("novel_writer.pipeline", fromlist=["build_plan"]).build_plan(ws)}["plot_import"]
    r = C.h_plot_import(ws, step, {})
    assert "已导入 1 条" in r["summary"]
    assert PS.is_store(ws.path("plot_items_dir"))
    assert PS.read_store(ws.path("plot_items_dir"))["foreshadowing"][0]["id"] == "F1"


def test_plan_wires_import_between_register_and_consumers(tmp_path):
    ws = _ws(tmp_path)
    plan = {s.id: s for s in __import__("novel_writer.pipeline", fromlist=["build_plan"]).build_plan(ws)}
    assert plan["plot_import"].deps == ("register_plot",)
    assert plan["plot_import"].handler == "plot_import"
    assert "plot_import" in plan["simulate_blueprint"].deps


def test_import_never_overwrites_an_existing_ledger(tmp_path):
    ws = _ws(tmp_path)
    PS.write_store(ws.path("plot_items_dir"), _sample())          # 已有推进记录的账本
    atomic_write_json(ws.path("plot_register"), {"foreshadowing": [{"id": "F1", "name": "新注册"}]})
    step = {s.id: s for s in __import__("novel_writer.pipeline", fromlist=["build_plan"]).build_plan(ws)}["plot_import"]
    r = C.h_plot_import(ws, step, {})
    assert "跳过" in r["summary"]
    assert PS.read_store(ws.path("plot_items_dir"))["foreshadowing"][0]["id"] == "F10"   # 原账本未被覆盖


def test_import_asks_for_migration_when_legacy_ledger_exists(tmp_path):
    ws = _ws(tmp_path)
    atomic_write_json(ws.path("plot_legacy"), _sample())
    step = {s.id: s for s in __import__("novel_writer.pipeline", fromlist=["build_plan"]).build_plan(ws)}["plot_import"]
    with pytest.raises(C.ChapterOpError) as exc:
        C.h_plot_import(ws, step, {})
    assert "迁移" in str(exc.value)


def test_migration_writes_register_copy_so_register_step_is_not_missing(tmp_path):
    ws = _ws(tmp_path)
    atomic_write_json(ws.path("plot_legacy"), _sample())
    r = tool_api.migrate_plot_ledger(str(ws.root))
    assert r["ok"]
    assert ws.path("plot_register").is_file()
    step = {s.id: s for s in __import__("novel_writer.pipeline", fromlist=["build_plan"]).build_plan(ws)}["plot_import"]
    assert "跳过" in C.h_plot_import(ws, step, {})["summary"]    # 迁移后再导入不会覆盖
