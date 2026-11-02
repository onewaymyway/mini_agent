"""缺失地点：C-5 草拟进入全局地点库，回填镜头；文本与定妆图确认沿用挡位自动审核。"""
import json

from novel_to_video import chapter_io as cio
from novel_to_video import tool_api
from novel_to_video.review import entity_key, get_item, load_review
from novel_to_video.steps.chapter_assets import run_assets
from novel_to_video.steps.chapter_script import run_script
from novel_to_video.steps.chapter_shots import run_shots
from novel_to_video.providers.image import StubImageProvider
from test_chapter_pipeline import ChapterLLM, SCRIPT, project  # noqa: F401  (fixture)


class DraftLLM:
    """假 LLM：地点与角色草稿都返回完整字段。"""
    name = "draft-llm"

    def ask(self, prompt, system=""):
        if "TASK=script" in prompt:
            return SCRIPT
        if "TASK=shots" in prompt:
            return json.dumps({"shots": SHOTS_WITH_NEW_LOC}, ensure_ascii=False)
        return json.dumps({"visual_anchor_en": "old pier, grey dawn light, rusted cranes",
                           "voice_profile": "low, steady", "gender": "unknown", "age_range": "adult",
                           "description_zh": "一处旧码头。"}, ensure_ascii=False)


SHOTS_WITH_NEW_LOC = [{
    "scene_no": 1, "location_id": None, "location_name": "旧码头", "character_ids": [],
    "blocks": [{"type": "narration", "text": "铁城的清晨很安静。", "speaker_id": None, "speaker_name": None},
               {"type": "dialogue", "text": "我们走。", "speaker_name": "林远"}],
    "visual_hint_zh": "码头", "visual_hint_en": "old pier at dawn",
}]


def _plan_with_new_location(p):
    run_script(p, 1, DraftLLM())
    run_shots(p, 1, DraftLLM())
    assert cio.load_shots(p, 1)["new_locations"] == ["旧码头"]


def test_missing_location_is_drafted_into_global_library(project):
    _plan_with_new_location(project)
    r = run_assets(project, 1, DraftLLM(), StubImageProvider())
    locs = cio.load_entities(project)["locations"]["locations"]
    new = next(l for l in locs if l["name"] == "旧码头")
    assert new["source"] == "auto_added" and new["auto_added_chapter"] == 1
    assert new["visual_anchor_en"].startswith("old pier")
    doc = cio.load_shots(project, 1)
    assert doc["new_locations"] == []
    assert doc["shots"][0]["location_id"] == new["id"]
    assert doc["shots"][0]["location_name"] is None
    assert r["drafted_locations"] == [new["id"]]


def test_manual_gear_waits_for_location_text_and_portrait(project):
    _plan_with_new_location(project)
    r = run_assets(project, 1, DraftLLM(), StubImageProvider())
    assert any("等待确认地点设定" in w for w in r["waiting"]), r["waiting"]
    drafted_id = cio.load_shots(project, 1)["shots"][0]["location_id"]
    assert get_item(load_review(project), entity_key("location", drafted_id))["text_status"] == "pending"


def test_auto_gear_approves_location_text_and_portrait(project):
    cfg = json.loads((project / "project.json").read_text(encoding="utf-8"))
    cfg["gear"] = "G3"
    (project / "project.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    _plan_with_new_location(project)
    r = run_assets(project, 1, DraftLLM(), StubImageProvider())
    review = load_review(project)
    mine = [l for l in cio.load_entities(project)["locations"]["locations"] if l.get("auto_added_chapter") == 1]
    assert mine and all(get_item(review, entity_key("location", l["id"]))["text_status"] == "approved" for l in mine)
    assert not any("地点" in w for w in r["waiting"]), r["waiting"]
    assert all(l.get("asset_path") for l in mine)
