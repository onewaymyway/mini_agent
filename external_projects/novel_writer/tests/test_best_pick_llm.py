"""精品档择优改为 LLM 判断：准备（机检）→ 择优（chapter-picker）→ 应用（按报告决定正文）。"""

from __future__ import annotations

import json

import pytest

from fakes import FakeInvoker
from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer import steps as S
from novel_writer import tool_api
from novel_writer.fsutil import atomic_write_json
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import run_worker
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _premium_book(isolated, total=2):
    ws = new_book(isolated, total=total)
    assert tool_api.update_settings(str(ws.root), {"quality": "premium", "gear": "G4"})["ok"]  # 托管：自动通过开篇审批
    return ws


def _picker(winner, reason="设定与伏笔更忠实"):
    def write(ws):
        atomic_write_json(ws.path("best_pick_report", nn=1), {
            "chapter": 1, "winner": winner, "reason": reason,
            "A": {"strengths": [], "problems": []}, "B": {"strengths": [], "problems": []},
            "decisive_factor": "设定与伏笔",
        })
    return write


def _run(ws, inv, mode="until_gate"):
    return run_worker(ws, mode=mode, invoker=inv, handlers=DEFAULT_PY_HANDLERS, heartbeat_interval=0.01)


def test_premium_plan_has_prep_llm_pick_and_apply_in_order(isolated):
    ws = _premium_book(isolated)
    ids = [s.id for s in P.build_plan(ws)]
    i = ids.index("ch01.alt_write")
    assert ids[i:i + 4] == ["ch01.alt_write", "ch01.best_pick_prep", "ch01.best_pick", "ch01.best_pick_apply"]
    pick = next(s for s in P.build_plan(ws) if s.id == "ch01.best_pick")
    assert pick.executor == S.EXECUTOR_SKILL and pick.skill == "chapter-picker"
    mc = next(s for s in P.build_plan(ws) if s.id == "ch01.machine_check")
    assert "ch01.best_pick_apply" in mc.deps


def test_llm_choosing_b_replaces_body_and_reason_is_logged(isolated):
    ws = _premium_book(isolated)
    inv = FakeInvoker(ws.root, writers={"ch01.best_pick": _picker("B", "B 版的伏笔埋设更自然")})
    _run(ws, inv)
    assert st.get_step_state(ws, "ch01.best_pick")["status"] == st.ST_PASSED
    assert "B 版的伏笔埋设更自然" in ws.path("studio_log").read_text(encoding="utf-8")
    assert st.get_step_state(ws, "ch01.best_pick_apply")["status"] == st.ST_PASSED


def test_llm_choosing_a_keeps_body(isolated):
    ws = _premium_book(isolated)
    inv = FakeInvoker(ws.root, writers={"ch01.best_pick": _picker("A")})
    _run(ws, inv)
    assert st.get_step_state(ws, "ch01.best_pick_apply")["status"] == st.ST_PASSED
    assert "采用A稿" in st.get_step_state(ws, "ch01.best_pick_apply")["summary"]


def test_missing_or_invalid_report_fails_instead_of_guessing(isolated):
    ws = _premium_book(isolated)
    inv = FakeInvoker(ws.root, writers={"ch01.best_pick": lambda w: atomic_write_json(
        w.path("best_pick_report", nn=1), {"winner": "C"})})
    _run(ws, inv)
    e = st.get_step_state(ws, "ch01.best_pick_apply")
    assert e["status"] in (st.ST_FAILED, st.ST_BLOCKED) and "winner" in (e.get("error") or "")


def test_legacy_completed_pick_is_adopted_not_redone(isolated):
    """旧版本择优已完成：新增的准备/应用步骤应视为已完成，不能重新择优改写已定稿的正文。"""
    ws = _premium_book(isolated)
    st.update_step(ws, "ch01.best_pick", status=st.ST_PASSED)  # 旧版本只有这一步
    adopted = st.adopt_legacy_picks(ws)
    assert set(adopted) == {"ch01.best_pick_prep", "ch01.best_pick_apply"}
    assert st.get_step_state(ws, "ch01.best_pick_apply")["status"] == st.ST_PASSED
    inv = FakeInvoker(ws.root)
    _run(ws, inv)
    assert not any(c.startswith("ch01.best_pick") for c in inv.calls)
