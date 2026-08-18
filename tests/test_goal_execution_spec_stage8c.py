"""tests/test_goal_execution_spec_stage8c.py

覆盖 next_doc/goal_output_directory_and_execution_phase_redesign_plan.md
Stage 8c 在 perception/goal_execution_spec.py 里新增的两块能力：
  - serialize_routine_steps()：把 RoutineStep 列表/dict 列表序列化成
    compute_routine_stability_signal() 期望的单段文本
  - list_spec_history() 附带每个历史版本的 execution_routine 原始步骤列表
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.perception import goal_execution_spec as ges
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(Path(tmp))


class TestSerializeRoutineSteps(unittest.TestCase):
    def test_empty_list_returns_empty_string(self):
        self.assertEqual(ges.serialize_routine_steps([]), "")
        self.assertEqual(ges.serialize_routine_steps(None), "")

    def test_routine_step_objects_joined_with_newline(self):
        steps = [ges.RoutineStep(step="扫描已有内容"), ges.RoutineStep(step="去重合并")]
        self.assertEqual(ges.serialize_routine_steps(steps), "扫描已有内容\n去重合并")

    def test_dict_form_supported(self):
        steps = [{"step": "第一步"}, {"step": "第二步"}]
        self.assertEqual(ges.serialize_routine_steps(steps), "第一步\n第二步")

    def test_blank_steps_are_dropped(self):
        steps = [ges.RoutineStep(step="有效步骤"), ges.RoutineStep(step="")]
        self.assertEqual(ges.serialize_routine_steps(steps), "有效步骤")


class TestListSpecHistoryIncludesRoutine(unittest.TestCase):
    def test_history_entries_carry_execution_routine(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            spec_v1 = ges.GoalExecutionSpec(
                goal_id="g1", version=1, confirmed=True, confirmed_at=1000.0,
                execution_routine=[ges.RoutineStep(step="步骤A")],
            )
            ges.save_spec(paths, "g1", spec_v1)

            spec_v2 = ges.GoalExecutionSpec(
                goal_id="g1", version=2, confirmed=True, confirmed_at=2000.0,
                execution_routine=[ges.RoutineStep(step="步骤B")],
            )
            ges.save_spec(paths, "g1", spec_v2)

            history = ges.list_spec_history(paths, "g1")
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["execution_routine"], ["步骤A"])

    def test_history_entries_default_to_empty_routine_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            spec_v1 = ges.GoalExecutionSpec(goal_id="g1", version=1, confirmed=True, confirmed_at=1000.0)
            ges.save_spec(paths, "g1", spec_v1)
            spec_v2 = ges.GoalExecutionSpec(goal_id="g1", version=2, confirmed=True, confirmed_at=2000.0)
            ges.save_spec(paths, "g1", spec_v2)

            history = ges.list_spec_history(paths, "g1")
            self.assertEqual(history[0]["execution_routine"], [])


if __name__ == "__main__":
    unittest.main()
