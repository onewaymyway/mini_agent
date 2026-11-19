"""P5 纯函数：转场时间、文件名、SRT 格式、字幕可见性与高亮、合成设置归一化、音轨放置、V6 判定。"""
import wave

import pytest

from article_to_video.compose.audio_track import build_part_audio
from article_to_video.compose.config import normalize_compose
from article_to_video.compose.export import part_offsets, safe_filename
from article_to_video.compose.subtitles import build_srt, covered_by_card, render_subtitle, subtitle_plan
from article_to_video.media.ffmpeg import AUDIO_RATE
from article_to_video.render.fonts import FontError, find_cjk_font
from article_to_video.validators.compose import check_video
from article_to_video.visual.director import plain

CANVAS = (1280, 720)


def test_part_offsets_for_cut_fade_dissolve():
    starts, total = part_offsets([10, 20, 30], "cut", 0)
    assert starts == [0, 10, 30] and total == 60
    starts, total = part_offsets([10, 20, 30], "fade", 0.5)
    assert starts == [0, 10, 30] and total == 60          # 淡入淡出不重叠
    starts, total = part_offsets([10, 20, 30], "dissolve", 0.5)
    # 每章起点 = 上一章终点 − 叠化时长：9.5 = 10 − 0.5；29.0 = (9.5 + 20) − 0.5
    assert starts == [0, 9.5, 29.0] and total == pytest.approx(59.0)


def test_safe_filename_strips_path_characters():
    assert safe_filename('a/b:c*d "e"') == "a_b_c_d_e"
    assert safe_filename("   ") == "video"


def test_srt_format_and_timing():
    text = build_srt([{"start": 0.0, "end": 1.5, "text": "第一句"},
                      {"start": 3661.25, "end": 3662.0, "text": "第二句"}])
    assert "1\n00:00:00,000 --> 00:00:01,500\n第一句" in text
    assert "2\n01:01:01,250 --> 01:01:02,000\n第二句" in text


def test_card_covering_the_sentence_hides_subtitle_in_auto_mode():
    sentence = "这是要点，讲清关键。"
    assert covered_by_card(sentence, {"type": "keypoint", "items": ["这是要点", "讲清关键"]})
    assert not covered_by_card(sentence, {"type": "keypoint", "items": ["另一个要点"]})
    assert not covered_by_card(sentence, {"type": "illustration", "items": ["这是要点"]})


def _tl_and_script():
    timeline = {"total_sec": 6.0, "sentences": [
        {"id": "P01.b01.s01", "beat": "P01.b01", "text": "这是要点", "start": 0.5, "end": 2.0},
        {"id": "P01.b02.s01", "beat": "P01.b02", "text": "画面外的话", "start": 3.0, "end": 4.5},
    ]}
    script = {"beats": [
        {"id": "P01.b01", "sentences": [{"text": "这是要点", "emphasis": ["要点"]}]},
        {"id": "P01.b02", "sentences": [{"text": "画面外的话", "emphasis": []}]},
    ]}
    storyboard = {"scenes": [
        {"beat": "P01.b01", "type": "keypoint", "items": ["这是要点"], "rows": []},
        {"beat": "P01.b02", "type": "illustration", "items": ["画面外的话"], "rows": []},
    ]}
    return timeline, script, storyboard


def test_subtitle_plan_modes():
    tl, sc, sb = _tl_and_script()
    auto = {s["id"]: s["visible"] for s in subtitle_plan(tl, sc, sb, "auto")}
    assert auto == {"P01.b01.s01": False, "P01.b02.s01": True}
    always = {s["id"]: s["visible"] for s in subtitle_plan(tl, sc, sb, "always")}
    assert all(always.values())
    off = subtitle_plan(tl, sc, sb, "off")
    assert not any(s["visible"] for s in off)
    assert subtitle_plan(tl, sc, sb, "always")[0]["emphasis"] == ["要点"]  # 关闭字幕只影响可见性


