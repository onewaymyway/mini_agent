"""tests/test_multi_entity.py — 第八轮批次五：多主体 Entity/
Relationship，从自由文本到结构化连通图。

对应 `next_doc/world_simulator_c_category_precision_upgrade_
improvement_plan.md` 第 6 节。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator.multi_entity import build_entity_graph, find_relationship_path


# ---------------------------------------------------------------------------
# build_entity_graph()
# ---------------------------------------------------------------------------


def test_build_entity_graph_basic_adjacency_is_undirected():
    relationships = [
        {"from": "甲方", "to": "乙方", "kind": "rival"},
        {"from": "乙方", "to": "丙方", "kind": "ally"},
    ]
    entities = {"甲方": {}, "乙方": {}, "丙方": {}}
    graph = build_entity_graph(relationships, entities)
    assert graph == {
        "甲方": ["乙方"],
        "乙方": ["甲方", "丙方"],
        "丙方": ["乙方"],
    }


def test_build_entity_graph_skips_relationships_referencing_unknown_entities():
    """`from`/`to` 有一个不在 `entities` 里时静默跳过，不报错、不
    污染图。"""
    relationships = [
        {"from": "甲方", "to": "神秘第三方", "kind": "rival"},
        {"from": "甲方", "to": "乙方", "kind": "ally"},
    ]
    entities = {"甲方": {}, "乙方": {}}
    graph = build_entity_graph(relationships, entities)
    assert graph == {"甲方": ["乙方"], "乙方": ["甲方"]}


def test_build_entity_graph_accepts_iterable_of_ids_not_just_dict():
    """`entities` 参数除了 `vars.entities` 字典本身，也接受调用方
    已经取好的 id 可迭代对象（比如一个 list）。"""
    relationships = [{"from": "A", "to": "B"}]
    graph = build_entity_graph(relationships, ["A", "B"])
    assert graph == {"A": ["B"], "B": ["A"]}


def test_build_entity_graph_empty_entities_returns_empty_dict():
    relationships = [{"from": "A", "to": "B"}]
    assert build_entity_graph(relationships, {}) == {}
    assert build_entity_graph(relationships, None) == {}


def test_build_entity_graph_empty_relationships_returns_empty_dict():
    assert build_entity_graph([], {"A": {}, "B": {}}) == {}
    assert build_entity_graph(None, {"A": {}, "B": {}}) == {}


def test_build_entity_graph_deduplicates_multiple_relationships_between_same_pair():
    """同一对实体之间声明了多条关系时，邻接表里只保留一条边（不重复
    出现在邻居列表里）。"""
    relationships = [
        {"from": "甲方", "to": "乙方", "kind": "rival"},
        {"from": "甲方", "to": "乙方", "kind": "ally", "note": "既竞争又合作的复杂关系"},
    ]
    entities = {"甲方": {}, "乙方": {}}
    graph = build_entity_graph(relationships, entities)
    assert graph == {"甲方": ["乙方"], "乙方": ["甲方"]}


def test_build_entity_graph_self_loop_is_ignored_but_node_still_present():
    relationships = [{"from": "甲方", "to": "甲方", "kind": "other"}]
    entities = {"甲方": {}}
    graph = build_entity_graph(relationships, entities)
    assert graph == {"甲方": []}


def test_build_entity_graph_malformed_relationship_items_are_skipped():
    """`normalize_relationships()` 已经负责丢弃 `from`/`to` 缺失的
    条目，这里回归确认那一层清洗仍然生效。"""
    relationships = [{"to": "乙方"}, {"from": "甲方"}, "不是字典"]
    entities = {"甲方": {}, "乙方": {}}
    assert build_entity_graph(relationships, entities) == {}


# ---------------------------------------------------------------------------
# find_relationship_path()
# ---------------------------------------------------------------------------


def test_find_relationship_path_returns_shortest_path():
    graph = {"A": ["B"], "B": ["A", "C"], "C": ["B", "D"], "D": ["C"]}
    assert find_relationship_path(graph, "A", "D") == ["A", "B", "C", "D"]


def test_find_relationship_path_direct_neighbor():
    graph = {"A": ["B"], "B": ["A"]}
    assert find_relationship_path(graph, "A", "B") == ["A", "B"]


def test_find_relationship_path_same_start_and_end_returns_single_node_path():
    graph = {"A": ["B"], "B": ["A"]}
    assert find_relationship_path(graph, "A", "A") == ["A"]


def test_find_relationship_path_returns_none_when_unreachable():
    graph = {"A": ["B"], "B": ["A"], "C": []}
    assert find_relationship_path(graph, "A", "C") is None


def test_find_relationship_path_returns_none_when_node_missing_from_graph():
    graph = {"A": ["B"], "B": ["A"]}
    assert find_relationship_path(graph, "A", "不存在的实体") is None
    assert find_relationship_path(graph, "不存在的实体", "A") is None


def test_find_relationship_path_respects_max_depth():
    """A-B-C-D 之间有 3 条边，`max_depth=2` 应该找不到（超出深度），
    `max_depth=3`（默认）应该找到。"""
    graph = {"A": ["B"], "B": ["A", "C"], "C": ["B", "D"], "D": ["C"]}
    assert find_relationship_path(graph, "A", "D", max_depth=2) is None
    assert find_relationship_path(graph, "A", "D", max_depth=3) == ["A", "B", "C", "D"]


def test_find_relationship_path_picks_shorter_route_when_multiple_exist():
    """A-B-D（2 步）和 A-C-E-D（3 步）都能到达，应该返回更短的那条。"""
    graph = {
        "A": ["B", "C"],
        "B": ["A", "D"],
        "C": ["A", "E"],
        "E": ["C", "D"],
        "D": ["B", "E"],
    }
    result = find_relationship_path(graph, "A", "D")
    assert result == ["A", "B", "D"]
