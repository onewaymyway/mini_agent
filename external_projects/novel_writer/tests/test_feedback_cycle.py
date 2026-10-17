"""修改核查循环：作者反馈 → 大模型修改 → 重新核查（手动入口 + 升级答复入口）。桩执行器，不调用真实 LLM。"""

from __future__ import annotations

import pytest

from fakes import FakeInvoker, drive_to_completion, statuses
from novel_writer import config, tool_api
from novel_writer import state as st
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import run_worker
from test_m3 import new_book, run_until


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _run_single(ws, inv, step_id):
    assert tool_api.run_novel(str(ws.root), mode="single", step_id=step_id, spawn=False)["ok"]
    return run_worker(ws, mode="single", invoker=inv, handlers=DEFAULT_PY_HANDLERS, heartbeat_interval=0.01)


def _finished(tmp_path, total=3):
    ws = new_book(tmp_path, total=total)
    inv = FakeInvoker(ws.root)
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS)
    return ws, inv


def test_feedback_request_is_revised_then_rechecked(isolated):
    ws, inv = _finished(isolated)
    r = tool_api.request_revision_cycle(str(ws.root), 1, "第二段对话太直白，改含蓄")
    assert r["ok"], r
    sid = r["data"]["step"]
    before = len(inv.calls)
    _run_single(ws, inv, sid)
    new_calls = inv.calls[before:]
    assert f"{sid}.revise.r1" in new_calls and f"{sid}.review.r1" in new_calls
    # 作者反馈原文进入修改提示词
    assert any("第二段对话太直白，改含蓄" in p for p in inv.prompts[f"{sid}.revise.r1"])
    # 修改后重新核查，清零即停：没有第二轮
    assert f"{sid}.revise.r2" not in new_calls
    assert "修改核查完成" in ws.path("revision_log", nn=1).read_text(encoding="utf-8")
    # 修改过的章节，下游被标为需重跑
    assert st.get_step_state(ws, sid)["status"] == st.ST_PASSED


def test_feedback_request_keeps_looping_while_red_remains(isolated):
    ws, inv = _finished(isolated)
    sid = tool_api.request_revision_cycle(str(ws.root), 1, "修正人物年龄矛盾")["data"]["step"]

    def still_red(ws_):  # 每次复核都仍报 🔴，应循环到上限（桩按 id 无法识别 rev.* 步骤的章节，故直接写报告）
        ws_.path("review_report", nn=1).write_text("- 🔴 人物年龄前后矛盾\n", encoding="utf-8")

    inv.writers[".review.r"] = still_red
    _run_single(ws, inv, sid)
    assert [c for c in inv.calls if c.startswith(f"{sid}.revise.r")] == [f"{sid}.revise.r{k}" for k in (1, 2, 3)]
    assert st.get_step_state(ws, sid)["status"] == st.ST_PASSED
    assert "仍有" in ws.path("revision_log", nn=1).read_text(encoding="utf-8")


def test_feedback_request_rejects_empty_text_and_unknown_chapter(isolated):
    ws, _ = _finished(isolated)
    assert not tool_api.request_revision_cycle(str(ws.root), 1, "   ")["ok"]
    assert not tool_api.request_revision_cycle(str(ws.root), 99, "改一下")["ok"]


def test_escalation_answer_with_feedback_reenters_loop(isolated):
    ws = new_book(isolated, total=3)
    inv = FakeInvoker(ws.root, red=[2])
    run_until(ws, inv, reasons=("waiting_review",))
    assert statuses(ws)["ch02.revise_loop"] == st.ST_WAITING_USER
    before = len(inv.calls)
    assert tool_api.answer_step(str(ws.root), "ch02.revise_loop",
                                {"decision": "按审稿意见再修改一轮", "feedback": "把结尾的独白删掉一半"})["ok"]
    inv.red.discard(2)  # 大模型修改后问题清零
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS)
    new_writes = [c for c in inv.calls[before:] if ".revise.r" in c]
    assert new_writes, "升级答复后应重新进入修改"
    assert any("把结尾的独白删掉一半" in p for p in inv.prompts[new_writes[0]])
    assert statuses(ws)["ch03.sync_plot"] == st.ST_PASSED


def test_review_question_answers_become_revision_feedback(isolated):
    """完章核查待答复 → 作者选择 → 转为修改意见 → 修改循环用它改写 → 重新核查。"""
    ws = new_book(isolated, total=2)
    inv = FakeInvoker(ws.root, needs_user=["ch01.review"])
    run_until(ws, inv, reasons=("waiting_review",))
    assert statuses(ws)["ch01.review"] == st.ST_WAITING_USER
    needs = st.get_step_state(ws, "ch01.review")["needs_user"]
    qid = needs[0]["id"]
    # 缺答复应被拒绝
    assert not tool_api.answer_step(str(ws.root), "ch01.review", {})["ok"]
    before = len(inv.calls)
    res = tool_api.answer_step(str(ws.root), "ch01.review", {qid: "A"})
    assert res["ok"], res
    assert res["data"]["routed_to"] == "ch01.revise_loop"
    # 答复后必须排入修改循环（不需要作者再点运行）
    assert st.read_control(ws)["command"] == "run"
    assert st.read_control(ws)["run_step"] == "ch01.revise_loop"
    assert res["data"]["run"]["queued"] is True
    assert statuses(ws)["ch01.review"] == st.ST_PASSED  # 核查不再重跑，直接转交修改
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS)
    # 修改循环以作者答复为修改意见执行 chapter-writer
    writes = [c for c in inv.calls[before:] if c == "ch01.revise.r1"]
    assert writes, inv.calls[before:]
    assert "选哪个方向？" in inv.prompts["ch01.revise.r1"][-1]
    assert "作者答复：A" in inv.prompts["ch01.revise.r1"][-1]
    assert statuses(ws)["ch02.sync_plot"] == st.ST_PASSED


def test_cycle_does_not_leave_check_steps_falsely_stale(isolated):
    """修改循环改写正文后，核查与机器校验已在循环内重跑，不应被误判为「上游变化需重跑」。"""
    from novel_writer import pipeline as P
    from novel_writer import state as st2

    ws, inv = _finished(isolated)
    sid = tool_api.request_revision_cycle(str(ws.root), 1, "压缩第二段")["data"]["step"]
    _run_single(ws, inv, sid)
    stale = st2.mark_stale(ws, P.build_plan(ws))
    assert "ch01.review" not in stale and "ch01.machine_check" not in stale


def test_routed_review_answers_do_not_leave_check_steps_falsely_stale(isolated):
    from novel_writer import pipeline as P
    from novel_writer import state as st2

    ws = new_book(isolated, total=2)
    inv = FakeInvoker(ws.root, needs_user=["ch01.review"])
    run_until(ws, inv, reasons=("waiting_review",))
    qid = st.get_step_state(ws, "ch01.review")["needs_user"][0]["id"]
    assert tool_api.answer_step(str(ws.root), "ch01.review", {qid: "A"})["ok"]
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS)
    stale = st2.mark_stale(ws, P.build_plan(ws))
    assert "ch01.review" not in stale and "ch01.machine_check" not in stale
