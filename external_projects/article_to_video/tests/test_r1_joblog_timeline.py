"""R1：项目运行日志、步骤事件、步骤时间表、流水线埋点、后台任务日志、驾驶舱监控面板。"""
import json
import threading
import time
from datetime import datetime, timedelta

import pytest

from article_to_video import cancel, joblog, timeline
from article_to_video.pipeline.run import run_pipeline
from article_to_video.providers.image import StubImageProvider
from article_to_video.providers.tts import SilenceTTS
from helpers_p2 import ScriptLLM, good_script, make_project


@pytest.fixture(autouse=True)
def _reset_timeline_cache():
    timeline._CACHE.clear()
    yield
    timeline._CACHE.clear()


# ---------- joblog 基础 ----------

def test_log_and_tail_roundtrip_with_scope_filter(tmp_path):
    joblog.log(tmp_path, "pipeline", "第一条")
    joblog.log(tmp_path, "script", "写稿中", level="warn")
    joblog.log(tmp_path, "pipeline", "第三条", level="ok")
    rows = joblog.tail(tmp_path, 10)
    assert [r["message"] for r in rows] == ["第一条", "写稿中", "第三条"]
    only = joblog.tail(tmp_path, 10, ["pipeline"])
    assert [r["message"] for r in only] == ["第一条", "第三条"]
    assert [r["message"] for r in joblog.tail(tmp_path, 1)] == ["第三条"]
    assert joblog.tail(tmp_path / "nope") == []


def test_unknown_level_falls_back_to_info(tmp_path):
    joblog.log(tmp_path, "x", "hi", level="weird")
    assert joblog.tail(tmp_path)[0]["level"] == "info"


def test_tail_tolerates_a_truncated_last_line(tmp_path):
    joblog.log(tmp_path, "x", "完整")
    with joblog.log_path(tmp_path).open("a", encoding="utf-8") as f:
        f.write('{"ts": "2026-01-01T00:00:00", "scope": "x", "mess')   # 进程被中途终止
    assert [r["message"] for r in joblog.tail(tmp_path)] == ["完整"]


def test_tail_only_reads_the_end_of_a_huge_file(tmp_path, monkeypatch):
    monkeypatch.setattr(joblog, "TAIL_BYTES", 600)
    for i in range(200):
        joblog.log(tmp_path, "x", f"第{i}条")
    rows = joblog.tail(tmp_path, 1000)
    assert 0 < len(rows) < 200
    assert rows[-1]["message"] == "第199条"
    assert all(r["message"].startswith("第") for r in rows)   # 被截断的第一行已丢弃


def test_format_row_indents_multiline_messages():
    out = joblog.format_row({"ts": "2026-11-18T10:20:30", "level": "error", "message": "失败\n第二行"})
    assert out.startswith("10:20:30 ✘ 失败") and "    │ 第二行" in out


def test_bound_log_only_writes_inside_bind(tmp_path):
    joblog.bound_log("没有绑定，不写入")
    assert joblog.tail(tmp_path) == []
    with joblog.bind(tmp_path, "voice"):
        joblog.bound_log("配音完成")
        joblog.bound_log("某句失败")
        joblog.bound_log("普通信息")
    rows = joblog.tail(tmp_path)
    assert [(r["scope"], r["level"]) for r in rows] == [("voice", "ok"), ("voice", "error"), ("voice", "info")]
    joblog.bound_log("绑定已解除")
    assert len(joblog.tail(tmp_path)) == 3


def test_bind_is_per_thread(tmp_path):
    seen = {}

    def other():
        seen["ctx"] = joblog.current_ctx()

    with joblog.bind(tmp_path, "x"):
        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert seen["ctx"] is None


# ---------- 步骤事件 ----------

def _events(tmp_path, kind):
    return [r for r in joblog.tail(tmp_path, 1000) if r.get("event") == kind]


def test_step_writes_start_and_end_with_duration_and_detail(tmp_path):
    rid = joblog.run_start(tmp_path, "pipeline", "流水线 G1")
    with joblog.step(tmp_path, "pipeline", "script", "P01", heartbeat=False) as info:
        info["detail"] = "已写 3 段"
    joblog.run_end(tmp_path, "pipeline", rid, "流水线 G1", "ok")
    start, end = _events(tmp_path, "step_start")[0], _events(tmp_path, "step_end")[0]
    assert start["run_id"] == end["run_id"] == rid
    assert (start["stage"], start["item"]) == ("script", "P01")
    assert end["status"] == "ok" and end["detail"] == "已写 3 段" and end["seconds"] >= 0
    assert "script · P01：完成" in end["message"]


