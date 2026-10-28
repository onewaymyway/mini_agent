"""伏笔账本自愈：agent 手写不合规文件 → 隔离/重建/转换；确定性错误不再自动重跑。全部使用桩。"""

import json

import pytest

from novel_writer import chapter_ops as C
from novel_writer import config, plot_delta as PD, plot_store as PS, runner, settings as SET, steps as S, tool_api
from novel_writer import validators as V
from novel_writer.workspace import Workspace

FREE_MD = """# fs_001 · 义眼异常代码

## 基本信息

- **ID**：fs_001
- **类型**：主线伏笔
- **状态**：planted（已埋设）
- **埋设章节**：第1章
- **计划兑现章节**：第38章（全书进度 76%）
- **紧迫程度**：non-urgent

- **depends_on**：`[]`
- **enables**：`["fs_002"]`

**埋设内容**：义眼闪过乱码
"""

REGISTER = {"foreshadowing": [
    {"id": "fs_001", "name": "义眼异常代码", "status": "planted", "plant_chapter": 1, "resolve_chapter": 38, "progress": []},
    {"id": "fs_002", "name": "父亲编程记忆", "status": "planted", "plant_chapter": 2, "resolve_chapter": 35, "progress": []},
]}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")


def _ws(tmp_path):
    r = tool_api.create_novel("自愈", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=5)
    return Workspace(r["data"]["workspace"])


def _poison(ws, register=True):
    """复现日志：agent 手写 fs_*.md 与自己风格的 _index.md，同时写了正确的 plot_register.json。"""
    base = ws.path("plot_items_dir")
    base.mkdir(parents=True, exist_ok=True)
    (base / "_index.md").write_text("# 伏笔总索引（自动生成，请勿手动编辑）\n", encoding="utf-8")
    for i in (1, 2):
        (base / f"fs_00{i}.md").write_text(FREE_MD.replace("fs_001", f"fs_00{i}"), encoding="utf-8")
    if register:
        ws.path("plot_register").parent.mkdir(parents=True, exist_ok=True)
        ws.path("plot_register").write_text(json.dumps(REGISTER, ensure_ascii=False), encoding="utf-8")
    return base


def test_agent_hand_written_index_is_detected(tmp_path):
    ws = _ws(tmp_path)
    base = _poison(ws)
    assert PS.is_store(base) and not PS.index_is_system(base)
    assert PS.store_problems(base)


def test_heal_rebuilds_from_register_when_no_progress(tmp_path):
    ws = _ws(tmp_path)
    base = _poison(ws)
    actions, left = PS.heal_store(base, ws.path("plot_register"))
    assert left == [] and any("重建" in a for a in actions)
    items = PS.read_store(base)["foreshadowing"]
    assert [i["id"] for i in items] == ["fs_001", "fs_002"] and items[0]["resolve_chapter"] == 38
    assert list(base.parent.glob("伏笔.bak_*"))            # 旧目录保留


def test_heal_never_rebuilds_over_real_progress(tmp_path):
    ws = _ws(tmp_path)
    base = ws.path("plot_items_dir")
    PS.write_store(base, {"foreshadowing": [{"id": "fs_002", "name": "已推进", "status": "advanced",
                                              "plant_chapter": 2, "resolve_chapter": 9,
                                              "progress": [{"chapter": 3, "note": "x"}]}]})
    (base / "fs_001.md").write_text(FREE_MD, encoding="utf-8")
    ws.path("plot_register").parent.mkdir(parents=True, exist_ok=True)
    ws.path("plot_register").write_text(json.dumps(REGISTER), encoding="utf-8")
    actions, left = PS.heal_store(base, ws.path("plot_register"))
    assert left == []
    got = {i["id"]: i for i in PS.read_store(base)["foreshadowing"]}
    assert got["fs_002"]["progress"] and got["fs_002"]["status"] == "advanced"   # 进展保留
    assert got["fs_001"]["plant_chapter"] == 1 and got["fs_001"]["resolve_chapter"] == 38
    assert not list(base.parent.glob("伏笔.bak_*"))                                # 没有整体重建


def test_load_ledger_self_heals_and_logs(tmp_path):
    ws = _ws(tmp_path)
    _poison(ws)
    data = V._load_ledger(ws)
    assert len(data["foreshadowing"]) == 2


def test_auto_repair_can_be_disabled(tmp_path):
    ws = _ws(tmp_path)
    _poison(ws)
    ws.save_settings({**ws.settings(), "ledger_auto_repair": False})
    with pytest.raises(PD.PlotDeltaError):
        V._load_ledger(ws)
    with pytest.raises(C.ChapterOpFatal):
        C._ledger_or_raise(ws)


def test_plot_import_repairs_instead_of_silently_skipping(tmp_path):
    ws = _ws(tmp_path)
    _poison(ws)
    step = S.plot_import_step()
    res = C.h_plot_import(ws, step, ws.settings())
    assert "自动修复" in res["summary"]
    assert not PS.store_problems(ws.path("plot_items_dir"))


def test_register_plot_check_quarantines_hand_written_files(tmp_path):
    ws = _ws(tmp_path)
    base = _poison(ws)
    problems = V._check_plot_register(ws, S.register_plot_step())
    assert problems == []                                   # 隔离是确定性修复，不让 agent 重做整步
    assert not base.exists()                                # 目录已空并移除，plot_import 将重新生成
    assert (ws.studio / "backup" / "伏笔_手写" / "fs_001.md").is_file()


def test_register_plot_check_keeps_valid_existing_ledger(tmp_path):
    ws = _ws(tmp_path)
    base = ws.path("plot_items_dir")
    PS.write_store(base, REGISTER)
    ws.path("plot_register").write_text(json.dumps(REGISTER), encoding="utf-8")
    assert V._check_plot_register(ws, S.register_plot_step()) == []
    assert len(PS.read_store(base)["foreshadowing"]) == 2


def test_wrong_register_key_gets_format_spec(tmp_path):
    ws = _ws(tmp_path)
    ws.path("plot_register").parent.mkdir(parents=True, exist_ok=True)
    ws.path("plot_register").write_text(json.dumps({"foreshadowing_registry": {"fs_001": {"id": "fs_001"}}}), encoding="utf-8")
    problems = V._check_plot_register(ws, S.register_plot_step())
    assert any("foreshadowing 数组" in p for p in problems)


def test_unrecoverable_ledger_is_fatal_no_retry(tmp_path):
    ws = _ws(tmp_path)
    base = ws.path("plot_items_dir")
    base.mkdir(parents=True, exist_ok=True)
    (base / "_index.md").write_text(PS.INDEX_HEADER + "\n", encoding="utf-8")
    (base / "fs_001.md").write_text("---\nid: {坏\n---\n", encoding="utf-8")     # 有头但 JSON 坏：无法转换，也无注册结果
    with pytest.raises(C.ChapterOpFatal) as ei:
        C._ledger_or_raise(ws)
    assert ei.value.no_retry


def test_settings_validation_and_default_on():
    assert SET.validate_settings({})["ledger_auto_repair"] is True
    with pytest.raises(SET.SettingsError):
        SET.validate_settings({"ledger_auto_repair": "yes"})


def test_utf8_bom_header_is_accepted():
    text = "﻿---\nid: \"fs_001\"\n---\n"
    assert PS._decode_fields(text, "x") == {"id": "fs_001"}
