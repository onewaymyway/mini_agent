"""C-3a 大场景：一章 → 多个大场景 → 镜头；场次校验、兜底归并、缓存、共用素材汇总。"""
import json
from pathlib import Path

import pytest

from test_shots_autofix import proj  # noqa: F401  (夹具)

from novel_to_video.steps import chapter_scenes as CS
from novel_to_video import chapter_io as cio
from novel_to_video.chapter_io import chapter_dir

pytestmark = pytest.mark.real_big_scenes

SCRIPT = """### 场次 1｜林远公寓·清晨（内景）
旁白：林远醒来。
### 场次 2｜林远公寓·清晨（内景）
林远：早。
### 场次 3｜林远公寓门口·清晨（外景）
旁白：他走出门。
### 场次 4｜地下通道·上午（外景）
旁白：通道很暗。
### 场次 5｜林远公寓·傍晚（内景）
旁白：夜幕降临。
"""


class LLM:
    name = "fake"

    def __init__(self, groups=None, invalid=False):
        self.groups = groups
        self.invalid = invalid
        self.calls = 0

    def ask(self, prompt, system=""):
        if "TASK=big_scenes" not in prompt:
            return json.dumps({"shots": []})
        self.calls += 1
        if self.invalid:
            return json.dumps({"big_scenes": [{"from_scene": 1, "to_scene": 2, "title_zh": "x"}]}, ensure_ascii=False)
        return json.dumps({"big_scenes": self.groups}, ensure_ascii=False)


GOOD = [
    {"from_scene": 1, "to_scene": 3, "title_zh": "林远公寓与门口", "place_zh": "林远公寓", "time_zh": "清晨", "summary_zh": "林远醒来并出门"},
    {"from_scene": 4, "to_scene": 4, "title_zh": "地下通道", "place_zh": "地下通道", "time_zh": "上午", "summary_zh": "穿过通道"},
    {"from_scene": 5, "to_scene": 5, "title_zh": "林远公寓（夜）", "place_zh": "林远公寓", "time_zh": "傍晚", "summary_zh": "回到公寓"},
]


def test_parse_script_scenes_and_place_key():
    scenes = CS.parse_script_scenes(SCRIPT)
    assert [s["scene_no"] for s in scenes] == [1, 2, 3, 4, 5]
    assert scenes[0]["place"] == "林远公寓"
    assert scenes[2]["place"] == "林远公寓门口"


def test_validate_groups_requires_contiguous_full_cover():
    assert CS.validate_groups(CS._normalize(GOOD), 5) == []
    gap = [{"from_scene": 1, "to_scene": 2}, {"from_scene": 4, "to_scene": 5}]
    assert any("连续" in x for x in CS.validate_groups(gap, 5))
    overlap = [{"from_scene": 1, "to_scene": 3}, {"from_scene": 3, "to_scene": 5}]
    assert CS.validate_groups(overlap, 5)
    short = [{"from_scene": 1, "to_scene": 4}]
    assert any("覆盖到" in x for x in CS.validate_groups(short, 5))


def test_fallback_groups_by_place_and_consecutive():
    groups = CS.fallback_groups(CS.parse_script_scenes(SCRIPT))
    assert [(g["from_scene"], g["to_scene"]) for g in groups] == [(1, 2), (3, 3), (4, 4), (5, 5)]


def test_plan_uses_model_output_and_caches(tmp_path):
    llm = LLM(groups=GOOD)
    plan = CS.plan_big_scenes(tmp_path, 1, SCRIPT, llm)
    assert plan["source"] == "model"
    assert [g["big_no"] for g in plan["big_scenes"]] == [1, 2, 3]
    assert plan["big_scenes"][0]["scene_nos"] == [1, 2, 3]
    again = CS.plan_big_scenes(tmp_path, 1, SCRIPT, llm)
    assert again["source"] == "model" and llm.calls == 1              # 剧本不变：读缓存，不再调用模型


def test_invalid_model_output_falls_back(tmp_path):
    llm = LLM(invalid=True)
    plan = CS.plan_big_scenes(tmp_path, 1, SCRIPT, llm)
    assert plan["source"] == "fallback"
    assert llm.calls == 2                                              # 先修正一次，仍不合格才兜底
    assert len(plan["big_scenes"]) == 4


def test_script_without_scene_headers_is_rejected(tmp_path):
    try:
        CS.plan_big_scenes(tmp_path, 1, "没有场次标题的剧本", LLM(groups=GOOD))
        assert False, "should raise"
    except ValueError as exc:
        assert "场次" in str(exc)