def test_step_failure_records_error_and_traceback_and_reraises(tmp_path):
    joblog.run_start(tmp_path, "pipeline", "r")
    with pytest.raises(ValueError, match="坏了"):
        with joblog.step(tmp_path, "pipeline", "voice", "P02", heartbeat=False):
            raise ValueError("坏了")
    end = _events(tmp_path, "step_end")[0]
    assert end["status"] == "failed" and "ValueError: 坏了" in end["error"]
    assert "Traceback" in end["traceback"] and end["level"] == "error"


def test_step_cancel_is_stopped_not_failed(tmp_path):
    joblog.run_start(tmp_path, "pipeline", "r")
    with pytest.raises(cancel.Cancelled):
        with joblog.step(tmp_path, "pipeline", "render", "P01", heartbeat=False):
            raise cancel.Cancelled("用户请求停止")
    end = _events(tmp_path, "step_end")[0]
    assert end["status"] == "stopped" and end["level"] == "warn" and not end["traceback"]


def test_step_status_can_be_overridden_to_waiting(tmp_path):
    joblog.run_start(tmp_path, "pipeline", "r")
    with joblog.step(tmp_path, "pipeline", "outline", heartbeat=False) as info:
        info["status"] = "waiting"
        info["detail"] = "C0 待确认"
    end = _events(tmp_path, "step_end")[0]
    assert end["status"] == "waiting" and end["level"] == "warn" and "C0 待确认" in end["message"]


def test_heartbeat_writes_still_running_lines(tmp_path):
    with joblog.step(tmp_path, "x", "slow", every=0.05):
        time.sleep(0.3)
    beats = [r for r in joblog.tail(tmp_path, 100) if "仍在进行" in r["message"]]
    assert len(beats) >= 2
    n = len(joblog.tail(tmp_path, 100))
    time.sleep(0.2)
    assert len(joblog.tail(tmp_path, 100)) == n   # 步骤结束后心跳停止


# ---------- 时间表 ----------

def _write_run(tmp_path, *, finish=True, fail_step=False):
    rid = joblog.run_start(tmp_path, "pipeline", "流水线 G1（运行到 export）")
    with joblog.step(tmp_path, "pipeline", "outline", heartbeat=False):
        pass
    with joblog.step(tmp_path, "pipeline", "script", heartbeat=False):
        with joblog.step(tmp_path, "pipeline", "script", "P01", heartbeat=False):
            pass
        if fail_step:
            with pytest.raises(RuntimeError):
                with joblog.step(tmp_path, "pipeline", "script", "P02", heartbeat=False):
                    raise RuntimeError("模型超时")
    if finish:
        joblog.run_end(tmp_path, "pipeline", rid, "流水线 G1（运行到 export）",
                       "failed" if fail_step else "ok", "模型超时" if fail_step else None)
    return rid


def test_timeline_groups_steps_per_run_with_substeps(tmp_path):
    _write_run(tmp_path)
    runs = timeline.build_timeline(tmp_path)
    assert len(runs) == 1 and runs[0]["status"] == "ok"
    labels = [(s["stage"], s["item"], s["status"]) for s in runs[0]["steps"]]
    assert labels == [("outline", None, "ok"), ("script", None, "ok"), ("script", "P01", "ok")]
    rows = timeline.step_rows(runs[0])
    assert any(r["步骤"].startswith("　└ P01") for r in rows)     # 子步骤缩进显示
    assert all(r["状态"].startswith("✅") for r in rows)


def test_timeline_failed_run_keeps_reason_and_traceback(tmp_path):
    _write_run(tmp_path, fail_step=True)
    run = timeline.build_timeline(tmp_path)[0]
    assert run["status"] == "failed" and run["error"] == "模型超时"
    bad = [s for s in run["steps"] if s["status"] == "failed"]
    assert bad and bad[0]["item"] == "P02" and "RuntimeError" in bad[0]["error"]
    assert "Traceback" in bad[0]["traceback"]


