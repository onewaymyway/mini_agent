"""逐幕滚动规划 + 幕末报告反馈：蓝图只细化第一幕时，进入下一幕前自动补全；全部使用桩。"""

from __future__ import annotations

import json

import pytest

from fakes import STUB_HANDLERS, FakeInvoker, approve_waiting, make_blueprint, put_blueprint, set_settings
from novel_writer import act_planning as AP
from novel_writer import chapter_ops as C
from novel_writer import config, pipeline as P, settings as SET, steps as S, tool_api, validators as V
from novel_writer import patrol_findings as PF
from novel_writer import state as st
from novel_writer.worker import run_worker
from novel_writer.workspace import Workspace

ACTS = [{"act": 1, "chapter_range": [1, 3], "word_budget": 12000},
        {"act": 2, "chapter_range": [4, 6], "word_budget": 12000}]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")


def book(tmp_path, *, written=3, total=6, gear="G4"):
    """规划了 total 章，但蓝图只写出前 written 章条目（复现 50 章只细化了第一幕）。"""
    r = tool_api.create_novel("滚动规划", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear=gear, total_chapters_planned=total)
    ws = Workspace(r["data"]["workspace"])
    bp = make_blueprint(written)
    bp["structure"] = {"total_chapters": total, "acts": ACTS}
    bp["meta"] = {"target_length": total * 4000}
    for ch in bp["chapters"]:
        ch["pov"] = "林远（主角视角）"
        ch["characters_present"] = ["林远"]
    put_blueprint(ws, bp)
    return ws


def ids(ws):
    return [s.id for s in P.build_plan(ws)]


def test_missing_act_gets_plan_and_extend_before_its_first_chapter(tmp_path):
    ws = book(tmp_path)
    i = ids(ws)
    assert "plan_act:1" not in i                                   # 第一幕已完整，不插入
    assert i.index("act_review:1") < i.index("plan_act:2") < i.index("blueprint_extend:2") < i.index("ch04.word_quota")
    assert i.index("act_apply:1") < i.index("plan_act:2")           # 幕末整改项先落实，再规划下一幕


def test_no_insertion_when_blueprint_complete_or_disabled(tmp_path):
    ws = book(tmp_path, written=6)
    assert not [x for x in ids(ws) if x.startswith(("plan_act", "blueprint_extend"))]
    ws2 = book(tmp_path / "b", written=3)
    set_settings(ws2, plan_act_auto=False)
    assert not [x for x in ids(ws2) if x.startswith(("plan_act", "blueprint_extend"))]


def test_plan_act_reads_previous_act_reports_and_pins_protagonist_name(tmp_path):
    ws = book(tmp_path)
    step = {s.id: s for s in P.build_plan(ws)}["plan_act:2"]
    for key in ("第1幕_健康报告.md", "第1幕_节奏.md", "第1幕_主题.md", "第1幕_风格对比.md"):
        assert key in step.instructions
    assert "主角名以规划为准" in step.instructions and "「林远」" in step.instructions
    extract = {s.id: s for s in P.build_plan(ws)}["act_extract:1"]
    assert "「林远」" in extract.instructions and "retro" in extract.instructions


def test_canonical_protagonist_prefers_character_archive(tmp_path):
    ws = book(tmp_path)
    assert AP.canonical_protagonist(ws, {"chapters": [{"id": 1, "pov": "李凌云（主角视角）"}]}) == "李凌云"
    assert AP.canonical_protagonist(ws, {"chapters": [{"id": 1, "characters_present": ["甲", "乙", "乙"]}]}) == "乙"


def _draft(ws, act, ids_, budget=4000, **extra):
    p = ws.path("act_blueprint_draft", act=act)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"chapters": [{"id": n, "title": f"t{n}", "word_budget": budget, **extra} for n in ids_]},
                            ensure_ascii=False), encoding="utf-8")


def _step(ws, act=2):
    return {s.id: s for s in P.build_plan(ws)}[f"plan_act:{act}"]


def test_draft_validation(tmp_path):
    ws = book(tmp_path)
    step = _step(ws)
    assert any("缺少本幕章节草稿" in p for p in AP.draft_problems(ws, step))
    _draft(ws, 2, [4, 5])
    assert any("缺少这些章" in p for p in AP.draft_problems(ws, step))
    _draft(ws, 2, [4, 5, 6, 7])
    assert any("不在第 2 幕范围" in p for p in AP.draft_problems(ws, step))
    _draft(ws, 2, [4, 5, 6], budget=9000)
    assert any("相差超过" in p for p in AP.draft_problems(ws, step))
    _draft(ws, 2, [4, 5, 6], foreshadowing=[{"id": "fs_9", "planned_reveal_chapter": 99}])
    assert any("超过全书总章数" in p for p in AP.draft_problems(ws, step))
    _draft(ws, 2, ["第4章", "5", 6])
    assert AP.draft_problems(ws, step) == []                         # 章号写法被规范为整数


