"""第三期：插图由内容决定——视觉主体须具体（非抽象词）、场景与构图、提示词结构与无文字约束、窗口落镜重试。

依据 next_doc/article_to_video_storyboard_model_redesign_plan.md 第 6 节。
"""
import pytest

from article_to_video.validators.visual import validate_storyboard
from article_to_video.visual import images as imgs
from article_to_video.visual.director_llm import build_window_prompt, parse_window, beat_context
from article_to_video.visual.normalize import normalize_scene
from article_to_video.visual.prompting import (NO_TEXT, DEFAULT_COMPOSITION, PROMPT_BUILD_VERSION,
                                               check_illustration, compose_prompt, is_abstract_subject)
from helpers_shots import ShotLLM, default_shots, shot, shots_json
from test_s1_shot_structure import PART, CHAPTER, _beat, run, synth_script

ANCHOR = "扁平插画风格，暖色调"


# ---------- 视觉主体：具体 vs 抽象 ----------

def test_abstract_subjects_are_detected_and_concrete_ones_pass():
    assert is_abstract_subject("效率")
    assert is_abstract_subject("效率、趋势")
    assert not is_abstract_subject("齿轮与蒸汽")
    assert not is_abstract_subject("工厂车间里的机器")
    assert not is_abstract_subject("")


def test_check_illustration_rejects_abstract_subject_and_text_requests():
    assert any("抽象词" in e for e in check_illustration({"subject": "效率"}))
    assert any("缺少视觉主体" in e for e in check_illustration({"subject": ""}))
    assert any("禁用词" in e for e in check_illustration({"subject": "齿轮", "scene": "写上标语的墙"}))
    assert check_illustration({"subject": "齿轮与蒸汽", "scene": "工厂车间", "composition": "远景"}) == []


# ---------- 提示词：主体 + 场景 + 构图 + 风格锚 + 无文字约束 ----------

def test_prompt_has_subject_scene_composition_anchor_and_no_text_constraint():
    p = compose_prompt({"subject": "蒸汽机", "scene": "工厂车间", "composition": "远景，主体靠左"},
                       ANCHOR, "landscape")
    assert "蒸汽机" in p and "工厂车间" in p and "远景，主体靠左" in p
    assert ANCHOR in p and NO_TEXT in p and "画幅比例" in p


def test_prompt_uses_default_composition_and_omits_empty_scene():
    p = compose_prompt({"subject": "齿轮"}, ANCHOR, "landscape")
    assert DEFAULT_COMPOSITION in p and "置身于" not in p


def test_image_module_builds_prompt_with_the_new_structure_and_version():
    scene = {"image": {"subject": "齿轮", "scene": "车间", "composition": ""}}
    assert imgs.build_prompt(scene, ANCHOR, "landscape") == compose_prompt(scene["image"], ANCHOR, "landscape")
    assert PROMPT_BUILD_VERSION == "p4-img-v2"


# ---------- 落镜：插图字段、整窗重做 ----------

def _win(script, bids, obj, *, blocks=None):
    beats = [_beat(script, b) for b in bids]
    ctxs = {b["id"]: beat_context(b, blocks or []) for b in script["beats"]}
    return parse_window(obj, beats, ctxs, prev=None, ill_used=0, allowance=10, shots=2)


def test_window_keeps_scene_and_composition_for_illustrations():
    script = synth_script(n_beats=1)
    obj = {"shots": [shot("P09.b01.s01", "P09.b01.s04", "illustration", image_subject="蒸汽机",
                          image_scene="工厂车间", image_composition="远景，主体靠左")]}
    scenes, errs, n_ill = _win(script, ["P09.b01"], obj)
    assert errs == [] and n_ill == 1
    img = scenes[0]["image"]
    assert img["subject"] == "蒸汽机" and img["scene"] == "工厂车间" and img["composition"] == "远景，主体靠左"


def test_window_rejects_an_abstract_illustration_subject():
    script = synth_script(n_beats=1)
    obj = {"shots": [shot("P09.b01.s01", "P09.b01.s04", "illustration", image_subject="效率")]}
    scenes, errs, _ = _win(script, ["P09.b01"], obj)
    assert scenes == [] and any("抽象词" in e for e in errs)


def test_window_prompt_states_the_illustration_rules():
    script = synth_script(n_beats=1)
    beats = [_beat(script, "P09.b01")]
    ctxs = {b["id"]: beat_context(b, []) for b in script["beats"]}
    prompt = build_window_prompt(PART, script, beats, ctxs, {}, wid="W01", allowance=3, shots=2, ill_used=0,
                                 prev=None, issues=[])
    assert "image_scene" in prompt and "image_composition" in prompt
    assert "数据、流程、定义应使用" in prompt and "抽象词" in prompt


def test_abstract_subject_triggers_only_that_window_to_retry(tmp_path):
    script = synth_script()

    def win(prompt, wid, n):
        def special(bid, block):
            if bid != "P09.b01" or wid != "W01":
                return None
            last = f"{bid}.s04"
            subject = "效率" if n == 1 else "蒸汽机与齿轮"     # 第一次抽象，第二次具体
            return [shot(f"{bid}.s01", last, "illustration", image_subject=subject,
                         image_scene="工厂车间", image_composition="远景")]
        return shots_json(default_shots(prompt, special))

    llm = ShotLLM(win_fn=win)
    sb = run(script, llm)
    counts = {w: llm.win_calls.count(w) for w in set(llm.win_calls)}
    assert counts == {"W01": 2, "W02": 1, "W03": 1}          # 只有出错的窗口重做
    ill = next(s for s in sb["scenes"] if s.get("type") == "illustration")
    assert ill["image"]["subject"] == "蒸汽机与齿轮" and ill["image"]["scene"] == "工厂车间"


# ---------- 分镜校验与编辑规范化 ----------

def _ill_scene(image, sid="P09.shot01"):
    return {"id": sid, "beat": "P09.b01", "type": "illustration", "kind": "illustration", "items": [],
            "image": dict({"leader": True, "group": "G01", "focus_x": 0.5}, **image),
            "reveal": [{"step": 0, "at_sentence": 0}], "emphasis": [], "refs": [], "sentences": []}


def _verdict(scene):
    script = synth_script(n_beats=1)
    return validate_storyboard({"scenes": [scene], "source_structure": []}, script, allowance=5,
                               style_anchor=ANCHOR, images_enabled=True, shots_per_image=2)


def test_chapter_validator_checks_subject_scene_and_composition():
    assert _verdict(_ill_scene({"subject": "齿轮"}))["errors"] == []
    assert any("抽象词" in e for e in _verdict(_ill_scene({"subject": "效率"}))["errors"])
    assert any("禁用词" in e for e in _verdict(_ill_scene({"subject": "齿轮", "composition": "写上标题"}))["errors"])


def test_normalize_keeps_scene_and_composition():
    script = synth_script(n_beats=1)
    raw = {"beat": "P09.b01", "type": "illustration", "image": {"subject": "齿轮", "scene": "车间",
                                                             "composition": "近景"}}
    sc = normalize_scene(raw, script["beats"][0]["sentences"])
    assert sc["image"]["scene"] == "车间" and sc["image"]["composition"] == "近景"
