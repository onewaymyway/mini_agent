"""tests/test_goal_focus_research_nodes.py — GoalBacklog.focus_research_nodes()

覆盖 next_doc/goal_tree_research_and_action_recommendation_plan.md 阶段一：
  1. 只有叶子 active Goal 时，行为与 active_goals() 完全一致
  2. current_focus_ids 指向的 domain/stage 结构节点会被并入结果
  3. 非 active 的结构节点、未被任何 current_focus_ids 引用的结构节点不会被并入
  4. 结构节点不会与叶子 Goal 重复计入
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


class TestFocusResearchNodes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmp.cleanup()

    def test_matches_active_goals_when_no_focus_structural_nodes(self):
        g1 = self.backlog.add_goal("学习 Rust", priority=2)
        self.backlog.add_goal("已归档目标", priority=5, status="completed")

        result = self.backlog.focus_research_nodes()

        self.assertEqual([n.id for n in result], [g1.id])
        self.assertEqual(
            [n.id for n in result],
            [n.id for n in self.backlog.active_goals()],
        )

    def test_includes_domain_node_in_current_focus(self):
        root = self.backlog.get_root_node()
        domain = self.backlog.add_node("domain", "健康", parent_id=root.id)
        stage_unfocused = self.backlog.add_node("stage", "未被聚焦的阶段", parent_id=root.id)
        goal = self.backlog.add_goal("跑步计划", priority=1)

        # add_node()/add_goal() 内部会重新加载磁盘状态再落盘，前面拿到的
        # `root` 局部变量已经不是 self._nodes 里的那个对象了，需要重新取。
        root = self.backlog.get(root.id)
        root.current_focus_ids = [domain.id]
        self.backlog.save()

        result = self.backlog.focus_research_nodes()
        result_ids = {n.id for n in result}

        self.assertIn(domain.id, result_ids)
        self.assertIn(goal.id, result_ids)
        self.assertNotIn(stage_unfocused.id, result_ids)

    def test_inactive_focus_node_excluded(self):
        root = self.backlog.get_root_node()
        domain = self.backlog.add_node("domain", "副业", parent_id=root.id, status="paused")
        root = self.backlog.get(root.id)
        root.current_focus_ids = [domain.id]
        self.backlog.save()

        result = self.backlog.focus_research_nodes()

        self.assertNotIn(domain.id, {n.id for n in result})

    def test_no_duplicate_when_focus_id_is_leaf_goal(self):
        root = self.backlog.get_root_node()
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        goal = self.backlog.add_node("goal", "换工作", parent_id=domain.id)

        domain = self.backlog.get(domain.id)
        domain.current_focus_ids = [goal.id]
        self.backlog.save()

        result = self.backlog.focus_research_nodes()
        result_ids = [n.id for n in result]

        self.assertEqual(result_ids.count(goal.id), 1)


if __name__ == "__main__":
    unittest.main()
