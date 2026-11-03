"""地点细化 L5：场景设定图（外观/内部/房间/标志物）、镜头选图、一键依次执行、原文段落检索。"""
import json
from pathlib import Path

from novel_to_video.providers.image import StubImageProvider
from novel_to_video import prompts
from novel_to_video.steps import location_presplit as lp
from novel_to_video.steps import location_auto as la
from novel_to_video.steps import location_sheets as LS
from novel_to_video.steps import location_context as LC
from novel_to_video.steps import chapter_clips as cc
from novel_to_video.steps import chapter_assets as ca
from novel_to_video.chapter_io import load_entities

from test_chapter_pipeline import project  # noqa: F401  (fixture)


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.calls = []

    def ask(self, prompt, system=""):
        self.calls.append(prompt)
        if "TASK=presplit" in prompt:
            return json.dumps({"spots": [{"name": "市政厅办公室", "spot_kind": "interior", "reason": "正文常见场所"},
                                         {"name": "东三街", "spot_kind": "exterior", "reason": "追逐戏"}]}, ensure_ascii=False)
        if "TASK=sheets" in prompt:
            spot_name = json.loads(prompt.split("RULE_INPUT=", 1)[1])["spot"]["name"]
            if spot_name == "东三街":
                return json.dumps({"exterior": {"description_zh": "街道", "visual_anchor_en": "narrow brass street, grey fog"},
                                   "markers": []}, ensure_ascii=False)
            return json.dumps({
                "exterior": {"description_zh": "市政厅外观", "visual_anchor_en": "grand stone city hall facade"},
                "interior": {"description_zh": "办公室", "visual_anchor_en": "wood desk, steam pipes, window with grey fog"},
                "rooms": [{"name": "档案室", "visual_anchor_en": "shelves of ledgers"},
                          {"name": "档案室", "visual_anchor_en": "重复房间，应去掉"}],
                "markers": [{"name": "蒸汽电话", "description_zh": "黄铜电话", "visual_anchor_en": "brass steam telephone on desk", "views": ["interior"]},
                            {"name": "巨钟", "visual_anchor_en": "giant clock on the wall", "views": []}],
            }, ensure_ascii=False)
        return json.dumps({"visual_anchor_en": "steampunk city, brass pipes, grey fog"}, ensure_ascii=False)


def _doc(proj):
    return json.loads((proj / "global" / "locations.json").read_text(encoding="utf-8"))["locations"]


def _area(proj):
    return next(l for l in _doc(proj) if (l.get("level") or "area") == "area")


# ---------------------------------------------------------------- 规范化规则（纯函数）

def test_exterior_spot_gets_only_exterior_sheet():
    spot = {"name": "东三街", "spot_kind": "exterior", "visual_anchor_en": "street"}
    sheets, markers = LS.build_sheets(spot, {"exterior": {"visual_anchor_en": "street view"}, "interior": {"visual_anchor_en": "x"}}, basis="novel_text")
    assert [s["id"] for s in sheets] == ["exterior"]
    assert sheets[0]["visual_anchor_en"] == "street view"


def test_interior_spot_gets_exterior_interior_and_deduped_capped_rooms():
    spot = {"name": "市政厅办公室", "spot_kind": "interior", "visual_anchor_en": "office"}
    rooms = [{"name": f"房间{i}", "visual_anchor_en": "r"} for i in range(6)] + [{"name": "房间0", "visual_anchor_en": "dup"}]
    sheets, _ = LS.build_sheets(spot, {"exterior": {"visual_anchor_en": "facade"}, "rooms": rooms}, basis="inferred")
    ids = [s["id"] for s in sheets]
    assert ids[:2] == ["exterior", "interior"]
    assert ids[2:] == ["room_01", "room_02", "room_03", "room_04"]          # 去重、最多 4 个
    assert sheets[1]["visual_anchor_en"] == "office"                       # 缺锚点回退为场景点锚点
    assert all(s["basis"] == "inferred" for s in sheets)


