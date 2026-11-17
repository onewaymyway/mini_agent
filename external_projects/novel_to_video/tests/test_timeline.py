"""运行时间线：从结构化日志事件汇总每章每次尝试、每个主步骤的起止时间、用时、状态、完整失败原因与重试。"""
import pytest

from novel_to_video import joblog, timeline


def _stage(pdir, no, stage, fail=None):
    try:
        with joblog.heartbeat(pdir, "chapter", f"第 {no} 章 {stage}", chapter=no, stage=stage):
            if fail:
                raise fail
    except Exception:
        pass


def test_timeline_attempts_stages_retry_and_full_error(tmp_path):
    long_err = "RuntimeError: edge-tts 配音失败：" + "x" * 800
    joblog.log(tmp_path, "chapter", "第 1 章开始运行", event="chapter_start", chapter=1, started_at="2026-11-17T08:00:00")
    _stage(tmp_path, 1, "C-1/C-2 剧本")
    _stage(tmp_path, 1, "C-6 配音", fail=RuntimeError(long_err[len("RuntimeError: "):]))
    joblog.log(tmp_path, "chapter", "结束", level="error", event="chapter_end", chapter=1, status="failed",
               error="C-6 配音：" + long_err, ended_at="2026-11-17T08:03:00")
    joblog.log(tmp_path, "queue", "重试", level="warn", event="retry", chapter=1, attempt=1, error="C-6 配音：失败",
               wait_sec=60, at="2026-11-17T08:03:00", next_at="2026-11-17T08:04:00")
    joblog.log(tmp_path, "chapter", "第 1 章开始运行", event="chapter_start", chapter=1, started_at="2026-11-17T08:04:00")
    _stage(tmp_path, 1, "C-6 配音")
    joblog.log(tmp_path, "chapter", "结束", level="ok", event="chapter_end", chapter=1, status="done",
               ended_at="2026-11-17T08:05:00")

    tl = timeline.build_timeline(tmp_path)[1]
    assert len(tl["attempts"]) == 2 and tl["retry_count"] == 1
    a1, a2 = tl["attempts"]
    assert a1["status"] == "failed" and a1["error"].endswith("x" * 800)           # 完整原因，不截断
    assert [s["status"] for s in a1["stages"]] == ["ok", "failed"]
    assert a1["stages"][1]["error"].count("x") == 800
    assert a1["stages"][1]["seconds"] is not None and a1["stages"][1]["started_at"] and a1["stages"][1]["ended_at"]
    assert a2["status"] == "done" and a2["stages"][0]["stage"] == "C-6 配音"
    assert tl["retries"][0]["wait_sec"] == 60 and tl["retries"][0]["next_at"].endswith("08:04:00")
    assert a1["seconds"] == 180.0


def test_unfinished_stage_running_vs_interrupted(tmp_path):
    joblog.log(tmp_path, "chapter", "开始", event="chapter_start", chapter=2, started_at="2026-11-17T08:00:00")
    joblog.log(tmp_path, "chapter", "开始", event="stage_start", chapter=2, stage="C-6 配音", started_at="2026-11-17T08:00:10")
    from datetime import datetime
    now = datetime.fromisoformat("2026-11-17T08:01:10")
    busy = timeline.build_timeline(tmp_path, busy=True, now=now)[2]["attempts"][0]
    assert busy["status"] == "running" and busy["stages"][0]["status"] == "running" and busy["stages"][0]["seconds"] == 60.0
    idle = timeline.build_timeline(tmp_path, busy=False, now=now)[2]["attempts"][0]
    assert idle["status"] == "interrupted" and idle["stages"][0]["status"] == "interrupted"


def test_stopped_stage_and_old_logs_ignored(tmp_path):
    joblog.log(tmp_path, "chapter", "旧日志行，没有 event")            # 旧格式：忽略
    from novel_to_video import cancel

    with pytest.raises(BaseException):
        with joblog.heartbeat(tmp_path, "chapter", "第 3 章 C-6 配音", chapter=3, stage="C-6 配音"):
            raise cancel.Cancelled()
    st = timeline.build_timeline(tmp_path)[3]["attempts"][0]["stages"][0]
    assert st["status"] == "stopped" and not st["error"]
    assert timeline.build_timeline(tmp_path / "none") == {}
