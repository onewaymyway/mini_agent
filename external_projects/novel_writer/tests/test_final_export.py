"""最终稿导出：正文清洗（四种元信息格式）+ 完整导出流程。"""

import json
from pathlib import Path

import pytest

from fakes import put_blueprint
from novel_writer import config, tool_api
from novel_writer.final_export import clean_chapter
from novel_writer.workspace import Workspace
from test_m3 import make_blueprint

PROSE = "他走进雨里，没有回头，只听见身后铁门合上的声音，像一句没说完的话。" * 2  # 长段落，模拟正文


# ── 清洗：四种格式 ──────────────────────────────────────────────────────────

def test_header_block_before_body_marker_is_dropped():
    raw = ("# 第一章 铁城黎明\n\n---\n\n**实际字数：5487字**\n\n**chapter_type：** opening\n\n"
           "**完成情节任务：**\n- ✓ 展示科技\n\n## 正文\n\n" + PROSE + "\n\n---\n\n【写作备注】\n- 实际字数：5487\n")
    text, warn = clean_chapter(raw, number=1)
    assert text.startswith("# 第1章 铁城黎明\n")
    assert "实际字数" not in text and "chapter_type" not in text and "完成情节任务" not in text
    assert "写作备注" not in text and PROSE in text
    assert warn == []


def test_separator_then_notes_at_end_is_cut():
    raw = PROSE + "\n\n---\n\n写作备注（第1轮写入）\n实际字数：2750字\n完成情节任务：\n  ✓ 雨夜出摊\n章末钩子类型：悬念钩子\n"
    text, _ = clean_chapter(raw, number=1, fallback_title="雨夜摊贩")
    assert text.startswith("# 第1章 雨夜摊贩\n")
    assert "写作备注" not in text and "章末钩子" not in text and PROSE in text


def test_stray_filename_line_and_indented_notes_are_removed():
    raw = ("第44章_候选B.md\n\n" + PROSE + "\n\n---\n\n写作备注\n实际字数：约3680字\n埋设伏笔（id）：\n"
           "  周正廷遗书藏于城南旧书店地下室\n  第三枚铜钱由母亲持有\n章末钩子类型：信息钩子\n"
           "\"你父亲当年，也是从这一步开始的。\"门关上后，夜色正浓。\n")
    text, _ = clean_chapter(raw, number=44, fallback_title="苏家介入")
    assert "第44章_候选B" not in text
    assert "周正廷遗书" not in text and "信息钩子" not in text
    assert PROSE in text and text.startswith("# 第44章 苏家介入\n")


def test_notes_block_with_heading_and_bold_labels_without_title():
    raw = PROSE + "\n\n---\n\n## 写作备注\n\n**实际字数**：约5600字\n\n**chapter_type**：conflict\n\n" \
                  "**完成的情节任务**：\n  ✓ 潜入数据中心\n"
    text, _ = clean_chapter(raw, number=10, fallback_title="第一次任务")
    assert text.startswith("# 第10章 第一次任务\n")
    assert "chapter_type" not in text and "实际字数" not in text and PROSE in text


def test_long_prose_after_note_keyword_is_not_cut():
    """正文里偶然出现备注关键字开头的行，后面仍有长段落时不能误删正文。"""
    raw = "开头。\n\n实际字数的问题他并没有想过，" + PROSE + "\n\n" + PROSE + "\n"
    text, _ = clean_chapter(raw, number=2, fallback_title="误判")
    assert PROSE in text and text.count(PROSE) == 2


def test_empty_after_cleaning_raises():
    with pytest.raises(ValueError):
        clean_chapter("## 正文\n\n---\n写作备注\n实际字数：0\n", number=3)


# ── 完整导出 ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _book(tmp_path) -> Workspace:
    r = tool_api.create_novel("雨夜钟声", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=3)
    ws = Workspace(r["data"]["workspace"])
    put_blueprint(ws, make_blueprint(3))
    save = [
        "# 第一章 铁城黎明\n\n---\n\n**实际字数：5487字**\n\n## 正文\n\n" + PROSE + "\n\n---\n\n【写作备注】\n- 实际字数：5487\n",
        PROSE + "\n\n---\n\n写作备注\n实际字数：2750字\n章末钩子类型：悬念钩子\n",
        "第2章_候选B.md\n\n" + PROSE + "\n\n---\n写作备注\n实际字数：100\n",
    ]
    for n, text in enumerate(save, start=1):
        tool_api.save_chapter(str(ws.root), n, text)
    return ws


