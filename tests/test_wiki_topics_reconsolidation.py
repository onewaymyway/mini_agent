"""
tests/test_wiki_topics_reconsolidation.py — wiki/topics.py O3 再巩固路径测试

覆盖《wiki 知识库改进计划·提取层与组织层》O3：已有 topic 页面因新增
相关页面而"再巩固"（追加内容、补充链接），而不是只能靠再凑一次聚类
阈值来生成内容重叠的新 topic 页。
"""

from __future__ import annotations

import json

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.parser import WikiLink, WikiPage, parse_page
from mini_agent.wiki.topics import (
    _find_topic_reconsolidation_candidates,
    append_to_topic_page,
    consolidate_topics,
)
from mini_agent.wiki.writer import write_page


def _make_page(pid: str, tags: list[str], body: str = "some body text", ptype: str = "entity") -> WikiPage:
    return WikiPage(id=pid, type=ptype, path=None, tags=tags, body=body)  # type: ignore[arg-type]


@pytest.fixture()
def wiki_paths(tmp_path):
    paths = AgentPaths(project_root=tmp_path)
    paths.ensure_wiki_dirs()
    return paths


# ── _find_topic_reconsolidation_candidates ──────────────────────────────


def test_reconsolidation_matches_overlapping_tags():
    topic = _make_page("topic-judge", ["judge-system"], ptype="topic")
    member = _make_page("m0", ["judge-system", "core"])
    topic.links = [WikiLink(target="m0", relation="absorbs", source="frontmatter")]
    new_page = _make_page("new0", ["judge-system", "extra"])
    unrelated = _make_page("new1", ["unrelated-topic"])

    pages_by_id = {"m0": member, "new0": new_page, "new1": unrelated, "topic-judge": topic}
    result = _find_topic_reconsolidation_candidates(
        [topic], [new_page, unrelated], pages_by_id, overlap_threshold=0.3
    )
    assert len(result) == 1
    matched_topic, matched_pages = result[0]
    assert matched_topic.id == "topic-judge"
    assert [p.id for p in matched_pages] == ["new0"]


def test_reconsolidation_skips_already_absorbed_pages():
    topic = _make_page("topic-judge", ["judge-system"], ptype="topic")
    topic.links = [WikiLink(target="already", relation="absorbs", source="frontmatter")]
    already = _make_page("already", ["judge-system"])
    pages_by_id = {"already": already, "topic-judge": topic}
    result = _find_topic_reconsolidation_candidates(
        [topic], [already], pages_by_id, overlap_threshold=0.1
    )
    assert result == []


def test_reconsolidation_no_match_below_threshold():
    topic = _make_page("topic-judge", ["judge-system"], ptype="topic")
    new_page = _make_page("new0", ["totally-different"])
    pages_by_id = {"topic-judge": topic, "new0": new_page}
    result = _find_topic_reconsolidation_candidates(
        [topic], [new_page], pages_by_id, overlap_threshold=0.3
    )
    assert result == []


# ── append_to_topic_page ─────────────────────────────────────────────────


def test_append_to_topic_page_updates_body_links_and_count(wiki_paths):
    write_page(
        wiki_paths,
        page_id="topic-judge",
        page_type="topic",
        body="既有专题正文。",
        tags=["judge-system"],
    )
    topic_path = wiki_paths.wiki_type_dir("topic") / "topic-judge.md"
    topic_page = parse_page(topic_path)

    new_page = _make_page("new0", ["judge-system"], body="新页面正文第一行")
    result = append_to_topic_page(wiki_paths, topic_page, [new_page])
    assert result == "topic-judge"

    updated = parse_page(topic_path)
    assert "新增关联" in updated.body
    assert "new0" in updated.body
    assert any(l.target == "new0" and l.relation == "absorbs" for l in updated.strong_links())
    assert updated.raw_frontmatter.get("reconsolidation_count") == 1


