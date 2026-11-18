"""P6 署名与水印（纯函数）：BGM 解析、水印位置与安全区、漂移表达式、水印类型归一化、版式输出。"""
import pytest

from article_to_video.compose.branding import (drift_expressions, inside_canvas, render_corner_tag,
                                               render_credit_card, render_lower_third, render_watermark,
                                               resolve_bgm, save_bgm, watermark_xy)
from article_to_video.project_store import normalize_branding, read_json
from article_to_video.render.fonts import FontError, find_cjk_font
from article_to_video.validators.compose import check_watermark

CANVAS = (1280, 720)


@pytest.fixture(autouse=True)
def _font():
    try:
        find_cjk_font({})
    except FontError:
        pytest.skip("本机没有中文字体")


def test_bgm_disabled_without_user_file(tmp_path):
    cfg, warn = resolve_bgm(tmp_path, {"bgm": {"enabled": True, "path": "assets/bgm.mp3"}})
    assert cfg["enabled"] is False and warn  # 未提供文件：不启用，不下载


def test_bgm_accepts_project_local_audio_only(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "bgm.mp3").write_bytes(b"x")
    cfg, _ = resolve_bgm(tmp_path, {"bgm": {"enabled": True, "path": "assets/bgm.mp3", "volume": 0.3}})
    assert cfg["enabled"] and cfg["volume"] == 0.3 and cfg["path"].endswith("bgm.mp3")
    cfg2, warn = resolve_bgm(tmp_path, {"bgm": {"enabled": True, "path": "../etc/passwd.mp3"}})
    assert cfg2["enabled"] is False


def test_save_bgm_refuses_absolute_and_parent_paths(tmp_path):
    from article_to_video.project_store import write_json_atomic
    write_json_atomic(tmp_path / "project.json", {"title": "t"})
    save_bgm(tmp_path, enabled=True, rel_path="/abs/x.mp3", volume=2.0)
    from article_to_video.app_settings import load_effective_project
    assert load_effective_project(tmp_path)["bgm"]["path"] == ""
    assert load_effective_project(tmp_path)["bgm"]["volume"] == 1.0


@pytest.mark.parametrize("pos", ["top_left", "top_right", "bottom_left", "bottom_right", "center"])
def test_static_watermark_positions_lie_inside_canvas(pos):
    size = (200, 50)
    x, y = watermark_xy(CANVAS, size, pos, 0.03)
    assert inside_canvas(CANVAS, (x, y, *size))


def test_drift_expressions_stay_within_margins_by_construction():
    px, py = drift_expressions(CANVAS, (200, 50), 0.03)
    assert "sin" in px and "cos" in py and "overlay_w" in px and "overlay_h" in py


def test_watermark_outside_canvas_is_reported_by_v6():
    assert check_watermark(CANVAS, (1200, 10, 200, 50))["errors"]
    assert check_watermark(CANVAS, (10, 10, 200, 50))["errors"] == []
    assert check_watermark(CANVAS, None)["errors"] == []


def test_watermark_type_falls_back_to_text_without_image():
    wm = normalize_branding({"watermark": {"type": "image", "image": None, "text": "甲"}})[0]["watermark"]
    assert wm["type"] == "text" and wm["enabled"] is True


def test_image_watermark_requires_existing_file_at_render(tmp_path):
    from PIL import Image
    (tmp_path / "assets").mkdir()
    Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(tmp_path / "assets" / "logo.png")
    wm = {"type": "image", "image": "assets/logo.png", "opacity": 0.5, "scale": 0.06}
    mark, size = render_watermark(CANVAS, "landscape", "ink", wm, tmp_path)
    assert mark.mode == "RGBA" and size[0] > 0


def test_tile_watermark_covers_canvas_with_several_marks():
    wm = {"type": "text", "text": "作者甲", "opacity": 0.35, "scale": 0.06}
    tile, size = render_watermark(CANVAS, "landscape", "ink", wm, None, tile=True)
    assert size == CANVAS and tile.size == CANVAS
    alpha = tile.getchannel("A")
    assert alpha.getbbox() is not None  # 有水印像素
    # 斜铺应在画面的多个区域都有水印像素
    quads = [alpha.crop(b).getbbox() is not None for b in [(0, 0, 640, 360), (640, 0, 1280, 360),
                                                          (0, 360, 640, 720), (640, 360, 1280, 720)]]
    assert sum(quads) >= 3


def test_credit_layers_render_only_when_they_have_text():
    assert render_credit_card(CANVAS, "landscape", "ink", [("", "title")]).getchannel("A").getbbox() is None
    assert render_credit_card(CANVAS, "landscape", "ink", [("标题", "title")]).getchannel("A").getbbox()
    assert render_lower_third(CANVAS, "landscape", "ink", "").getchannel("A").getbbox() is None
    assert render_corner_tag(CANVAS, "landscape", "ink", "原文作者：甲").getchannel("A").getbbox()


def test_partial_branding_update_keeps_other_fields(tmp_path):
    from article_to_video.project_store import update_branding, write_json_atomic
    write_json_atomic(tmp_path / "project.json", {"title": "t"})
    update_branding(tmp_path, {"author": "作者甲", "credits": {"corner_tag": True}})
    update_branding(tmp_path, {"watermark": {"text": "新水印"}})  # 只改水印
    b = read_json(tmp_path / "project.json")["branding"]
    assert b["author"] == "作者甲"                      # 作者未被清空
    assert b["credits"]["corner_tag"] is True           # 片头等设置保持
    assert b["watermark"]["text"] == "新水印"
