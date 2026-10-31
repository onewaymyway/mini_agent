"""幕边界封存：一幕结束、下一幕开始规划时，已完成的章节不得重跑，幕末/规划步骤也不得改写它们的私有产物。"""

from __future__ import annotations

import json
import uuid

from fakes import FakeInvoker, approve_waiting, make_blueprint, put_blueprint
from novel_writer import blueprint_store as BS
from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer import tool_api
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.steps import rel
from novel_writer.worker import run_worker
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _two_act_blueprint():
    """4 章、两幕；第二幕的章节条目缺失，必须在中途由 plan_act:2 补全。"""
    bp = make_blueprint(4)
    bp["structure"]["acts"] = [{"act": 1, "chapter_range": [1, 2]}, {"act": 2, "chapter_range": [3, 4]}]
    bp["chapters"] = [c for c in bp["chapters"] if c["id"] <= 2]
    return bp


def _drive(ws, inv, limit=400):
    """以 G3 运行：记录每次因审批而停下时等待的步骤，返回 (pauses, 最终原因)。"""
    pauses = []
    reason = None
    for _ in range(limit):
        res = run_worker(ws, mode="until_gate", invoker=inv, handlers=DEFAULT_PY_HANDLERS, heartbeat_interval=0.01)
        assert res["ok"], res
        reason = res["reason"]
        if reason == "completed":
            break
        assert reason == "waiting_review", res
        pauses.append(sorted(k for k, e in st.load_state(ws)["steps"].items()
                             if e.get("status") == st.ST_WAITING_REVIEW))
        approve_waiting(ws)
    return pauses, reason


def _book(isolated):
    ws = new_book(isolated, total=4)
    put_blueprint(ws, _two_act_blueprint())
    tool_api.update_settings(str(ws.root), {"gear": "G3"})
    return ws


def test_act_end_and_next_act_planning_do_not_rerun_finished_chapters(isolated):
    ws = _book(isolated)
    inv = FakeInvoker(ws.root)
    _, reason = _drive(ws, inv)
    assert reason == "completed"
    # 第一幕的章节在整个流程中只执行一次
    for ch in ("ch01", "ch02"):
        assert [c for c in inv.calls if c.startswith(f"{ch}.")].count(f"{ch}.review") == 1
    assert st.mark_stale(ws, P.build_plan(ws)) == []


def test_act_boundary_does_not_pause_at_g3(isolated):
    """幕末审稿与逐幕规划在 G3 下自动通过，不再为每一幕停下等人。"""
    ws = _book(isolated)
    pauses, _ = _drive(ws, FakeInvoker(ws.root))
    flat = {step for group in pauses for step in group}
    assert not any(s.startswith(("act_", "plan_act:")) for s in flat), flat
    assert "final_review" in flat  # 全书终审仍由作者确认


def test_next_act_planning_cannot_write_into_sealed_chapter_artifacts(isolated):
    """plan_act:2 之类的规划步骤改写第一幕章节的审稿报告：必须被还原，且不能让第一幕重跑。"""
    ws = _book(isolated)
    review_path = ws.root / rel("review_report", nn=1)
    intruder = "规划步骤偷偷改写的内容"

    def intrude(w):
        p = w.root / rel("review_report", nn=1)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(intruder, encoding="utf-8")

    inv = FakeInvoker(ws.root, writers={"plan_act:2": intrude})
    _, reason = _drive(ws, inv)
    assert reason == "completed"
    assert review_path.read_text(encoding="utf-8") != intruder
    assert [c for c in inv.calls if c.startswith("ch01.")].count("ch01.review") == 1


def test_author_edit_to_sealed_chapter_still_propagates(isolated):
    """封存只挡系统的规划/幕末改动；作者手改已封存的章节，依赖它的步骤仍须重跑。"""
    ws = _book(isolated)
    _drive(ws, FakeInvoker(ws.root))
    from novel_writer import chapter_ops as C
    path = C.chapter_file(ws, 1)
    path.write_text(path.read_text(encoding="utf-8") + "\n作者手改的一段。", encoding="utf-8")
    assert "ch01.review" in st.mark_stale(ws, P.build_plan(ws))


def test_sealed_upto_follows_passed_act_ideas(isolated):
    ws = _book(isolated)
    plan = P.build_plan(ws)
    assert st.sealed_upto(plan, {}) == 0
    _drive(ws, FakeInvoker(ws.root))
    assert st.sealed_upto(P.build_plan(ws), st.load_state(ws)["steps"]) == 4
