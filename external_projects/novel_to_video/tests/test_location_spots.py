"""地点细化（场景点）：镜头规划识别「现有地点不能直接使用」并规划新场景点（项目选项默认关闭）。"""
import json

from novel_to_video import chapter_io as cio
from novel_to_video import prompts, tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.steps import location_spots as LS
from novel_to_video.steps.chapter_assets import run_assets
from novel_to_video.steps.chapter_script import run_script
from novel_to_video.steps.chapter_shots import run_shots
from test_chapter_pipeline import ChapterLLM, SCRIPT, project  # noqa: F401  (fixture)

OFFICE_SCRIPT = """### 场次 1｜铁城·市政厅办公室·夜（内景）
旁白：铁城的清晨很安静。
林远：我们走。
影子：等一下。
新人：我也要去。
"""


def _blocks():
    return [{"type": "narration", "text": "铁城的清晨很安静。", "speaker_id": None, "speaker_name": None},
            {"type": "dialogue", "text": "我们走。", "speaker_id": None, "speaker_name": "林远"}]


def _shot(**kw):
    base = {"scene_no": 1, "location_id": "loc_01", "character_ids": [], "blocks": _blocks(),
            "visual_hint_zh": "办公室", "visual_hint_en": "office interior, medium shot"}
    base.update(kw)
    return base


class SpotLLM(ChapterLLM):
    """第一次把办公室的戏挂在铁城（loc_01）上；收到修正请求后规划新场景点。记录每次提示词。"""

    def __init__(self, fix=True):
        super().__init__(script=OFFICE_SCRIPT)
        self.fix = fix
        self.prompts = []

    def ask(self, prompt, system=""):
        self.prompts.append(prompt)
        if "TASK=shots_fix" in prompt and self.fix:
            return json.dumps({"shots": [_shot(location_id=None, location_name="市政厅办公室",
                                               parent_location_id="loc_01", spot_kind="interior",
                                               location_reason="铁城是整座城市，办公室内景不能用城市全景")]},
                              ensure_ascii=False)
        if "TASK=shots" in prompt:
            return json.dumps({"shots": [_shot()]}, ensure_ascii=False)
        if "TASK=script" in prompt:
            return OFFICE_SCRIPT
        if "kind\": \"location\"" in prompt or "视觉锚点" in prompt:
            return json.dumps({"visual_anchor_en": "steam-age city hall office, heavy oak desk"})
        return super().ask(prompt, system)


def _enable(proj, on=True):
    assert tool_api.set_location_spots(str(proj), on)["ok"]


def test_scene_places_parsing():
    p = LS.scene_places(OFFICE_SCRIPT)[1]
    assert (p["place"], p["specific"], p["area"], p["kind"]) == ("铁城·市政厅办公室", "市政厅办公室", "铁城", "interior")
    p = LS.scene_places(SCRIPT)[1]
    assert (p["specific"], p["area"], p["kind"]) == ("铁城", "铁城", "exterior")
    assert LS.scene_places("### 场次 2｜未知·夜（内景）\n")[2]["place"] == ""


def test_loc_fits_is_lenient():
    loc = {"name": "铁城", "aliases": []}
    assert LS.loc_fits("铁城", loc) and LS.loc_fits("", loc)
    assert not LS.loc_fits("市政厅办公室", loc)
    assert LS.loc_fits("市政厅办公室", {"name": "市政厅", "aliases": []})   # 互相包含：宁可放过


def test_option_defaults_off_and_prompt_unchanged_when_off(project):
    import json as _j
    assert _j.loads((project / "project.json").read_text(encoding="utf-8"))["location_spots_enabled"] is False
    off = prompts.shots_prompt("s", [], [{"id": "loc_01", "name": "铁城"}])
    assert "parent_location_id" not in off and "level" not in off
    on = prompts.shots_prompt("s", [], [{"id": "loc_01", "name": "铁城"}], spots_enabled=True)
    assert "parent_location_id" in on and "不能直接使用" in on and '"level": "area"' in on


