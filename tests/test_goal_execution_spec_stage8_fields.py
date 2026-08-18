"""tests/test_goal_execution_spec_stage8_fields.py

覆盖 next_doc/goal_output_directory_and_execution_phase_redesign_plan.md
Stage 8 新增的规范层/内容层两层模型字段：output_mode / execution_routine /
cadence / new_topic_discovery / hardening_target / sub_exploration。
"""
from __future__ import annotations

from mini_agent.perception.goal_execution_spec import (
    GoalExecutionSpec,
    RoutineStep,
)


def test_defaults_are_backward_compatible():
    spec = GoalExecutionSpec.from_dict({"goal_id": "g1"})
    assert spec.output_mode == "converging"
    assert spec.execution_routine == []
    assert spec.cadence == ""
    assert spec.new_topic_discovery == "none"
    assert spec.hardening_target == ""
    assert spec.sub_exploration == ""
    # 旧字段仍为空时 is_empty() 行为不变
    assert spec.is_empty() is True


def test_roundtrip_new_fields():
    spec = GoalExecutionSpec(
        goal_id="g2",
        output_mode="accretive",
        execution_routine=[RoutineStep(step="扫描已有条目"), RoutineStep(step="写入新条目")],
        cadence="daily",
        new_topic_discovery="intrinsic",
    )
    d = spec.to_dict()
    restored = GoalExecutionSpec.from_dict(d)
    assert restored.output_mode == "accretive"
    assert [r.step for r in restored.execution_routine] == ["扫描已有条目", "写入新条目"]
    assert restored.cadence == "daily"
    assert restored.new_topic_discovery == "intrinsic"
    assert restored.is_empty() is False  # execution_routine 非空即非空规范


def test_invalid_enum_values_fall_back_to_default():
    spec = GoalExecutionSpec.from_dict({
        "goal_id": "g3",
        "output_mode": "not_a_real_mode",
        "new_topic_discovery": "bogus",
    })
    assert spec.output_mode == "converging"
    assert spec.new_topic_discovery == "none"


def test_capability_hardening_target_roundtrip():
    spec = GoalExecutionSpec(
        goal_id="g4",
        output_mode="capability_hardening",
        hardening_target=".claude/skills/browser-cdp",
    )
    restored = GoalExecutionSpec.from_dict(spec.to_dict())
    assert restored.output_mode == "capability_hardening"
    assert restored.hardening_target == ".claude/skills/browser-cdp"


def test_render_prompt_block_includes_stage8_hints():
    spec = GoalExecutionSpec(
        goal_id="g5",
        output_mode="hybrid",
        new_topic_discovery="intrinsic",
        hardening_target="",
        sub_exploration="持续探索新的股票信息源，不参与主轨阶段判定",
        execution_routine=[RoutineStep(step="抓取热点股票")],
    )
    block = spec.render_prompt_block()
    assert "new_topic_discovery=intrinsic" in block
    assert "子探索说明" in block
    assert "抓取热点股票" in block


def test_render_prompt_block_empty_spec_still_empty():
    spec = GoalExecutionSpec(goal_id="g6")
    assert spec.render_prompt_block() == ""


def test_render_summary_for_user_includes_output_mode():
    spec = GoalExecutionSpec(goal_id="g7", output_mode="capability_hardening",
                              hardening_target=".claude/skills/finance-data-toolkit")
    summary = spec.render_summary_for_user()
    assert "output_mode：capability_hardening" in summary
    assert "finance-data-toolkit" in summary
