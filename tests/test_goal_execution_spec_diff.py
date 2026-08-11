"""
[goal_execution_spec_generation_plan.md §6.2 / implementation_record.md
§12 后续建议顺序第 1 条"差异高亮"] 覆盖 `apps/mini_agent_kanban/app.py::
_compute_spec_diff()`——`revise()`/"从模板重新起草"整段覆盖草稿后，前端
纯本地对比新旧两份 `GoalExecutionSpec` JSON 算出的"新增/删除/改写"标注。

只测试差异计算这个纯函数（不依赖任何 LLM/网络/文件 IO），渲染部分
（`_render_spec_diff()`）依赖 streamlit 的 UI 上下文，不在这里覆盖。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_kanban"))

from app import _compute_spec_diff  # noqa: E402


def _spec(**overrides) -> dict:
    base = {
        "deliverables": [],
        "handoff_fields": [],
        "sub_directories": [],
        "per_cycle_criteria": [],
        "overall_completion_criteria": [],
        "special_constraints": [],
    }
    base.update(overrides)
    return base


def test_identical_specs_produce_empty_diff():
    old = _spec(deliverables=[{"name": "a.md", "description": "x"}])
    new = _spec(deliverables=[{"name": "a.md", "description": "x"}])
    assert _compute_spec_diff(old, new) == {}


def test_added_item_detected_by_identity_key():
    old = _spec(deliverables=[{"name": "a.md", "description": "x"}])
    new = _spec(deliverables=[
        {"name": "a.md", "description": "x"},
        {"name": "b.md", "description": "y"},
    ])
    diff = _compute_spec_diff(old, new)
    assert diff["deliverables"]["added"] == [{"name": "b.md", "description": "y"}]
    assert diff["deliverables"]["removed"] == []
    assert diff["deliverables"]["changed"] == []


def test_removed_item_detected():
    old = _spec(deliverables=[
        {"name": "a.md", "description": "x"},
        {"name": "b.md", "description": "y"},
    ])
    new = _spec(deliverables=[{"name": "a.md", "description": "x"}])
    diff = _compute_spec_diff(old, new)
    assert diff["deliverables"]["removed"] == [{"name": "b.md", "description": "y"}]
    assert diff["deliverables"]["added"] == []


def test_changed_item_detected_when_same_key_different_content():
    old = _spec(handoff_fields=[{"key": "cursor", "description": "old"}])
    new = _spec(handoff_fields=[{"key": "cursor", "description": "new"}])
    diff = _compute_spec_diff(old, new)
    assert diff["handoff_fields"]["changed"] == [
        ({"key": "cursor", "description": "old"}, {"key": "cursor", "description": "new"})
    ]
    assert diff["handoff_fields"]["added"] == []
    assert diff["handoff_fields"]["removed"] == []


def test_criteria_sections_matched_by_text_field():
    old = _spec(per_cycle_criteria=[{"text": "报告存在", "verification_method": "file_check"}])
    new = _spec(per_cycle_criteria=[{"text": "报告存在", "verification_method": "manual_review"}])
    diff = _compute_spec_diff(old, new)
    assert "per_cycle_criteria" in diff
    assert len(diff["per_cycle_criteria"]["changed"]) == 1


def test_special_constraints_plain_string_list_diff():
    old = _spec(special_constraints=["不要修改 src/", "保持中文注释"])
    new = _spec(special_constraints=["不要修改 src/", "不要泄露真实姓名"])
    diff = _compute_spec_diff(old, new)
    assert diff["special_constraints"]["added"] == ["不要泄露真实姓名"]
    assert diff["special_constraints"]["removed"] == ["保持中文注释"]
    # special_constraints 是纯字符串列表，没有"改写"这个概念——同一条
    # 要么原样保留（不出现在 diff 里），要么被视为"删旧增新"两条。
    assert diff["special_constraints"]["changed"] == []


def test_unrelated_locked_fields_not_in_diff_when_unchanged():
    """字段级锁定（§6.2）场景：被锁定的 section 内容原样保留，不应该
    出现在 diff 结果里——只有真的发生变化的 section 才会被展示。"""
    old = _spec(
        deliverables=[{"name": "a.md", "description": "x"}],
        special_constraints=["锁定的约束"],
    )
    new = _spec(
        deliverables=[{"name": "a.md", "description": "x"}],
        special_constraints=["锁定的约束"],
        handoff_fields=[{"key": "new_key", "description": "新增的跨轮字段"}],
    )
    diff = _compute_spec_diff(old, new)
    assert "deliverables" not in diff
    assert "special_constraints" not in diff
    assert diff["handoff_fields"]["added"] == [{"key": "new_key", "description": "新增的跨轮字段"}]


def test_missing_optional_sections_default_to_empty_list():
    """spec dict 里字段缺失（比如后端某个版本没写这个 key）时按空列表
    处理，不应该抛异常。"""
    old = {}
    new = {"deliverables": [{"name": "a.md", "description": "x"}]}
    diff = _compute_spec_diff(old, new)
    assert diff["deliverables"]["added"] == [{"name": "a.md", "description": "x"}]
