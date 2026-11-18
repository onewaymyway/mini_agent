from article_to_video.script.budget import chars_for_minutes, pacing_issue
from article_to_video.script.lexicon import apply_lexicon, parse_lexicon_text
from article_to_video.script.validate import validate_script

SRC = "2022年以后，生成式AI爆发。GPU 投入达到3000亿元。原话：核心是通用计算。"


def _script(*sentences, refs=("s01",), budget=None):
    return {"budget_chars": budget or 0, "beats": [{
        "id": "P01.b01", "source_refs": list(refs),
        "sentences": [s if isinstance(s, dict) else {"text": s, "quote": False} for s in sentences],
    }]}


def test_valid_script_has_no_errors():
    v = validate_script(_script("2022年以后，生成式AI爆发了。"), SRC, ["s01"], check_pacing=False)
    assert v["errors"] == []


def test_fabricated_number_is_blocked():
    v = validate_script(_script("投入达到9999亿元。"), SRC, ["s01"], check_pacing=False)
    assert any("9999" in e for e in v["errors"])


def test_fabricated_english_term_is_blocked():
    v = validate_script(_script("它叫做 AgentOS 系统。"), SRC, ["s01"], check_pacing=False)
    assert any("AgentOS" in e for e in v["errors"])


def test_fabricated_quote_is_blocked():
    bad = {"text": "没有人这样说过这句话", "quote": True}
    v = validate_script(_script(bad), SRC, ["s01"], check_pacing=False)
    assert any("逐字" in e for e in v["errors"])


def test_real_quote_passes_even_with_markdown_elsewhere_in_source():
    good = {"text": "核心是通用计算", "quote": True}
    v = validate_script(_script(good), SRC, ["s01"], check_pacing=False)
    assert v["errors"] == []


def test_markdown_and_formula_symbols_are_blocked():
    v = validate_script(_script("**重点**内容", "公式 x^2 在这里"), SRC, ["s01"], check_pacing=False)
    assert any("Markdown" in e for e in v["errors"])
    assert any("公式" in e for e in v["errors"])


def test_overlong_sentence_is_blocked_and_medium_warned():
    v = validate_script(_script("字" * 61), SRC, ["s01"], check_pacing=False)
    assert any("过长" in e for e in v["errors"])
    v2 = validate_script(_script("字" * 45), SRC, ["s01"], check_pacing=False)
    assert v2["errors"] == [] and v2["warnings"]


def test_reference_to_section_outside_part_is_blocked():
    v = validate_script(_script("2022年以后。", refs=("s09",)), SRC, ["s01"], check_pacing=False)
    assert any("不属于本章" in e for e in v["errors"])


def test_pacing_tolerance_is_fifteen_percent():
    assert chars_for_minutes(1) == 252
    assert pacing_issue(260, 252) is None
    assert pacing_issue(200, 252) is not None
    assert pacing_issue(300, 252) is not None


def test_pacing_only_checked_when_asked():
    s = _script("二。", budget=1000)
    assert validate_script(s, SRC, ["s01"], check_pacing=False)["pacing"] is None
    assert validate_script(s, SRC, ["s01"], check_pacing=True)["pacing"] is not None


def test_lexicon_parse_and_apply():
    lex = parse_lexicon_text("AgentOS=艾真特 O S\nGPU = 显卡\n无效行\n")
    assert lex == {"AgentOS": "艾真特 O S", "GPU": "显卡"}
    assert apply_lexicon("它是 AgentOS 与 GPU", lex) == "它是 艾真特 O S 与 显卡"
    assert apply_lexicon("没有要替换的词", lex) is None


def test_lexicon_prefers_longer_word():
    lex = {"AI": "人工智能", "AI Agent": "智能体"}
    assert apply_lexicon("AI Agent 很重要", lex) == "智能体 很重要"
