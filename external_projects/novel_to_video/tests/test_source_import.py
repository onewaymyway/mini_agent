import os
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video.source_import import import_source, parse_frontmatter
from novel_to_video.project_store import parse_chapter_spec


def codes(result, level=None):
    return {i.code for i in result.issues if level is None or i.level == level}


def test_counts_and_mapping(export_dir):
    r = import_source(export_dir)
    assert r.ok
    assert r.stats["chapters"] == 3
    assert r.stats["characters"] == 2
    assert r.stats["locations"] == 1
    assert [c["no"] for c in r.chapters] == [1, 2, 3]
    assert r.chapters[0]["summary_file"] is not None
    assert r.chapters[0]["title"] == "测试章1"


def test_character_fields(export_dir):
    r = import_source(export_dir)
    lin = next(c for c in r.characters if c["id"] == "char_001")
    assert lin["names"] == ["林远"]
    assert lin["age"] == 28 and lin["age_range"] == "youth"
    assert lin["gender"] == "male"                       # 来自人设卡 "性别：男"
    assert lin["speech_pattern"] == "直接，略带讽刺"
    assert "定位：旧时代的人类抵抗者" in lin["description_zh"]
    assert "外在呈现（人设卡）" in lin["description_zh"]
    assert lin["visual_anchor_en"] is None and lin["voice_profile"] is None
    shadow = next(c for c in r.characters if c["id"] == "char_002")
    assert shadow["age_range"] == "teen" and shadow["gender"] is None
    assert shadow["persona_card"] is None


def test_missing_gender_is_warning(export_dir):
    r = import_source(export_dir)
    assert "MISSING_GENDER" in codes(r, "warning")


def test_location_mapping(export_dir):
    r = import_source(export_dir)
    loc = r.locations[0]
    assert loc["id"] == "loc_01"
    assert loc["name"] == "铁城"
    assert loc["location_type"] == "穹顶城市"
    assert loc["first_appear"] == 1
    assert "冰冷的城市" in loc["description_zh"]
    assert not loc["description_zh"].startswith("# ")


def test_gap_warning(tmp_path):
    base = build_export(tmp_path, chapters=(1, 2, 4), gap_at=2)
    r = import_source(base)
    assert r.ok
    assert "CHAPTER_GAP" in codes(r, "warning")
    assert [c["no"] for c in r.chapters] == [1, 4]


def test_duplicate_chapter_is_error(tmp_path):
    base = build_export(tmp_path)
    (base / "正文" / "第002章 重复.md").write_text("# 第2章 重复\n" + "字" * 400, encoding="utf-8")
    r = import_source(base)
    assert not r.ok
    assert "DUPLICATE_CHAPTER" in codes(r, "error")


def test_missing_chapters_dir_is_error(tmp_path):
    base = build_export(tmp_path)
    import shutil
    shutil.rmtree(base / "正文")
    r = import_source(base)
    assert not r.ok and "MISSING_CHAPTERS" in codes(r, "error")


def test_source_not_found(tmp_path):
    r = import_source(tmp_path / "nope")
    assert not r.ok and "SOURCE_NOT_FOUND" in codes(r, "error")


def test_readme_cleanup_notes_become_warnings(export_dir):
    r = import_source(export_dir)
    notes = [i for i in r.issues if i.code == "EXPORT_CLEANUP_NOTE"]
    assert len(notes) == 1 and "第8章" in notes[0].message


def test_index_mismatch_warning(tmp_path):
    base = build_export(tmp_path)
    (base / "设定" / "人物" / "角色" / "char_002.md").unlink()
    r = import_source(base)
    assert "INDEX_MISMATCH" in codes(r, "warning")


def test_fingerprint_changes_with_content(tmp_path):
    base = build_export(tmp_path)
    fp1 = import_source(base).fingerprint
    assert fp1 == import_source(base).fingerprint
    (base / "正文" / "第001章 测试章1.md").write_text("# 第1章 改\n" + "字" * 500, encoding="utf-8")
    assert import_source(base).fingerprint != fp1


def test_frontmatter_parser():
    meta, body = parse_frontmatter('---\nid: "x"\nn: 3\nobj: {"a":1}\n---\nbody')
    assert meta == {"id": "x", "n": 3, "obj": {"a": 1}}
    assert body.strip() == "body"
    assert parse_frontmatter("no front")[0] == {}


@pytest.mark.parametrize("spec,expected", [
    (None, [1, 2, 3, 5, 6]),
    ("all", [1, 2, 3, 5, 6]),
    ("1-2", [1, 2]),
    ("2-", [2, 3, 5, 6]),
    ("-2", [1, 2]),
    ("1,6", [1, 6]),
    ("1-2，5", [1, 2, 5]),
])
def test_chapter_spec(spec, expected):
    assert parse_chapter_spec(spec, [1, 2, 3, 5, 6]) == expected


@pytest.mark.parametrize("spec", ["9", "5-1", "abc"])
def test_chapter_spec_errors(spec):
    with pytest.raises(ValueError):
        parse_chapter_spec(spec, [1, 2, 3])


@pytest.mark.skipif(not os.environ.get("NOVEL_TO_VIDEO_TEST_EXPORT"),
                    reason="设置 NOVEL_TO_VIDEO_TEST_EXPORT=<真实导出目录> 以运行")
def test_real_export_smoke():
    r = import_source(os.environ["NOVEL_TO_VIDEO_TEST_EXPORT"])
    assert r.ok
    assert r.stats["chapters"] == len(r.chapters) > 0
    assert r.stats["characters"] > 0 and r.stats["locations"] > 0
