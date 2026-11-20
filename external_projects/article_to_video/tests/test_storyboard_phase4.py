"""分镜改进·第四期（以测试文章为样本）：对比卡（B6）、回顾分组（B7）、回归样本（C1）、覆盖报告展示（C2）。

依据 next_doc/article_to_video_storyboard_case_harness_plan.md 第 11 节。
"""
from pathlib import Path

import pytest

from article_to_video.source.md_parse import parse_markdown
from article_to_video.validators.visual import validate_storyboard
from article_to_video.visual.director_llm import _page, parse_window, beat_context
from article_to_video.visual.normalize import normalize_scene
from article_to_video.visual.shots import SHOT_KINDS, sentence_id
from article_to_video.visual.states import RECAP_CAP, SCENE_TYPES, expected_state_count
from article_to_video.visual.structures import (analogy_words_in, article_text, contrast_of, coverage_rows,
                                                recap_of)
from helpers_shots import shot

FIXTURE = Path(__file__).parent / "fixtures" / "harness_article.md"


@pytest.fixture(scope="module")
def blocks():
    return parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]


def _sec(blocks, n):
    return [b for b in blocks if b["id"].startswith(f"s{n:02d}.")]


def _para(bid, text):
    return {"id": bid, "kind": "paragraph", "plain": text, "text": text}


def _quote(bid, *lines):
    return {"id": bid, "kind": "quote", "plain": "\n".join(lines), "text": "\n".join(lines)}


def _beat(bid, texts, refs):
    return {"id": bid, "role": "claim", "source_refs": refs,
            "sentences": [{"text": t, "quote": False} for t in texts]}


def _run(obj, beat, blocks):
    ctx = beat_context(beat, blocks)
    scenes, errs, _n = parse_window(obj, [beat], {beat["id"]: ctx}, prev=None, ill_used=0, allowance=10, shots=2)
    return scenes, errs


# ---------- B6：对比卡 ----------

def test_contrast_in_fixture_section_30_has_both_sides_verbatim(blocks):
    c = contrast_of(_sec(blocks, 30))
    assert c is not None
    assert c["left"] == ["模型越强，Harness 越厚。", "Harness 会被模型逐渐吃掉。"]
    assert c["right"] == ["只要 AI 仍然需要存在于一个世界中，Harness 就不可能消失。"]
    # 栏标题取原文的对照词，不替原文下结论
    assert (c["left_title"], c["right_title"]) == ("不是", "而是")


def test_contrast_in_fixture_section_26_is_a_real_contrast_too(blocks):
    c = contrast_of(_sec(blocks, 26))
    assert c is not None
    assert c["left"] == ["模型能不能回答问题？"]
    assert c["right"] == ["模型能不能成为一个持续存在、持续行动、持续成长的主体？"]


def test_contrast_needs_both_sides():
    blocks = [_para("x.p1", "我的答案不是："), _quote("x.p2", "甲甲甲")]
    assert contrast_of(blocks) is None           # 只有否定一侧，没有“而是”
    blocks = [_para("x.p1", "而是："), _quote("x.p2", "乙乙乙")]
    assert contrast_of(blocks) is None           # 只有肯定一侧


def test_contrast_rejects_multiline_or_overlong_quotes():
    blocks = [_para("x.p1", "不是："), _quote("x.p2", "甲", "乙"),
              _para("x.p3", "而是："), _quote("x.p4", "丙丙丙")]
    assert contrast_of(blocks) is None
    long_line = "很长" * 30
    blocks = [_para("x.p1", "不是："), _quote("x.p2", "甲甲甲"),
              _para("x.p3", "而是："), _quote("x.p4", long_line)]
    assert contrast_of(blocks) is None


def test_kv_not_equal_pairs_form_a_contrast():
    kv = {"id": "x.k1", "kind": "code", "shape": "text", "hint": "kv", "items": ["社会 ≠ 个人", "规则 ≠ 习惯"]}
    c = contrast_of([kv])
    assert c is not None and c["left"] == ["社会", "规则"] and c["right"] == ["个人", "习惯"]
    assert (c["left_title"], c["right_title"]) == ("", "")
    # “=”不是对比：原文中的 Model = Claude 之类不得被当作对照
    eq = {"id": "x.k2", "kind": "code", "shape": "text", "hint": "kv", "items": ["Model = Claude", "Memory = A"]}
    assert contrast_of([eq]) is None


