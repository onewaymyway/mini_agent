"""M1 引擎内核测试：全部使用桩执行器（FakeInvoker），不调用真实 LLM。"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fakes import (
    STUB_HANDLERS, FakeInvoker, approve_waiting, drive_to_completion,
    make_blueprint, set_settings, statuses,
)
from novel_writer import gates as G
from novel_writer import pipeline as P
from novel_writer import runner as R
from novel_writer import service_cli
from novel_writer import state as st
from novel_writer import steps as S
from novel_writer import tool_api
from novel_writer.fsutil import atomic_write_json
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import run_worker
from novel_writer.workspace import Workspace

PROJECT_ROOT = Path(__file__).resolve().parent.parent

@pytest.fixture
def ws(tmp_path) -> Workspace:
    r = tool_api.create_novel("测试书", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"))
    assert r["ok"], r
    workspace = Workspace(r["data"]["workspace"])
    atomic_write_json(workspace.path("blueprint"), make_blueprint(4))
    return workspace


# ── 计划展开 ───────────────────────────────────────────────────────────────


def test_main_characters_threshold_and_name_cleanup():
    bp = make_blueprint(4)
    assert P.main_characters(bp) == ["李凌云", "王道长"]  # 王道长 3 章；路人甲只 1 章
    assert P.main_characters(None) == []


def test_plan_has_planning_then_chapters_with_confirm_after_third(ws):
    plan = P.build_plan(ws)
    ids = [s.id for s in plan]
    assert ids[0] == "init_workspace" and ids[1] == "plan_blueprint"
    assert "design_character:李凌云" in ids and "design_character:王道长" in ids
    i_confirm = ids.index("confirm_first3")
    assert ids[i_confirm - 1] == "ch03.sync"
    assert ids.index("ch04.word_quota") > ids.index("init_style_b")
    assert ids.index("ch01.word_quota") > ids.index("design_opening")
    assert {"ch01.write_chapter", "ch04.sync"} <= set(ids)


def test_reader_sim_only_every_fifth_chapter():
    five = S.chapter_steps(5, "", total=5, prev_last="x")
    four = S.chapter_steps(4, "", total=5, prev_last="x")
    assert any(s.id == "ch05.reader_sim" for s in five)
    assert not any(s.id.endswith("reader_sim") for s in four)


def test_chapter_outputs_use_glob_when_title_unknown():
    step = S.chapter_steps(2, "", total=5, prev_last="x")[4]
    assert step.id == "ch02.write_chapter" and step.outputs == ("glob:正文/第02章 *.md",)


def test_no_blueprint_plan_has_only_infra_and_planning(tmp_path):
    r = tool_api.create_novel("空书", novels_dir=str(tmp_path / "n"), index_file=str(tmp_path / "i.json"))
    plan = P.build_plan(Workspace(r["data"]["workspace"]))
    assert all(not s.id.startswith("ch") for s in plan)


# ── 闸门 ───────────────────────────────────────────────────────────────────



def test_gear_presets_match_plan_table(ws):
    base = ws.settings()
    design = S.design_theme_step()
    build = S.build_world_step()
    prep = S.chapter_steps(4, "", total=4, prev_last="x")[0]
    rev = S.simulate_blueprint_step()
    # (设计, 建档, 写前准备) 在各挡位下的闸门，对应方案 6.2 表格
    expect = {
        "G0": (G.GATE_MANUAL, G.GATE_MANUAL, G.GATE_MANUAL),
        "G1": (G.GATE_REVIEW, G.GATE_REVIEW, G.GATE_AUTO),
        "G2": (G.GATE_REVIEW, G.GATE_AUTO, G.GATE_AUTO),
        "G3": (G.GATE_REVIEW, G.GATE_AUTO, G.GATE_AUTO),
    }
    for gear, (d, b, p) in expect.items():
        s = {**base, "gear": gear}
        assert G.resolve_gate(design, s).gate == d, gear
        assert G.resolve_gate(build, s).gate == b, gear
        assert G.resolve_gate(prep, s).gate == p, gear
        assert G.resolve_gate(rev, s).gate == G.GATE_MANUAL, gear  # 修订/推演始终手动

def test_checkpoints_cannot_be_loosened_by_override_except_waiver(ws):
    s = {**ws.settings(), "gear": "G3", "step_overrides": {"plan_blueprint": "auto", "confirm_first3": "skip"}}
    assert G.resolve_gate(S.plan_blueprint_step(), s).gate == G.GATE_REVIEW
    assert G.resolve_gate(S.confirm_first3_step(), s).gate == G.GATE_REVIEW
    waived = {**s, "checkpoints": {"waive_blueprint_confirm": True}, "step_overrides": {}}
    d = G.resolve_gate(S.plan_blueprint_step(), waived)
    assert (d.gate, d.source) == (G.GATE_AUTO, "waived")
    assert G.resolve_gate(S.confirm_first3_step(), waived).gate == G.GATE_REVIEW


def test_step_and_stage_overrides_take_effect(ws):
    from dataclasses import replace

    s = {**ws.settings(), "gear": "G0", "stage_gear_overrides": {"p3": "G3"}}
    prep = S.chapter_steps(4, "", total=4, prev_last="x")[0]
    assert G.resolve_gate(prep, s).gate == G.GATE_AUTO
    assert G.resolve_gate(prep, s).source == "stage_gear"
    assert G.resolve_gate(S.design_theme_step(), s).gate == G.GATE_MANUAL  # p1 仍是 G0
    s2 = {**s, "step_overrides": {"ch04.word_quota": "manual"}}
    d = G.resolve_gate(replace(prep, id="ch04.word_quota"), s2)
    assert (d.gate, d.source) == (G.GATE_MANUAL, "override")

# ── 状态 / 脏标记 / 孤儿 ───────────────────────────────────────────────────


def test_stale_marked_when_upstream_changes_and_legacy_steps_not_marked(ws):
    plan = P.build_plan(ws)
    st.update_step(ws, "design_theme", status=st.ST_PASSED)  # 旧记录：没有 input_hash
    assert st.mark_stale(ws, plan) == []
    assert st.get_step_state(ws, "design_theme")["input_hash"]
    ws.path("blueprint").write_text(json.dumps(make_blueprint(4), ensure_ascii=False, indent=2) + " ", encoding="utf-8")
    assert st.mark_stale(ws, plan) == ["design_theme"]
    assert st.get_step_state(ws, "design_theme")["status"] == st.ST_STALE


def test_orphaned_running_step_is_interrupted_and_rerun(ws):
    st.update_step(ws, "plan_blueprint", status=st.ST_RUNNING, attempts=1)
    old = (dt.datetime.now().astimezone() - dt.timedelta(hours=2)).isoformat(timespec="seconds")
    atomic_write_json(ws.path("studio_worker"), {"pid": 1, "state": "running", "heartbeat": old})
    assert not st.worker_alive(ws)
    inv = FakeInvoker(ws.root)
    r = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert r["ok"] and r["reason"] == "waiting_review"
    assert "plan_blueprint" in inv.calls
    assert st.get_step_state(ws, "plan_blueprint")["attempts"] == 2


def test_live_worker_blocks_second_worker(ws):
    atomic_write_json(ws.path("studio_worker"), {"pid": 1, "state": "running", "heartbeat": st.now_iso()})
    r = run_worker(ws, mode="until_gate", invoker=FakeInvoker(ws.root), handlers=STUB_HANDLERS)
    assert r["ok"] is False and r["reason"] == "already_running"


# ── worker 流程 ────────────────────────────────────────────────────────────


def test_g0_stops_at_first_review_after_auto_infra(ws):
    inv = FakeInvoker(ws.root)
    r = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert r["reason"] == "waiting_review"
    assert [e["step"] for e in r["executed"]] == ["init_workspace", "plan_blueprint"]
    assert statuses(ws)["init_workspace"] == st.ST_PASSED
    assert statuses(ws)["plan_blueprint"] == st.ST_WAITING_REVIEW


def test_g0_manual_step_waits_until_requested(ws):
    inv = FakeInvoker(ws.root)
    r = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert r["reason"] == "waiting_review"  # 检查点
    approve_waiting(ws)
    r2 = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert r2["reason"] == "manual" and statuses(ws)["design_theme"] == st.ST_PENDING
    assert "design_theme" not in inv.calls
    # 手动挡的 ▶：显式请求运行这一步
    assert tool_api.run_novel(str(ws.root), mode="single", step_id="design_theme", spawn=False)["ok"]
    r3 = run_worker(ws, mode="single", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert r3["reason"] == "single_done" and "design_theme" in inv.calls
    assert st.read_control(ws)["run_step"] is None  # 请求被消费

def test_full_flow_g3_with_approvals_completes_and_skips_optional(ws):
    set_settings(ws, gear="G3")
    inv = FakeInvoker(ws.root)
    drive_to_completion(ws, inv)
    s = statuses(ws)
    assert s["plan_blueprint"] == st.ST_PASSED and s["confirm_first3"] == st.ST_PASSED
    assert all(s[f"ch0{n}.sync"] == st.ST_PASSED for n in range(1, 5))
    assert "simulate_blueprint" not in inv.calls  # 可选且手动：不阻塞
    assert inv.calls[0] == "plan_blueprint"
    assert ws.path("word_count").is_file()


def test_budget_chapters_pauses_not_fails(ws):
    set_settings(ws, gear="G3", budget={"max_chapters_per_run": 1})
    inv = FakeInvoker(ws.root)
    last = None
    for _ in range(100):
        last = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
        if last["reason"] != "waiting_review":
            break
        approve_waiting(ws)
    assert last["reason"] == "budget_chapters"
    s = statuses(ws)
    assert s["ch01.sync"] == st.ST_PASSED and s["ch02.word_quota"] == st.ST_PENDING


def test_budget_llm_calls_limit_is_per_run_and_stops_mid_run(ws):
    # 预算按一次 worker 运行计数：第一轮只跑了 plan_blueprint 就停在审批（1 次调用）
    set_settings(ws, gear="G3", budget={"max_llm_calls_per_run": 2},
                 step_overrides={"design_theme": "auto", "design_pacing": "auto",
                                 "design_character:李凌云": "auto", "design_character:王道长": "auto"})
    inv = FakeInvoker(ws.root)
    r = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert r["reason"] == "waiting_review" and inv.calls == ["plan_blueprint"]
    approve_waiting(ws)
    # 第二轮：自动执行 design_theme、design_pacing 两次调用后，第三次调用前预算耗尽 → 暂停
    r2 = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert r2["reason"] == "budget_calls"
    assert inv.calls == ["plan_blueprint", "design_theme", "design_pacing"]
    assert statuses(ws)["design_character:李凌云"] == st.ST_PENDING


def test_needs_user_suspends_then_answer_reaches_prompt(ws):
    set_settings(ws, gear="G3")
    inv = FakeInvoker(ws.root, needs_user=["plan_blueprint"])
    r = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert r["reason"] == "waiting_user"
    assert st.get_step_state(ws, "plan_blueprint")["needs_user"][0]["question"] == "选哪个方向？"
    assert tool_api.answer_step(str(ws.root), "plan_blueprint", {"q1": "选A"})["ok"]
    r2 = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert r2["reason"] == "waiting_review"
    assert "选A" in inv.prompts["plan_blueprint"][1]
    assert "不能等待或向作者提问" in inv.prompts["plan_blueprint"][0]


def test_invalid_receipt_or_missing_output_retries_on_new_agent(ws):
    set_settings(ws, gear="G3")
    inv = FakeInvoker(ws.root, skip_once=["design_theme"])
    run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)  # plan_blueprint
    approve_waiting(ws)
    r = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert r["reason"] == "waiting_review"
    assert inv.calls.count("design_theme") == 2
    assert "缺少产出" in inv.prompts["design_theme"][1]


def test_skill_failure_marks_failed_and_stops(ws):
    set_settings(ws, gear="G3")
    inv = FakeInvoker(ws.root, fail=["plan_blueprint"])
    r = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert r["ok"] and r["reason"] == "failed"
    entry = st.get_step_state(ws, "plan_blueprint")
    assert entry["status"] == st.ST_FAILED and "boom" in entry["error"] and entry["attempts"] == 1


def test_missing_python_handler_is_blocked_not_faked(ws):
    set_settings(ws, gear="G3", budget={"max_chapters_per_run": None})
    inv = FakeInvoker(ws.root)
    last = None
    for _ in range(100):
        last = run_worker(ws, mode="until_gate", invoker=inv, handlers=DEFAULT_PY_HANDLERS, heartbeat_interval=0.01)
        if last["reason"] != "waiting_review":
            break
        approve_waiting(ws)
    assert last["reason"] == "blocked"
    entry = st.get_step_state(ws, "ch01.word_quota")
    assert entry["status"] == st.ST_BLOCKED and "M3" in entry["error"]


def test_single_mode_runs_one_step(ws):
    set_settings(ws, gear="G3")
    r = run_worker(ws, mode="single", invoker=FakeInvoker(ws.root), handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert r["reason"] == "single_done" and len(r["executed"]) == 1


def test_pause_and_stop_take_effect_between_steps(ws):
    set_settings(ws, gear="G3")
    handlers = {**STUB_HANDLERS, "init_workspace": lambda w, s, c: (st.write_control(w, command="pause"), {"summary": "x"})[1]}
    r = run_worker(ws, mode="until_gate", invoker=FakeInvoker(ws.root), handlers=handlers, heartbeat_interval=0.01)
    assert r["reason"] == "paused" and statuses(ws)["init_workspace"] == st.ST_PASSED
    assert st.read_control(ws)["command"] == "none"
    handlers2 = {**STUB_HANDLERS, "init_workspace": lambda w, s, c: (st.write_control(w, command="stop"), {"summary": "x"})[1]}
    set_settings(ws, gear="G3")
    st.update_step(ws, "init_workspace", status=st.ST_PENDING)
    r2 = run_worker(ws, mode="until_gate", invoker=FakeInvoker(ws.root), handlers=handlers2, heartbeat_interval=0.01)
    assert r2["reason"] == "stopped"


def test_stale_upstream_reruns_downstream(ws):
    set_settings(ws, gear="G3")
    drive_to_completion(ws, FakeInvoker(ws.root))
    ws.path("blueprint").write_text(json.dumps(make_blueprint(4), ensure_ascii=False, indent=2) + "\n\n", encoding="utf-8")
    inv = FakeInvoker(ws.root)
    r = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert "design_theme" in r["stale"]
    assert inv.calls[0] == "design_theme"


# ── tool_api / CLI ─────────────────────────────────────────────────────────


def test_pipeline_status_shape(ws):
    r = tool_api.pipeline_status(str(ws.root))
    assert r["ok"], r
    data = r["data"]
    assert data["frontier"] == "init_workspace"
    row = data["steps"][1]
    assert {"id", "status", "gate", "gate_source", "outputs", "attempts"} <= set(row)
    assert data["worker"]["alive"] is False


def test_run_novel_queues_without_spawning(ws):
    r = tool_api.run_novel(str(ws.root), mode="single", step_id="plan_blueprint", spawn=False)
    assert r["ok"] and r["data"]["spawned_pid"] is None
    ctrl = st.read_control(ws)
    assert ctrl["command"] == "run" and ctrl["run_step"] == "plan_blueprint" and ctrl["mode"] == "single"


def test_run_novel_rejects_unknown_step(ws):
    r = tool_api.run_novel(str(ws.root), step_id="nope", spawn=False)
    assert not r["ok"] and r["error"]["type"] == "workspace_error"


def test_step_operations_validate_state(ws):
    assert tool_api.approve_step(str(ws.root), "plan_blueprint")["error"]["type"] == "workspace_error"
    assert tool_api.answer_step(str(ws.root), "plan_blueprint", {"q1": "a"})["ok"] is False
    assert tool_api.rerun_step(str(ws.root), "plan_blueprint")["ok"] is True
    st.update_step(ws, "plan_blueprint", status=st.ST_RUNNING)
    assert tool_api.rerun_step(str(ws.root), "plan_blueprint")["ok"] is False


def test_author_note_is_injected_into_prompt(ws):
    assert tool_api.write_author_note(str(ws.root), "plan_blueprint", "主角要有反转")["ok"]
    assert "主角要有反转" in R.build_prompt(ws, S.plan_blueprint_step())
    tool_api.write_author_note(str(ws.root), "plan_blueprint", "")
    assert "主角要有反转" not in R.build_prompt(ws, S.plan_blueprint_step())

def test_pause_and_stop_write_control(ws):
    assert tool_api.pause_novel(str(ws.root))["data"]["command"] == "pause"
    assert tool_api.stop_novel(str(ws.root))["data"]["command"] == "stop"


def test_cli_status_prints_json(ws, capsys):
    code = service_cli.main(["status", str(ws.root)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["ok"] and out["data"]["frontier"] == "init_workspace"


def test_cli_bad_json_answer_is_validation_error(ws, capsys):
    code = service_cli.main(["answer", str(ws.root), "plan_blueprint", "--json", "{不是json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["error"]["type"] == "validation_error"


def test_run_novel_entrypoint_rejects_missing_workspace(tmp_path):
    env = {**os.environ, "NOVEL_WRITER_DATA_DIR": str(tmp_path / "data")}
    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "entrypoints" / "run_novel.py"), "--workspace", str(tmp_path / "无此目录")],
        cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1
    assert json.loads(proc.stdout.strip().splitlines()[-1])["reason"] == "not_initialized"
