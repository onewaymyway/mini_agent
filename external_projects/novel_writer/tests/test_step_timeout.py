"""单步执行时限：默认 40 分钟，可配置；超时强制终止并按自动重跑规则重跑。桩执行器，不调用真实 LLM。"""

from __future__ import annotations

import time

import pytest

from fakes import STUB_HANDLERS, FakeInvoker, make_blueprint, put_blueprint, set_settings
from novel_writer import config, tool_api
from novel_writer import settings as SE
from novel_writer import state as st
from novel_writer import step_timeout as STO
from novel_writer.runner import FrameworkInvoker, InvokeResult, StepTimeout
from novel_writer.worker import run_worker
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def new_book(tmp_path, gear="G3"):
    r = tool_api.create_novel("限时", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear=gear, total_chapters_planned=3)
    ws = Workspace(r["data"]["workspace"])
    put_blueprint(ws, make_blueprint(3))
    return ws


class _Step:
    def __init__(self, sid="plan_blueprint", category="planning"):
        self.id, self.category = sid, category


# ── 配置 ───────────────────────────────────────────────────────────────────


def test_default_is_40_minutes_and_resolution_order():
    s = SE.validate_settings({})
    assert s["step_timeout"] == {"minutes": 40, "overrides": {}}
    assert STO.limit_minutes(s, _Step()) == 40
    s = SE.validate_settings({"step_timeout": {"minutes": 25, "overrides": {"planning": 10, "plan_blueprint": 90}}})
    assert STO.limit_minutes(s, _Step()) == 90                       # 步骤 id 优先于类别
    assert STO.limit_minutes(s, _Step("x", "planning")) == 10        # 类别优先于全局
    assert STO.limit_minutes(s, _Step("x", "writing")) == 25
    assert STO.limit_minutes(SE.validate_settings({"step_timeout": {"minutes": None}}), _Step()) is None
    assert STO.limit_minutes(SE.validate_settings({"step_timeout": {"minutes": 0}}), _Step()) is None
    s = SE.validate_settings({"step_timeout": {"overrides": {"plan_blueprint": 0}}})
    assert STO.limit_minutes(s, _Step()) is None                     # 单步关闭时限


def test_old_settings_file_without_step_timeout_gets_default():
    assert SE.validate_settings({"gear": "G1"})["step_timeout"]["minutes"] == 40


def test_invalid_step_timeout_rejected():
    for bad in ({"minutes": -1}, {"minutes": "40"}, {"overrides": {"x": "a"}}, {"overrides": {"x": -5}}, {"foo": 1}, 7):
        with pytest.raises(SE.SettingsError):
            SE.validate_settings({"step_timeout": bad})


# ── 步骤层 ─────────────────────────────────────────────────────────────────


class TimeoutOnceInvoker(FakeInvoker):
    """前 n 次调用模拟「被强杀」（timed_out），之后正常。"""

    def __init__(self, root, *, step, times, **kw):
        super().__init__(root, **kw)
        self.step, self.times = step, times
        self.timeouts = []

    def __call__(self, *, skill, prompt, export_dir, step_id, **kw):
        if step_id == self.step and self.count.get(step_id, 0) < self.times:
            self.count[step_id] = self.count.get(step_id, 0) + 1
            self.calls.append(step_id)
            self.timeouts.append(kw.get("timeout"))
            self.prompts.setdefault(step_id, []).append(prompt)
            return InvokeResult(False, "已超过步骤时限，强制终止执行进程", timed_out=True)
        return super().__call__(skill=skill, prompt=prompt, export_dir=export_dir, step_id=step_id, **kw)


def test_timeout_kills_then_step_is_rerun_and_recovers(tmp_path):
    ws = new_book(tmp_path)
    inv = TimeoutOnceInvoker(ws.root, step="plan_blueprint", times=1)
    r = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    entry = st.get_step_state(ws, "plan_blueprint")
    assert entry["status"] == st.ST_WAITING_REVIEW and entry["auto_retries"] == 1
    assert not entry.get("timed_out")                       # 重跑成功后标记清除
    assert inv.count["plan_blueprint"] == 2
    log = ws.path("studio_log").read_text(encoding="utf-8")
    assert "步骤时限：40 分钟" in log and "超时被终止" in log and "自动重跑（第 1/3 次）" in log
    assert r["reason"] == "waiting_review"


