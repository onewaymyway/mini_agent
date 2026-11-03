"""补建素材一键确认：批量通过设定、批量筛选定妆图对象、批量通过定妆图（跳过缺字段与无图项）。"""
import json
from pathlib import Path

from novel_to_video import tool_api
from novel_to_video.review import entity_key
from conftest import build_export


def _project(tmp_path):
    src = build_export(tmp_path / "src")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="书")
    return Path(res["data"]["project_dir"])


def _rd(p):
    return json.loads(p.read_text(encoding="utf-8"))


def _wr(p, o):
    p.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")


def _add_drafts(proj):
    chars = _rd(proj / "global" / "characters.json")
    chars["characters"] += [
        {"id": "char_90", "names": ["路人甲"], "role": "auto_added", "source": "auto_added", "auto_added_chapter": 1,
         "visual_anchor_en": "grey uniform", "description_zh": "d", "looks": [],
         "voice_profile": "平静的男中音"},
        {"id": "char_91", "names": ["无设定"], "role": "auto_added", "source": "auto_added", "auto_added_chapter": 1,
         "visual_anchor_en": "", "description_zh": "", "looks": []},
    ]
    _wr(proj / "global" / "characters.json", chars)
    from novel_to_video import gender_review
    gender_review.confirm(proj, "char_90", "male")
    locs = _rd(proj / "global" / "locations.json")
    locs["locations"].append({"id": "loc_90", "name": "配给站", "level": "area", "auto_added_chapter": 1,
                              "source": "auto_added", "visual_anchor_en": "station"})
    _wr(proj / "global" / "locations.json", locs)


def test_batch_text_approves_ready_and_skips_missing_fields(tmp_path):
    proj = _project(tmp_path)
    _add_drafts(proj)
    res = tool_api.batch_review(str(proj), "text", 1)
    assert res["ok"]
    names = res["data"]["approved"]
    assert "路人甲" in names and "配给站" in names
    skipped = {s["name"]: s["reason"] for s in res["data"]["skipped"]}
    assert "无设定" in skipped and "缺少字段" in skipped["无设定"]
    from novel_to_video.review import get_item
    from novel_to_video.project_store import read_json
    review = read_json(proj / "global" / "review.json")
    assert get_item(review, entity_key("character", "char_90"))["text_status"] == "approved"
    assert get_item(review, entity_key("character", "char_91"))["text_status"] != "approved"


def test_batch_portrait_only_approves_items_with_image(tmp_path):
    proj = _project(tmp_path)
    _add_drafts(proj)
    tool_api.batch_review(str(proj), "text", 1)
    # 路人甲已有图片，配给站没有
    img = proj / "global" / "assets" / "char_90.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"png")
    chars = _rd(proj / "global" / "characters.json")
    next(c for c in chars["characters"] if c["id"] == "char_90")["asset_path"] = "global/assets/char_90.png"
    _wr(proj / "global" / "characters.json", chars)
    from novel_to_video.review import update_item
    update_item(proj, entity_key("character", "char_90"), portrait_status="pending")

    res = tool_api.batch_review(str(proj), "portrait", 1)
    assert res["data"]["approved"] == ["路人甲"]
    assert any(s["name"] == "配给站" and "还没有定妆图" in s["reason"] for s in res["data"]["skipped"])


def test_chapter_portrait_ids_selects_approved_text_without_image(tmp_path):
    proj = _project(tmp_path)
    _add_drafts(proj)
    assert tool_api.chapter_portrait_ids(str(proj), 1)["data"] == []        # 设定未通过 → 不生成
    tool_api.batch_review(str(proj), "text", 1)
    ids = tool_api.chapter_portrait_ids(str(proj), 1)["data"]
    assert "char_90" in ids and "loc_90" in ids
    assert "char_91" not in ids                                              # 缺字段的不生成


def test_start_portraits_reports_nothing_to_do(tmp_path):
    proj = _project(tmp_path)
    res = tool_api.start_chapter_portraits_background(str(proj), 99)
    assert res["ok"] and res["data"]["started"] is False
