"""R31：规划阶段限制每页元素个数，避免一页挤满单句元素导致文字很小。"""
from pathlib import Path

from article_to_video.deck.plan import DECK_VERSION, MAX_PAGE_ELEMENTS, plan_slides
from article_to_video.deck.repair import build_repair_prompt
from article_to_video.deck.validate import validate_deck
from article_to_video.source.md_parse import parse_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "harness_article.md"
PART = {"id": "P01", "title": "章", "section_ids": ["s01"]}


def _blocks(n_paragraphs):
    out = [{"id": "h1", "kind": "heading", "level": 2, "text": "节", "section": "s01"}]
    out += [{"id": f"p{i}", "kind": "paragraph", "plain": f"第{i}段的一句话。", "section": "s01"}
            for i in range(n_paragraphs)]
    return out


def test_a_page_never_holds_more_than_the_element_cap():
    blocks = _blocks(20)
    deck = plan_slides(PART, blocks, "P01")
    assert max(len(s["elements"]) for s in deck["slides"]) <= MAX_PAGE_ELEMENTS
    assert len(deck["slides"]) >= 4 and deck["slides"][1]["title"].endswith("（续）")
    got = [x for s in deck["slides"] for e in s["elements"] for x in e["items"]]
    assert got == [f"第{i}段的一句话。" for i in range(20)]          # 内容完整、顺序不变
    assert validate_deck(deck, blocks)["errors"] == []


def test_lead_in_stays_with_its_content_under_the_cap():
    blocks = _blocks(5) + [{"id": "lead", "kind": "paragraph", "plain": "如下：", "section": "s01"},
                           {"id": "lst", "kind": "list", "items": ["甲", "乙"], "section": "s01"}]
    deck = plan_slides(PART, blocks, "P01")
    assert validate_deck(deck, blocks)["errors"] == []
    for s in deck["slides"]:
        assert not s["elements"][-1].get("lead_in")


def test_real_article_pages_respect_the_cap():
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    deck = plan_slides(PART | {"section_ids": sorted({b["section"] for b in blocks if b.get("section")})},
                       blocks, "P01")
    assert max(len(s["elements"]) for s in deck["slides"]) <= MAX_PAGE_ELEMENTS
    assert validate_deck(deck, blocks)["errors"] == []


def test_too_many_elements_is_a_warning_not_an_error():
    blocks = _blocks(8)
    deck = plan_slides(PART, blocks, "P01")
    deck["slides"] = [dict(deck["slides"][0], elements=[e for s in deck["slides"] for e in s["elements"]])]
    deck["slides"][0]["source_blocks"] = [b["id"] for b in blocks[1:]]
    v = validate_deck(deck, blocks)
    assert v["errors"] == [] and any("个元素" in w for w in v["warnings"])


def test_rules_version_changed_and_repair_prompt_mentions_the_cap():
    assert DECK_VERSION == "deck-rules-v5"
    assert "尽量不超过 6 个" in build_repair_prompt("章", _blocks(1), None, ["x"], 12, 12)