def test_timeline_open_run_is_running_only_when_busy(tmp_path):
    joblog.run_start(tmp_path, "pipeline", "r")
    ctx = joblog.step(tmp_path, "pipeline", "voice", "P01", heartbeat=False)
    ctx.__enter__()                      # 步骤已开始、尚未结束
    live = timeline.build_timeline(tmp_path, busy=True, now=datetime.now() + timedelta(seconds=5))[0]
    assert live["status"] == "running" and live["steps"][0]["status"] == "running"
    assert live["steps"][0]["seconds"] >= 4 and live["seconds"] >= 4
    dead = timeline.build_timeline(tmp_path, busy=False)[0]
    assert dead["status"] == "interrupted" and dead["steps"][0]["status"] == "interrupted"


def test_timeline_is_incremental_and_ignores_plain_lines(tmp_path):
    rid = joblog.run_start(tmp_path, "pipeline", "r")
    joblog.log(tmp_path, "pipeline", "普通日志，不是事件")
    first = timeline.build_timeline(tmp_path, busy=True)
    assert first[0]["status"] == "running" and first[0]["steps"] == []
    with joblog.step(tmp_path, "pipeline", "outline", heartbeat=False):
        pass
    joblog.run_end(tmp_path, "pipeline", rid, "r", "ok")
    second = timeline.build_timeline(tmp_path)
    assert second[0]["status"] == "ok" and len(second[0]["steps"]) == 1
    assert len(timeline.read_events(tmp_path)) == 4    # run_start, step_start, step_end, run_end


def test_timeline_rereads_after_log_truncation(tmp_path):
    _write_run(tmp_path)
    assert timeline.build_timeline(tmp_path)
    joblog.log_path(tmp_path).write_text("", encoding="utf-8")
    assert timeline.build_timeline(tmp_path) == []


def test_timeline_scope_filter_and_order_newest_first(tmp_path):
    a = joblog.run_start(tmp_path, "pipeline", "老的")
    joblog.run_end(tmp_path, "pipeline", a, "老的", "ok", started_at="2026-01-01T00:00:00")
    time.sleep(1.1)
    b = joblog.run_start(tmp_path, "script", "新的")
    joblog.run_end(tmp_path, "script", b, "新的", "ok")
    runs = timeline.build_timeline(tmp_path)
    assert [r["run"] for r in runs] == ["新的", "老的"]
    assert [r["run"] for r in timeline.build_timeline(tmp_path, scopes=["pipeline"])] == ["老的"]


def test_fmt_seconds():
    assert timeline.fmt_seconds(None) == "—"
    assert timeline.fmt_seconds(5.25) == "5.2 秒" or timeline.fmt_seconds(5.25) == "5.3 秒"
    assert timeline.fmt_seconds(125) == "2 分 5 秒"
    assert timeline.fmt_seconds(3700) == "1 小时 1 分"


def test_recently_active(tmp_path):
    assert joblog.recently_active(tmp_path) is False
    joblog.log(tmp_path, "pipeline", "x")
    assert joblog.recently_active(tmp_path) is True
    assert joblog.recently_active(tmp_path, now=datetime.now() + timedelta(hours=1)) is False


# ---------- 流水线埋点 ----------

@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    from article_to_video.planning.runner import confirm_spine, plan_outline
    plan_outline(pdir, engine="rules")
    confirm_spine(pdir)
    return pdir


def test_pipeline_writes_run_steps_and_substeps(fresh):
    st = run_pipeline(fresh, until="voice", gear="G1", llm=ScriptLLM(good_script), tts=SilenceTTS())
    assert st["state"] == "done" and st["run_id"]
    run = timeline.build_timeline(fresh)[0]
    assert run["run_id"] == st["run_id"] and run["status"] == "ok"
    stages = [s["stage"] for s in run["steps"] if not s["item"]]
    assert stages == ["outline", "script", "voice"]
    items = {(s["stage"], s["item"]) for s in run["steps"] if s["item"]}
    assert ("script", "P01") in items and ("voice", "P01") in items
    assert all(s["status"] == "ok" for s in run["steps"])
    msgs = [r["message"] for r in joblog.tail(fresh, 500, ["pipeline"])]
    assert any("挡位 G1" in m for m in msgs) and any("流水线 G1" in m and "完成" in m for m in msgs)


def test_pipeline_second_run_logs_skips(fresh):
    llm = ScriptLLM(good_script)
    run_pipeline(fresh, until="script", gear="G1", llm=llm)
    run_pipeline(fresh, until="script", gear="G1", llm=llm)
    msgs = [r["message"] for r in joblog.tail(fresh, 800, ["pipeline"])]
    assert any("口播稿已存在，跳过" in m for m in msgs)
    assert len(timeline.build_timeline(fresh)) == 2


