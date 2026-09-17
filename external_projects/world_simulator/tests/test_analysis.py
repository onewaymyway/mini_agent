"""tests/test_analysis.py — world_simulator/analysis.py 单元测试。

`analysis.py` 是纯函数模块，不需要任何打桩，直接对着构造好的 `vars`
列表验证聚合结果。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator.analysis import aggregate_field_stats


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