def test_marker_views_scope_which_sheets_show_it():
    spot = {"name": "办公室", "spot_kind": "interior", "visual_anchor_en": "office"}
    markers_raw = [{"name": "蒸汽电话", "visual_anchor_en": "phone", "views": ["interior"]},
                   {"name": "巨钟", "visual_anchor_en": "clock", "views": []}]
    sheets, markers = LS.build_sheets(spot, {"markers": markers_raw, "rooms": [{"name": "档案室"}]}, basis="novel_text")
    by = {s["id"]: s["marker_names"] for s in sheets}
    assert by["interior"] == ["蒸汽电话", "巨钟"]
    assert by["exterior"] == ["巨钟"]                    # 外观只带未限定视角的标志物
    assert by["room_01"] == ["巨钟"]
    assert len(markers) == 2


def test_sheet_for_shot_prefers_explicit_then_kind():
    loc = {"spot_kind": "interior", "sheets": [{"id": "exterior", "kind": "exterior"}, {"id": "interior", "kind": "interior"},
                                              {"id": "room_01", "kind": "room"}]}
    assert LS.sheet_for_shot(loc, {"sheet_id": "room_01"})["id"] == "room_01"
    assert LS.sheet_for_shot(loc, {"sheet_id": "nope"})["id"] == "interior"
    assert LS.sheet_for_shot(loc, {})["id"] == "interior"
    assert LS.sheet_for_shot({"spot_kind": "exterior", "sheets": loc["sheets"]}, {})["id"] == "exterior"
    assert LS.sheet_for_shot({"spot_kind": "interior"}, {}) is None      # 没有设定图 → 沿用地点定妆图


# ---------------------------------------------------------------- 原文检索

def test_novel_paragraphs_come_from_source_text(project):  # noqa: F811
    area = _area(project)
    paras = LC.novel_paragraphs(project, [area["name"]], max_paras=50, max_chars=10_000)
    assert paras and all(area["name"] in p["text"] for p in paras)
    assert all(p["chapter"] == 1 for p in paras)
    assert LC.novel_paragraphs(project, ["不存在的地名"], max_paras=5, max_chars=100) == []


def test_novel_paragraphs_respect_limits(project):  # noqa: F811
    area = _area(project)
    capped = LC.novel_paragraphs(project, [area["name"]], max_paras=1, max_chars=10_000)
    assert len(capped) <= 1


# ---------------------------------------------------------------- 一键依次执行

def test_run_all_is_sequential_and_skips_done_areas(project):  # noqa: F811
    llm = FakeLLM()
    img = StubImageProvider()
    res = la.run_all(project, llm, img)
    assert res["stopped"] is None and not res["failed"]
    spots = [l for l in _doc(project) if l.get("level") == "spot"]
    assert {s["name"] for s in spots} >= {"市政厅办公室", "东三街"}
    office = next(s for s in spots if s["name"] == "市政厅办公室")
    assert [s["id"] for s in office["sheets"]][:2] == ["exterior", "interior"]
    assert all(s.get("asset_path") for s in office["sheets"])                    # 设定图已逐张生成
    assert _area(project).get("presplit_status") == "done"
    # 再跑一次：区域已预拆解，跳过；没有新的场景点需要提议
    calls_before = len(llm.calls)
    res2 = la.run_all(project, llm, img)
    assert _area(project)["id"] in res2["skipped"]
    assert not any("TASK=presplit" in c for c in llm.calls[calls_before:])


def test_run_all_stops_when_requested(project, monkeypatch):  # noqa: F811
    from novel_to_video import runtime as rt
    monkeypatch.setattr(rt, "stop_requested", lambda p: True)
    res = la.run_all(project, FakeLLM(), StubImageProvider())
    assert res["stopped"]
    assert not any(l.get("level") == "spot" for l in _doc(project))            # 一个区域都没处理


