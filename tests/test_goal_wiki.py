"""
tests/test_goal_wiki.py — 覆盖 next_doc/goal_tree_visibility_wiki_and_
report_plan.md Stage 3

  1. goal_id 不存在时 render_goal_wiki_page() 返回 None，不写文件
  2. 单节点渲染：落盘的 index.md 存在、包含标题和产出目录索引内容
  3. 子节点链接是否正确对应树结构（父页里出现子节点的相对链接）
  4. build_goal_wiki_tree(root_id=None) 遍历全局森林，覆盖多个顶层节点，
     并生成全局根索引 goals_wiki/index.md
  5. build_goal_wiki_tree(root_id=<id>) 只重建该子树，不生成全局根索引
  6. 重新生成是否幂等（不产生垃圾历史文件——重复调用后文件数不变）
  7. root_id 不存在时返回空列表，不抛异常
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import goal_wiki as gw
from mini_agent.perception.goal_backlog import load_goal_backlog
from mini_agent.storage.paths import AgentPaths


class TestGoalWiki(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_render_missing_goal_returns_none(self):
        text = gw.render_goal_wiki_page(self.paths, self.gb, "nope")
        self.assertIsNone(text)
        self.assertFalse(gw.goal_wiki_index_path(self.paths, "nope").exists())

    def test_render_single_node_writes_index_md(self):
        node = self.gb.add_goal("My goal", source="user")
        text = gw.render_goal_wiki_page(self.paths, self.gb, node.id)
        self.assertIsNotNone(text)
        self.assertIn("# My goal", text)
        self.assertIn("产出目录索引", text)

        index_path = gw.goal_wiki_index_path(self.paths, node.id)
        self.assertTrue(index_path.exists())
        self.assertEqual(index_path.read_text(encoding="utf-8"), text)

    def test_child_links_match_tree_structure(self):
        parent = self.gb.add_goal("Parent", source="user")
        child = self.gb.add_objective("Child obj", parent_id=parent.id)

        text = gw.render_goal_wiki_page(self.paths, self.gb, parent.id)
        self.assertIn(f"({child.id}/index.md)", text)

    def test_build_tree_covers_global_forest_and_root_index(self):
        g1 = self.gb.add_goal("Goal 1", source="user")
        g2 = self.gb.add_goal("Goal 2", source="user")
        child = self.gb.add_objective("Child of g1", parent_id=g1.id)

        rendered = gw.build_goal_wiki_tree(self.paths, self.gb, root_id=None)
        self.assertIn(g1.id, rendered)
        self.assertIn(g2.id, rendered)
        self.assertIn(child.id, rendered)

        root_index = gw.goals_wiki_root(self.paths) / "index.md"
        self.assertTrue(root_index.exists())
        content = root_index.read_text(encoding="utf-8")
        self.assertIn(f"({g1.id}/index.md)", content)
        self.assertIn(f"({g2.id}/index.md)", content)

    def test_build_tree_with_root_id_scopes_and_skips_global_index(self):
        g1 = self.gb.add_goal("Goal 1", source="user")
        g2 = self.gb.add_goal("Goal 2", source="user")
        child = self.gb.add_objective("Child of g1", parent_id=g1.id)

        rendered = gw.build_goal_wiki_tree(self.paths, self.gb, root_id=g1.id)
        self.assertEqual(sorted(rendered), sorted([g1.id, child.id]))
        self.assertNotIn(g2.id, rendered)
        self.assertFalse((gw.goals_wiki_root(self.paths) / "index.md").exists())

    def test_rebuild_is_idempotent_no_extra_files(self):
        g1 = self.gb.add_goal("Goal 1", source="user")
        self.gb.add_objective("Child of g1", parent_id=g1.id)

        gw.build_goal_wiki_tree(self.paths, self.gb, root_id=None)
        files_first = sorted(str(p) for p in gw.goals_wiki_root(self.paths).rglob("*") if p.is_file())

        gw.build_goal_wiki_tree(self.paths, self.gb, root_id=None)
        files_second = sorted(str(p) for p in gw.goals_wiki_root(self.paths).rglob("*") if p.is_file())

        self.assertEqual(files_first, files_second)

    def test_build_tree_missing_root_id_returns_empty(self):
        rendered = gw.build_goal_wiki_tree(self.paths, self.gb, root_id="nope")
        self.assertEqual(rendered, [])


if __name__ == "__main__":
    unittest.main()
