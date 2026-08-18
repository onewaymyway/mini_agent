"""tests/test_goal_cron_bridge_converge_spec_draft.py

覆盖 next_doc/goal_output_directory_and_execution_phase_redesign_plan.md
§4 / Stage 4：`goal_cron_bridge._maybe_auto_generate_converge_spec_draft()`
——converge 阶段收尾时，最近两轮"方案对比说明"结论一致就自动生成一份
未确认的 GoalExecutionSpec 草稿。

用 unittest.mock.patch 直接打桩 `goal_execution_spec` 模块里被
`_maybe_auto_generate_converge_spec_draft` 用到的几个函数（`load_spec`/
`GoalExecutionSpecBuilder`/`save_spec`）和 `execution_phase.
compute_progress_trend_signal`/`NotificationDispatcher`，不依赖真实 LLM
调用或磁盘上的完整 Goal/backlog 基础设施——这个函数本身是一段"读几个信号、
决定是否触发"的胶水逻辑，用真实依赖测试反而会引入大量无关的搭建成本。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from mini_agent.evolution import goal_cron_bridge as bridge


class _FakeGoal:
    def __init__(self, goal_id="g1", title="示例 Goal", description="做点什么",
                 execution_spec_confirmed=False):
        self.id = goal_id
        self.title = title
        self.description = description
        self.execution_spec_confirmed = execution_spec_confirmed


class _FakeGoalBacklog:
    def __init__(self):
        self.notes: list[tuple[str, str]] = []

    def append_progress_note(self, node_id: str, line: str) -> bool:
        self.notes.append((node_id, line))
        return True


class TestMaybeAutoGenerateConvergeSpecDraft(unittest.TestCase):
    def setUp(self):
        self.paths = object()  # 只需非 None，函数内部不会真正解析路径
        self.goal_backlog = _FakeGoalBacklog()
        self.goal = _FakeGoal()

    def _call(self, phase_info):
        bridge._maybe_auto_generate_converge_spec_draft(
            self.paths, self.goal_backlog, self.goal, cycle_no=5, phase_info=phase_info,
        )

    def test_skips_when_effective_mode_not_converge(self):
        with patch("mini_agent.perception.goal_execution_spec.load_spec") as m_load:
            self._call({"effective_mode": "running", "llm_helper": None})
            m_load.assert_not_called()
        self.assertEqual(self.goal_backlog.notes, [])

    def test_skips_when_paths_none(self):
        self.paths = None
        with patch("mini_agent.perception.goal_execution_spec.load_spec") as m_load:
            self._call({"effective_mode": "converge", "llm_helper": None})
            m_load.assert_not_called()

    def test_skips_when_spec_already_confirmed(self):
        self.goal.execution_spec_confirmed = True
        with patch("mini_agent.perception.goal_execution_spec.load_spec") as m_load:
            self._call({"effective_mode": "converge", "llm_helper": None})
            m_load.assert_not_called()
        self.assertEqual(self.goal_backlog.notes, [])

    def test_skips_when_spec_already_exists(self):
        with patch("mini_agent.perception.goal_execution_spec.load_spec", return_value=MagicMock()) as m_load, \
             patch("mini_agent.perception.execution_phase.compute_progress_trend_signal") as m_signal:
            self._call({"effective_mode": "converge", "llm_helper": None})
            m_load.assert_called_once()
            m_signal.assert_not_called()
        self.assertEqual(self.goal_backlog.notes, [])

    def test_skips_when_consensus_signal_false(self):
        with patch("mini_agent.perception.goal_execution_spec.load_spec", return_value=None), \
             patch("mini_agent.perception.execution_phase.compute_progress_trend_signal", return_value=False) as m_signal, \
             patch("mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder") as m_builder_cls:
            self._call({"effective_mode": "converge", "llm_helper": "helper"})
            m_signal.assert_called_once()
            # window=2 是方案 §4 的字面要求，断言调用参数确实是 2。
            self.assertEqual(m_signal.call_args.kwargs.get("window"), 2)
            m_builder_cls.assert_not_called()
        self.assertEqual(self.goal_backlog.notes, [])

    def test_skips_when_consensus_signal_none(self):
        with patch("mini_agent.perception.goal_execution_spec.load_spec", return_value=None), \
             patch("mini_agent.perception.execution_phase.compute_progress_trend_signal", return_value=None), \
             patch("mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder") as m_builder_cls:
            self._call({"effective_mode": "converge", "llm_helper": None})
            m_builder_cls.assert_not_called()
        self.assertEqual(self.goal_backlog.notes, [])

    def test_generates_and_saves_draft_without_confirming(self):
        fake_spec = MagicMock()
        m_builder_instance = MagicMock()
        m_builder_instance.build_draft.return_value = fake_spec

        with patch("mini_agent.perception.goal_execution_spec.load_spec", return_value=None), \
             patch("mini_agent.perception.execution_phase.compute_progress_trend_signal", return_value=True), \
             patch("mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder",
                   return_value=m_builder_instance) as m_builder_cls, \
             patch("mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder.confirm") as m_confirm, \
             patch("mini_agent.perception.goal_execution_spec.save_spec") as m_save, \
             patch("mini_agent.config.load_config", return_value=MagicMock()), \
             patch("mini_agent.notification.dispatcher.NotificationDispatcher") as m_dispatcher_cls:
            self._call({"effective_mode": "converge", "llm_helper": None})

            m_builder_cls.assert_called_once()
            m_builder_instance.build_draft.assert_called_once_with(
                self.goal.id, self.goal.title, self.goal.description,
            )
            m_save.assert_called_once_with(self.paths, self.goal.id, fake_spec)
            m_confirm.assert_not_called()
            m_dispatcher_cls.return_value.dispatch.assert_called_once()

        self.assertEqual(len(self.goal_backlog.notes), 1)
        node_id, line = self.goal_backlog.notes[0]
        self.assertEqual(node_id, self.goal.id)
        self.assertIn("converge", line)
        self.assertIn("spec confirm", line)

    def test_build_draft_exception_is_swallowed(self):
        with patch("mini_agent.perception.goal_execution_spec.load_spec", return_value=None), \
             patch("mini_agent.perception.execution_phase.compute_progress_trend_signal", return_value=True), \
             patch("mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder",
                   side_effect=RuntimeError("boom")):
            # 不应抛出异常
            self._call({"effective_mode": "converge", "llm_helper": None})
        self.assertEqual(self.goal_backlog.notes, [])


if __name__ == "__main__":
    unittest.main()
