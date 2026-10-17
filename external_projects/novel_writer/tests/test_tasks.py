"""后台任务总览与管理：列出运行中/残留/排队/待运行请求，并支持停止、取消排队、清除残留、删除请求。"""

from __future__ import annotations

import pytest

from fakes import FakeInvoker
from novel_writer import config, tool_api
from novel_writer.fsutil import atomic_write_json
from novel_writer import state as st
from novel_writer.workspace import Workspace
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _list(kind=None):
    rows = tool_api.list_tasks(novels_dir=str(config.NOVELS_DIR),
                               index_file=str(config.SHELF_INDEX_FILE))["data"]["tasks"]
    return [r for r in rows if kind is None or r["kind"] == kind]


def _make_stale(ws):
    st.acquire_worker(ws, mode="until_gate")
    info = st.read_worker(ws)
    info["heartbeat"] = "2000-01-01T00:00:00+00:00"  # 心跳远超超时
    atomic_write_json(ws.path("studio_worker"), info)


def test_running_worker_is_listed_and_can_be_stopped(isolated):
    ws = new_book(isolated, total=2)
    assert st.acquire_worker(ws, mode="until_gate")
    rows = _list("running")
    assert len(rows) == 1 and rows[0]["workspace"] == str(ws.root)
    assert tool_api.stop_novel(str(ws.root))["ok"]
    assert st.read_control(ws)["command"] == "stop"
    st.release_worker(ws, "test")


def test_live_worker_cannot_be_cleared(isolated):
    ws = new_book(isolated, total=2)
    st.acquire_worker(ws, mode="until_gate")
    res = tool_api.clear_stale_worker(str(ws.root))
    assert not res["ok"]
    st.release_worker(ws, "test")


def test_stale_worker_is_listed_and_can_be_cleared(isolated):
    ws = new_book(isolated, total=2)
    _make_stale(ws)
    assert _list("stale")
    assert tool_api.clear_stale_worker(str(ws.root))["ok"]
    assert st.read_worker(ws)["state"] == "stopped"
    assert not _list("stale")


def test_queued_run_request_is_listed_and_can_be_cancelled(isolated):
    ws = new_book(isolated, total=2)
    assert tool_api.run_novel(str(ws.root), mode="single", step_id="ch01.review", spawn=False)["ok"]
    rows = _list("queued")
    assert rows and rows[0]["step"] == "ch01.review"
    assert tool_api.cancel_queued(str(ws.root))["ok"]
    assert not _list("queued")
    assert not tool_api.cancel_queued(str(ws.root))["ok"]


def test_pending_request_is_listed_and_can_be_deleted(isolated):
    ws = new_book(isolated, total=2)
    rid = tool_api.request_revision_cycle(str(ws.root), 1, "改一下")["data"]["id"]
    rows = _list("request")
    assert rows and rows[0]["request_id"] == rid
    assert tool_api.delete_request(str(ws.root), "revision", rid)["ok"]
    assert not _list("request")
    assert not ws.path("studio_state").read_text(encoding="utf-8").count(f"rev.{rid}")


def test_running_request_cannot_be_deleted(isolated):
    ws = new_book(isolated, total=2)
    rid = tool_api.request_revision_cycle(str(ws.root), 1, "改一下")["data"]["id"]
    sid = f"rev.{rid}"
    st.update_step(ws, sid, status=st.ST_RUNNING)
    st.acquire_worker(ws, mode="until_gate")
    res = tool_api.delete_request(str(ws.root), "revision", rid)
    assert not res["ok"] and "正在运行" in res["error"]["message"]
    st.release_worker(ws, "test")


def test_finished_request_is_not_listed(isolated):
    ws = new_book(isolated, total=2)
    rid = tool_api.request_revision_cycle(str(ws.root), 1, "改一下")["data"]["id"]
    st.update_step(ws, f"rev.{rid}", status=st.ST_PASSED)
    assert not _list("request")


