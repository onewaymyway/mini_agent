"""新方案第四阶段：按页渲染、合成与导出。"""
import os
from pathlib import Path

import pytest
from PIL import ImageChops

from article_to_video.compose.ffrun import ComposeError
from article_to_video.deck.compose import (_scene_for, build_segments, compose_deck_part, export_deck_video,
                                           page_state_images)
from article_to_video.deck.script import plan_script, confirm_script
from article_to_video.deck.store import confirm_deck, plan_deck
from article_to_video.deck.voice import load_deck_timeline, synthesize_deck
from article_to_video.project_store import write_json_atomic
from article_to_video.providers.tts import SilenceTTS
from article_to_video.render.themes import DEFAULT_THEME
from article_to_video.source.md_parse import parse_markdown
from helpers_p2 import make_project
from test_deck_phase2 import FIXTURE, FakeLLM


def _slide():
    return {"title": "示例页", "elements": [
        {"kind": "keypoint", "items": ["第一条要点", "第二条要点"], "emphasis": []},
        {"kind": "diagram", "items": ["甲", "乙", "丙"], "loop": False, "emphasis": []},
        {"kind": "table", "header": ["项目", "数值"], "rows": [["a", "1"], ["b", "2"]], "emphasis": []}]}


def test_each_element_adds_one_state_and_the_last_state_shows_everything():
    st = page_state_images({"title": "x"}, _slide(), (960, 540), "landscape", DEFAULT_THEME)
    assert len(st) == 3 and all(im.size == (960, 540) for im in st)
    for a, b in zip(st, st[1:]):
        assert ImageChops.difference(a, b).getbbox() is not None      # 每一步都有新内容出现


def test_table_scene_carries_one_state_per_row():
    sc = _scene_for(_slide()["elements"][2])
    assert len(sc["items"]) == 2 and sc["rows"] == [["a", "1"], ["b", "2"]]


def test_segments_cover_the_whole_timeline_exactly(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    deck = {"slides": [{"id": "P01.S01", "title": "甲", "elements": _slide()["elements"]},
                       {"id": "P01.S02", "title": "乙", "elements": _slide()["elements"][:1]}]}
    tl = {"lead_in_sec": 3.0, "total_sec": 20.0, "pages": [
        {"id": "P01.S01", "start": 3.0, "end": 9.0, "reveals": [{"element": 0, "at_sec": 3.0},
                                                             {"element": 1, "at_sec": 5.0},
                                                             {"element": 2, "at_sec": 7.0}]},
        {"id": "P01.S02", "start": 10.0, "end": 17.0, "reveals": [{"element": 0, "at_sec": 10.0}]}]}
    segs, total = build_segments(pdir, "P01", deck, tl, (960, 540), "landscape", DEFAULT_THEME,
                                 {"number": "第 1 章", "title": "测试", "one_line": ""})
    assert total == 20.0
    assert sum(d for _, d in segs) == pytest.approx(20.0, abs=1e-6)
    assert segs[0][1] == pytest.approx(3.0)                       # 章封面
    assert all(p.is_file() for p, _ in segs)


@pytest.fixture
def small_project(tmp_path, monkeypatch):
    """只用第一节（约 2 页）的完整项目：规划、讲稿、配音都已确认。"""
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": blocks})
    write_json_atomic(pdir / "plan" / "spine.json",
                      {"title": "测试文章", "confirmed": True,
                       "parts": [{"id": "P01", "title": "引言", "role": "claim", "one_line": "", "weight": 1.0,
                                  "budget_minutes": 6.0, "section_ids": ["s01"]}]})
    plan_deck(pdir, "P01")
    confirm_deck(pdir, "P01")
    plan_script(pdir, "P01", llm=FakeLLM(), part_title="引言")
    confirm_script(pdir, "P01")
    synthesize_deck(pdir, "P01", SilenceTTS())
    return pdir


def test_compose_requires_a_confirmed_plan_and_voice(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    with pytest.raises(ComposeError):
        compose_deck_part(pdir, "P01")


def test_compose_renders_a_video_whose_length_matches_the_voice(small_project):
    info = compose_deck_part(small_project, "P01")
    tl = load_deck_timeline(small_project, "P01")
    out = small_project / "parts" / "P01" / "deck_video" / "part.mp4"
    assert out.is_file() and os.path.getsize(out) > 0
    assert info["duration"] == pytest.approx(tl["total_sec"], abs=0.05)
    assert info["pages"] == len(tl["pages"])
    assert (small_project / "parts" / "P01" / "video" / "part.mp4").exists() is False   # 不写入现有方案的目录


def test_export_concatenates_parts_into_one_file(small_project):
    compose_deck_part(small_project, "P01")
    out = export_deck_video(small_project)
    assert out.is_file() and out.name.endswith("_新方案.mp4")


def test_export_refuses_when_a_part_has_no_video(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    write_json_atomic(pdir / "plan" / "spine.json",
                      {"title": "t", "confirmed": True,
                       "parts": [{"id": "P01", "title": "章", "role": "claim", "one_line": "", "weight": 1.0,
                                  "budget_minutes": 6.0, "section_ids": ["s01"]}]})
    with pytest.raises(ComposeError):
        export_deck_video(pdir)
