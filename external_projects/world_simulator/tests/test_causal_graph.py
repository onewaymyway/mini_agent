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


# ── 阶段三十三第三批（4.13 节：跨线级联的主动计算）────────────────────
# `spec_generator.resolve_causal_graph_hint()` 复用 `build_causal_
# graph()`，把聚合结果反过来喂给 `advance_step` prompt；这里只测试
# "有/无足够历史数据"两种情况下的输出，不重复测 `build_causal_graph()`
# 本身的聚合逻辑（上面已经覆盖）。


def test_resolve_causal_graph_hint_empty_without_declared_causal_lines():
    from world_simulator import spec_generator as sg

    history = [
        {"causal_links": [{"line_id": "industry", "source_line_id": "tech", "relation_type": "one_way"}]}
        for _ in range(3)
    ]
    # settings 里没有声明任何 causal_lines——即便历史数据充足，也不
    # 应该产生任何提示（没有"当前已声明因果线"这个筛选基准）。
    assert sg.resolve_causal_graph_hint({}, history) == ""
    assert sg.resolve_causal_graph_hint(None, history) == ""


def test_resolve_causal_graph_hint_empty_without_history():
    from world_simulator import spec_generator as sg

    settings = {"causal_lines": [{"id": "tech", "label": "技术线"}]}
    assert sg.resolve_causal_graph_hint(settings, []) == ""
    assert sg.resolve_causal_graph_hint(settings, None) == ""


def test_resolve_causal_graph_hint_empty_below_occurrence_threshold():
    from world_simulator import spec_generator as sg

    settings = {"causal_lines": [{"id": "tech", "label": "技术线"}]}
    # 只出现过一次的边不够"经常连带影响"，不产生提示。
    history = [{"causal_links": [{"line_id": "industry", "source_line_id": "tech"}]}]
    assert sg.resolve_causal_graph_hint(settings, history) == ""


def test_resolve_causal_graph_hint_ignores_self_loop_edges():
    from world_simulator import spec_generator as sg

    settings = {"causal_lines": [{"id": "tech", "label": "技术线"}]}
    # 同线内部的因果关系（source == target）不算"跨线级联"，即使出现
    # 次数达标也不应该被计入提示。
    history = [
        {"causal_links": [{"line_id": "tech", "source_line_id": "tech"}]} for _ in range(3)
    ]
    assert sg.resolve_causal_graph_hint(settings, history) == ""


def test_resolve_causal_graph_hint_reports_frequent_cross_line_edge():
    from world_simulator import spec_generator as sg

    settings = {"causal_lines": [{"id": "tech", "label": "技术线"}, {"id": "industry", "label": "产业线"}]}
    history = [
        {
            "causal_links": [
                {"line_id": "industry", "source_line_id": "tech", "relation_type": "one_way"}
            ]
        }
        for _ in range(3)
    ]
    hint = sg.resolve_causal_graph_hint(settings, history)
    assert "tech → industry" in hint
    assert "出现 3 次" in hint
    assert "仅作为提示参考" in hint


def test_resolve_causal_graph_hint_caps_at_three_edges():
    from world_simulator import spec_generator as sg

    settings = {
        "causal_lines": [
            {"id": f"line_{i}"} for i in range(5)
        ]
    }
    history = [
        {
            "causal_links": [
                {"line_id": f"target_{i}", "source_line_id": f"line_{i}"}
                for i in range(5)
            ]
        }
        for _ in range(2)
    ]
    hint = sg.resolve_causal_graph_hint(settings, history)
    # 每个 "line_i → target_i" 都出现 2 次、满足阈值，但提示最多只
    # 列出 3 条边，避免 prompt 膨胀。
    assert hint.count("→") == 3


# ── 第五轮方案 5.4 节（因果图先验声明 declared_causal_graph）───────


def test_resolve_causal_graph_hint_empty_without_declared_causal_graph_or_history():
    from world_simulator import spec_generator as sg

    settings = {"causal_lines": [{"id": "tech"}, {"id": "industry"}]}
    # 没有声明 declared_causal_graph、也没有历史数据时，两段式提示
    # 都不出现，返回空字符串（向后兼容原有行为）。
    assert sg.resolve_causal_graph_hint(settings, None) == ""


def test_resolve_causal_graph_hint_renders_declared_graph_without_history():
    from world_simulator import spec_generator as sg

    settings = {
        "causal_lines": [{"id": "tech"}, {"id": "industry"}],
        "declared_causal_graph": [
            {"from_line_id": "tech", "to_line_id": "industry", "note": "技术突破通常先影响行业格局"},
        ],
    }
    # 先验声明不依赖历史数据——即使这次模拟还一步都没推进，也应该
    # 展示先验关系。
    hint = sg.resolve_causal_graph_hint(settings, None)
    assert "tech → industry" in hint
    assert "技术突破通常先影响行业格局" in hint
    assert "先验声明，尚无实际历史印证" in hint
    assert "本次模拟实际发生过的因果链统计" not in hint


def test_resolve_causal_graph_hint_ignores_malformed_declared_graph_entries():
    from world_simulator import spec_generator as sg

    settings = {
        "causal_lines": [{"id": "tech"}],
        "declared_causal_graph": [
            {"from_line_id": "tech", "to_line_id": "tech"},  # 同线内部，忽略
            {"from_line_id": "", "to_line_id": "industry"},  # 缺 from，忽略
            "不是字典",  # 非法形状，忽略
        ],
    }
    assert sg.resolve_causal_graph_hint(settings, None) == ""


def test_resolve_causal_graph_hint_shows_both_sections_when_both_available():
    from world_simulator import spec_generator as sg

    settings = {
        "causal_lines": [{"id": "tech"}, {"id": "industry"}],
        "declared_causal_graph": [
            {"from_line_id": "tech", "to_line_id": "industry", "note": "常识性先验"},
        ],
    }
    history = [
        {"causal_links": [{"line_id": "industry", "source_line_id": "tech"}]}
        for _ in range(2)
    ]
    hint = sg.resolve_causal_graph_hint(settings, history)
    assert "先验声明，尚无实际历史印证" in hint
    assert "本次模拟实际发生过的因果链统计" in hint
    assert hint.index("先验声明") < hint.index("实际发生过的因果链统计")
