"""
tests/test_wiki_append_section_dedupe.py — 回归测试：修复
`wiki/writer.py::append_section()` 反复追加内容完全相同的 "## 历史沿革"
段落的 bug。

根因：`perception/library_index.py::on_new_entry()` 每次有新记忆链接到某
实体就调用一次 `_mirror_entities_to_wiki()`，进而调用
`wiki/migration.py::mirror_entity()`。实体已存在时该函数会追加一条
"历史沿革"，内容取 `note or entity.summary`——如果这段时间内 `entity.summary`
没有变化（比如 goal 模式的 `goal` 概念实体，短时间内被大量记忆条目引用但
摘要没重写），每次追加的内容都跟上一次一字不差，正文里就会堆出几十段完全
相同的 "## 历史沿革"。
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "src")

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.parser import parse_page
from mini_agent.wiki.writer import append_section, write_page


@pytest.fixture
def paths(tmp_path):
    p = AgentPaths(tmp_path)
    p.ensure_wiki_dirs()
    return p


def test_append_section_skips_identical_repeated_content(paths):
    write_page(
        paths, page_id="goal", page_type="entity",
        body="## 概述\n\nGoal，类型：concept。\n",
        tags=["concept"],
    )
    page = parse_page(paths.wiki_entities_dir / "goal.md")

    note = "对话目标明确为创建可直接运行的 Python 脚本……"
    for _ in range(30):
        append_section(paths, page, heading="历史沿革", content=note)
        page = parse_page(paths.wiki_entities_dir / "goal.md")

    assert page.body.count("## 历史沿革") == 1


def test_append_section_still_appends_when_content_changes(paths):
    write_page(
        paths, page_id="goal", page_type="entity",
        body="## 概述\n\nGoal，类型：concept。\n",
        tags=["concept"],
    )
    page = parse_page(paths.wiki_entities_dir / "goal.md")

    append_section(paths, page, heading="历史沿革", content="第一版目标描述")
    page = parse_page(paths.wiki_entities_dir / "goal.md")
    append_section(paths, page, heading="历史沿革", content="第一版目标描述")  # 重复，应跳过
    page = parse_page(paths.wiki_entities_dir / "goal.md")
    append_section(paths, page, heading="历史沿革", content="第二版目标描述（已更新）")  # 内容变了，应追加
    page = parse_page(paths.wiki_entities_dir / "goal.md")

    assert page.body.count("## 历史沿革") == 2
    assert "第一版目标描述" in page.body
    assert "第二版目标描述（已更新）" in page.body


def test_append_section_dedupe_false_restores_old_behavior(paths):
    write_page(
        paths, page_id="goal", page_type="entity",
        body="## 概述\n\nGoal，类型：concept。\n",
        tags=["concept"],
    )
    page = parse_page(paths.wiki_entities_dir / "goal.md")

    append_section(paths, page, heading="历史沿革", content="重复内容", dedupe=False)
    page = parse_page(paths.wiki_entities_dir / "goal.md")
    append_section(paths, page, heading="历史沿革", content="重复内容", dedupe=False)
    page = parse_page(paths.wiki_entities_dir / "goal.md")

    assert page.body.count("## 历史沿革") == 2


def test_mirror_entity_repeated_calls_do_not_duplicate(paths):
    from mini_agent.wiki.migration import mirror_entity

    class _FakeEntity:
        entity_id = "e_goal"
        name = "goal"
        entity_type = "concept"
        summary = "对话目标明确为创建可直接运行的 Python 脚本……"
        status = "active"
        first_seen = 0
        last_summary_update = 0
        related_entry_ids = ["entry_1"]
        aliases: list = []
        category = "000"
        superseded_notes: list = []

    entity = _FakeEntity()
    for _ in range(10):
        mirror_entity(entity, paths)

    from mini_agent.wiki.migration import load_entity_map
    page_id = load_entity_map(paths)[entity.entity_id]
    page = parse_page(paths.wiki_entities_dir / f"{page_id}.md")
    assert page.body.count("## 历史沿革") <= 1
