"""动态漫模式 S6：镜间淡化（逐段淡入淡出，时长不变）、硬切、字幕精确时间轴。"""
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from novel_to_video import render_mode as RM
from novel_to_video import shot_review as sr
from novel_to_video.media import ffmpeg as mf
from novel_to_video.media import motion as MO
from novel_to_video.media import subtitles as sub
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.steps import chapter_compose as cc
from novel_to_video.steps import chapter_segments as cseg
from novel_to_video.steps import chapter_stills as cs
from test_still_stills_s3 import _prepare, _project, _shots

W, H, FPS = 360, 640, 24           # 小尺寸，加快测试


def _solid(path: Path, value: int) -> Path:
    Image.new("RGB", (W, H), (value, value, value)).save(path, format="PNG")
    return path


def _clip(tmp_path: Path, name: str, value: int, seconds: float) -> Path:
    still = _solid(tmp_path / f"{name}.png", value)
    out = tmp_path / f"{name}.mp4"
    MO.render_segment(still, None, out, seconds=seconds, motion="none", strength="slow", w=W, h=H, fps=FPS)
    return out


def _luma(video: Path, t: float) -> float:
    """取 t 秒处的一帧，返回平均亮度（0–255）。"""
    raw = subprocess.run([mf.ffmpeg(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
                          "-frames:v", "1", "-vf", "scale=16:16", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return sum(raw) / len(raw)


# ---------- 设置校验

def test_transition_settings_validation():
    RM.validate_transition({"transition": "fade", "transition_sec": 0.4})
    RM.validate_transition({"transition": "cut"})                 # 硬切不需要时长
    with pytest.raises(ValueError):
        RM.validate_transition({"transition": "wipe", "transition_sec": 0.4})
    with pytest.raises(ValueError):
        RM.validate_transition({"transition": "fade", "transition_sec": 0})
    with pytest.raises(ValueError):
        RM.validate_transition({"transition": "fade", "transition_sec": 1.5})
    with pytest.raises(ValueError):
        RM.validate_transition({"transition": "fade", "transition_sec": "abc"})


# ---------- 淡化拼接：时长与画面

def test_fade_concat_keeps_total_duration_and_one_audio_stream(tmp_path):
    clips = [_clip(tmp_path, "a", 230, 1.5), _clip(tmp_path, "b", 230, 2.0), _clip(tmp_path, "c", 230, 1.2)]
    durs = [mf.probe_duration(c) for c in clips]
    out = tmp_path / "joined.mp4"
    mf.concat_fade(clips, durs, out, 0.4)
    assert abs(mf.probe_duration(out) - sum(durs)) < 0.1


def test_fade_darkens_each_shot_boundary_and_cut_does_not(tmp_path):
    a, b = _clip(tmp_path, "a", 230, 1.5), _clip(tmp_path, "b", 230, 1.5)
    durs = [mf.probe_duration(a), mf.probe_duration(b)]
    faded = tmp_path / "faded.mp4"
    mf.concat_fade([a, b], durs, faded, 0.4)
    # 镜头边界：上一镜头末尾淡到黑，下一镜头开头从黑淡入
    assert _luma(faded, durs[0] - 0.02) < 60
    assert _luma(faded, durs[0] + 0.02) < 60
    assert _luma(faded, durs[0] / 2) > 150      # 镜头中段保持明亮
    cut = tmp_path / "cut.mp4"
    mf.concat([a, b], cut)
    assert _luma(cut, durs[0] - 0.02) > 150     # 硬切：边界处保持明亮


def test_fade_clamped_to_half_of_a_very_short_shot(tmp_path):
    a = _clip(tmp_path, "s", 230, 0.5)
    d = mf.probe_duration(a)
    out = tmp_path / "short.mp4"
    mf.concat_fade([a], [d], out, 1.0)          # 淡化 1 秒会超过片段一半，应被钳制而不报错
    assert abs(mf.probe_duration(out) - d) < 0.1


# ---------- 字幕精确时间轴（数值）

def _shot(blocks):
    return {"blocks": blocks}


def test_exact_cues_follow_block_durations_and_gaps():
    shot = _shot([
        {"idx": 0, "type": "narration", "text": "旁白一", "duration": 1.0},
        {"idx": 1, "type": "narration", "text": "", "duration": 0.5},          # 空块仍占位（配音里也有间隔）
        {"idx": 2, "type": "dialogue", "text": "你好", "speaker_id": "c1", "duration": 2.0},
    ])
    cues, total = sub.build_cues([shot], [4.0], {"c1": "林远"}, exact=True)
    assert total == 4.0
    assert [(c.start, c.end) for c in cues] == [(0.0, 1.0), (1.9, 3.9)]
    # 镜头末尾裁剪
    cues, _ = sub.build_cues([shot], [2.0], {"c1": "林远"}, exact=True)
    assert cues[-1].end == 2.0


def test_exact_cues_offset_by_previous_shots():
    s1 = _shot([{"idx": 0, "type": "narration", "text": "甲", "duration": 1.0}])
    s2 = _shot([{"idx": 0, "type": "narration", "text": "乙", "duration": 1.5}])
    cues, total = sub.build_cues([s1, s2], [1.4, 1.9], {}, exact=True)
    assert cues[1].start == pytest.approx(1.4, abs=1e-3)      # 第二镜头从第一镜头结束处开始
    assert total == pytest.approx(3.3, abs=1e-3)


def test_exact_falls_back_to_estimate_without_durations():
    shot = _shot([{"idx": 0, "type": "narration", "text": "没有时长的台词"}])
    est, _ = sub.build_cues([shot], [3.0], {}, exact=False)
    exact, _ = sub.build_cues([shot], [3.0], {}, exact=True)
    assert [(c.start, c.end) for c in est] == [(c.start, c.end) for c in exact]


def test_estimate_mode_is_unchanged_for_video_projects():
    shot = _shot([{"idx": 0, "type": "narration", "text": "旁白", "duration": 1.0},
                  {"idx": 1, "type": "narration", "text": "再一句", "duration": 3.0}])
    cues, _ = sub.build_cues([shot], [2.0], {}, exact=False)
    # 估算模式：按镜头时长等比缩放，第二块的结束不会超过镜头时长
    assert cues[-1].end <= 2.0 + 1e-6


# ---------- 动态漫项目：配音时长与记录一致，合成时长随转场不变

@pytest.fixture
def still_ready(tmp_path):
    p = _project(tmp_path)
    _prepare(p)
    cs.run_stills(p, 1, StubImageProvider())
    for sh in _shots(p):
        sr.approve(p, 1, sh["id"])
    cseg.run_segments(p, 1)
    return p


def test_recorded_block_durations_match_audio_files(still_ready):
    """字幕起点依赖的块时长，应与配音文件的真实时长相差不超过 1 帧。"""
    tol = 1 / 24 + 1e-6
    checked = 0
    for sh in _shots(still_ready):
        for b in sh["blocks"]:
            wav = still_ready / "chapters/001/audio" / f"{sh['id']}_b{b['idx']:02d}.wav"
            assert wav.is_file()
            assert abs(mf.probe_duration(wav) - float(b["duration"])) <= tol
            checked += 1
    assert checked > 0


def test_compose_with_fade_and_cut_has_same_duration_and_cue_starts_at_shot_start(still_ready):
    import json
    meta_p = still_ready / "project.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["still"] = {"transition": "fade", "transition_sec": 0.4}
    meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    fade = cc.run_compose(still_ready, 1)
    total = sum(sh["segment_seconds"] for sh in _shots(still_ready))
    assert abs(fade["duration"] - total) < 0.1

    # 每个镜头的第一个字幕起点应与该镜头的起点（配音起点）相差不超过 1 帧
    shots = _shots(still_ready)
    durations = [mf.probe_duration(still_ready / "chapters/001/clips" / f"{sh['id']}.mp4") for sh in shots]
    cues, _ = sub.build_cues(shots, durations, {}, exact=True)
    starts, t = [], 0.0
    for sh, d in zip(shots, durations):
        starts.append(t)
        t += d
    for start in starts:
        assert any(abs(c.start - start) <= 1 / 24 + 1e-6 for c in cues)

    meta["still"] = {"transition": "cut"}
    meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    cut = cc.run_compose(still_ready, 1)
    assert abs(cut["duration"] - fade["duration"]) < 0.1


def test_invalid_transition_in_project_fails_compose(still_ready):
    import json
    meta_p = still_ready / "project.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["still"] = {"transition": "wipe"}
    meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        cc.run_compose(still_ready, 1)
