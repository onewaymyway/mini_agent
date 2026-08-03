"""
tests/test_reap_stale_steps_observability.py

覆盖 next_doc/daemon_task_hang_recovery_and_watchdog_hardening_plan.md
阶段三：

  - reap_stale_steps() 每回收一个 step，ObjectiveExecutor.stale_step_reap_count
    正确递增（累计跨多次调用）。
  - 未超时的调用不会让计数器递增。
  - AutonomousLoop._tick_maintenance() 在 cfg.autonomy.
    objective_step_stale_timeout_seconds 配置了自定义阈值时，会把它透传给
    reap_stale_steps(timeout_seconds=...)；未配置（None）时退回不传参的
    旧行为（对象自己用模块默认值）。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_reap_stale_steps_observability.py -q
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.evolution.objective_executor import (
    ExecutionStep,
    ObjectiveExecution,
    ObjectiveExecutor,
)
from mini_agent.evolution.autonomous_loop import AutonomousLoop
from mini_agent.storage.paths import AgentPaths


def _fake_submit(message: str, initiator: str, meta: dict):
    return "turn-x"


def _seed_stuck_execution(oe: ObjectiveExecutor, exec_id: str, timeout: float) -> ObjectiveExecution:
    now = time.time()
    step0 = ExecutionStep(
        step_id=f"{exec_id}_s0", step_index=0, description="第一步",
        status="running", started_at=now - timeout - 5, turn_id=f"{exec_id}-stuck-turn",
    )
    step1 = ExecutionStep(step_id=f"{exec_id}_s1", step_index=1, description="第二步")
    ex = ObjectiveExecution(
        execution_id=exec_id,
        objective_id=f"{exec_id}-obj",
        objective_title="测试目标",
        steps=[step0, step1],
        status="running",
        started_at=now - timeout - 5,
        current_step_idx=0,
    )
    oe._executions[exec_id] = ex
    oe._turn_to_exec[f"{exec_id}-stuck-turn"] = (exec_id, 0)
    return ex


class TestStaleStepReapCount(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _executor(self) -> ObjectiveExecutor:
        return ObjectiveExecutor(paths=self.paths, submit_fn=_fake_submit)

    def test_count_starts_at_zero(self):
        oe = self._executor()
        self.assertEqual(oe.stale_step_reap_count, 0)

    def test_count_increments_per_reaped_execution(self):
        oe = self._executor()
        _seed_stuck_execution(oe, "exec_a", timeout=1.0)
        _seed_stuck_execution(oe, "exec_b", timeout=1.0)

        reaped = oe.reap_stale_steps(timeout_seconds=1.0)

        self.assertEqual(set(reaped), {"exec_a", "exec_b"})
        self.assertEqual(oe.stale_step_reap_count, 2)

    def test_count_accumulates_across_multiple_calls(self):
        oe = self._executor()
        _seed_stuck_execution(oe, "exec_a", timeout=1.0)
        oe.reap_stale_steps(timeout_seconds=1.0)
        self.assertEqual(oe.stale_step_reap_count, 1)

        _seed_stuck_execution(oe, "exec_b", timeout=1.0)
        oe.reap_stale_steps(timeout_seconds=1.0)
        self.assertEqual(oe.stale_step_reap_count, 2)

    def test_not_yet_timed_out_step_does_not_increment_count(self):
        oe = self._executor()
        now = time.time()
        step0 = ExecutionStep(
            step_id="exec_c_s0", step_index=0, description="第一步",
            status="running", started_at=now, turn_id="exec_c-turn",
        )
        ex = ObjectiveExecution(
            execution_id="exec_c", objective_id="exec_c-obj",
            objective_title="测试目标", steps=[step0],
            status="running", started_at=now, current_step_idx=0,
        )
        oe._executions["exec_c"] = ex
        oe._turn_to_exec["exec_c-turn"] = ("exec_c", 0)

        reaped = oe.reap_stale_steps(timeout_seconds=600.0)

        self.assertEqual(reaped, [])
        self.assertEqual(oe.stale_step_reap_count, 0)


class TestAutonomousLoopStaleTimeoutConfig(unittest.TestCase):
    """AutonomousLoop._tick_maintenance() 透传
    cfg.autonomy.objective_step_stale_timeout_seconds 给 reap_stale_steps()。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_loop(self, autonomy_extra: dict) -> tuple[AutonomousLoop, list]:
        calls: list = []

        class _FakeObjectiveExecutor:
            def reap_stale_steps(self, timeout_seconds=None):
                calls.append(timeout_seconds)
                return []

            def retry_blocked_steps(self):
                return []

            def resume(self, execution_id=None):
                pass

            def pause_all(self):
                pass

            def set_gating_degraded(self, degraded):
                pass

            def can_start_new(self):
                return False

        class _FakeGoalBacklog:
            def active_goals(self):
                return []

            def has_actionable_work(self):
                return False

            def goals_missing_objective(self):
                return []

            def load(self):
                pass

        cfg = SimpleNamespace(autonomy=SimpleNamespace(**autonomy_extra))
        loop = AutonomousLoop(
            goal_backlog=_FakeGoalBacklog(),
            input_queue=SimpleNamespace(),
            paths=self.paths,
            cfg=cfg,
            objective_executor=_FakeObjectiveExecutor(),
        )
        return loop, calls

    def test_custom_timeout_is_forwarded(self):
        loop, calls = self._make_loop({"objective_step_stale_timeout_seconds": 123})
        loop._tick_maintenance()
        self.assertEqual(calls, [123])

    def test_none_falls_back_to_module_default_no_arg_forwarded(self):
        """未配置（None）时，向后兼容：不传 timeout_seconds 参数，让
        ObjectiveExecutor 自己回退模块级默认值（行为与硬编码 600s 一致）。"""
        loop, calls = self._make_loop({"objective_step_stale_timeout_seconds": None})
        loop._tick_maintenance()
        self.assertEqual(calls, [None])


if __name__ == "__main__":
    unittest.main()
