"""P4 章级渲染：清单完整性、时间与时间轴一致、缓存复用、插图降级、停止、竖屏、前置条件。"""
import json
import threading

import pytest
from PIL import Image

from article_to_video import cancel
from article_to_video.planning.errors import PlanError
from article_to_video.project_store import read_json, write_json_atomic
from article_to_video.providers.image import StubImageProvider
from article_to_video.render.compose_part import render_part
from article_to_video.visual import storyboard as sbm
from helpers_p4 import FailingImageProvider, voiced_project


@pytest.fixture
def vpdir(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = voiced_project(tmp_path)
    for pid in ("P02",):
        sbm.plan_storyboard(pdir, pid)
        sbm.confirm_storyboard(pdir, pid)
    return pdir


def _manifest(pdir, pid="P02"):
    return read_json(pdir / "parts" / pid / "visuals" / "manifest.json")


def test_manifest_covers_every_scene_with_real_files_and_canvas(vpdir):
    m = render_part(vpdir, "P02", provider=StubImageProvider())
    sb = sbm.load_storyboard(vpdir, "P02")
    assert [s["id"] for s in m["scenes"]] == [s["id"] for s in sb["scenes"]]
    base = vpdir / "parts" / "P02"
    assert (base / m["chapter"]["file"]).is_file()
    for s in m["scenes"]:
        assert (base / s["background"]).is_file()
        for f in s["frames"]:
            img = Image.open(base / f["file"])
            assert img.mode == "RGBA" and list(img.size) == m["canvas"]


def test_frame_times_follow_timeline_and_stay_inside_scene(vpdir):
    m = render_part(vpdir, "P02", provider=StubImageProvider())
    total = m["total_sec"]
    for s in m["scenes"]:
        times = [f["start"] for f in s["frames"]]
        assert times == sorted(times)
        assert all(s["start"] - 1e-6 <= t <= s["end"] + 1e-6 for t in times) or not times
        assert 0 <= s["start"] < s["end"] <= total + 1e-6
    assert m["chapter"]["end"] == pytest.approx(read_json(vpdir / "parts/P02/timeline.json")["lead_in_sec"], abs=1e-3)


def test_second_render_reuses_everything_and_calls_no_provider(vpdir):
    provider = StubImageProvider()
    first = render_part(vpdir, "P02", provider=provider)
    calls = len(provider.calls)
    assert calls == first["counts"]["illustration"] > 0
    second = render_part(vpdir, "P02", provider=provider)
    assert second["counts"]["reused"] == len(second["scenes"])
    assert len(provider.calls) == calls


def test_illustrations_after_the_first_use_the_first_as_style_reference(vpdir):
    provider = StubImageProvider()
    render_part(vpdir, "P02", provider=provider)
    assert provider.calls[0]["refs"] == []
    assert all(c["refs"] for c in provider.calls[1:])


def test_missing_provider_degrades_illustrations_to_cards_with_reason(vpdir):
    m = render_part(vpdir, "P02", provider=None)
    fallback = [s for s in m["scenes"] if s["fallback_reason"]]
    assert fallback and all(s["rendered_as"] == "keypoint" for s in fallback)
    assert m["counts"]["fallback"] == len(fallback)
    assert any("已按卡片绘制" in w for w in m["warnings"])


def test_failed_generation_also_degrades_and_does_not_stop_the_part(vpdir):
    provider = FailingImageProvider()
    m = render_part(vpdir, "P02", provider=provider)
    assert provider.calls == m["counts"]["fallback"] > 0
    assert all(s["frames"] for s in m["scenes"] if s["rendered_as"] == "keypoint")


def test_index_keeps_prompt_and_sha_for_each_generated_image(vpdir):
    render_part(vpdir, "P02", provider=StubImageProvider())
    index = read_json(vpdir / "parts" / "P02" / "images" / "index.json")
    assert index and all(v["sha"] and v["version"] == 1 and v["prompt"] for v in index.values())
    assert all("无任何文字" in v["prompt"] or "不出现任何文字" in v["prompt"] for v in index.values())


def test_changing_style_anchor_regenerates_image_as_new_version(vpdir):
    from article_to_video.visual.config import save_visual_config
    provider = StubImageProvider()
    render_part(vpdir, "P02", provider=provider)
    save_visual_config(vpdir, {"bible": {"style_anchor": "水墨风格，留白，不出现任何文字"}})
    # 设置变化使分镜过期：需要重新生成并确认，才能渲染
    sbm.plan_storyboard(vpdir, "P02", force=True)
    sbm.confirm_storyboard(vpdir, "P02")
    render_part(vpdir, "P02", provider=provider)
    index = read_json(vpdir / "parts" / "P02" / "images" / "index.json")
    assert any(v["version"] == 2 for v in index.values())
    assert any(p.name.endswith("_v002.png") for p in (vpdir / "parts" / "P02" / "images").iterdir())


def test_stop_request_interrupts_render_without_writing_manifest(vpdir):
    ev = threading.Event()
    ev.set()
    cancel.bind(ev)
    try:
        with pytest.raises(cancel.Cancelled):
            render_part(vpdir, "P02", provider=StubImageProvider())
    finally:
        cancel.bind(threading.Event())
    assert not (vpdir / "parts" / "P02" / "visuals" / "manifest.json").exists()


def test_portrait_project_renders_portrait_canvas(vpdir):
    project = read_json(vpdir / "project.json")
    project.setdefault("settings", {}).setdefault("video", {})["orientation"] = "portrait"
    write_json_atomic(vpdir / "project.json", project)
    sbm.plan_storyboard(vpdir, "P02", force=True)
    sbm.confirm_storyboard(vpdir, "P02")
    m = render_part(vpdir, "P02", provider=None)
    assert m["canvas"] == [1080, 1920] and m["orientation"] == "portrait"


def test_render_requires_confirmed_storyboard_and_timeline(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = voiced_project(tmp_path)
    with pytest.raises(PlanError, match="分镜"):
        render_part(pdir, "P03", provider=None)
    sbm.plan_storyboard(pdir, "P03")
    with pytest.raises(PlanError, match="C2"):
        render_part(pdir, "P03", provider=None)
    sbm.confirm_storyboard(pdir, "P03")
    (pdir / "parts" / "P03" / "timeline.json").unlink()
    with pytest.raises(PlanError, match="时间轴"):
        render_part(pdir, "P03", provider=None)
