"""
tests/test_active_alerting_p0_8.py

覆盖 next_doc/daemon_stability_and_ux_improvement_plan.md 第 8 项
（主动告警通道）已实现的三类信号源：

  1. Objective 被判定 failed 时（`ObjectiveExecutor._on_objective_failed`），
     应通过 NotificationDispatcher 推一条通知。
  2. Workflow 熔断触发时（`WorkflowWatchdog.report_workflow_level_failure`
     达到 circuit_breaker_distinct_step_threshold），应推一条通知。
  3. 卡死回收事件短时间内异常增长时（`recovery_event_log.record_recovery_event`
     传入 paths 参数、同一 kind 在窗口内达到阈值），应推一条通知；未达
     阈值/未传 paths 时不应该推送；同一次"突发"只推一次（冷却期内不重复）。

三类信号复用同一个 `notification/dispatcher.py`，这里统一用
`paths.notification_dispatch_log`（dispatch() 每次调用都会写一行，不管
具体渠道发送成功与否）来断言"确实触发了一次 dispatch()"，不依赖某个
具体渠道的存储细节。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_active_alerting_p0_8.py -q
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.evolution import recovery_event_log as recovery_log_mod
from mini_agent.evolution.objective_executor import ObjectiveExecution, ObjectiveExecutor
from mini_agent.storage.paths import AgentPaths
from mini_agent.workflow.registry import ControlState
from mini_agent.workflow.watchdog import WorkflowWatchdog


def _read_dispatch_log(paths: AgentPaths) -> list[dict]:
    p = paths.notification_dispatch_log
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


class TestObjectiveFailedNotification(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _executor(self) -> ObjectiveExecutor:
        return ObjectiveExecutor(
            paths=self.paths,
            submit_fn=lambda message, initiator, meta: "turn_1",
            llm_decompose_fn=lambda obj: [f"{obj.title} - 单步"],
            declare_paths_fn=lambda desc: [],
        )

    def test_on_objective_failed_dispatches_notification(self):
        executor = self._executor()
        ex = ObjectiveExecution(
            execution_id="exec_1",
            objective_id="obj_1",
            objective_title="测试目标",
            progress_notes="guardian: 已达最大轮次上限（5），停止执行",
        )
        executor._executions[ex.execution_id] = ex
        executor._on_objective_failed(ex)

        log = _read_dispatch_log(self.paths)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["source"], "objective_failed")


class TestCircuitBreakerNotification(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.control = ControlState()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_circuit_breaker_trip_dispatches_notification(self):
        wd = WorkflowWatchdog(
            self.paths, "wf_1", self.control,
            circuit_breaker_distinct_step_threshold=2,
        )
        self.assertFalse(wd.report_workflow_level_failure("step_a", "ToolError"))
        self.assertTrue(wd.report_workflow_level_failure("step_b", "ToolError"))
        self.assertTrue(wd.circuit_breaker_tripped)

        log = _read_dispatch_log(self.paths)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["source"], "workflow_circuit_breaker")

    def test_no_notification_when_threshold_not_reached(self):
        wd = WorkflowWatchdog(
            self.paths, "wf_2", self.control,
            circuit_breaker_distinct_step_threshold=5,
        )
        wd.report_workflow_level_failure("step_a", "ToolError")
        self.assertEqual(_read_dispatch_log(self.paths), [])


class TestRecoveryBurstNotification(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        recovery_log_mod._reset_for_tests()

    def tearDown(self):
        self._tmpdir.cleanup()
        recovery_log_mod._reset_for_tests()

    def test_burst_within_window_triggers_notification_once(self):
        recovery_log_mod.record_recovery_event("cron_job", "job1", "x", now=0.0, paths=self.paths)
        recovery_log_mod.record_recovery_event("cron_job", "job2", "x", now=10.0, paths=self.paths)
        self.assertEqual(_read_dispatch_log(self.paths), [])  # 未达阈值 (3)
        recovery_log_mod.record_recovery_event("cron_job", "job3", "x", now=20.0, paths=self.paths)
        log = _read_dispatch_log(self.paths)
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["source"], "recovery_burst")

        # 冷却期内继续触发不重复推送
        recovery_log_mod.record_recovery_event("cron_job", "job4", "x", now=30.0, paths=self.paths)
        self.assertEqual(len(_read_dispatch_log(self.paths)), 1)

    def test_no_paths_no_notification(self):
        for i in range(5):
            recovery_log_mod.record_recovery_event("cron_job", f"job{i}", "x", now=float(i))
        self.assertEqual(_read_dispatch_log(self.paths), [])

    def test_different_kinds_do_not_share_burst_count(self):
        recovery_log_mod.record_recovery_event("cron_job", "job1", "x", now=0.0, paths=self.paths)
        recovery_log_mod.record_recovery_event("objective_step", "e1:0", "x", now=1.0, paths=self.paths)
        recovery_log_mod.record_recovery_event("isolated_pool", "", "x", now=2.0, paths=self.paths)
        # 三种 kind 各只有一条，任何一个都没达到阈值 3
        self.assertEqual(_read_dispatch_log(self.paths), [])

    def test_events_outside_window_do_not_count(self):
        recovery_log_mod.record_recovery_event("cron_job", "job1", "x", now=0.0, paths=self.paths)
        recovery_log_mod.record_recovery_event("cron_job", "job2", "x", now=1000.0, paths=self.paths)  # 超出 600s 窗口
        recovery_log_mod.record_recovery_event("cron_job", "job3", "x", now=1001.0, paths=self.paths)
        # 此时窗口内只有 job2/job3 两条，未达阈值 3
        self.assertEqual(_read_dispatch_log(self.paths), [])


if __name__ == "__main__":
    unittest.main()
