"""R32：文字页（要点/列表/金句）的整页网格排版：占满页面、栏内对齐、前言不单占卡片、能分栏时分栏。"""
from pathlib import Path

import pytest
from PIL import ImageChops

from article_to_video.deck.compose import RENDER_VERSION, page_state_images
from article_to_video.deck.plan import plan_slides
from article_to_video.deck.textlayout import F_FLOOR, build_text_layout, supports
from article_to_video.render.themes import DEFAULT_THEME
from article_to_video.source.md_parse import parse_markdown

CANVAS = (1920, 1080)
TOP, BODY_H = 260, 780
FIXTURE = Path(__file__).parent / "fixtures" / "harness_article.md"


def _kp(*items, lead=False):
    return {"kind": "keypoint", "items": list(items), "emphasis": [], "lead_in": lead}


def _build(els, columns=True):
    return build_text_layout(els, CANVAS, TOP, BODY_H, "landscape", DEFAULT_THEME, columns=columns)


def test_only_text_kinds_use_the_grid():
    assert supports([_kp("甲"), {"kind": "list", "items": ["a"]}, {"kind": "quote", "items": ["q"]}])
    assert not supports([_kp("甲"), {"kind": "table", "header": ["a"], "rows": [["1"]]}])
    assert not supports([]) and _build([{"kind": "diagram", "items": ["a", "b"]}]) is None


def test_a_few_items_use_one_column_with_big_text():
    lay = _build([_kp("第一点内容"), _kp("第二点内容")])
    assert lay["columns"] == 1 and lay["effective_px"] >= 50 and not lay["small"]


def test_many_items_are_split_into_columns_instead_of_shrinking():
    els = [_kp(f"第{i}条要点的内容文字") for i in range(9)]
    lay = _build(els)
    assert lay["columns"] >= 2 and lay["effective_px"] >= F_FLOOR["landscape"] and not lay["small"]
    one = _build(els, columns=False)
    assert one["columns"] == 1 and lay["effective_px"] > one["effective_px"]      # 分栏的字号更大


def test_content_fills_the_page_width_and_stays_inside_the_region():
    lay = _build([_kp(f"第{i}条要点的内容文字") for i in range(9)])
    x0, x1, y0, y1 = lay["region"]
    xs = [x for _, x, _, _ in lay["placed"]]
    right = max(x + w for _, x, _, w in lay["placed"])
    assert min(xs) >= x0 - 1 and right <= x1 + 1
    assert (right - min(xs)) / CANVAS[0] > 0.8                       # 用到页面宽度的八成以上（旧排版约四成）
    for e, x, y, w in lay["placed"]:
        assert y0 - 1 <= y and y + e["h"] <= y1 + 1


def test_entries_in_a_column_share_the_same_left_edge_and_width():
    lay = _build([_kp(f"第{i}条要点的内容文字") for i in range(9)]
                 + [{"kind": "quote", "items": ["一句需要突出的话"], "emphasis": []}])
    by_x = {}
    for e, x, y, w in lay["placed"]:
        by_x.setdefault(round(x), set()).add(round(w))
    assert len(by_x) == lay["columns"] and all(len(v) == 1 for v in by_x.values())


def test_a_lead_in_is_a_small_heading_kept_with_its_content():
    els = [_kp(f"第{i}条要点的内容文字") for i in range(5)] + [_kp("于是：", lead=True), _kp("后面的内容")]
    lay = _build(els)
    pl = lay["placed"]
    li = next(i for i, (e, *_r) in enumerate(pl) if e["kind"] == "lead")
    assert pl[li][0]["h"] < pl[li + 1][0]["h"]                       # 前言不占整张卡片
    assert pl[li][1] == pl[li + 1][1]                                # 与下一条同栏
    assert pl[li][2] < pl[li + 1][2] < pl[li][2] + pl[li][0]["h"] + pl[li + 1][0]["h"]


def test_states_reveal_elements_in_order_without_moving_content():
    els = [_kp("甲内容"), {"kind": "list", "items": ["一", "二"], "emphasis": []}, _kp("丙内容")]
    st = page_state_images({"title": "t"}, {"title": "页", "elements": els}, CANVAS, "landscape", DEFAULT_THEME)
    assert len(st) == 3
    for a, b in zip(st, st[1:]):
        assert ImageChops.difference(a, b).getbbox() is not None
    diff = ImageChops.difference(st[0], st[1]).getbbox()
    assert diff is not None and diff[1] > 0                         # 新元素出现在原有元素之外，已有内容不挪动


def test_pages_with_tables_keep_the_previous_layout():
    meta = {}
    els = [_kp("甲"), {"kind": "table", "header": ["a", "b"], "rows": [["1", "2"]], "emphasis": []}]
    page_state_images({"title": "t"}, {"title": "页", "elements": els}, CANVAS, "landscape", DEFAULT_THEME, meta=meta)
    assert meta["mode"] in ("single", "columns")


def test_real_article_pages_are_all_legible():
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    deck = plan_slides({"id": "P01", "title": "章"}, blocks, "P01")
    modes = []
    for s in deck["slides"]:
        meta = {}
        page_state_images({"title": s["title"]}, s, CANVAS, "landscape", DEFAULT_THEME, meta=meta)
        modes.append(meta)
        assert not meta["small"], s["id"]
    grid = [m for m in modes if m["mode"] == "grid"]
    assert len(grid) > len(modes) * 0.6 and min(m["effective_px"] for m in grid) >= F_FLOOR["landscape"]


def test_render_version_bumped():
    assert RENDER_VERSION == "deck-render-v3"
