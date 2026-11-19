"""S1（第一期）：模型版分镜的镜头结构——分窗、覆盖、局部修复、保留与合并、流程与图表校验、缓存、
渲染时间段、字幕映射、编辑规范化、校验器的覆盖规则。依据 next_doc/article_to_video_storyboard_model_redesign_plan.md 第 4 节。
"""
import copy
import json
import re

import pytest

from article_to_video.compose.subtitles import subtitle_plan
from article_to_video.render.compose_part import _scene_span
from article_to_video.validators.visual import validate_storyboard
from article_to_video.visual.director_llm import (LLM_PROMPT_VERSION, beat_context, direct_part_llm,
                                                  parse_plan, parse_window)
from article_to_video.visual.normalize import normalize_scene
from article_to_video.visual.shots import plan_windows, reveal_in_range, sentence_id, shot_range
from helpers_shots import ShotLLM, default_plan, default_shots, shot, shots_json

CHAPTER = {"number": "第 9 章", "title": "测试章", "one_line": ""}
FILLER = "这是一段用于测试的填充内容。" * 19   # 不含数字与英文；每段约 1100 字，两段一窗


def synth_script(part="P09", n_beats=6, per_beat=4, quotes=(), chain_beats=(), chain_ref="blk1",
                 extra_text=None):
    beats = []
    for b in range(1, n_beats + 1):
        bid = f"{part}.b{b:02d}"
        sents = []
        for i in range(1, per_beat + 1):
            text = f"第{b}段第{i}句。" + FILLER
            if extra_text and (bid, i) in extra_text:
                text = extra_text[(bid, i)]
            sents.append({"text": text, "quote": (bid, i) in quotes, "emphasis": []})
        refs = [chain_ref] if bid in chain_beats else []
        beats.append({"id": bid, "role": "claim", "source_refs": refs, "sentences": sents})
    return {"part": part, "title": "测试章", "beats": beats}


PART = {"id": "P09", "title": "测试章", "budget_minutes": 3.0}


def run(script, llm, *, cache_dir=None, force=False, allowance=10, shots=2, blocks=None):
    return direct_part_llm(script, blocks or [], part=PART, chapter=CHAPTER, allowance=allowance,
                           shots_per_image=shots, llm=llm, images_enabled=True, style_anchor="无文字",
                           cache_dir=cache_dir, force=force)


def _beat(script, bid):
    return next(b for b in script["beats"] if b["id"] == bid)


# ---------- 分窗 ----------

def test_windows_follow_paragraph_boundaries_and_budget():
    script = synth_script(n_beats=6)
    wins = plan_windows(script["beats"])
    assert [[b["id"] for b in w] for w in wins] == [
        ["P09.b01", "P09.b02"], ["P09.b03", "P09.b04"], ["P09.b05", "P09.b06"]]
    for w in wins:
        assert sum(len(s["text"]) for b in w for s in b["sentences"]) <= 2500


def test_a_single_long_paragraph_forms_its_own_window():
    script = synth_script(n_beats=2, per_beat=40)       # 单段超过 2500 字
    wins = plan_windows(script["beats"])
    assert [len(w) for w in wins] == [1, 1]


# ---------- 覆盖与整章结构 ----------

def test_storyboard_covers_every_sentence_with_contiguous_shots():
    script = synth_script()
    llm = ShotLLM()
    sb = run(script, llm)
    assert sb["schema"] == 2 and sb["director"] == LLM_PROMPT_VERSION
    assert [s["id"] for s in sb["scenes"]] == [f"P09.shot{i:02d}" for i in range(1, len(sb["scenes"]) + 1)]
    for b in script["beats"]:
        covered = []
        for s in sb["scenes"]:
            if s["beat"] == b["id"]:
                a, c = shot_range(s, len(b["sentences"]))
                covered += list(range(a, c + 1))
        assert covered == list(range(len(b["sentences"])))      # 连续、不重叠、不遗漏
    assert llm.plan_calls == 1 and len(sb["windows"]) == 3
    assert llm.calls == 1 + 3
    assert set(sb["plan"]["beats"]) == {b["id"] for b in script["beats"]}


