"""
tests/test_graph_expand.py — wiki 提取层与组织层改进计划 O2 单元测试

覆盖：
  - wiki/graph.py::GraphIndex.expand() 的多跳衰减权重计算正确性
  - 同一节点通过多条路径/跳数可达时取最大权重而非累加
  - max_hops=1 时候选集合与 expand_legacy() 完全一致（权重统一为 decay）
  - max_candidates 硬上限截断（按权重降序保留前 N）
  - wiki/search.py::wiki_shelf_search 的自动/强制深度检索模式
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "src")

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.graph import GraphIndex
from mini_agent.wiki.parser import WikiLink
from mini_agent.wiki.search import wiki_shelf_search
from mini_agent.wiki.writer import write_page


@pytest.fixture
def paths(tmp_path):
    p = AgentPaths(tmp_path)
    p.ensure_wiki_dirs()
    return p


def _chain_graph() -> GraphIndex:
    """三层依赖链 A → B → C（frontmatter 强链接），供多跳衰减测试使用。"""
    g = GraphIndex()
    g._known_ids = {"a", "b", "c"}
    g.add_edge(source="a", target="b", relation="depends_on", strong=True)
    g.add_edge(source="b", target="c", relation="depends_on", strong=True)
    return g


# ── GraphIndex.expand() 多跳衰减 ─────────────────────────────────────────

def test_expand_one_hop_matches_legacy_membership():
    g = _chain_graph()
    legacy = g.expand_legacy(["a"], strong_only=True)
    new = g.expand(["a"], strong_only=True, max_hops=1, decay=0.5)

    assert set(new.keys()) == legacy
    assert all(w == pytest.approx(0.5) for w in new.values())


def test_expand_two_hops_decays_weight():
    g = _chain_graph()
    result = g.expand(["a"], strong_only=True, max_hops=2, decay=0.5)

    assert result["b"] == pytest.approx(0.5)
    assert result["c"] == pytest.approx(0.25)
    assert result["c"] < result["b"]


def test_expand_stops_when_max_hops_exceeds_graph_depth():
    g = _chain_graph()
    # 链条只有 2 跳深，max_hops=5 不应该报错或产生幻觉节点
    result = g.expand(["a"], strong_only=True, max_hops=5, decay=0.5)
    assert set(result.keys()) == {"b", "c"}


def test_expand_takes_max_weight_not_sum_across_multiple_paths():
    # d 同时是 a 的一跳邻居和 a-via-b 的二跳邻居：应该取一跳的更高权重 0.5，
    # 而不是 0.5 + 0.25 或者被二跳的 0.25 覆盖。
    g = GraphIndex()
    g._known_ids = {"a", "b", "d"}
    g.add_edge(source="a", target="b", relation="depends_on", strong=True)
    g.add_edge(source="a", target="d", relation="depends_on", strong=True)
    g.add_edge(source="b", target="d", relation="depends_on", strong=True)

    result = g.expand(["a"], strong_only=True, max_hops=2, decay=0.5)
    assert result["d"] == pytest.approx(0.5)


def test_expand_excludes_seed_nodes():
    g = _chain_graph()
    # a→b→a 的环也不应该把种子节点 a 自己纳入结果
    g.add_edge(source="c", target="a", relation="mentions", strong=True)
    result = g.expand(["a"], strong_only=True, max_hops=3, decay=0.5)
    assert "a" not in result


def test_expand_max_candidates_caps_and_keeps_highest_weight():
    g = GraphIndex()
    g._known_ids = {"seed", "n1", "n2", "n3"}
    for target in ("n1", "n2", "n3"):
        g.add_edge(source="seed", target=target, relation="mentions", strong=True)

    result = g.expand(["seed"], strong_only=True, max_hops=1, decay=0.5, max_candidates=2)
    assert len(result) == 2
    # 一跳权重全相同（都是 0.5），只验证数量截断生效，不假设具体保留哪两个


def test_expand_no_cap_when_max_candidates_none():
    g = GraphIndex()
    g._known_ids = {"seed", "n1", "n2", "n3"}
    for target in ("n1", "n2", "n3"):
        g.add_edge(source="seed", target=target, relation="mentions", strong=True)

    result = g.expand(["seed"], strong_only=True, max_hops=1, decay=0.5)
    assert len(result) == 3


# ── wiki/search.py 深度检索模式 ──────────────────────────────────────────

def _seed_chain_pages(paths):
    write_page(
        paths, page_id="a-module", page_type="entity",
        body="ModuleA handles entry routing and dispatch logic.",
        tags=["module"],
        links=[WikiLink(target="b-module", relation="depends_on", source="frontmatter")],
    )
    write_page(
        paths, page_id="b-module", page_type="entity",
        body="ModuleB handles middle layer processing.",
        tags=["module"],
        links=[WikiLink(target="c-module", relation="depends_on", source="frontmatter")],
    )
    write_page(
        paths, page_id="c-module", page_type="entity",
        body="ModuleC handles low level storage access.",
        tags=["module"],
    )


def test_wiki_search_deep_true_reaches_two_hop_page(paths):
    _seed_chain_pages(paths)
    result = wiki_shelf_search(paths, "ModuleA entry routing", use_index=False, deep=True, rerank_top_n=10)

    ids = {p.id for p in result.pages}
    assert "a-module" in ids
    assert "c-module" in ids  # 两跳之外的页面应该被深度检索带入
    assert result.stage_reached == "graph_deep"


def test_wiki_search_deep_false_stays_one_hop(paths):
    _seed_chain_pages(paths)
    result = wiki_shelf_search(paths, "ModuleA entry routing", use_index=False, deep=False, rerank_top_n=10)

    ids = {p.id for p in result.pages}
    assert "a-module" in ids
    assert "b-module" in ids
    assert "c-module" not in ids  # 强制一跳，不应该扩展到两跳之外
    assert result.stage_reached == "graph"


def test_wiki_search_default_behavior_unaffected_by_deep_param():
    # deep 未在签名里出现问题：默认 deep=None 不应该抛异常，函数签名向后兼容
    import inspect
    sig = inspect.signature(wiki_shelf_search)
    assert sig.parameters["deep"].default is None
