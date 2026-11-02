"""字幕测试：时间轴（与配音对齐）、对白说话人名、PNG 渲染、ffmpeg 烧录后画面像素、设置与合成接入。

ffmpeg 使用项目自带的查找（与其它测试一致）；真实烧录测试会读取输出帧的像素来验证字幕位置。
"""

import json
from pathlib import Path

import pytest
from PIL import Image

from novel_to_video import tool_api
from novel_to_video.media import ffmpeg as mf
from novel_to_video.media import subtitles as sub
from novel_to_video.steps import chapter_compose as cc


def _block(text, kind="narration", speaker_id=None, speaker_name=None, duration=None):
    b = {"type": kind, "text": text, "speaker_id": speaker_id, "speaker_name": speaker_name}
    if duration is not None:
        b["duration"] = duration
    return b


# ----------------------------------------------------------------- 文案与时间轴

def test_dialogue_gets_speaker_name_and_narration_stays_plain():
    names = {"char_001": "林远"}
    assert sub.cue_text(_block("我们走。", "dialogue", "char_001"), names) == "林远：我们走。"
    assert sub.cue_text(_block("新人的台词。", "dialogue", None, "新人"), names) == "新人：新人的台词。"
    assert sub.cue_text(_block("铁城的清晨很安静。"), names) == "铁城的清晨很安静。"
    assert sub.cue_text(_block("   "), names) == ""


def test_cues_follow_block_durations_with_audio_gap():
    shots = [{"blocks": [_block("旁白一", duration=2.0), _block("对白", "dialogue", "char_001", duration=1.0)]}]
    cues, total = sub.build_cues(shots, [3.2], {"char_001": "林远"}, gap=0.2)
    assert [c.text for c in cues] == ["旁白一", "林远：对白"]
    assert cues[0].start == 0.0 and cues[0].end == 2.0
    assert cues[1].start == pytest.approx(2.2) and cues[1].end == pytest.approx(3.2)
    assert total == pytest.approx(3.2)


def test_timeline_is_continuous_across_shots_and_scales_to_clip_length():
    # 第二个镜头的视频比配音长（慢放）：块时长按比例拉长，但不超过镜头结束时间
    shots = [{"blocks": [_block("A", duration=1.0)]}, {"blocks": [_block("B", duration=2.0)]}]
    cues, total = sub.build_cues(shots, [1.0, 4.0], {})
    assert cues[1].start == pytest.approx(1.0)
    assert cues[1].end == pytest.approx(5.0)
    assert total == pytest.approx(5.0)


def test_missing_duration_is_estimated_from_text_length():
    shots = [{"blocks": [_block("这是一段没有配音时长的旁白，用于估算")]}]
    cues, total = sub.build_cues(shots, [6.0], {})
    assert len(cues) == 1 and cues[0].end - cues[0].start == pytest.approx(6.0, abs=0.01)


def test_timeline_segments_cover_whole_video_with_blanks():
    cues = [sub.Cue(1.0, 2.0, "甲"), sub.Cue(2.5, 3.0, "乙")]
    segs = sub.timeline_segments(cues, 4.0)
    assert segs == [(None, 1.0), ("甲", 1.0), (None, 0.5), ("乙", 0.5), (None, 1.0)]
    assert sum(d for _, d in segs) == pytest.approx(4.0)


def test_mismatched_shot_and_clip_counts_are_rejected():
    with pytest.raises(sub.SubtitleError):
        sub.build_cues([{"blocks": []}], [1.0, 2.0], {})


# ----------------------------------------------------------------- PNG 渲染

def _font(size):
    from PIL import ImageFont
    return ImageFont.truetype(sub.resolve_subtitle_font(), size)


def test_cue_png_is_transparent_except_bottom_text_band(tmp_path):
    png = sub.render_cue_png("林远：我们走。", 320, 240, _font(20), 20, tmp_path / "c.png")
    img = Image.open(png).convert("RGBA")
    assert img.size == (320, 240)
    top = img.crop((0, 0, 320, 120))
    bottom = img.crop((0, 120, 320, 240))
    assert max(a for *_, a in top.getdata()) == 0          # 画面上半部分完全透明
    assert max(a for *_, a in bottom.getdata()) > 0        # 字幕在底部