def test_chapter_validator_rejects_gaps_overlaps_and_wrong_order():
    script = synth_script(n_beats=1)
    def sc(a, b, bid="P09.b01"):
        return {"id": f"x{a}{b}", "beat": bid, "type": "keypoint", "kind": "keypoint",
                "sentences": [sentence_id(bid, a), sentence_id(bid, b)], "items": ["甲"],
                "reveal": [{"step": 0, "at_sentence": a}], "emphasis": [], "refs": [], "image": None}
    base = dict(allowance=5, style_anchor="无文字", images_enabled=True, shots_per_image=2)
    ok = validate_storyboard({"scenes": [sc(0, 1), sc(2, 3)], "source_structure": []}, script, **base)
    assert ok["errors"] == []
    gap = validate_storyboard({"scenes": [sc(0, 1), sc(3, 3)], "source_structure": []}, script, **base)
    assert any("遗漏或重叠" in e for e in gap["errors"])
    overlap = validate_storyboard({"scenes": [sc(0, 2), sc(2, 3)], "source_structure": []}, script, **base)
    assert any("遗漏或重叠" in e for e in overlap["errors"])
    short = validate_storyboard({"scenes": [sc(0, 1)], "source_structure": []}, script, **base)
    assert any("末尾句子" in e or "未覆盖" in e or "只覆盖" in e for e in short["errors"])


def test_legacy_whole_paragraph_scene_is_still_valid():
    script = synth_script(n_beats=1)
    legacy = {"id": "P09.b01", "beat": "P09.b01", "type": "keypoint", "items": ["甲"],
              "reveal": [{"step": 0, "at_sentence": 0}], "emphasis": [], "refs": [], "image": None}
    v = validate_storyboard({"scenes": [legacy], "source_structure": []}, script, allowance=5,
                            style_anchor="无文字", images_enabled=True, shots_per_image=2)
    assert v["errors"] == []


def test_validator_warns_for_long_runs_of_one_type_not_errors():
    script = synth_script(n_beats=4)
    scenes = [{"id": f"s{i}", "beat": b["id"], "type": "keypoint", "kind": "keypoint", "items": ["甲"],
               "reveal": [{"step": 0, "at_sentence": 0}], "emphasis": [], "refs": [], "image": None}
              for i, b in enumerate(script["beats"])]
    v = validate_storyboard({"scenes": scenes, "source_structure": []}, script, allowance=5,
                            style_anchor="无文字", images_enabled=True, shots_per_image=2)
    assert v["errors"] == [] and any("连续 4 个" in w for w in v["warnings"])


# ---------- 局部修复与缓存 ----------

def test_failing_window_is_retried_alone_and_earlier_windows_are_kept(tmp_path):
    script = synth_script()
    cache = tmp_path / "director_cache"

    def bad_w2(prompt, wid, n):
        shots = default_shots(prompt)
        if wid == "W02" and n == 1:
            shots = shots[:-1]                    # 漏掉窗口末尾的句子：覆盖错误
        return shots_json(shots)

    llm = ShotLLM(win_fn=bad_w2)
    run(script, llm, cache_dir=cache)
    counts = {w: llm.win_calls.count(w) for w in set(llm.win_calls)}
    assert counts == {"W01": 1, "W02": 2, "W03": 1}     # 只有出错的窗口被重做


def test_unchanged_inputs_reuse_cache_and_force_regenerates(tmp_path):
    script = synth_script()
    cache = tmp_path / "director_cache"
    first = ShotLLM()
    run(script, first, cache_dir=cache)
    assert first.calls == 4                               # 策划 1 + 三窗
    again = ShotLLM()
    run(script, again, cache_dir=cache)
    assert again.calls == 0                               # 全部复用
    forced = ShotLLM()
    run(script, forced, cache_dir=cache, force=True)
    assert forced.calls == 4                              # 强制：忽略缓存


