"""R30：新方案页面排版——不再一味缩小；内容多时优先让渲染器缩字号，不够再分两栏；过小的页给出标记。"""
import pytest

from article_to_video.deck.compose import _scene_for, build_segments, page_state_images
from article_to_video.deck.layout import FLOOR_BASE, layout_page
from article_to_video.render.themes import DEFAULT_THEME
from article_to_video.visual.config import default_visual, normalize_visual
from helpers_p2 import make_project

CANVAS = (1920, 1080)


def _kp(text):
    return {"kind": "keypoint", "items": [text], "emphasis": []}


def _many(n):
    return [_kp(f"第{i}个元素的一句话内容，用来检验排版。") for i in range(n)]


def _layout(elements, columns=True, orientation="landscape"):
    body_h, gap = 800, 38
    return layout_page(elements, CANVAS, body_h, gap, orientation, DEFAULT_THEME, _scene_for, columns=columns), body_h


def test_a_short_page_keeps_one_column_and_big_text():
    lay, _ = _layout(_many(2))
    assert lay["mode"] == "single" and lay["effective_px"] >= 60 and not lay["small"]


def test_a_crowded_page_is_split_into_two_columns_with_legible_text():
    els = _many(8) + [{"kind": "quote", "items": ["一句金句"], "emphasis": []}]
    lay, body_h = _layout(els)
    assert lay["mode"] == "columns" and 0 < lay["split"] < len(els)
    assert lay["effective_px"] >= lay["floor_px"] and not lay["small"]
    assert len(lay["pieces"]) == len(els)                         # 每个元素都在页面上
    xs = {x for _, x, _ in lay["pieces"]}
    assert len(xs) > 1 and min(xs) >= 0                           # 左右两栏
    for img, x, y in lay["pieces"]:                               # 不超出页面，没有被截断
        assert 0 <= y and y + img.size[1] <= body_h and x + img.size[0] <= CANVAS[0]


def test_columns_can_be_turned_off():
    lay, _ = _layout(_many(8) + [{"kind": "quote", "items": ["一句金句"], "emphasis": []}], columns=False)
    assert lay["mode"] == "single"


def test_columns_beat_plain_shrinking():
    els = _many(8) + [{"kind": "quote", "items": ["一句金句"], "emphasis": []}]
    on, _ = _layout(els, True)
    off, _ = _layout(els, False)
    assert on["effective_px"] > off["effective_px"] * 1.05


def test_an_overloaded_page_is_flagged_not_silently_shrunk():
    lay, body_h = _layout(_many(14))
    assert lay["small"] is True and lay["effective_px"] < lay["floor_px"]
    for img, x, y in lay["pieces"]:
        assert y + img.size[1] <= body_h


def test_portrait_uses_its_own_floor():
    assert FLOOR_BASE["portrait"] < FLOOR_BASE["landscape"]
    lay, _ = _layout(_many(3), orientation="portrait")
    assert lay["floor_px"] == pytest.approx(FLOOR_BASE["portrait"] * 1920 / 1080, abs=0.1)   # 竖屏以 1080 宽为单位 1.0


def test_states_reveal_elements_one_by_one_in_the_columns_layout():
    els = _many(8) + [{"kind": "quote", "items": ["一句金句"], "emphasis": []}]
    meta = {}
    st = page_state_images({"title": "t"}, {"title": "页", "elements": els}, CANVAS, "landscape", DEFAULT_THEME,
                           meta=meta)
    assert len(st) == len(els) and meta["mode"] == "columns"
    from PIL import ImageChops
    for a, b in zip(st, st[1:]):
        assert ImageChops.difference(a, b).getbbox() is not None


def test_build_segments_records_layout_per_page(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    els = _many(8) + [{"kind": "quote", "items": ["一句金句"], "emphasis": []}]
    deck = {"slides": [{"id": "P01.S01", "title": "甲", "elements": els}]}
    tl = {"lead_in_sec": 3.0, "total_sec": 10.0, "pages": [
        {"id": "P01.S01", "start": 3.0, "end": 9.0, "reveals": [{"element": i, "at_sec": 3.0 + i * 0.5}
                                                              for i in range(len(els))]}]}
    log = []
    build_segments(pdir, "P01", deck, tl, CANVAS, "landscape", DEFAULT_THEME,
                   {"number": "第 1 章", "title": "测试", "one_line": ""}, layout_log=log)
    assert log[0]["id"] == "P01.S01" and log[0]["mode"] == "columns" and log[0]["small"] is False


def test_setting_defaults_to_on_and_normalizes():
    assert default_visual()["deck_layout"] == {"columns": True}
    assert normalize_visual({"deck_layout": {"columns": False}})[0]["deck_layout"] == {"columns": False}
    assert normalize_visual({})[0]["deck_layout"] == {"columns": True}


def test_settings_page_has_the_switch():
    from ui.settings_page import FIELDS
    assert any(f["path"] == "deck_layout.columns" and f["kind"] == "bool" for f in FIELDS["visual"])
