"""「下一步」一致性：界面显示的步骤 == 运行的步骤；待审批时不静默空转；单步选择框默认跟随下一步。

桩执行器下运行，不调用真实 LLM。
"""

from __future__ import annotations

import pytest

from fakes import STUB_HANDLERS, FakeInvoker, make_blueprint, put_blueprint
from novel_writer import config, tool_api
from novel_writer import pipeline as P
from novel_writer.fsutil import atomic_write_json
from novel_writer.runner import DEFAULT_PY_HANDLERS
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
    r = tool_api.create_novel("下一步", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear=gear, total_chapters_planned=3)
    ws = Workspace(r["data"]["workspace"])
    put_blueprint(ws, make_blueprint(3))
    return ws


def next_of(ws):
    return tool_api.next_step(str(ws.root))["data"]


def run_next(ws, inv, handlers=STUB_HANDLERS):
    """与界面按钮相同：带上 next_step 返回的步骤 id 运行（单步模式）。"""
    nxt = next_of(ws)
    assert nxt["action"] == "run", nxt
    assert tool_api.run_novel(str(ws.root), mode="single", step_id=nxt["step"], spawn=False)["ok"]
    return run_worker(ws, mode="single", invoker=inv, handlers=handlers, heartbeat_interval=0.01)


def test_next_step_matches_frontier_shown_in_pipeline(tmp_path):
    ws = new_book(tmp_path)
    status = tool_api.pipeline_status(str(ws.root))["data"]
    assert next_of(ws)["step"] == status["frontier"]


def test_running_next_executes_exactly_the_displayed_step(tmp_path):
    ws = new_book(tmp_path)
    inv = FakeInvoker(ws.root)
    shown = next_of(ws)["step"]
    run_next(ws, inv)
    last = tool_api.run_status(str(ws.root))["data"]["last_run"]
    assert [r["step"] for r in last["executed"]] == [shown]


def test_waiting_review_blocks_run_next_visibly_instead_of_silently(tmp_path):
    ws = new_book(tmp_path)
    inv = FakeInvoker(ws.root)
    run_next(ws, inv)  # init_workspace
    run_next(ws, inv)  # plan_blueprint -> waiting_review
    nxt = next_of(ws)
    assert nxt["step"] == "plan_blueprint" and nxt["action"] == "approve"
    assert "批准" in nxt["message"]
    # 界面的按钮是禁用的；即使绕过按钮，worker 也不会去执行别的步骤
    before = len(inv.calls)
    tool_api.run_novel(str(ws.root), mode="single", step_id="plan_blueprint", spawn=False)
    run_worker(ws, mode="single", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert len(inv.calls) == before


def test_single_step_run_is_reflected_in_next_step(tmp_path):
    ws = new_book(tmp_path)
    inv = FakeInvoker(ws.root)
    # 单步运行 init_workspace 后，下一步应立刻变为 plan_blueprint（不是之前的位置）
    tool_api.run_novel(str(ws.root), mode="single", step_id="init_workspace", spawn=False)
    run_worker(ws, mode="single", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert next_of(ws)["step"] == "plan_blueprint"


def test_manual_frontier_runs_when_displayed(tmp_path):
    ws = new_book(tmp_path, gear="G0")
    inv = FakeInvoker(ws.root)
    run_next(ws, inv)  # init
    run_next(ws, inv)  # plan_blueprint -> waiting_review
    assert tool_api.approve_step(str(ws.root), "plan_blueprint")["ok"]
    nxt = next_of(ws)
    assert nxt["step"] == "blueprint_split" and nxt["action"] == "run"   # 规划通过后，先拆分蓝图
    assert "手动" in nxt["message"]
    res = run_next(ws, inv)
    assert res["executed"] and res["executed"][0]["step"] == "blueprint_split", res


def test_cockpit_defaults_single_step_to_next_and_follows_progress(tmp_path):
    from streamlit.testing.v1 import AppTest

    ws = new_book(tmp_path)
    inv = FakeInvoker(ws.root)
    at = AppTest.from_file(str(config.PROJECT_ROOT / "app.py"), default_timeout=60).run()
    at.session_state["ws"] = str(ws.root)
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    assert not at.exception, at.exception
    assert at.selectbox(key="cp_step_pick").value == next_of(ws)["step"] == "init_workspace"

    run_next(ws, inv)  # 在后台跑完 init_workspace 之后，界面刷新应跟随到 plan_blueprint
    at.button(key="cp_refresh").click().run()
    assert not at.exception, at.exception
    assert at.selectbox(key="cp_step_pick").value == "plan_blueprint"
