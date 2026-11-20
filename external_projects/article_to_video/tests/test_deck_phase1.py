"""新生成方案·第一阶段：幻灯片规划（数据、规则、校验、存储、方案设置）。"""
import json
from pathlib import Path

import pytest

from article_to_video import app_settings as S
from article_to_video.deck.plan import MAX_SLIDE_ITEMS, elements_of, plan_slides
from article_to_video.deck.store import confirm_deck, deck_status, load_deck, plan_deck
from article_to_video.deck.validate import validate_deck
from article_to_video.planning.errors import PlanError
from article_to_video.planning.runner import load_spine
from article_to_video.source.md_parse import parse_markdown
from article_to_video.project_store import write_json_atomic
from helpers_p2 import make_project

FIXTURE = Path(__file__).parent / "fixtures" / "harness_article.md"


@pytest.fixture(scope="module")
def blocks():
    return parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]


def _write_spine(pdir, secs, confirmed=True):
    spine = {"title": "测试", "confirmed": confirmed,
             "parts": [{"id": "P01", "title": "测试章", "role": "claim", "one_line": "", "section_ids": secs,
                        "weight": 1.0, "budget_minutes": 6.0}]}
    write_json_atomic(Path(pdir) / "plan" / "spine.json", spine)


def _part(secs, title="测试章"):
    return {"id": "P01", "title": title, "section_ids": secs, "budget_minutes": 6.0}


def _sec_blocks(blocks, secs):
    return [b for b in blocks if b.get("section") in secs]


# ---------- 规划 ----------

def test_fixture_chapter_plans_slides_with_full_coverage(blocks):
    secs = ["s01", "s02", "s03"]
    part = _part(secs)
    bl = _sec_blocks(blocks, secs)
    deck = plan_slides(part, bl, "P01")
    assert deck["slides"]
    assert [s["id"] for s in deck["slides"]] == [f"P01.S{i:02d}" for i in range(1, len(deck["slides"]) + 1)]
    assert all(s["title"] and s["elements"] for s in deck["slides"])
    v = validate_deck(deck, bl)
    assert v["errors"] == []


def test_list_block_becomes_list_element_and_chain_becomes_diagram(blocks):
    lst = next(b for b in blocks if b.get("kind") == "list")
    chain = next(b for b in blocks if b.get("kind") == "code" and b.get("shape") == "chain")
    assert elements_of(lst)[0]["kind"] == "list"
    assert elements_of(chain)[0]["kind"] == "diagram" and elements_of(chain)[0]["items"]


def test_table_and_formula_elements():
    table = {"id": "t", "kind": "table", "header": ["a", "b"], "rows": [["1", "2"]]}
    assert elements_of(table)[0]["kind"] == "table"
    formula = {"id": "f", "kind": "code", "shape": "text", "hint": "formula", "items": ["智能 ≈ 压缩"], "content": ""}
    el = elements_of(formula)[0]
    assert el["kind"] == "formula" and el["text"] == "智能 ≈ 压缩"


def test_single_line_quote_is_a_quote_and_paragraph_splits_into_sentences():
    q = {"id": "q", "kind": "quote", "plain": "“只有一句。”"}
    assert elements_of(q)[0]["kind"] == "quote"
    p = {"id": "p", "kind": "paragraph", "plain": "第一句。第二句！第三句？"}
    el = elements_of(p)[0]
    assert el["kind"] == "keypoint" and el["items"] == ["第一句。", "第二句！", "第三句？"]


def test_long_lists_continue_on_new_pages_within_the_item_limit():
    items = [f"条目{i:02d}内容" for i in range(25)]
    blocks = [{"id": "h", "kind": "heading", "level": 2, "text": "长列表", "section": "s01"},
              {"id": "l", "kind": "list", "items": items, "section": "s01"}]
    deck = plan_slides(_part(["s01"]), blocks, "P01")
    assert len(deck["slides"]) >= 3
    assert all(sum(len(e.get("items", [])) for e in s["elements"]) <= MAX_SLIDE_ITEMS for s in deck["slides"])
    assert any("（续）" in s["title"] for s in deck["slides"])
    got = [x for s in deck["slides"] for e in s["elements"] for x in e.get("items", [])]
    assert got == items                                    # 全部保留，顺序不变


def test_estimated_speech_time_is_per_slide_and_summed(blocks):
    deck = plan_slides(_part(["s01"]), _sec_blocks(blocks, ["s01"]), "P01")
    assert all(s["est_sec"] > 0 for s in deck["slides"])
    assert deck["total_est_sec"] == pytest.approx(sum(s["est_sec"] for s in deck["slides"]), abs=0.2)


