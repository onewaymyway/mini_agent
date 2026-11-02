"""重新合成测试：只用已有原始视频与配音重做声音、画面、字幕；不调用视频接口，不改变原始视频。

章节流程沿用 test_chapter_pipeline 的夹具（stub 视频与配音，真实 ffmpeg）。
"""

import json
import time

import pytest

from novel_to_video import chapter_io as cio
from novel_to_video import jobs
from novel_to_video import shot_review as sr
from novel_to_video import tool_api
from novel_to_video.providers.video import StubVideoProvider
from novel_to_video.steps import chapter_compose as cc
from novel_to_video.steps import global_visual as gv

from test_chapter_pipeline import ChapterLLM, _run, project  # noqa: F401  (fixture)


def _finish(proj):
    """把第 1 章跑到 done：补建角色确认文本与定妆图、通过全部镜头。"""
    assert _run(proj, ChapterLLM())["status"] == "waiting_assets"
    chars = json.loads((proj / "global/characters.json").read_text(encoding="utf-8"))["characters"]
    new = next(c for c in chars if c.get("auto_added_chapter") == 1)
    gv.approve(proj, "character", new["id"], "text")
    assert _run(proj, ChapterLLM())["status"] == "waiting_assets"
    gv.approve(proj, "character", new["id"], "portrait")
    assert _run(proj, ChapterLLM())["status"] == "waiting_review"
    for sh in cio.load_shots(proj, 1)["shots"]:
        sr.approve(proj, 1, sh["id"])
    r = _run(proj, ChapterLLM())
    assert r["status"] == "done", r
    return proj / "chapters/001/chapter_001.mp4"


def _shot_ids(proj):
    return [sh["id"] for sh in cio.load_shots(proj, 1)["shots"]]


def test_recompose_rebuilds_clips_and_output_without_touching_raw(project, monkeypatch):
    out = _finish(project)
    cdir = project / "chapters/001"
    raw_before = {sid: (cdir / "clips" / "raw" / f"{sid}.mp4").read_bytes() for sid in _shot_ids(project)}

    # 视频接口不应被调用：如果被调用，测试直接失败
    def no_generation(*a, **k):
        raise AssertionError("重新合成不应重新生成视频")
    monkeypatch.setattr(StubVideoProvider, "generate", no_generation)

    # 删掉已拼合的镜头视频与成片，只保留原始视频与配音
    for sid in _shot_ids(project):
        (cdir / "clips" / f"{sid}.mp4").unlink()
    out.unlink()

    res = cc.recompose_chapter(project, 1)
    assert res["output"] and out.is_file()
    assert res["duration"] > 4
    for sid in _shot_ids(project):
        assert (cdir / "clips" / f"{sid}.mp4").is_file()             # 镜头视频已重新拟合
        assert (cdir / "clips" / "raw" / f"{sid}.mp4").read_bytes() == raw_before[sid]   # 原始视频一个字节都没变
    assert cio.chapter_row(project, 1)["status"] == "done"


def test_recompose_respects_subtitle_setting(project):
    _finish(project)
    assert tool_api.set_subtitles(str(project), False)["ok"]
    res = cc.recompose_chapter(project, 1)
    assert res["subtitles"]["enabled"] is False        # 设置在重新合成时生效
    assert (project / "chapters/001/chapter_001.mp4").is_file()
    assert not (project / "chapters/001/chapter_nosub.mp4").exists()

    assert tool_api.set_subtitles(str(project), True)["ok"]
    res = cc.recompose_chapter(project, 1)
    assert res["subtitles"]["enabled"] is True and res["subtitles"]["cues"] > 0


def test_recompose_refuses_while_shots_are_unapproved(project):
    _finish(project)
    sid = _shot_ids(project)[0]
    sr.reject(project, 1, sid, "重来一次")
    with pytest.raises(RuntimeError, match="未通过审核，不能重新合成"):
        cc.recompose_chapter(project, 1)


def test_recompose_reports_missing_raw_video(project):
    _finish(project)
    sid = _shot_ids(project)[0]
    (project / "chapters/001/clips/raw" / f"{sid}.mp4").unlink()
    with pytest.raises(RuntimeError, match="缺少原始视频"):
        cc.recompose_chapter(project, 1)


def test_tool_api_sync_result_and_error(project):
    _finish(project)
    ok = tool_api.recompose_chapter(str(project), 1)
    assert ok["ok"] and ok["data"]["output"]
    sid = _shot_ids(project)[0]
    (project / "chapters/001/clips/raw" / f"{sid}.mp4").unlink()
    bad = tool_api.recompose_chapter(str(project), 1)
    assert not bad["ok"] and "缺少原始视频" in bad["error"]["message"]


def test_background_batch_collects_per_chapter_results(project):
    _finish(project)
    sid = _shot_ids(project)[0]
    (project / "chapters/001/clips/raw" / f"{sid}.mp4").unlink()    # 第 1 章会失败（但不影响任务本身）
    res = tool_api.start_recompose_background(str(project), [1])
    assert res["ok"], res
    deadline = time.monotonic() + 30
    st = {}
    while time.monotonic() < deadline:
        st = jobs.status(project)
        if st.get("state") in ("done", "failed", "stopped"):
            break
        time.sleep(0.1)
    assert st["state"] == "done", st
    assert st["summary"]["failed"] == 1 and st["summary"].get("done", 0) == 0
