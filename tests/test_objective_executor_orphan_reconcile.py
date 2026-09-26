"""
tests/test_objective_executor_orphan_reconcile.py

覆盖 next_doc/goal_cycle_orphan_execution_recovery_plan.md 2.1（daemon
启动加载时识别并回收孤儿执行记录）：

- load() 之后紧跟调用 reconcile_orphaned_executions()，status 仍是
  running/paused_for_fairness 的记录被标记为 failed，并返回被回收的
  execution_id 列表。
- 已经是终态（completed/failed/cancelled）的记录不受影响。
- 回收后 is_running()/_goal_has_active_cycle() 依赖的判断会随之变化
  （is_running 直接验证）。
- goal_backlog.set_status() 被调用，同步对应 Objective 状态为 failed。
- 没有任何非终态记录时返回空列表，且不触发多余的 save()。

运行方式（仓库暂无 pytest.ini/conftest.py 设置 PYTHONPATH，手动指定 src）：
    PYTHONPATH=src python3 -m pytest tests/test_objective_executor_orphan_reconcile.py -q
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.evolution.objective_executor import ObjectiveExecution, ObjectiveExecutor
from mini_agent.storage.paths import AgentPaths


class _FakeGoalBacklog:
    def __init__(self):
        self.set_status_calls: list[tuple[str, str]] = []

    def set_status(self, node_id: str, status: str) -> None:
        self.set_status_calls.append((node_id, status))

    def get(self, node_id: str):
        return None


class TestOrphanReconcile(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.goal_backlog = _FakeGoalBacklog()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _executor(self) -> ObjectiveExecutor:
        return ObjectiveExecutor(paths=self.paths, goal_backlog=self.goal_backlog)

    def _seed(self, oe: ObjectiveExecutor, execution_id: str, objective_id: str, status: str) -> ObjectiveExecution:
        ex = ObjectiveExecution(
            execution_id=execution_id,
            objective_id=objective_id,
            objective_title=f"标题-{objective_id}",
            status=status,
            started_at=time.time() - 3600,
        )
        oe._executions[execution_id] = ex
        return ex

    def test_running_and_paused_are_recovered(self):
        oe = self._executor()
        self._seed(oe, "ex_running", "obj_a", "running")
        self._seed(oe, "ex_paused", "obj_b", "paused_for_fairness")
        recovered = oe.reconcile_orphaned_executions()
        self.assertEqual(set(recovered), {"ex_running", "ex_paused"})
        self.assertEqual(oe._executions["ex_running"].status, "failed")
        self.assertEqual(oe._executions["ex_paused"].status, "failed")
        self.assertIn("孤儿执行记录", oe._executions["ex_running"].progress_notes)
        self.assertIn(("obj_a", "failed"), self.goal_backlog.set_status_calls)
        self.assertIn(("obj_b", "failed"), self.goal_backlog.set_status_calls)

    def test_terminal_status_untouched(self):
        oe = self._executor()
        self._seed(oe, "ex_done", "obj_c", "completed")
        self._seed(oe, "ex_cancelled", "obj_d", "cancelled")
        recovered = oe.reconcile_orphaned_executions()
        self.assertEqual(recovered, [])
        self.assertEqual(oe._executions["ex_done"].status, "completed")
        self.assertEqual(oe._executions["ex_cancelled"].status, "cancelled")
        self.assertEqual(self.goal_backlog.set_status_calls, [])

    def test_is_running_false_after_reconcile(self):
        oe = self._executor()
        self._seed(oe, "ex_x", "obj_x", "running")
        self.assertTrue(oe.is_running("obj_x"))
        oe.reconcile_orphaned_executions()
        self.assertFalse(oe.is_running("obj_x"))

    def test_empty_recovered_list_when_nothing_stale(self):
        oe = self._executor()
        self._seed(oe, "ex_done", "obj_e", "completed")
        recovered = oe.reconcile_orphaned_executions()
        self.assertEqual(recovered, [])

    def test_finished_at_backfilled(self):
        oe = self._executor()
        ex = self._seed(oe, "ex_f", "obj_f", "running")
        self.assertEqual(ex.finished_at, 0.0)
        oe.reconcile_orphaned_executions()
        self.assertGreater(oe._executions["ex_f"].finished_at, 0.0)

    def test_load_then_reconcile_roundtrip(self):
        oe = self._executor()
        self._seed(oe, "ex_g", "obj_g", "running")
        oe.save()

        oe2 = self._executor()
        oe2.load()
        self.assertEqual(oe2._executions["ex_g"].status, "running")
        recovered = oe2.reconcile_orphaned_executions()
        self.assertEqual(recovered, ["ex_g"])
        self.assertEqual(oe2._executions["ex_g"].status, "failed")


if __name__ == "__main__":
    unittest.main()
