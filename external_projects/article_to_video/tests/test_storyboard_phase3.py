"""分镜改进·第三期（以测试文章为样本）：递进卡（B2）、公式符号卡（B3）、插图主体出自原文（B4）、
定义卡（B5）、结构块覆盖报告与严格覆盖（B1）。依据 next_doc/article_to_video_storyboard_case_harness_plan.md 第 10 节。
"""
import json
from pathlib import Path

import pytest

from article_to_video.planning.errors import PlanError
from article_to_video.source.md_parse import parse_markdown
from article_to_video.validators.visual import validate_storyboard
from article_to_video.visual.config import normalize_visual
from article_to_video.visual.director_llm import beat_context, direct_part_llm, parse_window
from article_to_video.visual.normalize import normalize_scene
from article_to_video.visual.shots import sentence_id
from article_to_video.visual.states import ALL_AT_ONCE_TYPES, expected_state_count
from article_to_video.visual.structures import (analogy_words_in, article_text, clip_definition,
                                                concepts_of, coverage_report, stage_groups,
                                                subject_grounded, symbol_items)
from article_to_video.validators.fidelity import norm_key
from helpers_shots import ShotLLM, default_plan, shot, shots_json

FIXTURE = Path(__file__).parent / "fixtures" / "harness_article.md"


@pytest.fixture(scope="module")
def blocks():
    return parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]


def _sec(blocks, sec):
    return [b for b in blocks if b["section"] == sec]


def _beat(bid, sentences, refs):
    return {"id": bid, "role": "claim", "source_refs": refs,
            "sentences": [{"text": t, "quote": False} for t in sentences]}


def _window(obj, beat, blocks, grounding=None):
    ctx = beat_context(beat, blocks)
    ctx["grounding"] = grounding
    return parse_window(obj, [beat], {beat["id"]: ctx}, prev=None, ill_used=0, allowance=10, shots=2)


def _sid(bid, i):
    return sentence_id(bid, i)


# ---------- A6 修订：定义句的选取 ----------

def test_concept_definitions_skip_lead_ins_and_negated_sentences(blocks):
    defs = {b["text"]: b["definition"] for b in blocks if b.get("concept")}
    assert defs["Experience Loop"].startswith("而这个循环")
    assert defs["Life Data"] == "一个 AI 的人生。"             # 跳过“不是普通的：”与“Chat History”
    assert defs["AgentOS"].startswith("管理人工智能主体")        # 跳过否定句“管理 Agent 的软件”
    assert defs["Artificial Life"] == "人工生命。"
    assert defs["Intelligence Infrastructure"].startswith("如何把")
    assert defs["Harness"] == "" and defs["Identity Infrastructure"] == ""   # 本节没有定义句：不编造


def test_concepts_without_definition_make_no_card(blocks):
    assert concepts_of(_sec(blocks, "s02")) == []
    got = concepts_of(_sec(blocks, "s20"))
    assert [c["term"] for c in got] == ["AgentOS"]


def test_clip_definition_cuts_at_clause_boundary():
    long = "这是一个很长的定义，" * 8
    out = clip_definition(long, 30)
    assert len(out) <= 30 and not out.endswith("，")
    assert clip_definition("短句。") == "短句。"


# ---------- B2 递进 ----------

def test_stage_blocks_merge_in_source_order(blocks):
    stages, why = stage_groups(_sec(blocks, "s15"))
    assert why == ""
    assert [s["title"] for s in stages] == ["早期 Harness", "中期 Harness", "高级 Harness"]
    assert stages[0]["labels"] == ["Prompt", "Tool", "Agent Loop"]
    assert len(stages[2]["labels"]) == 7                       # 原文标签不丢


def test_textual_progression_becomes_three_stages(blocks):
    stages, _ = stage_groups(_sec(blocks, "s25"))
    assert [s["labels"] for s in stages] == [["Compute"], ["Model"], ["Application"]]
    assert [s["title"] for s in stages] == ["过去大家最关注", "然后", "接下来"]


def test_progression_needs_three_pairs_and_no_negation_noise(blocks):
    two = [b for b in _sec(blocks, "s25") if b["id"] not in ("s25.p08", "s25.p09")]
    assert stage_groups(two)[0] == []
    assert stage_groups(_sec(blocks, "s07"))[0] == []           # 普通段落不触发


