"""新方案第二阶段：每页讲稿（字数控制、数字校验、批次重做、过期与确认）。"""
import json
import re
from pathlib import Path

import pytest

from article_to_video.deck.script import (MAX_ATTEMPTS, check_batch, confirm_script, load_script, page_source,
                                          plan_script, script_status, target_chars)
from article_to_video.deck.store import confirm_deck, plan_deck
from article_to_video.planning.errors import PlanError
from article_to_video.project_store import write_json_atomic
from article_to_video.source.md_parse import parse_markdown
from helpers_p2 import make_project

FIXTURE = Path(__file__).parent / "fixtures" / "harness_article.md"
_HEAD = re.compile(r"\[(P\d+\.S\d+)\] 标题：(.*?)；目标字数：(\d+)")


class FakeLLM:
    """按提示词里的“目标字数”生成讲稿；可注入一次错误输出。"""

    def __init__(self, bad_first=False, bad_numbers=False, always_bad=False):
        self.calls = 0
        self.bad_first = bad_first
        self.bad_numbers = bad_numbers
        self.always_bad = always_bad

    def ask(self, prompt, system=""):
        self.calls += 1
        if self.always_bad:
            return json.dumps({"slides": []}, ensure_ascii=False)
        if self.bad_first and self.calls == 1:
            return "这不是 JSON"
        out = []
        for sid, title, tc in _HEAD.findall(prompt):
            body = f"{title}。" + "这一页讲解要点，" * 40
            body = body[: int(tc)]
            if self.bad_numbers and sid.endswith("S01"):
                body = body[:-4] + "2025年"
            out.append({"id": sid, "narration": body})
        return json.dumps({"slides": out}, ensure_ascii=False)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": blocks})
    from article_to_video.planning.runner import load_spine  # noqa: F401
    spine = {"title": "测试", "confirmed": True,
             "parts": [{"id": "P01", "title": "测试章", "role": "claim", "one_line": "", "weight": 1.0,
                        "budget_minutes": 6.0, "section_ids": ["s01", "s02", "s03"]}]}
    write_json_atomic(pdir / "plan" / "spine.json", spine)
    deck = plan_deck(pdir, "P01")
    confirm_deck(pdir, "P01")
    return pdir


def test_target_chars_follow_the_page_time():
    s = {"est_sec": 30.0}
    assert target_chars(s) == round((30.0 - 2.0) * 4.2)


def test_page_source_covers_items_and_table_cells():
    sl = {"title": "标题", "elements": [{"kind": "list", "items": ["甲"]},
                                       {"kind": "table", "header": ["列"], "rows": [["乙"]]}]}
    src = page_source(sl)
    assert "标题" in src and "甲" in src and "乙" in src


def test_generates_a_script_for_every_page_within_length(project):
    doc = plan_script(project, "P01", llm=FakeLLM(), part_title="测试章")
    deck = json.loads((project / "deck" / "parts" / "P01" / "deck.json").read_text(encoding="utf-8"))
    assert [x["id"] for x in doc["slides"]] == [s["id"] for s in deck["slides"]]
    for x in doc["slides"]:
        assert x["narration"] and x["chars"] == len(x["narration"])
        assert 0.6 * x["target_chars"] <= x["chars"] <= 1.4 * x["target_chars"]
    assert doc["total_chars"] == sum(x["chars"] for x in doc["slides"])


def test_a_bad_batch_is_redone_and_then_accepted(project):
    llm = FakeLLM(bad_first=True)
    plan_script(project, "P01", llm=llm)
    assert llm.calls >= 2
    assert load_script(project, "P01") is not None


def test_numbers_not_in_the_page_or_source_are_rejected(project):
    llm = FakeLLM(bad_numbers=True)
    with pytest.raises(PlanError) as exc:
        plan_script(project, "P01", llm=llm)
    assert "2025" in str(exc.value) or "没有" in str(exc.value)


def test_gives_up_after_max_attempts(project):
    llm = FakeLLM(always_bad=True)
    with pytest.raises(PlanError):
        plan_script(project, "P01", llm=llm, max_attempts=MAX_ATTEMPTS)
    assert llm.calls == MAX_ATTEMPTS


def test_script_requires_a_confirmed_deck(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": blocks})
    write_json_atomic(pdir / "plan" / "spine.json",
                      {"title": "t", "confirmed": True,
                       "parts": [{"id": "P01", "title": "章", "role": "claim", "one_line": "", "weight": 1.0,
                                  "budget_minutes": 6.0, "section_ids": ["s01"]}]})
    plan_deck(pdir, "P01")                        # 未确认
    with pytest.raises(PlanError):
        plan_script(pdir, "P01", llm=FakeLLM())


def test_reuses_script_when_inputs_unchanged_and_detects_staleness(project):
    llm = FakeLLM()
    a = plan_script(project, "P01", llm=llm)
    calls = llm.calls
    b = plan_script(project, "P01", llm=llm)
    assert b["input_sha"] == a["input_sha"] and llm.calls == calls      # 复用，不调用模型
    assert script_status(project, "P01")["stale"] is False
    deck_path = project / "deck" / "parts" / "P01" / "deck.json"
    deck = json.loads(deck_path.read_text(encoding="utf-8"))
    deck["input_sha"] = "changed"
    write_json_atomic(deck_path, deck)
    assert script_status(project, "P01")["stale"] is True
    with pytest.raises(PlanError):
        confirm_script(project, "P01")


def test_confirm_marks_the_script_confirmed(project):
    plan_script(project, "P01", llm=FakeLLM())
    doc = confirm_script(project, "P01")
    assert doc["confirmed"] is True and script_status(project, "P01")["confirmed"] is True


def test_check_batch_reports_missing_pages(project):
    deck = json.loads((project / "deck" / "parts" / "P01" / "deck.json").read_text(encoding="utf-8"))
    batch = deck["slides"][:2]
    obj = {"slides": [{"id": batch[0]["id"], "narration": "一段话。"}]}
    got, errs = check_batch(obj, batch)
    assert any("缺少讲稿" in e for e in errs)
