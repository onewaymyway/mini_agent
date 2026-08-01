"""
tests/test_goal_execution_fairness.py — Goal 执行公平性调度改进（P1-P3）

覆盖 next_doc/goal_execution_fairness_improvement_plan.md 的验收标准：
  - P1: GoalBacklog.mark_scheduled() / ObjectiveExecutor 的按 Goal 分组并发计数
  - P2: GoalBacklog.active_objectives_fair_ranked() 的公平轮询排序与自我修正
  - P3: goal_backlog.compute_aging_boost() 的老化加成计算
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.perception.goal_backlog import GoalBacklog, compute_aging_boost
from mini_agent.storage.paths import AgentPaths


class TestActiveObjectivesFairRanked(unittest.TestCase):
    """P2 验收标准 1/2：公平轮询排序。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_same_priority_goals_round_robin(self):
        """3 个优先级相同的 Goal，各 1 个 Objective：连续多轮应该轮流被排到
        第一位，而不是每次都选同一个（旧的稳定排序会一直选靠前的那个）。"""
        goals = [
            self.backlog.add_goal(title=f"g{i}", priority=50) for i in range(3)
        ]
        objs = [
            self.backlog.add_objective(title=f"o{i}", parent_id=g.id, priority=50)
            for i, g in enumerate(goals)
        ]

        picked_first = []
        for _ in range(3):
            ranked = self.backlog.active_objectives_fair_ranked()
            top = ranked[0]
            picked_first.append(top.id)
            # 模拟调度器实际启动了这个 Objective
            self.backlog.mark_scheduled(top.id)

        # 三轮应该三个都不一样（轮流），而不是同一个 id 反复出现
        self.assertEqual(len(set(picked_first)), 3)
        self.assertEqual(set(picked_first), {o.id for o in objs})

    def test_higher_priority_wins_when_never_scheduled(self):
        """两个都从未被调度过的 Goal：起点相同时应优先选高 priority 的；
        但那个 Goal 被调度一次后，即使 priority 仍然更高，下一轮也应该轮到
        另一个 Goal。"""
        g_high = self.backlog.add_goal(title="high", priority=80)
        g_low = self.backlog.add_goal(title="low", priority=10)
        o_high = self.backlog.add_objective(title="oh", parent_id=g_high.id, priority=80)
        o_low = self.backlog.add_objective(title="ol", parent_id=g_low.id, priority=10)

        ranked = self.backlog.active_objectives_fair_ranked()
        self.assertEqual(ranked[0].id, o_high.id)

        self.backlog.mark_scheduled(o_high.id)

        ranked2 = self.backlog.active_objectives_fair_ranked()
        self.assertEqual(ranked2[0].id, o_low.id)

    def test_priority_strategy_unaffected(self):
        """priority 策略（active_objectives()）本身不受本次改动影响，
        行为与改造前完全一致：稳定排序，仅按 priority 降序。"""
        g1 = self.backlog.add_goal(title="g1", priority=10)
        g2 = self.backlog.add_goal(title="g2", priority=90)
        o1 = self.backlog.add_objective(title="o1", parent_id=g1.id, priority=10)
        o2 = self.backlog.add_objective(title="o2", parent_id=g2.id, priority=90)

        ranked = self.backlog.active_objectives()
        self.assertEqual([n.id for n in ranked], [o2.id, o1.id])


