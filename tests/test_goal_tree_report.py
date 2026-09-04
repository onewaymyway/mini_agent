"""
tests/test_goal_tree_report.py — 覆盖
next_doc/goal_tree_visibility_wiki_and_report_plan.md Stage 1

  1. root_id 不存在时返回 found=False，不抛异常
  2. 空森林（没有任何节点）时返回 node_count=0，不报错
  3. 子树收集：root_id 指定时只收集该节点及其后代，不含无关节点
  4. root_id 省略时收集全局森林（所有顶层节点及其整棵子树）
  5. by_status 分组统计正确
  6. by_phase 分组：全新节点没有阶段文件时按 last_known_effective_mode
     的保守默认值 "explore" 分组
  7. 待办清单：decompose_candidates / 焦点未确认（有子节点但
     current_focus_ids 为空）分别正确收集
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.perception import goal_tree_report as gtr
from mini_agent.perception.goal_backlog import load_goal_backlog
from mini_agent.storage.paths import AgentPaths


class TestBuildGoalTreeReport(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_root_not_found(self):
        report = gtr.build_goal_tree_report(self.paths, self.gb, "nope")
        self.assertFalse(report.found)
        self.assertIsNotNone(report.error)

    def test_empty_forest(self):
        report = gtr.build_goal_tree_report(self.paths, self.gb, None)
        self.assertTrue(report.found)
        self.assertEqual(report.node_count, 0)

    def test_subtree_scoped_to_root(self):
        goal_a = self.gb.add_goal("Goal A", source="user")
        obj_a1 = self.gb.add_objective("Obj A1", parent_id=goal_a.id)
        goal_b = self.gb.add_goal("Goal B (unrelated)", source="user")

        report = gtr.build_goal_tree_report(self.paths, self.gb, goal_a.id)
        ids = {item["id"] for items in report.by_status.values() for item in items}
        self.assertIn(goal_a.id, ids)
        self.assertIn(obj_a1.id, ids)
        self.assertNotIn(goal_b.id, ids)
        self.assertEqual(report.node_count, 2)

    def test_global_forest_without_root_id(self):
        goal_a = self.gb.add_goal("Goal A", source="user")
        goal_b = self.gb.add_goal("Goal B", source="user")

        report = gtr.build_goal_tree_report(self.paths, self.gb, None)
        ids = {item["id"] for items in report.by_status.values() for item in items}
        self.assertIn(goal_a.id, ids)
        self.assertIn(goal_b.id, ids)
        self.assertEqual(report.node_count, 2)

    def test_by_status_grouping(self):
        active_goal = self.gb.add_goal("Active goal", source="user")
        paused_goal = self.gb.add_goal("Paused goal", source="user")
        self.gb.update_fields(paused_goal.id, status="paused")

        report = gtr.build_goal_tree_report(self.paths, self.gb, None)
        active_ids = {item["id"] for item in report.by_status.get("active", [])}
        paused_ids = {item["id"] for item in report.by_status.get("paused", [])}
        self.assertIn(active_goal.id, active_ids)
        self.assertIn(paused_goal.id, paused_ids)

    def test_by_phase_defaults_to_explore_when_no_phase_file(self):
        node = self.gb.add_goal("Fresh goal", source="user")
        report = gtr.build_goal_tree_report(self.paths, self.gb, None)
        explore_ids = {item["id"] for item in report.by_phase.get("explore", [])}
        self.assertIn(node.id, explore_ids)

    def test_pending_decompose_candidates(self):
        node = self.gb.add_goal("Needs decompose", source="user")
        self.gb.append_decompose_candidates(node.id, [
            {"id": "cand_1", "title": "候选子目标 1"},
        ])
        report = gtr.build_goal_tree_report(self.paths, self.gb, None)
        self.assertEqual(len(report.pending_decompose_candidates), 1)
        self.assertEqual(report.pending_decompose_candidates[0]["id"], node.id)
        self.assertEqual(report.pending_decompose_candidates[0]["candidate_id"], "cand_1")

    def test_pending_focus_confirmation(self):
        parent = self.gb.add_goal("Parent with children", source="user")
        self.gb.add_objective("Child", parent_id=parent.id)
        # 未手动 pin，也未跑过 focus 重算 -> current_focus_ids 仍为空
        report = gtr.build_goal_tree_report(self.paths, self.gb, None)
        pending_ids = {item["id"] for item in report.pending_focus_confirmation}
        self.assertIn(parent.id, pending_ids)

    def test_report_is_json_serializable(self):
        self.gb.add_goal("Goal", source="user")
        report = gtr.build_goal_tree_report(self.paths, self.gb, None)
        import json
        json.dumps(report.to_dict())  # 不应抛异常


if __name__ == "__main__":
    unittest.main()
