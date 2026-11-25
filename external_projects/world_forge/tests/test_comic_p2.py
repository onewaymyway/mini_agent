"""漫画质量升级 P2：风格指南、每幕配色、角色表、场景图、候选选用与调用上限。"""

import pytest

from world_forge import comic_layout as cl
from world_forge import engine, generate, proposals, repo, store


def _story(world, llm):
    proposals.submit(world, "create_character", payload={"name": "守灯人", "role": "次要", "summary": "沉默的老人"})
    engine.advance(llm, world, 2)
    ids = [e["id"] for e in repo.load_events(world, "main")]
    return engine.make_arc(llm, world, event_ids=ids)


def _board_path(world, sid):
    return repo.story_dir(world, sid) / "storyboard.json"


def _set_panel(world, sid, page=0, panel=0, **fields):
    board = store.read_json(_board_path(world, sid))
    board["pages"][page]["panels"][panel].update(fields)
    store.write_json(_board_path(world, sid), board)


# ---------- 纯逻辑

@pytest.mark.parametrize("emotion,key", [("愤怒", "angry"), ("悲伤", "sad"), ("惊讶", "surprised"),
                                         ("平静", None), ("", None), (None, None)])
def test_expression_key_maps_emotion_words(emotion, key):
    assert cl.expression_key(emotion) == key


def test_style_validation_requires_english_fields():
    assert generate._validate_style({"summary": "中文", "line": "x", "palette": "", "lighting": "y"})
    assert generate._validate_style({"summary": "中文", "line": "x", "palette": "y", "lighting": "z"}) == []


def test_storyboard_normalizes_location_and_beat_palette_validates():
    p = {"visual_en": "x", "location": "  中心城邦 "}
    generate.normalize_panel(p)
    assert p["location"] == "中心城邦"
    d = {"pages": [{"purpose": "铺垫：开场", "focus": "门", "palette": " warm "}]}
    assert generate._validate_beats(d) == [] and d["pages"][0]["palette"] == "warm"


# ---------- 风格指南与每幕配色

def test_style_guide_is_saved_and_applied_to_panel_prompts(world, llm, img):
    engine.make_style_guide(llm, world)
    sg = repo.load_meta(world)["style_guide"]
    assert sg["palette"] and sg["summary"]
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=2)
    engine.render_comic(img, world, sid)
    assert "muted teal" in img.calls[0]["prompt"]


def test_each_page_carries_its_own_palette_in_color_mode(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=2)
    engine.render_comic(img, world, sid)
    first_page = img.calls[0]["prompt"]
    last_page = img.calls[-1]["prompt"]
    assert "Color palette: warm amber" in first_page
    assert "Color palette: teal and grey" in last_page and "warm amber" not in last_page


def test_black_and_white_mode_drops_palette_and_uses_manga_style(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=2, color="bw")
    engine.render_comic(img, world, sid)
    assert all("Color palette" not in c["prompt"] for c in img.calls)
    assert "black and white manga" in img.calls[0]["prompt"]


# ---------- 角色表

def _with_portrait(world, llm, img, sid):
    _set_panel(world, sid, characters=["守灯人"])
    cid = next(c["id"] for c in repo.load_characters(world) if c["name"] == "守灯人")
    assert engine.make_portrait(llm, img, world, cid)["ok"]
    return cid


def test_character_sheets_generate_side_and_three_expressions_then_skip(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=2)
    cid = _with_portrait(world, llm, img, sid)
    res = engine.make_character_sheets(img, world, sid)
    assert res["made"] == 4 and res["failed"] == 0
    for key in cl.SHEET_KEYS:
        assert repo.resolve_sheet(world, cid, key) is not None
    again = engine.make_character_sheets(img, world, sid)
    assert again["made"] == 0 and again["skipped"] == 4
    over = engine.make_character_sheets(img, world, sid, overwrite=True)
    assert over["made"] == 4


def test_angry_panel_references_angry_sheet_instead_of_portrait(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=2)
    cid = _with_portrait(world, llm, img, sid)
    engine.make_character_sheets(img, world, sid)
    _set_panel(world, sid, page=0, panel=0, characters=["守灯人"], emotion="愤怒")
    img.calls.clear()
    engine.render_comic(img, world, sid, only=[(1, 1)])
    assert img.calls[0]["refs"] == [f"{cid}_angry.png"]


def test_sheets_skip_characters_without_portrait(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=2)
    _set_panel(world, sid, characters=["守灯人"])
    res = engine.make_character_sheets(img, world, sid)
    assert res["made"] == 0 and res["skipped"] == 4 and img.calls == []


# ---------- 场景图

def test_scene_image_is_generated_and_used_as_reference(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=2)
    _set_panel(world, sid, page=0, panel=0, location="中心城邦")
    res = engine.make_scene_images(llm, img, world, sid)
    assert res["made"] == 1
    eid = next(e["id"] for e in repo.load_entities(world) if e["name"] == "中心城邦")
    assert repo.resolve_scene(world, eid) is not None
    ent = next(e for e in repo.load_entities(world) if e["id"] == eid)
    assert ent["attrs"]["visual_en"], "场景英文描述应保存在设定条目上，供再次生成使用"
    img.calls.clear()
    engine.render_comic(img, world, sid, only=[(1, 1)])
    assert f"{eid}.png" in img.calls[0]["refs"]


# ---------- 候选与选用

def test_candidates_are_kept_and_one_can_be_chosen(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=2)
    engine.render_comic(img, world, sid, candidates=2)
    board = store.read_json(_board_path(world, sid))
    pn = board["pages"][0]["panels"][0]
    assert len(pn["candidates"]) == 2 and pn["chosen"] == 1
    d = repo.story_dir(world, sid)
    second_bytes = (d / pn["candidates"][1]).read_bytes()
    engine.choose_candidate(world, sid, 1, 1, 2)
    board = store.read_json(_board_path(world, sid))
    assert board["pages"][0]["panels"][0]["chosen"] == 2
    assert (d / pn["image"]).read_bytes() == second_bytes


def test_choose_candidate_rejects_out_of_range(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=2)
    engine.render_comic(img, world, sid)          # 默认 1 张，无候选
    with pytest.raises(engine.EngineError):
        engine.choose_candidate(world, sid, 1, 1, 2)


def test_candidate_count_is_capped_at_three(world, llm, img):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=3)
    engine.render_comic(img, world, sid, candidates=9)
    pn = store.read_json(_board_path(world, sid))["pages"][0]["panels"][0]
    assert len(pn["candidates"]) == cl.CANDIDATE_MAX


# ---------- 调用上限

def test_image_budget_stops_render_and_keeps_finished_panels(world, llm, img, monkeypatch):
    monkeypatch.setenv("WORLD_FORGE_IMAGE_BUDGET", "2")
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=3)
    res = engine.render_comic(img, world, sid)
    assert res["budget_exhausted"] is True
    assert res["image_calls"] == 2 and len(img.calls) == 2
    board = store.read_json(_board_path(world, sid))
    assert board["pages"][0]["panels"][0]["image"] is not None
    assert board["pages"][-1]["panels"][-1].get("image") is None


def test_sheet_budget_limits_character_sheet_calls(world, llm, img, monkeypatch):
    sid = _story(world, llm)
    engine.make_storyboard(llm, world, sid, pages=2)
    _with_portrait(world, llm, img, sid)
    monkeypatch.setenv("WORLD_FORGE_IMAGE_BUDGET", "2")
    res = engine.make_character_sheets(img, world, sid)
    assert res["budget_exhausted"] is True and res["image_calls"] == 2
