"""R28：幻灯片规划不合格时，把具体问题交给文本模型修复并持续重试，而不是直接失败。"""
import json
from pathlib import Path

import pytest

from article_to_video.deck import store as deck_store
from article_to_video.deck.plan import plan_slides
from article_to_video.deck.repair import build_repair_prompt, lead_block_ids, repair_deck
from article_to_video.deck.store import deck_status, load_deck, plan_deck
from article_to_video.deck.validate import validate_deck
from article_to_video.planning.errors import PlanError
from article_to_video.project_store import write_json_atomic
from article_to_video.source.md_parse import parse_markdown
from helpers_p2 import make_project

FIXTURE = Path(__file__).parent / "fixtures" / "harness_article.md"
PART = {"id": "P01", "title": "章", "section_ids": ["s01"], "budget_minutes": 6.0}


def _h(i, text, level=2):
    return {"id": f"h{i}", "kind": "heading", "level": level, "text": text, "section": "s01"}


def _p(i, text):
    return {"id": f"p{i}", "kind": "paragraph", "plain": text, "section": "s01"}


def _l(i, n):
    return {"id": f"l{i}", "kind": "list", "items": [f"条目{k:02d}内容" for k in range(n)], "section": "s01"}


BLOCKS = [_h(1, "第一节"), _p(1, "先看三点如下："), _l(1, 3), _h(2, "第二节"), _p(2, "第二节只有一句话。")]


def _answer_from(deck):
    """把规划转成模型应答（只含模型该给的字段）。"""
    return json.dumps({"slides": [{"title": s["title"], "elements": [
        {k: e[k] for k in ("kind", "source", "items", "header", "rows", "text") if k in e}
        for e in s["elements"]]} for s in deck["slides"]]}, ensure_ascii=False)


def _good_deck():
    return plan_slides(PART, BLOCKS, "P01", page_cap=12, element_cap=12)


def _bad_deck(blocks, part_id, **kw):
    """模拟规则分页产出不合格规划：每个元素各占一页（前言单独成页）。"""
    good = plan_slides(PART, blocks, part_id, page_cap=12, element_cap=12)
    slides = []
    for s in good["slides"]:
        for e in s["elements"]:
            slides.append(dict(s, id=f"P01.S{len(slides) + 1:02d}", elements=[e], source_blocks=[e["source"]]))
    return dict(good, slides=slides, page_cap=kw.get("page_cap", 12), element_cap=kw.get("element_cap", 12))


class ScriptedLLM:
    model = "scripted"

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def ask(self, prompt, system=""):
        self.prompts.append(prompt)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return r(prompt) if callable(r) else r


def test_chained_lead_ins_no_longer_leave_a_lead_only_page():
    """根因回归：三级标题后紧跟前言段，两者曾被单独分成一组，成为“只有前言”的页。"""
    blocks = [_h(1, "第一节"), _h(2, "小标题", level=3), _p(1, "下面的内容如下："), _l(1, 9)]
    for pc, ec in ((12, 12), (8, 8), (5, 5)):
        deck = plan_slides(PART, blocks, "P01", page_cap=pc, element_cap=ec)
        assert validate_deck(deck, blocks, page_cap=pc, element_cap=ec)["errors"] == [], (pc, ec)


def test_repair_loop_feeds_specific_problems_and_retries_until_valid():
    base = _bad_deck(BLOCKS, "P01")
    errs = validate_deck(base, BLOCKS)["errors"]
    assert any("前言留在页尾" in e or "只有标题或前言" in e for e in errs)
    cheat = json.loads(_answer_from(_good_deck()))
    cheat["slides"][0]["elements"][0]["items"] = ["原文里根本没有的一句话"]
    llm = ScriptedLLM(["这不是JSON", json.dumps(cheat, ensure_ascii=False), _answer_from(_good_deck())])
    deck = repair_deck(PART, BLOCKS, "P01", base, errs, llm)
    assert validate_deck(deck, BLOCKS, page_cap=12, element_cap=12)["errors"] == []
    assert deck["repaired_by_llm"] and deck["repair_attempts"] == 3
    assert len(llm.prompts) == 3
    assert errs[0] in llm.prompts[0]                              # 第一次：校验给出的具体问题
    assert "不是有效的 JSON" in llm.prompts[1]                      # 第二次：JSON 错误
    assert "不在原文中" in llm.prompts[2]                           # 第三次：上一版内容不在原文中的具体问题
    assert "先看三点如下：" in llm.prompts[0]                       # 原文随提示一并给出