def test_subtitle_image_has_box_and_emphasis_colour():
    try:
        find_cjk_font({})
    except FontError:
        pytest.skip("本机没有中文字体")
    img = render_subtitle("这是要点，很重要", ["要点"], (1280, 720), "landscape", "ink")
    assert img.size == (1280, 720) and img.mode == "RGBA"
    colours = {img.getpixel((x, y))[:3] for x in range(0, img.width, 3) for y in range(0, img.height, 3)
               if img.getpixel((x, y))[3] > 200}
    assert any(c[0] > 180 and c[2] < 120 for c in colours)  # 强调色（金色）出现
    assert img.getchannel("A").getbbox() is not None


def test_compose_settings_are_normalized():
    cfg, warn = normalize_compose({"transition": "wipe", "subtitles": "maybe", "transition_sec": 99})
    assert cfg["transition"] == "fade" and cfg["subtitles"] == "always"
    assert cfg["transition_sec"] == 2.0 and cfg["fps"] == 25
    assert len(warn) == 2


def test_audio_track_places_each_sentence_at_its_timeline_start(tmp_path):
    part_dir = tmp_path / "P01"
    (part_dir / "audio").mkdir(parents=True)
    tl = {"total_sec": 4.0, "sentences": [
        {"id": "a", "start": 1.0}, {"id": "b", "start": 2.5}]}
    for sid, amp in (("a", 1000), ("b", 2000)):
        with wave.open(str(part_dir / "audio" / f"{sid}.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(AUDIO_RATE)
            w.writeframes(amp.to_bytes(2, "little", signed=True) * (AUDIO_RATE // 2))  # 0.5 秒
    out = tmp_path / "audio.wav"
    dur = build_part_audio(part_dir, tl, out)
    assert dur == pytest.approx(4.0)
    with wave.open(str(out)) as w:
        data = w.readframes(w.getnframes())
    def sample(t):
        i = int(t * AUDIO_RATE) * 2
        return int.from_bytes(data[i:i + 2], "little", signed=True)
    assert sample(0.5) == 0 and sample(1.2) == 1000 and sample(1.9) == 0
    assert sample(2.6) == 2000 and sample(3.5) == 0


def test_audio_track_rejects_wrong_format(tmp_path):
    from article_to_video.compose.ffrun import ComposeError
    part_dir = tmp_path / "P01"
    (part_dir / "audio").mkdir(parents=True)
    with wave.open(str(part_dir / "audio" / "a.wav"), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100); w.writeframes(b"\0" * 400)
    with pytest.raises(ComposeError, match="格式"):
        build_part_audio(part_dir, {"total_sec": 1.0, "sentences": [{"id": "a", "start": 0.0}]}, tmp_path / "x.wav")


def test_v6_flags_wrong_resolution_duration_and_missing_audio():
    good = {"streams": [{"codec_type": "video", "width": 1280, "height": 720, "r_frame_rate": "25/1"},
                        {"codec_type": "audio"}], "format": {"duration": "10.00"}}
    assert check_video(good, canvas=(1280, 720), fps=25, expected_sec=10.0)["errors"] == []
    bad_res = {"streams": good["streams"], "format": {"duration": "10.0"}}
    bad_res["streams"][0]["width"] = 640
    assert any("分辨率" in e for e in check_video(bad_res, canvas=(1280, 720), fps=25, expected_sec=10.0)["errors"])
    assert any("时长" in e for e in check_video(good, canvas=(1280, 720), fps=25, expected_sec=12.0)["errors"])
    no_audio = {"streams": [good["streams"][0]], "format": {"duration": "10.0"}}
    assert any("音轨" in e for e in check_video(no_audio, canvas=(1280, 720), fps=25, expected_sec=10.0)["errors"])
    assert any("字幕" in e for e in check_video(good, canvas=(1280, 720), fps=25, expected_sec=10.0,
                                                subtitle_end_sec=11.0)["errors"])


def test_plain_strips_markdown_markers():
    assert plain("**智能与自主生产**") == "智能与自主生产"
    assert plain("`x` y") == "x y"
