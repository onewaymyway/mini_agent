"""第三阶段：快照瘦身；草稿档伏笔同步改为增量（与精品档一致）。"""

import json

import pytest

from novel_writer import chapter_ops as C
from novel_writer import config, plot_store as PS, steps as S, tool_api
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
    r = tool_api.create_novel("瘦身", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


def test_snapshot_is_summary_not_full_copy(tmp_path):
    ws = _ws(tmp_path)
    PS.write_store(ws.path("plot_items_dir"), {"foreshadowing": [
        {"id": "F1", "name": "玉佩", "status": "planted", "plant_chapter": 1},
        {"id": "F2", "name": "信", "status": "resolved", "plant_chapter": 1, "resolve_chapter": 2},
    ]})
    atomic_write_json(ws.path("characters"), {"char_001": {"id": "char_001", "name": "甲"}})
    step = S.StepDef(id="snapshot:2", title="快照", stage="p3", category=S.CAT_INFRA,
                     executor=S.EXECUTOR_PYTHON, handler="snapshot", chapter=0, extra={"through_chapter": 2})
    C.h_snapshot(ws, step, {})
    snap = json.loads(ws.path("memory_snapshot", n=2).read_text(encoding="utf-8"))
    assert "plot_tracker" not in snap and "characters" not in snap      # 不再整份复制
    assert snap["foreshadowing"] == {"total": 2, "by_status": {"planted": 1, "resolved": 1}}
    assert snap["characters_count"] == 1


def test_draft_sync_outputs_delta_and_merge_follows(tmp_path):
    r = tool_api.create_novel("草稿", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3, quality="draft")
    ws = Workspace(r["data"]["workspace"])
    plan = {s.id: s for s in __import__("novel_writer.pipeline", fromlist=["build_plan"]).build_plan(ws)}
    sync = plan["ch01.sync"]
    assert S.rel("plot_delta", nn=1) in sync.outputs and sync.extra.get("output_check") == "plot_delta"
    assert plan["ch01.plot_merge"].deps == ("ch01.sync",)
    # 下游依赖合并步骤，而不是直接依赖同步步骤
    assert any("ch01.plot_merge" in (s.deps or ()) for s in plan.values())
