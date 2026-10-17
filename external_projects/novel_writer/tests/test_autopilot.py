"""托管挡（G4）：自动批准、自动回答，一直运行直到暂停/停止/失败/全书完成；自动回答有上限。"""

from __future__ import annotations

from fakes import FakeInvoker, statuses
from novel_writer import autopilot as AP
from novel_writer import state as st
from novel_writer import tool_api
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import run_worker
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _run(ws, inv):
    """返回停止原因（reason 字段）。"""
    res = run_worker(ws, mode="until_gate", invoker=inv, handlers=DEFAULT_PY_HANDLERS,
                     heartbeat_interval=0.01)
    return res["reason"]


def _set_g4(ws):
    assert tool_api.update_settings(str(ws.root), {"gear": "G4"})["ok"]


def test_autopilot_runs_to_completion_approving_everything(isolated):
    ws = new_book(isolated, total=2)
    _set_g4(ws)
    # 切换到托管挡之前已经在等待批准的步骤：托管应自动批准，而不是停下
    st.update_step(ws, "init_workspace", status=st.ST_PASSED)
    st.update_step(ws, "plan_blueprint", status=st.ST_WAITING_REVIEW)
    inv = FakeInvoker(ws.root)
    result = _run(ws, inv)
    assert result == "completed", result
    s = st.load_state(ws)["steps"]
    assert s["plan_blueprint"]["status"] == st.ST_PASSED
    assert "托管" in s["plan_blueprint"].get("auto_decision", "")
    assert all(e.get("status") not in (st.ST_WAITING_REVIEW, st.ST_WAITING_USER) for e in s.values())


def test_autopilot_is_not_limited_by_budget_and_stops_on_pause(isolated):
    ws = new_book(isolated, total=3)
    _set_g4(ws)
    tool_api.update_settings(str(ws.root), {"budget": {"max_chapters_per_run": 1}})  # 普通挡会在第 1 章后停
    # 设计步骤执行时由作者发出暂停：应在当前步骤后停下，而不是按预算停
    inv = FakeInvoker(ws.root, writers={"design_theme": lambda w: st.write_control(w, command="pause")})
    assert _run(ws, inv) == "paused"
    done = [k for k, e in st.load_state(ws)["steps"].items() if e.get("status") == st.ST_PASSED]
    assert "design_theme" in done and "ch02.sync" not in done


def test_autopilot_stops_after_max_auto_answers_on_persistent_escalation(isolated):
    """修改循环每次都解决不了：自动回答达到上限后必须停下，不能无限循环烧 LLM 调用。"""
    ws = new_book(isolated, total=2)
    _set_g4(ws)
    inv = FakeInvoker(ws.root, red=[1])  # 第一章始终有 🔴
    result = _run(ws, inv)
    assert result == st.ST_WAITING_USER, result
    entry = st.get_step_state(ws, "ch01.revise_loop")
    assert entry["status"] == st.ST_WAITING_USER
    assert entry["auto_answers"] == AP.MAX_AUTO_ANSWERS


def test_default_answers_pick_first_option_and_free_text_default():
    qs = [{"id": "d", "question": "选哪个", "options": ["甲", "乙"]},
          {"id": "t", "question": "补充说明"}]
    answers = AP.default_answers(qs)
    assert answers["d"] == "甲"
    assert answers["t"] == AP.FREE_TEXT_DEFAULT


def test_manual_gate_and_revise_stay_manual_in_autopilot(isolated):
    from novel_writer import gates as G
    from novel_writer import steps as S
    ws = new_book(isolated, total=2)
    settings = {"gear": "G4"}
    rev = S.StepDef(id="rev.x", title="t", stage="ondemand", category=S.CAT_REVISE,
                    executor=S.EXECUTOR_SKILL, skill="chapter-writer", chapter=1)
    assert G.resolve_gate(rev, settings).gate == G.GATE_MANUAL
    plan = {"gear": "G4", "step_overrides": {"plan_blueprint": "manual"}}
    from novel_writer import pipeline as P
    bp = [s for s in P.build_plan(ws) if s.id == "plan_blueprint"][0]
    assert G.resolve_gate(bp, plan).gate == G.GATE_MANUAL
