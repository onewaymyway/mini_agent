"""小场景逐镜审核：每个大场景显示它实际使用的场景参考图（与 C-8 选图规则一致），并标注是否已生成。"""
from pathlib import Path

from novel_to_video import chapter_pipeline as cp


def _img(root: Path, rel: str) -> str:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"png")
    return rel


def test_refs_follow_c8_selection_and_dedupe(tmp_path):
    interior = _img(tmp_path, "global/assets/sheets/loc_02_interior.png")
    portrait = _img(tmp_path, "global/assets/loc_02.png")
    raw_locs = {"loc_02": {"id": "loc_02", "name": "核心区工作间", "spot_kind": "interior", "asset_path": portrait,
                           "sheets": [{"id": "exterior", "kind": "exterior", "label": "外观全景",
                                       "asset_path": None},
                                      {"id": "interior", "kind": "interior", "label": "内部总览",
                                       "asset_path": interior}]},
                "loc_03": {"id": "loc_03", "name": "地下通道", "spot_kind": "exterior", "asset_path": "global/missing.png",
                           "sheets": []}}
    shots = [
        {"id": "s01", "big_scene_no": 1, "location_id": "loc_02"},                       # 室内默认 → 内部总览
        {"id": "s02", "big_scene_no": 1, "location_id": "loc_02", "sheet_id": "interior"},  # 同一张，去重
        {"id": "s03", "big_scene_no": 2, "location_id": "loc_03"},                       # 没有设定图 → 地点图（文件缺失）
    ]
    big = [{"big_no": 1, "title_zh": "工作间"}, {"big_no": 2, "title_zh": "地下通道"}]
    out = cp.big_scene_refs(tmp_path, shots, big, raw_locs)
    g1, g2 = out
    assert len(g1["refs"]) == 1
    assert g1["refs"][0] == {"label": "核心区工作间·内部总览", "path": interior, "exists": True}
    assert g2["refs"] == [{"label": "地下通道·地点定妆图", "path": "global/missing.png", "exists": False}]


def test_big_scene_without_shots_has_no_refs(tmp_path):
    out = cp.big_scene_refs(tmp_path, [], [{"big_no": 1, "title_zh": "空"}], {})
    assert out[0]["refs"] == []
