"""角色多形象（阶段 4，默认关闭）：开关、镜头 character_looks、C-5 起草与确认、提示词/参考图/拦截、合并。"""
import json

from novel_to_video import chapter_io as cio
from novel_to_video import chapter_pipeline as cp
from novel_to_video import tool_api
from novel_to_video.project_store import write_json_atomic
from novel_to_video.review import entity_key, get_item, load_review
from novel_to_video.steps import chapter_clips as cc
from novel_to_video.steps import character_looks as cl
from novel_to_video.steps import global_visual as gv
from novel_to_video.steps.chapter_assets import missing_portraits, run_assets
from novel_to_video.steps.chapter_script import run_script
from novel_to_video.steps.chapter_shots import run_shots
from novel_to_video.providers.image import StubImageProvider
from test_chapter_pipeline import SCRIPT, project  # noqa: F401  (fixture)


def _enable(p, on=True):
    assert tool_api.set_character_looks(str(p), on)["ok"]


def _hero(p):
    return next(c for c in cio.load_entities(p)["characters"]["characters"] if "林远" in c["names"])


def _shots(label):
    return {"shots": [{
        "scene_no": 1, "location_id": "loc_01", "character_ids": [],
        "character_looks": [{"character_id": "林远", "look": label}] if label is not None else [],
        "blocks": [{"type": "narration", "text": "铁城的清晨很安静。", "speaker_id": None, "speaker_name": None},
                   {"type": "dialogue", "text": "我们走。", "speaker_id": None, "speaker_name": "林远"}],
        "visual_hint_zh": "街道", "visual_hint_en": "dawn street"}]}


class LookLLM:
    name = "look-llm"

    def __init__(self, label):
        self.label = label
        self.prompts = []

    def ask(self, prompt, system=""):
        self.prompts.append(prompt)
        if "TASK=script" in prompt:
            return SCRIPT
        if "TASK=shots" in prompt:
            return json.dumps(_shots(self.label), ensure_ascii=False)
        if "TASK=look" in prompt:
            return json.dumps({"description_zh": "战斗装", "visual_anchor_en": "same man, black combat armor"},
                              ensure_ascii=False)
        if "TASK=entity" in prompt:
            return json.dumps({"gender": "male", "age_range": "youth", "description_zh": "x",
                               "visual_anchor_en": "man", "voice_profile": "低沉"}, ensure_ascii=False)
        return json.dumps({"visual_anchor_en": "x"})


def _add_look(p, label="战斗装", aliases=()):
    doc = cio.load_entities(p)["characters"]
    c = next(c for c in doc["characters"] if "林远" in c["names"])
    c.setdefault("looks", []).append({"id": "look_01", "label": label, "aliases": list(aliases),
                                      "description_zh": "", "visual_anchor_en": "same man, black combat armor",
                                      "asset_path": None, "source": "manual"})
    write_json_atomic(p / "global" / "characters.json", doc)


def test_disabled_by_default_ignores_character_looks(project):
    llm = LookLLM("战斗装")
    run_script(project, 1, llm)
    run_shots(project, 1, llm)
    doc = cio.load_shots(project, 1)
    assert doc["new_looks"] == [] and "look_ids" not in doc["shots"][0]
    assert not any("character_looks" in p for p in llm.prompts)       # 关闭时提示词不提这个字段


def test_enabled_matches_existing_look_and_records_unknown(project):
    _enable(project)
    _add_look(project, aliases=["铠甲"])
    run_script(project, 1, LookLLM("铠甲"))
    run_shots(project, 1, LookLLM("铠甲"))
    sh = cio.load_shots(project, 1)["shots"][0]
    cid = _hero(project)["id"]
    assert sh["look_ids"] == {cid: "look_01"} and cid in sh["character_ids"]
    run_shots(project, 1, LookLLM("病中"), force=True)
    doc = cio.load_shots(project, 1)
    assert doc["new_looks"] == [{"character_id": cid, "label": "病中"}]
    assert doc["shots"][0]["look_labels"] == {cid: "病中"}


def test_default_label_means_no_look(project):
    _enable(project)
    run_script(project, 1, LookLLM("默认"))
    run_shots(project, 1, LookLLM("默认"))
    doc = cio.load_shots(project, 1)
    assert doc["new_looks"] == [] and "look_ids" not in doc["shots"][0] and "look_labels" not in doc["shots"][0]


