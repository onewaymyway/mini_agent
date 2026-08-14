"""
tests/test_autonomous_loop_trigger_objective_now.py

覆盖 goal_cron_convergence_and_governance_improvement_plan.md Track 1：
`AutonomousLoop.trigger_objective_now()`（供 `ObjectiveChannelAdapter.
execute()` 调用的安全入口）与 `_trigger_objective_candidate()`（从
`_tick_maintenance()` 排序循环体抽取的触发逻辑）。

不构造完整的 AutonomousLoop 运行时（cron_scheduler/input_queue 等均传
None），只验证 trigger_objective_now() 自身的筛选 + 委托逻辑，复用
test_autonomous_loop_decommission_hook.py 里"轻量构造 + 只测目标方法"
的既有模式。
"""

from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, "src")

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.autonomous_loop import AutonomousLoop
from mini_agent.perception.goal_backlog import GoalBacklog


def _make_loop(paths, goal_backlog, objective_executor) -> AutonomousLoop:
    return AutonomousLoop(
        goal_backlog=goal_backlog,
        input_queue=None,
        paths=paths,
        cfg=None,
        objective_executor=objective_executor,
    )


class TestTriggerObjectiveNow(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)
        self.goal = self.backlog.add_goal(title="parent goal", priority=5)
        self.obj = self.backlog.add_objective(title="child objective", parent_id=self.goal.id, priority=5)
        self.backlog.save()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_executor(self, **overrides):
        ex = MagicMock()
        ex.is_running.return_value = False
        ex.user_paused_objective_ids.return_value = []
        ex.can_start_new.return_value = True
        ex.fairness_paused_objective_ids.return_value = []
        ex.running_count_for_goal.return_value = 0
        ex._goal_id_of_objective.return_value = self.goal.id
        ex.start.return_value = "exec-1"
        for k, v in overrides.items():
            getattr(ex, k).return_value = v
        return ex

    def test_no_objective_executor_returns_false(self):
        loop = _make_loop(self.paths, self.backlog, None)
        self.assertFalse(loop.trigger_objective_now(self.obj.id))

    def test_unknown_objective_id_returns_false(self):
        ex = self._make_executor()
        loop = _make_loop(self.paths, self.backlog, ex)
        self.assertFalse(loop.trigger_objective_now("does-not-exist"))

    def test_already_running_returns_false(self):
        ex = self._make_executor(is_running=True)
        loop = _make_loop(self.paths, self.backlog, ex)
        self.assertFalse(loop.trigger_objective_now(self.obj.id))
        ex.start.assert_not_called()

    def test_user_paused_returns_false(self):
        ex = self._make_executor(user_paused_objective_ids=[self.obj.id])
        loop = _make_loop(self.paths, self.backlog, ex)
        self.assertFalse(loop.trigger_objective_now(self.obj.id))
        ex.start.assert_not_called()

    def test_cannot_start_new_returns_false(self):
        ex = self._make_executor(can_start_new=False)
        loop = _make_loop(self.paths, self.backlog, ex)
        self.assertFalse(loop.trigger_objective_now(self.obj.id))
        ex.start.assert_not_called()

    def test_per_goal_cap_reached_returns_false(self):
        ex = self._make_executor(running_count_for_goal=1)
        loop = _make_loop(self.paths, self.backlog, ex)
        # 默认 per_goal_cap=1（cfg 为 None 时 getattr 回落默认值），
        # running_count_for_goal 已经是 1，应该被挡住。
        self.assertFalse(loop.trigger_objective_now(self.obj.id))
        ex.start.assert_not_called()

    def test_normal_start_succeeds_and_marks_scheduled(self):
        ex = self._make_executor()
        loop = _make_loop(self.paths, self.backlog, ex)
        result = loop.trigger_objective_now(self.obj.id)
        self.assertTrue(result)
        ex.start.assert_called_once()
        # mark_scheduled 应该已经更新了 last_scheduled_at
        self.assertGreater(self.backlog.get(self.obj.id).last_scheduled_at, 0.0)

    def test_start_returning_falsy_propagates_false(self):
        ex = self._make_executor(start=None)
        loop = _make_loop(self.paths, self.backlog, ex)
        self.assertFalse(loop.trigger_objective_now(self.obj.id))

    def test_fairness_paused_resumes_instead_of_start(self):
        ex = self._make_executor(fairness_paused_objective_ids=[self.obj.id])
        ex.resume_fairness.return_value = True
        loop = _make_loop(self.paths, self.backlog, ex)
        result = loop.trigger_objective_now(self.obj.id)
        self.assertTrue(result)
        ex.resume_fairness.assert_called_once_with(self.obj.id)
        ex.start.assert_not_called()

    def test_fairness_paused_resume_failure_returns_false(self):
        ex = self._make_executor(fairness_paused_objective_ids=[self.obj.id])
        ex.resume_fairness.return_value = False
        loop = _make_loop(self.paths, self.backlog, ex)
        result = loop.trigger_objective_now(self.obj.id)
        self.assertFalse(result)

    def test_internal_exception_is_caught_and_returns_false(self):
        ex = self._make_executor()
        ex.is_running.side_effect = RuntimeError("boom")
        loop = _make_loop(self.paths, self.backlog, ex)
        # 不应该向上抛异常
        self.assertFalse(loop.trigger_objective_now(self.obj.id))


if __name__ == "__main__":
    unittest.main()
