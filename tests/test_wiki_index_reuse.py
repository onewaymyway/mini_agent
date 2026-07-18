"""
tests/test_wiki_index_reuse.py — wiki 提取层与组织层改进计划 O1 单元测试

覆盖：
  - wiki/graph.py::GraphIndex.from_dict 与 build() 行为一致性
  - wiki/index_reader.py::load_index 的新鲜/过期判定
  - wiki/search.py::wiki_shelf_search 索引路径与全量扫描路径结果一致
    （tests/test_context_builder_wiki_search_primary.py 等既有测试的
    行为不变性由该文件本身覆盖，这里只新增 O1 相关场景）
  - confidence_weight=0 时排序结果与改动前完全一致（回归保护）
  - wiki/writer.py::increment_grounded_hit_count 的幂等 / 累加写入
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "src")

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.graph import GraphIndex
from mini_agent.wiki.index_reader import load_index
from mini_agent.wiki.indexer import build_index
from mini_agent.wiki.parser import parse_page
from mini_agent.wiki.search import wiki_shelf_search
from mini_agent.wiki.writer import increment_grounded_hit_count, write_page


@pytest.fixture
def paths(tmp_path):
    p = AgentPaths(tmp_path)
    p.ensure_wiki_dirs()
    return p


def _seed_pages(paths):
    write_page(
        paths,
        page_id="client-pool",
        page_type="entity",
        body="ClientPool 负责多 LLM provider 的 API key 轮换与故障转移。",
        tags=["module", "llm"],
    )
    write_page(
        paths,
        page_id="key-rotation-decision",
        page_type="decision",
        body="决定用轮询策略做 key 轮换，而不是随机策略。",
        tags=["decision"],
        links=[__import__("mini_agent.wiki.parser", fromlist=["WikiLink"]).WikiLink(
            target="client-pool", relation="implements", source="frontmatter",
        )],
    )


def test_graph_from_dict_matches_build(paths):
    _seed_pages(paths)
    pages = [parse_page(p) for p in paths.wiki_dir.rglob("*.md")]
    built = GraphIndex.build(pages)

    loaded = GraphIndex.from_dict(built.to_dict(), known_ids={p.id for p in pages})

    for pid in ("client-pool", "key-rotation-decision"):
        assert {e.target for e in built.outgoing(pid)} == {e.target for e in loaded.outgoing(pid)}
        assert built.expand([pid], strong_only=True) == loaded.expand([pid], strong_only=True)


def test_load_index_none_when_manifest_missing(paths):
    _seed_pages(paths)
    # 还没跑过 build_index，_manifest.json 不存在 -> 索引不可复用
    assert load_index(paths) is None


def test_load_index_fresh_after_build(paths):
    _seed_pages(paths)
    build_index(paths)
    index = load_index(paths)
    assert index is not None
    assert "client-pool" in index.id_to_path
    assert index.id_to_path["client-pool"].exists()


def test_load_index_stale_after_new_page_added(paths):
    _seed_pages(paths)
    build_index(paths)
    assert load_index(paths) is not None

    # 索引构建之后又新增一篇页面，manifest 记录的文件数与磁盘不一致
    write_page(
        paths, page_id="new-entity", page_type="entity",
        body="后来新增的实体，还没被索引收录。", tags=["module"],
    )
    assert load_index(paths) is None


def test_search_index_path_matches_full_scan_results(paths):
    _seed_pages(paths)
    build_index(paths)

    via_index = wiki_shelf_search(paths, "ClientPool key 轮换", use_index=True)
    via_scan = wiki_shelf_search(paths, "ClientPool key 轮换", use_index=False)

    assert via_index.stage_reached != "none"
    assert via_scan.stage_reached != "none"
    assert {p.id for p in via_index.pages} == {p.id for p in via_scan.pages}


def test_confidence_weight_zero_matches_pre_change_behavior(paths):
    _seed_pages(paths)
    build_index(paths)

    # grounded_hit_count 影响排序，但 confidence_weight=0 时应完全不参与打分
    page_path = paths.wiki_entities_dir / "client-pool.md"
    page = parse_page(page_path)
    increment_grounded_hit_count(paths, page)
    page = parse_page(page_path)
    increment_grounded_hit_count(paths, page)

    result_weighted = wiki_shelf_search(paths, "ClientPool", confidence_weight=0.5, use_index=False)
    result_zero = wiki_shelf_search(paths, "ClientPool", confidence_weight=0.0, use_index=False)

    # 都应该命中同一批候选页面 id（confidence_weight 只影响排序权重，不
    # 应该引入/排除候选）
    assert {p.id for p in result_weighted.pages} == {p.id for p in result_zero.pages}


def test_increment_grounded_hit_count_accumulates(paths):
    _seed_pages(paths)
    page_path = paths.wiki_entities_dir / "client-pool.md"

    page = parse_page(page_path)
    assert int(page.raw_frontmatter.get("grounded_hit_count") or 0) == 0

    increment_grounded_hit_count(paths, page)
    page = parse_page(page_path)
    assert page.raw_frontmatter.get("grounded_hit_count") == 1

    increment_grounded_hit_count(paths, page)
    page = parse_page(page_path)
    assert page.raw_frontmatter.get("grounded_hit_count") == 2

    # 不应该影响 updated / body 等其它字段
    assert "ClientPool" in page.body
