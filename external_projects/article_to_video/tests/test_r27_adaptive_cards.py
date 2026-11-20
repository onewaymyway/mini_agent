"""R27 卡片自适应：要点、列表、流程图的全部条目都画得下（字号与分栏随数量与画面调整，不截断）。"""
import pytest
from PIL import Image, ImageDraw

from article_to_video.render.fonts import FontError, find_cjk_font
from article_to_video.render.renderers import _Ctx, _grid_fit, _place_columns, _stack_fit
from article_to_video.render.themes import DEFAULT_THEME, canvas_size
from article_to_video.validators.visual import validate_storyboard
from article_to_video.visual.director import MAX_DIAGRAM_NODES, MAX_KEYPOINT_ITEMS, MAX_LIST_ITEMS

LONG = "这是一个相对较长的要点，用来检验自动换行与分栏是否都能把全部文字完整地画出来，不被省略"
ORIENTATIONS = ("landscape", "portrait")


def _need_font():
    try:
        find_cjk_font({})
    except FontError:
        pytest.skip("本机没有中文字体")


def _ctx(orientation):
    return _Ctx(canvas_size(orientation, "1080p"), orientation, DEFAULT_THEME)


def _draw(ctx):
    return ImageDraw.Draw(Image.new("RGBA", (ctx.W, ctx.H)))


def _keypoint_layout(ctx, items):
    d = _draw(ctx)
    font, col_w, assign, blocks, heights = _stack_fit(
        d, ctx, items, base=66 if ctx.landscape else 54, lo=30 if ctx.landscape else 26,
        avail_w=ctx.content_w, avail_h=ctx.H - 2 * ctx.my, gap_v=30 * ctx.u, gap_x=60 * ctx.u,
        pad_v=36 * ctx.u, pad_h=36 * ctx.u, text_extra=10 * ctx.u, ratio=0.25)
    pos = _place_columns(ctx, assign, heights, col_w, 30 * ctx.u, 60 * ctx.u)
    return pos, heights, col_w, blocks


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_eight_long_keypoints_are_all_placed_inside_the_canvas(orientation):
    _need_font()
    ctx = _ctx(orientation)
    items = [LONG] * MAX_KEYPOINT_ITEMS
    pos, heights, col_w, blocks = _keypoint_layout(ctx, items)
    assert len(pos) == len(items)
    for i in range(len(items)):
        x, y = pos[i]
        assert x >= 0 and y >= 0 and x + col_w <= ctx.W and y + heights[i] <= ctx.H
    assert not any("…" in "".join(b) for b in blocks)           # 不省略


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_more_items_shrink_the_font_instead_of_dropping_them(orientation):
    _need_font()
    ctx = _ctx(orientation)
    few, *_ = _keypoint_layout(ctx, [LONG] * 2)
    many_pos, heights, col_w, _ = _keypoint_layout(ctx, [LONG] * MAX_KEYPOINT_ITEMS)
    assert len(many_pos) == MAX_KEYPOINT_ITEMS
    assert set(many_pos) == set(range(MAX_KEYPOINT_ITEMS))


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_twelve_list_items_are_all_placed(orientation):
    _need_font()
    ctx = _ctx(orientation)
    d = _draw(ctx)
    items = [f"第{i}项：{LONG[:20]}" for i in range(MAX_LIST_ITEMS)]
    font, col_w, assign, blocks, heights = _stack_fit(
        d, ctx, items, base=52 if ctx.landscape else 44, lo=26 if ctx.landscape else 24,
        avail_w=ctx.content_w * 0.8, avail_h=ctx.H - 2 * ctx.my, gap_v=34 * ctx.u, gap_x=80 * ctx.u,
        pad_v=0, pad_h=0, text_extra=2 * 30 * ctx.u + 36 * ctx.u, ratio=0.2)
    pos = _place_columns(ctx, assign, heights, col_w, 34 * ctx.u, 80 * ctx.u)
    assert len(pos) == MAX_LIST_ITEMS
    for i in range(MAX_LIST_ITEMS):
        x, y = pos[i]
        assert y >= 0 and y + heights[i] <= ctx.H


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_twelve_flow_nodes_fit_the_grid_without_truncation(orientation):
    _need_font()
    ctx = _ctx(orientation)
    d = _draw(ctx)
    nodes = ["外部世界进入感官接收并经过注意力筛选的长节点名称"] * MAX_DIAGRAM_NODES
    avail_w = ctx.content_w * (0.8 if ctx.landscape else 0.92)
    font, cols, node_w, node_h, blocks, lh = _grid_fit(
        d, ctx, nodes, avail_w=avail_w, avail_h=ctx.H - 2 * ctx.my, base=42 if ctx.landscape else 36,
        lo=24 if ctx.landscape else 22, gx=90 * ctx.u, gy=70 * ctx.u, prefer_wide=ctx.landscape)
    rows = -(-len(nodes) // cols)
    total_h = rows * node_h + (rows - 1) * 70 * ctx.u
    total_w = cols * node_w + (cols - 1) * 90 * ctx.u
    assert total_h <= ctx.H - 2 * ctx.my + 1e-6
    assert total_w <= avail_w + 1e-6
    assert not any("…" in "".join(b) for b in blocks)


@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_render_draws_every_keypoint_state(orientation):
    _need_font()
    from article_to_video.render.renderers import render_scene_frames
    canvas = canvas_size(orientation, "1080p")
    items = [LONG] * MAX_KEYPOINT_ITEMS
    sc = {"type": "keypoint", "items": items, "emphasis": [],
          "reveal": [{"step": i, "at_sentence": 0, "at_frac": 0.0} for i in range(len(items))]}
    frames = render_scene_frames(sc, canvas, orientation, DEFAULT_THEME)
    assert len(frames) == len(items)
    assert frames[-1].getbbox() is not None


def test_validator_accepts_the_new_display_limits():
    script = {"beats": [{"id": "P01.b01", "sentences": [{"text": "甲甲甲", "quote": False}]}]}
    scene = {"id": "P01.b01", "beat": "P01.b01", "type": "keypoint", "sentences": ["P01.b01.s01", "P01.b01.s01"],
             "items": ["甲甲甲"] * MAX_KEYPOINT_ITEMS, "emphasis": [], "reveal": [], "kind": "keypoint"}
    v = validate_storyboard({"scenes": [scene]}, script, allowance=3, style_anchor="无文字", images_enabled=False)
    assert not any("超过上限" in e for e in v["errors"])
