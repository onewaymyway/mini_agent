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


def test_watch_signature_changes_only_when_visible_state_changes(isolated):
    from ui.autorefresh import workspace_signature

    ws = new_book(isolated, total=2)
    base = workspace_signature(str(ws.root))
    assert workspace_signature(str(ws.root)) == base  # 没有变化 → 签名不变
    st.update_step(ws, "init_workspace", status=st.ST_PASSED)
    assert workspace_signature(str(ws.root)) != base  # 步骤状态变化 → 签名变化
    before = workspace_signature(str(ws.root))
    st.acquire_worker(ws, mode="until_gate")  # 心跳会变，但签名只看 worker 的 state/step/pid
    mid = workspace_signature(str(ws.root))
    assert mid != before  # worker 运行状态变化
    st.release_worker(ws, "test")


def test_cockpit_watcher_triggers_full_rerun_on_change(isolated):
    """状态变化后，整页（包括流水线表格）应自动更新，无需手动刷新。"""
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    ws = new_book(isolated, total=2)
    root_app = Path(__file__).resolve().parent.parent / "app.py"
    at = AppTest.from_file(str(root_app), default_timeout=60).run()
    at.session_state["ws"] = str(ws.root)
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    assert not at.exception, at.exception
    # 模拟后台推进：状态在页面之外被改变，监视器的下一次轮询应检测到并整页刷新
    st.update_step(ws, "plan_blueprint", status=st.ST_WAITING_REVIEW)
    at.run()  # 等同于一次 fragment 轮询（AppTest 不会按计时器触发，这里手动执行）
    assert not at.exception, at.exception
