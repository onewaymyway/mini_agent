"""漫画改进 v2 · P5：镜头表字段、越轴检查、旁白约束、结构化提示词。"""

import pytest

from world_forge import comic_layout as cl
from world_forge import engine, generate


def _panel(**kw):
    base = {"visual_en": "a stone hall", "shot": "中景", "span": 1, "dialogue": [], "narration": "",
            "characters": []}
    base.update(kw)
    return base


def _pages(*panels):
    return [{"no": 1, "panels": list(panels)}]


# ---------- 越轴

def test_flipping_two_characters_without_reverse_shot_is_an_axis_violation():
    a = _panel(characters=["甲", "乙"], screen_order=["甲", "乙"])
    b = _panel(characters=["甲", "乙"], screen_order=["乙", "甲"], camera="平视")
    errs = generate._validate_storyboard({"pages": _pages(a, b)})
    assert any("越轴" in e for e in errs)


def test_flipping_is_allowed_for_reverse_shot():
    a = _panel(characters=["甲", "乙"], screen_order=["甲", "乙"])
    b = _panel(characters=["甲", "乙"], screen_order=["乙", "甲"], camera="反打")
    assert generate._validate_storyboard({"pages": _pages(a, b)}) == []


def test_same_order_across_panels_passes_and_single_character_is_ignored():
    a = _panel(characters=["甲", "乙"], screen_order=["甲", "乙"])
    b = _panel(characters=["甲", "乙"], screen_order=["甲", "乙"])
    c = _panel(characters=["甲"], screen_order=[])
    assert generate._validate_storyboard({"pages": _pages(a, b, c)}) == []


def test_axis_check_crosses_page_boundaries():
    a = _panel(characters=["甲", "乙"], screen_order=["甲", "乙"])
    b = _panel(characters=["甲", "乙"], screen_order=["乙", "甲"])
    pages = [{"no": 1, "panels": [a]}, {"no": 2, "panels": [b]}]
    assert any("越轴" in e for e in generate._validate_storyboard({"pages": pages}))


def test_screen_order_must_name_present_characters():
    p = _panel(characters=["甲", "乙"], screen_order=["甲", "丙"])
    generate.normalize_panel(p)
    assert p["screen_order"] == []   # 只剩一个有效名字，视为无左右关系


# ---------- 字段规范化与旁白

def test_invalid_camera_and_gaze_values_are_replaced_or_dropped():
    p = _panel(camera="乱写", gaze={"甲": "朝右", "乙": "跳舞"}, characters=["甲", "乙"])
    assert generate.normalize_panel(p) == []
    assert p["camera"] == "平视"
    assert p["gaze"] == {"甲": "朝右"}


def test_narration_longer_than_limit_is_rejected():
    assert generate.normalize_panel(_panel(narration="字" * (cl.NARRATION_MAX + 1)))
    assert generate.normalize_panel(_panel(narration="字" * cl.NARRATION_MAX)) == []


def test_new_fields_have_safe_defaults():
    p = _panel()
    generate.normalize_panel(p)
    assert p["camera"] == "平视" and p["gaze"] == {} and p["screen_order"] == []
    assert p["action"] == "" and p["composition"] == "" and p["connects_prev"] == ""


# ---------- 结构化提示词

def test_panel_prompt_carries_camera_action_gaze_layout_and_composition():
    pn = _panel(camera="过肩", action="举起油灯", composition="主体居左，右上留白",
                gaze={"甲": "朝右"}, screen_order=["甲", "乙"], visual_en="a stone hall")
    prompt = engine._panel_prompt("style", pn, "甲: tall man", "warm amber")
    assert "over-the-shoulder shot" in prompt
    assert "Action: 举起油灯" in prompt
    assert "Composition: 主体居左，右上留白" in prompt
    assert "甲 looking right" in prompt
    assert "Screen layout from left to right: 甲, 乙" in prompt
    assert "Background: a stone hall" in prompt
    assert "Color palette: warm amber" in prompt
    assert "clear area for speech bubbles" in prompt


def test_panel_prompt_without_structure_still_builds():
    prompt = engine._panel_prompt("style", _panel(), "", "")
    assert "Action:" not in prompt and "Gaze:" not in prompt and prompt.endswith(".")


def test_stub_storyboard_passes_axis_and_narration_checks(world, llm):
    from world_forge.providers.text import RuleTextLLM
    from world_forge import generate as g
    board = g.storyboard_draft(RuleTextLLM(), context={"beats": [{"no": 1, "purpose": "铺垫：开场", "focus": "x" * 80,
                                                                   "hook": ""}],
                                                        "characters": [{"name": "守灯人", "summary": ""}]},
                               pages=1)
    for pg in board["pages"]:
        for pn in pg["panels"]:
            assert len(pn["narration"]) <= cl.NARRATION_MAX