def test_ui_tasks_page_renders_and_stop_button_works(isolated):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    root_app = Path(__file__).resolve().parent.parent / "app.py"
    ws = new_book(isolated, total=2)
    st.acquire_worker(ws, mode="until_gate")
    at = AppTest.from_file(str(root_app), default_timeout=60).run()
    at.sidebar.radio[0].set_value("⚙ 后台任务").run()
    assert not at.exception, at.exception
    assert any("运行中" in m.value for m in at.markdown)
    stop = [b for b in at.button if b.label == "⏹ 温和停止"]
    assert stop, "运行中的任务应当有温和停止按钮"
    stop[0].click().run()
    assert not at.exception, at.exception
    assert st.read_control(ws)["command"] == "stop"
    st.release_worker(ws, "test")


# ── 立即停止（进程级强制终止）────────────────────────────────────────────────

import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

psutil = pytest.importorskip("psutil")


def _spawn_fake_worker(ws, child_cmd=None):
    """启动一个假 worker 子进程（带一个子进程），并在工作目录里登记它为运行中的 worker。"""
    code = ("import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "time.sleep(120)\n")
    proc = subprocess.Popen([sys.executable, "-c", code])
    time.sleep(0.6)  # 等子进程起来
    from novel_writer.procs import process_create_time
    p = psutil.Process(proc.pid)
    info = {"pid": proc.pid, "pid_create_time": p.create_time(), "state": "running", "mode": "until_gate",
            "started_at": st.now_iso(), "heartbeat": st.now_iso(), "step": "ch01.review",
            "step_title": "第1章：完章核查", "step_started_at": st.now_iso(), "reason": None}
    atomic_write_json(ws.path("studio_worker"), info)
    st.update_step(ws, "ch01.review", status=st.ST_RUNNING)
    return proc, p


def test_force_stop_kills_worker_and_children_and_interrupts_running_step(isolated):
    ws = new_book(isolated, total=2)
    proc, p = _spawn_fake_worker(ws)
    children = p.children(recursive=True)
    assert children, "假 worker 应当有子进程"
    res = tool_api.force_stop(str(ws.root))
    assert res["ok"], res
    assert res["data"]["terminated"] is True
    proc.wait(timeout=10)
    assert not psutil.pid_exists(proc.pid) or not psutil.Process(proc.pid).is_running()
    assert all(not c.is_running() for c in children)
    assert st.get_step_state(ws, "ch01.review")["status"] == st.ST_INTERRUPTED
    assert "强制停止" in st.get_step_state(ws, "ch01.review")["error"]
    assert st.read_worker(ws)["state"] == "stopped"


def test_force_stop_refuses_same_process(isolated):
    ws = new_book(isolated, total=2)
    st.acquire_worker(ws, mode="until_gate")  # 本进程即 worker
    res = tool_api.force_stop(str(ws.root))
    assert not res["ok"] and "同一进程" in res["error"]["message"]
    st.release_worker(ws, "test")


def test_force_stop_never_kills_on_pid_reuse(isolated):
    """进程号存在但创建时间对不上：说明是别的进程复用了号，绝不能终止，只清理状态。"""
    ws = new_book(isolated, total=2)
    proc, p = _spawn_fake_worker(ws)
    info = st.read_worker(ws)
    info["pid_create_time"] = info["pid_create_time"] - 10_000  # 伪造不匹配的创建时间
    atomic_write_json(ws.path("studio_worker"), info)
    try:
        res = tool_api.force_stop(str(ws.root))
        assert res["ok"], res
        assert res["data"]["terminated"] is False
        assert p.is_running(), "创建时间不匹配的进程不能被终止"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_force_stop_without_running_worker_is_rejected(isolated):
    ws = new_book(isolated, total=2)
    assert not tool_api.force_stop(str(ws.root))["ok"]


def test_force_stop_refuses_when_live_process_identity_unknown(isolated):
    """旧版本启动的 worker 没有创建时间：进程仍活着就必须拒绝，且不能清理状态。"""
    ws = new_book(isolated, total=2)
    proc, p = _spawn_fake_worker(ws)
    info = st.read_worker(ws)
    info.pop("pid_create_time", None)
    atomic_write_json(ws.path("studio_worker"), info)
    try:
        res = tool_api.force_stop(str(ws.root))
        assert not res["ok"] and "无法校验" in res["error"]["message"]
        assert p.is_running()
        assert st.read_worker(ws)["state"] == "running"  # 状态未被清理
        assert st.get_step_state(ws, "ch01.review")["status"] == st.ST_RUNNING
    finally:
        proc.kill()
        proc.wait(timeout=10)
