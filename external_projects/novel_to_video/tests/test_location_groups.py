"""全书素材页：区域内容块（区域 → 场景点 → 设定图）的分组与展示。"""
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from conftest import build_export
from novel_to_video import tool_api
from novel_to_video.steps.location_groups import group_locations, sheet_counts

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _locs():
    return [
        {"id": "loc_01", "name": "铁城", "level": "area"},
        {"id": "loc_02", "name": "市政厅办公室", "level": "spot", "parent_id": "loc_01", "spot_kind": "interior"},
        {"id": "loc_03", "name": "东三街", "level": "spot", "parent_id": "loc_01", "spot_kind": "exterior"},
        {"id": "loc_04", "name": "北城", "level": "area"},
        {"id": "loc_05", "name": "孤立场所", "level": "spot", "parent_id": "loc_99", "spot_kind": "exterior"},
        {"id": "loc_06", "name": "未出现的场所", "level": "spot", "parent_id": "loc_01"},
    ]


def test_group_locations_nests_spots_under_their_area():
    ids = ["loc_01", "loc_02", "loc_03", "loc_04", "loc_05"]          # loc_06 不在审核列表里
    g = group_locations(_locs(), ids)
    names = {x["area"]["name"]: [s["name"] for s in x["spots"]] for x in g["groups"]}
    assert names == {"铁城": ["市政厅办公室", "东三街"], "北城": []}
    assert [o["name"] for o in g["orphans"]] == ["孤立场所"]        # 父级不存在 → 未归属


def test_sheet_counts():
    loc = {"sheets": [{"asset_path": "a.png"}, {"asset_path": None}, {"asset_path": "b.png"}]}
    assert sheet_counts(loc, lambda rel: rel == "a.png") == (1, 3)
    assert sheet_counts({}, lambda rel: True) == (0, 0)


def test_assets_page_shows_one_block_per_area(tmp_path):
    src = build_export(tmp_path / "src")
    out = tmp_path / "out"
    res = tool_api.create_project_from_source(str(out), str(src), title="书")
    proj = Path(res["data"]["project_dir"])
    doc = json.loads((proj / "global" / "locations.json").read_text(encoding="utf-8"))
    area = next(l for l in doc["locations"] if (l.get("level") or "area") == "area")
    doc["locations"] += [
        {"id": "loc_80", "name": "市政厅办公室", "level": "spot", "parent_id": area["id"], "spot_kind": "interior",
         "visual_anchor_en": "office", "sheets": [{"id": "interior", "kind": "interior", "label": "内部总览", "marker_names": []}]},
        {"id": "loc_81", "name": "东三街", "level": "spot", "parent_id": area["id"], "spot_kind": "exterior",
         "visual_anchor_en": "street", "sheets": []},
    ]
    (proj / "global" / "locations.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    at = AppTest.from_file(APP, default_timeout=90).run()
    at.sidebar.radio(key="nav").set_value("🎭 全书素材").run()
    at.text_input(key="assets_root").input(str(out)).run()
    assert not at.exception
    texts = [m.value for m in at.markdown]
    assert sum(1 for t in texts if f"区域「{area['name']}」" in t) == 1         # 同一区域只有一个内容块
    assert any("场景点「市政厅办公室」" in t for t in texts)
    assert any("场景点「东三街」" in t for t in texts)
    assert any("尚未生成设定图" in c.value for c in at.caption)
