"""R22：角标（作者）位置可设置，默认右上角。"""
import os

import pytest

from article_to_video.compose.branding import corner_tag_xy, render_corner_tag
from article_to_video.compose.config import save_compose_config
from article_to_video.compose.export import export_final
from article_to_video.media.ffmpeg import find_ffmpeg
from article_to_video.project_store import (CORNER_POSITIONS, normalize_branding, read_json, update_branding)
from helpers_p4 import voiced_project
from helpers_p5 import fake_part

CANVAS = (1280, 720)


def _bbox(**kw):
    return render_corner_tag(CANVAS, "landscape", "ink", "原文作者：甲", **kw).getchannel("A").getbbox()


def test_default_position_is_top_right():
    b, w = normalize_branding({})
    assert b["credits"]["corner_position"] == "top_right" and w == []
    x0, y0, x1, y1 = _bbox()
    assert x0 > CANVAS[0] / 2 and y1 < CANVAS[1] / 2   # 默认在右上象限


@pytest.mark.parametrize("pos,right,top", [("top_left", False, True), ("top_right", True, True),
                                           ("bottom_left", False, False), ("bottom_right", True, False)])
def test_each_corner(pos, right, top):
    x0, y0, x1, y1 = _bbox(position=pos)
    assert (x0 > CANVAS[0] / 2) == right and (y1 < CANVAS[1] / 2) == top
    assert x0 >= 0 and y0 >= 0 and x1 <= CANVAS[0] and y1 <= CANVAS[1]   # 完整在画面内


def test_avoid_height_moves_tag_inward():
    top = _bbox(position="top_right")
    top2 = _bbox(position="top_right", avoid_h=60)
    assert top2[1] == top[1] + 60
    bot = _bbox(position="bottom_right")
    bot2 = _bbox(position="bottom_right", avoid_h=60)
    assert bot2[3] == bot[3] - 60
    assert corner_tag_xy(CANVAS, (100, 40), "top_left")[0] == round(CANVAS[0] * 0.03)


def test_invalid_position_falls_back_with_warning():
    b, w = normalize_branding({"credits": {"corner_position": "middle"}})
    assert b["credits"]["corner_position"] == "top_right" and any("角标位置" in x for x in w)
    assert set(CORNER_POSITIONS) == {"top_left", "top_right", "bottom_left", "bottom_right"}


def test_position_saved_and_other_credits_kept(tmp_path):
    from article_to_video.project_store import write_json_atomic
    write_json_atomic(tmp_path / "project.json", {"title": "t"})
    update_branding(tmp_path, {"credits": {"corner_tag": True}})
    update_branding(tmp_path, {"credits": {"corner_position": "bottom_left"}})
    c = read_json(tmp_path / "project.json")["branding"]["credits"]
    assert c["corner_position"] == "bottom_left" and c["corner_tag"] is True


def test_settings_page_lists_position_field():
    from ui.settings_page import FIELDS, LABELS
    paths = [f["path"] for f in FIELDS["branding"]]
    assert "credits.corner_position" in paths
    f = next(f for f in FIELDS["branding"] if f["path"] == "credits.corner_position")
    assert f["options"] == list(CORNER_POSITIONS) and "默认" in LABELS["corner"]["top_right"]


@pytest.mark.skipif(not find_ffmpeg(), reason="需要 ffmpeg")
def test_export_uses_position_and_reruns_master_on_change(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    d, _ = voiced_project(tmp_path)
    from article_to_video.app_settings import set_field
    set_field(d, "settings", "video.resolution", "720P")
    fake_part(d, "P01", 3.0)
    update_branding(d, {"author": "作者甲", "watermark": {"enabled": True, "text": "水印", "position": "top_right",
                                                          "mode": "static"},
                        "credits": {"title_card": False, "lower_third": False, "end_card": False,
                                    "corner_tag": True}})
    save_compose_config(d, {"transition": "cut"})

    def rep():
        return read_json(d / "exports" / next(f for f in os.listdir(d / "exports") if f.endswith("_report.json")))

    r1 = export_final(d, part_ids=["P01"])
    assert r1["corner_position"] == "top_right"          # 未设置 → 默认右上
    st = read_json(d / "exports" / ".work" / "main" / "state.json")["master"]
    update_branding(d, {"credits": {"corner_position": "bottom_left"}})
    r2 = export_final(d, part_ids=["P01"])
    assert r2["corner_position"] == "bottom_left"
    assert read_json(d / "exports" / ".work" / "main" / "state.json")["master"] != st   # 改位置会重做主版本