def test_too_many_stages_or_labels_explains_instead_of_dropping():
    big = [{"kind": "code", "id": f"c{i}", "hint": "stages", "hint_text": f"阶段{i}", "items": ["甲"]}
           for i in range(5)]
    stages, why = stage_groups(big)
    assert stages == [] and "超过每镜上限" in why
    fat = [{"kind": "code", "id": "a", "hint": "stages", "hint_text": "早期", "items": ["x"] * 9},
           {"kind": "code", "id": "b", "hint": "stages", "hint_text": "中期", "items": ["y"]}]
    assert "标签超过" in stage_groups(fat)[1]


def test_model_builds_stages_shot_from_source(blocks):
    beat = _beat("P15.b01", ["早期只有提示词和工具。", "中期加入记忆。", "高级阶段有身份。"],
                 ["s15.p03", "s15.p05", "s15.p07"])
    ctx = beat_context(beat, blocks)
    assert "stages" in ctx["allowed"] and any("原文递进" in n for n in ctx["notes"])
    obj = {"shots": [shot(_sid("P15.b01", 0), _sid("P15.b01", 2), "stages")]}
    scenes, errs, _ = _window(obj, beat, blocks)
    assert errs == []
    sc = scenes[0]
    assert sc["type"] == "stages" and sc["kind"] == "stages"
    assert sc["items"] == ["早期 Harness", "中期 Harness", "高级 Harness"]
    assert sc["stages"][1]["labels"] == ["Memory", "State", "Task", "Sub-Agent", "Environment"]
    assert expected_state_count(sc) == 1 and "stages" in ALL_AT_ONCE_TYPES


def test_stages_not_offered_without_source_progression(blocks):
    beat = _beat("P07.b01", ["一句话。"], ["s07.p20"])
    ctx = beat_context(beat, blocks)
    assert "stages" not in ctx["allowed"]
    scenes, errs, _ = _window({"shots": [shot(_sid("P07.b01", 0), _sid("P07.b01", 0), "stages",
                                              items=["一句话"])]}, beat, blocks)
    assert errs == [] and scenes[0]["type"] == "keypoint" and scenes[0]["fallback_from"] == "stages"


# ---------- B5 定义卡 ----------

def test_definition_card_uses_source_term_and_sentence(blocks):
    beat = _beat("P11.b01", ["这个循环决定了一个智能体会成为什么。"], ["s11"])
    ctx = beat_context(beat, blocks)
    assert "definition" in ctx["allowed"]
    scenes, errs, _ = _window({"shots": [shot(_sid("P11.b01", 0), _sid("P11.b01", 0), "definition")]}, beat, blocks)
    assert errs == []
    sc = scenes[0]
    assert sc["type"] == "definition" and sc["term"] == "Experience Loop"
    assert sc["items"][0].startswith("而这个循环") and expected_state_count(sc) == 1


def test_definition_not_offered_when_no_definition_sentence(blocks):
    beat = _beat("P02.b01", ["开场。"], ["s02"])
    assert "definition" not in beat_context(beat, blocks)["allowed"]


def test_definition_with_two_concepts_requires_term(blocks):
    beat = _beat("P99.b01", ["甲。", "乙。"], ["s11", "s14"])
    ctx = beat_context(beat, blocks)
    assert len(ctx["concepts"]) == 2
    _, errs, _ = _window({"shots": [shot(_sid("P99.b01", 0), _sid("P99.b01", 0), "definition"),
                                    shot(_sid("P99.b01", 1), _sid("P99.b01", 1), "keypoint", items=["乙"])]},
                         beat, blocks)
    assert any("term" in e for e in errs)
    ok, errs2, _ = _window({"shots": [shot(_sid("P99.b01", 0), _sid("P99.b01", 0), "definition", term="life data"),
                                      shot(_sid("P99.b01", 1), _sid("P99.b01", 1), "keypoint", items=["乙"])]},
                           beat, blocks)
    assert errs2 == [] and ok[0]["term"] == "Life Data"


# ---------- B3 公式符号卡 ----------

FORMULA = "Identity ≈ Model + Memory + Experience + Continuity"
SENTS = ["模型提供潜能。", "经历塑造个体。", "连续性创造我。"]


def test_symbol_items_take_meaning_from_sentences():
    items, errs = symbol_items([{"symbol": "Model", "meaning": "潜能"},
                                {"symbol": "Experience", "meaning": "塑造个体"}], FORMULA, SENTS, "x")
    assert errs == [] and items == ["Model — 潜能", "Experience — 塑造个体"]


