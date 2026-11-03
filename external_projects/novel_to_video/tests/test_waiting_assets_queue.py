"""补建素材队列：角色、地点、道具、形象的草稿都要进入队列，且与章节等待状态一致。"""
import json
from pathlib import Path

from novel_to_video import tool_api
from novel_to_video.review import entity_key
from novel_to_video.steps.character_looks import look_key
from novel_to_video.steps import location_presplit  # noqa: F401  (保证模块可导入)
from conftest import build_export


def _project(tmp_path):
    src = build_export(tmp_path / "src")
    out = tmp_path / "out"
    res = tool_api.create_project_from_source(str(out), str(src), title="书")
    return Path(res["data"]["project_dir"])


def _read(p):
    return json.loads(p.read_text(encoding="utf-8"))


def _write(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def test_queue_includes_drafted_location_prop_and_look_for_the_chapter(tmp_path):
    proj = _project(tmp_path)
    chars = _read(proj / "global" / "characters.json")
    chars["characters"].append({"id": "char_90", "names": ["路人甲"], "role": "auto_added", "source": "auto_added",
                                "auto_added_chapter": 1, "visual_anchor_en": "a", "description_zh": "d",
                                "looks": [{"id": "look_01", "label": "工装", "visual_anchor_en": "grey uniform",
                                           "auto_added_chapter": 1}]})
    _write(proj / "global" / "characters.json", chars)
    locs = _read(proj / "global" / "locations.json")
    locs["locations"].append({"id": "loc_90", "name": "配给站", "level": "area", "auto_added_chapter": 1,
                              "source": "auto_added", "visual_anchor_en": "station"})
    _write(proj / "global" / "locations.json", locs)
    props = _read(proj / "global" / "props.json")
    props["props"].append({"id": "prop_90", "name": "配给袋", "auto_added_chapter": 1, "source": "auto_added",
                           "visual_anchor_en": "bag"})
    _write(proj / "global" / "props.json", props)
    # 其它章节的草稿：队列里 chapter 不同，界面过滤
    locs["locations"].append({"id": "loc_91", "name": "别的章", "level": "area", "auto_added_chapter": 2,
                              "source": "auto_added", "visual_anchor_en": "x"})
    _write(proj / "global" / "locations.json", locs)

    rows = tool_api.auto_added_queue(str(proj))["data"]
    keys = {r["key"]: r for r in rows}
    assert entity_key("character", "char_90") in keys
    assert entity_key("look", look_key("char_90", "look_01")) in keys
    assert entity_key("location", "loc_90") in keys
    assert entity_key("prop", "prop_90") in keys
    assert keys[entity_key("location", "loc_91")]["chapter"] == 2     # 队列含全部章节，界面按章过滤
    assert {r["kind"] for r in rows if r["chapter"] == 1} == {"character", "look", "location", "prop"}


def test_queue_excludes_global_spots_without_chapter(tmp_path):
    proj = _project(tmp_path)
    locs = _read(proj / "global" / "locations.json")
    locs["locations"].append({"id": "loc_95", "name": "场景点", "level": "spot", "parent_id": None,
                              "auto_added_chapter": None, "source": "auto_added"})
    _write(proj / "global" / "locations.json", locs)
    keys = {r["key"] for r in tool_api.auto_added_queue(str(proj))["data"]}
    assert entity_key("location", "loc_95") not in keys
