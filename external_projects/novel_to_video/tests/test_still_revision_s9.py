"""动态漫模式 S9：批量（G3 自动通过、预算）、修改回退、设置变化的过期标记、重新合成。"""
import json
from pathlib import Path

import pytest

from novel_to_video import batch
from novel_to_video import call_log
from novel_to_video import chapter_io as cio
from novel_to_video import chapter_pipeline as cp
from novel_to_video import gates
from novel_to_video import revision as rv
from novel_to_video import shot_review as sr
from novel_to_video import tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.steps import chapter_compose as cc
from novel_to_video.steps import chapter_segments as cseg
from novel_to_video.steps import chapter_stills as cs
from test_still_stills_s3 import _prepare, _project, _shots


def _meta(p: Path) -> dict:
    return json.loads((p / "project.json").read_text(encoding="utf-8"))


def _stills_approved_and_built(tmp_path):
    """动态漫项目：场景图已生成并全部通过，动效片段与章节视频已合成。"""
    p = _project(tmp_path)
    _prepare(p)
    cs.run_stills(p, 1, StubImageProvider())
    for sh in _shots(p):
        sr.approve(p, 1, sh["id"])
    cseg.run_segments(p, 1)
    cc.run_compose(p, 1)
    return p


@pytest.fixture
def built(tmp_path):
    return _stills_approved_and_built(tmp_path)


# ---------- 修改回退

def test_clip_only_regenerates_scene_image_as_new_version(built):
    sh = _shots(built)[0]
    sid = sh["id"]
    old_file = built / "chapters/001" / sh["still_file"]
    rv.invalidate(built, 1, sid, "clip_only")
    after = next(s for s in _shots(built) if s["id"] == sid)
    assert after["still_file"] is None and after["still_key"] is None and after["segment_key"] is None
    assert not (built / "chapters/001/clips" / f"{sid}.mp4").exists()     # 动效片段已清理
    assert old_file.is_file()                                             # 旧场景图保留作为历史
    assert sr.shot_states(built, 1)[sid] == "missing"

    r = cs.run_stills(built, 1, StubImageProvider())
    assert sid in r["generated"]
    new = next(s for s in _shots(built) if s["id"] == sid)
    assert new["still_file"] == f"stills/{sid}_v002.png"
    assert sr.shot_states(built, 1)[sid] == "pending"                     # 需要重新审核


def test_clip_only_does_not_touch_other_shots(built):
    ids = [s["id"] for s in _shots(built)]
    before = {s["id"]: s["still_file"] for s in _shots(built)}
    rv.invalidate(built, 1, ids[0], "clip_only")
    for s in _shots(built)[1:]:
        assert s["still_file"] == before[s["id"]]
        assert sr.shot_states(built, 1)[s["id"]] == "approved"


def test_prompt_level_clears_prompt_and_scene_image(built):
    sid = _shots(built)[0]["id"]
    rv.invalidate(built, 1, sid, "prompt")
    sh = next(s for s in _shots(built) if s["id"] == sid)
    assert sh["prompt_en"] is None and sh["still_key"] is None and sh["still_file"] is None


def test_dialogue_level_also_clears_audio(built):
    sid = _shots(built)[0]["id"]
    audio = built / "chapters/001/audio" / f"{sid}.wav"
    assert audio.is_file()
    rv.invalidate(built, 1, sid, "dialogue", touch_script=False)
    assert not audio.exists()
    sh = next(s for s in _shots(built) if s["id"] == sid)
    assert sh["audio_key"] is None and sh["still_key"] is None


# ---------- 设置变化：只让片段或合成过期，场景图不变

def test_motion_change_marks_segments_stale_but_keeps_scene_images(built):
    stills_before = {s["id"]: s["still_file"] for s in _shots(built)}
    r = tool_api.set_still_settings(str(built), motion="none", motion_strength="slow", transition="fade",
                                    transition_sec=0.4, silent_shot_sec=3)
    assert r["ok"], r
    assert r["data"]["stale_chapters"] == [1]
    st = cio.load_state(built, 1)["stages"]
    assert st["segments"]["status"] == "stale" and st["compose"]["status"] == "stale"
    assert {s["id"]: s["still_file"] for s in _shots(built)} == stills_before
    assert set(sr.shot_states(built, 1).values()) == {"approved"}        # 审核不受影响
    assert cio.load_index(built)["chapters"][0]["status"] == "stale"