class TestMarkScheduled(unittest.TestCase):
    """P1/P2：mark_scheduled 只写 last_scheduled_at，不影响 last_touched_at。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_mark_scheduled_does_not_touch_last_touched_at(self):
        goal = self.backlog.add_goal(title="g", priority=50)
        obj = self.backlog.add_objective(title="o", parent_id=goal.id, priority=50)
        before = self.backlog.get(obj.id).last_touched_at

        ok = self.backlog.mark_scheduled(obj.id)
        self.assertTrue(ok)

        after_node = self.backlog.get(obj.id)
        self.assertGreater(after_node.last_scheduled_at, 0.0)
        self.assertEqual(after_node.last_touched_at, before)

        # 父 Goal 的 last_scheduled_at 也应同步更新（供以 Goal 为单位统计用）
        parent_node = self.backlog.get(goal.id)
        self.assertEqual(parent_node.last_scheduled_at, after_node.last_scheduled_at)

    def test_mark_scheduled_missing_node_returns_false(self):
        self.assertFalse(self.backlog.mark_scheduled("does-not-exist"))


class TestComputeAgingBoost(unittest.TestCase):
    """P3 验收标准 1/2。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_no_boost_when_not_stale(self):
        goal = self.backlog.add_goal(title="g", priority=30)
        node = self.backlog.get(goal.id)
        boost = compute_aging_boost(node, time.time(), stale_days=7.0)
        self.assertEqual(boost, 0.0)

    def test_boost_increases_with_staleness_and_overtakes_priority(self):
        """停滞 10 天、priority=30 的 Goal，加了老化加成后 effective_priority
        应该反超 priority=50、刚被调度过的 Goal。"""
        now = time.time()
        stale = self.backlog.add_goal(title="stale", priority=30)
        fresh = self.backlog.add_goal(title="fresh", priority=32)

        stale_node = self.backlog.get(stale.id)
        # 模拟 10 天前最后一次有实质进展
        stale_node.last_touched_at = now - 10 * 86400
        fresh_node = self.backlog.get(fresh.id)
        fresh_node.last_touched_at = now

        # boost_per_day 调大一些，让"停滞够久"这一效应在测试里更明显、
        # 不需要真的等很多天就能反超一个 priority 只高一点点的 Goal。
        boost = compute_aging_boost(stale_node, now, stale_days=7.0, boost_per_day=10.0)
        self.assertGreater(boost, 0.0)
        effective_stale = stale_node.priority + boost
        self.assertGreater(effective_stale, fresh_node.priority)

    def test_boost_capped(self):
        now = time.time()
        goal = self.backlog.add_goal(title="very-stale", priority=0)
        node = self.backlog.get(goal.id)
        node.last_touched_at = now - 100 * 86400  # 极端停滞
        boost = compute_aging_boost(
            node, now, stale_days=7.0, boost_per_day=1.0, max_boost_days=14.0,
        )
        self.assertEqual(boost, 14.0)

    def test_boost_resets_after_being_rescheduled(self):
        """Goal 重新被调度一次（意味着 last_touched_at 也会随执行产生的
        进展更新）后，aging_boost 应在下一次计算时降为 0。"""
        now = time.time()
        goal = self.backlog.add_goal(title="g", priority=10)
        node = self.backlog.get(goal.id)
        node.last_touched_at = now - 20 * 86400
        self.assertGreater(compute_aging_boost(node, now, stale_days=7.0), 0.0)

        # 模拟执行产生了实质进展，last_touched_at 被刷新
        self.backlog.update_progress(goal.id, "有进展了")
        refreshed = self.backlog.get(goal.id)
        self.assertEqual(compute_aging_boost(refreshed, time.time(), stale_days=7.0), 0.0)


class TestRunningCountForGoal(unittest.TestCase):
    """P1 验收标准 1：ObjectiveExecutor.running_count_for_goal() 按 Goal
    分组统计 running execution 数，供 AutonomousLoop 做并发上限判断。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_counts_only_running_under_same_goal(self):
        from mini_agent.evolution.objective_executor import (
            ObjectiveExecution,
            ObjectiveExecutor,
        )

        goal_a = self.backlog.add_goal(title="A", priority=50)
        goal_b = self.backlog.add_goal(title="B", priority=50)
        obj_a1 = self.backlog.add_objective(title="a1", parent_id=goal_a.id, priority=50)
        obj_a2 = self.backlog.add_objective(title="a2", parent_id=goal_a.id, priority=50)
        obj_b1 = self.backlog.add_objective(title="b1", parent_id=goal_b.id, priority=50)

        executor = ObjectiveExecutor(paths=self.paths, goal_backlog=self.backlog)
        executor._executions["e1"] = ObjectiveExecution(
            execution_id="e1", objective_id=obj_a1.id, objective_title="a1", status="running",
        )
        executor._executions["e2"] = ObjectiveExecution(
            execution_id="e2", objective_id=obj_a2.id, objective_title="a2", status="running",
        )
        executor._executions["e3"] = ObjectiveExecution(
            execution_id="e3", objective_id=obj_b1.id, objective_title="b1", status="completed",
        )

        self.assertEqual(executor.running_count_for_goal(goal_a.id), 2)
        self.assertEqual(executor.running_count_for_goal(goal_b.id), 0)


if __name__ == "__main__":
    unittest.main()