def test_lead_in_flags_come_from_the_source_not_from_the_model():
    assert lead_block_ids(BLOCKS) == {"p1"}
    bad = json.dumps({"slides": [
        {"title": "第一节", "elements": [{"kind": "keypoint", "source": "p1", "items": ["先看三点如下："]}]},
        {"title": "第一节（续）", "elements": [{"kind": "list", "source": "l1",
                                              "items": BLOCKS[2]["items"]}]},
        {"title": "第二节", "elements": [{"kind": "keypoint", "source": "p2", "items": ["第二节只有一句话。"]}]}]},
        ensure_ascii=False)
    llm = ScriptedLLM([bad, _answer_from(_good_deck())])
    deck = repair_deck(PART, BLOCKS, "P01", None, ["x"], llm)
    assert deck["repair_attempts"] == 2                           # 模型自称无前言也无效：前言单独成页被校验拒绝
    assert "只有标题或前言" in llm.prompts[1] or "前言留在页尾" in llm.prompts[1]


def test_unknown_source_and_kind_are_reported_back():
    bad = json.dumps({"slides": [{"title": "t", "elements": [
        {"kind": "banner", "source": "p1", "items": ["a"]},
        {"kind": "keypoint", "source": "zzz", "items": ["a"]}]}]})
    llm = ScriptedLLM([bad, _answer_from(_good_deck())])
    deck = repair_deck(PART, BLOCKS, "P01", None, ["x"], llm)
    assert deck["repair_attempts"] == 2
    assert "不合法" in llm.prompts[1] and "不是本章的原文块编号" in llm.prompts[1]


def test_model_call_failure_is_retried_not_fatal():
    class Flaky(ScriptedLLM):
        def ask(self, prompt, system=""):
            if not self.prompts:
                self.prompts.append(prompt)
                raise RuntimeError("429 限流")
            return super().ask(prompt, system)
    deck = repair_deck(PART, BLOCKS, "P01", None, ["x"], Flaky([_answer_from(_good_deck())]))
    assert deck["repaired_by_llm"]


def test_gives_up_only_after_max_attempts_with_the_last_problems():
    llm = ScriptedLLM(["仍然不是JSON"])
    with pytest.raises(PlanError) as ei:
        repair_deck(PART, BLOCKS, "P01", None, ["x"], llm, max_attempts=3)
    assert "3 次模型修复仍不合格" in str(ei.value) and len(llm.prompts) == 3


def test_prompt_lists_rules_source_current_plan_and_errors():
    p = build_repair_prompt("章", BLOCKS, _good_deck(), ["P01.S02：只有标题或前言，没有实际内容"], 12, 12)
    for key in ("分页标准", "[p1] 段落（前言/小标题", "[l1] 列表", "当前规划", "P01.S02：只有标题或前言", "原样取自"):
        assert key in p


# ---------- 与存储、一键流程的衔接 ----------

@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": BLOCKS})
    write_json_atomic(pdir / "plan" / "spine.json",
                      {"title": "测试", "confirmed": True,
                       "parts": [{"id": "P01", "title": "章", "role": "claim", "one_line": "", "weight": 1.0,
                                  "budget_minutes": 6.0, "section_ids": ["s01"]}]})
    return pdir


def _break_rules(monkeypatch):
    monkeypatch.setattr(deck_store, "plan_slides", lambda part, blocks, pid, **kw: _bad_deck(blocks, pid, **kw))


def test_plan_deck_repairs_with_llm_when_rules_keep_failing(project, monkeypatch):
    _break_rules(monkeypatch)
    llm = ScriptedLLM([_answer_from(_good_deck())])
    deck = plan_deck(project, "P01", llm=llm)
    assert deck["repaired_by_llm"] and load_deck(project, "P01")["slides"]
    assert any("由模型修复" in w for w in deck_status(project, "P01")["warnings"])
    assert deck_status(project, "P01")["exists"]


def test_plan_deck_without_model_still_fails_clearly(project, monkeypatch):
    _break_rules(monkeypatch)
    with pytest.raises(PlanError) as ei:
        plan_deck(project, "P01")
    assert "未配置文本模型" in str(ei.value)
    assert load_deck(project, "P01") is None                      # 校验不过不写入


def test_plan_deck_unchanged_input_is_reused_without_calling_the_model(project, monkeypatch):
    _break_rules(monkeypatch)
    plan_deck(project, "P01", llm=ScriptedLLM([_answer_from(_good_deck())]))
    llm2 = ScriptedLLM(["不应被调用"])
    plan_deck(project, "P01", llm=llm2)
    assert llm2.prompts == []


def test_g3_pipeline_passes_the_model_to_the_plan_stage(project, monkeypatch):
    from article_to_video.deck.pipeline import run_deck_pipeline
    from article_to_video.providers.tts import SilenceTTS
    from test_deck_phase2 import FakeLLM
    _break_rules(monkeypatch)
    fake = FakeLLM()
    repair = ScriptedLLM([_answer_from(_good_deck())])

    class Both:                                     # 规划修复与讲稿共用一个模型对象
        model = "both"

        def ask(self, prompt, system=""):
            return repair.ask(prompt, system) if "幻灯片编排员" in system else fake.ask(prompt, system)
    st = run_deck_pipeline(project, gear="G3", until="plan", llm=Both(), tts=SilenceTTS())
    assert st["state"] == "done", st
    assert repair.prompts and load_deck(project, "P01")["repaired_by_llm"]
