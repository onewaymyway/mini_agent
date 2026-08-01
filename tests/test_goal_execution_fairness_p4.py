"""
tests/test_goal_execution_fairness_p4.py — Goal 执行公平性调度改进 P4
（执行时间片化）

覆盖 next_doc/goal_execution_fairness_improvement_plan.md P4 的设计与验收：
  - `fairness_time_slicing_enabled=False`（默认）时，`on_turn_done()` 行为
    与改造前完全一致，恒不让出槽位。
  - 开启后，跑满 `fairness_yield_after_steps` 步且确实有其它 Goal 排队等待
    时，才会让出槽位（`paused_for_fairness`），否则继续正常推进。
  - 只有一个 active Goal（没有其它 Goal 排队）时，即使跑满阈值也不让出——
    让出没有意义。
  - `resume_fairness()` 能从断点续跑（不重新拆解、不丢失已完成 step），并
    开启新的时间片计时。

运行方式（仓库暂无 pytest.ini/conftest.py 设置 PYTHONPATH，手动指定 src）：
    PYTHONPATH=src python3 -m pytest tests/test_goal_execution_fairness_p4.py -q
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.evolution.objective_executor import ExecutionStep, ObjectiveExecutor
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _make_cfg(**autonomy_overrides) -> SimpleNamespace:
    defaults = dict(
        fairness_time_slicing_enabled=True,
        fairness_yield_after_steps=2,
        fairness_yield_after_seconds=10_000.0,  # 默认设很大，测试只走"步数"这条路径
        fairness_aging_boost_per_day=1.0,
        fairness_aging_boost_max_days=14.0,
    )
    defaults.update(autonomy_overrides)
    return SimpleNamespace(
        autonomy=SimpleNamespace(**defaults),
        next_action_stale_days=7.0,
    )


class TestFairnessTimeSlicing(unittest.TestCase):
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

    def _executor(self, cfg=None) -> ObjectiveExecutor:
        return ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self._submit_fn,
            llm_decompose_fn=lambda obj: [f"step{i}" for i in range(4)],
            goal_backlog=self.backlog,
            cfg=cfg,
        )

    def _turn_id_for(self, oe: ObjectiveExecutor, exec_id: str, step_idx: int) -> str:
        for turn_id, (eid, idx) in oe._turn_to_exec.items():
            if eid == exec_id and idx == step_idx:
                return turn_id
        raise AssertionError("找不到对应 turn_id")

    def test_disabled_by_default_never_yields(self):
        """未提供 cfg / 关闭开关时，完成一步后恒继续推进下一步，不让出槽位
        （与改造前行为完全一致的回归测试）。"""
        goal_a = self.backlog.add_goal(title="A", priority=50)
        obj_a = self.backlog.add_objective(title="oa", parent_id=goal_a.id, priority=50)
        goal_b = self.backlog.add_goal(title="B", priority=50)
        self.backlog.add_objective(title="ob", parent_id=goal_b.id, priority=50)

        oe = self._executor(cfg=None)  # 未提供 cfg
        exec_id = oe.start(obj_a)
        self.assertIsNotNone(exec_id)

        turn_id = self._turn_id_for(oe, exec_id, 0)
        oe.on_turn_done(turn_id, "done step0")

        ex = oe._executions[exec_id]
        self.assertEqual(ex.status, "running")
        self.assertEqual(ex.current_step_idx, 1)

    def test_yields_when_other_goal_waiting_after_threshold(self):
        """开启开关、跑满阈值步数、且确实有其它 Goal 排队等待时，应该让出
        槽位（paused_for_fairness），断点停在下一个未提交的 step。"""
        goal_a = self.backlog.add_goal(title="A", priority=50)
        obj_a = self.backlog.add_objective(title="oa", parent_id=goal_a.id, priority=50)
        goal_b = self.backlog.add_goal(title="B", priority=50)
        self.backlog.add_objective(title="ob", parent_id=goal_b.id, priority=50)

        oe = self._executor(cfg=_make_cfg())
        exec_id = oe.start(obj_a)
        ex = oe._executions[exec_id]

        # 完成 step0 → 正常推进到 step1（未跑满阈值 2 步）
        oe.on_turn_done(self._turn_id_for(oe, exec_id, 0), "s0")
        self.assertEqual(ex.status, "running")
        self.assertEqual(ex.current_step_idx, 1)

        # 完成 step1 → 跑满阈值 2 步（本片段内完成了 step0、step1），且
        # Goal B 的 Objective 还在排队等待 → 应该让出槽位
        oe.on_turn_done(self._turn_id_for(oe, exec_id, 1), "s1")
        self.assertEqual(ex.status, "paused_for_fairness")
        self.assertEqual(ex.current_step_idx, 2)  # 断点停在 step2（尚未提交）
        self.assertNotIn(exec_id, [oe._turn_to_exec[t][0] for t in oe._turn_to_exec])

    def test_does_not_yield_when_no_other_goal_waiting(self):
        """只有一个 active Goal（没有其它 Goal 排队）时，即使跑满阈值步数
        也不应该让出槽位——让出没有意义。"""
        goal_a = self.backlog.add_goal(title="A", priority=50)
        obj_a = self.backlog.add_objective(title="oa", parent_id=goal_a.id, priority=50)

        oe = self._executor(cfg=_make_cfg())
        exec_id = oe.start(obj_a)
        ex = oe._executions[exec_id]

        oe.on_turn_done(self._turn_id_for(oe, exec_id, 0), "s0")
        oe.on_turn_done(self._turn_id_for(oe, exec_id, 1), "s1")

        self.assertEqual(ex.status, "running")
        self.assertEqual(ex.current_step_idx, 2)

    def test_resume_fairness_continues_from_checkpoint(self):
        """resume_fairness() 应该从断点（current_step_idx）继续提交，不
        重新拆解、不丢失已完成的 step，并重置时间片起点。"""
        goal_a = self.backlog.add_goal(title="A", priority=50)
        obj_a = self.backlog.add_objective(title="oa", parent_id=goal_a.id, priority=50)
        goal_b = self.backlog.add_goal(title="B", priority=50)
        self.backlog.add_objective(title="ob", parent_id=goal_b.id, priority=50)

        oe = self._executor(cfg=_make_cfg())
        exec_id = oe.start(obj_a)
        ex = oe._executions[exec_id]

        oe.on_turn_done(self._turn_id_for(oe, exec_id, 0), "s0")
        oe.on_turn_done(self._turn_id_for(oe, exec_id, 1), "s1")
        self.assertEqual(ex.status, "paused_for_fairness")

        self.assertIn(obj_a.id, oe.fairness_paused_objective_ids())

        resumed = oe.resume_fairness(obj_a.id)
        self.assertTrue(resumed)
        self.assertEqual(ex.status, "running")
        self.assertEqual(ex.fairness_slice_start_step, 2)
        # 已完成的两步进度不受影响
        self.assertEqual(ex.steps[0].status, "done")
        self.assertEqual(ex.steps[1].status, "done")
        self.assertEqual(ex.steps[2].status, "running")

    def test_resume_fairness_returns_false_when_not_paused(self):
        goal_a = self.backlog.add_goal(title="A", priority=50)
        obj_a = self.backlog.add_objective(title="oa", parent_id=goal_a.id, priority=50)
        oe = self._executor(cfg=_make_cfg())
        oe.start(obj_a)
        self.assertFalse(oe.resume_fairness(obj_a.id))
        self.assertFalse(oe.resume_fairness("nonexistent"))


if __name__ == "__main__":
    unittest.main()
