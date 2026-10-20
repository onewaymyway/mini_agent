"""agent 步骤会改写未声明的文件（如归档顺手更新蓝图的进度）：早期规划步骤不能因此被连带重跑。"""

from __future__ import annotations

import json

from fakes import FakeInvoker, drive_to_completion, statuses, put_blueprint, read_blueprint
from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.runner import DEFAULT_PY_HANDLERS
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _archive_touches_blueprint(ws):
    bp = read_blueprint(ws)
    bp.setdefault("progress", {})["last_archived"] = "ch02"  # 归档 agent 顺手更新进度
    put_blueprint(ws, bp)


def test_undeclared_write_by_later_agent_does_not_restale_early_planning(isolated):
    ws = new_book(isolated, total=3)
    inv = FakeInvoker(ws.root, writers={"ch02.archive": _archive_touches_blueprint})
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS, limit=200)
    # 早期规划步骤只执行一次
    assert inv.calls.count("build_world") == 1
    assert inv.calls.count("register_plot") == 1 if "register_plot" in inv.calls else True
    assert st.get_step_state(ws, "build_world")["attempts"] == 1
    assert st.get_step_state(ws, "ch01.context_pack")["attempts"] == 1
    assert st.mark_stale(ws, P.build_plan(ws)) == []
    # 写入者记录确实是归档步骤
    assert st.load_state(ws)["file_writers"]["大纲规划/蓝图/_index.md"] == "ch02.archive"


def test_stale_marks_that_no_longer_hold_are_restored(isolated):
    ws = new_book(isolated, total=2)
    plan = P.build_plan(ws)
    st.update_step(ws, "build_world", status=st.ST_PASSED)
    st.mark_stale(ws, plan)  # 补录指纹
    st.update_step(ws, "build_world", status=st.ST_STALE, note="上游产物已变化，需要重跑")
    assert st.mark_stale(ws, plan) == []  # 上游未变化 → 自愈
    assert st.get_step_state(ws, "build_world")["status"] == st.ST_PASSED


def test_rebaseline_clears_confirmed_false_stale(isolated):
    ws = new_book(isolated, total=2)
    plan = P.build_plan(ws)
    st.update_step(ws, "build_world", status=st.ST_PASSED)
    st.mark_stale(ws, plan)
    bp = read_blueprint(ws)
    bp["progress"] = {"touched": True}
    put_blueprint(ws, bp)
    # 没有写入者记录时，这次变化会被当作上游变化
    assert "build_world" in st.mark_stale(ws, plan)
    assert st.rebaseline_stale(ws, plan, ["build_world"]) == ["build_world"]
    assert st.get_step_state(ws, "build_world")["status"] == st.ST_PASSED
    assert st.mark_stale(ws, plan) == []


def test_revise_cycle_writes_are_attributed_to_the_chapter_revise_loop(isolated):
    ws = new_book(isolated, total=2)
    plan = P.build_plan(ws)
    idx = P.plan_index(plan)
    assert st._writer_index("ch01.revise.r2", {k: v for k, v in idx.items()}) == idx["ch01.revise_loop"]
