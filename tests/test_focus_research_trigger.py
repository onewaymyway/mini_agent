"""tests/test_focus_research_trigger.py — FocusResearchTrigger（阶段二）

覆盖 next_doc/goal_tree_research_and_action_recommendation_plan.md §4.2：
  - trigger() 生成一条 origin="focus_research" 的 GrowthCandidate，落进
    GrowthBacklog
  - 节奏治理：should_trigger() 按结构节点/叶子 goal 使用不同的最小间隔，
    间隔内跳过、force=True 时忽略间隔
  - 同一节点重复触发命中 GrowthBacklog 已有的字面去重/合并证据逻辑
  - find_newly_focused_nodes()：对比前后两次焦点集合，只返回新增部分
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.evolution.focus_research_trigger import (
    MIN_INTERVAL_SECONDS_GOAL,
    MIN_INTERVAL_SECONDS_STRUCTURAL,
    FocusResearchTrigger,
    find_newly_focused_nodes,
)
from mini_agent.evolution.growth_advisor import GrowthBacklog
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


class TestFocusResearchTrigger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmp.cleanup()

    def test_trigger_creates_focus_research_candidate(self):
        goal = self.backlog.add_goal("学习 Rust 异步编程", priority=3)
        trigger = FocusResearchTrigger(self.paths, self.backlog)

        candidate = trigger.trigger(goal.id)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.origin, "focus_research")
        self.assertEqual(candidate.title, goal.title)
        self.assertIn(f"goal_tree:{goal.id}", candidate.evidence_refs)

        pending = GrowthBacklog(self.paths).pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].candidate_id, candidate.candidate_id)

    def test_trigger_returns_none_for_missing_node(self):
        trigger = FocusResearchTrigger(self.paths, self.backlog)
        self.assertIsNone(trigger.trigger("goal_not_exist"))

    def test_should_trigger_blocks_within_min_interval(self):
        goal = self.backlog.add_goal("换工作", priority=1)
        trigger = FocusResearchTrigger(self.paths, self.backlog)

        first = trigger.trigger(goal.id)
        self.assertIsNotNone(first)

        node = self.backlog.get(goal.id)
        skip_reason = trigger.should_trigger(node)
        self.assertIsNotNone(skip_reason)

        second = trigger.trigger(goal.id)
        self.assertIsNone(second)

    def test_force_bypasses_min_interval(self):
        goal = self.backlog.add_goal("副业调研", priority=1)
        trigger = FocusResearchTrigger(self.paths, self.backlog)

        trigger.trigger(goal.id)
        forced = trigger.trigger(goal.id, force=True)

        # add_or_merge 对同一 dedupe_key 会合并证据、返回同一条候选，
        # 不会因为强制触发就生成第二条重复候选。
        self.assertIsNotNone(forced)
        pending = GrowthBacklog(self.paths).pending()
        self.assertEqual(len(pending), 1)

    def test_structural_node_uses_longer_min_interval(self):
        root = self.backlog.get_root_node()
        domain = self.backlog.add_node("domain", "健康", parent_id=root.id)
        trigger = FocusResearchTrigger(self.paths, self.backlog)

        self.assertGreater(MIN_INTERVAL_SECONDS_STRUCTURAL, MIN_INTERVAL_SECONDS_GOAL)

        trigger.trigger(domain.id)
        # 手动回拨触发时间到"叶子 goal 间隔已过，但结构节点间隔未过"的
        # 中间点，验证结构节点确实用的是更长的间隔而不是叶子间隔。
        state = trigger._load_state()
        state[domain.id] = time.time() - (MIN_INTERVAL_SECONDS_GOAL + 3600)
        trigger._state_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        trigger._state_path.write_text(json.dumps(state), encoding="utf-8")

        node = self.backlog.get(domain.id)
        skip_reason = trigger.should_trigger(node)
        self.assertIsNotNone(skip_reason)


class TestFindNewlyFocusedNodes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_only_newly_added_nodes(self):
        g1 = self.backlog.add_goal("目标一", priority=2)
        g2 = self.backlog.add_goal("目标二", priority=1)

        previous = {g1.id}
        newly = find_newly_focused_nodes(self.backlog, previous)

        self.assertEqual([n.id for n in newly], [g2.id])

    def test_empty_when_nothing_new(self):
        g1 = self.backlog.add_goal("目标一", priority=2)
        previous = {g1.id}
        newly = find_newly_focused_nodes(self.backlog, previous)
        self.assertEqual(newly, [])


if __name__ == "__main__":
    unittest.main()
