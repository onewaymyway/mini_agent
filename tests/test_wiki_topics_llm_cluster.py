"""
tests/test_wiki_topics_llm_cluster.py — wiki/topics.py P3 LLM 聚类路径测试

覆盖《wiki 式知识库改进计划》P3：不依赖 embedding、只用 LLM 聚类补齐
tag+链接密度路径覆盖不到的候选，以及两套候选池合并去重的行为。
"""

from __future__ import annotations

import json

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.parser import WikiLink, WikiPage
from mini_agent.wiki.topics import (
    TopicCandidate,
    _merge_candidate_pools,
    _slugify_topic_tag,
    consolidate_topics,
    find_topic_candidates_llm_cluster,
)
from mini_agent.wiki.writer import write_page


def _make_page(pid: str, tags: list[str], body: str = "some body text") -> WikiPage:
    return WikiPage(id=pid, type="entity", path=None, tags=tags, body=body)  # type: ignore[arg-type]


# ── _slugify_topic_tag ───────────────────────────────────────────────────


def test_slugify_topic_tag_basic():
    tag = _slugify_topic_tag("Judge 统一迁移", set())
    assert tag == "Judge-统一迁移"


def test_slugify_topic_tag_dedupes_against_taken():
    taken = {"foo"}
    tag = _slugify_topic_tag("foo", taken)
    assert tag == "foo-2"


def test_slugify_topic_tag_empty_falls_back():
    tag = _slugify_topic_tag("   ", set())
    assert tag == "cluster"


# ── find_topic_candidates_llm_cluster ───────────────────────────────────


def test_llm_cluster_parses_valid_response():
    pages = [_make_page(f"p{i}", ["misc"]) for i in range(5)]

    def fake_llm(prompt: str) -> str:
        return json.dumps(
            [
                {"topic": "判断系统整合", "page_ids": ["p0", "p1", "p2"]},
                {"topic": "太短不算", "page_ids": ["p3"]},  # 少于 min_pages，应被过滤
            ]
        )

    candidates = find_topic_candidates_llm_cluster(pages, fake_llm, min_pages=3)
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.source == "llm_cluster"
    assert cand.label == "判断系统整合"
    assert cand.page_ids == ["p0", "p1", "p2"]


def test_llm_cluster_handles_malformed_json_gracefully():
    pages = [_make_page(f"p{i}", ["misc"]) for i in range(5)]

    def fake_llm(prompt: str) -> str:
        return "not json at all, sorry"

    candidates = find_topic_candidates_llm_cluster(pages, fake_llm, min_pages=3)
    assert candidates == []


def test_llm_cluster_handles_llm_exception():
    pages = [_make_page(f"p{i}", ["misc"]) for i in range(5)]

    def failing_llm(prompt: str) -> str:
        raise RuntimeError("boom")

    candidates = find_topic_candidates_llm_cluster(pages, failing_llm, min_pages=3)
    assert candidates == []


def test_llm_cluster_ignores_unknown_page_ids():
    pages = [_make_page(f"p{i}", ["misc"]) for i in range(4)]

    def fake_llm(prompt: str) -> str:
        return json.dumps(
            [{"topic": "话题", "page_ids": ["p0", "p1", "ghost-id"]}]
        )

    candidates = find_topic_candidates_llm_cluster(pages, fake_llm, min_pages=3)
    # ghost-id 不在候选池内应被剔除，剩余 2 篇低于 min_pages=3，整簇作废
    assert candidates == []


def test_llm_cluster_skips_when_pool_too_small():
    pages = [_make_page("p0", ["misc"]), _make_page("p1", ["misc"])]
    calls = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return "[]"

    candidates = find_topic_candidates_llm_cluster(pages, fake_llm, min_pages=3)
    assert candidates == []
    assert calls == []  # 候选池不足 min_pages 时不应该调用 LLM