# ---------- 校验 ----------

def test_validator_rejects_text_that_is_not_in_the_source(blocks):
    bl = _sec_blocks(blocks, ["s01"])
    deck = plan_slides(_part(["s01"]), bl, "P01")
    deck["slides"][0]["elements"][0]["items"] = ["原文里根本没有的一句话"]
    v = validate_deck(deck, bl)
    assert any("不在原文中" in e for e in v["errors"])


def test_validator_rejects_uncovered_content():
    bl = [{"id": "h1", "kind": "heading", "level": 2, "text": "甲", "section": "s01"},
          {"id": "a.p1", "kind": "paragraph", "plain": "第一段内容。", "section": "s01"},
          {"id": "h2", "kind": "heading", "level": 2, "text": "乙", "section": "s02"},
          {"id": "b.p1", "kind": "paragraph", "plain": "第二段内容。", "section": "s02"}]
    deck = plan_slides(_part(["s01"]), [b for b in bl if b["section"] == "s01"], "P01")   # 漏掉 s02 的内容
    v = validate_deck(deck, bl)
    assert any("未被任何幻灯片承载" in e and "b.p1" in e for e in v["errors"])


def test_validator_rejects_overfull_slide(blocks):
    bl = _sec_blocks(blocks, ["s01"])
    deck = plan_slides(_part(["s01"]), bl, "P01")
    big = deck["slides"][0]
    big["elements"] = [{"kind": "keypoint", "items": [big["title"]] * 1, "emphasis": []}] * 1
    big["elements"][0]["items"] = [bl[0]["plain"] if "plain" in bl[0] else "x"] * (MAX_SLIDE_ITEMS + 1)
    v = validate_deck(deck, bl)
    assert any("超过一页上限" in e or "超过" in e for e in v["errors"])


# ---------- 存储与状态 ----------

@pytest.fixture
def project(tmp_path, monkeypatch, blocks):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": blocks})
    _write_spine(pdir, ["s01", "s02"])
    return pdir


def test_plan_store_confirm_and_staleness(project, blocks):
    deck = plan_deck(project, "P01")
    assert deck["input_sha"] and deck["confirmed"] is False
    st = deck_status(project, "P01")
    assert st["exists"] and not st["stale"] and st["slides"] == len(deck["slides"])
    assert plan_deck(project, "P01")["input_sha"] == deck["input_sha"]        # 未变则复用
    confirm_deck(project, "P01")
    assert deck_status(project, "P01")["confirmed"] is True
    # 原文变化 → 规划过期
    changed = [dict(b) for b in blocks]
    changed[0] = dict(changed[0], plain="另一段原文")
    write_json_atomic(project / "source" / "blocks.json", {"blocks": changed})
    assert deck_status(project, "P01")["stale"] is True


def test_planning_requires_a_confirmed_outline(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": []})
    _write_spine(pdir, ["s01"], confirmed=False)
    with pytest.raises(PlanError):
        plan_deck(pdir, "P01")


# ---------- 生成方案设置 ----------

def test_generation_mode_defaults_to_classic_and_rejects_unknown_values():
    assert S._norm_pipeline({})[0]["mode"] == "classic"
    norm, warnings = S._norm_pipeline({"mode": "weird"})
    assert norm["mode"] == "classic" and warnings


def test_mode_is_remembered_per_project(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path / "a")
    root = tmp_path / "global"
    S.save_budget(pdir, gear="G0", max_images=0, max_minutes=0.0, mode="deck", root=root)
    assert S.effective_group(pdir, "pipeline", root)["mode"] == "deck"
    other = make_project(tmp_path / "b")
    assert S.effective_group(other, "pipeline", root)["mode"] == "classic"


def test_pages_are_split_when_the_speech_would_exceed_the_page_time():
    from article_to_video.deck.plan import MAX_PAGE_CHARS, MAX_PAGE_SEC
    sents = [f"第{i:02d}句内容，用于检验单页时长是否被控制住。" for i in range(40)]
    blocks = [{"id": "h", "kind": "heading", "level": 2, "text": "长段", "section": "s01"},
              {"id": "p", "kind": "paragraph", "plain": "".join(sents), "section": "s01"}]
    deck = plan_slides(_part(["s01"]), blocks, "P01")
    assert len(deck["slides"]) >= 2
    assert all(s["est_sec"] <= MAX_PAGE_SEC for s in deck["slides"])
    assert MAX_PAGE_CHARS > 0
    got = [x for s in deck["slides"] for e in s["elements"] for x in e.get("items", [])]
    assert got == sents                                     # 句子不丢失、顺序不变
