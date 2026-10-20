"""写作之后的更新是为后续准备，不回溯已完成的章节与设计；作者手动修改才是真正的上游变化。"""

from __future__ import annotations

from fakes import FakeInvoker, drive_to_completion
from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.runner import DEFAULT_PY_HANDLERS
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _chapter_writes_planning(ws):
    """第 2 章归档/同步时更新蓝图、人物与伏笔（为后续章节准备）。"""
    bp = read_json(ws.path("blueprint"), default={}) or {}
    bp.setdefault("progress", {})["after_ch2"] = True
    atomic_write_json(ws.path("blueprint"), bp)
    atomic_write_json(ws.path("characters"), {"after_ch2": True})
    atomic_write_json(ws.path("plot_tracker"), {"foreshadowing": [{"id": "F1"}], "after_ch2": True})


def _finished_book(isolated, writers=None):
    ws = new_book(isolated, total=3)
    inv = FakeInvoker(ws.root, writers=writers or {})
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS, limit=300)
    return ws, inv


def test_chapter_writes_to_planning_files_do_not_touch_finished_work(isolated):
    ws, _ = _finished_book(isolated, writers={"ch02.archive": _chapter_writes_planning,
                                              "ch02.sync": _chapter_writes_planning})
    before = {k: v["status"] for k, v in st.load_state(ws)["steps"].items() if k.startswith(("ch01", "ch02", "design", "build", "register", "init", "plan"))}
    assert st.mark_stale(ws, P.build_plan(ws)) == []
    after = {k: v["status"] for k, v in st.load_state(ws)["steps"].items() if k in before}
    assert before == after


def test_author_edit_to_blueprint_is_a_real_upstream_change(isolated):
    ws, _ = _finished_book(isolated)
    plan = P.build_plan(ws)
    bp = read_json(ws.path("blueprint"), default={}) or {}
    bp["meta"] = dict(bp.get("meta") or {}, author_note="作者手改")
    atomic_write_json(ws.path("blueprint"), bp)
    st.record_writer_paths(ws, [ws.path("blueprint").relative_to(ws.root).as_posix()], "author")
    stale = st.mark_stale(ws, plan)
    assert "build_world" in stale and "ch01.context_pack" in stale


def test_same_chapter_revision_still_restales_its_own_archive(isolated):
    ws, _ = _finished_book(isolated)
    plan = P.build_plan(ws)
    from novel_writer import chapter_ops as C
    path = C.chapter_file(ws, 1)
    st.record_writer_paths(ws, [path.relative_to(ws.root).as_posix()], "ch01.revise.r1")
    path.write_text(path.read_text(encoding="utf-8") + "\n修改后的段落。", encoding="utf-8")
    stale = st.mark_stale(ws, plan)
    assert "ch01.archive" in stale
    assert not any(x.startswith("ch02.") for x in stale), stale
