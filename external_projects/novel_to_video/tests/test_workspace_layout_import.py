"""回归：novel_writer 版本目录（_versions/vNNN，大纲规划/… 布局）可直接导入。

根因：导入器只认最终稿导出包布局（设定/人物/角色 …），版本目录是原始内容树（大纲规划/人物设定/角色 …），
导致 MISSING_CHARACTERS 阻断。修复：检测到该布局后转换为导出包布局的副本（layout_adapt）。
"""
from pathlib import Path

from conftest import CHAR_1, CHAR_2, PERSONA_1, _w
from novel_to_video import chapter_io as cio
from novel_to_video import tool_api
from novel_to_video.source_import import import_source


def build_version_dir(root: Path) -> Path:
    base = root / "_versions" / "v001"
    body = "这是一段很长的正文。" * 60
    for n in (1, 2):
        _w(base / "正文" / f"第{n:02d}章 测试章{n}.md", f"# 第{n}章 测试章{n}\n\n{body}\n")
        _w(base / "章节总结" / f"第{n:02d}章_总结.md", f"# 第{n:02d}章 总结\n\n概述{n}\n")
    _w(base / "正文" / "_drafts" / "第01章_候选.md", "草稿不应被导入")
    o = base / "大纲规划"
    _w(o / "人物设定" / "角色" / "char_001.md", CHAR_1)
    _w(o / "人物设定" / "角色" / "char_002.md", CHAR_2)
    _w(o / "人物设定" / "角色索引.md",
       "# 人物索引\n\n| id | 姓名 |\n|---|---|\n| char_001 | 林远 |\n| char_002 | 影子 |\n")
    _w(o / "人物设定" / "人设卡" / "林远.md", PERSONA_1)
    _w(o / "世界观设定" / "地点详情" / "铁城.md",
       "# 铁城\n\n## 基本信息\n- **类型**：穹顶城市\n- **首次出现**：第1章\n\n## 描述\n冰冷的城市。\n")
    return base


def test_version_dir_imports_without_blocking(tmp_path):
    res = import_source(build_version_dir(tmp_path))
    codes = {i.code: i.level for i in res.issues}
    assert res.ok, codes
    assert "MISSING_CHARACTERS" not in codes and "MISSING_LOCATIONS" not in codes
    assert "NO_README" not in codes and "NO_FULL_TEXT" not in codes
    assert codes["WORKSPACE_LAYOUT_CONVERTED"] == "info"
    assert codes["RAW_VERSION_TEXT"] == "warning"
    assert [c["no"] for c in res.chapters] == [1, 2]
    assert {c["id"] for c in res.characters} == {"char_001", "char_002"}
    assert res.characters[0]["gender"] == "male"          # 人设卡随之读到
    assert [l["name"] for l in res.locations] == ["铁城"]


def test_version_dir_project_reads_text_from_working_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(build_version_dir(tmp_path)), title="t")
    assert res["ok"], res
    proj = Path(res["data"]["project_dir"])
    assert (proj / "source" / "normalized" / "设定" / "人物" / "角色").is_dir()
    assert not (proj / "source" / "normalized" / "正文" / "_drafts").exists()
    assert "这是一段很长的正文" in cio.source_text(proj, 1)
