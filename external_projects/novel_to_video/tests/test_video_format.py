"""视频输出格式：默认横屏 16:9 720P 24fps；竖屏/分辨率/帧率由项目配置决定；缓存键随格式变化。"""
import json

import pytest

from novel_to_video import tool_api
from novel_to_video import video_format as vf
from novel_to_video.media import ffmpeg as mf
from novel_to_video.providers.factory import make_video_provider
from novel_to_video.steps.chapter_clips import build_prompt


def test_default_is_landscape_720p_24fps():
    f = vf.resolve({})
    assert (f.orientation, f.aspect_ratio, f.width, f.height, f.fps, f.resolution) == \
        ("landscape", "16:9", 1280, 720, 24, "720P")


def test_portrait_swaps_dimensions():
    f = vf.resolve({"orientation": "portrait"})
    assert (f.aspect_ratio, f.width, f.height) == ("9:16", 720, 1280)


def test_1080p_landscape_and_portrait():
    assert (vf.resolve({"resolution": "1080P"}).width, vf.resolve({"resolution": "1080P"}).height) == (1920, 1080)
    p = vf.resolve({"orientation": "portrait", "resolution": "1080P"})
    assert (p.width, p.height) == (1080, 1920)


@pytest.mark.parametrize("bad", [{"orientation": "square"}, {"resolution": "4K"}, {"fps": 60}, {"fps": "abc"}])
def test_invalid_values_fall_back_to_defaults(bad):
    f = vf.resolve(bad)
    assert f.orientation == "landscape" and f.resolution == "720P" and f.fps == 24


def test_cache_key_changes_with_format():
    assert vf.resolve({}).key != vf.resolve({"orientation": "portrait"}).key
    assert vf.resolve({}).key != vf.resolve({"fps": 30}).key


def test_apply_format_sets_media_size():
    try:
        mf.apply_format(vf.resolve({"orientation": "portrait"}))
        assert (mf.W, mf.H) == (720, 1280)
        assert "scale=720:1280" in mf._scale_filter()
    finally:
        mf.apply_format(vf.resolve({}))


def test_clip_prompt_uses_the_frame_of_the_format():
    shot = {"visual_hint_en": "a man walks", "character_ids": [], "location_id": None}
    assert "horizontal 16:9 frame" in build_prompt("style", shot, {}, {}, vf.resolve({}).frame_prompt)["prompt"]
    assert "vertical 9:16 frame" in build_prompt("style", shot, {}, {}, vf.resolve({"orientation": "portrait"}).frame_prompt)["prompt"]


def test_set_video_format_saves_and_validates(tmp_path):
    (tmp_path / "project.json").write_text(json.dumps({"video": {"max_wait_sec": 3600}}), encoding="utf-8")
    r = tool_api.set_video_format(str(tmp_path), "portrait", "1080P", 30, 10)
    assert r["ok"] and r["data"]["orientation"] == "portrait"
    saved = json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))["video"]
    assert saved["orientation"] == "portrait" and saved["fps"] == 30 and saved["max_wait_sec"] == 3600
    assert not tool_api.set_video_format(str(tmp_path), "square", "720P", 24, 5)["ok"]
    assert not tool_api.set_video_format(str(tmp_path), "landscape", "4K", 24, 5)["ok"]
    assert not tool_api.set_video_format(str(tmp_path), "landscape", "720P", 60, 5)["ok"]
    assert not tool_api.set_video_format(str(tmp_path), "landscape", "720P", 24, 0)["ok"]


def test_video_provider_accepts_size_and_aspect():
    import inspect
    from novel_to_video.providers.video import StubVideoProvider, AgnesVideoProvider
    from novel_to_video.providers.skill_providers import SkillVideoProvider
    for cls in (StubVideoProvider, AgnesVideoProvider, SkillVideoProvider):
        params = inspect.signature(cls.generate).parameters
        assert "size" in params and "aspect_ratio" in params, cls.__name__
        assert params["aspect_ratio"].default == "16:9"
