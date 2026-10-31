"""agent 的中间输出实时镜像进 run.log（单写者仍是 state.log_line），完整输出仍落在 agent.log。"""

from __future__ import annotations

import io

from fakes import FakeInvoker, approve_waiting
from novel_writer import outlog
from novel_writer import state as st
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import run_worker
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _run_log(ws):
    return ws.path("studio_log").read_text(encoding="utf-8").splitlines()


def test_linepump_calls_on_line_for_every_line_in_order(tmp_path):
    seen = []
    pump = outlog.LinePump(io.BytesIO("第一行\nsecond\n第三行\n".encode("utf-8")), tmp_path / "agent.log",
                           on_line=seen.append)
    assert pump.join(5)
    assert seen == ["第一行", "second", "第三行"]
    assert (tmp_path / "agent.log").read_text(encoding="utf-8") == "第一行\nsecond\n第三行\n"


def test_failing_sink_does_not_break_full_log(tmp_path):
    def boom(_text):
        raise RuntimeError("sink down")

    pump = outlog.LinePump(io.BytesIO(b"a\nb\n"), tmp_path / "agent.log", on_line=boom)
    assert pump.join(5)
    assert pump.line_count == 2
    assert (tmp_path / "agent.log").read_bytes() == b"a\nb\n"


def test_run_log_sink_mirrors_only_progress_lines(isolated):
    """正文、推理、JSON 等长文本不进 run.log；工具标题与告警行保留。"""
    ws = new_book(isolated)
    sink = outlog.run_log_sink(ws)
    for line in ["林远读完最后一行字。", '  "key": 1,', "", "📄 read_file  大纲规划/蓝图/_index.md",
                 "⚠  模型超时，重试", "✗  写入失败"]:
        sink(line)
    mirrored = [l.split("] ", 1)[1] for l in _run_log(ws) if "│" in l]
    assert mirrored == [
        "  │ 📄 read_file  大纲规划/蓝图/_index.md",
        "  │ ⚠  模型超时，重试",
        "  │ ✗  写入失败",
    ]


def test_run_log_sink_caps_progress_lines(isolated, monkeypatch):
    ws = new_book(isolated)
    monkeypatch.setattr(outlog, "MIRROR_MAX_LINES", 3)
    sink = outlog.run_log_sink(ws)
    for i in range(5):
        sink(f"🔎 grep  pattern{i}")
    sink("林远读完最后一行字。")  # 正文即使在超限之后也不计入、不提示
    lines = _run_log(ws)
    assert any(l.endswith("pattern2") for l in lines)
    assert not any(l.endswith("pattern3") for l in lines)
    assert sum("进度行过多" in l for l in lines) == 1


def test_run_log_sink_truncates_long_progress_lines(isolated):
    ws = new_book(isolated)
    outlog.run_log_sink(ws)("📄 read_file  " + "y" * 1000)
    mirrored = [l for l in _run_log(ws) if "│" in l][0]
    assert mirrored.endswith("…") and len(mirrored) < 200


def test_sink_is_installed_while_skill_step_invokes_agent(isolated):
    ws = new_book(isolated, total=2)
    inv = FakeInvoker(ws.root)
    seen = {}

    def hook(step_id):
        if step_id == "plan_blueprint":
            seen["sink"] = outlog.current_line_sink()

    inv.hook = hook
    for _ in range(50):
        res = run_worker(ws, mode="until_gate", invoker=inv, handlers=DEFAULT_PY_HANDLERS, heartbeat_interval=0.01)
        assert res["ok"], res
        if res["reason"] == "completed":
            break
        approve_waiting(ws)
    assert seen.get("sink") is not None
    assert outlog.current_line_sink() is None  # 调用结束即清除
