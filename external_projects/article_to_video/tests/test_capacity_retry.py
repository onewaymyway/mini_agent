"""容量与重试：较多条目不再让规划失败（自动拆分/续页）；规划不合格时改用更细的分页重试。"""
import pytest
from PIL import Image, ImageDraw

from article_to_video.deck import store as ST
from article_to_video.deck.plan import MAX_ELEMENT_ITEMS, MAX_SLIDE_ITEMS, RETRY_CAPS, plan_slides
from article_to_video.deck.validate import validate_deck
from article_to_video.render.fonts import FontError, find_cjk_font
from article_to_video.render.renderers import _Ctx, _grid_fit, _place_columns, _stack_fit
from article_to_video.render.themes import DEFAULT_THEME, canvas_size
from article_to_video.visual.director import MAX_DIAGRAM_NODES, MAX_KEYPOINT_ITEMS, MAX_LIST_ITEMS

TXT = "这是一个较长的要点，用来测试在大量条目时分栏与缩小字号能否容纳全部文字内容"


def _chain(n):
    return [{"id": "c", "kind": "code", "shape": "chain", "nodes": [f"节点{i:02d}" for i in range(n)],
             "loop": False, "section": "s01"},
            {"id": "h", "kind": "heading", "level": 2, "text": "流程", "section": "s01"}]


def test_twelve_nodes_plan_without_error():
    blocks = [{"id": "h", "kind": "heading", "level": 2, "text": "流程", "section": "s01"}] + _chain(12)[:1]
    deck = plan_slides({"id": "P01", "title": "t", "section_ids": ["s01"]}, blocks, "P01")
    assert validate_deck(deck, blocks)["errors"] == []


def test_a_long_flow_is_split_across_pages_in_order():
    blocks = [{"id": "h", "kind": "heading", "level": 2, "text": "流程", "section": "s01"},
              {"id": "c", "kind": "code", "shape": "chain", "nodes": [f"节点{i:02d}" for i in range(30)],
               "loop": False, "section": "s01"}]
    deck = plan_slides({"id": "P01", "title": "t", "section_ids": ["s01"]}, blocks, "P01")
    nodes = [x for s in deck["slides"] for e in s["elements"] for x in e.get("items", [])]
    assert nodes == [f"节点{i:02d}" for i in range(30)]            # 全部保留、顺序不变
    assert all(len(e["items"]) <= MAX_ELEMENT_ITEMS for s in deck["slides"] for e in s["elements"])
    assert validate_deck(deck, blocks)["errors"] == []


def test_planning_retries_with_finer_pages_when_a_check_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    blocks = [{"id": "h", "kind": "heading", "level": 2, "text": "甲", "section": "s01"},
              {"id": "p", "kind": "paragraph", "plain": "第一句。第二句。", "section": "s01"}]
    part = {"id": "P01", "title": "t", "section_ids": ["s01"]}
    real = ST.validate_deck
    calls = []

    def flaky(deck, blocks, **kw):
        calls.append(kw.get("page_cap"))
        if len(calls) == 1:
            return {"errors": ["模拟的第一次失败"], "warnings": []}
        return real(deck, blocks, **kw)

    monkeypatch.setattr(ST, "validate_deck", flaky)
    deck = ST._plan_with_retry(part, blocks, "P01")
    assert deck["attempts"] == 2
    assert calls == [RETRY_CAPS[0][0], RETRY_CAPS[1][0]]          # 第二次改用更细的分页


def test_gives_up_only_after_all_retries_fail(monkeypatch):
    blocks = [{"id": "p", "kind": "paragraph", "plain": "甲。", "section": "s01"}]
    monkeypatch.setattr(ST, "validate_deck", lambda *a, **k: {"errors": ["始终不合格"], "warnings": []})
    from article_to_video.planning.errors import PlanError
    with pytest.raises(PlanError) as exc:
        ST._plan_with_retry({"id": "P01", "title": "t", "section_ids": ["s01"]}, blocks, "P01")
    assert str(len(RETRY_CAPS)) in str(exc.value)


# ---------- 渲染容量（与上限一致） ----------

def _ctx(orientation):
    try:
        find_cjk_font({})
    except FontError:
        pytest.skip("本机没有中文字体")
    return _Ctx(canvas_size(orientation, "1080p"), orientation, DEFAULT_THEME)


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_renderer_fits_the_keypoint_capacity(orientation):
    ctx = _ctx(orientation)
    d = ImageDraw.Draw(Image.new("RGBA", (ctx.W, ctx.H)))
    n = MAX_KEYPOINT_ITEMS
    items = [f"{i + 1}. {TXT}" for i in range(n)]
    font, col_w, assign, blocks, heights = _stack_fit(
        d, ctx, items, base=66 if ctx.landscape else 54, lo=30 if ctx.landscape else 26,
        avail_w=ctx.content_w, avail_h=ctx.H - 2 * ctx.my, gap_v=30 * ctx.u, gap_x=60 * ctx.u,
        pad_v=36 * ctx.u, pad_h=36 * ctx.u, text_extra=10 * ctx.u, ratio=0.25)
    pos = _place_columns(ctx, assign, heights, col_w, 30 * ctx.u, 60 * ctx.u)
    assert len(pos) == n
    assert all(pos[i][1] >= 0 and pos[i][1] + heights[i] <= ctx.H for i in range(n))


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_renderer_fits_the_flow_capacity(orientation):
    ctx = _ctx(orientation)
    d = ImageDraw.Draw(Image.new("RGBA", (ctx.W, ctx.H)))
    nodes = ["外部世界进入感官接收并经过注意力筛选的节点名称"] * MAX_DIAGRAM_NODES
    font, cols, nw, nh, blocks, lh = _grid_fit(
        d, ctx, nodes, avail_w=ctx.content_w * 0.8, avail_h=ctx.H - 2 * ctx.my,
        base=42 if ctx.landscape else 36, lo=24 if ctx.landscape else 22, gx=90 * ctx.u, gy=70 * ctx.u,
        prefer_wide=ctx.landscape)
    rows = -(-len(nodes) // cols)
    assert rows * nh + (rows - 1) * 70 * ctx.u <= ctx.H - 2 * ctx.my + 1e-6


def test_limits_are_the_documented_capacities():
    assert MAX_KEYPOINT_ITEMS == 12 and MAX_LIST_ITEMS == 12 and MAX_DIAGRAM_NODES == 16
    assert MAX_SLIDE_ITEMS == 12 and MAX_ELEMENT_ITEMS == 12