def test_missing_look_drafted_backfilled_and_gated(project):
    _enable(project)
    llm = LookLLM("战斗装")
    run_script(project, 1, llm)
    run_shots(project, 1, llm)
    r = run_assets(project, 1, llm, StubImageProvider())
    hero = _hero(project)
    lk = hero["looks"][0]
    assert lk["label"] == "战斗装" and lk["source"] == "auto_added" and lk["auto_added_chapter"] == 1
    doc = cio.load_shots(project, 1)
    assert doc["new_looks"] == [] and doc["shots"][0]["look_ids"] == {hero["id"]: lk["id"]}
    assert "look_labels" not in doc["shots"][0]
    assert r["drafted_looks"] == [f"{hero['id']}:{lk['id']}"]
    assert any("等待确认形象设定" in w for w in r["waiting"]), r["waiting"]
    # 手动挡位：文本 → 定妆图 → 放行
    key = f"{hero['id']}:{lk['id']}"
    assert tool_api.review_asset(str(project), "look", key, "approve", "text")["ok"]
    r = run_assets(project, 1, llm, StubImageProvider())
    assert any("等待确认形象定妆图" in w for w in r["waiting"]), r["waiting"]
    path = _hero(project)["looks"][0]["asset_path"]
    assert path == f"global/assets/characters/{hero['id']}/look_01.png" and (project / path).is_file()
    assert tool_api.review_asset(str(project), "look", key, "approve", "portrait")["ok"]
    r = run_assets(project, 1, llm, StubImageProvider())
    assert not any("形象" in w for w in r["waiting"]), r["waiting"]


def test_auto_gear_approves_look(project):
    cfg = json.loads((project / "project.json").read_text(encoding="utf-8"))
    cfg["gear"] = "G3"
    (project / "project.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    _enable(project)
    llm = LookLLM("战斗装")
    run_script(project, 1, llm)
    run_shots(project, 1, llm)
    r = run_assets(project, 1, llm, StubImageProvider())
    assert not any("形象" in w for w in r["waiting"]), r["waiting"]
    hero = _hero(project)
    assert hero["looks"][0]["asset_path"]
    assert get_item(load_review(project), entity_key("look", f"{hero['id']}:look_01"))["portrait_status"] == "approved"


def test_prompt_and_refs_use_look_but_fall_back_without_it(tmp_path):
    (tmp_path / "base.png").write_bytes(b"x")
    (tmp_path / "look.png").write_bytes(b"x")
    c = {"visual_anchor_en": "base man", "asset_path": "base.png",
         "looks": [{"id": "look_01", "label": "战斗装", "visual_anchor_en": "armored man", "asset_path": "look.png"}]}
    shot = {"character_ids": ["a"], "visual_hint_en": "h", "look_ids": {"a": "look_01"}}
    built = cc.build_prompt("s", shot, {"a": c}, {})
    assert "armored man" in built["prompt"] and "base man" not in built["prompt"] and built["consistency"] == "pass"
    assert [p.name for p in cc._reference_images(tmp_path, shot, {"a": c}, {})] == ["look.png"]
    plain = {"character_ids": ["a"], "visual_hint_en": "h"}
    assert "base man" in cc.build_prompt("s", plain, {"a": c}, {})["prompt"]
    assert [p.name for p in cc._reference_images(tmp_path, plain, {"a": c}, {})] == ["base.png"]


def test_clips_blocked_when_look_portrait_missing(project):
    _enable(project)
    _add_look(project)
    run_script(project, 1, LookLLM("战斗装"))
    run_shots(project, 1, LookLLM("战斗装"))
    hero = _hero(project)
    assert f"形象 {hero['id']}:look_01" in missing_portraits(project, 1)


def test_edit_look_marks_portrait_stale_and_status_rows(project):
    _add_look(project)
    hero = _hero(project)
    key = f"{hero['id']}:look_01"
    gv.portrait_pass(project, StubImageProvider(), ids=[key])
    gv.approve(project, "look", key, "portrait")
    rows = [r for r in gv.visual_status(project)["rows"] if r["kind"] == "look"]
    assert rows and rows[0]["id"] == key and rows[0]["portrait_status"] == "approved"
    item = tool_api.edit_asset(str(project), "look", key, {"visual_anchor_en": "man in red robe"})
    assert item["ok"] and item["data"]["portrait_status"] == "stale"
    assert _hero(project)["looks"][0]["visual_anchor_en"] == "man in red robe"


def test_chapter_view_shows_looks(project):
    _enable(project)
    run_script(project, 1, LookLLM("病中"))
    run_shots(project, 1, LookLLM("病中"))
    v = cp.chapter_view(project, 1)
    cid = _hero(project)["id"]
    assert v["new_looks"] == [{"character_id": cid, "label": "病中"}]
    assert v["shots"][0]["look_labels"] == {cid: "病中"}


def test_toggle_persists(project):
    assert not cl.looks_enabled(project)
    _enable(project)
    assert cl.looks_enabled(project)
    _enable(project, False)
    assert not cl.looks_enabled(project)
