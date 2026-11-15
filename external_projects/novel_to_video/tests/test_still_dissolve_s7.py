"""动态漫模式 S7：叠化（dissolve）。画面重叠、音频裁掉前一镜头的静音余量，配音起点与画面起点一致。"""
import array
import math
import subprocess
import wave
from pathlib import Path

import pytest

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
from test_still_transition_s6 import W, H, FPS, _clip, _luma, _solid

RATE = mf.AUDIO_RATE


def _tone(path: Path, seconds: float, freq: float = 440.0, amp: float = 0.6) -> Path:
    n = int(seconds * RATE)
    frames = array.array("h", (int(amp * 32767 * math.sin(2 * math.pi * freq * i / RATE)) for i in range(n)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames.tobytes())
    return path


def _segment_with_tone(tmp_path: Path, name: str, tone_sec: float, total_sec: float) -> Path:
    """0–tone_sec 为音调，之后为静音，总长 total_sec（与镜头的配音+余量结构相同）。"""
    still = _solid(tmp_path / f"{name}.png", 120)
    wav = _tone(tmp_path / f"{name}.wav", tone_sec)
    out = tmp_path / f"{name}.mp4"
    MO.render_segment(still, wav, out, seconds=total_sec, motion="none", strength="slow", w=W, h=H, fps=FPS)
    return out


def _audio_samples(video: Path) -> array.array:
    raw = subprocess.run([mf.ffmpeg(), "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(RATE),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    a = array.array("h")
    a.frombytes(raw)
    return a


def _first_loud_after(samples: array.array, t: float, threshold: int = 2000) -> float:
    start = int(t * RATE)
    for i in range(start, len(samples)):
        if abs(samples[i]) > threshold:
            return i / RATE
    raise AssertionError("没有找到音调")


# ---------- 设置

def test_dissolve_settings_validation():
    RM.validate_transition({"transition": "dissolve", "transition_sec": 0.4})
    RM.validate_transition({"transition": "dissolve", "transition_sec": 0.2})
    with pytest.raises(ValueError):
        RM.validate_transition({"transition": "dissolve", "transition_sec": 0.5})   # 超过静音余量
    with pytest.raises(ValueError):
        RM.validate_transition({"transition": "dissolve", "transition_sec": 0})
    RM.validate_transition({"transition": "fade", "transition_sec": 0.8})           # 淡化的上限仍是 1 秒


# ---------- 时间轴（数值）

def test_dissolve_timeline_gives_each_shot_its_start():
    assert mf.dissolve_timeline([1.5, 2.0, 1.2], 0.4) == pytest.approx([1.1, 1.6, 1.2])
    # 总时长 = 各段之和 - (n-1) * 叠化时长
    assert sum(mf.dissolve_timeline([1.5, 2.0, 1.2], 0.4)) == pytest.approx(4.7 - 0.8)


def test_effective_dissolve_never_exceeds_half_the_shortest_shot():
    assert mf.effective_dissolve([0.3, 2.0], 0.4) == pytest.approx(0.15)
    assert mf.effective_dissolve([3.0, 3.0], 0.4) == pytest.approx(0.4)


# ---------- 真实拼接

def test_dissolve_output_duration_and_audio_length_match_timeline(tmp_path):
    clips = [_clip(tmp_path, "a", 230, 1.5), _clip(tmp_path, "b", 230, 2.0), _clip(tmp_path, "c", 230, 1.2)]
    durs = [mf.probe_duration(c) for c in clips]
    out = tmp_path / "dissolved.mp4"
    mf.concat_dissolve(clips, durs, out, 0.4)
    expected = sum(durs) - 2 * 0.4
    assert abs(mf.probe_duration(out) - expected) < 0.1
    assert len(_audio_samples(out)) / RATE == pytest.approx(expected, abs=0.1)


def test_dissolve_blends_pictures_at_the_shot_boundary(tmp_path):
    dark, bright = _clip(tmp_path, "dark", 40, 1.5), _clip(tmp_path, "bright", 230, 1.5)
    durs = [mf.probe_duration(dark), mf.probe_duration(bright)]
    out = tmp_path / "blend.mp4"
    mf.concat_dissolve([dark, bright], durs, out, 0.4)
    s1 = durs[0] - 0.4                       # 第二个镜头开始的时刻
    assert _luma(out, s1 - 0.15) == pytest.approx(40, abs=25)           # 叠化之前：第一镜头
    mid = _luma(out, s1 + 0.2)
    assert 60 < mid < 210                                              # 叠化中途：两者之间
    assert _luma(out, s1 + 0.4 + 0.15) == pytest.approx(230, abs=25)    # 叠化之后：第二镜头


def test_dissolve_keeps_the_second_shots_speech_on_its_timeline_start(tmp_path):
    """第一镜头：0.8 秒音调 + 静音至 1.4 秒；第二镜头同样。叠化 0.4 秒后，第二段音调应从 1.0 秒开始。
    （AAC 编码会带来约 0.02–0.05 秒的固定延迟，容差取 0.05 秒。）"""
    a = _segment_with_tone(tmp_path, "a", 0.8, 1.4)
    b = _segment_with_tone(tmp_path, "b", 0.8, 1.4)
    out = tmp_path / "speech.mp4"
    mf.concat_dissolve([a, b], [mf.probe_duration(a), mf.probe_duration(b)], out, 0.4)
    samples = _audio_samples(out)
    onset = _first_loud_after(samples, 0.9)
    assert onset == pytest.approx(1.0, abs=0.05)
    assert _first_loud_after(samples, 0.0) < 0.05                       # 第一段从 0 秒开始


# ---------- 动态漫项目：整章

@pytest.fixture
def ready(tmp_path):
    p = _project(tmp_path)
    _prepare(p)
    cs.run_stills(p, 1, StubImageProvider())
    for sh in _shots(p):
        sr.approve(p, 1, sh["id"])
    cseg.run_segments(p, 1)
    return p


def test_chapter_with_dissolve_has_timeline_and_cues_consistent(ready):
    import json
    meta_p = ready / "project.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["still"] = {"transition": "dissolve", "transition_sec": 0.4}
    meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    res = cc.run_compose(ready, 1)

    shots = _shots(ready)
    seg_durs = [mf.probe_duration(ready / "chapters/001/clips" / f"{sh['id']}.mp4") for sh in shots]
    t = mf.effective_dissolve(seg_durs, 0.4)
    assert abs(res["duration"] - (sum(seg_durs) - (len(shots) - 1) * t)) < 0.1

    # 每个镜头的第一句台词，在章节时间轴上的起点应等于该镜头的起点（容差 1 帧）
    timeline = mf.dissolve_timeline(seg_durs, 0.4)
    cues, total = sub.build_cues(shots, timeline, {}, exact=True)
    assert total == pytest.approx(sum(timeline), abs=1e-3)
    starts, acc = [], 0.0
    for d in timeline:
        starts.append(acc)
        acc += d
    for start in starts:
        assert any(abs(c.start - start) <= 1 / 24 + 1e-6 for c in cues)
