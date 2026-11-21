"""R33：列表整体放在一栏（字少时栏内排网格）；语义分页（问句与回答、同一段、空章节标题不丢）。"""
from pathlib import Path

from article_to_video.deck.plan import DECK_VERSION, heading_punchlines, is_lead_in, plan_slides
from article_to_video.deck.textlayout import _wrap, build_text_layout
from article_to_video.deck.validate import validate_deck
from article_to_video.render.fonts import load_font
from article_to_video.render.themes import DEFAULT_THEME
from article_to_video.source.md_parse import parse_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "intelligence_article.md"
CANVAS, TOP, BODY_H = (1920, 1080), 260, 780
PART = {"id": "P01", "title": "章", "section_ids": ["s01"]}


def _h(i, text, level=2):
    return {"id": f"h{i}", "kind": "heading", "level": level, "text": text, "section": "s01"}


def _p(i, text):
    return {"id": f"p{i}", "kind": "paragraph", "plain": text, "section": "s01"}


def _layout(els):
    return build_text_layout(els, CANVAS, TOP, BODY_H, "landscape", DEFAULT_THEME)


def _all_text(deck):
    return [x for s in deck["slides"] for e in s["elements"] for x in (e.get("items") or [])]


# ---------- 排版：列表 ----------

def test_a_list_stays_in_one_column_and_short_items_form_a_grid_inside_it():
    items = ["程序员", "科学家", "作家", "数学家", "产品经理", "教师", "分析师", "艺术家", "研究员"]
    els = [{"kind": "keypoint", "items": ["一个模型中可能存在："], "emphasis": [], "lead_in": True},
           {"kind": "list", "items": items, "emphasis": []},
           {"kind": "keypoint", "items": ["无数种潜在的角色。"], "emphasis": [], "lead_in": False}]
    lay = _layout(els)
    lists = [e for e, *_ in lay["placed"] if e["kind"] == "list"]
    assert len(lists) == 1 and lists[0]["grid"]["k"] == 3                  # 一个整体，栏内 3 列
    assert lists[0]["h"] < 9 * lay["effective_px"]                          # 比逐条竖排矮得多
    assert not [e for e, *_ in lay["placed"] if e["kind"] == "row"]         # 没有被拆成逐条的行


def test_a_short_list_is_a_plain_vertical_list():
    lay = _layout([{"kind": "list", "items": ["人类；", "摄像头；", "卫星；"], "emphasis": []}])
    assert lay["placed"][0][0]["grid"]["k"] == 1


def test_a_list_is_never_split_across_the_left_and_right_columns():
    els = [{"kind": "keypoint", "items": [f"第{i}条要点的内容文字"], "emphasis": [], "lead_in": False} for i in range(5)]
    els.insert(2, {"kind": "list", "items": [f"条目{i}" for i in range(6)], "emphasis": []})
    lay = _layout(els)
    xs = {round(x) for e, x, *_ in lay["placed"] if e["kind"] == "list"}
    assert len(xs) == 1


def test_punctuation_never_starts_a_line():
    font = load_font(56)
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    for width in range(200, 700, 13):
        for ln in _wrap(d, "最重要的是——人的寿命有限。", font, width)[1:]:
            assert ln[0] not in "。，、；！？"


# ---------- 规划：语义分页 ----------

def test_version_and_short_questions_are_lead_ins():
    assert DECK_VERSION == "deck-rules-v5"
    assert is_lead_in(_p(1, "为什么？")) and not is_lead_in(_p(2, "这是一个比较长的问题，它不应被当作前言吗？"))


def test_a_short_question_stays_with_its_answer():
    blocks = [_h(1, "节")] + [_p(i, f"第{i}段的内容句子。") for i in range(4)] + [
        _p(10, "为什么？"), _p(11, "因为这样更好。"), _p(12, "后面还有一段内容。")]
    deck = plan_slides(PART, blocks, "P01")
    assert validate_deck(deck, blocks)["errors"] == []
    for s in deck["slides"]:
        assert not (s["elements"][-1].get("items") or [""])[-1].endswith("？")


def test_pages_do_not_end_on_a_long_question_before_its_answer():
    q = "这究竟意味着什么呢，我们该如何去理解它？"
    blocks = [_h(1, "节")] + [_p(i, f"第{i}段的内容句子比较长一些，用来占位。") for i in range(5)] + [
        _p(10, q), _p(11, "答案是这样的，需要和问题放在一起看。")] + [_p(20 + i, f"收尾第{i}段内容。") for i in range(4)]
    deck = plan_slides(PART, blocks, "P01")
    for s in deck["slides"][:-1]:
        assert (s["elements"][-1].get("items") or [""])[-1] != q


def test_an_empty_chapter_heading_becomes_the_punchline_of_the_previous_lead():
    blocks = [_h(1, "十二、通信"), _p(1, "互联网解决了带宽问题。"), _p(2, "但还有一个更加重要的问题："),
              _h(2, "语义带宽。", level=1), _h(3, "十三、矛盾"), _p(3, "信息越来越多。")]
    out = heading_punchlines(blocks)
    assert out[3]["kind"] == "quote" and out[3]["plain"] == "语义带宽。"
    deck = plan_slides(PART, blocks, "P01")
    assert validate_deck(deck, blocks)["errors"] == []
    first = deck["slides"][0]
    items = [x for e in first["elements"] for x in e["items"]]
    assert "但还有一个更加重要的问题：" in items and "语义带宽。" in items        # 前言与答案同页
    assert deck["slides"][1]["title"] == "十三、矛盾"


def test_a_parent_heading_before_a_child_heading_is_not_turned_into_a_quote():
    blocks = [_h(1, "一、大标题"), _h(2, "（一）小标题"), _p(1, "内容。")]
    assert heading_punchlines(blocks) == blocks


def test_real_article_pages_are_complete_and_continuous():
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    deck = plan_slides(PART, blocks, "P01")
    v = validate_deck(deck, blocks)
    assert v["errors"] == [] and v["warnings"] == []
    assert "语义带宽。" in _all_text(deck)                                  # 空章节标题不再丢失
    assert max(len(s["elements"]) for s in deck["slides"]) <= 6
    assert sum(len(s["elements"]) == 1 for s in deck["slides"]) <= 3        # 孤页很少
    for a, b in zip(deck["slides"], deck["slides"][1:]):
        last = (a["elements"][-1].get("items") or [""])[-1]
        assert not (last.endswith("？") and b["elements"][0].get("lead_in"))   # 问句之后不接前言页首