def test_changing_one_paragraph_recalls_only_its_window(tmp_path):
    script = synth_script()
    cache = tmp_path / "director_cache"
    run(script, ShotLLM(), cache_dir=cache)
    changed = copy.deepcopy(script)
    changed["beats"][4]["sentences"][1]["text"] = "第5段改动后的第2句。" + FILLER   # 只改第三窗口的一句
    llm = ShotLLM()
    run(changed, llm, cache_dir=cache)
    assert llm.plan_calls == 1                            # 输入指纹变化，策划重算一次（很便宜）
    assert llm.win_calls == ["W03"]                       # 只有改动的窗口重新落镜


def test_a_failed_window_keeps_the_successful_ones_for_next_time(tmp_path):
    script = synth_script()
    cache = tmp_path / "director_cache"

    def always_bad_w2(prompt, wid, n):
        if wid == "W02":
            return "坏输出"
        return shots_json(default_shots(prompt))

    from article_to_video.planning.errors import PlanError
    with pytest.raises(PlanError):
        run(script, ShotLLM(win_fn=always_bad_w2), cache_dir=cache)
    llm = ShotLLM()
    run(script, llm, cache_dir=cache)
    assert llm.plan_calls == 0                            # 策划已缓存
    assert llm.win_calls == ["W02", "W03"]                # W01 复用，只做未完成的窗口


# ---------- 镜头类型：保留与合并、流程、图表、金句、预算 ----------

def _win(script, bid_list, obj, *, ill_used=0, allowance=10, shots=2, blocks=None, prev=None):
    beats = [_beat(script, b) for b in bid_list]
    ctxs = {b["id"]: beat_context(b, blocks or []) for b in script["beats"]}
    return parse_window(obj, beats, ctxs, prev=prev, ill_used=ill_used, allowance=allowance, shots=shots)


def test_hold_is_merged_into_the_previous_shot_of_the_same_paragraph():
    script = synth_script(n_beats=1)
    obj = {"shots": [shot("P09.b01.s01", "P09.b01.s02", "keypoint", items=["甲"]),
                     shot("P09.b01.s03", "P09.b01.s04", "hold")]}
    scenes, errs, _ = _win(script, ["P09.b01"], obj)
    assert errs == [] and len(scenes) == 1
    assert scenes[0]["sentences"] == ["P09.b01.s01", "P09.b01.s04"]
    assert reveal_in_range(scenes[0], 0, 3)


def test_hold_at_the_start_of_a_paragraph_falls_back_to_a_card():
    script = synth_script(n_beats=1)
    obj = {"shots": [shot("P09.b01.s01", "P09.b01.s04", "hold")]}
    scenes, errs, _ = _win(script, ["P09.b01"], obj)
    assert errs == [] and scenes[0]["type"] == "keypoint" and scenes[0]["fallback_from"] == "hold"


def test_shots_must_cover_the_window_without_gaps_or_crossing_paragraphs():
    script = synth_script(n_beats=2)
    gap = {"shots": [shot("P09.b01.s01", "P09.b01.s02", "keypoint", items=["甲"]),
                     shot("P09.b01.s04", "P09.b01.s04", "keypoint", items=["乙"])]}
    _s, errs, _ = _win(script, ["P09.b01", "P09.b02"], gap)
    assert any("未被覆盖" in e or "重叠" in e for e in errs)
    cross = {"shots": [shot("P09.b01.s01", "P09.b02.s01", "keypoint", items=["甲"])]}
    _s, errs, _ = _win(script, ["P09.b01", "P09.b02"], cross)
    assert any("跨段" in e for e in errs)


