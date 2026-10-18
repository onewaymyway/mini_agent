"""写入历史：每次写入都追加一条记录（谁、何时、哪个文件、前后指纹），可按文件/写入者追溯；标脏时说明原因。"""

from __future__ import annotations

from fakes import FakeInvoker, drive_to_completion
from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer import tool_api
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.runner import DEFAULT_PY_HANDLERS
from test_m3 import isolated, new_book  # noqa: F401  (fixture)

BP = "大纲规划/小说蓝图.json"


def _archive_touches_blueprint(ws):
    bp = read_json(ws.path("blueprint"), default={}) or {}
    bp.setdefault("progress", {})["ch2"] = "archived"
    atomic_write_json(ws.path("blueprint"), bp)


def test_agent_writes_are_recorded_with_before_and_after(isolated):
    ws = new_book(isolated, total=2)
    inv = FakeInvoker(ws.root, writers={"ch01.archive": _archive_touches_blueprint})
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS, limit=200)
    events = st.read_write_history(ws, path=BP, writer="ch01.archive")
    assert events, "归档对蓝图的写入应当被记录"
    ev = events[0]
    assert ev["kind"] == "agent" and ev["chapter"] == 1
    assert ev["before"] and ev["after"] and ev["before"] != ev["after"]
    assert ev["ts"]


def test_declared_outputs_are_recorded_as_declared(isolated):
    ws = new_book(isolated, total=2)
    drive_to_completion(ws, FakeInvoker(ws.root), handlers=DEFAULT_PY_HANDLERS, limit=200)
    kinds = {e["kind"] for e in st.read_write_history(ws, limit=1000)}
    assert "declared" in kinds


def test_history_is_newest_first_and_filterable(isolated):
    ws = new_book(isolated, total=2)
    drive_to_completion(ws, FakeInvoker(ws.root), handlers=DEFAULT_PY_HANDLERS, limit=200)
    all_events = st.read_write_history(ws, limit=1000)
    assert all_events[0]["ts"] >= all_events[-1]["ts"]
    res = tool_api.write_history(str(ws.root), path=BP, limit=5)
    assert res["ok"] and len(res["data"]["events"]) <= 5
    assert all(e["path"] == BP for e in res["data"]["events"])


def test_author_edit_is_recorded_and_stale_note_names_the_cause(isolated):
    ws = new_book(isolated, total=2)
    drive_to_completion(ws, FakeInvoker(ws.root), handlers=DEFAULT_PY_HANDLERS, limit=200)
    plan = P.build_plan(ws)
    bp = read_json(ws.path("blueprint"), default={}) or {}
    bp["meta"] = dict(bp.get("meta") or {}, author_note="手改")
    atomic_write_json(ws.path("blueprint"), bp)
    st.record_writer_paths(ws, [BP], "author", kind="author")
    assert "build_world" in st.mark_stale(ws, plan)
    note = st.get_step_state(ws, "build_world")["note"]
    assert "author" in note and BP in note
    assert st.read_write_history(ws, path=BP, writer="author")[0]["kind"] == "author"
