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


def _run_to_gate(ws, invoker=None):
    from novel_writer.worker import run_worker
    from novel_writer.runner import DEFAULT_PY_HANDLERS
    inv = invoker or FakeInvoker(ws.root)
    return run_worker(ws, mode="until_gate", invoker=inv, handlers=DEFAULT_PY_HANDLERS,
                      heartbeat_interval=0.01), inv


def test_autopilot_run_stops_at_review_and_never_auto_approves_it(tmp_path):
    """托管挡（G5）下，构思完成后仍停在构思审核，不会被自动批准。"""
    ws = _ws(tmp_path, pause=True)
    _run_to_gate(ws)
    entry = st.get_step_state(ws, S.IDEATION_REVIEW_ID)
    assert entry["status"] == st.ST_WAITING_REVIEW
    assert entry.get("auto_decision") != "托管：自动批准"
    assert "已批准" not in st.read_log_tail(ws) if hasattr(st, "read_log_tail") else True


def test_review_approval_passes_and_clears_flag(tmp_path):
    ws = _ws(tmp_path, pause=True)
    _run_to_gate(ws)
    assert tool_api.approve_step(str(ws.root), S.IDEATION_REVIEW_ID)["ok"]
    assert st.get_step_state(ws, S.IDEATION_REVIEW_ID)["status"] == st.ST_PASSED


def test_reopen_is_noop_when_switch_off(tmp_path):
    ws = _ws(tmp_path, pause=False)
    assert IR.reopen_after_revision(ws) is False
    assert st.read_control(ws).get("ideation_autorun") is False


def test_revision_after_approval_auto_runs_back_to_review(tmp_path):
    """审核 → 批准 → 构思修订 → 自动续跑到构思审核（不逐步确认）→ 再次批准。"""
    ws = _ws(tmp_path, pause=True)
    _run_to_gate(ws)
    tool_api.approve_step(str(ws.root), S.IDEATION_REVIEW_ID)

    rid = OD.create_request(ws, "concept", direction="把复仇改为救赎", scopes=["theme"])["id"]
    fake = FakeInvoker(ws.root, writers={
        "theme": lambda w: w.path("theme_design").write_text("新主题：救赎", encoding="utf-8")})
    CR.h_concept(ws, _concept_step(rid), {}, invoker=fake)

    # 修订后：审核标为需重跑，并置位自动续跑（不会直接停在等待）
    assert st.get_step_state(ws, S.IDEATION_REVIEW_ID)["status"] == st.ST_STALE
    assert st.read_control(ws)["ideation_autorun"] is True
    assert OD.load_request(ws, "concept", rid)["result"]["auto_rerun_to_review"] is True

    # 运行到下一个检查点：自动跑完构思步骤，停在构思审核
    _run_to_gate(ws, invoker=fake)
    assert st.get_step_state(ws, S.IDEATION_REVIEW_ID)["status"] == st.ST_WAITING_REVIEW
    assert "新主题：救赎" in ws.path("ideation_review").read_text(encoding="utf-8")
    assert st.read_control(ws)["ideation_autorun"] is False     # 续跑结束，标记清除

    assert tool_api.approve_step(str(ws.root), S.IDEATION_REVIEW_ID)["ok"]


def test_undo_also_auto_runs_back_to_review(tmp_path):
    ws = _ws(tmp_path, pause=True)
    _run_to_gate(ws)
    tool_api.approve_step(str(ws.root), S.IDEATION_REVIEW_ID)

    rid = OD.create_request(ws, "concept", direction="改", scopes=["theme"])["id"]
    fake = FakeInvoker(ws.root, writers={
        "theme": lambda w: w.path("theme_design").write_text("改后", encoding="utf-8")})
    CR.h_concept(ws, _concept_step(rid), {}, invoker=fake)
    _run_to_gate(ws, invoker=fake)
    tool_api.approve_step(str(ws.root), S.IDEATION_REVIEW_ID)

    CR.restore(ws, rid)
    assert st.get_step_state(ws, S.IDEATION_REVIEW_ID)["status"] == st.ST_STALE
    assert st.read_control(ws)["ideation_autorun"] is True
    _run_to_gate(ws, invoker=fake)
    assert st.get_step_state(ws, S.IDEATION_REVIEW_ID)["status"] == st.ST_WAITING_REVIEW


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


@pytest.mark.parametrize("gear", ["G5", "G0"])
def test_stale_ideation_step_runs_without_confirmation_until_review(tmp_path, gear):
    """回归：修订后，依赖上游变化的构思步骤（如节奏设计）自动重跑，不弹「需重跑确认」，也不停在手动闸门。

    前置状态直接构造（审核之前的步骤全部已通过），因为在 G0 下蓝图确认本身就是检查点，无法靠运行走到审核。
    """
    from novel_writer.worker import run_worker
    from novel_writer.runner import DEFAULT_PY_HANDLERS
    ws = _ws(tmp_path, pause=True)
    ws.save_settings({**ws.settings(), "gear": gear})
    plan = build_plan(ws)
    review_at = [s.id for s in plan].index(S.IDEATION_REVIEW_ID)
    for step in plan[:review_at]:
        st.update_step(ws, step.id, status=st.ST_PASSED)
    # 模拟：节奏设计的上游（蓝图）已变化，节奏设计被标为需重跑；审核也需要重跑；构思自动续跑已安排
    st.update_step(ws, "design_pacing", status=st.ST_STALE, note=f"{st._UPSTREAM_NOTE}（测试）")
    st.update_step(ws, S.IDEATION_REVIEW_ID, status=st.ST_STALE, note="构思已修订")
    st.write_control(ws, ideation_autorun=True)

    res = run_worker(ws, mode="until_gate", invoker=FakeInvoker(ws.root), handlers=DEFAULT_PY_HANDLERS,
                     heartbeat_interval=0.01)
    assert res["reason"] != "stale_confirm", res
    assert st.get_step_state(ws, "design_pacing")["status"] == st.ST_PASSED, res
    assert st.get_step_state(ws, S.IDEATION_REVIEW_ID)["status"] == st.ST_WAITING_REVIEW, res
