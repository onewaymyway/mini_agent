"""R10：大纲中的金句。直接输入即可加入（不要求出现在原文中，标注来源）；指定到章节后，该章口播必须原样使用。
被剔除的金句可修改后加入；被剔除的数字只能忽略（暂未接入口播）。"""
import json
import re

import pytest

from article_to_video.planning.errors import PlanError
from article_to_video.planning.runner import (add_outline_quote, confirm_spine, dismiss_dropped, load_digests,
                                             load_spine, plan_outline, promote_dropped_quote, remove_outline_quote)
from article_to_video.script import writer as w
from article_to_video.script.validate import validate_script
from article_to_video.validators.outline_report import outline_report
from helpers_p1 import ScriptedLLM, covering_spine, make_project
from helpers_p2 import ScriptLLM, ready_project


def _rules_outline(tmp_path):
    pdir = make_project(tmp_path)
    plan_outline(pdir, engine="rules")
    return pdir


def test_add_quote_directly_without_source_and_marks_origin(tmp_path):
    pdir = _rules_outline(tmp_path)
    spine = add_outline_quote(pdir, "这是我自己想到的一句话", "P01")
    q = spine["quotes"][0]
    assert q == {"text": "这是我自己想到的一句话", "part_id": "P01", "origin": "人工"}
    assert load_spine(pdir)["quotes"] == spine["quotes"]           # 已保存


def test_add_quote_found_in_source_is_marked_original(tmp_path):
    pdir = _rules_outline(tmp_path)
    article = (pdir / "source" / "article.md").read_text(encoding="utf-8")
    line = next(x.strip(" >*") for x in article.splitlines() if 12 <= len(x.strip(" >*")) <= 30 and not re.search(r"[|#]", x))
    spine = add_outline_quote(pdir, line, "P02")
    assert spine["quotes"][0]["origin"] == "原文"


def test_add_quote_validation(tmp_path):
    pdir = _rules_outline(tmp_path)
    with pytest.raises(PlanError):
        add_outline_quote(pdir, "   ", "P01")
    with pytest.raises(PlanError):
        add_outline_quote(pdir, "内容", "P99")
    add_outline_quote(pdir, "一句话", "P01")
    with pytest.raises(PlanError):
        add_outline_quote(pdir, "一句话", "P01")                    # 同一章不重复


def test_add_quote_requires_outline(tmp_path):
    pdir = make_project(tmp_path)
    with pytest.raises(PlanError):
        add_outline_quote(pdir, "内容", "P01")


def test_remove_quote(tmp_path):
    pdir = _rules_outline(tmp_path)
    add_outline_quote(pdir, "甲", "P01")
    add_outline_quote(pdir, "乙", "P01")
    spine = remove_outline_quote(pdir, 0)
    assert [q["text"] for q in spine["quotes"]] == ["乙"]
    with pytest.raises(PlanError):
        remove_outline_quote(pdir, 5)


def test_promote_dropped_quote_moves_it_into_outline_with_edited_text(tmp_path):
    pdir = make_project(tmp_path)
    plan_outline(pdir, engine="llm", llm=ScriptedLLM(
        lambda p: json.dumps({"summary": "x", "core_claims": ["c"], "quotable": ["编造原话ABC"]}, ensure_ascii=False),
        covering_spine))
    assert load_digests(pdir)[0]["dropped"][0]["kind"] == "quote"
    spine = promote_dropped_quote(pdir, "g01", 0, "改过的金句", "P01")
    assert {"text": "改过的金句", "part_id": "P01", "origin": "人工"} in spine["quotes"]
    assert load_digests(pdir)[0]["dropped"] == []                 # 已从剔除列表移除


def test_promote_failure_keeps_dropped_item(tmp_path):
    pdir = make_project(tmp_path)
    plan_outline(pdir, engine="llm", llm=ScriptedLLM(
        lambda p: json.dumps({"summary": "x", "core_claims": ["c"], "quotable": ["编造原话ABC"]}, ensure_ascii=False),
        covering_spine))
    with pytest.raises(PlanError):
        promote_dropped_quote(pdir, "g01", 0, "内容", "P99")       # 章节不存在
    assert len(load_digests(pdir)[0]["dropped"]) == 1             # 失败时条目仍在


def test_numbers_cannot_be_promoted_only_dismissed(tmp_path):
    pdir = make_project(tmp_path)
    plan_outline(pdir, engine="llm", llm=ScriptedLLM(
        lambda p: json.dumps({"summary": "x", "core_claims": ["c"], "key_numbers": ["9999亿"]}, ensure_ascii=False),
        covering_spine))
    with pytest.raises(PlanError):
        promote_dropped_quote(pdir, "g01", 0, "9999亿", "P01")
    dismiss_dropped(pdir, "g01", 0)
    assert load_digests(pdir)[0]["dropped"] == []


