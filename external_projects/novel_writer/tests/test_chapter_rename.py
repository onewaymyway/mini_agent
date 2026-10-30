"""章节文件名规范化：标题去重复章节号 + 改名计划（预览/执行/冲突）。"""

import pytest

from novel_writer import chapter_names as N
from novel_writer import chapter_rename as R
from novel_writer import config, tool_api
from novel_writer.workspace import Workspace, chapter_filename


@pytest.mark.parametrize("raw, want", [
    ("第058章 第58章 第五十八章 复仇计划", "复仇计划"),
    ("第064章 第六十四章 证据确凿", "证据确凿"),
    ("第065章 正面交锋", "正面交锋"),
    ("第一章 铁城黎明", "铁城黎明"),
    ("复仇计划", "复仇计划"),
])
def test_normalize_title_strips_repeated_chapter_numbers(raw, want):
    assert N.normalize_chapter_title(raw) == want


def test_chinese_numerals_and_number_reading():
    assert N.cn_to_int("五十八") == 58 and N.cn_to_int("一百零三") == 103 and N.cn_to_int("六十四") == 64
    assert N.chapter_number_from_name("第五十八章 复仇计划") == 58
    assert N.chapter_number_from_name("第058章 x") == 58
    assert N.cn_to_int("abc") is None


def test_filename_generation_uses_normalized_title():
    assert chapter_filename(65, "第065章 正面交锋", 120) == "第065章 正面交锋.md"
    assert chapter_filename(58, "第058章 第58章 第五十八章 复仇计划", 120) == "第058章 复仇计划.md"
    assert chapter_filename(5, "第5章 开端", 30) == "第05章 开端.md"


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    r = tool_api.create_novel("改名测试", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=120)
    ws = Workspace(r["data"]["workspace"])
    (ws.root / "正文").mkdir(parents=True, exist_ok=True)
    return ws


def _touch(ws, name):
    (ws.root / "正文" / name).write_text("正文\n", encoding="utf-8")


def test_plan_classifies_keep_rename_and_conflict(book):
    _touch(book, "第058章 第58章 第五十八章 复仇计划.md")
    _touch(book, "第064章 第六十四章 证据确凿.md")
    _touch(book, "第065章 正面交锋.md")                       # 已规范
    _touch(book, "第066章 第66章 乱写.md")
    _touch(book, "第066章 乱写.md")                           # 与上一条映射到同一目标 → 冲突
    _touch(book, "第070章 第七十章 原名.md")
    _touch(book, "第070章 原名.md")                           # 目标已存在 → 冲突
    plan = {r["file"]: r for r in tool_api.plan_chapter_renames(str(book.root))["data"]}
    assert plan["第058章 第58章 第五十八章 复仇计划.md"]["action"] == "rename"
    assert plan["第058章 第58章 第五十八章 复仇计划.md"]["target"] == "第058章 复仇计划.md"
    assert plan["第064章 第六十四章 证据确凿.md"]["target"] == "第064章 证据确凿.md"
    assert plan["第065章 正面交锋.md"]["action"] == "keep"
    assert plan["第066章 第66章 乱写.md"]["action"] == "conflict"
    assert plan["第066章 乱写.md"]["action"] == "keep"          # 已规范，目标就是它自己
    assert plan["第070章 第七十章 原名.md"]["action"] == "conflict"  # 目标 第070章 原名.md 已存在
    assert plan["第070章 原名.md"]["action"] == "keep"


def test_apply_renames_only_rename_actions_and_never_overwrites(book):
    _touch(book, "第058章 第58章 第五十八章 复仇计划.md")
    _touch(book, "第065章 正面交锋.md")
    _touch(book, "第070章 第七十章 原名.md")
    _touch(book, "第070章 原名.md")
    plan = tool_api.plan_chapter_renames(str(book.root))["data"]
    out = tool_api.apply_chapter_renames(str(book.root), plan)["data"]
    names = sorted(p.name for p in (book.root / "正文").iterdir())
    assert "第058章 复仇计划.md" in names and "第058章 第58章 第五十八章 复仇计划.md" not in names
    assert "第065章 正面交锋.md" in names
    assert "第070章 原名.md" in names and "第070章 第七十章 原名.md" in names   # 冲突未覆盖
    assert [d["to"] for d in out["renamed"]] == ["第058章 复仇计划.md"]


def test_drafts_directory_is_not_touched(book):
    drafts = book.root / "正文" / "_drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "第058章 第58章 候选稿B.md").write_text("候选\n", encoding="utf-8")
    plan = tool_api.plan_chapter_renames(str(book.root))["data"]
    assert plan == []


def test_two_sources_mapping_to_same_target_are_both_conflicts(book):
    _touch(book, "第067章 第67章 甲.md")
    _touch(book, "第067章 第六十七章 甲.md")
    plan = tool_api.plan_chapter_renames(str(book.root))["data"]
    assert [r["action"] for r in plan] == ["conflict", "conflict"]
    out = tool_api.apply_chapter_renames(str(book.root), plan)["data"]
    assert out["renamed"] == []
    assert len(list((book.root / "正文").glob("第067章*"))) == 2