def test_extend_merges_without_touching_existing_and_updates_quota(tmp_path):
    ws = book(tmp_path)
    step = _step(ws)
    before = {c["id"]: c for c in tool_json_chapters(ws)}
    _draft(ws, 2, [4, 5, 6])
    res = AP.h_blueprint_extend(ws, step, {})
    assert "追加 3 章" in res["summary"]
    after = {c["id"]: c for c in tool_json_chapters(ws)}
    assert sorted(after) == [1, 2, 3, 4, 5, 6]
    assert all(after[k] == before[k] for k in before)                 # 已有章节原样保留
    assert C._base_quota(ws, 5) == 4000
    again = AP.h_blueprint_extend(ws, step, {})
    assert "无变化" in again["summary"]


def tool_json_chapters(ws):
    from novel_writer import blueprint_store as BS
    return BS.load_for_ws(ws, strict=True)["chapters"]


def test_missing_chapter_entry_is_fatal_with_guidance(tmp_path):
    ws = book(tmp_path)
    step = {s.id: s for s in P.build_plan(ws)}["ch04.word_quota"]
    with pytest.raises(C.ChapterOpFatal) as ei:
        C.h_word_quota(ws, step, {})
    assert "没有蓝图条目" in str(ei.value) and "plan_act_auto" in str(ei.value)


def test_blueprint_must_cover_first_act(tmp_path):
    p = tmp_path / "bp.json"
    bp = {"structure": {"total_chapters": 6, "acts": ACTS}, "chapters": [{"id": 1}, {"id": 2}]}
    p.write_text(json.dumps(bp), encoding="utf-8")
    probs = V.blueprint_problems(p)
    assert probs and "第一幕" in probs[0] and "3" in probs[0]
    bp["chapters"].append({"id": 3})
    p.write_text(json.dumps(bp), encoding="utf-8")
    assert V.blueprint_problems(p) == []                             # 后幕缺章不拦
    p.write_text(json.dumps({"chapters": [{"id": 1}]}), encoding="utf-8")
    assert V.blueprint_problems(p) == []                             # 无幕结构不检查


def test_act_findings_share_ledger_without_clobbering_patrol(tmp_path):
    ws = book(tmp_path)
    one = {"findings": [{"dimension": "人物", "severity": "严重", "title": "主角名统一", "directive": "统一为林远", "scope": "forward"}]}
    PF.apply_extract(ws, 3, one, True, source="act:1")
    PF.apply_extract(ws, 3, {"findings": [{"dimension": "节奏", "severity": "一般", "title": "放缓", "directive": "x", "scope": "forward"}]}, True)
    led = PF.read_ledger(ws)["findings"]
    assert {f["id"] for f in led} == {"A1-1", "P3-1"}                 # 同一章号，来源不同，互不覆盖
    assert [f["title"] for f in PF.active_for_chapter(ws, 4, PF.SCOPE_FORWARD)] == ["主角名统一", "放缓"]
    PF.apply_extract(ws, 3, one, True, source="act:1")                # 幂等
    assert len(PF.read_ledger(ws)["findings"]) == 2


def test_end_to_end_stuck_book_now_proceeds(tmp_path):
    """复现原故障：规划 6 章、蓝图只有前 3 章。流水线应在第 4 章前自动规划第二幕并继续。"""
    ws = book(tmp_path)
    inv = FakeInvoker(ws.root)
    for _ in range(80):
        r = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
        assert r["ok"], r
        if r["reason"] == "completed":
            break
        assert r["reason"] == "waiting_review", r
        approve_waiting(ws)
    else:
        raise AssertionError("未完成")
    s = {x.id: st.get_step_state(ws, x.id)["status"] for x in P.build_plan(ws)}
    assert s["plan_act:2"] == st.ST_PASSED and s["blueprint_extend:2"] == st.ST_PASSED
    assert s["ch06.word_quota"] == st.ST_PASSED
    assert "plan_act:2" in inv.calls and "act_extract:1" in inv.calls
    # 蓝图补全后，步骤仍留在计划里；已通过的前序步骤没有被当作「上游变化」标脏
    assert "plan_act:2" in ids(ws)
    assert not [k for k, v in st.load_state(ws)["steps"].items() if v.get("status") == st.ST_STALE]


def test_settings_validation():
    assert SET.validate_settings({})["plan_act_auto"] is True
    with pytest.raises(SET.SettingsError):
        SET.validate_settings({"plan_act_auto": 1})
