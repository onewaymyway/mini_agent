"""tests/test_quality_signals.py — 阶段三十四第四批（第五轮方案 5.7
节，`next_doc/world_simulator_decision_engine_round2_gap_analysis_
plan.md`）单元测试。

验收点（对应方案 5.7 节）：五项统计数字在已知输入下计算正确；空
`history`/空 `causal_lines` 不报错，返回合理的全零/`None` 默认值。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import quality_signals as qs


def test_empty_history_returns_zero_counts_and_none_ratios():
    result = qs.summarize_quality_signals([])
    assert result["total_steps"] == 0
    assert result["explainability"]["decision_points"] == 0
    assert result["explainability"]["ratio"] is None
    assert result["cross_line_influence"]["ratio"] is None
    assert result["branch_diversity"]["ratio"] is None
    assert result["expansion_usage"]["ratio"] is None
    assert result["uncertainty_coverage"]["ratio"] is None


def test_explainability_density_counts_top_level_and_option_level_reason():
    history = [
        {"options": []},  # 不是决策点，不计入分母
        {
            "options": [{"id": "a", "label": "A"}],
            "decision_reason": "出现了新的分岔",
        },
        {
            "options": [
                {"id": "b", "label": "B", "action_reason": "值得一试"},
                {"id": "c", "label": "C"},
            ],
            "decision_reason": "",
        },
        {
            "options": [{"id": "d", "label": "D"}],
            "decision_reason": "",
        },
    ]
    result = qs.summarize_quality_signals(history)
    assert result["explainability"]["decision_points"] == 3
    assert result["explainability"]["with_reason"] == 2
    assert result["explainability"]["ratio"] == 2 / 3


def test_branch_diversity_counts_multi_option_batches():
    history = [
        {"options": [{"id": "a", "label": "A"}]},
        {"options": [{"id": "b", "label": "B"}, {"id": "c", "label": "C"}]},
        {"options": []},
    ]
    result = qs.summarize_quality_signals(history)
    assert result["branch_diversity"]["decision_points"] == 2
    assert result["branch_diversity"]["multi_option"] == 1
    assert result["branch_diversity"]["ratio"] == 0.5


def test_cross_line_influence_ratio():
    history = [
        {
            "causal_links": [
                {"driver": "x", "effect": "y", "source_line_id": "tech"},
                {"driver": "x2", "effect": "y2"},
            ]
        },
        {"causal_links": [{"driver": "x3", "effect": "y3", "source_line_id": ""}]},
    ]
    result = qs.summarize_quality_signals(history)
    assert result["cross_line_influence"]["total_causal_links"] == 3
    assert result["cross_line_influence"]["with_source_line"] == 1
    assert result["cross_line_influence"]["ratio"] == 1 / 3


def test_uncertainty_coverage_ratio():
    history = [
        {"uncertain_fields": [{"field": "success_rate", "confidence": "low"}]},
        {"uncertain_fields": []},
        {},
    ]
    result = qs.summarize_quality_signals(history)
    assert result["total_steps"] == 3
    assert result["uncertainty_coverage"]["with_uncertain_fields"] == 1
    assert result["uncertainty_coverage"]["ratio"] == 1 / 3


def test_expansion_usage_recurses_into_children_and_sub_branches():
    causal_lines = [
        {
            "id": "tech",
            "future_tree": {
                "branches": [
                    {
                        "id": "fast",
                        "expansion_level": "expanded",
                        "children": [
                            {"id": "fast_a", "expansion_level": "compressed"},
                        ],
                        "sub_branches": [
                            {"id": "fast_sub", "expansion_level": "expanded"},
                        ],
                    },
                    {"id": "slow", "expansion_level": "compressed"},
                ]
            },
        },
        {"id": "industry", "future_tree": {}},
    ]
    result = qs.summarize_quality_signals([], causal_lines=causal_lines)
    # 4 个分支：fast（expanded）、fast_a（compressed，children 展开）、
    # fast_sub（expanded，sub_branches 展开）、slow（compressed）。
    assert result["expansion_usage"]["total_branches"] == 4
    assert result["expansion_usage"]["expanded"] == 2
    assert result["expansion_usage"]["ratio"] == 0.5


def test_expansion_usage_none_when_no_causal_lines_given():
    result = qs.summarize_quality_signals([])
    assert result["expansion_usage"]["total_branches"] == 0
    assert result["expansion_usage"]["ratio"] is None


def test_accepts_simstate_objects_not_only_dicts():
    from world_simulator.state_model import ChoiceOption, SimState

    state = SimState(
        step=0,
        summary="s",
        options=[ChoiceOption(id="a", label="A", action_reason="值得")],
        decision_reason="",
    )
    result = qs.summarize_quality_signals([state])
    assert result["explainability"]["decision_points"] == 1
    assert result["explainability"]["with_reason"] == 1
