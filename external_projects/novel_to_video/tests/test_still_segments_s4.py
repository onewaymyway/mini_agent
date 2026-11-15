"""动态漫模式 S4：C-8b 动效片段（动效滤镜 golden、真实 ffmpeg 渲染、片段步骤、整章流程）。"""
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from conftest import build_export
from novel_to_video import chapter_io as cio
from novel_to_video import chapter_pipeline as cp
from novel_to_video import render_mode as RM
from novel_to_video import shot_review as sr
from novel_to_video import tool_api
from novel_to_video.media import ffmpeg as mf
from novel_to_video.media import motion as MO
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.tts import SilenceTTS
from novel_to_video.providers.video import StubVideoProvider
from novel_to_video.steps import chapter_segments as cseg
from novel_to_video.steps import chapter_stills as cs
from novel_to_video.steps.chapter_clips import run_prompts
from test_chapter_pipeline import CHAPTER_TEXT, ChapterLLM
from test_still_stills_s3 import _prepare, _project, _shots


# ---------- 纯规则（不调用 ffmpeg）

def test_auto_alternate_pushes_then_pulls_by_shot_index():
    assert [MO.resolve_motion("auto_alternate", i) for i in range(4)] == \
        ["push_in", "pull_out", "push_in", "pull_out"]


def test_explicit_motion_names_pass_through_and_unknown_is_rejected():
    assert MO.resolve_motion("push_in", 5) == "push_in"
    assert MO.resolve_motion("none", 1) == "none"
    with pytest.raises(ValueError):
        MO.resolve_motion("spin", 0)


