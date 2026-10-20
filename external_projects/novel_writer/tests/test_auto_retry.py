"""失败步骤自动重跑：最多 3 次；临时性故障可自动恢复；预算用尽或收到暂停时不再重跑。

桩执行器下运行，不调用真实 LLM。
"""

from __future__ import annotations

import pytest

from fakes import STUB_HANDLERS, FakeInvoker, approve_waiting, make_blueprint, set_settings, put_blueprint
from novel_writer import config, inbox, tool_api
from novel_writer import state as st
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


def new_book(tmp_path, gear="G3"):
    r = tool_api.create_novel("重跑", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear=gear, total_chapters_planned=3)
    ws = Workspace(r["data"]["workspace"])
    put_blueprint(ws, make_blueprint(3))
    return ws


class FlakyInvoker(FakeInvoker):
    """前 `failures` 次调用对指定步骤返回失败，之后正常。"""

    def __init__(self, root, *, flaky_step, failures, **kw):
        super().__init__(root, **kw)
        self.flaky_step = flaky_step
        self.failures = failures

    def __call__(self, *, skill, prompt, export_dir, step_id, **kw):
        if step_id == self.flaky_step and self.count.get(step_id, 0) < self.failures:
            self.calls.append(step_id)
            self.count[step_id] = self.count.get(step_id, 0) + 1
            from fakes import InvokeResult
            return InvokeResult(False, "临时错误")
        return super().__call__(skill=skill, prompt=prompt, export_dir=export_dir, step_id=step_id, **kw)


def test_persistent_failure_retries_exactly_three_times(tmp_path):
    ws = new_book(tmp_path)
    inv = FakeInvoker(ws.root, fail=["plan_blueprint"])
    r = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert r["reason"] == "failed"
    assert inv.count["plan_blueprint"] == 4  # 1 次原始 + 3 次自动重跑
    log = ws.path("studio_log").read_text(encoding="utf-8")
    for k in (1, 2, 3):
        assert f"第 {k}/3 次）" in log
    assert "第 4/3" not in log
    last = st.read_last_run(ws)
    row = [x for x in last["executed"] if x["step"] == "plan_blueprint"][0]
    assert row["auto_retries"] == 3 and row["status"] == st.ST_FAILED


def test_failure_message_in_inbox_mentions_retries(tmp_path):
    ws = new_book(tmp_path)
    run_worker(ws, mode="until_gate", invoker=FakeInvoker(ws.root, fail=["plan_blueprint"]),
               handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    failed = [it for it in inbox.collect(ws)["items"] if it["kind"] == "failed"]
    assert failed and "已自动重跑 3 次仍失败" in failed[0]["message"]


def test_transient_failure_recovers_automatically(tmp_path):
    ws = new_book(tmp_path)
    inv = FlakyInvoker(ws.root, flaky_step="plan_blueprint", failures=2)
    r = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    entry = st.get_step_state(ws, "plan_blueprint")
    assert entry["status"] == st.ST_WAITING_REVIEW  # 第 3 次成功，进入正常的审批流程
    assert entry["auto_retries"] == 2 and r["reason"] == "waiting_review"
    assert st.read_last_run(ws)["executed"][-1]["status"] == st.ST_WAITING_REVIEW


def test_budget_exhausted_stops_auto_retry(tmp_path):
    ws = new_book(tmp_path)
    set_settings(ws, budget={"max_chapters_per_run": 3, "max_llm_calls_per_run": 1})
    inv = FakeInvoker(ws.root, fail=["plan_blueprint"])
    run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert inv.count["plan_blueprint"] == 1
    assert "预算已用尽，不再自动重跑" in ws.path("studio_log").read_text(encoding="utf-8")


def test_retry_backoff_grows_and_is_logged(tmp_path, monkeypatch):
    import novel_writer.worker as worker_mod

    monkeypatch.setattr(worker_mod, "RETRY_BACKOFF_SEC", (5, 15, 30))
    slept = []
    monkeypatch.setattr(worker_mod.time, "sleep", lambda s: slept.append(s))
    ws = new_book(tmp_path)
    run_worker(ws, mode="until_gate", invoker=FakeInvoker(ws.root, fail=["plan_blueprint"]),
               handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert slept == [5, 15, 30]
    assert "5 秒后自动重跑（第 1/3 次）" in ws.path("studio_log").read_text(encoding="utf-8")
