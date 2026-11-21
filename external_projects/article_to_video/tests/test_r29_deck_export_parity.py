"""R29：新方案导出与现有方案对齐——封面合成进成片、水印等署名一致、文件名即文章标题。"""
import pytest
from PIL import Image, ImageChops, ImageStat

from article_to_video.app_settings import load_effective_project, set_field
from article_to_video.compose.export import safe_filename
from article_to_video.compose.branding import TITLE_SEC
from article_to_video.compose.config import save_compose_config
from article_to_video.compose.ffrun import ffmpeg, probe
from article_to_video.deck.compose import compose_deck_part, export_deck_video
from article_to_video.deck.voice import load_deck_timeline
from article_to_video.project_store import read_json, update_branding
from test_deck_phase4 import small_project  # noqa: F401  (fixture)


@pytest.fixture
def exported(small_project):
    pdir = small_project
    set_field(pdir, "settings", "video.resolution", "720P")
    update_branding(pdir, {"author": "作者甲", "watermark": {"enabled": True, "text": "作者甲 · 频道",
                           "position": "top_right", "mode": "static", "opacity": 0.5},
                           "credits": {"corner_tag": True, "lower_third": False, "end_card": False,
                                       "title_card": False}})
    save_compose_config(pdir, {"transition": "cut"})
    compose_deck_part(pdir, "P01")
    out = export_deck_video(pdir)
    return pdir, out


def _name(pdir):
    return safe_filename(load_effective_project(pdir)["title"])      # 与现有方案同一来源：项目标题


def _frame(video, t, dest):
    ffmpeg(["-ss", f"{t}", "-i", str(video), "-frames:v", "1", str(dest)])
    return Image.open(dest).convert("RGB")


def test_final_video_is_named_after_the_article_title(exported):
    pdir, out = exported
    n = _name(pdir)
    assert out.name == f"{n}.mp4" and out.parent == pdir / "deck" / "exports"
    names = {p.name for p in out.parent.iterdir()}
    assert {f"{n}_clean.mp4", f"{n}_cover.png", f"{n}_report.json", f"{n}.srt"} <= names
    assert not any("新方案" in n for n in names)
    assert not (pdir / "exports").exists()                      # 不写入现有方案的目录


def test_cover_is_merged_into_the_start_of_the_video(exported, tmp_path):
    pdir, out = exported
    rep = read_json(out.parent / f"{_name(pdir)}_report.json")
    assert rep["sequence"][0]["kind"] == "cover" and rep["sequence"][1]["kind"] == "chapter"
    part = read_json(pdir / "deck" / "parts" / "P01" / "video" / "info.json")["duration"]
    assert float(probe(out)["format"]["duration"]) == pytest.approx(part + TITLE_SEC, abs=0.3)
    first = _frame(out.parent / f"{_name(pdir)}_clean.mp4", 0.5, tmp_path / "f0.png")
    cover = Image.open(out.parent / f"{_name(pdir)}_cover.png").convert("RGB").resize(first.size)
    later = _frame(out.parent / f"{_name(pdir)}_clean.mp4", TITLE_SEC + 1.0, tmp_path / "f1.png")
    d_cover = sum(ImageStat.Stat(ImageChops.difference(first, cover)).mean)
    d_later = sum(ImageStat.Stat(ImageChops.difference(later, cover)).mean)
    assert d_cover < 25 and d_cover < d_later                    # 开头就是封面画面，之后是正文


def test_watermark_and_corner_tag_follow_the_classic_settings(exported, tmp_path):
    pdir, out = exported
    rep = read_json(out.parent / f"{_name(pdir)}_report.json")
    assert rep["watermark"]["enabled"] and rep["watermark"]["box"] and rep["corner_tag"] is True
    t = TITLE_SEC + 1.0
    final = _frame(out, t, tmp_path / "wf.png")
    clean = _frame(out.parent / f"{_name(pdir)}_clean.mp4", t, tmp_path / "wc.png")
    x, y, w, h = rep["watermark"]["box"]
    box = (x, y, x + w, y + h)
    assert ImageChops.difference(final.crop(box), clean.crop(box)).getbbox() is not None   # 水印只在成片里
    assert rep["checks"]["errors"] == []


def test_deck_timeline_hold_is_used_for_the_chapter_card(exported):
    pdir, out = exported
    tl = load_deck_timeline(pdir, "P01")
    assert tl["lead_in_sec"] > 0
