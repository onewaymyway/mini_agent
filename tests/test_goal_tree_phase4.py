"""
tests/test_goal_tree_phase4.py — 目标树系统阶段四（看板树形 UI）后端测试

阶段四本身主要是 REST + Streamlit UI 包装（见
next_doc/goal_tree_system_phase4_implementation_record.md），没有自动化
测试基础设施覆盖 Streamlit 部分；这里只覆盖阶段四遗留项——补齐的
`GoalBacklog.reparent_node()`（对应 REST `POST /v1/goals/{id}/reparent`
与看板"🔀 改父节点"下拉框）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _make_backlog(tmp) -> GoalBacklog:
    paths = AgentPaths(Path(tmp))
    return GoalBacklog(paths)


class TestReparentNode(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.backlog = _make_backlog(self._tmp)

    def test_normal_reparent_moves_between_siblings(self):
        root = self.backlog.add_node("ultimate", "根")
        d1 = self.backlog.add_node("domain", "d1", parent_id=root.id)
        d2 = self.backlog.add_node("domain", "d2", parent_id=root.id)
        g1 = self.backlog.add_node("goal", "g1", parent_id=d1.id)

        updated = self.backlog.reparent_node(g1.id, d2.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.parent_id, d2.id)
        self.assertNotIn(g1.id, self.backlog.get(d1.id).children_ids)
        self.assertIn(g1.id, self.backlog.get(d2.id).children_ids)

    def test_reparent_ultimate_rejected(self):
        root = self.backlog.add_node("ultimate", "根")
        d1 = self.backlog.add_node("domain", "d1", parent_id=root.id)
        self.assertIsNone(self.backlog.reparent_node(root.id, d1.id))
        # 根节点自身不受影响。
        self.assertEqual(self.backlog.get(root.id).parent_id, None)

    def test_reparent_to_self_rejected(self):
        root = self.backlog.add_node("ultimate", "根")
        d1 = self.backlog.add_node("domain", "d1", parent_id=root.id)
        self.assertIsNone(self.backlog.reparent_node(d1.id, d1.id))

    def test_reparent_to_own_descendant_rejected_cycle(self):
        root = self.backlog.add_node("ultimate", "根")
        d1 = self.backlog.add_node("domain", "d1", parent_id=root.id)
        g1 = self.backlog.add_node("goal", "g1", parent_id=d1.id)
        obj1 = self.backlog.add_node("objective", "o1", parent_id=g1.id)
        # d1 -> g1 -> obj1；把 d1 挂到自己的孙节点 obj1 下面应该被拒绝。
        self.assertIsNone(self.backlog.reparent_node(d1.id, obj1.id))
        # 树结构应保持不变。
        self.assertEqual(self.backlog.get(d1.id).parent_id, root.id)

    def test_reparent_invalid_hierarchy_rejected(self):
        root = self.backlog.add_node("ultimate", "根")
        d1 = self.backlog.add_node("domain", "d1", parent_id=root.id)
        g1 = self.backlog.add_node("goal", "g1", parent_id=d1.id)
        # domain 不能挂在 goal 下面（层级顺序倒挂）。
        self.assertIsNone(self.backlog.reparent_node(d1.id, g1.id))

    def test_reparent_nonexistent_node_or_parent(self):
        root = self.backlog.add_node("ultimate", "根")
        d1 = self.backlog.add_node("domain", "d1", parent_id=root.id)
        self.assertIsNone(self.backlog.reparent_node("no_such_id", d1.id))
        self.assertIsNone(self.backlog.reparent_node(d1.id, "no_such_id"))

    def test_reparent_to_none_promotes_to_orphan_root(self):
        root = self.backlog.add_node("ultimate", "根")
        d1 = self.backlog.add_node("domain", "d1", parent_id=root.id)
        updated = self.backlog.reparent_node(d1.id, None)
        self.assertIsNotNone(updated)
        self.assertIsNone(updated.parent_id)
        self.assertNotIn(d1.id, self.backlog.get(root.id).children_ids)

    def test_reparent_persists_across_reload(self):
        root = self.backlog.add_node("ultimate", "根")
        d1 = self.backlog.add_node("domain", "d1", parent_id=root.id)
        d2 = self.backlog.add_node("domain", "d2", parent_id=root.id)
        g1 = self.backlog.add_node("goal", "g1", parent_id=d1.id)
        self.backlog.reparent_node(g1.id, d2.id)

        reloaded = _make_backlog(self._tmp)
        reloaded.load()
        self.assertEqual(reloaded.get(g1.id).parent_id, d2.id)
        self.assertIn(g1.id, reloaded.get(d2.id).children_ids)
        self.assertNotIn(g1.id, reloaded.get(d1.id).children_ids)


if __name__ == "__main__":
    unittest.main()
