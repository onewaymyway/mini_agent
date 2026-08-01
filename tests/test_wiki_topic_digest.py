"""tests/test_wiki_topic_digest.py — wiki/topics.py::build_topic_digest 测试
（外部数据知识化改进计划 P2）。

覆盖：
  1. 没有任何专题页时返回空字符串（调用方不注入 prompt 段落）
  2. 有专题页时正确产出 id + topic_label/tag + 一句话摘要
  3. build_topic_digest_section 带表头包装，空 digest 时同样返回空字符串
"""

from __future__ import annotations

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.topics import build_topic_digest, build_topic_digest_section
from mini_agent.wiki.writer import write_page


@pytest.fixture()
def wiki_paths(tmp_path):
    paths = AgentPaths(project_root=tmp_path)
    paths.ensure_wiki_dirs()
    return paths


def test_empty_when_no_topic_pages(wiki_paths):
    assert build_topic_digest(wiki_paths) == ""
    assert build_topic_digest_section(wiki_paths) == ""


def test_digest_lists_topic_pages_with_label_and_summary(wiki_paths):
    write_page(
        wiki_paths,
        page_id="topic-ai-agent",
        page_type="topic",
        body="## 概述\n\nAI agent 架构相关的动态汇总。\n",
        tags=["ai-agent"],
        confidence=0.5,
        extra_frontmatter={"topic_label": "AI Agent 架构动态", "source_tag": "ai-agent"},
    )
    digest = build_topic_digest(wiki_paths)
    assert "topic-ai-agent" in digest
    assert "AI Agent 架构动态" in digest
    assert "AI agent 架构相关的动态汇总" in digest


def test_digest_ignores_non_topic_pages(wiki_paths):
    write_page(
        wiki_paths,
        page_id="some-entity",
        page_type="entity",
        body="## 概述\n\n一个普通实体页面。\n",
        tags=["concept"],
        confidence=0.5,
    )
    assert build_topic_digest(wiki_paths) == ""


def test_section_wraps_digest_with_header(wiki_paths):
    write_page(
        wiki_paths,
        page_id="topic-x",
        page_type="topic",
        body="## 概述\n\n某个主题。\n",
        tags=["x"],
        confidence=0.5,
    )
    section = build_topic_digest_section(wiki_paths)
    assert "topic_id" in section
    assert "topic-x" in section
