"""伏笔增量（M9 第一阶段）：增量校验、合并、幂等重跑、损坏不静默、合并处理器只在有变化时写盘。"""

import json

import pytest

from fakes import make_blueprint  # noqa: F401  (保证测试包可导入)
from novel_writer import chapter_ops as C
from novel_writer import config, plot_delta as PD, steps as S, tool_api, validators as V
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
    r = tool_api.create_novel("伏笔增量", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=5)
    return Workspace(r["data"]["workspace"])


# ── 纯逻辑 ───────────────────────────────────────────────────────────

def test_planted_then_advanced_then_resolved_lifecycle():
    t = PD.apply_delta({"foreshadowing": []}, {"planted": [{"id": "F1", "name": "玉佩"}]}, 3)
    assert t["foreshadowing"][0]["status"] == "planted" and t["foreshadowing"][0]["plant_chapter"] == 3
    t = PD.apply_delta(t, {"advanced": [{"id": "F1", "note": "玉佩出现"}]}, 4)
    assert t["foreshadowing"][0]["status"] == "advanced"
    t = PD.apply_delta(t, {"resolved": [{"id": "F1", "note": "揭晓"}]}, 5)
    assert t["foreshadowing"][0]["status"] == "resolved" and t["foreshadowing"][0]["resolve_chapter"] == 5
    assert [p["chapter"] for p in t["foreshadowing"][0]["progress"]] == [4, 5]


def test_applying_same_delta_twice_is_idempotent():
    d = {"planted": [{"id": "F1", "name": "玉佩"}]}
    t1 = PD.apply_delta({"foreshadowing": []}, d, 3)
    t2 = PD.apply_delta(t1, d, 3)
    assert PD.canonical(t1) == PD.canonical(t2)


def test_rerunning_a_chapter_with_different_delta_replaces_previous_effect():
    t = PD.apply_delta({"foreshadowing": []}, {"planted": [{"id": "F1"}]}, 3)
    t = PD.apply_delta(t, {"advanced": [{"id": "F1", "note": "旧版本"}]}, 4)
    t = PD.apply_delta(t, {"advanced": [{"id": "F1", "note": "新版本"}]}, 4)
    assert [p["note"] for p in t["foreshadowing"][0]["progress"]] == ["新版本"]


@pytest.mark.parametrize("delta, base_items, keyword", [
    ({"planted": [{"id": "F1"}]}, [{"id": "F1", "status": "planted"}], "已存在"),
    ({"advanced": [{"id": "F9"}]}, [], "不存在"),
    ({"resolved": [{"id": "F1"}]}, [{"id": "F1", "status": "resolved"}], "已兑现"),
    ({"advanced": [{"id": "F1"}], "resolved": [{"id": "F1"}]}, [{"id": "F1", "status": "planted"}], "出现多次"),
    ({"planted": [{"name": "没有 id"}]}, [], "缺少 id"),
    ({"planted": "不是数组"}, [], "必须是数组"),
    (["不是对象"], [], "JSON 对象"),
])
def test_invalid_delta_is_rejected_with_reason(delta, base_items, keyword):
    problems = PD.delta_problems(delta, {"foreshadowing": base_items}, 4)
    assert problems and any(keyword in p for p in problems)


def test_empty_delta_is_valid_and_changes_nothing():
    base = {"foreshadowing": [{"id": "F1", "status": "planted"}]}
    assert PD.delta_problems({"planted": [], "advanced": [], "resolved": []}, base, 4) == []
    t = PD.apply_delta(base, {}, 4)
    assert t["foreshadowing"] == base["foreshadowing"]          # 条目不变（审计记录另外保存）


def test_corrupt_tracker_raises_instead_of_silently_becoming_empty(tmp_path):
    p = tmp_path / "plot_tracker.json"
    p.write_text("{不是 json", encoding="utf-8")
    with pytest.raises(PD.PlotDeltaError) as exc:
        PD.load_tracker_strict(p)
    assert "无法读取" in exc.value.problems[0]


def test_missing_tracker_is_an_empty_ledger(tmp_path):
    assert PD.load_tracker_strict(tmp_path / "none.json") == {"foreshadowing": []}


# ── 步骤与处理器 ─────────────────────────────────────────────────────

def test_merge_handler_writes_only_when_content_changes(tmp_path):
    ws = _ws(tmp_path)
    step = {s.id: s for s in __import__("novel_writer.pipeline", fromlist=["build_plan"]).build_plan(ws)}["ch03.plot_merge"]
    atomic_write_json(ws.path("plot_delta", nn=3), {"planted": [{"id": "F1", "name": "玉佩"}]})
    r1 = C.h_plot_merge(ws, step, {})
    assert "已合并" in r1["summary"] and "未写盘" not in r1["summary"]
    tracker_path = ws.path("plot_tracker")
    mtime = tracker_path.stat().st_mtime_ns
    r2 = C.h_plot_merge(ws, step, {})                       # 同一增量再跑一次：无变化
    assert "未写盘" in r2["summary"]
    assert tracker_path.stat().st_mtime_ns == mtime
    assert json.loads(tracker_path.read_text(encoding="utf-8"))["foreshadowing"][0]["id"] == "F1"


def test_merge_handler_reports_missing_delta_as_step_error(tmp_path):
    ws = _ws(tmp_path)
    step = {s.id: s for s in __import__("novel_writer.pipeline", fromlist=["build_plan"]).build_plan(ws)}["ch03.plot_merge"]
    with pytest.raises(C.ChapterOpError):
        C.h_plot_merge(ws, step, {})


def test_delta_content_check_catches_bad_reference_at_sync_plot(tmp_path):
    ws = _ws(tmp_path)
    step = {s.id: s for s in __import__("novel_writer.pipeline", fromlist=["build_plan"]).build_plan(ws)}["ch03.sync_plot"]
    atomic_write_json(ws.path("plot_delta", nn=3), {"advanced": [{"id": "F404"}]})
    problems = V.content_problems(ws, step)
    assert problems and "不存在" in problems[0]
    atomic_write_json(ws.path("plot_delta", nn=3), {"planted": [{"id": "F404", "name": "新伏笔"}]})
    assert V.content_problems(ws, step) == []


def test_downstream_steps_depend_on_merge_not_sync(tmp_path):
    ws = _ws(tmp_path)
    plan = {s.id: s for s in __import__("novel_writer.pipeline", fromlist=["build_plan"]).build_plan(ws)}
    premium_downstream = [s for s in plan.values() if "ch03.plot_merge" in (s.deps or ())]
    assert premium_downstream, "精品档中合并步骤应当是下游的依赖"
