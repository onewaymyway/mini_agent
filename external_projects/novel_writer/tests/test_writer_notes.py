"""写作备注统一格式：渲染、检查、机器校验提示（过渡阶段）。"""

import pytest

from novel_writer import writer_notes as WN

PROSE = "他走进雨里，没有回头，只听见身后铁门合上的声音，像一句没说完的话。" * 2

FIELDS = {
    "word_count": 5487,
    "chapter_type": "opening",
    "plot_tasks": [{"text": "展示科技水平", "done": True}, {"text": "暗示自由度低", "done": False, "reason": "留到下章"}],
    "scenes": [{"text": "城市全景", "covered": True}],
    "planted": ["fs_001（视觉细节）"],
    "resolved": [],
    "character_changes": ["林远：开始动摇"],
    "hook": "悬念钩子：他望向穹顶之外",
}


def test_render_then_audit_is_clean():
    text = "# 第1章 铁城黎明\n\n" + PROSE + "\n\n" + WN.render_notes(FIELDS)
    assert WN.audit_notes(text) == []


def test_render_contains_fixed_labels_in_order_and_integer_count():
    block = WN.render_notes(FIELDS)
    assert block.startswith("---\n\n## 写作备注\n")
    assert "- 实际字数：5487" in block
    assert "  - [ ] 暗示自由度低（未完成原因：留到下章）" in block
    assert "- 兑现伏笔：无" in block          # 空列表写「无」
    positions = [block.index(f"- {l}") if l != "完成情节任务" else block.index("- 完成情节任务")
                 for l in WN.CANON_LABELS]
    assert positions == sorted(positions)


@pytest.mark.parametrize("variant, expected", [
    ("---\n写作备注\n实际字数：2750字\n", "标题"),                     # 无 ##，且字数带「字」
    ("【写作备注】\n- 实际字数：5487\n", "标题"),                      # 【】 变体
    ("---\n\n## 写作备注（第1轮写入）\n\n- 实际字数：5487\n", "标题"),  # 标题带后缀
    ("---\n\n## 写作备注\n\n**实际字数**：约5600字\n", "字段"),          # 加粗 + 约字
])
def test_historical_variants_are_flagged(variant, expected):
    text = "# 第1章\n\n" + PROSE + "\n\n" + variant
    issues = WN.audit_notes(text)
    assert issues, f"应当被标记：{variant!r}"


def test_missing_fields_and_bad_order_are_flagged():
    block = WN.render_notes(FIELDS).replace("- 角色状态变化：林远：开始动摇\n", "")
    issues = WN.audit_notes("# 第1章\n\n" + PROSE + "\n\n" + block)
    assert any("缺少字段" in i and "角色状态变化" in i for i in issues)


def test_no_notes_at_all_is_flagged():
    assert WN.audit_notes("# 第1章\n\n" + PROSE + "\n") == ["缺少写作备注块"]


def test_prose_after_note_keyword_is_not_treated_as_notes():
    text = "# 第1章\n\n实际字数的问题他并没有想过，" + PROSE + "\n\n" + PROSE + "\n"
    assert WN.tail_cut(text.splitlines()) == len(text.splitlines())


def test_machine_findings_warns_on_nonstandard_notes(tmp_path, monkeypatch):
    """机器校验：格式不统一只产生提示（warn），不产生硬问题（hard）。"""
    from novel_writer import chapter_ops as C
    from novel_writer.workspace import Workspace
    from novel_writer import config, tool_api
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    r = tool_api.create_novel("测试", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=2)
    ws = Workspace(r["data"]["workspace"])

    tool_api.save_chapter(str(ws.root), 1, "# 第1章 甲\n\n" + PROSE + "\n\n【写作备注】\n- 实际字数：5487\n")
    bad = C.machine_findings(ws, 1, {})
    assert any("写作备注格式不统一" in w for w in bad["warn"])
    assert not any("写作备注" in h for h in bad["hard"])

    tool_api.save_chapter(str(ws.root), 2, "# 第2章 乙\n\n" + PROSE + "\n\n" + WN.render_notes(FIELDS))
    good = C.machine_findings(ws, 2, {})
    assert not any("写作备注格式不统一" in w for w in good["warn"])
