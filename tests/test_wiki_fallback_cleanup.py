"""
tests/test_wiki_fallback_cleanup.py — wiki/fallback_cleanup.py 单元测试

覆盖：命中正式实体页时归并、未命中时标记 stale、未到年龄阈值的页面跳过、
已处理过的页面不重复处理。
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

sys.path.insert(0, "src")

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.fallback_cleanup import cleanup_fallback_pages
from mini_agent.wiki.parser import parse_page
from mini_agent.wiki.writer import write_page


@pytest.fixture()
def paths(tmp_path):
    p = AgentPaths(project_root=tmp_path)
    p.ensure_wiki_dirs()
    return p


def _old_fallback_id(days_ago: int) -> str:
    return f"session-facts-{(date.today() - timedelta(days=days_ago)).isoformat()}"


def test_cleanup_merges_when_similar_entity_exists(paths):
    write_page(
        paths, page_id="client-pool", page_type="entity",
        body="ClientPool 负责多 LLM provider 的 key 轮换调度。", tags=["module"],
    )
    fallback_id = _old_fallback_id(40)
    write_page(
        paths, page_id=fallback_id, page_type="entity",
        body="ClientPool 会在 key 轮换失败时自动重试。", tags=["session-facts"],
    )

    report = cleanup_fallback_pages(paths, min_age_days=30)
    assert report.scanned == 1
    assert report.merged + report.marked_stale == 1

    fallback_page = parse_page(paths.wiki_entities_dir / f"{fallback_id}.md")
    assert fallback_page.raw_frontmatter.get("knowledge_state") in ("stale", "superseded")


def test_cleanup_marks_stale_when_no_similar_page(paths):
    fallback_id = _old_fallback_id(40)
    write_page(
        paths, page_id=fallback_id, page_type="entity",
        body="一段完全孤立、找不到任何关联实体的历史事实。", tags=["session-facts"],
    )

    report = cleanup_fallback_pages(paths, min_age_days=30)
    assert report.scanned == 1
    assert report.marked_stale == 1
    assert report.merged == 0

    fallback_page = parse_page(paths.wiki_entities_dir / f"{fallback_id}.md")
    assert fallback_page.raw_frontmatter.get("knowledge_state") == "stale"


def test_cleanup_skips_pages_younger_than_threshold(paths):
    fallback_id = _old_fallback_id(5)
    write_page(
        paths, page_id=fallback_id, page_type="entity",
        body="很新鲜的一条事实，还不到清理年龄。", tags=["session-facts"],
    )
    report = cleanup_fallback_pages(paths, min_age_days=30)
    assert report.scanned == 0
    assert report.marked_stale == 0
    assert report.merged == 0


def test_cleanup_skips_already_processed_pages(paths):
    fallback_id = _old_fallback_id(40)
    write_page(
        paths, page_id=fallback_id, page_type="entity",
        body="一段孤立的历史事实。", tags=["session-facts"],
    )
    first = cleanup_fallback_pages(paths, min_age_days=30)
    assert first.scanned == 1

    second = cleanup_fallback_pages(paths, min_age_days=30)
    assert second.scanned == 0
    assert second.skipped_already_checked == 1


def test_cleanup_no_fallback_pages_is_noop(paths):
    write_page(paths, page_id="regular-entity", page_type="entity", body="正常实体", tags=[])
    report = cleanup_fallback_pages(paths, min_age_days=30)
    assert report.scanned == 0
