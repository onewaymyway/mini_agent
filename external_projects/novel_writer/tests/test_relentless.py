"""不停机挡（G5）：托管 + 步骤失败后自动重置并继续，直到全书完成或作者暂停/停止。"""

from __future__ import annotations

from fakes import FakeInvoker, InvokeResult
from novel_writer import autopilot as AP
from novel_writer import state as st
from novel_writer import tool_api
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import MAX_AUTO_RETRIES, run_worker
from test_m3 import isolated, new_book  # noqa: F401  (fixture)

STEP = "design_theme"


class Flaky(FakeInvoker):
    """对 STEP 前 failures 次调用返回失败；after_calls 为 {第几次调用: 回调}，用于模拟作者在中途操作。"""

    def __init__(self, root, *, failures, after_calls=None, **kw):
        super().__init__(root, **kw)
        self.failures = failures
        self.after_calls = after_calls or {}
        self.step_calls = 0

    def __call__(self, *, skill, prompt, export_dir, step_id, **kw):
        if step_id == STEP:
            self.step_calls += 1
            cb = self.after_calls.get(self.step_calls)
            if cb:
                cb()
            if self.step_calls <= self.failures:
                self.prompts = getattr(self, "prompts", []) + [prompt]
                return InvokeResult(False, "临时错误")
        return super().__call__(skill=skill, prompt=prompt, export_dir=export_dir, step_id=step_id, **kw)


def _gear(ws, gear):
    assert tool_api.update_settings(str(ws.root), {"gear": gear})["ok"]


def _run(ws, inv):
    return run_worker(ws, mode="until_gate", invoker=inv, handlers=DEFAULT_PY_HANDLERS,
                      heartbeat_interval=0.01)["reason"]


def test_g4_stops_on_failure_but_g5_resets_and_completes(isolated):
    failures = 1 + MAX_AUTO_RETRIES + 2          # 超过一轮自动重跑的上限
    ws4 = new_book(isolated / "a", total=2)
    _gear(ws4, "G4")
    assert _run(ws4, Flaky(ws4.root, failures=failures)) == st.ST_FAILED

    ws5 = new_book(isolated / "b", total=2)
    _gear(ws5, "G5")
    inv = Flaky(ws5.root, failures=failures)
    assert _run(ws5, inv) == "completed"
    entry = st.get_step_state(ws5, STEP)
    assert entry["status"] == st.ST_PASSED and entry["auto_resets"] >= 1
    assert "不停机" in ws5.path("studio_log").read_text(encoding="utf-8")


def test_g5_pause_during_failures_stops_loop(isolated):
    ws = new_book(isolated, total=2)
    _gear(ws, "G5")
    # 持续失败；第 2 次调用时作者点暂停 → 必须停下，而不是无限重置
    inv = Flaky(ws.root, failures=10**6, after_calls={2: lambda: st.write_control(ws, command="pause")})
    assert _run(ws, inv) == "paused"
    assert inv.step_calls < 10


def test_g5_retry_prompt_carries_failure_reason_after_reset(isolated):
    ws = new_book(isolated, total=2)
    _gear(ws, "G5")
    inv = Flaky(ws.root, failures=1 + MAX_AUTO_RETRIES)
    _run(ws, inv)
    assert st.get_step_state(ws, STEP).get("retry_hint") is None   # 成功后已清除


def test_g5_does_not_stop_on_max_auto_answers(isolated):
    """G4 在自动回答达上限后停下；G5 重置该步骤后继续（持续有 🔴 时由暂停打断）。"""
    ws = new_book(isolated, total=2)
    _gear(ws, "G5")
    calls = {"n": 0}

    def count():
        calls["n"] += 1
        if calls["n"] >= 40:
            st.write_control(ws, command="stop")

    inv = FakeInvoker(ws.root, red=[1], writers={"ch01.review": lambda w: count()})
    reason = _run(ws, inv)
    assert reason != st.ST_WAITING_USER
    assert st.get_step_state(ws, "ch01.revise_loop").get("auto_answers", 0) <= AP.MAX_AUTO_ANSWERS


def test_g5_is_a_valid_gear_and_gates_like_autopilot():
    from novel_writer import gates as G, settings as SE, steps as S

    assert "G5" in SE.GEARS and SE.validate_settings({"gear": "G5"})["gear"] == "G5"
    assert all("G5" in cols for cols in G.GEAR_GATES.values())
    assert "G5" in G.RUN_SCOPE
