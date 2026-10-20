"""实时运行输出与结束提示：运行日志、上一次运行结果、运行状态接口、子 agent verbose、界面面板。

桩执行器下运行，不调用真实 LLM。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fakes import STUB_HANDLERS, FakeInvoker, make_blueprint, set_settings, put_blueprint
from novel_writer import config, runner as R, state as st, tool_api
from novel_writer.fsutil import atomic_write_json
from novel_writer.worker import run_worker
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def new_book(tmp_path, gear="G0"):
    r = tool_api.create_novel("实时测试", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear=gear, total_chapters_planned=3)
    ws = Workspace(r["data"]["workspace"])
    put_blueprint(ws, make_blueprint(3))
    return ws


def run_once(ws, inv):
    return run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)


def test_run_log_records_step_boundaries_and_summaries(tmp_path):
    ws = new_book(tmp_path, gear="G0")
    inv = FakeInvoker(ws.root)
    run_once(ws, inv)
    log = ws.path("studio_log").read_text(encoding="utf-8")
    assert "═══ 运行开始（模式 until_gate）═══" in log
    assert "▶ init_workspace" in log
    assert "▶ plan_blueprint" in log and "  调用 skill：novel-planner" in log
    assert "⏸ plan_blueprint → waiting_review" in log
    assert "═══ 运行结束：waiting_review" in log


def test_last_run_keeps_step_results_and_summaries(tmp_path):
    ws = new_book(tmp_path, gear="G0")
    run_once(ws, FakeInvoker(ws.root))
    last = st.read_last_run(ws)
    assert last["reason"] == "waiting_review" and last["finished_at"]
    by_step = {r["step"]: r for r in last["executed"]}
    assert by_step["plan_blueprint"]["status"] == "waiting_review"
    assert "plan_blueprint ok" in by_step["plan_blueprint"]["summary"]
    assert by_step["plan_blueprint"]["duration_sec"] is not None


def test_log_is_retained_across_runs(tmp_path):
    ws = new_book(tmp_path, gear="G0")
    inv = FakeInvoker(ws.root)
    run_once(ws, inv)
    tool_api.approve_step(str(ws.root), "plan_blueprint")
    run_once(ws, inv)
    log = ws.path("studio_log").read_text(encoding="utf-8")
    assert log.count("═══ 运行开始") == 2 and log.count("═══ 运行结束") == 2, "上一次运行的输出必须保留"
    assert "⏸ plan_blueprint → waiting_review" in log  # 第一次运行的记录仍在


def test_run_status_idle_then_alive(tmp_path):
    ws = new_book(tmp_path, gear="G0")
    idle = tool_api.run_status(str(ws.root))["data"]
    assert idle["alive"] is False and idle["log"] == [] and idle["last_run"] is None
    run_once(ws, FakeInvoker(ws.root))
    done = tool_api.run_status(str(ws.root))["data"]
    assert done["alive"] is False and done["reason"] == "waiting_review"
    assert any("运行结束" in line for line in done["log"])
    assert done["last_run"]["executed"]

    # 模拟一个正在运行的 worker（心跳新鲜）：应显示步骤标题与开始时间
    from datetime import datetime
    atomic_write_json(ws.path("studio_worker"), {
        "pid": 1, "state": "running", "mode": "until_gate", "started_at": st.now_iso(),
        "heartbeat": datetime.now().astimezone().isoformat(), "step": "ch01.write_chapter",
        "step_title": "第1章：写作", "step_started_at": st.now_iso(), "attempt": 2,
    })
    live = tool_api.run_status(str(ws.root))["data"]
    assert live["alive"] is True and live["step_title"] == "第1章：写作" and live["attempt"] == 2


def test_framework_invoker_runs_sub_agent_verbose(tmp_path, monkeypatch):
    """子 agent 必须以 verbose 运行，否则工具调用不会出现在 run.log 里。"""
    seen = {}

    class FakeRunner:
        def __init__(self, cfg):
            seen["verbose"] = cfg.verbose

        def run(self, wf, inputs, output_export_dir=None):
            return SimpleNamespace(status="done", error=None, step_results=[])

    wf = SimpleNamespace(steps=[SimpleNamespace()])

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return wf

    # 用假模块替换框架的两个导入：只验证本项目的调用路径，不依赖框架的完整导入链（需要 fastapi 等）
    import sys
    import types

    fake_runner = types.ModuleType("mini_agent.workflow.runner")
    fake_runner.WorkflowRunner = FakeRunner
    fake_store = types.ModuleType("mini_agent.workflow.store")
    fake_store.WorkflowStore = FakeStore
    monkeypatch.setitem(sys.modules, "mini_agent.workflow.runner", fake_runner)
    monkeypatch.setitem(sys.modules, "mini_agent.workflow.store", fake_store)
    cfg = SimpleNamespace(verbose=False)
    res = R.FrameworkInvoker(cfg)(skill="novel-planner", prompt="x", export_dir=tmp_path / "run", step_id="s")
    assert res.ok is True
    assert seen["verbose"] is True
    assert cfg.verbose is False, "原配置不应被修改"


def test_cockpit_page_shows_live_panel(tmp_path):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    ws = new_book(tmp_path, gear="G0")
    run_once(ws, FakeInvoker(ws.root))
    at = AppTest.from_file(str(config.PROJECT_ROOT / "app.py"), default_timeout=60).run()
    at.session_state["ws"] = str(ws.root)
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    assert not at.exception, at.exception
    assert any("运行输出" in e.label for e in at.expander)
    assert any("运行结束" in m.value for m in list(at.info) + list(at.success) + list(at.warning))
    assert not any("正在运行（步骤" in m.value for m in at.info), "旧的只显示步骤名的提示应已移除"
