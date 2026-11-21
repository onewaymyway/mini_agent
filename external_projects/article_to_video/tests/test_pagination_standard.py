"""分页标准：标题与前言和它引出的内容同页；页面不只有标题或前言；节从新页开始；续页标题加“（续）”；
不留过小的尾页；前言不丢失。"""
from pathlib import Path

from article_to_video.deck.plan import MAX_SLIDE_ITEMS, is_lead_in, plan_slides
from article_to_video.deck.validate import validate_deck
from article_to_video.source.md_parse import parse_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "harness_article.md"
PART = {"id": "P01", "title": "章", "section_ids": ["s01", "s02"]}


def _h(i, text, level=2, sec="s01"):
    return {"id": f"h{i}", "kind": "heading", "level": level, "text": text, "section": sec}


def _p(i, text, sec="s01"):
    return {"id": f"p{i}", "kind": "paragraph", "plain": text, "section": sec}


def _list(i, n, sec="s01", prefix="条目"):
    return {"id": f"l{i}", "kind": "list", "items": [f"{prefix}{k:02d}内容" for k in range(n)], "section": sec}


def _items(deck):
    return [x for s in deck["slides"] for e in s["elements"] for x in (e.get("items") or [])]


def test_lead_in_stays_on_the_same_page_as_its_content():
    blocks = [_h(1, "第一节"), _p(1, "下面三条如下："), _list(1, 15)]
    deck = plan_slides(PART, blocks, "P01")
    first = deck["slides"][0]["elements"]
    assert first[0].get("lead_in") is True
    assert not first[-1].get("lead_in")                   # 页尾不是前言
    assert validate_deck(deck, blocks)["errors"] == []


def test_a_page_never_holds_only_a_title_or_lead_in():
    blocks = [_h(1, "第一节"), _p(1, "下面的内容如下："), _h(2, "第二节", sec="s02"), _p(2, "只有一句。", sec="s02")]
    deck = plan_slides(PART, blocks, "P01")
    for s in deck["slides"]:
        assert not all(e.get("lead_in") for e in s["elements"])
    assert validate_deck(deck, blocks)["errors"] == []


def test_each_top_level_heading_starts_a_new_page():
    blocks = [_h(1, "甲节"), _p(1, "甲的内容一。"), _h(2, "乙节", sec="s02"), _p(2, "乙的内容。", sec="s02")]
    deck = plan_slides(PART, blocks, "P01")
    assert [s["title"] for s in deck["slides"]] == ["甲节", "乙节"]


def test_continuation_pages_carry_the_mark_and_keep_order():
    items = [f"条目{i:02d}内容" for i in range(40)]
    blocks = [_h(1, "长节"), {"id": "l", "kind": "list", "items": items, "section": "s01"}]
    deck = plan_slides(PART, blocks, "P01")
    assert len(deck["slides"]) >= 4
    assert deck["slides"][0]["title"] == "长节"
    assert all(s["title"] == "长节（续）" for s in deck["slides"][1:])
    assert _items(deck) == items                            # 全部保留、顺序不变


def test_a_tail_stays_separate_when_the_previous_page_is_full():
    blocks = [_h(1, "节"), _list(1, MAX_SLIDE_ITEMS + 1)]    # 13 条：前一页满 12 条，尾页 1 条放不进去
    deck = plan_slides(PART, blocks, "P01")
    assert [len(e["items"]) for s in deck["slides"] for e in s["elements"]] == [MAX_SLIDE_ITEMS, 1]
    assert _items(deck) == [f"条目{k:02d}内容" for k in range(MAX_SLIDE_ITEMS + 1)]


def test_a_trailing_lead_in_is_not_lost():
    blocks = [_h(1, "节"), _list(1, 3), _p(9, "最后的前言：")]
    deck = plan_slides(PART, blocks, "P01")
    texts = [x for s in deck["slides"] for e in s["elements"] for x in (e.get("items") or [])]
    assert any("最后的前言" in t for t in texts)


def test_lead_in_detection():
    assert is_lead_in({"kind": "paragraph", "plain": "下面三条如下："})
    assert is_lead_in({"kind": "paragraph", "plain": "以下是原因:"})
    assert not is_lead_in({"kind": "paragraph", "plain": "这是一个普通的陈述句。"})
    assert not is_lead_in({"kind": "list", "items": ["a"]})


def test_validator_rejects_a_lead_in_left_at_the_page_end():
    deck = {"slides": [{"id": "P01.S01", "title": "t", "elements": [
        {"kind": "keypoint", "items": ["内容"]}, {"kind": "keypoint", "items": ["前言："], "lead_in": True}],
        "source_blocks": ["x"], "est_sec": 10}]}
    v = validate_deck(deck, [{"id": "x", "kind": "paragraph", "plain": "内容。前言："}])
    assert any("前言留在页尾" in e for e in v["errors"])


def test_validator_rejects_a_page_with_only_a_lead_in():
    deck = {"slides": [{"id": "P01.S01", "title": "t", "elements": [
        {"kind": "keypoint", "items": ["前言："], "lead_in": True}], "source_blocks": ["x"], "est_sec": 10}]}
    v = validate_deck(deck, [{"id": "x", "kind": "paragraph", "plain": "前言："}])
    assert any("只有标题或前言" in e for e in v["errors"])


def test_full_article_follows_the_standard():
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    secs = sorted({b["section"] for b in blocks})
    deck = plan_slides({"id": "P01", "title": "测试文章", "section_ids": secs}, blocks, "P01")
    assert validate_deck(deck, blocks)["errors"] == []
    for s in deck["slides"]:
        els = s["elements"]
        assert els and not all(e.get("lead_in") for e in els)
        assert not els[-1].get("lead_in")
        assert sum(len(e.get("items") or []) for e in els) <= MAX_SLIDE_ITEMS or any(
            e["kind"] in ("table", "diagram", "formula") for e in els)
