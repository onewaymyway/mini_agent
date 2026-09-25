"""tests/test_analysis.py — world_simulator/analysis.py 单元测试。

`analysis.py` 是纯函数模块，不需要任何打桩，直接对着构造好的 `vars`
列表验证聚合结果。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator.analysis import (
    aggregate_field_stats,
    discover_numeric_fields,
    normalize_objectives,
    rank_by_objectives,
)


def test_numeric_field_stats():
    vars_list = [{"cash": 1000}, {"cash": 1200}, {"cash": 800}]
    stats = aggregate_field_stats(vars_list, ["cash"])
    assert len(stats) == 1
    s = stats[0]
    assert s.kind == "numeric"
    assert s.count == 3
    assert s.mean == 1000.0
    assert s.min == 800.0
    assert s.max == 1200.0
    assert s.stdev > 0


def test_numeric_field_single_sample_has_zero_stdev():
    stats = aggregate_field_stats([{"cash": 500}], ["cash"])
    assert stats[0].stdev == 0.0


def test_categorical_field_distribution():
    vars_list = [
        {"outcome": "success"}, {"outcome": "success"}, {"outcome": "failure"},
    ]
    stats = aggregate_field_stats(vars_list, ["outcome"])
    s = stats[0]
    assert s.kind == "categorical"
    assert s.count == 3
    assert s.distribution == {"success": 2, "failure": 1}


def test_missing_field_returns_placeholder_not_omitted():
    stats = aggregate_field_stats([{"cash": 1}, {"cash": 2}], ["not_a_real_field"])
    assert len(stats) == 1
    assert stats[0].kind == "missing"
    assert stats[0].count == 0
    assert stats[0].mean is None


def test_nested_field_path():
    vars_list = [
        {"resources": {"amount": 10}},
        {"resources": {"amount": 30}},
    ]
    stats = aggregate_field_stats(vars_list, ["resources.amount"])
    assert stats[0].kind == "numeric"
    assert stats[0].mean == 20.0


def test_field_paths_order_preserved_and_length_matches():
    vars_list = [{"a": 1, "b": "x"}]
    stats = aggregate_field_stats(vars_list, ["b", "missing", "a"])
    assert [s.field for s in stats] == ["b", "missing", "a"]
    assert len(stats) == 3


def test_discover_numeric_fields_empty_list_returns_empty():
    assert discover_numeric_fields([]) == []


def test_discover_numeric_fields_top_level_and_nested():
    vars_list = [
        {"age": 30, "resources": {"cash": 100}},
        {"age": 31, "resources": {"cash": 150}},
    ]
    fields = discover_numeric_fields(vars_list)
    assert fields == ["age", "resources.cash"]


def test_discover_numeric_fields_excludes_categorical_fields():
    vars_list = [
        {"age": 30, "stage": "seed"},
        {"age": 31, "stage": "growth"},
    ]
    fields = discover_numeric_fields(vars_list)
    assert fields == ["age"]


def test_discover_numeric_fields_excludes_bool_values():
    vars_list = [{"is_active": True}, {"is_active": False}]
    assert discover_numeric_fields(vars_list) == []


def test_discover_numeric_fields_requires_majority_numeric():
    vars_list = [
        {"cash": 100},
        {"cash": 200},
        {"cash": "约 500 元"},
    ]
    assert discover_numeric_fields(vars_list) == ["cash"]


def test_discover_numeric_fields_skips_mostly_non_numeric_field():
    vars_list = [
        {"cash": "约 100 元"},
        {"cash": "约 200 元"},
        {"cash": 300},
    ]
    assert discover_numeric_fields(vars_list) == []


def test_discover_numeric_fields_ignores_non_dict_entries():
    vars_list = ["not a dict", {"cash": 100}]
    assert discover_numeric_fields(vars_list) == ["cash"]


def test_discover_numeric_fields_sorted_alphabetically():
    vars_list = [{"zeta": 1, "alpha": 2}]
    assert discover_numeric_fields(vars_list) == ["alpha", "zeta"]


def test_normalize_objectives_plain_strings_have_no_field():
    result = normalize_objectives(["资产净值", "工作满意度"])
    assert [o.label for o in result] == ["资产净值", "工作满意度"]
    assert all(o.field is None for o in result)


def test_normalize_objectives_structured_dict():
    result = normalize_objectives(
        [{"label": "资产净值", "field": "resources.cash", "direction": "max"}]
    )
    assert len(result) == 1
    assert result[0].field == "resources.cash"
    assert result[0].direction == "max"


def test_normalize_objectives_invalid_direction_defaults_to_max():
    result = normalize_objectives([{"label": "x", "field": "x", "direction": "sideways"}])
    assert result[0].direction == "max"


def test_normalize_objectives_mixed_list():
    result = normalize_objectives(["纯文本", {"label": "现金", "field": "cash"}])
    assert len(result) == 2
    assert result[0].field is None
    assert result[1].field == "cash"


def test_rank_by_objectives_no_field_declared_returns_empty():
    vars_list = [{"cash": 100}, {"cash": 200}]
    ranked = rank_by_objectives(vars_list, ["资产净值"])
    assert ranked == []


def test_rank_by_objectives_sorts_by_score_descending():
    vars_list = [{"cash": 100}, {"cash": 300}, {"cash": 200}]
    objectives = [{"label": "资产净值", "field": "cash", "direction": "max"}]
    ranked = rank_by_objectives(vars_list, objectives)
    assert len(ranked) == 3
    assert ranked[0].index == 1  # cash=300 最优
    assert ranked[0].score == 1
    assert ranked[0].values == {"资产净值": 300.0}


def test_rank_by_objectives_min_direction():
    vars_list = [{"risk": 0.8}, {"risk": 0.2}]
    objectives = [{"label": "风险", "field": "risk", "direction": "min"}]
    ranked = rank_by_objectives(vars_list, objectives)
    assert ranked[0].index == 1  # risk=0.2 最优（越小越好）


def test_rank_by_objectives_custom_labels():
    vars_list = [{"cash": 100}, {"cash": 200}]
    objectives = [{"label": "现金", "field": "cash"}]
    ranked = rank_by_objectives(vars_list, objectives, labels=["分支 A", "分支 B"])
    assert ranked[0].label == "分支 B"


def test_rank_by_objectives_missing_value_not_scored():
    vars_list = [{"cash": 100}, {}]
    objectives = [{"label": "现金", "field": "cash"}]
    ranked = rank_by_objectives(vars_list, objectives)
    by_index = {r.index: r for r in ranked}
    assert by_index[1].values["现金"] is None
    assert by_index[1].score == 0
