"""道具（阶段 3）：镜头 prop_names → 匹配/缺失 → C-5 草拟入全局库 → 确认 → C-7 提示词 / C-8 参考图。"""
import json

from novel_to_video import chapter_io as cio
from novel_to_video import tool_api
from novel_to_video.project_store import write_json_atomic
from novel_to_video.review import entity_key, get_item, load_review
from novel_to_video.steps import chapter_clips as cc
from novel_to_video.steps import global_visual as gv
from novel_to_video.steps.chapter_assets import missing_portraits, run_assets
from novel_to_video.steps.chapter_script import run_script
from novel_to_video.steps.chapter_shots import run_shots
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from test_chapter_pipeline import SCRIPT, project  # noqa: F401  (fixture)


def _shots(prop_names):
    return {"shots": [{
        "scene_no": 1, "location_id": "loc_01", "character_ids": [], "prop_names": prop_names,
        "blocks": [{"type": "narration", "text": "铁城的清晨很安静。", "speaker_id": None, "speaker_name": None},
                   {"type": "dialogue", "text": "我们走。", "speaker_id": None, "speaker_name": "林远"}],
        "visual_hint_zh": "街道", "visual_hint_en": "dawn street"}]}


class PropLLM:
    name = "prop-llm"

    def __init__(self, prop_names):
        self.prop_names = prop_names

    def ask(self, prompt, system=""):
        if "TASK=script" in prompt:
            return SCRIPT
        if "TASK=shots" in prompt:
            return json.dumps(_shots(self.prop_names), ensure_ascii=False)
        if "TASK=entity" in prompt:
            return json.dumps({"gender": "male", "age_range": "youth", "description_zh": "x",
                               "visual_anchor_en": "man", "voice_profile": "低沉"}, ensure_ascii=False)
        return json.dumps({"visual_anchor_en": "jade pendant, green, carved dragon"}, ensure_ascii=False)


def _add_prop(p, pid="prop_01", name="玉佩", aliases=(), anchor="jade pendant, green, carved dragon"):
    doc = cio.load_entities(p)["props"]
    doc["props"].append({"id": pid, "name": name, "aliases": list(aliases), "first_appear": 1,
                         "description_zh": "", "visual_anchor_en": anchor, "visual_anchor_source": "t",
                         "asset_path": None, "source": "manual"})
    write_json_atomic(p / "global" / "props.json", doc)


def test_new_project_has_empty_props_and_old_project_tolerated(project):
    assert (project / "global" / "props.json").is_file()
    (project / "global" / "props.json").unlink()          # 旧项目没有该文件
    assert cio.load_entities(project)["props"] == {"props": []}
    assert gv.visual_status(project)["rows"]               # 不因缺文件崩溃


def test_shot_props_matched_by_name_alias_and_unknown_recorded(project):
    _add_prop(project, aliases=["龙佩"])
    run_script(project, 1, PropLLM([]))
    run_shots(project, 1, PropLLM(["龙佩", "铜钥匙", "铜钥匙", "", "玉佩"]))
    doc = cio.load_shots(project, 1)
    sh = doc["shots"][0]
    assert sh["prop_ids"] == ["prop_01"]                  # 别名与原名去重
    assert sh["prop_names"] == ["铜钥匙"]                  # 库里没有：保留名字
    assert doc["new_props"] == ["铜钥匙"]


def test_at_most_three_props_per_shot(project):
    run_script(project, 1, PropLLM([]))
    run_shots(project, 1, PropLLM(["甲", "乙", "丙", "丁"]))
    assert cio.load_shots(project, 1)["new_props"] == ["甲", "乙", "丙"]


def test_missing_prop_drafted_into_global_library_and_backfilled(project):
    run_script(project, 1, PropLLM([]))
    run_shots(project, 1, PropLLM(["铜钥匙"]))
    r = run_assets(project, 1, PropLLM(["铜钥匙"]), StubImageProvider())
    props = cio.load_entities(project)["props"]["props"]
    new = next(p for p in props if p["name"] == "铜钥匙")
    assert new["source"] == "auto_added" and new["auto_added_chapter"] == 1 and new["id"] == "prop_01"
    doc = cio.load_shots(project, 1)
    assert doc["new_props"] == [] and doc["shots"][0]["prop_ids"] == [new["id"]]
    assert doc["shots"][0]["prop_names"] == []
    assert r["drafted_props"] == [new["id"]]
    assert any("等待确认道具设定" in w for w in r["waiting"]), r["waiting"]
    assert get_item(load_review(project), entity_key("prop", new["id"]))["text_status"] == "pending"


