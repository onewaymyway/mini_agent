"""脏标记不能制造回路：本章的状态同步改写人物/伏笔文件时，同一章的核查/准备步骤不应被判为「上游变化」。"""

from __future__ import annotations

import uuid

from fakes import FakeInvoker, drive_to_completion, statuses
from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer.runner import DEFAULT_PY_HANDLERS
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _change_characters(ws):  # 与真实代码一致：状态同步只写人物档案（声明的产出）
    ws.path("characters").write_text(str(uuid.uuid4()), encoding="utf-8")


def _change_plot(ws):  # 伏笔同步只写伏笔账本
    ws.path("plot_tracker").write_text(str(uuid.uuid4()), encoding="utf-8")


def test_sync_rewriting_state_does_not_loop_chapter(isolated):
    ws = new_book(isolated, total=3)
    inv = FakeInvoker(ws.root, writers={"ch01.sync": _change_characters, "ch01.sync_plot": _change_plot})
    last = drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS, limit=60)
    # 每个步骤在第 1 章只执行一次，不会反复回到核查/准备
    ch1 = [c for c in inv.calls if c.startswith("ch01.")]
    assert ch1.count("ch01.review") == 1
    assert ch1.count("ch01.sync") == 1
    assert st.get_step_state(ws, "ch01.context_pack")["attempts"] == 1
    assert st.get_step_state(ws, "ch01.review")["attempts"] == 1
    assert st.mark_stale(ws, P.build_plan(ws)) == []


def test_genuine_upstream_change_still_marks_downstream_stale(isolated):
    """真正的上游变化（前面章节的状态被改写）仍应让后续步骤重跑。"""
    ws = new_book(isolated, total=3)
    inv = FakeInvoker(ws.root)
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS, limit=200)
    assert st.mark_stale(ws, P.build_plan(ws)) == []
    # 作者手改了第 1 章正文：第 1 章的核查应被标为需重跑
    from novel_writer import chapter_ops as C
    path = C.chapter_file(ws, 1)
    path.write_text(path.read_text(encoding="utf-8") + "\n作者手改的一段。", encoding="utf-8")
    stale = st.mark_stale(ws, P.build_plan(ws))
    assert "ch01.review" in stale