def test_flow_nodes_must_appear_in_the_shot_sentences():
    script = synth_script(n_beats=1, chain_beats=("P09.b01",),
                          extra_text={("P09.b01", 3): "AgentOS 负责调度。", ("P09.b01", 4): "模型与工具协同工作。"})
    blocks = [{"id": "blk1", "kind": "code", "shape": "chain", "nodes": ["AgentOS", "模型", "工具"], "loop": False}]
    too_short = {"shots": [shot("P09.b01.s01", "P09.b01.s02", "flow"), shot("P09.b01.s03", "P09.b01.s04", "keypoint", items=["甲"])]}
    _s, errs, _ = _win(script, ["P09.b01"], too_short, blocks=blocks)
    assert any("流程节点" in e and "未在本镜头" in e for e in errs)
    ok = {"shots": [shot("P09.b01.s01", "P09.b01.s04", "flow", items=["编造"])]}
    scenes, errs, _ = _win(script, ["P09.b01"], ok, blocks=blocks)
    assert errs == [] and scenes[0]["type"] == "diagram"
    assert scenes[0]["items"] == ["AgentOS", "模型", "工具"]         # 节点来自原文，不取模型的条目
    times = [r["at_sentence"] for r in scenes[0]["reveal"]]
    assert times == sorted(times) and all(0 <= t <= 3 for t in times)


def test_chart_labels_must_be_said_and_values_must_come_from_the_source():
    script = synth_script(n_beats=1, extra_text={("P09.b01", 1): "今年收入是3亿元。",
                                                 ("P09.b01", 2): "成本为2亿元。"})
    tail = shot("P09.b01.s03", "P09.b01.s04", "keypoint", items=["补充说明"])
    good = {"shots": [shot("P09.b01.s01", "P09.b01.s02", "chart",
                           data=[{"label": "收入", "value": "3亿"}, {"label": "成本", "value": "2亿"}]), tail]}
    scenes, errs, _ = _win(script, ["P09.b01"], good)
    assert errs == [] and scenes[0]["type"] == "table" and scenes[0]["header"] == ["项目", "数值"]
    bad_value = {"shots": [shot("P09.b01.s01", "P09.b01.s02", "chart",
                                data=[{"label": "收入", "value": "9亿"}, {"label": "成本", "value": "2亿"}]), tail]}
    _s, errs, _ = _win(script, ["P09.b01"], bad_value)
    assert any("数值" in e for e in errs)
    bad_label = {"shots": [shot("P09.b01.s01", "P09.b01.s02", "chart",
                                data=[{"label": "利润", "value": "3亿"}, {"label": "成本", "value": "2亿"}]), tail]}
    _s, errs, _ = _win(script, ["P09.b01"], bad_label)
    assert any("利润" in e for e in errs)


def test_quote_shot_is_built_from_the_quoted_sentence():
    script = synth_script(n_beats=1, quotes=(("P09.b01", 2),))
    obj = {"shots": [shot("P09.b01.s01", "P09.b01.s01", "keypoint", items=["甲"]),
                     shot("P09.b01.s02", "P09.b01.s02", "quote"),
                     shot("P09.b01.s03", "P09.b01.s04", "keypoint", items=["乙"])]}
    scenes, errs, _ = _win(script, ["P09.b01"], obj)
    assert errs == []
    q = scenes[1]
    assert q["type"] == "quote" and q["items"] == [script["beats"][0]["sentences"][1]["text"][:60]]
    assert len(q["reveal"]) == 1 and q["reveal"][0]["at_sentence"] == 1


def test_illustrations_are_counted_against_the_remaining_image_budget():
    script = synth_script(n_beats=1)
    obj = {"shots": [shot("P09.b01.s01", "P09.b01.s02", "illustration", image_subject="蒸汽机"),
                     shot("P09.b01.s03", "P09.b01.s04", "illustration", image_subject="齿轮")]}
    _s, errs, n_ill = _win(script, ["P09.b01"], obj, allowance=1, shots=1)
    assert any("图片额度余量" in e for e in errs) and n_ill == 2
    _s, errs, _ = _win(script, ["P09.b01"], obj, allowance=2, shots=2)
    assert errs == []


