"""tests/test_relationship.py — 阶段三十一（Relationship 最小起步版）
+ 阶段三十六第二批（Influence Field / Relationship 完整机制）单元
测试。

对应 `next_doc/world_simulator_universal_simulator_gap_analysis_and_
roadmap_v2_plan.md` 4.24 节，以及 `next_doc/world_simulator_event_
driven_engine_and_full_architecture_plan.md` 2.2 节的验收标准：
`normalize_relationships()` 丢弃无效条目、对不认识的 `kind`/
`strength`/`reversible` 给安全默认值，正确解析 `delay_steps`/
`propagation_path`；`summarize_by_subject()` 正确按 `from` 分组；
`queue_pending_effect()`/`due_pending_effects()` 的入队/到期正确性。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import relationship as rel


def test_normalize_relationships_empty_input_returns_empty_list():
    assert rel.normalize_relationships(None) == []
    assert rel.normalize_relationships([]) == []


def test_normalize_relationships_drops_entries_missing_from_or_to():
    raw = [
        {"from": "甲方", "kind": "rival"},
        {"to": "乙方", "kind": "ally"},
        {"from": "", "to": "丙方"},
        {"from": "甲方", "to": "乙方", "kind": "rival"},
    ]
    result = rel.normalize_relationships(raw)
    assert len(result) == 1
    assert result[0]["from"] == "甲方"
    assert result[0]["to"] == "乙方"


def test_normalize_relationships_falls_back_unknown_kind_and_strength():
    raw = [{"from": "a", "to": "b", "kind": "nonsense", "strength": "extreme"}]
    result = rel.normalize_relationships(raw)
    assert result[0]["kind"] == "other"
    assert result[0]["strength"] == "medium"


def test_normalize_relationships_keeps_valid_kind_and_strength_with_labels():
    raw = [{"from": "a", "to": "b", "kind": "ally", "strength": "high", "note": "并肩作战"}]
    result = rel.normalize_relationships(raw)
    assert result[0]["kind"] == "ally"
    assert result[0]["kind_label"] == "同盟/合作"
    assert result[0]["strength"] == "high"
    assert result[0]["strength_label"] == "强"
    assert result[0]["note"] == "并肩作战"


def test_normalize_relationships_ignores_non_dict_items():
    raw = ["not a dict", 123, {"from": "a", "to": "b"}]
    result = rel.normalize_relationships(raw)
    assert len(result) == 1


def test_summarize_by_subject_groups_by_from():
    raw = [
        {"from": "甲方", "to": "乙方", "kind": "rival"},
        {"from": "甲方", "to": "丙方", "kind": "ally"},
        {"from": "乙方", "to": "甲方", "kind": "rival"},
    ]
    grouped = rel.summarize_by_subject(raw)
    assert set(grouped.keys()) == {"甲方", "乙方"}
    assert len(grouped["甲方"]) == 2
    assert len(grouped["乙方"]) == 1


def test_summarize_by_subject_empty_input_returns_empty_dict():
    assert rel.summarize_by_subject(None) == {}


# ─────────────────────────────────────────────────────────────
# 第二批（2.2 节）新字段：delay_steps / propagation_path / reversible
# ─────────────────────────────────────────────────────────────


def test_normalize_relationships_defaults_delay_and_path_when_absent():
    raw = [{"from": "a", "to": "b"}]
    result = rel.normalize_relationships(raw)
    assert result[0]["delay_steps"] == 0
    assert result[0]["propagation_path"] == []
    assert "reversible" not in result[0]
    assert "reversible_label" not in result[0]


def test_normalize_relationships_parses_delay_steps_and_path_and_reversible():
    raw = [
        {
            "from": "a",
            "to": "b",
            "delay_steps": 3,
            "propagation_path": ["line_x", "line_y"],
            "reversible": "hard_to_reverse",
        }
    ]
    result = rel.normalize_relationships(raw)
    assert result[0]["delay_steps"] == 3
    assert result[0]["propagation_path"] == ["line_x", "line_y"]
    assert result[0]["reversible"] == "hard_to_reverse"
    assert result[0]["reversible_label"] == "难以撤销"


def test_normalize_relationships_negative_or_invalid_delay_steps_falls_back_to_zero():
    raw = [
        {"from": "a", "to": "b", "delay_steps": -5},
        {"from": "a", "to": "b", "delay_steps": "not a number"},
    ]
    result = rel.normalize_relationships(raw)
    assert result[0]["delay_steps"] == 0
    assert result[1]["delay_steps"] == 0


def test_normalize_relationships_invalid_reversible_is_dropped():
    raw = [{"from": "a", "to": "b", "reversible": "maybe"}]
    result = rel.normalize_relationships(raw)
    assert "reversible" not in result[0]


def test_normalize_relationships_non_list_propagation_path_falls_back_to_empty():
    raw = [{"from": "a", "to": "b", "propagation_path": "not a list"}]
    result = rel.normalize_relationships(raw)
    assert result[0]["propagation_path"] == []


# ─────────────────────────────────────────────────────────────
# queue_pending_effect() / due_pending_effects()
# ─────────────────────────────────────────────────────────────


def test_queue_pending_effect_computes_due_step_from_delay_steps():
    relationships = [{"from": "a", "to": "b", "delay_steps": 3}]
    pending = rel.queue_pending_effect(
        None, relationships=relationships, relationship_ref="0", triggered_at_step=5
    )
    assert len(pending) == 1
    assert pending[0]["relationship_ref"] == "0"
    assert pending[0]["triggered_at_step"] == 5
    assert pending[0]["due_step"] == 8


def test_queue_pending_effect_resolves_ref_by_explicit_id():
    relationships = [{"id": "trust_break", "from": "a", "to": "b", "delay_steps": 2}]
    pending = rel.queue_pending_effect(
        None,
        relationships=relationships,
        relationship_ref="trust_break",
        triggered_at_step=1,
    )
    assert pending[0]["due_step"] == 3


def test_queue_pending_effect_unresolvable_ref_falls_back_to_zero_delay():
    pending = rel.queue_pending_effect(
        None,
        relationships=[{"from": "a", "to": "b"}],
        relationship_ref="does_not_exist",
        triggered_at_step=4,
    )
    assert pending[0]["due_step"] == 4


def test_queue_pending_effect_dedupes_same_ref_and_step():
    relationships = [{"from": "a", "to": "b", "delay_steps": 1}]
    pending = rel.queue_pending_effect(
        None, relationships=relationships, relationship_ref="0", triggered_at_step=1
    )
    pending = rel.queue_pending_effect(
        pending, relationships=relationships, relationship_ref="0", triggered_at_step=1
    )
    assert len(pending) == 1


def test_due_pending_effects_filters_by_current_step():
    pending = [
        {"relationship_ref": "0", "triggered_at_step": 1, "due_step": 3},
        {"relationship_ref": "1", "triggered_at_step": 2, "due_step": 10},
    ]
    due = rel.due_pending_effects(pending, current_step=5)
    assert len(due) == 1
    assert due[0]["relationship_ref"] == "0"


def test_due_pending_effects_empty_input_returns_empty_list():
    assert rel.due_pending_effects(None, current_step=5) == []
    assert rel.due_pending_effects([], current_step=5) == []


def test_due_pending_effects_does_not_mutate_input():
    pending = [{"relationship_ref": "0", "triggered_at_step": 1, "due_step": 1}]
    result = rel.due_pending_effects(pending, current_step=1)
    result[0]["due_step"] = 999
    assert pending[0]["due_step"] == 1

