"""模型版分镜按真实配音时间定时：要点在它被讲到的句子出现（at_sentence），并记录句内位置（at_frac）；
时间线进入提示词与缓存键；配音变化使分镜过期；渲染按 at_frac 换算出现时刻。"""
import json

from article_to_video.render.compose_part import _frame_times
from article_to_video.visual.director_llm import (_window_payload, beat_context, build_window_prompt)
from article_to_video.visual.shots import item_frac, scene_reveal
from article_to_video.visual.storyboard import _input_sha, _timing_digest

TEXTS = ["视觉是一个很好的例子。", "我们每天看到的世界包含海量信息。人脸、光线、颜色。", "大脑真正做的事情不是保存世界。"]


def _kp(items):
    return {"type": "keypoint", "items": items, "emphasis": []}


def test_item_appears_in_the_sentence_where_it_is_spoken():
    sc = _kp(["海量信息", "大脑真正做的事情"])
    rev = scene_reveal(sc, TEXTS, start=3)
    assert [r["at_sentence"] for r in rev] == [3 + 1, 3 + 2]     # 第二句、第三句
    assert rev[0]["at_frac"] == round(TEXTS[1].find("海量信息") / len(TEXTS[1]), 3)
    assert rev[1]["at_frac"] == 0.0                                 # 句首


def test_offset_inside_one_sentence_follows_text_position():
    sc = _kp(["人脸", "颜色"])
    rev = scene_reveal(sc, TEXTS, start=0)
    assert rev[0]["at_sentence"] == rev[1]["at_sentence"] == 1
    assert rev[0]["at_frac"] < rev[1]["at_frac"]                    # 人脸先于颜色


def test_unmentioned_item_falls_back_and_never_goes_backwards():
    sc = _kp(["大脑真正做的事情", "原文没有的说法", "海量信息"])
    rev = scene_reveal(sc, TEXTS, start=0)
    at = [r["at_sentence"] for r in rev]
    assert at == sorted(at)                                          # 讲到顺序不倒退
    assert rev[1]["at_frac"] == 0.0


def test_other_types_keep_uniform_reveal_without_offset():
    sc = {"type": "quote", "items": [TEXTS[0]], "emphasis": []}
    rev = scene_reveal(sc, TEXTS, start=0)
    assert all("at_frac" not in r for r in rev)


def test_item_frac_is_relative_position():
    assert item_frac("海量", "我们每天看到的世界包含海量信息。") == round(11 / 16, 3)
    assert item_frac("不存在", "甲乙丙") == 0.0


def test_render_uses_offset_inside_the_sentence():
    starts = {"P01.b01.s02": (10.0, 14.0, 0.2)}
    scene = {"beat": "P01.b01", "reveal": [{"step": 0, "at_sentence": 1, "at_frac": 0.5},
                                           {"step": 1, "at_sentence": 1, "at_frac": 0.25}]}
    t = _frame_times(scene, starts, 9.0)
    assert t[0] == 12.0                       # 句内一半处
    assert t[1] == 12.0                       # 0.25 < 0.5，不倒退，取前一个时刻


def test_render_keeps_old_uniform_split_without_offsets():
    starts = {"P01.b01.s01": (0.0, 4.0, 0.0)}
    scene = {"beat": "P01.b01", "reveal": [{"step": 0, "at_sentence": 0}, {"step": 1, "at_sentence": 0}]}
    assert _frame_times(scene, starts, 0.0) == [0.0, 2.0]


def _beat(bid, texts):
    return {"id": bid, "role": "claim", "source_refs": [], "sentences": [{"text": t, "quote": False} for t in texts]}


def test_window_prompt_states_real_durations_and_timing_rules():
    beat = _beat("P01.b01", ["甲甲甲。", "乙乙乙。"])
    script = {"part": "P01", "beats": [beat]}
    part = {"title": "测试"}
    ctx = beat_context(beat, [])
    ctx["timing"] = {"P01.b01.s01": (3.5, 6.0), "P01.b01.s02": (6.2, 9.0)}
    prompt = build_window_prompt(part, script, [beat], {beat["id"]: ctx}, {}, wid="W1", allowance=2, shots=2,
                                 ill_used=0, prev=None, issues=[])
    assert "配音 3.5–6.0 秒，时长 2.5 秒" in prompt
    assert "时间规则" in prompt and "T2" in prompt


def test_window_prompt_without_timing_has_no_timing_rules():
    beat = _beat("P01.b01", ["甲甲甲。"])
    script = {"part": "P01", "beats": [beat]}
    ctx = beat_context(beat, [])
    prompt = build_window_prompt({"title": "测试"}, script, [beat], {beat["id"]: ctx}, {}, wid="W1", allowance=2,
                                 shots=2, ill_used=0, prev=None, issues=[])
    assert "时间规则" not in prompt and "配音" not in prompt


def test_timing_is_part_of_the_window_cache_key():
    beat = _beat("P01.b01", ["甲甲甲。"])
    ctx_a = beat_context(beat, [])
    ctx_b = beat_context(beat, [])
    ctx_a["timing"] = {"P01.b01.s01": (0.0, 3.0)}
    ctx_b["timing"] = {"P01.b01.s01": (0.0, 4.5)}
    pa = _window_payload([beat], {beat["id"]: ctx_a}, {}, allowance=1, shots=2, ill_used=0, prev=None)
    pb = _window_payload([beat], {beat["id"]: ctx_b}, {}, allowance=1, shots=2, ill_used=0, prev=None)
    assert json.dumps(pa, sort_keys=True) != json.dumps(pb, sort_keys=True)


def test_reallocating_voice_makes_llm_storyboard_stale(tmp_path):
    part = {"budget_minutes": 6.0}
    script = {"part": "P01", "beats": []}
    cfg = {"images": {}}
    tl_path = tmp_path / "parts" / "P01" / "timeline.json"
    tl_path.parent.mkdir(parents=True)
    tl_path.write_text(json.dumps({"sentences": [{"id": "P01.b01.s01", "start": 3.5, "end": 9.0}]}), encoding="utf-8")
    before = _input_sha(tmp_path, "P01", script, part, cfg, "llm")
    tl_path.write_text(json.dumps({"sentences": [{"id": "P01.b01.s01", "start": 3.5, "end": 9.8}]}), encoding="utf-8")
    after = _input_sha(tmp_path, "P01", script, part, cfg, "llm")
    assert before != after
    assert _timing_digest(tmp_path, "P01") is not None


def test_rules_engine_fingerprint_ignores_timing(tmp_path):
    part = {"budget_minutes": 6.0}
    script = {"part": "P01", "beats": []}
    cfg = {"images": {}}
    (tmp_path / "parts" / "P01").mkdir(parents=True)
    (tmp_path / "parts" / "P01" / "timeline.json").write_text(
        json.dumps({"sentences": [{"id": "P01.b01.s01", "start": 1.0, "end": 2.0}]}), encoding="utf-8")
    a = _input_sha(tmp_path, "P01", script, part, cfg, "rules")
    (tmp_path / "parts" / "P01" / "timeline.json").write_text(
        json.dumps({"sentences": [{"id": "P01.b01.s01", "start": 1.0, "end": 5.0}]}), encoding="utf-8")
    assert a == _input_sha(tmp_path, "P01", script, part, cfg, "rules")
