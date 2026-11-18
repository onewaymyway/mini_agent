"""R9：口播校验忽略标点与大小写；数字按完整记号比较；金句对齐原文的规范写法。"""
import json
import re

import pytest

from article_to_video.script import writer as w
from article_to_video.script.validate import canonicalize_quotes, validate_script
from article_to_video.validators.fidelity import number_tokens, numbers_supported
from helpers_p2 import ScriptLLM, ready_project

SRC = (
    "这时 AI 的经济属性发生了变化。\n\n它不再只是：\n\n> Software as a Tool\n\n而开始变成：\n\n"
    "> **AI正在把“智能”本身变成一种可以被规模化生产、复制和调用的生产要素。**\n\n"
    "规模是3,000亿元，增长率3.5%，agentos 是一个平台。\n"
)


def _script(*sentences):
    return {"beats": [{"id": "P01.b01", "source_refs": ["s01"],
                       "sentences": [dict(s, text=t) if isinstance(s, dict) else {"text": s} for t, s in
                                     [(x if isinstance(x, str) else x["text"], {} if isinstance(x, str) else x)
                                      for x in sentences]]}]}


def _errors(sentences):
    return validate_script(_script(*sentences), SRC, ["s01"], check_pacing=False)["errors"]


# ---------- 数字 ----------

def test_numbers_match_complete_tokens_only():
    assert numbers_supported("3000亿", "规模是3,000亿元")          # 千分位逗号不影响
    assert not numbers_supported("35", "增长率3.5%")                # 3.5 与 35 不相同
    assert not numbers_supported("3.5", "代码 35 行")
    assert number_tokens("3,000亿元，增长3.5%") == ["3000", "3.5%"]


def test_script_flags_number_that_is_only_a_substring():
    errs = _errors(["增长率是35%。"])
    assert any("数字" in e and "35" in e for e in errs)


def test_script_accepts_number_with_thousands_separator():
    assert _errors(["规模大约是3000亿。"]) == []


# ---------- 英文词与金句 ----------

def test_english_word_is_case_insensitive():
    assert _errors(["AgentOS 是一个平台。"]) == []


def test_english_word_absent_from_source_is_flagged():
    assert any("英文词" in e for e in _errors(["Kubernetes 是一个平台。"]))


def test_quote_sentence_matches_despite_punctuation_and_quotes():
    sent = {"text": 'AI正在把"智能"本身变成一种可以被规模化生产、复制和调用的生产要素', "quote": True}
    assert _errors([sent]) == []


def test_fabricated_quote_is_still_rejected():
    sent = {"text": "这句话在原文中从来没有出现过", "quote": True}
    assert any("逐字" in e for e in _errors([sent]))


def test_canonicalize_quotes_rewrites_matched_quote_sentences_only():
    script = _script({"text": 'AI正在把"智能"本身变成一种可以被规模化生产、复制和调用的生产要素', "quote": True},
                     {"text": "这句不是金句，随便写的内容。", "quote": False})
    n = canonicalize_quotes(script, SRC)
    assert n == 1
    texts = [s["text"] for s in script["beats"][0]["sentences"]]
    assert texts[0] == "AI正在把“智能”本身变成一种可以被规模化生产、复制和调用的生产要素"
    assert texts[1] == "这句不是金句，随便写的内容。"


# ---------- 端到端：写稿时金句被对齐，校验通过 ----------

def test_writer_accepts_mangled_quote_and_stores_source_form(tmp_path):
    pdir = ready_project(tmp_path)
    spine = w.load_spine(pdir)
    part = spine["parts"][0]
    source = w._part_source(pdir, part["section_ids"])
    sentence = next(x.strip() for x in re.split(r"[。！？\n]", source)
                    if 10 <= len(x.strip()) <= 30 and not re.search(r"[0-9A-Za-z]", x))

    def mangle(s):
        return s.replace("，", ",").replace("“", '"').replace("”", '"').lower()

    def responder(prompt):
        budget = int(re.search(r"BUDGET_CHARS=(\d+)", prompt).group(1))
        refs = json.loads(re.search(r"SECTION_IDS=(\[.*?\])", prompt).group(1))
        filler = "这是口播中的一句话，用来说明本章的要点内容。"
        n = max(1, budget // len(filler)) + 1
        sents = [{"text": filler, "quote": False} for _ in range(n)]
        sents[0] = {"text": mangle(sentence), "quote": True}
        return json.dumps({"beats": [{"role": "claim", "source_refs": [refs[0]], "sentences": sents}]},
                          ensure_ascii=False)

    w.write_part(pdir, part["id"], ScriptLLM(responder))
    saved = w.load_script(pdir, part["id"])
    first = saved["beats"][0]["sentences"][0]["text"]
    from article_to_video.validators.fidelity import norm_key
    assert norm_key(first) == norm_key(sentence)  # 内容与原文一致（忽略标点与大小写）
    assert ">" not in first and "\n" not in first  # 引用符与换行已去掉
    assert first in source                        # 写法来自原文
