"""tests/test_relationship.py — 阶段三十一（Relationship 最小起步版）
单元测试。

对应 `next_doc/world_simulator_universal_simulator_gap_analysis_and_
roadmap_v2_plan.md` 4.24 节的验收标准：`normalize_relationships()`
丢弃无效条目、对不认识的 `kind`/`strength` 给安全默认值；
`summarize_by_subject()` 正确按 `from` 分组。
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
