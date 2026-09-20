"""tests/test_decision_opportunity_aggregation.py — 第五轮方案 5.2
节（`next_doc/world_simulator_decision_engine_round2_gap_analysis_
plan.md`）：`engine/advance.py::_build_decision_opportunity()` 新增
的三个批次级聚合字段（`max_urgency`/`max_risk`/`baseline_option_
id`）的单元测试。

直接构造 `SimState`/`ChoiceOption` 调用 `_build_decision_
opportunity()`，不经过完整的 `advance()` 流程——这三个字段是纯
Python 聚合逻辑，不涉及任何 LLM 调用或 workflow 编排，没有必要
在这一层测试里重新搭一遍 `advance()` 的完整 mock 环境。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator.engine.advance import _build_decision_opportunity
from world_simulator.state_model import ChoiceOption, SimState


def _state_with_options(options) -> SimState:
    return SimState(step=1, summary="", vars={}, options=options)


def test_decision_opportunity_is_none_when_no_options():
    state = _state_with_options([])
    assert _build_decision_opportunity(state) is None


def test_max_urgency_and_max_risk_take_the_highest_declared_tier():
    options = [
        ChoiceOption(id="a", label="A", description="", urgency="low", risk_level="medium"),
        ChoiceOption(id="b", label="B", description="", urgency="critical", risk_level="low"),
        ChoiceOption(id="c", label="C", description="", urgency="medium", risk_level="high"),
    ]
    opp = _build_decision_opportunity(_state_with_options(options))
    assert opp["max_urgency"] == "critical"
    assert opp["max_risk"] == "high"


def test_max_urgency_and_max_risk_are_none_when_nothing_declared():
    options = [ChoiceOption(id="a", label="A", description="")]
    opp = _build_decision_opportunity(_state_with_options(options))
    assert opp["max_urgency"] is None
    assert opp["max_risk"] is None


def test_max_urgency_ignores_options_that_did_not_declare_it():
    """部分选项声明了 `urgency`，部分没有——只在已声明的里面取最高档，
    未声明的选项不应该被当成"最低档"参与比较（`None` 不等于 `low`）。"""
    options = [
        ChoiceOption(id="a", label="A", description="", urgency=None),
        ChoiceOption(id="b", label="B", description="", urgency="medium"),
    ]
    opp = _build_decision_opportunity(_state_with_options(options))
    assert opp["max_urgency"] == "medium"


def test_baseline_option_id_finds_continue_prefixed_option():
    options = [
        ChoiceOption(id="continue_status_quo", label="维持现状", description=""),
        ChoiceOption(id="switch_job", label="换工作", description=""),
    ]
    opp = _build_decision_opportunity(_state_with_options(options))
    assert opp["baseline_option_id"] == "continue_status_quo"


def test_baseline_option_id_is_none_without_continue_prefixed_option():
    options = [ChoiceOption(id="switch_job", label="换工作", description="")]
    opp = _build_decision_opportunity(_state_with_options(options))
    assert opp["baseline_option_id"] is None


def test_decision_opportunity_still_carries_existing_fields():
    """5.2 节只是新增字段，不应该影响既有的 `trigger_line_ids`/
    `trigger_node_ids`/`decision_reason`/`context_note` 四个字段。"""
    state = _state_with_options(
        [ChoiceOption(id="a", label="A", description="")]
    )
    state.line_updates = {"tech": {"summary": "s"}}
    state.decision_reason = "技术线出现关键窗口"
    opp = _build_decision_opportunity(state)
    assert opp["trigger_line_ids"] == ["tech"]
    assert opp["decision_reason"] == "技术线出现关键窗口"
    assert opp["context_note"] == ""
