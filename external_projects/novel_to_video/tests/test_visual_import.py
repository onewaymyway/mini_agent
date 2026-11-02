"""novel_writer 角色文件的结构化 visual 字段导入测试：性别、英文锚点、声音直接采用，缺失时回退。"""

from pathlib import Path

from novel_to_video import source_import as SI


def _write_char(root: Path, cid: str, meta_lines: str, body: str = "") -> None:
    d = root / "设定" / "人物" / "角色"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cid}.md").write_text(f"---\n{meta_lines}\n---\n{body}\n", encoding="utf-8")


VISUAL = ('{"gender":"female","hair":{"style":"高马尾","color":"银灰"},'
          '"default_outfit":{"top":"黑色机能外套","bottom":"工装裤","footwear":"军靴"},'
          '"accessories":["右腕护腕"],"voice":{"timbre":"低沉","pace":"快"},'
          '"anchor_en":"25-year-old woman, silver-grey high ponytail, black tactical jacket"}')


def test_structured_visual_is_used_directly(tmp_path):
    _write_char(tmp_path, "char_001", f'id: "char_001"\nname: "沈岚"\nrole: "protagonist"\nvisual: {VISUAL}')
    issues = []
    chars = SI._load_characters(tmp_path, issues)
    c = chars[0]
    assert c["gender"] == "female" and c["gender_source"] == "persona"
    assert c["visual_anchor_en"].startswith("25-year-old woman")
    assert c["voice_profile"] == "音色低沉，语速快"
    assert "发型：高马尾，银灰" in c["description_zh"] and "默认装扮：黑色机能外套" in c["description_zh"]
    assert not any(i.code == "MISSING_GENDER" for i in issues)


def test_missing_visual_falls_back_and_keeps_warning(tmp_path):
    _write_char(tmp_path, "char_002", 'id: "char_002"\nname: "路人"\nrole: "supporting"')
    issues = []
    c = SI._load_characters(tmp_path, issues)[0]
    assert c["gender"] is None and c["visual_anchor_en"] is None and c["voice_profile"] is None
    assert any(getattr(i, "code", "") == "MISSING_GENDER" for i in issues)
