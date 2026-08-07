"""
tests/test_kanban_realtime_and_status_bugfix.py

覆盖两处修复（2026-08 会话）：
  1. ObjectiveExecutor.get_status_summary() 新增 is_stale 字段——
     is_active_fn 提供且返回 False 时，status=="running" 的记录应标记
     is_stale=True；未提供 is_active_fn（默认，向后兼容）时恒为 False。
  2. ObjectivePersistentRunner.has_worker()：能正确反映 _executors
     registry 里是否存在该 execution_id 的专属线程池。

看板 app.py 里 _render_goal_card() 状态下拉框对非标准 status 值的兜底
（原 ValueError 崩溃修复）因为依赖 streamlit 运行时环境，这里不做
无头单测覆盖，已通过人工复现 + 代码走查确认。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

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


class TestIsStaleField(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)
        self.submitter = _FakeSubmitter()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _executor(self, is_active_fn=None):
        return ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self.submitter,
            llm_decompose_fn=lambda obj: [f"{obj.title} - 单步"],
            goal_backlog=self.backlog,
            is_active_fn=is_active_fn,
        )

    def test_is_stale_false_when_no_is_active_fn(self):
        """未接线 is_active_fn（默认，向后兼容）时 is_stale 恒为 False。"""
        ex_obj = _make_objective(self.backlog, "task-a")
        executor = self._executor(is_active_fn=None)
        executor.start(ex_obj)
        summary = executor.get_status_summary()
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["status"], "running")
        self.assertFalse(summary[0]["is_stale"])

    def test_is_stale_true_when_worker_missing(self):
        """is_active_fn 对该 execution_id 返回 False 时，running 记录应标记 is_stale=True。"""
        ex_obj = _make_objective(self.backlog, "task-b")
        executor = self._executor(is_active_fn=lambda exec_id: False)
        executor.start(ex_obj)
        summary = executor.get_status_summary()
        self.assertEqual(summary[0]["status"], "running")
        self.assertTrue(summary[0]["is_stale"])

    def test_is_stale_false_when_worker_present(self):
        """is_active_fn 对该 execution_id 返回 True 时，不应标记为 stale。"""
        ex_obj = _make_objective(self.backlog, "task-c")
        executor = self._executor(is_active_fn=lambda exec_id: True)
        executor.start(ex_obj)
        summary = executor.get_status_summary()
        self.assertFalse(summary[0]["is_stale"])

    def test_is_stale_ignored_for_non_running_status(self):
        """非 running 状态（如 completed）不计算 is_stale，恒为 False，
        即使 is_active_fn 会返回 False。"""
        ex_obj = _make_objective(self.backlog, "task-d")
        executor = self._executor(is_active_fn=lambda exec_id: False)
        executor.start(ex_obj)
        exec_id = executor.get_status_summary()[0]["execution_id"]
        executor.cancel(exec_id)
        summary = executor.get_status_summary()
        self.assertEqual(summary[0]["status"], "cancelled")
        self.assertFalse(summary[0]["is_stale"])

    def test_is_active_fn_exception_degrades_to_not_stale(self):
        """is_active_fn 自身抛异常时不应影响整体状态摘要，退化为 is_stale=False。"""
        ex_obj = _make_objective(self.backlog, "task-e")

        def _boom(exec_id):
            raise RuntimeError("boom")

        executor = self._executor(is_active_fn=_boom)
        executor.start(ex_obj)
        summary = executor.get_status_summary()
        self.assertFalse(summary[0]["is_stale"])


class TestPersistentRunnerHasWorker(unittest.TestCase):
    def test_has_worker_reflects_executors_registry(self):
        from mini_agent.evolution.objective_agent_bridge import ObjectivePersistentRunner

        runner = ObjectivePersistentRunner(
            base_cfg=MagicMock(),
            on_done=MagicMock(),
            on_failed=MagicMock(),
        )
        self.assertFalse(runner.has_worker("exec-not-registered"))
        # 直接往 registry 里塞一条记录，模拟"确实占着一条专属线程"的情形，
        # 不依赖真正启动线程池（避免测试引入额外的调度/网络依赖）。
        runner._executors["exec-x"] = object()
        self.assertTrue(runner.has_worker("exec-x"))
        self.assertFalse(runner.has_worker("exec-y"))


if __name__ == "__main__":
    unittest.main()
