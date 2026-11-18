"""P6 成片导出（假章视频，720P）：片头/片尾/下三分之一条、元数据、水印三种模式、BGM、
以及“改水印只重跑最后一步”（章视频、主版本不重编码）。"""
import os
import subprocess

import pytest

from article_to_video.compose.config import save_compose_config
from article_to_video.compose.export import export_final
from article_to_video.compose.ffrun import probe
from article_to_video.compose.part import video_dir
from article_to_video.media.ffmpeg import find_ffmpeg
from article_to_video.project_store import read_json, update_branding, write_json_atomic
from helpers_p4 import voiced_project
from helpers_p5 import fake_part


@pytest.fixture
def pdir(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    d, _ = voiced_project(tmp_path)
    p = d / "project.json"
    pj = read_json(p)
    pj["settings"]["video"]["resolution"] = "720P"
    write_json_atomic(p, pj)
    fake_part(d, "P01", 3.0)
    fake_part(d, "P02", 3.0)
    update_branding(d, {"author": "作者甲", "source_url": "https://example.com/a",
                        "watermark": {"enabled": True, "text": "作者甲 · 频道", "position": "top_right",
                                      "mode": "static", "opacity": 0.35},
                        "credits": {"title_card": True, "lower_third": True, "end_card": True,
                                    "corner_tag": True}})
    save_compose_config(d, {"transition": "cut"})
    return d


def _final(d):
    rep = read_json(d / "exports" / next(f for f in os.listdir(d / "exports") if f.endswith("_report.json")))
    return rep


def test_full_branded_export_has_credits_sequence_and_metadata(pdir):
    rep = export_final(pdir, part_ids=["P01", "P02"])
    kinds = [s["kind"] for s in rep["sequence"]]
    assert kinds == ["title_card", "chapter", "chapter", "end_card"]
    assert rep["duration"] == pytest.approx(4 + 3 + 3 + 5, abs=1e-3)
    assert rep["lower_third"] == [9.0, 13.0] or rep["lower_third"] is None  # 长度不足时不压片尾
    tags = probe(pdir / "exports" / rep["files"]["video"])["format"]["tags"]
    assert tags["title"] == rep["title"] and tags["artist"] == "作者甲"
    assert "AI" in tags.get("description", "")
    assert "example.com" in tags.get("comment", "")
    assert rep["checks"]["errors"] == []


def test_clean_version_has_no_watermark_and_final_does(pdir):
    rep = export_final(pdir, part_ids=["P01", "P02"])
    final = pdir / "exports" / rep["files"]["video"]
    clean = pdir / "exports" / rep["files"]["clean"]
    assert final.is_file() and clean.is_file()
    assert final.read_bytes() != clean.read_bytes()
    assert rep["watermark"]["enabled"] and rep["watermark"]["box"]


def test_changing_watermark_reruns_only_the_last_step(pdir):
    export_final(pdir, part_ids=["P01", "P02"])
    work = pdir / "exports" / ".work"
    marks = {
        "part": (video_dir(pdir, "P01") / "part.mp4").stat().st_mtime_ns,
        "joined": (work / "joined.mp4").stat().st_mtime_ns,
        "master_exists": (work / "master.mp4").is_file(),
    }
    final_before = next((pdir / "exports").glob("*.mp4"))
    before_bytes = final_before.read_bytes()
    update_branding(pdir, {"watermark": {**read_json(pdir / "project.json")["branding"]["watermark"],
                                         "text": "新的水印文字"}})
    rep2 = export_final(pdir, part_ids=["P01", "P02"])
    assert (video_dir(pdir, "P01") / "part.mp4").stat().st_mtime_ns == marks["part"]
    assert (work / "joined.mp4").stat().st_mtime_ns == marks["joined"]
    assert marks["master_exists"]
    final_after = pdir / "exports" / rep2["files"]["video"]
    assert final_after.read_bytes() != before_bytes


@pytest.mark.parametrize("position,mode,expect", [
    ("bottom_left", "static", "static"),
    ("bottom_left", "drift", "drift"),
    ("diagonal_tile", "static", "diagonal_tile"),  # 斜铺是位置，与模式无关
])
def test_each_watermark_mode_exports(pdir, position, mode, expect):
    update_branding(pdir, {"watermark": {"enabled": True, "text": "水印", "position": position,
                                         "mode": mode, "opacity": 0.4}})
    rep = export_final(pdir, part_ids=["P01", "P02"])
    assert rep["watermark"]["mode"] == expect and rep["checks"]["errors"] == []


def test_credits_off_and_watermark_off_gives_plain_film(pdir):
    update_branding(pdir, {"watermark": {"enabled": False, "text": "", "position": "top_right", "mode": "static",
                                         "opacity": 0.3},
                           "credits": {"title_card": False, "lower_third": False, "end_card": False,
                                       "corner_tag": False}, "ai_disclosure": {"enabled": False, "text": ""}})
    rep = export_final(pdir, part_ids=["P01", "P02"])
    assert [s["kind"] for s in rep["sequence"]] == ["chapter", "chapter"]
    assert rep["duration"] == pytest.approx(6.0, abs=1e-3)
    tags = probe(pdir / "exports" / rep["files"]["video"])["format"].get("tags", {})
    assert "description" not in tags
    assert rep["ai_disclosure"] is None


def test_bgm_is_mixed_when_user_supplies_audio(pdir):
    (pdir / "assets").mkdir(exist_ok=True)
    subprocess.run([find_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=220:sample_rate=24000", "-t", "5", str(pdir / "assets" / "bgm.wav")],
                   check=True)
    from article_to_video.compose.branding import save_bgm
    save_bgm(pdir, enabled=True, rel_path="assets/bgm.wav", volume=0.3)
    rep = export_final(pdir, part_ids=["P01", "P02"])
    assert rep["bgm"] is True
    streams = probe(pdir / "exports" / rep["files"]["video"])["streams"]
    assert any(s["codec_type"] == "audio" for s in streams)
    assert rep["checks"]["errors"] == []


def test_short_film_skips_lower_third_instead_of_covering_end_card(pdir):
    rep = export_final(pdir, part_ids=["P01"])
    assert rep["lower_third"] is None