def test_full_export_layout_and_content(tmp_path):
    ws = _book(tmp_path)
    (ws.root / "大纲规划" / "主题设计.md").parent.mkdir(parents=True, exist_ok=True)
    (ws.root / "大纲规划" / "主题设计.md").write_text("# 主题\n\n核心是选择。\n", encoding="utf-8")
    ws.path("world_bible").parent.mkdir(parents=True, exist_ok=True)
    ws.path("world_bible").write_text(json.dumps({"城市": {"名称": "铁城", "人口": 2000}}, ensure_ascii=False),
                                      encoding="utf-8")
    chars = ws.path("characters_dir")
    chars.mkdir(parents=True, exist_ok=True)
    (chars / "林远.md").write_text("# 林远\n\n工号7749。\n", encoding="utf-8")

    r = tool_api.export_final(str(ws.root))
    assert r["ok"], r
    d = r["data"]
    out = ws.root / d["dir"]
    assert out.parent.name == "最终稿" and out.name.startswith("雨夜钟声_")
    assert d["chapters"] == 3 and d["errors"] == [] and d["warnings"] == []

    chapter_files = sorted((out / "正文").glob("*.md"))
    assert [f.name for f in chapter_files] == ["第001章 铁城黎明.md", "第002章 第2章 测试.md", "第003章 第3章 测试.md"]
    for f in chapter_files:
        body = f.read_text(encoding="utf-8")
        assert "实际字数" not in body and "写作备注" not in body and "章末钩子" not in body
        assert PROSE in body

    assert "工号7749" in (out / "设定" / "人物" / "角色" / "林远.md").read_text(encoding="utf-8")
    assert "核心是选择" in (out / "设定" / "主题与基调.md").read_text(encoding="utf-8")
    world = (out / "设定" / "世界观" / "世界观.md").read_text(encoding="utf-8")
    assert "铁城" in world and "2000" in world

    txt = (ws.root / d["txt"]).read_text(encoding="utf-8")
    assert "实际字数" not in txt and "写作备注" not in txt and PROSE in txt
    assert "README" not in txt and (out / "README.md").exists()


def test_export_creates_new_timestamped_directory_each_time(tmp_path):
    import time
    ws = _book(tmp_path)
    a = tool_api.export_final(str(ws.root))["data"]["dir"]
    time.sleep(1.1)
    b = tool_api.export_final(str(ws.root))["data"]["dir"]
    assert a != b


# ── 回归：候选稿标题 + 长字段值的备注 ───────────────────────────────────────

def test_candidate_marker_heading_is_not_used_as_title():
    raw = "# 第58章 候选稿B\n\n" + PROSE + "\n\n天快亮了。\n\n---\n写作备注（候选稿B提交给chapter-review）\n实际字数：约2850字\n"
    text, warn = clean_chapter(raw, number=58, fallback_title="复仇计划")
    assert text.startswith("# 第58章 复仇计划\n")
    assert "候选稿B" not in text.splitlines()[0]
    assert any("候选" in w for w in warn)


def test_long_labelled_note_fields_do_not_block_the_cut():
    """备注里的长字段（如「风格差异说明：…」）不能被当成正文，否则整个备注块会残留。"""
    raw = (PROSE + "\n\n---\n写作备注\n实际字数：2850\n"
           "完成的情节任务：林小川整合现有情报制定反击策略，获得苏晚晴提供的苏家档案，安排明日会面\n"
           "风格差异说明：① 开场从父亲遗留物品切入而非直接引用赵天衡短信，减少信息堆砌感；② 苏晚晴主动上门\n")
    text, _ = clean_chapter(raw, number=58, fallback_title="复仇计划")
    assert "风格差异说明" not in text and "写作备注" not in text and "完成的情节任务" not in text
    assert PROSE in text


def test_dialogue_line_with_colon_is_still_prose():
    """带冒号的对白（标签很短，但含引号）是正文，即使它紧跟在备注关键字之后也不能被切掉。"""
    dialogue = "他说：「" + "这件事没有那么简单，我们必须先弄清楚父亲留下的每一条线索，否则谁也救不了你" + "」"
    raw = PROSE + "\n\n---\n实际字数的问题他并没有想过。\n" + dialogue + "\n\n" + PROSE + "\n"
    text, _ = clean_chapter(raw, number=3, fallback_title="对白")
    assert dialogue in text and text.count(PROSE) == 2
