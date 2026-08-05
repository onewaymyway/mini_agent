"""
tests/test_objective_edit_step_result.py

覆盖 next_doc/daemon_stability_and_ux_improvement_plan.md 第 10 项
（P2-10）：ObjectiveExecutor.edit_step_result() —— 编辑一个已完成 step 的
产出并继续，不重新执行该 step 本身。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.objective_executor import ObjectiveExecutor
from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
from mini_agent.storage.paths import AgentPaths


def _make_objective(backlog: GoalBacklog, title: str) -> GoalNode:
    goal = backlog.add_goal(title=f"{title}-goal", description="", source="user", priority=50)
    objs = backlog.add_objectives_for_goal(goal.id, [title])
    return objs[0]


class _FakeSubmitter:
    def __init__(self):
        self.calls: list[dict] = []
        self._n = 0

    def __call__(self, message: str, initiator: str, meta: dict):
        self._n += 1
        turn_id = f"turn_{self._n}"
        self.calls.append({"turn_id": turn_id, "message": message, "initiator": initiator, "meta": meta})
        return turn_id


class _ObjectiveExecutorTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)
        self.submitter = _FakeSubmitter()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _executor(self, steps=None):
        steps = steps or ["单步"]
        return ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self.submitter,
            llm_decompose_fn=lambda obj: list(steps),
            declare_paths_fn=lambda desc: [f"path-for-{desc}"],
            goal_backlog=self.backlog,
        )


class TestEditStepResult(_ObjectiveExecutorTestBase):
    def test_edit_done_step_updates_summary_and_marks_edited(self):
        executor = self._executor(steps=["第一步", "第二步"])
        objective = _make_objective(self.backlog, "obj1")
        exec_id = executor.start(objective)
        turn_id = executor._executions[exec_id].steps[0].turn_id
        executor.on_turn_done(turn_id, result_summary="原始结果，有个小笔误", valid=True)

        ok = executor.edit_step_result(exec_id, 0, result_summary="修正后的结果")
        self.assertTrue(ok)
        step = executor._executions[exec_id].steps[0]
        self.assertEqual(step.result_summary, "修正后的结果")
        self.assertTrue(step.edited_by_user)
        # 不应该重新提交这一步（turn_id/status 不受影响）
        self.assertEqual(step.status, "done")

    def test_edit_updates_artifacts_only(self):
        executor = self._executor(steps=["第一步", "第二步"])
        objective = _make_objective(self.backlog, "obj2")
        exec_id = executor.start(objective)
        turn_id = executor._executions[exec_id].steps[0].turn_id
        executor.on_turn_done(turn_id, result_summary="做完了", valid=True)

        ok = executor.edit_step_result(exec_id, 0, artifacts=["out/report.md"])
        self.assertTrue(ok)
        step = executor._executions[exec_id].steps[0]
        self.assertEqual(step.artifacts, ["out/report.md"])
        self.assertEqual(step.result_summary, "做完了")  # 未传 result_summary，不应被清空

    def test_edit_non_done_step_returns_false(self):
        executor = self._executor(steps=["第一步", "第二步"])
        objective = _make_objective(self.backlog, "obj3")
        exec_id = executor.start(objective)
        # 第一步仍处于 running（未调用 on_turn_done），不允许编辑
        ok = executor.edit_step_result(exec_id, 0, result_summary="试图编辑未完成的步骤")
        self.assertFalse(ok)

    def test_edit_with_no_changes_returns_false(self):
        executor = self._executor(steps=["第一步"])
        objective = _make_objective(self.backlog, "obj4")
        exec_id = executor.start(objective)
        turn_id = executor._executions[exec_id].steps[0].turn_id
        executor.on_turn_done(turn_id, result_summary="完成", valid=True)
        ok = executor.edit_step_result(exec_id, 0)
        self.assertFalse(ok)

    def test_edit_unknown_execution_returns_false(self):
        executor = self._executor()
        self.assertFalse(executor.edit_step_result("no-such-exec", 0, result_summary="x"))

    def test_edit_out_of_range_step_returns_false(self):
        executor = self._executor(steps=["唯一步骤"])
        objective = _make_objective(self.backlog, "obj5")
        exec_id = executor.start(objective)
        self.assertFalse(executor.edit_step_result(exec_id, 5, result_summary="x"))

    def test_edited_result_feeds_subsequent_step_prompt(self):
        """修正后的 result_summary 应该作为"前序步骤结果"喂给下一步的 prompt。"""
        executor = self._executor(steps=["第一步", "第二步"])
        objective = _make_objective(self.backlog, "obj6")
        exec_id = executor.start(objective)
        turn_id = executor._executions[exec_id].steps[0].turn_id
        executor.on_turn_done(turn_id, result_summary="原始结果", valid=True)
        executor.edit_step_result(exec_id, 0, result_summary="修正后的关键结论")

        # 手动触发第二步的重新提交，观察 prompt 是否包含修正后的内容
        ex = executor._executions[exec_id]
        executor._submit_step(ex, 1)
        last_call = self.submitter.calls[-1]
        self.assertIn("修正后的关键结论", last_call["message"])
        self.assertNotIn("原始结果", last_call["message"])


if __name__ == "__main__":
    unittest.main()