def test_llm_cluster_respects_exclude_page_ids():
    pages = [_make_page(f"p{i}", ["misc"]) for i in range(5)]

    def fake_llm(prompt: str) -> str:
        assert "p0" not in prompt
        return json.dumps([{"topic": "剩余话题", "page_ids": ["p1", "p2", "p3"]}])

    candidates = find_topic_candidates_llm_cluster(
        pages, fake_llm, min_pages=3, exclude_page_ids={"p0"}
    )
    assert len(candidates) == 1
    assert "p0" not in candidates[0].page_ids


# ── _merge_candidate_pools ───────────────────────────────────────────────


def test_merge_pools_keeps_non_overlapping_candidates():
    rule = [TopicCandidate(tag="a", page_ids=["p0", "p1"], link_density=0.6)]
    llm = [TopicCandidate(tag="b", page_ids=["p2", "p3"], link_density=-1.0, source="llm_cluster")]
    merged = _merge_candidate_pools(rule, llm)
    assert len(merged) == 2


def test_merge_pools_drops_overlapping_llm_candidate():
    rule = [TopicCandidate(tag="a", page_ids=["p0", "p1", "p2"], link_density=0.6)]
    llm = [
        TopicCandidate(
            tag="b", page_ids=["p0", "p1", "p3"], link_density=-1.0, source="llm_cluster"
        )
    ]
    # jaccard(p0,p1,p2 | p0,p1,p3) = 2/4 = 0.5 >= threshold(0.5) -> dropped
    merged = _merge_candidate_pools(rule, llm, overlap_threshold=0.5)
    assert merged == rule


# ── consolidate_topics: end-to-end with both pools ──────────────────────


@pytest.fixture()
def wiki_paths(tmp_path):
    paths = AgentPaths(project_root=tmp_path)
    paths.ensure_wiki_dirs()
    return paths


def test_consolidate_topics_merges_rule_and_llm_pools(wiki_paths):
    # 4 篇共享 tag 且强链接密集的页面 -> 命中规则路径
    for i in range(4):
        write_page(
            wiki_paths,
            page_id=f"rule-{i}",
            page_type="entity",
            body=f"rule cluster page {i}",
            tags=["judge-system"],
            links=[
                WikiLink(target=f"rule-{j}", relation="depends_on", source="frontmatter")
                for j in range(4)
                if j != i
            ],
        )
    # 3 篇 tag 各异、无强链接，但语义相关 -> 只能靠 LLM 聚类命中
    for i in range(3):
        write_page(
            wiki_paths,
            page_id=f"sem-{i}",
            page_type="entity",
            body=f"semantic cluster page {i}",
            tags=[f"misc-{i}"],
        )

    def fake_llm(prompt: str) -> str:
        if "id=" in prompt:
            # find_topic_candidates_llm_cluster 的聚类请求格式：每行 "- id=..."
            return json.dumps(
                [{"topic": "语义相关簇", "page_ids": ["sem-0", "sem-1", "sem-2"]}]
            )
        # generate_topic_page 的专题页正文生成请求
        return "综合叙事正文内容。"

    created = consolidate_topics(wiki_paths, fake_llm, min_pages=4, min_density=0.5)
    assert len(created) == 2
    sources = set()
    for pid in created:
        page_path = wiki_paths.wiki_type_dir("topic") / f"{pid}.md"
        assert page_path.exists()


def test_consolidate_topics_can_disable_llm_clustering(wiki_paths):
    for i in range(3):
        write_page(
            wiki_paths,
            page_id=f"sem-{i}",
            page_type="entity",
            body=f"semantic cluster page {i}",
            tags=[f"misc-{i}"],
        )

    calls = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps([{"topic": "x", "page_ids": ["sem-0", "sem-1", "sem-2"]}])

    created = consolidate_topics(
        wiki_paths, fake_llm, min_pages=4, use_llm_clustering=False
    )
    assert created == []
    assert calls == []
