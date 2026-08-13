"""tests/test_fairness_diagnostics.py

覆盖 next_doc/goal_fairness_scheduling_diagnostics_plan.md：
`perception/fairness_diagnostics.py::fairness_diagnostics_snapshot()`
的只读快照聚合逻辑。
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.perception.fairness_diagnostics import fairness_diagnostics_snapshot
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _cfg(*, time_slicing=False, boost_per_day=1.0, boost_max_days=14.0, stale_days=7.0,
         yield_after_steps=3, yield_after_seconds=900.0):
    return SimpleNamespace(
        autonomy=SimpleNamespace(
            fairness_time_slicing_enabled=time_slicing,
            fairness_aging_boost_per_day=boost_per_day,
            fairness_aging_boost_max_days=boost_max_days,
            fairness_yield_after_steps=yield_after_steps,
            fairness_yield_after_seconds=yield_after_seconds,
        ),
        next_action_stale_days=stale_days,
    )


class _FakeExecutor:
    def __init__(self, paused_ids=None, running_ids=None):
        self._paused = paused_ids or []
        self._running = running_ids or []

    def fairness_paused_objective_ids(self):
        return list(self._paused)

    def is_running(self, objective_id):
        return objective_id in self._running


class TestFairnessDiagnosticsSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_none_cfg_returns_empty_snapshot(self):
        result = fairness_diagnostics_snapshot(self.backlog, None, None)
        self.assertEqual(result["active_objectives_count"], 0)
        self.assertFalse(result["time_slicing_enabled"])

    def test_config_values_reflected_in_snapshot(self):
        cfg = _cfg(time_slicing=True, boost_per_day=2.0, boost_max_days=10.0, stale_days=5.0)
        result = fairness_diagnostics_snapshot(None, None, cfg)
        self.assertTrue(result["time_slicing_enabled"])
        self.assertEqual(result["config"]["aging_boost_per_day"], 2.0)
        self.assertEqual(result["config"]["aging_boost_max_days"], 10.0)
        self.assertEqual(result["config"]["stale_days"], 5.0)

    def test_none_goal_backlog_gives_empty_objectives_but_valid_config(self):
        cfg = _cfg()
        result = fairness_diagnostics_snapshot(None, None, cfg)
        self.assertEqual(result["objectives"], [])
        self.assertEqual(result["active_objectives_count"], 0)

    def test_active_objectives_included_with_zero_aging_boost_when_fresh(self):
        goal = self.backlog.add_goal(title="g1", priority=50)
        obj = self.backlog.add_objective(title="o1", parent_id=goal.id, priority=50)
        cfg = _cfg()
        result = fairness_diagnostics_snapshot(self.backlog, None, cfg)
        self.assertEqual(result["active_objectives_count"], 1)
        item = result["objectives"][0]
        self.assertEqual(item["objective_id"], obj.id)
        self.assertEqual(item["aging_boost"], 0.0)
        self.assertEqual(item["effective_priority"], 50)
        self.assertEqual(result["goals_with_active_aging_boost"], 0)

    def test_stale_objective_gets_nonzero_aging_boost(self):
        goal = self.backlog.add_goal(title="g1", priority=50)
        obj = self.backlog.add_objective(title="o1", parent_id=goal.id, priority=50)
        # 手动把 last_touched_at 拨回 10 天前，触发老化加成（stale_days=7）
        node = self.backlog.get(obj.id)
        node.last_touched_at = time.time() - 10 * 86400
        self.backlog.save()
        cfg = _cfg(boost_per_day=1.0, stale_days=7.0, boost_max_days=14.0)
        result = fairness_diagnostics_snapshot(self.backlog, None, cfg)
        item = next(i for i in result["objectives"] if i["objective_id"] == obj.id)
        self.assertGreater(item["aging_boost"], 0.0)
        self.assertEqual(result["goals_with_active_aging_boost"], 1)

    def test_paused_and_running_flags_reflected(self):
        goal = self.backlog.add_goal(title="g1", priority=50)
        obj = self.backlog.add_objective(title="o1", parent_id=goal.id, priority=50)
        executor = _FakeExecutor(paused_ids=[obj.id], running_ids=[])
        cfg = _cfg()
        result = fairness_diagnostics_snapshot(self.backlog, executor, cfg)
        self.assertEqual(result["paused_for_fairness_count"], 1)
        self.assertIn(obj.id, result["paused_for_fairness_objective_ids"])
        item = result["objectives"][0]
        self.assertTrue(item["is_paused_for_fairness"])
        self.assertFalse(item["is_running"])

    def test_executor_exception_does_not_crash_snapshot(self):
        class _BoomExecutor:
            def fairness_paused_objective_ids(self):
                raise RuntimeError("boom")

            def is_running(self, objective_id):
                raise RuntimeError("boom")

        goal = self.backlog.add_goal(title="g1", priority=50)
        self.backlog.add_objective(title="o1", parent_id=goal.id, priority=50)
        cfg = _cfg()
        result = fairness_diagnostics_snapshot(self.backlog, _BoomExecutor(), cfg)
        self.assertEqual(result["paused_for_fairness_count"], 0)
        # objective 列表本身应该照常生成（is_running 内部异常被兜底为 False）
        self.assertEqual(len(result["objectives"]), 1)

    def test_max_objectives_truncates(self):
        for i in range(5):
            goal = self.backlog.add_goal(title=f"g{i}", priority=50)
            self.backlog.add_objective(title=f"o{i}", parent_id=goal.id, priority=50)
        cfg = _cfg()
        result = fairness_diagnostics_snapshot(self.backlog, None, cfg, max_objectives=2)
        self.assertEqual(len(result["objectives"]), 2)
        self.assertEqual(result["active_objectives_count"], 5)


if __name__ == "__main__":
    unittest.main()
