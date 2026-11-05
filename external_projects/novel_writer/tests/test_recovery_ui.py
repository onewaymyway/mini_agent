"""状态恢复面板（驾驶舱）：用 Streamlit AppTest 真正渲染并点击按钮。"""

import pytest

pytest.importorskip("streamlit.testing.v1")

from streamlit.testing.v1 import AppTest  # noqa: E402

from novel_writer import config  # noqa: E402
from novel_writer import state as st  # noqa: E402
from novel_writer import tool_api  # noqa: E402
from novel_writer.pipeline import build_plan  # noqa: E402
from novel_writer.workspace import Workspace  # noqa: E402

from fakes import FakeInvoker  # noqa: E402,F401


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _ws(tmp_path) -> Workspace:
    r = tool_api.create_novel("恢复面板", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G5", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


def _mark_stale(ws):
    plan = build_plan(ws)
    for step in plan:
        if step.chapter == 0:
            st.update_step(ws, step.id, status=st.ST_PASSED)
    st.update_step(ws, "design_pacing", status=st.ST_STALE, note=f"{st._UPSTREAM_NOTE}（测试）")
    st.arm_revision_autorun(ws, chapter=36)


SCRIPT = """
import streamlit as st
from ui import cockpit
cockpit._render_recovery(%(ws)r)
"""


def test_panel_renders_and_accept_button_clears_markers(tmp_path):
    ws = _ws(tmp_path)
    _mark_stale(ws)
    at = AppTest.from_string(SCRIPT % {"ws": str(ws.root)}).run()
    assert not at.exception, at.exception
    assert any("需重跑" in m.label for m in at.metric)
    # 未勾选确认时，「备份并清除」按钮不可用
    accept = next(b for b in at.button if b.key == "rec_accept")
    assert accept.disabled
    at.checkbox(key="rec_confirm").check().run()
    next(b for b in at.button if b.key == "rec_accept").click().run()
    assert not at.exception, at.exception
    assert st.get_step_state(ws, "design_pacing")["status"] == st.ST_PASSED
    assert st.read_control(ws)["revision_autorun"] is False
    assert any(p.name.startswith("recovery_backup_") for p in ws.studio.iterdir())


def test_flags_only_button_does_not_rebaseline(tmp_path):
    ws = _ws(tmp_path)
    _mark_stale(ws)
    at = AppTest.from_string(SCRIPT % {"ws": str(ws.root)}).run()
    next(b for b in at.button if b.key == "rec_flags").click().run()
    assert not at.exception, at.exception
    assert st.read_control(ws)["revision_autorun"] is False
    assert st.get_step_state(ws, "design_pacing")["status"] == st.ST_STALE  # 标记仍在


def test_panel_blocks_when_worker_alive(tmp_path):
    ws = _ws(tmp_path)
    _mark_stale(ws)
    st.acquire_worker(ws, mode="until_gate")  # 模拟存活的 worker
    at = AppTest.from_string(SCRIPT % {"ws": str(ws.root)}).run()
    assert not at.exception, at.exception
    assert any("worker 正在运行" in w.value for w in at.warning)
    assert next(b for b in at.button if b.key == "rec_flags").disabled


def test_jump_to_latest_button_moves_frontier(tmp_path):
    ws = _ws(tmp_path)
    st.update_step(ws, "ch01.sync", status=st.ST_PASSED)  # 最新已完成的步骤
    at = AppTest.from_string(SCRIPT % {"ws": str(ws.root)}).run()
    assert not at.exception, at.exception
    assert next(b for b in at.button if b.key == "rec_jump_go").disabled  # 未确认前不可点
    at.checkbox(key="rec_jump_confirm").check().run()
    next(b for b in at.button if b.key == "rec_jump_go").click().run()
    assert not at.exception, at.exception
    assert st.get_step_state(ws, "init_workspace")["status"] == st.ST_PASSED
    nxt = tool_api.next_step(str(ws.root))["data"]
    assert nxt["step"] == "ch01.word_quota" or nxt["step"].startswith("ch01.")
