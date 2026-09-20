"""tests/test_option_heuristics.py — 阶段三十三第三批（`next_doc/
world_simulator_potential_causal_space_and_decision_engine_plan.md`
4.4 节可干预性下沉 + 4.5 节现实行动语义各自的"辅助校验"部分）单元
测试。

只覆盖 `option_heuristics.py` 里两个纯函数的检测逻辑：命中/不命中的
边界情况，以及"这只是弱信号，允许误报"这条既有取舍本身（用一个
明知会被误判的合理选项做反例，确认代码行为符合方案预期，而不是
反过来要求代码变得更"聪明"）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator.engine.option_heuristics import (
    compute_option_warnings,
    detect_macro_overlap_warnings,
    detect_metric_adjustment_warnings,
)
from world_simulator.state_model import ChoiceOption


def test_macro_overlap_warning_triggered_when_option_repeats_key_driver():
    options = [
        ChoiceOption(id="a", label="应对全球经济衰退", description=""),
        ChoiceOption(id="b", label="调整职业风险", description="降低对单一雇主的依赖"),
    ]
    warnings = detect_macro_overlap_warnings(options, key_drivers=["全球经济衰退"])
    assert len(warnings) == 1
    assert warnings[0]["option_id"] == "a"
    assert warnings[0]["kind"] == "macro_event_overlap"


def test_macro_overlap_warning_not_triggered_without_overlap():
    options = [ChoiceOption(id="b", label="调整职业风险", description="降低对单一雇主的依赖")]
    warnings = detect_macro_overlap_warnings(options, key_drivers=["全球经济衰退"])
    assert warnings == []


def test_macro_overlap_ignores_short_key_drivers():
    """短于 4 个字的驱动因素不参与重合检测——太短几乎必然"重合"，
    没有信息量。"""
    options = [ChoiceOption(id="a", label="加息", description="")]
    warnings = detect_macro_overlap_warnings(options, key_drivers=["加息"])
    assert warnings == []


def test_macro_overlap_returns_empty_when_no_key_drivers():
    options = [ChoiceOption(id="a", label="随便什么选项", description="")]
    assert detect_macro_overlap_warnings(options, key_drivers=[]) == []


def test_metric_adjustment_warning_triggered_by_percentage_pattern():
    options = [ChoiceOption(id="a", label="提升职业竞争力 20%", description="")]
    warnings = detect_metric_adjustment_warnings(options)
    assert len(warnings) == 1
    assert warnings[0]["option_id"] == "a"
    assert warnings[0]["kind"] == "metric_adjustment_pattern"


def test_metric_adjustment_warning_triggered_by_bare_delta_pattern():
    options = [ChoiceOption(id="a", label="模型能力 +0.1", description="")]
    warnings = detect_metric_adjustment_warnings(options)
    assert len(warnings) == 1


def test_metric_adjustment_warning_not_triggered_by_plain_action_language():
    options = [ChoiceOption(id="a", label="转向推理模型路线", description="换工作")]
    assert detect_metric_adjustment_warnings(options) == []


def test_metric_adjustment_pattern_does_not_flag_time_quantities():
    """数字后面紧跟常见量词（"减少 1 个月"这种描述时间/数量的正常
    现实行动）不应该被裸数值增减记号那条规则命中。"""
    options = [ChoiceOption(id="a", label="缩短交付周期", description="减少 1 个月的等待时间")]
    assert detect_metric_adjustment_warnings(options) == []


def test_metric_adjustment_pattern_accepts_expected_false_positive():
    """方案明确接受的误报："提高利率 0.25%"是完全合理的现实行动，
    但仍会被这条弱信号规则命中——这是刻意的取舍，不是需要修的 bug。"""
    options = [ChoiceOption(id="a", label="提高利率 0.25%", description="")]
    warnings = detect_metric_adjustment_warnings(options)
    assert len(warnings) == 1


def test_compute_option_warnings_combines_both_checks():
    options = [
        ChoiceOption(id="a", label="应对全球经济衰退", description=""),
        ChoiceOption(id="b", label="提升收入 15%", description=""),
        ChoiceOption(id="c", label="调整职业风险", description=""),
    ]
    warnings = compute_option_warnings(options, key_drivers=["全球经济衰退"])
    kinds_by_option = {w["option_id"]: w["kind"] for w in warnings}
    assert kinds_by_option.get("a") == "macro_event_overlap"
    assert kinds_by_option.get("b") == "metric_adjustment_pattern"
    assert "c" not in kinds_by_option