@pytest.mark.parametrize("raw,needle", [
    ([{"symbol": "Goal", "meaning": "潜能"}], "不在原文公式中"),
    ([{"symbol": "Model", "meaning": "智慧"}], "不是本镜头句子中的原文"),
    ([{"symbol": "Model", "meaning": "模型提供潜能。经历塑造个体。"}], "超过"),
    ([{"symbol": "Model"}], "需要 symbol 与 meaning"),
    ([{"symbol": "Model", "meaning": "潜能"}] * 4, "超过每镜上限"),
])
def test_symbol_items_reject_invented_content(raw, needle):
    _, errs = symbol_items(raw, FORMULA, SENTS, "x")
    assert any(needle in e for e in errs)


def test_formula_shot_uses_symbols_and_keeps_formula_visible(blocks):
    beat = _beat("P07.b01", SENTS, ["s07.p20"])
    obj = {"shots": [shot(_sid("P07.b01", 0), _sid("P07.b01", 2), "formula",
                          formula_symbols=[{"symbol": "Model", "meaning": "潜能"},
                                           {"symbol": "Continuity", "meaning": "创造我"}])]}
    scenes, errs, _ = _window(obj, beat, blocks)
    assert errs == []
    sc = scenes[0]
    assert sc["type"] == "formula" and sc["formula"] == FORMULA
    assert sc["items"] == ["Model — 潜能", "Continuity — 创造我"]
    assert expected_state_count(sc) == 3                      # 公式本体 + 两条符号逐条出现


def test_formula_shot_with_invented_symbol_meaning_is_redone(blocks):
    beat = _beat("P07.b01", SENTS, ["s07.p20"])
    obj = {"shots": [shot(_sid("P07.b01", 0), _sid("P07.b01", 2), "formula",
                          formula_symbols=[{"symbol": "Model", "meaning": "神经网络"}])]}
    _, errs, _ = _window(obj, beat, blocks)
    assert errs and "原文" in errs[0]


def test_formula_explanation_still_works_without_symbols(blocks):
    beat = _beat("P07.b01", SENTS, ["s07.p20"])
    obj = {"shots": [shot(_sid("P07.b01", 0), _sid("P07.b01", 2), "formula",
                          formula_explanation=["模型提供潜能"])]}
    scenes, errs, _ = _window(obj, beat, blocks)
    assert errs == [] and scenes[0]["items"] == ["模型提供潜能"]


def test_all_four_article_formulas_are_available_to_the_model(blocks):
    for bid in ("s07.p20", "s29.p08", "s12.p12"):
        b = next(x for x in blocks if x["id"] == bid)
        beat = _beat("P01.b01", ["句子。"], [bid])
        assert "formula" in beat_context(beat, blocks)["allowed"], (bid, b.get("hint"))


# ---------- B4 插图主体出自原文 ----------

def test_article_has_concrete_analogies(blocks):
    words = analogy_words_in(article_text(blocks))
    assert {"人", "大脑", "神经系统", "操作系统"} <= set(words)


def test_subject_grounding_rules(blocks):
    text = article_text(blocks)
    words = analogy_words_in(text)
    norm = norm_key(text)
    assert subject_grounded("一个人站在大脑前", norm, words)       # 含原文的具象类比词
    assert subject_grounded("神经系统", norm, words)
    assert not subject_grounded("一只发光的机器鸟", norm, words)    # 原文没有
    assert not subject_grounded("", norm, words)


def _ill_beat():
    return _beat("P01.b01", ["把智能体比作一个人。"], ["s01"])


def _ill_shot(subject):
    return {"shots": [shot(_sid("P01.b01", 0), _sid("P01.b01", 0), "illustration", items=["人"],
                           image_subject=subject)]}


def test_grounding_is_off_by_default(blocks):
    scenes, errs, _ = _window(_ill_shot("一只发光的机器鸟"), _ill_beat(), blocks)
    assert errs == [] and scenes[0]["image"]["subject"] == "一只发光的机器鸟"


def test_grounding_rejects_subject_not_in_article(blocks):
    text = article_text(blocks)
    g = {"article_norm": norm_key(text), "words": analogy_words_in(text)}
    _, errs, _ = _window(_ill_shot("一只发光的机器鸟"), _ill_beat(), blocks, grounding=g)
    assert errs and "未出现在原文" in errs[0]
    ok, errs2, _ = _window(_ill_shot("一个人站在大脑前"), _ill_beat(), blocks, grounding=g)
    assert errs2 == [] and ok[0]["type"] == "illustration"