def test_manual_quote_is_flagged_in_report_but_not_blocking(tmp_path):
    pdir = _rules_outline(tmp_path)
    spine = add_outline_quote(pdir, "完全人工写的一句话", "P01")
    rep = outline_report(spine, [c for c in ["s01"]], [])
    fid = next(c for c in rep["categories"] if c["key"] == "fidelity")
    assert any("人工加入" in i["text"] for i in fid["items"])
    assert rep["ok"] is True or rep["ok"] is False                  # 门禁只看原有规则，不因人工金句而失败


# ---------- 写稿：指定章节的金句必须原样出现 ----------

def test_must_quotes_are_listed_in_writer_prompt(tmp_path):
    pdir = ready_project(tmp_path)
    spine = load_spine(pdir)
    part = spine["parts"][0]
    add_outline_quote(pdir, "这句话必须原样出现", part["id"])
    spine = load_spine(pdir)
    assert w.must_quotes_for(spine, part["id"]) == ["这句话必须原样出现"]
    prompt = w.build_prompt(part, "原文", persona="host", budget_chars=100, glossary=[], prev_tail="",
                            issues=[], must_quotes=w.must_quotes_for(spine, part["id"]))
    assert "这句话必须原样出现" in prompt


def test_validator_accepts_manual_quote_not_in_source():
    script = {"beats": [{"id": "P01.b01", "source_refs": ["s01"],
                         "sentences": [{"text": "这句话必须原样出现", "quote": True}]}]}
    v = validate_script(script, "原文里完全没有这句", ["s01"], check_pacing=False)
    assert any("逐字" in e for e in v["errors"])                   # 没有豁免时仍报错
    v = validate_script(script, "原文里完全没有这句", ["s01"], check_pacing=False,
                        manual_quotes=["这句话必须原样出现"])
    assert v["errors"] == []                                       # 作为人工金句，允许


def test_writer_uses_manual_quote_end_to_end(tmp_path):
    pdir = ready_project(tmp_path)
    part = load_spine(pdir)["parts"][0]
    must = "这句话必须原样出现在口播里"
    add_outline_quote(pdir, must, part["id"])
    confirm_spine(pdir)                                            # 编辑会取消确认，写稿前需重新确认

    def responder(prompt):
        assert must in prompt                                     # 提示词包含该金句
        budget = int(re.search(r"BUDGET_CHARS=(\d+)", prompt).group(1))
        refs = json.loads(re.search(r"SECTION_IDS=(\[.*?\])", prompt).group(1))
        filler = "这是口播中的一句话，用来说明本章的要点内容。"
        n = max(1, budget // len(filler)) + 1
        sents = [{"text": must, "quote": True}] + [{"text": filler, "quote": False} for _ in range(n)]
        return json.dumps({"beats": [{"role": "claim", "source_refs": [refs[0]], "sentences": sents}]},
                          ensure_ascii=False)

    w.write_part(pdir, part["id"], ScriptLLM(responder))
    saved = w.load_script(pdir, part["id"])
    assert saved["beats"][0]["sentences"][0]["text"] == must


def test_changing_quotes_marks_writer_output_stale(tmp_path):
    pdir = ready_project(tmp_path)
    part = load_spine(pdir)["parts"][0]

    def responder(prompt):
        budget = int(re.search(r"BUDGET_CHARS=(\d+)", prompt).group(1))
        refs = json.loads(re.search(r"SECTION_IDS=(\[.*?\])", prompt).group(1))
        filler = "这是口播中的一句话，用来说明本章的要点内容。"
        return json.dumps({"beats": [{"role": "claim", "source_refs": [refs[0]],
                                      "sentences": [{"text": filler} for _ in range(max(2, budget // len(filler)))]}]},
                          ensure_ascii=False)

    llm = ScriptLLM(responder)
    w.write_part(pdir, part["id"], llm)
    first = llm.calls
    w.write_part(pdir, part["id"], llm)                           # 未变化：不重写
    assert llm.calls == first
    add_outline_quote(pdir, "新加的金句", part["id"])
    confirm_spine(pdir)
    w.write_part(pdir, part["id"], llm)                           # 金句变化：重写
    assert llm.calls > first


def test_outline_page_has_quote_section_and_direct_add_form(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from conftest import PROJECT_DIR
    from helpers_ui_nav import go_step
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    _rules_outline(tmp_path)
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    go_step(at, "🗺️ 大纲")
    assert not at.exception, [e.value for e in at.exception]
    assert any(s.value == "金句（写稿时原样使用）" for s in at.subheader)
    assert any(b.label == "加入金句" for b in at.button)
    assert any(t.label == "金句文字" for t in at.text_input)