def test_persistent_timeout_marks_failed_with_flag(tmp_path):
    ws = new_book(tmp_path)
    inv = TimeoutOnceInvoker(ws.root, step="plan_blueprint", times=99)
    r = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    entry = st.get_step_state(ws, "plan_blueprint")
    assert r["reason"] == "failed" and entry["status"] == st.ST_FAILED and entry["timed_out"] is True
    assert "强制终止" in entry["error"]
    assert inv.count["plan_blueprint"] == 4                  # 1 + 3 次自动重跑
    row = st.read_last_run(ws)["executed"][-1]
    assert row["timed_out"] is True and row["auto_retries"] == 3
    # 下一次重跑的 prompt 带着超时原因
    assert "强制终止" in inv.prompts["plan_blueprint"][-1]


def test_invoker_receives_remaining_time_as_timeout(tmp_path):
    ws = new_book(tmp_path)
    inv = TimeoutOnceInvoker(ws.root, step="plan_blueprint", times=1)
    run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert 40 * 60 - 60 < inv.timeouts[0] <= 40 * 60


def test_disabled_limit_keeps_legacy_900s_and_no_log(tmp_path):
    ws = new_book(tmp_path)
    set_settings(ws, step_timeout={"minutes": None})
    inv = TimeoutOnceInvoker(ws.root, step="plan_blueprint", times=1)
    run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert inv.timeouts[0] == 900
    assert "步骤时限" not in ws.path("studio_log").read_text(encoding="utf-8")


def test_limit_is_total_across_receipt_attempts(tmp_path):
    """补写回执的多轮尝试共用一个总时限：第 1 轮耗尽时限后，不再发起第 2 轮。"""
    ws = new_book(tmp_path)
    set_settings(ws, step_timeout={"minutes": 0.002})        # 0.12 秒
    set_settings(ws, step_retry={"max_retries": 0})
    calls = []

    def slow_no_receipt(*, skill, prompt, export_dir, step_id, **kw):
        calls.append(step_id)
        time.sleep(0.3)
        return InvokeResult(True)                             # 没写回执 → 本会触发第 2 轮补写

    run_worker(ws, mode="until_gate", invoker=slow_no_receipt, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    entry = st.get_step_state(ws, "plan_blueprint")
    assert calls.count("plan_blueprint") == 1
    assert entry["status"] == st.ST_FAILED and entry["timed_out"] is True


def test_step_timeout_is_a_step_failed():
    from novel_writer.runner import StepFailed

    assert issubclass(StepTimeout, StepFailed)


# ── 真正的进程终止 ─────────────────────────────────────────────────────────


def test_subprocess_is_killed_with_its_children_on_deadline(tmp_path, monkeypatch):
    import os
    import psutil

    here = os.path.dirname(__file__)
    monkeypatch.setenv("PYTHONPATH", here + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setattr(FrameworkInvoker, "_CHILD_MODULE", "slow_child")
    export = tmp_path / "run"
    t0 = time.monotonic()
    with STO.deadline_scope(2.0):
        result = FrameworkInvoker()(skill="x", prompt="p", export_dir=export, step_id="s")
    elapsed = time.monotonic() - t0
    assert result.ok is False and result.timed_out is True
    assert elapsed < 20
    gc_pid = int((export / "_grandchild.pid").read_text())
    time.sleep(0.5)
    assert not psutil.pid_exists(gc_pid) or psutil.Process(gc_pid).status() == psutil.STATUS_ZOMBIE
    assert "连带终止" in result.error


def test_child_crash_without_result_is_a_plain_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(FrameworkInvoker, "_CHILD_MODULE", "json.tool_does_not_exist")
    with STO.deadline_scope(30.0):
        result = FrameworkInvoker()(skill="x", prompt="p", export_dir=tmp_path / "run", step_id="s")
    assert result.ok is False and result.timed_out is False and "异常退出" in result.error


def test_save_matrix_round_trip_keeps_overrides(tmp_path):
    ws = new_book(tmp_path)
    set_settings(ws, step_timeout={"overrides": {"plan_blueprint": 90}})
    res = tool_api.save_matrix(str(ws.root), {}, {}, {"step_timeout": {"minutes": 25}})
    assert res["ok"], res
    saved = ws.settings()["step_timeout"]
    assert saved == {"minutes": 25, "overrides": {"plan_blueprint": 90}}
    assert tool_api.matrix(str(ws.root))["data"]["step_timeout"]["minutes"] == 25
