"""新方案第三阶段：每页讲稿配音与时间线（页停留时长、页内元素出现时刻）。"""
import json

import pytest

from article_to_video.deck.script import plan_script
from article_to_video.deck.store import confirm_deck, plan_deck
from article_to_video.deck.voice import (load_deck_timeline, page_reveals, pages_as_script, split_sentences,
                                         synthesize_deck, voice_status)
from article_to_video.planning.errors import PlanError
from article_to_video.project_store import write_json_atomic
from article_to_video.providers.tts import SilenceTTS
from article_to_video.source.md_parse import parse_markdown
from helpers_p2 import make_project
from test_deck_phase2 import FakeLLM, FIXTURE  # 复用第二阶段的模拟模型与夹具文章


@pytest.fixture
def ready(tmp_path, monkeypatch):
    """已确认的规划与讲稿（测试文章第 1–3 节）。"""
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": blocks})
    write_json_atomic(pdir / "plan" / "spine.json",
                      {"title": "t", "confirmed": True,
                       "parts": [{"id": "P01", "title": "测试章", "role": "claim", "one_line": "", "weight": 1.0,
                                  "budget_minutes": 6.0, "section_ids": ["s01", "s02", "s03"]}]})
    plan_deck(pdir, "P01")
    confirm_deck(pdir, "P01")
    plan_script(pdir, "P01", llm=FakeLLM(), part_title="测试章")
    from article_to_video.deck.script import confirm_script
    confirm_script(pdir, "P01")
    return pdir


def test_sentences_split_and_keep_their_punctuation():
    assert split_sentences("甲乙丙。丁戊己！庚辛？") == ["甲乙丙。", "丁戊己！", "庚辛？"]
    assert split_sentences("没有句末标点") == ["没有句末标点"]


def test_each_page_becomes_a_beat_with_its_sentences(ready):
    doc = json.loads((ready / "deck" / "parts" / "P01" / "deck_script.json").read_text(encoding="utf-8"))
    st = pages_as_script("P01", doc)
    assert [b["id"] for b in st["beats"]] == [p["id"] for p in doc["slides"]]
    assert all(b["pause_after_ms"] == 500 and b["sentences"] for b in st["beats"])


def test_synthesis_gives_page_durations_and_non_overlapping_pages(ready):
    tl = synthesize_deck(ready, "P01", SilenceTTS())
    assert load_deck_timeline(ready, "P01") == tl
    pages = tl["pages"]
    assert len(pages) == len(json.loads((ready / "deck" / "parts" / "P01" / "deck_script.json").read_text())["slides"])
    for a, b in zip(pages, pages[1:]):
        assert a["end"] <= b["start"] + 1e-6                       # 页与页不重叠、按顺序
    for p in pages:
        assert p["dur"] == pytest.approx(p["end"] - p["start"], abs=1e-3) and p["dur"] > 0
        assert p["sentences"][0]["start"] == pytest.approx(p["start"], abs=1e-3)


def test_page_reveals_are_monotonic_and_inside_the_page(ready):
    tl = synthesize_deck(ready, "P01", SilenceTTS())
    for p in tl["pages"]:
        ats = [r["at_sec"] for r in p["reveals"]]
        assert ats == sorted(ats)
        assert all(p["start"] - 1e-6 <= t <= p["end"] + 1e-6 for t in ats)


def test_element_with_keyword_in_narration_is_timed_at_that_sentence():
    sents = ["第一句讲背景。", "第二句讲关键的流程节点。", "第三句收尾。"]
    times = [(0.0, 2.0), (2.5, 5.0), (5.5, 7.0)]
    page = {"elements": [{"kind": "diagram", "items": ["流程节点"]}]}
    r = page_reveals(page, times, sents)
    assert 2.5 <= r[0]["at_sec"] <= 5.0                      # 落在第二句内部


def test_element_without_keyword_falls_back_by_character_share():
    sents = ["甲甲甲甲。", "乙乙乙乙。"]
    times = [(0.0, 2.0), (2.0, 4.0)]
    page = {"elements": [{"kind": "keypoint", "items": ["不存在的词"]}, {"kind": "keypoint", "items": ["也没有"]}]}
    r = page_reveals(page, times, sents)
    assert r[0]["at_sec"] <= r[1]["at_sec"]


def test_voicing_requires_confirmed_and_fresh_script(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    with pytest.raises(PlanError):
        synthesize_deck(pdir, "P01", SilenceTTS())


def test_voice_status_reports_stale_after_script_change(ready):
    synthesize_deck(ready, "P01", SilenceTTS())
    assert voice_status(ready, "P01")["stale"] is False
    doc_path = ready / "deck" / "parts" / "P01" / "deck_script.json"
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    doc["input_sha"] = "changed"
    write_json_atomic(doc_path, doc)
    assert voice_status(ready, "P01")["stale"] is True


def test_classic_timeline_file_is_not_touched(ready):
    synthesize_deck(ready, "P01", SilenceTTS())
    assert not (ready / "parts" / "P01" / "timeline.json").exists()