def test_zoom_filter_golden_push_in_slow():
    assert MO.zoom_filter("push_in", frames=48, w=720, h=1280, fps=24, strength="slow") == (
        "scale=1440:2560:force_original_aspect_ratio=increase,crop=1440:2560,"
        "zoompan=z='min(zoom+0.001667,1.0800)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        "d=1:s=720x1280:fps=24,setsar=1")


def test_zoom_filter_golden_pull_out_slow():
    assert MO.zoom_filter("pull_out", frames=48, w=720, h=1280, fps=24, strength="slow") == (
        "scale=1440:2560:force_original_aspect_ratio=increase,crop=1440:2560,"
        "zoompan=z='max(1.0800-0.001667*on,1)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        "d=1:s=720x1280:fps=24,setsar=1")


def test_zoom_filter_golden_none_and_medium_strength():
    assert "zoompan=z='1':" in MO.zoom_filter("none", frames=24, w=720, h=1280, fps=24)
    med = MO.zoom_filter("push_in", frames=50, w=720, h=1280, fps=24, strength="medium")
    assert "min(zoom+0.003000,1.1500)" in med


def test_zoom_filter_rejects_unsupported_motion_and_strength():
    with pytest.raises(ValueError):
        MO.zoom_filter("pan_left", frames=10, w=720, h=1280, fps=24)
    with pytest.raises(ValueError):
        MO.zoom_filter("push_in", frames=10, w=720, h=1280, fps=24, strength="wild")


def test_still_settings_defaults_and_overrides():
    assert RM.still_settings(None)["motion"] == "auto_alternate"
    s = RM.still_settings({"still": {"motion": "none", "silent_shot_sec": 5}})
    assert s["motion"] == "none" and s["silent_shot_sec"] == 5 and s["motion_strength"] == "slow"


# ---------- 真实 ffmpeg 渲染

def _ffprobe_dims(path: Path):
    out = subprocess.run([mf.ffprobe(), "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def _png(path: Path, size=(360, 640)):
    Image.new("RGB", size, (90, 120, 160)).save(path, format="PNG")
    return path


def test_render_segment_with_audio_has_audio_plus_margin_duration(tmp_path):
    still = _png(tmp_path / "s.png")
    wav = tmp_path / "a.wav"
    mf.write_silence_wav(wav, 2.0)
    out = tmp_path / "seg.mp4"
    MO.render_segment(still, wav, out, seconds=2.4, motion="push_in", strength="slow", w=720, h=1280, fps=24)
    assert out.is_file()
    assert abs(mf.probe_duration(out) - 2.4) < 0.15
    assert _ffprobe_dims(out) == (720, 1280)


def test_render_segment_silent_uses_given_duration(tmp_path):
    out = tmp_path / "silent.mp4"
    MO.render_segment(_png(tmp_path / "s.png"), None, out, seconds=3.0, motion="pull_out",
                      strength="slow", w=720, h=1280, fps=24)
    assert abs(mf.probe_duration(out) - 3.0) < 0.15


# ---------- 片段步骤（动态漫项目，场景图已审核）

@pytest.fixture
def ready(tmp_path):
    p = _project(tmp_path)
    _prepare(p)
    cs.run_stills(p, 1, StubImageProvider())
    for sh in _shots(p):
        sr.approve(p, 1, sh["id"])
    return p


def _seg(p, sid):
    return p / "chapters/001/clips" / f"{sid}.mp4"


def test_segments_built_per_shot_with_audio_timing_and_alternating_motion(ready):
    r = cseg.run_segments(ready, 1)
    shots = _shots(ready)
    assert r["failed"] == [] and r["generated"] == [sh["id"] for sh in shots]
    for i, sh in enumerate(shots):
        assert _seg(ready, sh["id"]).is_file()
        wav = ready / "chapters/001/audio" / f"{sh['id']}.wav"
        assert abs(sh["segment_seconds"] - (mf.probe_duration(wav) + 0.4)) < 0.01   # 不裁剪
        assert sh["segment_motion"] == ("push_in" if i % 2 == 0 else "pull_out")
    assert cio.load_state(ready, 1)["stages"]["segments"]["status"] == "done"


def test_segments_rerun_is_idempotent(ready):
    cseg.run_segments(ready, 1)
    assert cseg.run_segments(ready, 1)["generated"] == []


def test_segments_skipped_without_scene_image(tmp_path):
    p = _project(tmp_path / "x")
    _prepare(p)
    r = cseg.run_segments(p, 1)              # 还没有场景图
    assert r["generated"] == [] and len(r["skipped"]) == len(_shots(p))


def test_segment_rebuilt_when_scene_image_changes(ready):
    cseg.run_segments(ready, 1)
    sid = _shots(ready)[0]["id"]
    old_key = next(s for s in _shots(ready) if s["id"] == sid)["segment_key"]
    sr.edit(ready, 1, sid, visual_hint_en="a different street at dusk")
    run_prompts(ready, 1, None)
    cs.run_stills(ready, 1, StubImageProvider())         # 新场景图
    r = cseg.run_segments(ready, 1)
    assert r["generated"] == [sid]
    new_key = next(s for s in _shots(ready) if s["id"] == sid)["segment_key"]
    assert new_key != old_key


def test_silent_shot_uses_configured_default_duration(ready):
    sid = _shots(ready)[0]["id"]
    (ready / "chapters/001/audio" / f"{sid}.wav").unlink()
    cseg.run_segments(ready, 1)
    sh = next(s for s in _shots(ready) if s["id"] == sid)
    assert sh["segment_seconds"] == 3
    assert abs(mf.probe_duration(_seg(ready, sid)) - 3.0) < 0.15


def test_project_setting_changes_motion(ready):
    import json
    meta_p = ready / "project.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["still"] = {"motion": "none"}
    meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    cseg.run_segments(ready, 1)
    assert {s["segment_motion"] for s in _shots(ready)} == {"none"}


# ---------- 整章流程（动态漫）

def _run(p):
    return cp.run_chapter(p, 1, text_llm=ChapterLLM(), image=StubImageProvider(),
                          video=StubVideoProvider(), tts=SilenceTTS())


def test_still_chapter_full_flow_done_and_duration_matches_segments(tmp_path):
    from novel_to_video.providers.text import RuleTextLLM
    from novel_to_video.steps import global_visual as gv
    src = build_export(tmp_path / "src", chapters=(1,))
    (src / "正文" / "第001章 测试章1.md").write_text(CHAPTER_TEXT, encoding="utf-8")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试",
                                              options={"render_mode": "still"})
    p = Path(res["data"]["project_dir"])
    gv.visual_text_pass(p, RuleTextLLM())
    gv.portrait_pass(p, StubImageProvider())

    import json
    r = _run(p)
    assert r["status"] == "waiting_assets", r
    chars = json.loads((p / "global/characters.json").read_text(encoding="utf-8"))["characters"]
    new = next(c for c in chars if c.get("auto_added_chapter") == 1)
    gv.approve(p, "character", new["id"], "text")
    assert _run(p)["status"] == "waiting_assets"
    gv.approve(p, "character", new["id"], "portrait")

    r = _run(p)
    assert r["status"] == "waiting_review", r               # 场景图已生成，等待审核
    assert cio.load_state(p, 1)["stages"]["stills"]["status"] == "done"
    assert all(sh.get("still_file") for sh in _shots(p))

    for sh in _shots(p):
        sr.approve(p, 1, sh["id"])
    r = _run(p)
    assert r["status"] == "done", r
    assert cio.load_state(p, 1)["stages"]["segments"]["status"] == "done"
    out = p / "chapters/001/chapter_001.mp4"
    assert out.is_file()
    total = sum(sh["segment_seconds"] for sh in _shots(p))
    assert abs(mf.probe_duration(out) - total) < 0.5
    from novel_to_video import video_format
    fmt = video_format.load(p)                                 # 默认横屏 720P，按项目格式输出
    assert _ffprobe_dims(out) == (fmt.width, fmt.height)
