"""tests/test_attribution.py — 阶段二十九（归因/贡献拆解报告）单元测试。

对应 `next_doc/world_simulator_universal_simulator_gap_analysis_and_
roadmap_v2_plan.md` 4.21 节的验收标准：`summarize_contributions()`
能正确按来源线聚合命中 `target_field` 的 `causal_links`、给出高/中/
低相关分档，`uncertain_fields` 命中时附带提示，空输入不报错。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import attribution as attr


def test_summarize_contributions_empty_history_returns_empty_report():
    report = attr.summarize_contributions([], "cash")
    assert report.target_field == "cash"
    assert report.total_matched_links == 0
    assert report.sources == []
    assert report.caveat is None


def test_summarize_contributions_empty_target_field_returns_empty_report():
    history = [{"causal_links": [{"affected_fields": ["cash"], "line_id": "econ"}]}]
    report = attr.summarize_contributions(history, "")
    assert report.sources == []
    assert report.total_matched_links == 0


def test_summarize_contributions_ignores_links_without_matching_field():
    history = [{"causal_links": [{"affected_fields": ["stage"], "line_id": "econ"}]}]
    report = attr.summarize_contributions(history, "cash")
    assert report.sources == []


def test_summarize_contributions_groups_by_source_line_id():
    history = [
        {
            "step": 1,
            "causal_links": [
                {
                    "driver": "AI 成本下降",
                    "effect": "现金流改善",
                    "affected_fields": ["cash"],
                    "line_id": "econ",
                    "source_line_id": "tech",
                    "relation_type": "one_way",
                },
                {
                    "driver": "谈判失败",
                    "effect": "现金流恶化",
                    "affected_fields": ["cash"],
                    "line_id": "econ",
                    "source_line_id": "tech",
                    "relation_type": "feedback_loop",
                },
            ],
        },
        {
            "step": 2,
            "causal_links": [
                {
                    "driver": "个人决策失误",
                    "effect": "现金减少",
                    "affected_fields": ["cash"],
                    "line_id": "personal",
                },
            ],
        },
    ]
    report = attr.summarize_contributions(history, "cash")
    assert report.total_matched_links == 3
    assert len(report.sources) == 2

    tech_source = next(s for s in report.sources if s.source_line == "tech")
    assert tech_source.count == 2
    assert tech_source.level == "high"
    assert tech_source.relation_counts == {"one_way": 1, "feedback_loop": 1}
    assert len(tech_source.examples) == 2

    personal_source = next(s for s in report.sources if s.source_line == "personal")
    assert personal_source.count == 1
    assert personal_source.level == "medium"


def test_summarize_contributions_missing_source_and_line_id_falls_back_to_unassigned():
    history = [{"causal_links": [{"affected_fields": ["cash"]}]}]
    report = attr.summarize_contributions(history, "cash")
    assert report.sources[0].source_line == "(未归属)"


def test_summarize_contributions_matches_nested_field_by_last_segment():
    history = [{"causal_links": [{"affected_fields": ["resources.cash"], "line_id": "econ"}]}]
    report = attr.summarize_contributions(history, "cash")
    assert report.total_matched_links == 1
    assert report.sources[0].source_line == "econ"


def test_summarize_contributions_ignores_non_dict_link_entries():
    history = [{"causal_links": ["not a dict", 123, None]}]
    report = attr.summarize_contributions(history, "cash")
    assert report.sources == []


def test_summarize_contributions_examples_capped_at_three():
    history = [
        {
            "causal_links": [
                {"affected_fields": ["cash"], "line_id": "econ", "driver": f"driver_{i}"}
                for i in range(5)
            ]
        }
    ]
    report = attr.summarize_contributions(history, "cash")
    assert report.sources[0].count == 5
    assert len(report.sources[0].examples) == 3


def test_summarize_contributions_sorted_by_count_descending():
    history = [
        {
            "causal_links": [
                {"affected_fields": ["cash"], "source_line_id": "a"},
                {"affected_fields": ["cash"], "source_line_id": "b"},
                {"affected_fields": ["cash"], "source_line_id": "b"},
            ]
        }
    ]
    report = attr.summarize_contributions(history, "cash")
    assert report.sources[0].source_line == "b"
    assert report.sources[0].count == 2
    assert report.sources[1].source_line == "a"


def test_summarize_contributions_attaches_uncertainty_caveat_when_field_flagged():
    history = [
        {
            "causal_links": [{"affected_fields": ["startup_success_rate"], "line_id": "econ"}],
            "uncertain_fields": [{"field": "startup_success_rate", "confidence": "low"}],
        }
    ]
    report = attr.summarize_contributions(history, "startup_success_rate")
    assert report.caveat == "这个结果本身包含较大不确定性，归因仅供参考。"


def test_summarize_contributions_no_caveat_when_field_never_flagged():
    history = [
        {
            "causal_links": [{"affected_fields": ["cash"], "line_id": "econ"}],
            "uncertain_fields": [{"field": "other_field", "confidence": "low"}],
        }
    ]
    report = attr.summarize_contributions(history, "cash")
    assert report.caveat is None


def test_discover_target_fields_empty_history_returns_empty_list():
    assert attr.discover_target_fields([]) == []


def test_discover_target_fields_counts_and_sorts_by_frequency():
    history = [
        {
            "causal_links": [
                {"affected_fields": ["cash"]},
                {"affected_fields": ["cash", "stage"]},
                {"affected_fields": ["stage"]},
            ]
        },
        {"causal_links": [{"affected_fields": ["cash"]}]},
    ]
    fields = attr.discover_target_fields(history)
    assert [f.field for f in fields] == ["cash", "stage"]
    assert fields[0].count == 3
    assert fields[1].count == 2


def test_discover_target_fields_merges_nested_and_bare_names():
    history = [
        {
            "causal_links": [
                {"affected_fields": ["resources.cash"]},
                {"affected_fields": ["cash"]},
            ]
        }
    ]
    fields = attr.discover_target_fields(history)
    assert len(fields) == 1
    assert fields[0].field == "cash"
    assert fields[0].count == 2


def test_discover_target_fields_ignores_non_dict_links_and_blanks():
    history = [{"causal_links": ["not a dict", {"affected_fields": ["", None]}]}]
    assert attr.discover_target_fields(history) == []


def test_discovered_field_to_dict():
    field = attr.DiscoveredField(field="cash", count=2)
    assert field.to_dict() == {"field": "cash", "count": 2}


def test_contribution_report_to_dict_roundtrip_shape():
    history = [{"causal_links": [{"affected_fields": ["cash"], "line_id": "econ"}]}]
    report = attr.summarize_contributions(history, "cash")
    d = report.to_dict()
    assert d["target_field"] == "cash"
    assert d["total_matched_links"] == 1
    assert d["sources"][0]["source_line"] == "econ"
    assert d["sources"][0]["level_label"] == "高相关"