def test_blank_png_is_fully_transparent(tmp_path):
    img = Image.open(sub.render_cue_png(None, 64, 36, _font(12), 12, tmp_path / "b.png")).convert("RGBA")
    assert max(a for *_, a in img.getdata()) == 0


def test_default_font_size_scales_with_height():
    assert sub.default_font_size(720) == 30
    assert sub.default_font_size(1280) == 54
    assert sub.default_font_size(100) == 18                 # 有下限


# ----------------------------------------------------------------- ffmpeg 烧录（真实像素）

def _make_clip(path: Path, seconds: float, w: int = 320, h: int = 240) -> Path:
    """纯蓝色、无声的测试片段（真实 ffmpeg）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    mf._run([mf.ffmpeg(), "-y", "-f", "lavfi", "-i", f"color=c=blue:s={w}x{h}:d={seconds}:r=24",
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)])
    return path


def _frame_at(video: Path, t: float, out_png: Path) -> Image.Image:
    mf._run([mf.ffmpeg(), "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(out_png)])
    return Image.open(out_png).convert("RGB")


def _white_pixels(img: Image.Image, box) -> int:
    crop = img.crop(box)
    return sum(1 for px in crop.getdata() if min(px) > 230)


def test_burned_subtitles_appear_only_while_cued(tmp_path):
    raw = _make_clip(tmp_path / "raw.mp4", 3.0)
    cues = [sub.Cue(0.5, 1.5, "林远：我们走。")]
    info = sub.burn(raw, tmp_path / "out.mp4", cues, 3.0, 320, 240, tmp_path / "subs")
    out = tmp_path / "out.mp4"
    assert info["cues"] == 1
    assert mf.probe_duration(out) == pytest.approx(3.0, abs=0.15)      # 时长不变
    band = (0, 150, 320, 240)                                          # 字幕所在底部区域
    during = _frame_at(out, 1.0, tmp_path / "during.png")
    before = _frame_at(out, 0.1, tmp_path / "before.png")
    after = _frame_at(out, 2.5, tmp_path / "after.png")
    assert _white_pixels(during, band) > 50       # 字幕期间底部有白色文字
    assert _white_pixels(before, band) < 5        # 字幕出现之前没有
    assert _white_pixels(after, band) < 5         # 字幕结束之后没有


def test_burn_keeps_the_audio_track(tmp_path):
    raw = _make_clip(tmp_path / "raw.mp4", 2.0)
    sub.burn(raw, tmp_path / "out.mp4", [sub.Cue(0.2, 1.0, "旁白")], 2.0, 320, 240, tmp_path / "subs")
    probe = mf._run([mf.ffprobe(), "-v", "error", "-show_entries", "stream=codec_type",
                     "-of", "csv=p=0", str(tmp_path / "out.mp4")])
    assert "audio" in probe and "video" in probe


# ----------------------------------------------------------------- 设置与合成接入

def test_subtitles_default_on_and_respect_project_setting(tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({}), encoding="utf-8")
    assert cc.subtitle_settings(tmp_path) == {"enabled": True, "font_size": None}
    (tmp_path / "project.json").write_text(json.dumps({"subtitles": {"enabled": False, "font_size": 40}}),
                                           encoding="utf-8")
    assert cc.subtitle_settings(tmp_path) == {"enabled": False, "font_size": 40}


def test_set_subtitles_persists_and_validates(tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({"video": {}}), encoding="utf-8")
    assert tool_api.set_subtitles(str(tmp_path), False)["ok"]
    saved = json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))
    assert saved["subtitles"] == {"enabled": False, "font_size": None}
    assert saved["video"] == {}                                         # 其它字段保留
    assert not tool_api.set_subtitles(str(tmp_path), True, font_size=5)["ok"]    # 字号越界被拒绝


def test_default_project_options_enable_subtitles():
    from novel_to_video import project_store as ps
    assert ps.DEFAULT_OPTIONS["subtitles"]["enabled"] is True
