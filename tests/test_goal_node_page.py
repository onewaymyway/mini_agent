"""
tests/test_goal_node_page.py — 覆盖
next_doc/goal_tree_visibility_wiki_and_report_plan.md Stage 2

  1. goal_id 不存在时返回 found=False，不抛异常
  2. 基本字段：title/status/level
  3. path_from_root：多层父子关系下面包屑顺序正确（根→当前）
  4. children：直接子节点列表正确，不递归展开孙节点详情
  5. pending_items：复用 goal_tree_report 的同一份逻辑，字段对得上
  6. feedback_history：透传 GoalNode.user_feedback
  7. output_structure/output_readme_text：产出目录扫描结果非空
  8. 结果可 JSON 序列化
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.perception import goal_node_page as gnp
from mini_agent.perception.goal_backlog import load_goal_backlog
from mini_agent.storage.paths import AgentPaths


class TestBuildGoalNodePage(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_goal_not_found(self):
        page = gnp.build_goal_node_page(self.paths, self.gb, "nope")
        self.assertFalse(page.found)
        self.assertIsNotNone(page.error)

    def test_basic_fields(self):
        node = self.gb.add_goal("My goal", source="user")
        page = gnp.build_goal_node_page(self.paths, self.gb, node.id)
        self.assertTrue(page.found)
        self.assertEqual(page.title, "My goal")
        self.assertEqual(page.status, "active")
        self.assertEqual(page.level, "goal")

    def test_path_from_root_breadcrumb(self):
        root = self.gb.add_goal("Root goal", source="user")
        child = self.gb.add_objective("Child obj", parent_id=root.id)
        grandchild_candidates = self.gb.add_objectives_for_goal(root.id, ["ignored"])  # noqa: F841

        page = gnp.build_goal_node_page(self.paths, self.gb, child.id)
        ids = [c["id"] for c in page.path_from_root]
        self.assertEqual(ids[0], root.id)
        self.assertEqual(ids[-1], child.id)

    def test_children_listed_without_recursion(self):
        parent = self.gb.add_goal("Parent", source="user")
        child = self.gb.add_objective("Child", parent_id=parent.id)

        page = gnp.build_goal_node_page(self.paths, self.gb, parent.id)
        self.assertEqual(len(page.children), 1)
        self.assertEqual(page.children[0]["id"], child.id)
        self.assertEqual(page.children[0]["title"], "Child")

    def test_pending_items_matches_tree_report_helper(self):
        node = self.gb.add_goal("Needs decompose", source="user")
        self.gb.append_decompose_candidates(node.id, [{"id": "cand_1", "title": "候选"}])

        page = gnp.build_goal_node_page(self.paths, self.gb, node.id)
        self.assertEqual(len(page.pending_items["decompose_candidates"]), 1)
        self.assertEqual(page.pending_items["decompose_candidates"][0]["candidate_id"], "cand_1")

    def test_feedback_history_passthrough(self):
        node = self.gb.add_goal("Goal with feedback", source="user")
        self.gb.add_user_feedback(node.id, "please speed up")

        page = gnp.build_goal_node_page(self.paths, self.gb, node.id)
        self.assertEqual(len(page.feedback_history), 1)
        self.assertEqual(page.feedback_history[0]["text"], "please speed up")

    def test_output_structure_and_readme_present(self):
        node = self.gb.add_goal("Goal with output", source="user")
        page = gnp.build_goal_node_page(self.paths, self.gb, node.id)
        self.assertIsInstance(page.output_structure, dict)
        self.assertIn("产出目录索引", page.output_readme_text)

    def test_page_is_json_serializable(self):
        node = self.gb.add_goal("Goal", source="user")
        page = gnp.build_goal_node_page(self.paths, self.gb, node.id)
        json.dumps(page.to_dict())  # 不应抛异常


if __name__ == "__main__":
    unittest.main()