def test_manual_flow_text_then_portrait_then_released(project):
    run_script(project, 1, PropLLM([]))
    run_shots(project, 1, PropLLM(["铜钥匙"]))
    run_assets(project, 1, PropLLM(["铜钥匙"]), StubImageProvider())
    assert tool_api.review_asset(str(project), "prop", "prop_01", "approve", "text")["ok"]
    r = run_assets(project, 1, PropLLM(["铜钥匙"]), StubImageProvider())
    assert any("等待确认道具定妆图" in w for w in r["waiting"]), r["waiting"]
    assert tool_api.review_asset(str(project), "prop", "prop_01", "approve", "portrait")["ok"]
    r = run_assets(project, 1, PropLLM(["铜钥匙"]), StubImageProvider())
    assert not any("道具" in w for w in r["waiting"]), r["waiting"]
    assert (project / cio.load_entities(project)["props"]["props"][0]["asset_path"]).is_file()


def test_auto_gear_approves_prop(project):
    cfg = json.loads((project / "project.json").read_text(encoding="utf-8"))
    cfg["gear"] = "G3"
    (project / "project.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    run_script(project, 1, PropLLM([]))
    run_shots(project, 1, PropLLM(["铜钥匙"]))
    r = run_assets(project, 1, PropLLM(["铜钥匙"]), StubImageProvider())
    assert not any("道具" in w for w in r["waiting"]), r["waiting"]
    p = cio.load_entities(project)["props"]["props"][0]
    assert p["asset_path"] and get_item(load_review(project), entity_key("prop", p["id"]))["portrait_status"] == "approved"


def test_prop_portrait_is_square_still_object_prompt(project):
    seen = {}

    class Rec(StubImageProvider):
        def generate(self, prompt, save_path, *, ratio=None, **kw):
            seen["ratio"], seen["prompt"] = ratio, prompt
            return super().generate(prompt, save_path, ratio=ratio, **kw)

    _add_prop(project)
    gv.portrait_pass(project, Rec(), ids=["prop_01"])
    assert seen["ratio"] == "1:1" and "no people" in seen["prompt"] and "jade pendant" in seen["prompt"]


def test_prompt_contains_prop_anchor_and_consistency(project):
    shot = {"visual_hint_en": "close-up", "character_ids": [], "location_id": None, "prop_ids": ["prop_01"]}
    props = {"prop_01": {"visual_anchor_en": "jade pendant, green"}}
    built = cc.build_prompt("style", shot, {}, {}, props=props)
    assert "Props: jade pendant, green" in built["prompt"] and built["consistency"] == "pass"
    # 没有道具的镜头：提示词与之前完全一致（不含 Props 段）
    assert "Props:" not in cc.build_prompt("style", {**shot, "prop_ids": []}, {}, {}, props=props)["prompt"]


def test_reference_images_prop_only_when_room(tmp_path):
    for n in ("c1", "c2", "l", "p"):
        (tmp_path / f"{n}.png").write_bytes(b"x")
    chars = {"a": {"asset_path": "c1.png"}, "b": {"asset_path": "c2.png"}}
    locs = {"l": {"asset_path": "l.png"}}
    props = {"p": {"asset_path": "p.png"}}
    full = cc._reference_images(tmp_path, {"character_ids": ["a", "b"], "location_id": "l", "prop_ids": ["p"]},
                                chars, locs, props)
    assert [x.name for x in full] == ["c1.png", "c2.png", "l.png"]       # 3 张已满，道具不挤占
    room = cc._reference_images(tmp_path, {"character_ids": ["a"], "location_id": "l", "prop_ids": ["p"]},
                                chars, locs, props)
    assert [x.name for x in room] == ["c1.png", "l.png", "p.png"]


def test_clips_blocked_until_prop_portrait_exists(project):
    _add_prop(project)
    run_script(project, 1, PropLLM([]))
    run_shots(project, 1, PropLLM(["玉佩"]))
    assert missing_portraits(project, 1) and any("道具 prop_01" == m for m in missing_portraits(project, 1))


def test_chapter_view_lists_props_and_new_props(project):
    from novel_to_video import chapter_pipeline as cp
    _add_prop(project)
    run_script(project, 1, PropLLM([]))
    run_shots(project, 1, PropLLM(["玉佩", "铜钥匙"]))
    v = cp.chapter_view(project, 1)
    assert v["new_props"] == ["铜钥匙"]
    assert [p["name"] for p in v["shots"][0]["props"]] == ["玉佩"]
    assert v["shots"][0]["prop_names"] == ["铜钥匙"]


def test_edit_prop_marks_portrait_stale(project):
    _add_prop(project)
    gv.visual_text_pass(project, RuleTextLLM())
    gv.portrait_pass(project, StubImageProvider(), ids=["prop_01"])
    gv.approve(project, "prop", "prop_01", "portrait")
    item = gv.update_entity(project, "prop", "prop_01", {"visual_anchor_en": "silver key"})
    assert item["portrait_status"] == "stale"