def test_contrast_shot_is_single_state_with_source_sides(blocks):
    beat = _beat("s30.p04", ["我的答案不是模型越强。", "也不是被吃掉。", "而是要有世界。"],
                 ["s30.p04", "s30.p05", "s30.p06", "s30.p07", "s30.p08", "s30.p09"])
    obj = {"shots": [shot(sentence_id(beat["id"], 0), sentence_id(beat["id"], 2), "contrast")]}
    scenes, errs = _run(obj, beat, blocks)
    assert errs == [] and len(scenes) == 1
    sc = scenes[0]
    assert sc["type"] == "contrast" and sc["items"] == []
    assert sc["left"] == ["模型越强，Harness 越厚。", "Harness 会被模型逐渐吃掉。"]
    assert expected_state_count(sc) == 1         # 单状态：两侧一次显示
    v = validate_storyboard({"scenes": scenes}, {"beats": [beat]}, allowance=3, style_anchor="无文字",
                            images_enabled=False)
    assert v["errors"] == []


def test_contrast_card_is_not_offered_when_the_beat_has_no_contrast():
    beat = _beat("p.b1", ["甲。", "乙。"], ["s01.p01"])
    ctx = beat_context(beat, [_para("s01.p01", "普通段落。")])
    assert "contrast" not in ctx["allowed"] and ctx["contrast"] is None


def test_normalize_keeps_contrast_sides_and_drops_items():
    sents = [{"text": "甲"}, {"text": "乙"}]
    raw = {"id": "s30.p04", "beat": "s30.p04", "type": "contrast", "kind": "contrast",
           "sentences": ["s30.p04.s01", "s30.p04.s02"], "left": ["模型越强"], "right": ["只要存在"],
           "left_title": "不是", "right_title": "而是", "items": ["不应保留"]}
    sc = normalize_scene(raw, sents)
    assert sc["type"] == "contrast" and sc["items"] == []
    assert sc["left"] == ["模型越强"] and sc["right"] == ["只要存在"]
    assert (sc["left_title"], sc["right_title"]) == ("不是", "而是")


# ---------- B7：回顾分组 ----------

def test_recap_in_fixture_section_30_is_six_lines_in_order(blocks):
    r = recap_of(_sec(blocks, 30))
    assert r is not None and r["id"] == "s30.p37"
    assert r["lines"] == ["模型赋予 AI 灵魂的可能性。", "Harness 赋予 AI 成为一个“存在”的可能性。",
                          "环境赋予它经历。", "记忆赋予它连续性。", "学习赋予它成长。", "而时间，最终决定它会成为谁。"]


def test_recap_is_not_triggered_by_short_quotes(blocks):
    assert recap_of([_quote("x.p1", "只有一行")]) is None
    assert recap_of([_quote("x.p1", "甲", "乙", "丙")]) is None          # 3 行：普通金句，不是回顾
    assert recap_of([_quote("x.p1", "甲", "乙", "丙", "丁" * 41)]) is None


def test_six_recap_lines_split_into_two_cards_of_three(blocks):
    # 页码计算：每张卡最多 RECAP_CAP 条，第 4–6 条为第二张
    assert RECAP_CAP == 3
    assert _page({"part": [1, 3]}, 6, RECAP_CAP, "x", []) == (0, 3)
    assert _page({"part": [4, 6]}, 6, RECAP_CAP, "x", []) == (3, 6)
    assert _page({}, 6, RECAP_CAP, "x", []) == (0, 3)                    # 缺省只取前 3 条


def test_recap_shots_cover_all_six_lines_in_order(blocks):
    beat = _beat("s30.p37", ["模型赋予 AI 灵魂。", "Harness 赋予存在。", "环境赋予经历。"], ["s30.p37"])
    obj = {"shots": [shot(sentence_id(beat["id"], 0), sentence_id(beat["id"], 1), "recap", part=[1, 3]),
                     shot(sentence_id(beat["id"], 2), sentence_id(beat["id"], 2), "recap", part=[4, 6])]}
    scenes, errs = _run(obj, beat, blocks)
    assert errs == [], errs
    assert [s["type"] for s in scenes] == ["recap", "recap"]
    assert [len(s["items"]) for s in scenes] == [3, 3]
    assert [x for s in scenes for x in s["items"]] == recap_of(_sec(blocks, 30))["lines"]


def test_recap_missing_lines_are_reported_not_silently_dropped(blocks):
    beat = _beat("s30.p37", ["模型赋予 AI 灵魂。", "Harness 赋予存在。", "环境赋予经历。"], ["s30.p37"])
    obj = {"shots": [shot(sentence_id(beat["id"], 0), sentence_id(beat["id"], 2), "recap", part=[1, 3])]}
    scenes, errs = _run(obj, beat, blocks)
    assert scenes == []
    assert any("未展示第 4、5、6 项" in e for e in errs)