def test_append_to_topic_page_sets_needs_review_past_soft_cap(wiki_paths):
    write_page(
        wiki_paths,
        page_id="topic-judge",
        page_type="topic",
        body="既有专题正文。",
        tags=["judge-system"],
        extra_frontmatter={"reconsolidation_count": 8},
    )
    topic_path = wiki_paths.wiki_type_dir("topic") / "topic-judge.md"
    topic_page = parse_page(topic_path)

    new_page = _make_page("new0", ["judge-system"])
    append_to_topic_page(wiki_paths, topic_page, [new_page], soft_cap=8)

    updated = parse_page(topic_path)
    assert updated.raw_frontmatter.get("reconsolidation_count") == 9
    assert updated.raw_frontmatter.get("needs_review") is True


def test_append_to_topic_page_empty_pages_returns_none(wiki_paths):
    write_page(
        wiki_paths, page_id="topic-judge", page_type="topic", body="正文", tags=["t"]
    )
    topic_page = parse_page(wiki_paths.wiki_type_dir("topic") / "topic-judge.md")
    assert append_to_topic_page(wiki_paths, topic_page, []) is None


# ── consolidate_topics: end-to-end reconsolidation ──────────────────────


def test_consolidate_topics_reconsolidates_on_interval(wiki_paths):
    # 既有 topic 页面，已吸收 m0/m1/m2/m3
    write_page(
        wiki_paths,
        page_id="topic-judge-system",
        page_type="topic",
        body="既有专题正文，讲述 judge 系统整合。",
        tags=["judge-system"],
        links=[
            WikiLink(target=f"m{i}", relation="absorbs", source="frontmatter") for i in range(4)
        ],
        extra_frontmatter={"source_tag": "judge-system"},
    )
    for i in range(4):
        write_page(
            wiki_paths, page_id=f"m{i}", page_type="entity", body=f"member {i}", tags=["judge-system"]
        )
    # 新增一篇高度相关的新页面（未被吸收）
    write_page(
        wiki_paths,
        page_id="new-judge-note",
        page_type="entity",
        body="judge 系统的新发现",
        tags=["judge-system"],
    )

    def fake_llm(prompt: str) -> str:
        return "[]"

    created = consolidate_topics(
        wiki_paths, fake_llm, min_pages=4, reconsolidation_interval_runs=1
    )
    # 新页面被再巩固并入既有 topic，而不是生成新 topic 页
    assert created == []

    updated_topic = parse_page(wiki_paths.wiki_type_dir("topic") / "topic-judge-system.md")
    assert "new-judge-note" in updated_topic.body
    assert any(l.target == "new-judge-note" for l in updated_topic.strong_links())

    log_path = wiki_paths.wiki_topics_reconsolidation_log_path
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["topic_id"] == "topic-judge-system"
    assert "new-judge-note" in event["added_page_ids"]


def test_consolidate_topics_skips_reconsolidation_off_interval(wiki_paths):
    write_page(
        wiki_paths,
        page_id="topic-judge-system",
        page_type="topic",
        body="既有专题正文。",
        tags=["judge-system"],
        links=[WikiLink(target="m0", relation="absorbs", source="frontmatter")],
    )
    write_page(wiki_paths, page_id="m0", page_type="entity", body="member 0", tags=["judge-system"])
    write_page(
        wiki_paths,
        page_id="new-judge-note",
        page_type="entity",
        body="judge 系统的新发现",
        tags=["judge-system"],
    )

    def fake_llm(prompt: str) -> str:
        return "[]"

    # interval=5，第一次运行 run_count=1，1 % 5 != 0，本次不触发再巩固
    consolidate_topics(wiki_paths, fake_llm, min_pages=4, reconsolidation_interval_runs=5)

    updated_topic = parse_page(wiki_paths.wiki_type_dir("topic") / "topic-judge-system.md")
    assert "new-judge-note" not in updated_topic.body
    assert not wiki_paths.wiki_topics_reconsolidation_log_path.exists()


def test_consolidate_topics_run_counter_persists(wiki_paths):
    write_page(wiki_paths, page_id="m0", page_type="entity", body="member 0", tags=["t"])

    def fake_llm(prompt: str) -> str:
        return "[]"

    consolidate_topics(wiki_paths, fake_llm)
    consolidate_topics(wiki_paths, fake_llm)
    data = json.loads(wiki_paths.wiki_topics_run_counter_path.read_text(encoding="utf-8"))
    assert data["run_count"] == 2
