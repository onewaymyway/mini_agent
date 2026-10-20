"""蓝图与伏笔账本的内容校验（M8）：格式错误在产出步骤本身被拦下，不静默地让下游失效。"""

import json

import pytest

from novel_writer import config, pipeline as P, steps as S, tool_api, validators as V
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
    r = tool_api.create_novel("结构校验", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        atomic_write_json(path, data)


# ── 蓝图 ─────────────────────────────────────────────────────────────

def test_blueprint_with_int_ids_passes_untouched(tmp_path):
    ws = _ws(tmp_path)
    p = ws.path("blueprint"); _write(p, {"chapters": [{"id": 1, "word_budget": 2000}, {"id": 2}]})
    before = p.read_text(encoding="utf-8")
    assert V.blueprint_problems(p) == []
    assert p.read_text(encoding="utf-8") == before


def test_blueprint_string_chapter_ids_are_normalized(tmp_path):
    ws = _ws(tmp_path)
    p = ws.path("blueprint")
    _write(p, {"chapters": [{"id": "第1章", "word_budget": "2000"}, {"id": "2"}]})
    assert V.blueprint_problems(p) == []
    data = json.loads(p.read_text(encoding="utf-8"))
    assert [c["id"] for c in data["chapters"]] == [1, 2]
    assert data["chapters"][0]["word_budget"] == 2000


@pytest.mark.parametrize("data, keyword", [
    ({"chapters": []}, "非空"),
    ({"outline": []}, "chapters"),
    ({"chapters": [{"id": "序章"}]}, "章号整数"),
    ({"chapters": [{"id": 1}, {"id": 1}]}, "重复"),
    ({"chapters": [{"id": 1, "word_budget": "很多"}]}, "word_budget"),
    ({"chapters": ["第一章"]}, "必须是对象"),
])
def test_blueprint_problems_are_reported(tmp_path, data, keyword):
    ws = _ws(tmp_path)
    p = ws.path("blueprint"); _write(p, data)
    problems = V.blueprint_problems(p)
    assert problems and any(keyword in x for x in problems)


def test_blueprint_invalid_json_is_reported(tmp_path):
    ws = _ws(tmp_path)
    p = ws.path("blueprint"); _write(p, "不是 JSON")
    assert V.blueprint_problems(p)


# ── 伏笔账本 ─────────────────────────────────────────────────────────

def test_plot_tracker_valid_passes(tmp_path):
    ws = _ws(tmp_path)
    p = ws.path("plot_tracker")
    _write(p, {"foreshadowing": [{"id": "F1", "plant_chapter": 1, "resolve_chapter": 3}]})
    assert V.plot_tracker_problems(p) == []


def test_plot_tracker_string_chapters_are_normalized(tmp_path):
    ws = _ws(tmp_path)
    p = ws.path("plot_tracker")
    _write(p, {"threads": [{"id": "F1", "plant_chapter": "第1章", "payoff_chapter": "3"}]})
    assert V.plot_tracker_problems(p) == []
    item = json.loads(p.read_text(encoding="utf-8"))["threads"][0]
    assert item["plant_chapter"] == 1 and item["payoff_chapter"] == 3


@pytest.mark.parametrize("data, keyword", [
    ({"notes": []}, "伏笔列表"),
    ({"foreshadowing": []}, "为空"),
    ({"foreshadowing": [{"name": "没有id"}]}, "缺少 id"),
    ({"foreshadowing": [{"id": "F1", "plant_chapter": "早期"}]}, "章号整数"),
    ({"foreshadowing": ["F1"]}, "必须是对象"),
])
def test_plot_tracker_problems_are_reported(tmp_path, data, keyword):
    ws = _ws(tmp_path)
    p = ws.path("plot_tracker"); _write(p, data)
    problems = V.plot_tracker_problems(p)
    assert problems and any(keyword in x for x in problems)


# ── 步骤挂接 ─────────────────────────────────────────────────────────

def test_structured_steps_declare_their_checks(tmp_path):
    ws = _ws(tmp_path)
    plan = {s.id: s for s in P.build_plan(ws)}
    assert plan["plan_blueprint"].extra.get("output_check") == "blueprint"
    assert plan["register_plot"].extra.get("output_check") == "plot_tracker"
    assert plan["ch01.sync_plot"].extra.get("output_check") == "plot_delta"   # sync_plot 只产出增量
    assert plan["ch01.plot_merge"].handler == "plot_merge"
    assert S.rel("plot_tracker") in plan["ch01.plot_merge"].outputs


def test_content_problems_dispatch_through_registry(tmp_path):
    ws = _ws(tmp_path)
    _write(ws.path("blueprint"), {"chapters": [{"id": "序章"}]})
    step = {s.id: s for s in P.build_plan(ws)}["plan_blueprint"]
    assert V.content_problems(ws, step)
    _write(ws.path("blueprint"), {"chapters": [{"id": 1}]})
    assert V.content_problems(ws, step) == []


def test_unknown_check_name_is_an_error_not_silent(tmp_path):
    ws = _ws(tmp_path)
    step = S.StepDef(id="x", title="x", stage="p1", category=S.CAT_BUILD, executor=S.EXECUTOR_PYTHON,
                     extra={"output_check": "不存在的检查"})
    with pytest.raises(KeyError):
        V.content_problems(ws, step)
