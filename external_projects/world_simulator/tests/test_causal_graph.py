"""tests/test_causal_graph.py — 阶段二十七（因果线耦合结构化）单元测试。

对应 `next_doc/world_simulator_universal_simulator_gap_analysis_and_
roadmap_v2_plan.md` 4.19 节的验收标准：`build_causal_graph()` 能正确
按 `(source_line_id, line_id)` 聚合 `relation_type` 计数，未给出的
字段有合理兜底，空输入不报错。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import causal_graph as cg


def test_build_causal_graph_empty_history_returns_empty_list():
    assert cg.build_causal_graph([]) == []


def test_build_causal_graph_state_with_no_causal_links_is_skipped():
    history = [{"causal_links": []}, {"causal_links": None}]
    assert cg.build_causal_graph(history) == []


def test_build_causal_graph_aggregates_by_source_and_target_line():
    history = [
        {
            "causal_links": [
                {
                    "driver": "AI 成本下降",
                    "effect": "企业加速采用",
                    "line_id": "industry",
                    "source_line_id": "tech",
                    "relation_type": "one_way",
                },
                {
                    "driver": "产业需求反向推动研发",
                    "effect": "技术投入增加",
                    "line_id": "tech",
                    "source_line_id": "industry",
                    "relation_type": "two_way",
                },
            ]
        },
        {
            "causal_links": [
                {
                    "driver": "生产率提升带动利润",
                    "line_id": "industry",
                    "source_line_id": "tech",
                    "relation_type": "feedback_loop",
                },
            ]
        },
    ]
    edges = cg.build_causal_graph(history)
    assert len(edges) == 2

    tech_to_industry = next(
        e for e in edges if e.source_line == "tech" and e.target_line == "industry"
    )
    assert tech_to_industry.relation_counts == {"one_way": 1, "feedback_loop": 1}
    assert tech_to_industry.total == 2

    industry_to_tech = next(
        e for e in edges if e.source_line == "industry" and e.target_line == "tech"
    )
    assert industry_to_tech.relation_counts == {"two_way": 1}


def test_build_causal_graph_missing_fields_fall_back_to_unassigned_and_one_way():
    history = [{"causal_links": [{"driver": "某个变化", "effect": "某个后果"}]}]
    edges = cg.build_causal_graph(history)
    assert len(edges) == 1
    edge = edges[0]
    assert edge.source_line == "(未归属)"
    assert edge.target_line == "(未归属)"
    assert edge.relation_counts == {"one_way": 1}


def test_build_causal_graph_unknown_relation_type_falls_back_to_one_way():
    history = [
        {
            "causal_links": [
                {"line_id": "a", "source_line_id": "b", "relation_type": "not_a_real_type"}
            ]
        }
    ]
    edges = cg.build_causal_graph(history)
    assert edges[0].relation_counts == {"one_way": 1}


def test_build_causal_graph_sorted_by_total_descending():
    history = [
        {
            "causal_links": [
                {"line_id": "y", "source_line_id": "x"},
                {"line_id": "b", "source_line_id": "a"},
                {"line_id": "b", "source_line_id": "a"},
            ]
        }
    ]
    edges = cg.build_causal_graph(history)
    assert edges[0].source_line == "a" and edges[0].total == 2
    assert edges[1].source_line == "x" and edges[1].total == 1


def test_build_causal_graph_examples_capped_at_three():
    history = [
        {
            "causal_links": [
                {"line_id": "a", "source_line_id": "b", "driver": f"driver_{i}"}
                for i in range(5)
            ]
        }
    ]
    edges = cg.build_causal_graph(history)
    assert edges[0].total == 5
    assert len(edges[0].examples) == 3


def test_build_causal_graph_ignores_non_dict_link_entries():
    history = [{"causal_links": ["not a dict", 123, None]}]
    assert cg.build_causal_graph(history) == []


def test_causal_edge_to_dict_roundtrip_shape():
    history = [{"causal_links": [{"line_id": "a", "source_line_id": "b"}]}]
    edge = cg.build_causal_graph(history)[0]
    d = edge.to_dict()
    assert d["source_line"] == "b"
    assert d["target_line"] == "a"
    assert d["total"] == 1
    assert "relation_counts" in d and "examples" in d


def test_relation_type_label_known_and_unknown():
    assert cg.relation_type_label("feedback_loop") == "反馈循环"
    assert cg.relation_type_label("weird") == "weird"


def test_format_edges_for_display_produces_readable_lines():
    history = [
        {
            "causal_links": [
                {"line_id": "industry", "source_line_id": "tech", "relation_type": "one_way"}
            ]
        }
    ]
    edges = cg.build_causal_graph(history)
    lines = cg.format_edges_for_display(edges)
    assert lines == ["tech → industry：1 次单向影响"]
