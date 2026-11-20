"""R26 清空重跑后成片可见：成片列表含多期与全集；流水线停在终审时任务不显示“完成”；导出前需终审时阻止空跑。"""
import time

from article_to_video.compose.export import list_exports
from article_to_video.pipeline.gates import export_needs_approval
from article_to_video.pipeline.regenerate import outcome_of
from ui import jobs


def test_all_exports_are_listed_including_episodes_and_merged(tmp_path):
    ex = tmp_path / "exports"
    ex.mkdir()
    for name in ("视频.mp4", "视频_第1期.mp4", "视频_第2期.mp4", "视频_全集.mp4", "视频.srt", "视频_cover.png"):
        (ex / name).write_bytes(b"x")
        time.sleep(0.01)
    names = [p.name for p in list_exports(tmp_path)]
    assert sorted(names) == sorted(["视频.mp4", "视频_第1期.mp4", "视频_第2期.mp4", "视频_全集.mp4"])
    assert names[0] == "视频_全集.mp4"              # 最新的在前


def test_no_exports_folder_means_empty_list(tmp_path):
    assert list_exports(tmp_path) == []


def test_waiting_for_approval_is_not_reported_as_done():
    out = outcome_of({"state": "waiting", "waiting": {"stage": "export", "detail": "终审待人工确认"}})
    assert out["state"] == "waiting"
    assert "导出" in out["message"] and "终审" in out["message"]


def test_done_reports_number_of_videos():
    out = outcome_of({"state": "done", "outputs": ["a.mp4", "b.mp4"]})
    assert out == {"state": "done", "message": "完成：已导出 2 个视频"}


def test_failures_are_errors():
    assert outcome_of({"state": "budget_exceeded", "error": "超出生图预算"})["state"] == "error"
    assert outcome_of({"state": "failed", "error": "ffmpeg 失败"})["state"] == "error"
    assert outcome_of({"state": "stopped"})["state"] == "stopped"


def test_gear_with_export_checkpoint_needs_approval_before_running():
    assert export_needs_approval("G0", approve=False) is True
    assert export_needs_approval("G1", approve=False) is True
    assert export_needs_approval("G0", approve=True) is False
    assert export_needs_approval("G2", approve=False) is False       # G2 全自动


def _wait(pdir, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        j = jobs.status(pdir)
        if j and j["state"] != "running":
            return j
        time.sleep(0.02)
    raise AssertionError("后台任务未结束")


def test_job_state_follows_the_pipeline_outcome(tmp_path):
    pdir = tmp_path / "p"
    pdir.mkdir()
    jobs.start(pdir, "流水线 G0", lambda: {"state": "waiting", "message": "停在「导出」等待人工确认"}, scope="pipeline")
    j = _wait(pdir)
    assert j["state"] == "waiting" and "等待人工确认" in j["message"]


def test_job_without_result_is_still_done(tmp_path):
    pdir = tmp_path / "q"
    pdir.mkdir()
    jobs.start(pdir, "任务", lambda: None, scope="job")
    j = _wait(pdir)
    assert j["state"] == "done" and j["message"] == "完成"