def test_run_all_refused_when_presplit_disabled(project):  # noqa: F811
    from novel_to_video import tool_api
    tool_api.set_location_presplit(str(project), False)
    try:
        la.run_all(project, FakeLLM(), StubImageProvider())
        assert False, "should refuse"
    except Exception as exc:  # StepError
        assert "已关闭" in str(exc)


# ---------------------------------------------------------------- 提示词与门禁

def test_shots_prompt_unchanged_without_sheets():
    base = prompts.shots_prompt("剧本", [], [{"id": "loc_01", "name": "铁城", "level": "area"}], spots_enabled=True)
    assert "sheet_id" not in base and "11. 所选地点" not in base


def test_shots_prompt_offers_sheet_ids_when_sheets_exist():
    locs = [{"id": "loc_02", "name": "办公室", "level": "spot", "parent_id": "loc_01", "spot_kind": "interior",
             "sheets": [{"id": "interior", "kind": "interior", "label": "内部总览", "marker_names": []}]}]
    text = prompts.shots_prompt("剧本", [], locs, spots_enabled=True)
    assert "sheet_id" in text and "11. 所选地点" in text and '"sheets"' in text


def test_build_prompt_uses_sheet_anchor_and_markers():
    loc = {"id": "loc_02", "name": "办公室", "level": "spot", "parent_id": "loc_01", "spot_kind": "interior",
           "visual_anchor_en": "office base", "markers": [{"name": "蒸汽电话", "visual_anchor_en": "brass phone"}],
           "sheets": [{"id": "interior", "kind": "interior", "visual_anchor_en": "office interior",
                       "marker_names": ["蒸汽电话"], "asset_path": "x.png"}]}
    shot = {"location_id": "loc_02", "character_ids": [], "prop_ids": [], "visual_hint_en": "close-up"}
    out = cc.build_prompt("style", shot, {}, {"loc_02": loc, "loc_01": {"id": "loc_01", "name": "铁城"}})
    assert "office interior" in out["prompt"] and "brass phone" in out["prompt"]
    assert "office base" not in out["prompt"]
    assert out["consistency"] == "pass"


def test_missing_sheet_image_blocks_c8(project):  # noqa: F811
    ents = load_entities(project)
    locs = ents["locations"]["locations"]
    spot = {"id": "loc_99", "name": "办公室", "level": "spot", "parent_id": locs[0]["id"], "spot_kind": "interior",
            "visual_anchor_en": "office", "sheets": [{"id": "interior", "kind": "interior", "visual_anchor_en": "office",
                                                       "marker_names": [], "asset_path": "global/assets/sheets/none.png"}]}
    shot = {"location_id": "loc_99"}
    assert LS.sheet_for_shot(spot, shot)["id"] == "interior"
    # 直接验证门禁逻辑：设定图文件不存在 → 不通过
    assert not LS._file_ok(project, spot["sheets"][0]["asset_path"])


def test_validate_drops_unknown_sheet_id(project):  # noqa: F811
    from novel_to_video.steps import chapter_shots as cs
    locs = [{"id": "loc_02", "name": "办公室", "level": "spot", "parent_id": None, "spot_kind": "interior",
             "sheets": [{"id": "interior", "kind": "interior", "label": "内部", "marker_names": []}]}]
    raw = [{"scene_no": 1, "location_id": "loc_02", "sheet_id": "bogus", "character_ids": [], "prop_names": [],
            "blocks": [{"type": "narration", "text": "旁白"}], "visual_hint_zh": "a", "visual_hint_en": "a"}]
    built = cs.validate_and_build(raw, "### 场次 1｜办公室（内景）\n旁白：旁白", locs, [], [])
    assert "sheet_id" not in built["shots"][0]
    raw[0]["sheet_id"] = "interior"
    built = cs.validate_and_build(raw, "### 场次 1｜办公室（内景）\n旁白：旁白", locs, [], [])
    assert built["shots"][0].get("sheet_id") == "interior"
