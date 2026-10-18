"""写入历史：每次写入都追加一条记录（谁、何时、哪个文件、前后指纹），可按文件/写入者追溯；标脏时说明原因。"""

from __future__ import annotations

from fakes import FakeInvoker, drive_to_completion
from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer import tool_api
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.runner import DEFAULT_PY_HANDLERS
import pytest
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


# ── 轮转、跨分段读取、过滤与界面 ──────────────────────────────────────────────

def test_history_rotates_into_segments_and_reads_back_everything(isolated, monkeypatch):
    ws = new_book(isolated, total=2)
    monkeypatch.setattr(st, "WRITE_HISTORY_MAX_BYTES", 400)  # 很小的阈值，强制多次轮转
    for i in range(30):
        st.record_writer_paths(ws, [BP], f"ch0{i % 2 + 1}.archive", kind="agent",
                               before={BP: f"b{i}"})
    segs = sorted(p.name for p in ws.studio.iterdir() if p.name.startswith("write_history"))
    assert any(n.startswith("write_history.0") for n in segs), segs
    assert "write_history.jsonl" in segs
    events = st.read_write_history(ws, limit=1000)
    assert len(events) == 30  # 没有丢失
    assert events[0]["before"] == "b29" and events[-1]["before"] == "b0"  # 最新在前，跨分段有序


def test_filters_by_chapter_and_kind(isolated):
    ws = new_book(isolated, total=2)
    st.record_writer_paths(ws, [BP], "ch01.archive", kind="agent", before={BP: "x"})
    st.record_writer_paths(ws, [BP], "ch02.sync", kind="agent", before={BP: "y"})
    st.record_writer_paths(ws, [BP], "author", kind="author")
    assert {e["writer"] for e in st.read_write_history(ws, chapter=2)} == {"ch02.sync"}
    assert {e["kind"] for e in st.read_write_history(ws, kind="author")} == {"author"}
    res = tool_api.write_history(str(ws.root), chapter=1, limit=10)
    assert res["ok"] and all(e["chapter"] == 1 for e in res["data"]["events"])


def test_ui_write_history_panel_renders(isolated):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    ws = new_book(isolated, total=2)
    st.record_writer_paths(ws, [BP], "ch01.archive", kind="agent", before={BP: "x"}, )
    root_app = Path(__file__).resolve().parent.parent / "app.py"
    at = AppTest.from_file(str(root_app), default_timeout=60).run()
    at.session_state["ws"] = str(ws.root)
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    assert not at.exception, at.exception
    assert at.selectbox(key="wh_kind") is not None
    assert len(at.dataframe) >= 1


def test_read_only_tables_are_arrow_safe_with_mixed_types():
    """回归：列里同时有整数与空串时，Arrow 转换会失败（界面报 Serialization of dataframe 错误）。"""
    import pandas as pd
    import pyarrow as pa

    from ui.components import arrow_safe

    df = pd.DataFrame([{"章节": 1, "文件": "a"}, {"章节": "", "文件": "b"}, {"章节": None, "文件": None}])
    with pytest.raises(Exception):
        pa.Table.from_pandas(df)  # 原始数据确实无法转换
    pa.Table.from_pandas(arrow_safe(df))  # 处理后可以


def test_all_readonly_table_call_sites_use_show_df():
    from pathlib import Path

    ui = Path(__file__).resolve().parent.parent / "ui"
    offenders = [p.name for p in ui.glob("*.py") if "st.dataframe(" in p.read_text(encoding="utf-8")
                 and p.name != "components.py"]
    assert offenders == [], offenders