def test_plan_drops_points_not_in_the_source_and_keeps_roles():
    script = synth_script(n_beats=1)
    ctxs = {b["id"]: beat_context(b, []) for b in script["beats"]}
    obj = {"beats": [{"beat": "P09.b01", "role": "data", "points": ["第1段", "增长 999 倍"]}]}
    plan, errs, drops = parse_plan(obj, script, ctxs)
    assert errs == [] and plan["P09.b01"]["role"] == "data"
    assert plan["P09.b01"]["points"] == ["第1段"] and any("999" in d for d in drops)


# ---------- 渲染与字幕 ----------

def test_renderer_places_a_shot_by_its_first_and_last_sentence():
    starts = {f"P09.b01.s{i:02d}": (float(i - 1), float(i), 0.1) for i in range(1, 5)}
    shot_sc = {"beat": "P09.b01", "id": "x", "sentences": ["P09.b01.s02", "P09.b01.s03"]}
    assert _scene_span(starts, shot_sc, 4) == pytest.approx((1.0, 3.1))
    legacy = {"beat": "P09.b01", "id": "y"}
    assert _scene_span(starts, legacy, 4) == pytest.approx((0.0, 4.1))


def test_subtitles_are_matched_to_the_shot_that_contains_each_sentence():
    script = synth_script(n_beats=1)
    s = [x["text"] for x in script["beats"][0]["sentences"]]
    timeline = {"sentences": [{"id": f"P09.b01.s{i:02d}", "start": float(i - 1), "end": float(i) - 0.1,
                               "beat": "P09.b01", "text": s[i - 1]} for i in range(1, 5)]}
    storyboard = {"scenes": [
        {"beat": "P09.b01", "type": "keypoint", "sentences": ["P09.b01.s01", "P09.b01.s02"],
         "items": [s[0], s[1]]},
        {"beat": "P09.b01", "type": "keypoint", "sentences": ["P09.b01.s03", "P09.b01.s04"],
         "items": ["无关的卡片"]},
    ]}
    plan = subtitle_plan(timeline, script, storyboard, "auto")
    visible = {p["id"]: p["visible"] for p in plan}
    assert visible["P09.b01.s01"] is False and visible["P09.b01.s02"] is False   # 第一镜已完整呈现
    assert visible["P09.b01.s03"] is True and visible["P09.b01.s04"] is True     # 第二镜未呈现


# ---------- 编辑规范化 ----------

def test_normalize_keeps_the_shot_range_and_recomputes_reveal_within_it():
    script = synth_script(n_beats=1)
    sents = script["beats"][0]["sentences"]
    raw = {"beat": "P09.b01", "type": "keypoint", "sentences": ["P09.b01.s02", "P09.b01.s03"],
           "items": ["甲", "乙", "丙"], "reveal": [], "kind": "keypoint"}
    sc = normalize_scene(raw, sents)
    assert sc["sentences"] == ["P09.b01.s02", "P09.b01.s03"] and sc["kind"] == "keypoint"
    assert reveal_in_range(sc, 1, 2)


def test_normalize_follows_the_picture_type_when_the_kind_disagrees():
    script = synth_script(n_beats=1)
    raw = {"beat": "P09.b01", "type": "table", "kind": "keypoint", "header": ["a", "b"],
           "rows": [["1", "2"]], "items": []}
    sc = normalize_scene(raw, script["beats"][0]["sentences"])
    assert sc["kind"] == "table" and sc["items"] == ["1、2"]


def test_planner_output_for_unknown_beat_is_rejected_by_parse_plan():
    script = synth_script(n_beats=1)
    ctxs = {b["id"]: beat_context(b, []) for b in script["beats"]}
    _p, errs, _d = parse_plan({"beats": [{"beat": "P09.b99", "role": "data", "points": []}]}, script, ctxs)
    assert any("不存在的段落" in e for e in errs)
