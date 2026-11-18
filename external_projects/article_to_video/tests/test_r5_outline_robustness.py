"""R5 大纲稳健性：忽略标点与大小写的忠实度校验、引文对齐、只对结构性问题重试、分组断点续跑。

用例直接取自一次真实失败（大纲 g01 两次输出的引文被判为“不是原文逐字片段”）。
"""
import json

import pytest

from article_to_video.planning import llm as L
from article_to_video.planning.errors import PlanError
from article_to_video.planning.runner import _context, load_digests, load_spine, plan_outline
from article_to_video.planning.chunks import build_groups
from article_to_video.validators import fidelity as F
from article_to_video.validators.coverage import validate_spine
from helpers_p1 import ScriptedLLM, covering_spine, good_digest, make_project

# 原文片段：跨段落、带引用符 ">"、中文引号与加粗
SRC_CROSS = (
    "这时 AI 的经济属性发生了变化。\n\n它不再只是：\n\n> Software as a Tool\n\n而开始变成：\n\n"
    "> **Intelligence as Labor**\n\n也就是……\n\n"
    "> **AI正在把“智能”本身变成一种可以被规模化生产、复制和调用的生产要素。**\n"
)


# ---------- 归一化与对齐 ----------

def test_norm_key_ignores_punctuation_case_space_and_markup():
    assert F.norm_key("“智能”本身") == F.norm_key('"智能"本身')
    assert F.norm_key("Software as a Tool") == F.norm_key("software  as a tool")
    assert F.norm_key("它不再只是：Software") == F.norm_key("它不再只是 Software")
    assert F.norm_key("3,000 亿元") == F.norm_key("3000亿元")
    assert F.norm_key("全角，逗号") == F.norm_key("全角,逗号")


def test_locate_recovers_verbatim_span_across_markdown_lines():
    """模型把跨段落的引文合并成一句（去掉了换行与 > 符号），仍应对齐到原文。"""
    found = F.locate("它不再只是：Software as a Tool 而开始变成", SRC_CROSS)
    assert found is not None
    assert "Software as a Tool" in found and "而开始变成" in found
    assert "\n" not in found and ">" not in found


def test_locate_matches_curly_and_straight_quotes():
    found = F.locate('AI正在把"智能"本身变成一种可以被规模化生产、复制和调用的生产要素', SRC_CROSS)
    assert found is not None
    assert "“智能”本身" in found


def test_locate_is_case_insensitive_and_returns_source_casing():
    found = F.locate("intelligence as labor", SRC_CROSS)
    assert found == "Intelligence as Labor"


def test_locate_rejects_fabrication_and_too_short_fragments():
    assert F.locate("原文从未说过这句话", SRC_CROSS) is None
    assert F.locate("是", SRC_CROSS) is None          # 归一化后不足 4 字，不可靠


def test_repair_digest_keeps_matches_and_drops_the_rest():
    obj = {"summary": "s", "core_claims": ["c"],
           "quotable": ["intelligence as labor", "编造的话ABC"],
           "key_numbers": ["3,000", "9999亿"]}
    src = SRC_CROSS + "\n核心是3000亿元。\n"
    fixed, dropped = F.repair_digest(obj, src)
    assert fixed["quotable"] == ["Intelligence as Labor"]
    assert fixed["key_numbers"] == ["3000"]
    assert len(dropped) == 2
    assert any("编造" in x for x in dropped) and any("9999" in x for x in dropped)


def test_validate_digest_compat_uses_normalized_rules():
    obj = {"summary": "s", "core_claims": ["c"], "quotable": ["Intelligence, as labor!"]}
    assert F.validate_digest(obj, SRC_CROSS) == []
    bad = {**obj, "quotable": ["原文从未说过这句话"]}
    assert any("逐字" in x for x in F.validate_digest(bad, SRC_CROSS))


# ---------- LLM 摘要：只对结构性问题重试 ----------

GROUP = {"id": "g01", "section_ids": ["s01"], "text": SRC_CROSS}


def _digest_json(**over):
    base = {"summary": "摘要", "core_claims": ["论点"], "key_numbers": [], "quotable": []}
    base.update(over)
    return json.dumps(base, ensure_ascii=False)


