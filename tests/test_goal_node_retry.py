"""
tests/test_goal_node_retry.py — 覆盖 evolution/goal_node_retry.py（目标树
节点失败自动重试，用户对话内明确的需求）

  1. status=="failed" 的 Objective 被自动拉回 "active"，consecutive_
     failures 从 0 变 1，progress_notes 留痕
  2. status=="cancelled" 的节点不会被自动重试（用户主动取消，非失败）
  3. Goal 节点（is_objective=False）不受影响，即使误设为 failed 状态
  4. 未达到 threshold 时不推通知，仅仅重试
  5. 连续失败次数达到 threshold 整数倍时推一条通知，之后继续重试不受
     影响（不限次数）
  6. 成功完成一轮（set_status(..., "completed")）后 consecutive_failures
     清零，下一次失败重新从 1 开始计数
  7. 不存在的 root 场景下（空 backlog）返回空结果，不抛异常
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import goal_node_retry as gnr
from mini_agent.perception.goal_backlog import load_goal_backlog
from mini_agent.storage.paths import AgentPaths


class _FakeDispatcher:
    calls: list = []

    def __init__(self, _paths):
        pass

    def dispatch(self, message, channels=None):
        _FakeDispatcher.calls.append(message)
        return {"kanban": True}


def _patch_dispatcher():
    import mini_agent.notification.dispatcher as dispatcher_mod
    original = dispatcher_mod.NotificationDispatcher
    _FakeDispatcher.calls = []
    dispatcher_mod.NotificationDispatcher = _FakeDispatcher
    return dispatcher_mod, original


class TestGoalNodeRetry(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)
        self.goal = self.gb.add_goal("Goal", source="user")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_failed_objective_retried_and_counted(self):
        obj = self.gb.add_objective("Obj", parent_id=self.goal.id)
        self.gb.set_status(obj.id, "failed")

        result = gnr.retry_failed_goal_tree_nodes(self.gb, threshold=3)

        self.assertEqual(result["retried"], [obj.id])
        self.assertEqual(result["escalated"], [])
        updated = self.gb.get(obj.id)
        self.assertEqual(updated.status, "active")
        self.assertEqual(updated.consecutive_failures, 1)
        self.assertIn("第 1 次连续失败后自动重试", updated.progress_notes)

    def test_cancelled_objective_not_retried(self):
        obj = self.gb.add_objective("Obj", parent_id=self.goal.id)
        self.gb.set_status(obj.id, "cancelled")

        result = gnr.retry_failed_goal_tree_nodes(self.gb, threshold=3)

        self.assertEqual(result["retried"], [])
        updated = self.gb.get(obj.id)
        self.assertEqual(updated.status, "cancelled")
        self.assertEqual(updated.consecutive_failures, 0)

    def test_goal_level_node_not_affected_even_if_status_failed(self):
        # Goal 正常不会被置为 failed，这里手工构造一个异常数据场景防御性验证
        self.gb.set_status(self.goal.id, "failed")

        result = gnr.retry_failed_goal_tree_nodes(self.gb, threshold=3)

        self.assertEqual(result["retried"], [])
        self.assertEqual(self.gb.get(self.goal.id).status, "failed")

    def test_no_notification_below_threshold(self):
        obj = self.gb.add_objective("Obj", parent_id=self.goal.id)
        self.gb.set_status(obj.id, "failed")

        dispatcher_mod, original = _patch_dispatcher()
        try:
            result = gnr.retry_failed_goal_tree_nodes(self.gb, threshold=3)
        finally:
            dispatcher_mod.NotificationDispatcher = original

        self.assertEqual(result["escalated"], [])
        self.assertEqual(_FakeDispatcher.calls, [])

    def test_notification_fires_at_threshold_and_retries_continue_after(self):
        obj = self.gb.add_objective("Obj", parent_id=self.goal.id)

        dispatcher_mod, original = _patch_dispatcher()
        try:
            for _ in range(3):
                self.gb.set_status(obj.id, "failed")
                gnr.retry_failed_goal_tree_nodes(self.gb, threshold=3)
        finally:
            dispatcher_mod.NotificationDispatcher = original

        self.assertEqual(len(_FakeDispatcher.calls), 1)
        self.assertIn("连续失败 3 次", _FakeDispatcher.calls[0].title)
        # 第 4 次失败仍然继续自动重试（不限次数），只是还没到下一个阈值倍数
        self.gb.set_status(obj.id, "failed")
        result = gnr.retry_failed_goal_tree_nodes(self.gb, threshold=3)
        self.assertEqual(result["retried"], [obj.id])
        self.assertEqual(self.gb.get(obj.id).consecutive_failures, 4)

    def test_consecutive_failures_reset_on_completion(self):
        obj = self.gb.add_objective("Obj", parent_id=self.goal.id)
        self.gb.set_status(obj.id, "failed")
        gnr.retry_failed_goal_tree_nodes(self.gb, threshold=3)
        self.assertEqual(self.gb.get(obj.id).consecutive_failures, 1)

        self.gb.set_status(obj.id, "completed")
        self.assertEqual(self.gb.get(obj.id).consecutive_failures, 0)

        self.gb.set_status(obj.id, "failed")
        gnr.retry_failed_goal_tree_nodes(self.gb, threshold=3)
        self.assertEqual(self.gb.get(obj.id).consecutive_failures, 1)

    def test_empty_backlog_no_error(self):
        result = gnr.retry_failed_goal_tree_nodes(self.gb, threshold=3)
        self.assertEqual(result, {"retried": [], "escalated": []})


if __name__ == "__main__":
    unittest.main()
