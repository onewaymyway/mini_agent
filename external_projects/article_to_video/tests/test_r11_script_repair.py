"""R11：口播稿的局部修正。句子级问题由模型只改出问题的句子；金句由模型按编号指定原文句子（不做逐字比较）；
段落级错误与修正失败时回到整章重写。"""
import json
import re

import pytest

from article_to_video.script import writer as w
from article_to_video.script.repair import apply_fixes, repair_prompt, source_sentences
from article_to_video.script.validate import validate_script
from helpers_p2 import ScriptLLM, good_script, ready_project


def _repair_all(prompt):
    ids = re.findall(r"- id: (\S+)", prompt)
    return json.dumps({"fixes": [{"id": i, "text": "这是口播中的一句话，用来说明本章的要点内容。"} for i in ids]},
                      ensure_ascii=False)


def _part_source(pdir, part):
    return w._part_source(pdir, part["section_ids"])


def test_source_sentences_are_clean_and_numbered():
    sents = source_sentences("> **AI正在把“智能”本身变成生产要素。**\n\n规模是3,000亿元。")
    assert sents[0] == "AI正在把“智能”本身变成生产要素"
    assert all(len(x) >= 4 for x in sents)


def test_repair_prompt_lists_only_flagged_sentences_with_ids():
    script = {"beats": [{"id": "P01.b01", "sentences": [{"text": "甲"}, {"text": "投入9999亿。"}]}]}
    flags = [{"beat": "P01.b01", "index": 1, "kind": "number", "problem": "数字“9999”在本章原文中找不到"}]
    p = repair_prompt(flags, script, ["原文一句"])
    assert "id: P01.b01#1" in p and "id: P01.b01#0" not in p
    assert "【原文句子】" in p and "[0] 原文一句" in p


def test_apply_fixes_ignores_unknown_ids_and_uses_source_index():
    script = {"beats": [{"id": "P01.b01", "sentences": [{"text": "改写的金句", "quote": True}, {"text": "坏句"}]}]}
    sents = ["原文第一句", "原文第二句"]
    n = apply_fixes(script, [{"id": "P01.b01#0", "text": "", "source_index": 1},
                             {"id": "P01.b01#1", "text": "好句"},
                             {"id": "P99.b99#0", "text": "无效"}], sents)
    assert n == 2
    s0, s1 = script["beats"][0]["sentences"]
    assert s0 == {"text": "原文第二句", "quote": True}
    assert s1["text"] == "好句"


def test_quote_matched_by_model_meaning_takes_the_source_sentence(tmp_path):
    """金句的判断交给模型：模型指出原文中意思相同的句子，系统采用该原文句子，无需逐字相同。"""
    pdir = ready_project(tmp_path)
    part = w.load_spine(pdir)["parts"][0]
    source = _part_source(pdir, part)
    target = next(x for x in source_sentences(source) if not re.search(r"[0-9A-Za-z]", x) and 10 <= len(x) <= 30)
    idx = source_sentences(source).index(target)
    state = {"n": 0}

    def responder(prompt):
        state["n"] += 1
        if state["n"] == 1:
            obj = json.loads(good_script(prompt))              # 完整的稿件，只有第一句是换了说法的金句
            obj["beats"][0]["sentences"][0] = {"text": "换了说法的金句，意思和原文一样", "quote": True}
            return json.dumps(obj, ensure_ascii=False)
        assert "【口播修正任务】" in prompt
        return json.dumps({"fixes": [{"id": "P01.b01#0", "text": "", "source_index": idx}]}, ensure_ascii=False)

    w.write_part(pdir, part["id"], ScriptLLM(responder))
    saved = w.load_script(pdir, part["id"])
    first = saved["beats"][0]["sentences"][0]
    from article_to_video.validators.fidelity import norm_key
    assert norm_key(first["text"]) == norm_key(target) and first["quote"] is True   # 采用的是原文中的那一句（末尾标点按 R9 规则去掉）


def test_hard_error_skips_repair_and_rewrites_whole_part(tmp_path):
    """段落级错误（缺少 source_refs）不能靠局部修正解决，直接整章重写。"""
    pdir = ready_project(tmp_path)
    state = {"n": 0}

    def responder(prompt):
        state["n"] += 1
        assert "【口播修正任务】" not in prompt
        if state["n"] == 1:
            return json.dumps({"beats": [{"sentences": [{"text": "这句话没有引用来源。"}]}]}, ensure_ascii=False)
        return good_script(prompt)

    llm = ScriptLLM(responder)
    w.write_part(pdir, "P01", llm)
    assert llm.calls == 2


def test_invalid_repair_output_falls_back_to_full_rewrite(tmp_path):
    pdir = ready_project(tmp_path)
    state = {"n": 0}

    def responder(prompt):
        state["n"] += 1
        if state["n"] == 1:
            return json.dumps({"beats": [{"source_refs": ["s01"], "sentences": [{"text": "投入9999亿。"}]}]},
                              ensure_ascii=False)
        if "【口播修正任务】" in prompt:
            return "这不是 JSON"
        return good_script(prompt)

    llm = ScriptLLM(responder)
    w.write_part(pdir, "P01", llm)
    assert llm.calls == 3          # 第一次整章 → 修正（无效）→ 第二次整章


def test_validator_marks_flags_and_hardness():
    script = {"beats": [{"id": "P01.b01", "source_refs": ["s01"],
                         "sentences": [{"text": "投入9999亿。"}, {"text": "正常的一句话。"}]}]}
    v = validate_script(script, "原文只有3000亿", ["s01"], check_pacing=False)
    assert v["flags"] and v["flags"][0]["kind"] == "number" and v["flags"][0]["index"] == 0
    assert v["hard"] is False
    script["beats"][0].pop("source_refs")
    v = validate_script(script, "原文只有3000亿", ["s01"], check_pacing=False)
    assert v["hard"] is True
