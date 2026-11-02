"""构思审核检查点测试：开关进入计划、闸门强制审核、清单生成、批准，以及「审核 → 修订 → 自动重新打开 → 再次批准」的循环。

全部使用桩执行器（tests/fakes.FakeInvoker）。
"""

import pytest

from novel_writer import concept_revision as CR
from novel_writer import config, gates
from novel_writer import ideation_review as IR
from novel_writer import ondemand as OD
from novel_writer import state as st
from novel_writer import steps as S
from novel_writer import tool_api
from novel_writer.pipeline import build_plan
from novel_writer.workspace import Workspace

from fakes import FakeInvoker


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _ws(tmp_path, pause=False) -> Workspace:
    r = tool_api.create_novel("构思审核", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G5", total_chapters_planned=3, pause_after_ideation=pause)
    return Workspace(r["data"]["workspace"])


def _concept_step(rid):
    return S.StepDef(id=f"cpt.{rid}", title="构思修订", stage="ondemand", category=S.CAT_REVISE,
                     executor=S.EXECUTOR_PYTHON, handler="concept", chapter=0,
                     extra={"request": f"concept_{rid}", "kind": "concept"})


def _review_ids(ws):
    return [s.id for s in build_plan(ws) if s.id == S.IDEATION_REVIEW_ID]


# ----------------------------------------------------------------- 开关与计划

def test_switch_off_keeps_plan_unchanged(tmp_path):
    ws = _ws(tmp_path, pause=False)
    assert _review_ids(ws) == []


def test_switch_on_inserts_review_after_ideation_before_chapters(tmp_path):
    ws = _ws(tmp_path, pause=True)
    plan = build_plan(ws)
    ids = [s.id for s in plan]
    assert S.IDEATION_REVIEW_ID in ids
    assert ids.index(S.IDEATION_REVIEW_ID) > ids.index("design_opening")
    import re
    chapter_like = [i for i, s in enumerate(plan) if re.match(r"^(ch\d|act_)", s.id)]
    assert chapter_like, "计划里应有章节步骤"
    assert ids.index(S.IDEATION_REVIEW_ID) < chapter_like[0]


def test_toggle_via_api_takes_effect_for_next_plan(tmp_path):
    ws = _ws(tmp_path, pause=False)
    assert tool_api.set_pause_after_ideation(str(ws.root), True)["data"]["pause_after_ideation"] is True
    assert _review_ids(ws) == [S.IDEATION_REVIEW_ID]
    tool_api.set_pause_after_ideation(str(ws.root), False)
    assert _review_ids(ws) == []


def test_gate_is_review_regardless_of_gear_but_skip_is_respected(tmp_path):
    step = S.ideation_review_step()
    settings = {"gear": "G5", "stage_gear_overrides": {}, "step_overrides": {}}
    d = gates.resolve_gate(step, settings)
    assert d.gate == gates.GATE_REVIEW and d.source == "ideation_switch"
    skipped = gates.resolve_gate(step, {**settings, "step_overrides": {S.IDEATION_REVIEW_ID: gates.GATE_SKIP}})
    assert skipped.gate == gates.GATE_SKIP


# ----------------------------------------------------------------- 清单与审批

def test_handler_writes_readable_summary_without_inventing_missing_parts(tmp_path):
    ws = _ws(tmp_path, pause=True)
    step = S.ideation_review_step()
    IR.h_ideation_review(ws, step, ws.settings())
    text = ws.path("ideation_review").read_text(encoding="utf-8")
    assert "# 构思审核清单" in text and "尚未生成主题设计" in text and "尚未建立角色档案" in text


def test_review_waits_then_approval_passes(tmp_path):
    ws = _ws(tmp_path, pause=True)
    assert IR.refresh_review(ws) is True
    assert st.get_step_state(ws, S.IDEATION_REVIEW_ID)["status"] == st.ST_WAITING_REVIEW
    r = tool_api.approve_step(str(ws.root), S.IDEATION_REVIEW_ID)
    assert r["ok"], r
    assert st.get_step_state(ws, S.IDEATION_REVIEW_ID)["status"] == st.ST_PASSED


def test_refresh_is_noop_when_switch_off(tmp_path):
    ws = _ws(tmp_path, pause=False)
    assert IR.refresh_review(ws) is False


# ----------------------------------------------------------------- 修改循环

def test_revision_after_approval_reopens_review_for_another_round(tmp_path):
    """审核 → 批准 → 构思修订 → 审核自动重新打开（waiting_review）→ 再次批准。"""
    ws = _ws(tmp_path, pause=True)
    theme = ws.path("theme_design")
    theme.parent.mkdir(parents=True, exist_ok=True)
    theme.write_text("原主题：复仇", encoding="utf-8")

    # 第一轮审核
    IR.refresh_review(ws)
    tool_api.approve_step(str(ws.root), S.IDEATION_REVIEW_ID)
    assert st.get_step_state(ws, S.IDEATION_REVIEW_ID)["status"] == st.ST_PASSED

    # 作者用「构思修订」提意见：桩执行器把主题改为救赎
    rid = OD.create_request(ws, "concept", direction="把复仇改为救赎", scopes=["theme"])["id"]
    fake = FakeInvoker(ws.root, writers={
        "theme": lambda w: w.path("theme_design").write_text("新主题：救赎", encoding="utf-8")})
    CR.h_concept(ws, _concept_step(rid), {}, invoker=fake)

    entry = st.get_step_state(ws, S.IDEATION_REVIEW_ID)
    assert entry["status"] == st.ST_WAITING_REVIEW          # 自动重新打开，等待第二轮审核
    assert "重新审核" in (entry.get("note") or "")
    assert "新主题：救赎" in ws.path("ideation_review").read_text(encoding="utf-8")  # 清单已刷新
    req = OD.load_request(ws, "concept", rid)
    assert req["result"]["review_reopened"] is True

    # 第二轮审核通过
    assert tool_api.approve_step(str(ws.root), S.IDEATION_REVIEW_ID)["ok"]
    assert st.get_step_state(ws, S.IDEATION_REVIEW_ID)["status"] == st.ST_PASSED


def test_undo_after_approval_reopens_review(tmp_path):
    ws = _ws(tmp_path, pause=True)
    theme = ws.path("theme_design")
    theme.parent.mkdir(parents=True, exist_ok=True)
    theme.write_text("原主题", encoding="utf-8")
    IR.refresh_review(ws)
    tool_api.approve_step(str(ws.root), S.IDEATION_REVIEW_ID)

    rid = OD.create_request(ws, "concept", direction="改", scopes=["theme"])["id"]
    fake = FakeInvoker(ws.root, writers={
        "theme": lambda w: w.path("theme_design").write_text("改后", encoding="utf-8")})
    CR.h_concept(ws, _concept_step(rid), {}, invoker=fake)
    tool_api.approve_step(str(ws.root), S.IDEATION_REVIEW_ID)

    CR.restore(ws, rid)                                       # 撤销也要让作者重新审核
    assert st.get_step_state(ws, S.IDEATION_REVIEW_ID)["status"] == st.ST_WAITING_REVIEW
    assert theme.read_text(encoding="utf-8") == "原主题"


def test_pipeline_frontier_stops_at_review_after_ideation(tmp_path):
    """构思步骤全部通过后，流水线前沿停在构思审核，不会直接进入章节。"""
    from novel_writer import pipeline as P
    ws = _ws(tmp_path, pause=True)
    plan = build_plan(ws)
    idx = [s.id for s in plan].index(S.IDEATION_REVIEW_ID)
    statuses = {s.id: st.ST_PASSED for s in plan[:idx]}
    settings = {"gear": "G5", "stage_gear_overrides": {}, "step_overrides": {}}
    nxt = P.frontier(plan, statuses, settings)
    assert nxt is not None and nxt.id == S.IDEATION_REVIEW_ID
