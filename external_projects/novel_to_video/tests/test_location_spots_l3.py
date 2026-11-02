"""地点细化 L3：C-7 提示词注明场景点所属区域；同区域近似名提示。"""
from novel_to_video.steps import chapter_clips as CC
from novel_to_video.steps.location_spots import near_name_pairs

AREA = {"id": "loc_01", "name": "铁城", "level": "area", "visual_anchor_en": "steampunk iron city, grey fog"}
SPOT = {"id": "loc_02", "name": "市政厅办公室", "level": "spot", "parent_id": "loc_01", "spot_kind": "interior",
        "visual_anchor_en": "steampunk office, heavy oak desk, steam pipes"}
SHOT = {"id": "s01", "visual_hint_en": "A woman stands at a desk.", "character_ids": [], "location_id": None}


def _prompt(shot_loc, locs):
    shot = dict(SHOT, location_id=shot_loc)
    return CC.build_prompt("ink style", shot, {}, locs)


def test_spot_prompt_names_its_region_and_keeps_anchor():
    res = _prompt("loc_02", {"loc_01": AREA, "loc_02": SPOT})
    assert SPOT["visual_anchor_en"] in res["prompt"]
    assert "(inside 铁城)" in res["prompt"]
    assert res["consistency"] == "pass"


def test_area_prompt_is_unchanged_without_region_clause():
    res = _prompt("loc_01", {"loc_01": AREA})
    assert "inside" not in res["prompt"]
    assert f"Setting: {AREA['visual_anchor_en']}" in res["prompt"]


def test_spot_without_parent_has_no_region_clause():
    orphan = dict(SPOT, parent_id=None)
    res = _prompt("loc_02", {"loc_02": orphan})
    assert "inside" not in res["prompt"]
    assert res["consistency"] == "pass"


def test_near_names_in_same_region_are_paired():
    locs = [AREA, SPOT, {"id": "loc_03", "name": "市长办公室", "level": "spot", "parent_id": "loc_01"}]
    pairs = near_name_pairs(locs)
    assert [(p["a_name"], p["b_name"]) for p in pairs] == [("市政厅办公室", "市长办公室")]


def test_near_names_across_regions_or_distinct_names_not_paired():
    other = {"id": "loc_09", "name": "码头", "level": "area"}
    locs = [AREA, other, SPOT,
            {"id": "loc_04", "name": "市长办公室", "level": "spot", "parent_id": "loc_09"},   # 另一区域
            {"id": "loc_05", "name": "东三街", "level": "spot", "parent_id": "loc_01"}]        # 名字不相近
    assert near_name_pairs(locs) == []


def test_chapter_page_shows_new_plan_warnings_without_crashing(monkeypatch):
    """回归：默认开启地点细化后，plan_warnings 可能只有 message（无 parts），章节页不能崩溃，并要显示出来。"""
    import ui.chapter_page as cp
    shown = []
    monkeypatch.setattr(cp.st, "warning", lambda m, *a, **k: shown.append(m))
    monkeypatch.setattr(cp.st, "expander", lambda *a, **k: _NullCtx())
    view = {"shots": [{"id": "s01"}],
            "plan_warnings": [{"kind": "too_many_spots", "message": "本章规划了 5 个新场景点"},
                              {"kind": "location_not_specific", "message": "场次 2 的地点未写具体场所"}]}
    cp._plan_warnings("unused", 1, view)
    assert "本章规划了 5 个新场景点" in shown and "场次 2 的地点未写具体场所" in shown
    assert not any("旧版镜头规划提示" in m for m in shown)


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
