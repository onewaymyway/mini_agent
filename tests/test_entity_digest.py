"""
tests/test_entity_digest.py — wiki 提取层与组织层改进计划 E3 单元测试

覆盖：
  - wiki/entity_digest.py::build_entity_digest 的排序（relevance_hint 命中
    > grounded_hit_count > 最近更新）、数量上限、无实体时的空字符串降级
  - build_entity_digest_section 的表头包装 / 空实体时不留孤立表头
  - history/world_extraction.py::EntityCandidate 的 reused_existing_id
    解析往返
  - wiki/world_writer.py::consolidate_pending 里 reused_existing_id 命中
    时优先合并进指定页面；分数过低（误判）时忽略该字段、退回规则判重
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "src")

from mini_agent.history.world_extraction import EntityCandidate, FactCandidate
from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.entity_digest import build_entity_digest, build_entity_digest_section
from mini_agent.wiki.parser import parse_page
from mini_agent.wiki.world_writer import consolidate_pending, queue_entities
from mini_agent.wiki.writer import increment_grounded_hit_count, write_page


@pytest.fixture
def paths(tmp_path):
    p = AgentPaths(tmp_path)
    p.ensure_wiki_dirs()
    return p


def _seed_entity(paths, page_id, *, entity_type="module", body_desc="示例描述。", tags=None):
    write_page(
        paths,
        page_id=page_id,
        page_type="entity",
        body=f"## 概述\n\n{body_desc}\n\n## 当前状态\n\n占位。\n",
        tags=tags if tags is not None else [entity_type],
    )


# ── build_entity_digest ──────────────────────────────────────────────────

def test_empty_when_no_entity_pages(paths):
    assert build_entity_digest(paths) == ""
    assert build_entity_digest_section(paths) == ""


def test_digest_includes_type_label_and_first_sentence(paths):
    _seed_entity(paths, "client-pool", entity_type="module", body_desc="负责 API key 轮换与故障转移。")
    digest = build_entity_digest(paths)
    assert "client-pool" in digest
    assert "模块" in digest
    assert "负责 API key 轮换与故障转移" in digest


def test_digest_respects_max_entities(paths):
    for i in range(5):
        _seed_entity(paths, f"entity-{i}", body_desc=f"第 {i} 个实体。")
    digest = build_entity_digest(paths, max_entities=2)
    assert len(digest.splitlines()) == 2


def test_digest_ranks_relevance_hint_first(paths):
    _seed_entity(paths, "unrelated-thing", tags=["module"], body_desc="不相关的模块。")
    _seed_entity(paths, "goal-mode", tags=["module", "goal-mode"], body_desc="目标追踪子系统。")

    digest = build_entity_digest(paths, relevance_hint="goal-mode")
    lines = digest.splitlines()
    assert lines[0].startswith("- goal-mode")


def test_digest_ranks_grounded_hit_count_over_recency(paths):
    _seed_entity(paths, "rarely-hit", body_desc="很少被命中。")
    _seed_entity(paths, "often-hit", body_desc="经常被命中。")

    page = parse_page(paths.wiki_entities_dir / "often-hit.md")
    increment_grounded_hit_count(paths, page)
    page = parse_page(paths.wiki_entities_dir / "often-hit.md")
    increment_grounded_hit_count(paths, page)

    digest = build_entity_digest(paths)
    lines = digest.splitlines()
    assert lines[0].startswith("- often-hit")


def test_digest_section_has_header_and_body(paths):
    _seed_entity(paths, "client-pool", body_desc="负责 key 轮换。")
    section = build_entity_digest_section(paths)
    assert "Already-known entities" in section
    assert "client-pool" in section


# ── EntityCandidate.reused_existing_id round-trip ───────────────────────

def test_entity_candidate_reused_existing_id_round_trip():
    candidate = EntityCandidate(
        name="客户端池", entity_type="module", description="负责 key 轮换",
        reused_existing_id="client-pool",
    )
    restored = EntityCandidate.from_dict(candidate.to_dict())
    assert restored.reused_existing_id == "client-pool"


def test_entity_candidate_reused_existing_id_defaults_to_none():
    candidate = EntityCandidate.from_dict({"name": "x", "description": "y"})
    assert candidate.reused_existing_id is None

    candidate = EntityCandidate.from_dict({"name": "x", "description": "y", "reused_existing_id": ""})
    assert candidate.reused_existing_id is None


# ── consolidate_pending() reused_existing_id 合并行为 ────────────────────

def test_consolidate_merges_into_reused_existing_id_when_score_sufficient(paths):
    _seed_entity(paths, "client-pool", body_desc="负责多 LLM provider 的 API key 轮换与故障转移。")

    candidate = EntityCandidate(
        name="客户端池",
        entity_type="module",
        description="负责 API key 轮换与故障转移的新增认知：支持按 provider 权重分配。",
        reused_existing_id="client-pool",
    )
    queue_entities(paths, [candidate])
    report = consolidate_pending(paths)

    assert len(report.actions) == 1
    assert report.actions[0].kind == "entity_updated"
    assert report.actions[0].page_id == "client-pool"

    # 没有新建页面，且新增认知被追加进同一篇页面
    entity_files = list(paths.wiki_entities_dir.glob("*.md"))
    assert len(entity_files) == 1
    page = parse_page(paths.wiki_entities_dir / "client-pool.md")
    assert "按 provider 权重分配" in page.body


def test_consolidate_ignores_reused_existing_id_when_score_too_low(paths):
    _seed_entity(paths, "client-pool", body_desc="负责多 LLM provider 的 API key 轮换与故障转移。")

    # 描述与 client-pool 完全不相关（模型误判 reused_existing_id），
    # 应当忽略该字段，退回规则判重（规则判重本身分数也不够，因此新建页面）。
    candidate = EntityCandidate(
        name="完全不相关的东西",
        entity_type="concept",
        description="一个和 key 轮换毫无关系的全新概念。",
        reused_existing_id="client-pool",
    )
    queue_entities(paths, [candidate])
    report = consolidate_pending(paths)

    assert len(report.actions) == 1
    assert report.actions[0].kind == "entity_created"
    # 原页面未被追加误判内容
    original = parse_page(paths.wiki_entities_dir / "client-pool.md")
    assert "毫无关系" not in original.body


def test_consolidate_reused_existing_id_missing_page_falls_back(paths):
    # reused_existing_id 指向一个不存在的 id：不应该报错，应退回常规判重/新建
    candidate = EntityCandidate(
        name="新实体",
        entity_type="module",
        description="第一次出现的实体。",
        reused_existing_id="does-not-exist",
    )
    queue_entities(paths, [candidate])
    report = consolidate_pending(paths)

    assert len(report.actions) == 1
    assert report.actions[0].kind == "entity_created"