def test_pipeline_waiting_is_not_a_failure(fresh):
    st = run_pipeline(fresh, until="script", gear="G0", llm=ScriptLLM(good_script))
    assert st["state"] == "waiting"
    run = timeline.build_timeline(fresh)[0]
    assert run["status"] == "waiting" and "待人工确认" in run["error"]
    assert [s["status"] for s in run["steps"] if not s["item"]][-1] == "waiting"


def test_pipeline_failure_records_stage_error(fresh):
    st = run_pipeline(fresh, until="script", gear="G1", llm=None)     # 写稿需要文本模型
    assert st["state"] == "failed"
    run = timeline.build_timeline(fresh)[0]
    assert run["status"] == "failed" and "文本模型" in run["error"]
    failed = [s for s in run["steps"] if s["status"] == "failed"]
    assert failed and failed[-1]["stage"] == "script"
    assert any(r["level"] == "error" for r in joblog.tail(fresh, 200, ["pipeline"]))


def test_pipeline_stop_is_recorded_as_stopped(fresh):
    ev = threading.Event()
    ev.set()
    cancel.bind(ev)
    try:
        st = run_pipeline(fresh, until="script", gear="G1", llm=ScriptLLM(good_script))
    finally:
        cancel.bind(threading.Event())
    assert st["state"] == "stopped"
    assert timeline.build_timeline(fresh)[0]["status"] == "stopped"


def test_pipeline_status_is_saved_when_started_and_after_each_stage(fresh):
    from article_to_video.pipeline.run import read_status
    seen = []

    class Spy(ScriptLLM):
        def ask(self, prompt, system=""):
            seen.append(read_status(fresh))    # 写稿进行中，另一个进程读到的状态
            return super().ask(prompt, system)

    run_pipeline(fresh, until="script", gear="G1", llm=Spy(good_script))
    assert seen and seen[0]["state"] == "running" and seen[0]["steps"][0]["stage"] == "outline"


# ---------- 后台任务 ----------

def test_background_job_writes_start_and_end_to_log(tmp_path):
    from ui import jobs
    done = threading.Event()

    def work():
        joblog.bound_log("任务内部的过程信息")
        done.set()

    assert jobs.start(tmp_path, "测试任务", work, scope="voice")
    done.wait(2)
    for _ in range(50):
        st = jobs.status(tmp_path)
        if st["state"] != "running":
            break
        time.sleep(0.05)
    msgs = [r["message"] for r in joblog.tail(tmp_path, 50, ["voice"])]
    assert msgs[0].startswith("后台任务开始") and "任务内部的过程信息" in msgs[1]
    assert msgs[-1].startswith("后台任务完成")


def test_background_job_failure_logs_traceback(tmp_path):
    from ui import jobs

    def boom():
        raise RuntimeError("炸了")

    jobs.start(tmp_path, "会失败的任务", boom, scope="job")
    for _ in range(50):
        if jobs.status(tmp_path)["state"] != "running":
            break
        time.sleep(0.05)
    err = [r for r in joblog.tail(tmp_path, 50) if r["level"] == "error"][-1]
    assert "炸了" in err["message"] and "Traceback" in err["traceback"]
    assert jobs.status(tmp_path)["state"] == "error"


# ---------- 界面 ----------

def test_log_html_escapes_content_and_autoscrolls():
    from ui.joblog_panel import build_log_html
    out = build_log_html("<script>alert(1)</script>\n第二行", 240)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out and "<script>alert(1)" not in out
    assert "scrollTop = el.scrollHeight" in out and "height:240px" in out


def test_run_state_detects_external_process(tmp_path):
    pytest.importorskip("streamlit")
    from ui.joblog_panel import run_state
    assert run_state(tmp_path, ["pipeline"])["busy"] is False
    joblog.run_start(tmp_path, "pipeline", "命令行启动的运行")      # 另一个进程刚写了日志、尚未结束
    st = run_state(tmp_path, ["pipeline"])
    assert st["external"] is True and st["local"] is False and st["busy"] is True
    timeline._CACHE.clear()
    old = datetime.now() + timedelta(hours=1)
    assert joblog.recently_active(tmp_path, ["pipeline"], now=old) is False


def test_cockpit_page_renders_monitor(monkeypatch, tmp_path):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from conftest import PROJECT_DIR
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    make_project(tmp_path)
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=60)
    at.run()
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    assert not at.exception
    assert any("流水线运行监控" in m.value for m in at.markdown)
    assert any("步骤时间表" in m.value for m in at.markdown)
