"""R42：幻灯片规划的确定性修复——能用代码修好的不交给模型；修复循环只接受更好的结果，重复即停止。"""
import pytest

from article_to_video.deck import repair as rp
from article_to_video.deck.autofix import autofix_deck
from article_to_video.deck.validate import validate_deck
from article_to_video.planning.errors import PlanError


def _list_block(bid, n, prefix="条目"):
    return {"id": bid, "kind": "list", "items": [f"{prefix}内容{i}" for i in range(n)]}


def _slide(i, title, sections, source_blocks, elements):
    return {"id": f"P01.S{i:02d}", "title": title, "section_ids": sections, "source_blocks": source_blocks,
            "elements": elements, "chars": 0, "est_sec": 0.0}


def _kp(items, lead=False):
    return {"kind": "keypoint", "items": items, "emphasis": [], "lead_in": lead}


def _deck(slides, cap=12):
    return {"schema": 1, "part": "P01", "mode": "deck", "slides": slides, "page_cap": cap, "element_cap": cap}


def _errs(deck, blocks, cap=12):
    return validate_deck(deck, blocks, page_cap=cap, element_cap=cap)["errors"]


def test_oversize_page_is_split_into_continuation_pages():
    blocks = [_list_block("s01.p01", 17)]
    deck = _deck([_slide(1, "甲", ["s01"], ["s01.p01"], [{"kind": "list", "items": [f"条目内容{i}" for i in range(17)], "emphasis": []}])])
    assert any("超过一页上限" in e for e in _errs(deck, blocks))
    fixed, notes = autofix_deck(deck, blocks, "P01", page_cap=12, element_cap=12)
    assert len(fixed["slides"]) >= 2
    assert fixed["slides"][1]["title"].endswith("（续）")
    assert _errs(fixed, blocks) == []
    assert any("续页" in n for n in notes)


def test_fabricated_items_are_removed():
    blocks = [{"id": "s01.p01", "kind": "list", "items": ["真实的条目"]}]
    deck = _deck([_slide(1, "甲", ["s01"], ["s01.p01"], [_kp(["真实的条目", "编造的句子XYZ"])])])
    assert any("不在原文中" in e for e in _errs(deck, blocks))
    fixed, _ = autofix_deck(deck, blocks, "P01", page_cap=12)
    assert fixed["slides"][0]["elements"][0]["items"] == ["真实的条目"]
    assert _errs(fixed, blocks) == []


def test_uncovered_block_is_attached_to_the_same_section():
    blocks = [{"id": "s01.p01", "kind": "list", "items": ["第一节内容"]},
              {"id": "s02.p01", "kind": "list", "items": ["第二节内容"]}]
    deck = _deck([_slide(1, "第一节", ["s01"], ["s01.p01"], [_kp(["第一节内容"])])])
    assert any("未被任何幻灯片承载" in e for e in _errs(deck, blocks))
    fixed, notes = autofix_deck(deck, blocks, "P01", page_cap=12)
    assert _errs(fixed, blocks) == []
    assert any("挂接" in n for n in notes)


def test_lead_only_page_is_merged_into_the_next_page():
    blocks = [{"id": "s01.p01", "kind": "para", "text": "如下："}, _list_block("s01.p02", 2)]
    deck = _deck([
        _slide(1, "前言", ["s01"], ["s01.p01"], [_kp(["如下："], lead=True)]),
        _slide(2, "内容", ["s01"], ["s01.p02"], [_kp(["条目内容0", "条目内容1"])]),
    ])
    assert any("只有标题或前言" in e for e in _errs(deck, blocks))
    fixed, notes = autofix_deck(deck, blocks, "P01", page_cap=12)
    assert len(fixed["slides"]) == 1
    assert fixed["slides"][0]["elements"][0].get("lead_in") is True          # 前言与内容同页
    assert _errs(fixed, blocks) == []
    assert any("并入" in n for n in notes)


def test_trailing_lead_moves_to_the_next_page():
    blocks = [{"id": "s01.p01", "kind": "para", "text": "以下是要点："}, _list_block("s01.p02", 2)]
    deck = _deck([
        _slide(1, "甲", ["s01"], ["s01.p01", "s01.p02"],
               [_kp(["条目内容0"]), _kp(["以下是要点："], lead=True)]),
        _slide(2, "乙", ["s01"], [], [_kp(["条目内容1"])]),
    ])
    assert any("前言留在页尾" in e for e in _errs(deck, blocks))
    fixed, _ = autofix_deck(deck, blocks, "P01", page_cap=12)
    assert _errs(fixed, blocks) == []


def test_page_ids_are_renumbered_and_slides_are_refreshed():
    blocks = [_list_block("s01.p01", 2)]
    deck = _deck([_slide(7, "甲", ["s01"], ["s01.p01"], [_kp(["条目内容0"])]),
                  _slide(9, "乙", ["s01"], [], [])])
    fixed, _ = autofix_deck(deck, blocks, "P01", page_cap=12)
    assert [s["id"] for s in fixed["slides"]] == ["P01.S01"]
    assert fixed["slides"][0]["est_sec"] > 0


class FakeLLM:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = 0

    def ask(self, prompt, system=None):
        self.calls += 1
        return self.replies.pop(0) if self.replies else "not json"


def test_repair_resolves_mechanical_problems_without_the_model():
    blocks = [_list_block("s01.p01", 17)]
    deck = _deck([_slide(1, "甲", ["s01"], ["s01.p01"], [{"kind": "list", "items": [f"条目内容{i}" for i in range(17)], "emphasis": []}])],
                 cap=rp.REPAIR_CAPS[0])
    errors = _errs(deck, blocks, rp.REPAIR_CAPS[0])
    llm = FakeLLM()
    out = rp.repair_deck({"title": "P01"}, blocks, "P01", deck, errors, llm, max_attempts=5)
    assert llm.calls == 0
    assert out["attempts"] == "autofix"


def test_repeated_error_set_stops_calling_the_model():
    blocks = [{"id": "s01.p01", "kind": "list", "items": ["条目内容0"]}]
    deck = _deck([_slide(1, "", ["s01"], ["s01.p01"], [_kp(["条目内容0"])])])     # 缺少标题：确定性修复修不了
    errors = _errs(deck, blocks, rp.REPAIR_CAPS[0])
    assert errors
    llm = FakeLLM(['{"slides": []}'] * 30)            # 每次都给出同一个不合格结构
    with pytest.raises(PlanError) as ei:
        rp.repair_deck({"title": "P01"}, blocks, "P01", deck, errors, llm, max_attempts=30)
    assert llm.calls == 2                            # 第二次出现相同结果即停止，不再空耗 30 轮
    assert "剩余" in str(ei.value)