def test_recap_card_renders_inside_canvas():
    from article_to_video.render.fonts import FontError, find_cjk_font
    try:
        find_cjk_font({})
    except FontError:
        pytest.skip("本机没有中文字体")
    from article_to_video.render.renderers import render_scene_frames
    from article_to_video.render.themes import DEFAULT_THEME, canvas_size
    for orientation in ("landscape", "portrait"):
        canvas = canvas_size(orientation, "1080p")
        sc = {"type": "recap", "kind": "recap", "items": ["模型赋予 AI 灵魂的可能性。", "环境赋予它经历。",
                                                          "而时间，最终决定它会成为谁。"], "emphasis": []}
        frames = render_scene_frames(sc, canvas, orientation, DEFAULT_THEME)
        assert len(frames) == 1 and frames[0].getbbox() is not None
        x0, y0, x1, y1 = frames[0].getchannel("A").getbbox()
        w, h = frames[0].size
        assert x0 >= 0 and y0 >= 0 and x1 <= w and y1 <= h


def test_contrast_card_renders_inside_canvas_in_both_orientations():
    from article_to_video.render.fonts import FontError, find_cjk_font
    try:
        find_cjk_font({})
    except FontError:
        pytest.skip("本机没有中文字体")
    from article_to_video.render.renderers import render_scene_frames
    from article_to_video.render.themes import DEFAULT_THEME, canvas_size
    for orientation in ("landscape", "portrait"):
        canvas = canvas_size(orientation, "1080p")
        sc = {"type": "contrast", "kind": "contrast", "items": [], "emphasis": [],
              "left": ["模型越强，Harness 越厚。", "Harness 会被模型逐渐吃掉。"],
              "right": ["只要 AI 仍然需要存在于一个世界中，Harness 就不可能消失。"],
              "left_title": "不是", "right_title": "而是"}
        frames = render_scene_frames(sc, canvas, orientation, DEFAULT_THEME)
        assert len(frames) == 1 and frames[0].getbbox() is not None
        x0, y0, x1, y1 = frames[0].getchannel("A").getbbox()
        w, h = frames[0].size
        assert x0 >= 0 and y0 >= 0 and x1 <= w and y1 <= h


# ---------- C2：覆盖报告展示 ----------

def test_coverage_rows_show_shots_and_unshown_counts():
    cov = {"blocks": [
        {"beat": "s30.p37", "family": "recap", "block": "s30.p37", "total": 6, "shots": ["P04.s1", "P04.s2"], "shown": 6},
        {"beat": "s30.p04", "family": "contrast", "block": "s30.p04", "total": 1, "shots": [], "shown": 0},
        {"beat": "s07.p20", "family": "concept", "block": "s07.p20", "total": 1, "shots": ["P01.s3"], "shown": 1,
         "term": "Identity"},
    ], "uncovered": [], "notes": []}
    rows = coverage_rows(cov)
    assert rows[0] == {"段落": "s30.p37", "结构": "回顾", "承载镜头": "P04.s1、P04.s2", "已展示": "6 / 6"}
    assert rows[1]["承载镜头"] == "未承载" and rows[1]["结构"] == "对比"
    assert rows[2]["结构"] == "概念定义「Identity」"
    assert coverage_rows(None) == [] and coverage_rows({}) == []


def test_ui_offers_the_new_card_types_with_labels():
    from ui.storyboard_page import _TYPE_LABELS
    for t in ("contrast", "recap"):
        assert t in SCENE_TYPES and t in SHOT_KINDS
        assert _TYPE_LABELS.get(t)


# ---------- C1：回归样本（以测试文章为样本的断言，覆盖第一至四期的关键结论） ----------

def test_regression_table_s21_pages_cover_all_sixteen_rows(blocks):
    t = next(b for b in _sec(blocks, 21) if b["kind"] == "table")
    total = len(t["rows"])
    assert total == 16
    covered = set()
    for part in ([1, 6], [7, 12], [13, 16]):
        from article_to_video.visual.director import MAX_TABLE_ROWS
        raw = {"part": part}
        a0, a1 = _page(raw, total, MAX_TABLE_ROWS, "s21", [])
        covered.update(range(a0, a1))
    assert covered == set(range(total))


def test_regression_s25_upward_arrows_are_a_stack_not_a_flow(blocks):
    chains = [b for b in _sec(blocks, 25) if b["kind"] == "code" and b.get("shape") == "chain"]
    assert chains and chains[0]["direction"] == "up" and len(chains[0]["nodes"]) == 4


def test_regression_s07_formula_block_is_detected(blocks):
    hints = [b for b in _sec(blocks, 7) if b.get("hint") == "formula"]
    assert hints and "≈" in hints[0]["hint_text"]


def test_regression_s28_architecture_loop_is_parsed(blocks):
    loops = [b for b in _sec(blocks, 28) if b["kind"] == "code" and b.get("loop")]
    assert loops and len(loops[0]["nodes"]) >= 7


def test_regression_s30_contrast_and_six_line_recap_both_present(blocks):
    assert contrast_of(_sec(blocks, 30)) is not None
    assert len(recap_of(_sec(blocks, 30))["lines"]) == 6


def test_regression_illustration_analogies_come_from_the_article(blocks):
    words = analogy_words_in(article_text(blocks))
    for w in ("人", "大脑", "神经系统", "电网", "交通系统"):
        assert w in words
