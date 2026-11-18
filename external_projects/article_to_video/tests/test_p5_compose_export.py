"""P5 集成：章合成（真实 ffmpeg 编码）、停止与过期、成片导出的三种转场、SRT 偏移、元数据与失败提示。"""
import json
import threading

import pytest

from article_to_video import cancel
from article_to_video.compose.config import save_compose_config
from article_to_video.compose.export import export_final
from article_to_video.compose.ffrun import ComposeError
from article_to_video.compose.part import compose_part
from article_to_video.project_store import read_json, write_json_atomic
from article_to_video.providers.image import StubImageProvider
from article_to_video.render.compose_part import render_part
from article_to_video.visual import storyboard as sbm
from helpers_p4 import voiced_project
from helpers_p5 import fake_part
from article_to_video.compose.ffrun import probe


def _use_720p(pdir):
    from article_to_video.app_settings import set_field
    set_field(pdir, "settings", "video.resolution", "720P")


@pytest.fixture(scope="module")
def composed(tmp_path_factory, monkeypatch_module):
    root = tmp_path_factory.mktemp("p5")
    monkeypatch_module.setenv("ARTICLE_TO_VIDEO_HOME", str(root))
    pdir, _ = voiced_project(root)
    _use_720p(pdir)
    sbm.plan_storyboard(pdir, "P01")
    sbm.confirm_storyboard(pdir, "P01")
    render_part(pdir, "P01", provider=StubImageProvider())
    info = compose_part(pdir, "P01")
    return pdir, info


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


def test_chapter_video_matches_timeline_resolution_and_audio(composed):
    pdir, info = composed
    part = pdir / "parts" / "P01" / "video" / "part.mp4"
    pr = probe(part)
    v = next(s for s in pr["streams"] if s["codec_type"] == "video")
    assert (int(v["width"]), int(v["height"])) == (1280, 720)
    assert any(s["codec_type"] == "audio" for s in pr["streams"])
    tl = read_json(pdir / "parts" / "P01" / "timeline.json")
    assert float(pr["format"]["duration"]) == pytest.approx(tl["total_sec"], abs=0.25)
    assert info["duration"] == pytest.approx(tl["total_sec"], abs=1e-3)
    assert info["segments"] > 10 and info["subtitles"]


def test_chapter_info_lists_only_visible_subtitles_within_duration(composed):
    pdir, info = composed
    assert all(0 <= s["start"] < s["end"] <= info["duration"] + 1e-6 for s in info["subtitles"])


def test_compose_refuses_stale_render(composed):
    pdir, _ = composed
    sb = sbm.load_storyboard(pdir, "P01")
    sb["input_sha"] = "changed"
    write_json_atomic(pdir / "parts" / "P01" / "storyboard.json", sb)
    with pytest.raises(ComposeError, match="不一致"):
        compose_part(pdir, "P01")
    sb["input_sha"] = read_json(pdir / "parts" / "P01" / "visuals" / "manifest.json")["storyboard_input_sha"]
    write_json_atomic(pdir / "parts" / "P01" / "storyboard.json", sb)


def test_stop_request_during_compose_raises_before_encoding(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = voiced_project(tmp_path)
    _use_720p(pdir)
    sbm.plan_storyboard(pdir, "P03")
    sbm.confirm_storyboard(pdir, "P03")
    render_part(pdir, "P03", provider=None)
    ev = threading.Event()
    ev.set()
    cancel.bind(ev)
    try:
        with pytest.raises(cancel.Cancelled):
            compose_part(pdir, "P03")
    finally:
        cancel.bind(threading.Event())
    assert not (pdir / "parts" / "P03" / "video" / "part.mp4").exists()


def test_compose_without_render_explains_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = voiced_project(tmp_path)
    with pytest.raises(ComposeError, match="渲染"):
        compose_part(pdir, "P02")


# ---------- 成片导出（假章视频，快速） ----------

@pytest.fixture
def fakes(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = voiced_project(tmp_path)
    _use_720p(pdir)  # 假章视频为 720P，成片设定须一致（否则 V6 拦截）
    # 本组测试只针对章节之间的转场与字幕偏移：关闭 P6 默认的片头/片尾与水印（署名另有测试）
    from article_to_video.project_store import update_branding
    update_branding(pdir, {"credits": {"title_card": False, "end_card": False, "lower_third": False,
                                       "corner_tag": False}, "watermark": {"enabled": False}})
    fake_part(pdir, "P01", 2.0, [{"id": "P01.s1", "start": 0.2, "end": 1.0, "text": "甲一"}])
    fake_part(pdir, "P02", 3.0, [{"id": "P02.s1", "start": 0.5, "end": 2.0, "text": "乙一"}])
    return pdir


@pytest.mark.parametrize("mode", ["cut", "fade", "dissolve"])
def test_export_transitions_produce_expected_duration(fakes, mode):
    save_compose_config(fakes, {"transition": mode, "transition_sec": 0.5})
    rep = export_final(fakes, part_ids=["P01", "P02"])
    expected = 5.0 - (0.5 if mode == "dissolve" else 0.0)
    assert rep["duration"] == pytest.approx(expected, abs=1e-3)
    video = next(p for p in (fakes / "exports").glob("*.mp4"))
    assert probe_duration_of(video) == pytest.approx(expected, abs=0.25)
    assert rep["checks"]["errors"] == []


def probe_duration_of(path):
    return float(probe(path)["format"]["duration"])


def test_srt_is_shifted_by_chapter_start_and_dissolve_overlap(fakes):
    save_compose_config(fakes, {"transition": "dissolve", "transition_sec": 0.5})
    export_final(fakes, part_ids=["P01", "P02"])
    srt = next((fakes / "exports").glob("*.srt")).read_text(encoding="utf-8")
    # P02 起点 = 2.0 - 0.5 = 1.5 秒；其字幕 0.5–2.0 秒 → 2.0–3.5 秒
    assert "甲一" in srt and "乙一" in srt
    assert "00:00:02,000 --> 00:00:03,500" in srt


def test_export_writes_metadata_cover_and_report(fakes):
    save_compose_config(fakes, {"transition": "cut"})
    rep = export_final(fakes, part_ids=["P01", "P02"])
    out = next((fakes / "exports").glob("*.mp4"))
    tags = probe(out)["format"].get("tags", {})
    assert tags.get("title") == rep["title"]
    assert (fakes / "exports" / rep["files"]["cover"]).is_file()
    report = json.loads((fakes / "exports" / next((fakes / "exports").glob("*_report.json")).name).read_text("utf-8"))
    assert report["parts"] == ["P01", "P02"] and report["transition"] == "cut"
    assert not (fakes / "exports" / ".build").exists()


def test_export_lists_missing_chapters(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir, _ = voiced_project(tmp_path)
    fake_part(pdir, "P01", 2.0)
    with pytest.raises(ComposeError, match="P02"):
        export_final(pdir)