def test_new_scene_is_identified_and_planned_when_enabled(project):
    _enable(project)
    llm = SpotLLM()
    run_script(project, 1, llm)
    r = run_shots(project, 1, llm)
    doc = cio.load_shots(project, 1)
    assert r["attempts"] == 2                                  # 第一次挂在铁城上 → 反馈修正 → 规划新场景点
    assert doc["new_locations"] == ["市政厅办公室"]
    spec = doc["new_location_specs"][0]
    assert spec["parent_id"] == "loc_01" and spec["spot_kind"] == "interior" and "城市全景" in spec["reason"]
    assert doc["shots"][0]["location_id"] is None and doc["shots"][0]["location_name"] == "市政厅办公室"
    fix_prompt = next(p for p in llm.prompts if "TASK=shots_fix" in p)
    assert "场次 1 的标题地点是「铁城·市政厅办公室」" in fix_prompt


def test_when_off_behaviour_is_unchanged(project):
    llm = SpotLLM()
    run_script(project, 1, llm)
    r = run_shots(project, 1, llm)
    doc = cio.load_shots(project, 1)
    assert r["attempts"] == 1 and doc["shots"][0]["location_id"] == "loc_01"
    assert "new_location_specs" not in doc and doc["new_locations"] == []


def test_exhausted_retries_become_warning_not_failure(project):
    _enable(project)
    cfg = json.loads((project / "project.json").read_text(encoding="utf-8"))
    cfg["shots_max_retries"] = 0
    (project / "project.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    llm = SpotLLM(fix=False)
    run_script(project, 1, llm)
    run_shots(project, 1, llm)                                  # 不抛错
    warns = cio.load_shots(project, 1)["plan_warnings"]
    assert any(w.get("kind") == "location_not_specific" for w in warns)


def test_scene_matching_existing_area_is_not_flagged(project):
    _enable(project)
    llm = ChapterLLM()                                          # 标题「铁城·清晨」+ 挂 loc_01：对得上
    run_script(project, 1, llm)
    r = run_shots(project, 1, llm)
    assert r["attempts"] == 1 and cio.load_shots(project, 1)["new_location_specs"] == []


def test_spot_is_drafted_with_parent_and_inherits_style(project):
    _enable(project)
    llm = SpotLLM()
    run_script(project, 1, llm)
    run_shots(project, 1, llm)
    run_assets(project, 1, llm, StubImageProvider())
    locs = cio.load_entities(project)["locations"]["locations"]
    spot = next(l for l in locs if l["name"] == "市政厅办公室")
    assert (spot["level"], spot["parent_id"], spot["spot_kind"]) == ("spot", "loc_01", "interior")
    draft_prompt = next(p for p in llm.prompts if "具体场景点" in p)
    assert "位于「" in draft_prompt and "内景" in draft_prompt and '"parent"' in draft_prompt
    assert cio.load_shots(project, 1)["shots"][0]["location_id"] == spot["id"]


def test_portrait_prompt_by_spot_kind():
    assert prompts.portrait_prompt("S", "a", kind="location", name="办公室", spot_kind="interior").startswith("S. Interior view")
    assert "Exterior view" in prompts.portrait_prompt("S", "a", kind="location", name="东街", spot_kind="exterior")
    assert "Establishing location art" in prompts.portrait_prompt("S", "a", kind="location", name="铁城")


def test_too_many_new_spots_warns(project):
    _enable(project)
    names = ["A室", "B室", "C室", "D室", "E室"]
    raw = [_shot(location_id=None, location_name=n) for n in names]
    from novel_to_video.steps.chapter_shots import validate_and_build
    locs = cio.load_entities(project)["locations"]["locations"]
    built = validate_and_build(raw, OFFICE_SCRIPT, locs, [], 1, spots_enabled=True)
    assert any(w.get("kind") == "too_many_spots" for w in built["plan_warnings"])