def test_grounding_hint_reaches_prompt_and_cache_key(blocks, tmp_path):
    script = {"part": "P01", "title": "t", "beats": [_ill_beat()]}
    llm_off, llm_on = ShotLLM(), ShotLLM()
    kw = dict(part={"id": "P01", "title": "t", "budget_minutes": 2.0}, chapter={"number": "第 1 章", "title": "t", "one_line": ""},
              allowance=5, shots_per_image=2, images_enabled=True, style_anchor="无文字", cache_dir=tmp_path)
    direct_part_llm(script, blocks, llm=llm_off, **kw)
    direct_part_llm(script, blocks, llm=llm_on, subject_from_source=True, **kw)
    assert not any("image_subject 必须出自原文" in p for p in llm_off.prompts)
    assert any("image_subject 必须出自原文" in p and "大脑" in p for p in llm_on.prompts)
    assert llm_on.win_calls                                     # 缓存键含主体词表：开关变化不会误用旧缓存


# ---------- B1 覆盖报告与严格覆盖 ----------

def _sb_script(bid, sentences, refs):
    return {"part": "P07", "title": "t", "beats": [_beat(bid, sentences, refs)]}


PART = {"id": "P07", "title": "t", "budget_minutes": 2.0}
CHAPTER = {"number": "第 7 章", "title": "t", "one_line": ""}


def _run(script, blocks, win, **kw):
    llm = ShotLLM(plan_fn=default_plan, win_fn=lambda p, w, n: shots_json(win(p)))
    return direct_part_llm(script, blocks, part=PART, chapter=CHAPTER, allowance=5, shots_per_image=2, llm=llm,
                           images_enabled=True, style_anchor="无文字", **kw)


def test_coverage_report_lists_carriers_and_gaps(blocks):
    script = _sb_script("P07.b01", SENTS, ["s07.p20"])
    carried = _run(script, blocks, lambda p: [shot(_sid("P07.b01", 0), _sid("P07.b01", 2), "formula")])
    cov = carried["coverage"]
    assert cov["uncovered"] == [] and cov["blocks"][0]["family"] == "math"
    assert cov["blocks"][0]["shots"] == [carried["scenes"][0]["id"]]
    missed = _run(script, blocks, lambda p: [shot(_sid("P07.b01", 0), _sid("P07.b01", 2), "keypoint", items=["模型提供潜能"])])
    assert any("公式未被任何镜头承载" in u for u in missed["coverage"]["uncovered"])
    assert any("公式未被任何镜头承载" in w for w in missed["warnings"])      # 默认只提示，不失败


def test_strict_coverage_fails_the_chapter(blocks):
    script = _sb_script("P07.b01", SENTS, ["s07.p20"])
    with pytest.raises(PlanError) as e:
        _run(script, blocks, lambda p: [shot(_sid("P07.b01", 0), _sid("P07.b01", 2), "keypoint", items=["模型提供潜能"])],
             strict_coverage=True)
    assert "严格覆盖" in str(e.value)
    ok = _run(script, blocks, lambda p: [shot(_sid("P07.b01", 0), _sid("P07.b01", 2), "formula")], strict_coverage=True)
    assert ok["coverage"]["uncovered"] == []


def test_coverage_counts_paged_structures_and_concepts(blocks):
    ctxs = {"P11.b01": beat_context(_beat("P11.b01", ["甲。"], ["s11"]), blocks)}
    script = {"beats": [_beat("P11.b01", ["甲。"], ["s11"])]}
    cov = coverage_report([], script, ctxs)
    assert cov["blocks"][0]["family"] == "concept" and "概念定义「Experience Loop」未被任何镜头承载" in cov["uncovered"][0]
    done = coverage_report([{"id": "x", "beat": "P11.b01", "type": "definition", "items": ["d"]}], script, ctxs)
    assert done["uncovered"] == []


def test_oversize_stages_are_reported_as_note_not_uncovered():
    blocks = [{"id": f"c{i}", "section": "s1", "kind": "code", "shape": "text", "hint": "stages",
               "hint_text": f"阶段{i}", "items": ["甲"]} for i in range(5)]
    beat = _beat("P01.b01", ["一句。"], ["s1"])
    ctxs = {"P01.b01": beat_context(beat, blocks)}
    cov = coverage_report([], {"beats": [beat]}, ctxs)
    assert cov["uncovered"] == [] and any("超过每镜上限" in n for n in cov["notes"])


