"""分镜改进·第二期（以测试文章为样本）：文本代码块自动分类（A3）、框线架构图解析（A4）、概念条目标记（A6）；
模型版分镜使用公式式表达（视同公式）与标签（视同列表）。
"""
from pathlib import Path

import pytest

from article_to_video.source.ascii_graph import classify_code
from article_to_video.source.md_parse import parse_markdown
from article_to_video.visual.director_llm import beat_context, parse_window
from article_to_video.visual.shots import sentence_id

FIXTURE = Path(__file__).parent / "fixtures" / "harness_article.md"


@pytest.fixture(scope="module")
def blocks():
    return parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]


def _b(blocks, bid):
    return next(x for x in blocks if x["id"] == bid)


# ---------- A4：框线架构图 ----------

def test_architecture_diagram_becomes_loop_chain(blocks):
    arch = _b(blocks, "s28.p03")
    assert arch["shape"] == "chain" and arch["source"] == "ascii"
    assert arch["nodes"][0] == "Foundation Model" and arch["nodes"][-1] == "New Agent State"
    assert arch["loop"] is True                       # └──→ Experience 回到已有节点
    assert len(arch["nodes"]) == 9


def test_plain_box_art_with_one_box_stays_art():
    assert classify_code(["┌──┐", "│ A │", "└──┘"])["shape"] == "ascii_art"


# ---------- A3：文本代码块的自动分类 ----------

def test_formula_hints_for_article_expressions(blocks):
    f = _b(blocks, "s07.p20")
    assert f["hint"] == "formula" and f["items"] == ["Identity", "Model", "Memory", "Experience", "Continuity"]
    assert _b(blocks, "s29.p08")["hint"] == "formula"
    assert _b(blocks, "s12.p12")["hint"] == "formula"


def test_key_value_hints(blocks):
    assert _b(blocks, "s17.p06")["hint"] == "kv"      # Model / = / Potential Intelligence
    assert _b(blocks, "s07.p08")["hint"] == "kv"      # Agent A + Model = Claude …（多数行为键值）


def test_stage_and_fact_hints(blocks):
    st = _b(blocks, "s15.p03")
    assert st["hint"] == "stages" and st["hint_text"] == "早期 Harness" and st["items"] == ["Prompt", "Tool", "Agent Loop"]
    assert _b(blocks, "s14.p07")["hint"] == "facts"


def test_label_hint_and_plain_text_has_no_hint(blocks):
    assert _b(blocks, "s22.p03")["hint"] == "labels"
    assert "hint" not in classify_code(["普通说明文字"]) or classify_code(["普通说明文字"]).get("hint") is None


# ---------- A6：概念条目 ----------

def test_english_level_one_headings_are_concepts(blocks):
    names = {x["text"] for x in blocks if x.get("concept")}
    assert {"Identity Infrastructure", "Experience Loop", "Life Data", "AgentOS",
            "Artificial Life", "Intelligence Infrastructure"} <= names
    assert not any(x.get("concept") for x in blocks if x.get("text", "").startswith("一、"))
    concept = _b(blocks, "s20.p01")
    assert concept["definition"].startswith("管理人工智能主体")   # 三期修订：同节内第一句完整陈述（跳过否定句与引导句）


# ---------- 模型版：公式与标签 ----------

def _beat(bid, sentences, refs):
    return {"id": bid, "role": "claim", "source_refs": refs,
            "sentences": [{"text": t, "quote": False} for t in sentences]}


def test_model_path_offers_formula_for_hinted_expression(blocks):
    beat = _beat("P07.b01", ["模型提供潜能。", "经历塑造个体。"], ["s07.p20"])
    ctx = beat_context(beat, blocks)
    assert "formula" in ctx["allowed"] and any("原文公式" in n for n in ctx["notes"])


def test_labels_are_paged_like_lists_and_must_be_covered(blocks):
    lab = _b(blocks, "s22.p03")                       # 9 个标签，每镜上限 5 项，分两镜
    bid = "P22.b01"
    beat = _beat(bid, ["身份与记忆。", "环境与经验。"], ["s22.p03"])
    ok = {"shots": [{"sentences": [sentence_id(bid, 0)], "kind": "list", "part": [1, 5]},
                    {"sentences": [sentence_id(bid, 1)], "kind": "list", "part": [6, 9]}]}
    scenes, errs, _ = parse_window(ok, [beat], {bid: beat_context(beat, blocks)}, prev=None,
                                   ill_used=0, allowance=10, shots=2)
    assert errs == [] and [i for s in scenes for i in s["items"]] == lab["items"]
    partial = {"shots": [{"sentences": [sentence_id(bid, 0), sentence_id(bid, 1)], "kind": "list", "part": [1, 5]}]}
    _, errs2, _ = parse_window(partial, [beat], {bid: beat_context(beat, blocks)}, prev=None,
                               ill_used=0, allowance=10, shots=2)
    assert any("未展示第 6、7、8、9 项" in e for e in errs2)
