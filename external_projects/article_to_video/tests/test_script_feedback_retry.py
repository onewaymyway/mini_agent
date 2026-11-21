"""讲稿校验失败：反馈具体到页（字数、数字），只重做失败页；全自动挡位持续修改直到通过。"""
import json

import pytest

from article_to_video.deck.script import (AUTO_ATTEMPTS, MAX_ATTEMPTS, build_prompt, check_batch, plan_script,
                                          slide_problems, target_chars)
from article_to_video.deck.store import confirm_deck, plan_deck
from article_to_video.planning.errors import PlanError
from article_to_video.project_store import write_json_atomic
from article_to_video.source.md_parse import parse_markdown
from helpers_p2 import make_project
from test_deck_phase2 import FIXTURE, _HEAD, FakeLLM


def _slide(tc_sec=30.0):
    return {"id": "P01.S01", "title": "页", "est_sec": tc_sec,
            "elements": [{"kind": "keypoint", "items": ["第一要点的内容", "第二要点的内容"]}]}


def _body(tc):
    return "页面讲稿内容的一段话，" * 20


class ScriptedLLM:
    """按脚本返回：每次调用由 responder(prompt, n) 生成输出，n 为调用序号（从 1 开始）。"""

    def __init__(self, responder):
        self.responder = responder
        self.calls = 0
        self.prompts = []

    def ask(self, prompt, system=""):
        self.calls += 1
        self.prompts.append(prompt)
        return self.responder(prompt, self.calls)


def _respond_good(prompt, n, short_until=0, numbers_until=0):
    out = []
    for sid, title, tc in _HEAD.findall(prompt):
        body = (f"{title}。" + "这一页讲解要点，" * 40)[: int(tc)]
        if n <= short_until and sid.endswith("S01"):
            body = "太短。"
        if n <= numbers_until and sid.endswith("S02"):
            body = body[:-2] + "2025年"
        out.append({"id": sid, "narration": body})
    return json.dumps({"slides": out}, ensure_ascii=False)


def test_problems_say_exactly_what_is_wrong():
    s = _slide()
    tc = target_chars(s)
    probs = slide_problems("短。", s)
    assert any("太短" in p and str(tc) in p for p in probs)
    assert slide_problems("字" * tc, s) == []
    assert any("数字" in p for p in slide_problems("字" * tc + "2025", s))


def test_feedback_is_given_per_page_with_length_and_fix_instruction():
    s = _slide()
    prompt = build_prompt("章", [s], {s["id"]: ["讲稿只有 3 字，太短（目标约 47 字，需要 28–65 字）"]})
    assert "上一次输出的问题" in prompt and "太短" in prompt
    assert "扩写" in prompt and "28–65" in prompt


def test_only_the_failing_page_is_redone_and_good_pages_are_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": blocks})
    write_json_atomic(pdir / "plan" / "spine.json",
                      {"title": "t", "confirmed": True,
                       "parts": [{"id": "P01", "title": "章", "role": "claim", "one_line": "", "weight": 1.0,
                                  "budget_minutes": 6.0, "section_ids": ["s01", "s02", "s03"]}]})
    plan_deck(pdir, "P01")
    confirm_deck(pdir, "P01")
    llm = ScriptedLLM(lambda p, n: _respond_good(p, n, short_until=1))
    plan_script(pdir, "P01", llm=llm)
    assert llm.calls == 2
    second = llm.prompts[1]
    assert "[P01.S01]" in second and "[P01.S02]" not in second               # 第二次只带失败页，合格页不重做


def test_full_auto_keeps_retrying_past_the_manual_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": blocks})
    write_json_atomic(pdir / "plan" / "spine.json",
                      {"title": "t", "confirmed": True,
                       "parts": [{"id": "P01", "title": "章", "role": "claim", "one_line": "", "weight": 1.0,
                                  "budget_minutes": 6.0, "section_ids": ["s01"]}]})
    plan_deck(pdir, "P01")
    confirm_deck(pdir, "P01")
    llm = ScriptedLLM(lambda p, n: _respond_good(p, n, short_until=5))     # 前 5 次讲稿都太短
    doc = plan_script(pdir, "P01", llm=llm, max_attempts=AUTO_ATTEMPTS)     # 全自动：持续修改直到通过
    assert llm.calls == 6 and llm.calls > MAX_ATTEMPTS
    assert doc["slides"] and all(x["narration"] for x in doc["slides"])


def test_manual_gear_stops_after_its_limit_with_the_specific_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": blocks})
    write_json_atomic(pdir / "plan" / "spine.json",
                      {"title": "t", "confirmed": True,
                       "parts": [{"id": "P01", "title": "章", "role": "claim", "one_line": "", "weight": 1.0,
                                  "budget_minutes": 6.0, "section_ids": ["s01"]}]})
    plan_deck(pdir, "P01")
    confirm_deck(pdir, "P01")
    llm = ScriptedLLM(lambda p, n: _respond_good(p, n, short_until=99))
    with pytest.raises(PlanError) as exc:
        plan_script(pdir, "P01", llm=llm, max_attempts=MAX_ATTEMPTS)
    assert llm.calls == MAX_ATTEMPTS
    assert "太短" in str(exc.value) and "P01.S01" in str(exc.value)