def test_llm_digest_repairs_quotes_without_retry():
    """真实失败的情形：金句含引用符与换行差异，应直接对齐，不重试。"""
    llm = ScriptedLLM(lambda p: _digest_json(
        quotable=["它不再只是：Software as a Tool 而开始变成",
                  'AI正在把"智能"本身变成一种可以被规模化生产、复制和调用的生产要素']), lambda p: "")
    obj, _raw = L.llm_digest(GROUP, llm, "文章")
    assert llm.calls["digest"] == 1
    assert len(obj["quotable"]) == 2
    assert "Software as a Tool" in obj["quotable"][0] and "而开始变成" in obj["quotable"][0]
    assert obj["quotable"][1] == "AI正在把“智能”本身变成一种可以被规模化生产、复制和调用的生产要素"


def test_llm_digest_retries_only_on_structural_problems():
    state = {"n": 0}

    def responder(prompt):
        state["n"] += 1
        if state["n"] == 1:
            return "不是 JSON"
        return _digest_json(quotable=["Intelligence as Labor"])

    llm = ScriptedLLM(responder, lambda p: "")
    obj, _ = L.llm_digest(GROUP, llm, "文章")
    assert llm.calls["digest"] == 2
    assert obj["quotable"] == ["Intelligence as Labor"]


def test_llm_digest_structural_failure_raises_after_two_attempts():
    llm = ScriptedLLM(lambda p: _digest_json(summary="", core_claims=[]), lambda p: "")
    with pytest.raises(PlanError):
        L.llm_digest(GROUP, llm, "文章")
    assert llm.calls["digest"] == L.MAX_ATTEMPTS


# ---------- 大纲金句对齐 ----------

def test_golden_quotes_are_mapped_to_registry_and_unknown_dropped():
    digests = [{"quotable": ["Intelligence as Labor", "核心是通用计算"]}]
    spine = {"golden_quotes": ["intelligence, as labor", "编造的金句XYZ", "核心是通用计算。"]}
    dropped = L.repair_golden_quotes(spine, digests)
    assert spine["golden_quotes"] == ["Intelligence as Labor", "核心是通用计算"]
    assert len(dropped) == 1 and "编造" in dropped[0]


def test_coverage_golden_check_ignores_punctuation_and_case():
    section_ids = ["s01"]
    spine = {"parts": [{"id": "P01", "title": "章", "section_ids": ["s01"], "weight": 1}],
             "hook_candidates": ["h"], "selected_hook": 0, "cuts": [],
             "golden_quotes": ["INTELLIGENCE, as labor"]}
    digests = [{"quotable": ["Intelligence as Labor"]}]
    assert validate_spine(spine, section_ids, digests)["errors"] == []
    spine["golden_quotes"] = ["从未出现的金句"]
    assert any("金句" in e for e in validate_spine(spine, section_ids, digests)["errors"])


# ---------- 断点续跑 ----------

def test_partial_digests_resume_after_a_failed_group(tmp_path):
    pdir = make_project(tmp_path)
    total = len(build_groups(_context(pdir)["lines"], _context(pdir)["doc"]))
    assert total >= 3

    def first_run(prompt):
        if "GROUP_ID=g03" in prompt:
            return _digest_json(summary="", core_claims=[])   # 结构性失败
        return good_digest(prompt)

    with pytest.raises(PlanError):
        plan_outline(pdir, engine="llm", llm=ScriptedLLM(first_run, covering_spine))
    partial = json.loads((pdir / "plan" / "digests_partial.json").read_text(encoding="utf-8"))
    assert [d["group_id"] for d in partial["digests"]] == ["g01", "g02"]
    assert load_spine(pdir) is None

    second = ScriptedLLM(good_digest, covering_spine)
    plan_outline(pdir, engine="llm", llm=second)
    assert second.calls["digest"] == total - 2       # 只重算 g01、g02 之外的组
    assert load_spine(pdir) is not None
    assert not (pdir / "plan" / "digests_partial.json").exists()
    assert len(load_digests(pdir)) == total


def test_partial_is_ignored_when_group_input_changed(tmp_path):
    pdir = make_project(tmp_path)
    (pdir / "plan").mkdir(exist_ok=True)
    (pdir / "plan" / "digests_partial.json").write_text(
        json.dumps({"digests": [{"group_id": "g01", "input_sha": "stale", "summary": "旧"}]}, ensure_ascii=False),
        encoding="utf-8")
    llm = ScriptedLLM(good_digest, covering_spine)
    plan_outline(pdir, engine="llm", llm=llm)
    total = len(build_groups(_context(pdir)["lines"], _context(pdir)["doc"]))
    assert llm.calls["digest"] == total              # 过期的部分结果不会被复用