def test_transition_change_marks_only_compose_stale(built):
    tool_api.set_still_settings(str(built), motion="auto_alternate", motion_strength="slow",
                                transition="cut", transition_sec=0.4, silent_shot_sec=3)
    st = cio.load_state(built, 1)["stages"]
    assert st["compose"]["status"] == "stale"
    assert st["segments"]["status"] != "stale"


def test_unchanged_settings_mark_nothing(built):
    before = cio.load_state(built, 1)["stages"]["compose"]["status"]
    r = tool_api.set_still_settings(str(built), motion="auto_alternate", motion_strength="slow",
                                    transition="fade", transition_sec=0.4, silent_shot_sec=3)
    assert r["ok"] and r["data"]["stale_chapters"] == []
    assert cio.load_state(built, 1)["stages"]["compose"]["status"] == before


# ---------- 重新合成：不调用生图，按新设置重建片段

def test_recompose_rebuilds_segments_without_image_calls(built):
    tool_api.set_still_settings(str(built), motion="none", motion_strength="slow", transition="fade",
                                transition_sec=0.4, silent_shot_sec=3)
    images_before = call_log.count_calls(built, "image")
    stills_before = {s["id"]: s["still_file"] for s in _shots(built)}
    res = cc.recompose_chapter(built, 1)
    assert res["output"] and (built / "chapters/001/chapter_001.mp4").is_file()
    assert call_log.count_calls(built, "image") == images_before          # 没有调用生图
    assert {s["id"]: s["still_file"] for s in _shots(built)} == stills_before
    assert {s["segment_motion"] for s in _shots(built)} == {"none"}
    assert cio.load_index(built)["chapters"][0]["status"] == "done"


def test_recompose_refused_when_a_scene_image_is_not_approved(built):
    rv.invalidate(built, 1, _shots(built)[0]["id"], "clip_only")
    with pytest.raises(RuntimeError, match="未通过审核"):
        cc.recompose_chapter(built, 1)


# ---------- 预算：只计图像与耗时

def test_still_budget_needs_image_and_minutes_not_video():
    gates.validate_budget({"max_image_calls": 3, "max_minutes": 10}, "G3", "still")
    with pytest.raises(gates.GateError):
        gates.validate_budget({"max_image_calls": 3, "max_minutes": 10}, "G3", "video")
    with pytest.raises(gates.GateError):
        gates.validate_budget({"max_minutes": 10}, "G3", "still")


def test_still_budget_ignores_video_usage():
    budget = {"max_image_calls": 5, "max_minutes": 60}
    gates.require_budget(budget, {"video": 999, "image": 1}, 1.0, "G3", mode="still")   # 不抛出
    with pytest.raises(gates.GateError, match="image"):
        gates.require_budget(budget, {"video": 0, "image": 5}, 1.0, "G3", mode="still")


def test_scene_images_count_toward_image_budget(built):
    # 每个场景图生成都会记录一次 image 调用（计入图像预算）
    n = len(_shots(built))
    before = call_log.count_calls(built, "image")
    rv.invalidate(built, 1, _shots(built)[0]["id"], "clip_only")
    cs.run_stills(built, 1, StubImageProvider())
    assert call_log.count_calls(built, "image") == before + 1
    assert n >= 1


def test_set_gear_accepts_still_budget_without_video_limit(built):
    r = tool_api.set_gear(str(built), "G3", {"max_image_calls": 10, "max_minutes": 30})
    assert r["ok"], r
    assert "max_video_calls" not in _meta(built)["budget"] or _meta(built)["budget"]["max_video_calls"] is None


# ---------- 批量：G3 自动通过使用场景图

def test_g3_auto_approves_shots_with_scene_images(tmp_path):
    p = _project(tmp_path)
    _prepare(p)
    cs.run_stills(p, 1, StubImageProvider())
    approved = batch.auto_approve_shots(p, 1)
    assert approved == [s["id"] for s in _shots(p)]
    assert set(sr.shot_states(p, 1).values()) == {"approved"}


def test_check_budget_for_still_uses_image_usage(tmp_path):
    p = _project(tmp_path)
    budget = {"max_image_calls": 1000, "max_minutes": 1000, "max_video_calls": None}
    assert batch.check_budget(p, "G3", budget) is None