# ---------- 校验、规范化、渲染、设置 ----------

def _scene(**kw):
    d = {"id": "P01.shot01", "beat": "P01.b01", "type": "keypoint", "kind": "keypoint",
         "sentences": ["P01.b01.s01", "P01.b01.s01"], "refs": [], "items": ["甲"], "emphasis": [], "header": [],
         "rows": [], "loop": False, "formula": "", "image": None, "reveal": [{"step": 0, "at_sentence": 0}]}
    d.update(kw)
    return d


def _verdict(scene):
    script = {"part": "P01", "title": "t", "beats": [_beat("P01.b01", ["一句。"], [])]}
    sb = {"scenes": [scene], "source_structure": []}
    return validate_storyboard(sb, script, allowance=3, style_anchor="无文字", images_enabled=False)


def test_validator_checks_new_scene_types():
    ok = _scene(type="stages", kind="stages", items=["甲", "乙"],
                stages=[{"title": "甲", "labels": ["a"]}, {"title": "乙", "labels": ["b"]}])
    assert _verdict(ok)["errors"] == []
    one = _scene(type="stages", kind="stages", items=["甲"], stages=[{"title": "甲", "labels": ["a"]}])
    assert any("至少需要 2 个阶段" in e for e in _verdict(one)["errors"])
    assert _verdict(_scene(type="definition", kind="definition", term="Life Data", items=["一个 AI 的人生。"]))["errors"] == []
    bad = _verdict(_scene(type="definition", kind="definition", term="", items=[]))
    assert any("缺少术语" in e for e in bad["errors"]) and any("缺少定义句" in e for e in bad["errors"])


def test_normalize_keeps_stage_and_definition_fields():
    sents = [{"text": "一句。", "quote": False}]
    st = normalize_scene(_scene(type="stages", kind="stages", items=["旧"],
                                stages=[{"title": "早期", "labels": ["a", "b"]}, {"title": "中期", "labels": []}]), sents)
    assert st["items"] == ["早期", "中期"] and st["stages"][0]["labels"] == ["a", "b"] and st["kind"] == "stages"
    df = normalize_scene(_scene(type="definition", kind="definition", term=" AgentOS ", items=["甲", "乙"]), sents)
    assert df["term"] == "AgentOS" and df["items"] == ["甲"]


@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_new_cards_render_inside_canvas(orientation):
    from article_to_video.render.fonts import FontError, find_cjk_font
    try:
        find_cjk_font({})
    except FontError:
        pytest.skip("本机没有中文字体")
    from article_to_video.render.renderers import render_scene_frames
    from article_to_video.render.themes import DEFAULT_THEME, canvas_size
    canvas = canvas_size(orientation, "1080p")
    stages = _scene(type="stages", kind="stages", items=["早期 Harness", "中期 Harness", "高级 Harness"],
                    stages=[{"title": "早期 Harness", "labels": ["Prompt", "Tool", "Agent Loop"]},
                            {"title": "中期 Harness", "labels": ["Memory", "State", "Task", "Sub-Agent", "Environment"]},
                            {"title": "高级 Harness", "labels": ["Identity", "Experience", "Relationship", "Motivation",
                                                                "Learning", "Adaptation", "Governance"]}])
    frames = render_scene_frames(stages, canvas, orientation, DEFAULT_THEME)
    assert len(frames) == 1 and frames[0].getbbox() is not None
    df = _scene(type="definition", kind="definition", term="Experience Loop", items=["而这个循环可能比模型本身更决定一个 Agent 最终会变成什么。"])
    f2 = render_scene_frames(df, canvas, orientation, DEFAULT_THEME)
    assert len(f2) == 1 and f2[0].getbbox() is not None
    for img in frames + f2:
        w, h = img.size
        x0, y0, x1, y1 = img.getchannel("A").getbbox()
        assert x0 >= 0 and y0 >= 0 and x1 <= w and y1 <= h


def test_settings_default_off_and_clamped():
    cfg, _ = normalize_visual({})
    assert cfg["storyboard"] == {"strict_coverage": False, "subject_from_source": False}
    cfg2, _ = normalize_visual({"storyboard": {"strict_coverage": True, "subject_from_source": 1}})
    assert cfg2["storyboard"] == {"strict_coverage": True, "subject_from_source": True}
    cfg3, _ = normalize_visual({"storyboard": "oops"})
    assert cfg3["storyboard"]["strict_coverage"] is False