def test_attach_marks_shots_and_rolls_up_shared_assets(tmp_path):
    plan = {"big_scenes": CS._normalize(GOOD)}
    for g, nos in zip(plan["big_scenes"], [[1, 2, 3], [4], [5]]):
        g["scene_nos"] = nos
    shots_doc = {"shots": [
        {"id": "s01", "scene_no": 1, "location_id": "loc_01", "character_ids": ["char_01"], "prop_names": ["工作证"]},
        {"id": "s02", "scene_no": 2, "location_id": "loc_01", "character_ids": ["char_01", "char_02"], "prop_names": []},
        {"id": "s03", "scene_no": 3, "location_id": "loc_02", "character_ids": ["char_02"], "prop_names": ["工作证"]},
        {"id": "s04", "scene_no": 4, "location_id": "loc_03", "character_ids": [], "prop_names": []},
        {"id": "s05", "scene_no": 5, "location_id": "loc_01", "character_ids": ["char_01"], "prop_names": []},
    ]}
    chars = [{"id": "char_01", "names": ["林远"]}, {"id": "char_02", "names": ["盖亚"]}]
    locs = [{"id": "loc_01"}, {"id": "loc_02"}, {"id": "loc_03"}]
    CS.attach(shots_doc, plan, locs, chars)
    assert [s["big_scene_no"] for s in shots_doc["shots"]] == [1, 1, 1, 2, 3]
    g1 = shots_doc["big_scenes"][0]
    assert g1["shot_ids"] == ["s01", "s02", "s03"]
    assert g1["location_id"] == "loc_01"                                # 多数镜头所在的地点
    assert g1["character_ids"] == ["char_01", "char_02"]                # 出镜角色去重汇总
    assert g1["prop_names"] == ["工作证"]


def test_every_script_scene_must_have_a_shot():
    from novel_to_video.steps import chapter_shots as cs
    missing = [{"scene_no": 1, "blocks": [{"type": "narration", "text": "x"}]},
               {"scene_no": 3, "blocks": [{"type": "narration", "text": "y"}]}]
    issues = cs._scene_coverage_issues(missing, SCRIPT)
    assert any("第 2 场" in i["message"] for i in issues)
    assert any("第 4 场" in i["message"] for i in issues) and any("第 5 场" in i["message"] for i in issues)
    full = [{"scene_no": n, "blocks": []} for n in range(1, 6)]
    assert cs._scene_coverage_issues(full, SCRIPT) == []


def test_unknown_scene_number_is_reported():
    from novel_to_video.steps import chapter_shots as cs
    issues = cs._scene_coverage_issues([{"scene_no": 9, "blocks": []}], SCRIPT)
    assert any("第 9 个" in i["message"] or "第 9 场" in i["message"] for i in issues)


# ---------------------------------------------------------------- 分段规划：每个大场景单独调用模型

MULTI = """### 场次 1｜铁城街道·清晨（外景）
旁白：铁城的清晨很安静。
### 场次 2｜公寓·清晨（内景）
旁白：林远醒来。
### 场次 3｜工作间·黄昏（内景）
旁白：林远沉默地回到工位。
"""


class SegLLM:
    """按段回答：每段返回该段每场一个旁白镜头；第一次遇到场次 3 时故意漏掉它，用来验证只修正那一段。"""
    name = "fake"

    def __init__(self):
        self.shot_prompts = []
        self.omitted_once = False

    def ask(self, prompt, system=""):
        if "TASK=shots" not in prompt:
            return json.dumps({"shots": []})
        self.shot_prompts.append(prompt)
        seg = prompt.split("剧本：\n", 1)[1].split("\n\nRULE_INPUT=", 1)[0]
        import re
        scenes = re.findall(r"^### 场次 (\d+)｜(.*)$", seg, re.M)
        shots = []
        for n, header in scenes:
            n = int(n)
            if n == 3 and not self.omitted_once and "没有任何镜头" not in prompt:
                self.omitted_once = True
                continue
            shots.append({"scene_no": n, "location_id": "loc_01", "character_ids": [], "prop_names": [],
                          "blocks": [{"type": "narration", "text": f"第 {n} 场的旁白。"}],
                          "visual_hint_zh": f"中景：第{n}场", "visual_hint_en": f"medium shot of scene {n}"})
        return json.dumps({"shots": shots}, ensure_ascii=False)


def test_each_big_scene_is_planned_separately_and_only_the_missing_one_is_fixed(proj):
    cdir = cio.chapter_dir(proj, 1)
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "script.md").write_text(MULTI, encoding="utf-8")
    # 本测试只关心分段与修正：关闭“场所粒度”检查，避免它与场次覆盖检查互相干扰
    from novel_to_video.project_store import read_json, write_json_atomic
    pj = read_json(proj / "project.json")
    pj["location_spots_enabled"] = False
    write_json_atomic(proj / "project.json", pj)
    llm = SegLLM()
    from novel_to_video.steps.chapter_shots import run_shots as _run
    _run(proj, 1, llm, force=True)
    doc = cio.load_shots(proj, 1)
    assert [s["id"] for s in doc["shots"]] == ["s01", "s02", "s03"]        # 合并后重新编号
    assert [s["scene_no"] for s in doc["shots"]] == [1, 2, 3]
    assert [s["big_scene_no"] for s in doc["shots"]] == [1, 2, 3]
    # 每个大场景各调用一次模型；只有第 3 场那一段因漏场被修正（共 4 次调用）
    assert len(llm.shot_prompts) == 4
    fix = [p for p in llm.shot_prompts if "没有任何镜头" in p]
    assert len(fix) == 1
    assert "第 3 场的旁白" in fix[0] or "工作间" in fix[0]
    assert "铁城街道" not in fix[0] and "公寓" not in fix[0]               # 修正只带本段剧本
