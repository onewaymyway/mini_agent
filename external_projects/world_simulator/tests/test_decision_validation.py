"""tests/test_decision_validation.py — 阶段三十三 4.12 节第一步
（`next_doc/world_simulator_potential_causal_space_and_decision_
engine_plan.md`）单元测试。

只覆盖 `decision_validation.py` 里三个归一化函数的行为，以及
`compute_option_warnings()` 能正确转发给 `engine/option_heuristics.
py`——具体的宏观重合/指标调节检测细节已经在
`tests/test_option_heuristics.py` 里覆盖，这里不重复。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import decision_validation
from world_simulator.state_model import ChoiceOption


def test_normalize_risk_level_none_stays_none():
    assert decision_validation.normalize_risk_level(None) is None


def test_normalize_risk_level_valid_value_passthrough():
    assert decision_validation.normalize_risk_level("High") == "high"


def test_normalize_risk_level_unknown_value_falls_back_to_medium():
    assert decision_validation.normalize_risk_level("nonsense") == "medium"


def test_normalize_urgency_none_stays_none():
    assert decision_validation.normalize_urgency(None) is None


def test_normalize_urgency_valid_value_passthrough():
    assert decision_validation.normalize_urgency("Critical") == "critical"


def test_normalize_urgency_unknown_value_falls_back_to_medium():
    assert decision_validation.normalize_urgency("whatever") == "medium"


def test_normalize_action_type_default_and_unknown_fall_back_to_single():
    assert decision_validation.normalize_action_type(None) == "single"
    assert decision_validation.normalize_action_type("") == "single"
    assert decision_validation.normalize_action_type("bogus") == "single"


def test_normalize_action_type_valid_value_passthrough():
    assert decision_validation.normalize_action_type("Combo") == "combo"


def test_choice_option_from_dict_uses_decision_validation_normalizers():
    opt = ChoiceOption.from_dict(
        {
            "id": "x",
            "label": "l",
            "risk_level": "weird",
            "urgency": "weird",
            "action_type": "weird",
        }
    )
    assert opt.risk_level == "medium"
    assert opt.urgency == "medium"
    assert opt.action_type == "single"


def test_compute_option_warnings_forwards_to_option_heuristics():
    opt = ChoiceOption(id="a", label="提升竞争力 20%", description="")
    warnings = decision_validation.compute_option_warnings([opt], key_drivers=[])
    assert any(w["kind"] == "metric_adjustment_pattern" for w in warnings)


def test_compute_option_warnings_empty_inputs_returns_empty_list():
    assert decision_validation.compute_option_warnings([], []) == []
