"""驾驶舱「刷新」与单步操作：需要处理的步骤必须始终可见可操作，刷新后选择框回到它。"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from novel_writer import state as st
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _approve_keys(at):
    return [b.key for b in at.button if b.key and b.key.startswith("cp_approve_")
            or (b.key and b.key.startswith("cp_top_approve_"))]


def _setup(isolated):
    ws = new_book(isolated, total=3)
    st.update_step(ws, "init_workspace", status=st.ST_PASSED)
    st.update_step(ws, "plan_blueprint", status=st.ST_WAITING_REVIEW)
    root_app = Path(__file__).resolve().parent.parent / "app.py"
    at = AppTest.from_file(str(root_app), default_timeout=60).run()
    at.session_state["ws"] = str(ws.root)
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    return ws, at


def test_pending_approval_visible_even_when_another_step_is_selected(isolated):
    ws, at = _setup(isolated)
    assert at.selectbox(key="cp_step_pick").value == "plan_blueprint"
    at.selectbox(key="cp_step_pick").set_value("init_workspace").run()
    assert "cp_top_approve_plan_blueprint" in _approve_keys(at)


def test_refresh_returns_selection_to_step_needing_action(isolated):
    ws, at = _setup(isolated)
    at.selectbox(key="cp_step_pick").set_value("init_workspace").run()
    at.button(key="cp_refresh").click().run()
    assert not at.exception, at.exception
    assert at.selectbox(key="cp_step_pick").value == "plan_blueprint"
    assert "cp_approve_plan_blueprint" in _approve_keys(at)


def test_top_approve_button_really_approves(isolated):
    ws, at = _setup(isolated)
    at.button(key="cp_top_approve_plan_blueprint").click().run()
    assert not at.exception, at.exception
    assert st.get_step_state(ws, "plan_blueprint")["status"] == st.ST_PASSED
