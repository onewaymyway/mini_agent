"""
tests/test_wiki_gap_scanner.py — wiki/gap_scanner.py 单元测试

覆盖：浅层实体检测、孤儿页面检测（复用 validator.py）、陈旧专题页检测与标注
（复用 lifecycle.py 的 knowledge_state 机制）、max_results 截断。
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "src")

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.gap_scanner import mark_stale_topics, scan_gaps
from mini_agent.wiki.parser import WikiLink, parse_page
from mini_agent.wiki.writer import write_page


@pytest.fixture
def paths(tmp_path):
    p = AgentPaths(tmp_path)
    p.ensure_wiki_dirs()
    return p


def test_scan_gaps_detects_shallow_entity(paths):
    write_page(
        paths, page_id="lonely-module", page_type="entity",
        body="一个还没被详细补全的模块。", tags=["module"],
    )
    gaps = scan_gaps(paths, max_results=10)
    kinds = {g.gap_kind for g in gaps}
    ids = {g.page_id for g in gaps}
    assert "shallow_entity" in kinds
    assert "lonely-module" in ids


def test_scan_gaps_shallow_entity_not_flagged_when_well_linked(paths):
    # 阈值语义：strong_links 数量 <= 1 视为浅层，所以要有 >= 2 条强链接
    # 才不会被标记——用一个有 2 条出边的页面验证。
    write_page(
        paths, page_id="module-hub", page_type="entity",
        body="连接了两个模块的枢纽实体。", tags=["module"],
        links=[
            WikiLink(target="module-a", relation="depends_on", source="frontmatter"),
            WikiLink(target="module-b", relation="depends_on", source="frontmatter"),
        ],
    )
    write_page(paths, page_id="module-a", page_type="entity", body="模块 A。", tags=["module"])
    write_page(paths, page_id="module-b", page_type="entity", body="模块 B。", tags=["module"])

    gaps = scan_gaps(paths, max_results=10)
    shallow_ids = {g.page_id for g in gaps if g.gap_kind == "shallow_entity"}
    assert "module-hub" not in shallow_ids
    # module-a/module-b 各自只有 1 条入边，仍然算浅层——符合"<=1 即浅层"的设计
    assert "module-a" in shallow_ids
    assert "module-b" in shallow_ids


def test_scan_gaps_detects_orphan_page(paths):
    write_page(
        paths, page_id="orphan-decision", page_type="decision",
        body="一个没有任何关系的决策页。", tags=["decision"],
    )
    gaps = scan_gaps(paths, max_results=10)
    orphan_ids = {g.page_id for g in gaps if g.gap_kind == "orphan_page"}
    assert "orphan-decision" in orphan_ids


def test_scan_gaps_detects_stale_topic_and_mark_writes_back(paths):
    write_page(
        paths, page_id="deprecated-a", page_type="entity",
        body="已经废弃的实体 A。", tags=["x"], status="deprecated",
    )
    write_page(
        paths, page_id="deprecated-b", page_type="entity",
        body="已经废弃的实体 B。", tags=["x"], status="deprecated",
    )
    write_page(
        paths, page_id="topic-x", page_type="topic",
        body="关于 X 的专题综述。", tags=["x"],
        links=[
            WikiLink(target="deprecated-a", relation="absorbs", source="frontmatter"),
            WikiLink(target="deprecated-b", relation="absorbs", source="frontmatter"),
        ],
    )

    gaps = scan_gaps(paths, max_results=10)
    stale = [g for g in gaps if g.gap_kind == "stale_topic"]
    assert len(stale) == 1
    assert stale[0].page_id == "topic-x"

    marked = mark_stale_topics(paths, gaps)
    assert marked == 1

    # 复用 O4 生命周期机制：knowledge_state 应该已经写成 stale
    topic_path = paths.wiki_topics_dir / "topic-x.md"
    page = parse_page(topic_path)
    assert page.raw_frontmatter.get("knowledge_state") == "stale"

    # 已经标注过的 topic 再次扫描不应该重复报告
    gaps_again = scan_gaps(paths, max_results=10)
    stale_again = [g for g in gaps_again if g.gap_kind == "stale_topic"]
    assert stale_again == []


def test_scan_gaps_respects_max_results(paths):
    for i in range(5):
        write_page(
            paths, page_id=f"lonely-{i}", page_type="entity",
            body=f"孤零零的实体 {i}。", tags=["module"],
        )
    gaps = scan_gaps(paths, max_results=2)
    assert len(gaps) <= 2


def test_scan_gaps_empty_wiki_returns_empty(paths):
    assert scan_gaps(paths) == []


def test_mark_stale_topics_no_gaps_is_noop(paths):
    assert mark_stale_topics(paths, []) == 0
