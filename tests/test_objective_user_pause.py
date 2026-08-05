"""
tests/test_objective_user_pause.py

覆盖 next_doc/daemon_stability_and_ux_improvement_plan.md P1-5：
Goal/Objective 侧引入用户主动"暂停"（paused_by_user），区别于已有的
paused_for_fairness（调度层面自动让出）与 paused（资源仲裁全局暂停）：
  - 当前 step 正在跑时请求暂停 → 只记标记，等这一步完成后才落定为
    paused_by_user，不打断正在执行的这一步、不丢失这一步的结果。
  - 落定后不再提交下一步，current_step_idx 停在断点。
  - resume_user_pause() 从断点续跑，不重新拆解，已完成 step 的进度不受影响。
  - paused_for_fairness 状态下请求暂停可以立即落定（没有正在跑的 turn）。
  - 已终止状态（completed/failed/cancelled）请求暂停应返回 False。
  - AutonomousLoop 侧不会把 paused_by_user 的 Objective 当作可以重新
    start() 的候选。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.objective_executor import ObjectiveExecutor
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


class TestObjectiveUserPause(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)
        self.submitted: list[str] = []

    def tearDown(self):
        self._tmpdir.cleanup()

    def _submit_fn(self, message, initiator, meta):
        turn_id = f"turn_{len(self.submitted)}"
        self.submitted.append(turn_id)
        return turn_id

    def _executor(self) -> ObjectiveExecutor:
        return ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self._submit_fn,
            llm_decompose_fn=lambda obj: [f"step{i}" for i in range(4)],
            goal_backlog=self.backlog,
        )

    def _turn_id_for(self, oe: ObjectiveExecutor, exec_id: str, step_idx: int) -> str:
        for turn_id, (eid, idx) in oe._turn_to_exec.items():
            if eid == exec_id and idx == step_idx:
                return turn_id
        raise AssertionError("找不到对应 turn_id")

    def _start(self, oe: ObjectiveExecutor, title: str = "obj"):
        goal = self.backlog.add_goal(title=f"{title}-goal", priority=50)
        obj = self.backlog.add_objective(title=title, parent_id=goal.id, priority=50)
        exec_id = oe.start(obj)
        self.assertIsNotNone(exec_id)
        return obj, exec_id

    def test_pause_requested_while_step_running_defers_until_done(self):
        """当前 step 正在跑时请求暂停：不立即改变 status，等这一步完成后
        才落定为 paused_by_user，且这一步的结果正常写入。"""
        oe = self._executor()
        _, exec_id = self._start(oe)
        ex = oe._executions[exec_id]
        self.assertEqual(ex.status, "running")

        ok = oe.request_pause(exec_id)
        self.assertTrue(ok)
        # 还没到 on_turn_done，status 应该保持不变，只是记了标记。
        self.assertEqual(ex.status, "running")
        self.assertTrue(ex.pause_requested)

        turn_id = self._turn_id_for(oe, exec_id, 0)
        oe.on_turn_done(turn_id, "step0 完成")

        self.assertEqual(ex.status, "paused_by_user")
        self.assertFalse(ex.pause_requested)
        self.assertEqual(ex.current_step_idx, 1)
        # 这一步的结果没有被暂停请求影响，正常写入。
        self.assertEqual(ex.steps[0].status, "done")
        self.assertEqual(ex.steps[0].result_summary, "step0 完成")
        # 暂停后不会自动提交下一步。
        self.assertEqual(len(self.submitted), 1)

    def test_resume_from_user_pause_continues_from_breakpoint(self):
        """resume_user_pause() 从断点续跑，不重新拆解，已完成 step 保留。"""
        oe = self._executor()
        _, exec_id = self._start(oe)
        ex = oe._executions[exec_id]

        oe.request_pause(exec_id)
        turn_id = self._turn_id_for(oe, exec_id, 0)
        oe.on_turn_done(turn_id, "step0 完成")
        self.assertEqual(ex.status, "paused_by_user")

        ok = oe.resume_user_pause(exec_id)
        self.assertTrue(ok)
        self.assertEqual(ex.status, "running")
        self.assertEqual(ex.current_step_idx, 1)
        # 步骤数量没变，原步骤没有被重新拆解替换。
        self.assertEqual(len(ex.steps), 4)
        self.assertEqual(ex.steps[0].status, "done")
        # 第二步应该被重新提交。
        self.assertEqual(len(self.submitted), 2)

    def test_pause_while_paused_for_fairness_takes_effect_immediately(self):
        """paused_for_fairness 状态下没有正在跑的 turn，请求暂停可以立即
        落定为 paused_by_user，不需要等待。"""
        oe = self._executor()
        _, exec_id = self._start(oe)
        ex = oe._executions[exec_id]
        ex.status = "paused_for_fairness"  # 模拟已经因公平性让出槽位

        ok = oe.request_pause(exec_id)
        self.assertTrue(ok)
        self.assertEqual(ex.status, "paused_by_user")
        self.assertFalse(ex.pause_requested)

    def test_pause_on_terminal_status_returns_false(self):
        """已经是终止态（completed/failed/cancelled）或已经是
        paused_by_user 本身时，request_pause 应该返回 False，不做任何改动。"""
        for terminal in ("completed", "failed", "cancelled", "paused_by_user"):
            oe = self._executor()
            _, exec_id = self._start(oe, title=f"obj-{terminal}")
            ex = oe._executions[exec_id]
            ex.status = terminal
            ok = oe.request_pause(exec_id)
            self.assertFalse(ok, f"terminal status {terminal!r} 不应该允许暂停")
            self.assertEqual(ex.status, terminal)

    def test_resume_user_pause_only_works_on_paused_by_user(self):
        """resume_user_pause() 只对 paused_by_user 状态生效，其它状态（比如
        running）应该返回 False，不做任何改动。"""
        oe = self._executor()
        _, exec_id = self._start(oe)
        ex = oe._executions[exec_id]
        self.assertEqual(ex.status, "running")

        ok = oe.resume_user_pause(exec_id)
        self.assertFalse(ok)
        self.assertEqual(ex.status, "running")

    def test_pause_completing_last_step_ignores_pending_request(self):
        """暂停请求发出后，如果这一步恰好是最后一步且完成了，应该正常
        走完成收尾（completed），不应该被暂停请求打断成 paused_by_user。"""
        oe = ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self._submit_fn,
            llm_decompose_fn=lambda obj: ["only-step"],
            goal_backlog=self.backlog,
        )
        _, exec_id = self._start(oe, title="single-step-obj")
        ex = oe._executions[exec_id]

        oe.request_pause(exec_id)
        turn_id = self._turn_id_for(oe, exec_id, 0)
        oe.on_turn_done(turn_id, "唯一一步完成")

        self.assertEqual(ex.status, "completed")
        self.assertFalse(ex.pause_requested)

    def test_user_paused_objective_ids_reflects_current_state(self):
        oe = self._executor()
        obj, exec_id = self._start(oe)
        self.assertEqual(oe.user_paused_objective_ids(), [])

        ex = oe._executions[exec_id]
        ex.status = "paused_for_fairness"
        oe.request_pause(exec_id)
        self.assertEqual(oe.user_paused_objective_ids(), [obj.id])

        oe.resume_user_pause(exec_id)
        self.assertEqual(oe.user_paused_objective_ids(), [])

    def test_cancel_still_works_after_pause_requested(self):
        """暂停请求发出但还没落定时，用户改主意直接终止仍然应该正常生效——
        request_pause 只是记标记，不影响 cancel() 的既有行为。"""
        oe = self._executor()
        _, exec_id = self._start(oe)
        oe.request_pause(exec_id)
        ok = oe.cancel(exec_id)
        self.assertTrue(ok)
        self.assertEqual(oe._executions[exec_id].status, "cancelled")


if __name__ == "__main__":
    unittest.main()
