"""
tests/test_goal_tree_phase1.py — 目标树系统阶段一（数据模型）测试

覆盖 next_doc/goal_tree_system_plan.md §4.1：
  - level 开放为 ultimate/domain/stage/goal/objective
  - validate_node_hierarchy() 的层级校验规则（§4.1.1）
  - GoalBacklog.add_node() 通用创建入口
  - GoalBacklog.get_root_node() 全局根节点幂等创建
  - GoalBacklog.migrate_directions_to_domain_nodes() Direction 迁移
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.perception.goal_backlog import (
    GoalBacklog,
    GoalNode,
    LEVEL_ORDER,
    validate_node_hierarchy,
)
from mini_agent.storage.paths import AgentPaths


class TestValidateNodeHierarchy(unittest.TestCase):
    def test_level_order_matches_plan(self):
        self.assertEqual(LEVEL_ORDER, ("ultimate", "domain", "stage", "goal", "objective"))

    def test_ultimate_must_have_no_parent(self):
        self.assertIsNone(validate_node_hierarchy("ultimate", None))
        self.assertIsNotNone(validate_node_hierarchy("ultimate", "domain"))

    def test_domain_parent_must_be_ultimate(self):
        self.assertIsNone(validate_node_hierarchy("domain", "ultimate"))
        self.assertIsNotNone(validate_node_hierarchy("domain", "domain"))
        self.assertIsNotNone(validate_node_hierarchy("domain", "stage"))

    def test_stage_allows_skipping_domain(self):
        self.assertIsNone(validate_node_hierarchy("stage", "ultimate"))
        self.assertIsNone(validate_node_hierarchy("stage", "domain"))
        self.assertIsNotNone(validate_node_hierarchy("stage", "goal"))

    def test_goal_allows_domain_stage_or_goal_parent(self):
        for parent_level in ("domain", "stage", "goal"):
            self.assertIsNone(validate_node_hierarchy("goal", parent_level))
        self.assertIsNotNone(validate_node_hierarchy("goal", "ultimate"))
        self.assertIsNotNone(validate_node_hierarchy("goal", "objective"))

    def test_objective_parent_must_be_goal_unchanged(self):
        self.assertIsNone(validate_node_hierarchy("objective", "goal"))
        self.assertIsNotNone(validate_node_hierarchy("objective", "stage"))
        self.assertIsNotNone(validate_node_hierarchy("objective", None))

    def test_unknown_level_rejected(self):
        self.assertIsNotNone(validate_node_hierarchy("nonsense", "ultimate"))


class TestGoalNodeIsStructural(unittest.TestCase):
    def test_structural_levels(self):
        for level in ("ultimate", "domain", "stage"):
            node = GoalNode(id="x", level=level, title="t", source="user", status="active")
            self.assertTrue(node.is_structural)
        for level in ("goal", "objective"):
            node = GoalNode(id="x", level=level, title="t", source="user", status="active")
            self.assertFalse(node.is_structural)

    def test_new_fields_roundtrip(self):
        node = GoalNode(
            id="domain_x", level="domain", title="t", source="user", status="active",
            current_focus_ids=["a"], focus_pinned_ids=["b"],
            decompose_candidates=[{"id": "c1", "title": "候选"}],
        )
        d = node.to_dict()
        restored = GoalNode.from_dict(d)
        self.assertEqual(restored.current_focus_ids, ["a"])
        self.assertEqual(restored.focus_pinned_ids, ["b"])
        self.assertEqual(restored.decompose_candidates, [{"id": "c1", "title": "候选"}])

    def test_from_dict_backward_compatible_without_new_fields(self):
        old_dict = {"id": "goal_old", "level": "goal", "title": "老目标", "source": "user", "status": "active"}
        restored = GoalNode.from_dict(old_dict)
        self.assertEqual(restored.current_focus_ids, [])
        self.assertEqual(restored.focus_pinned_ids, [])
        self.assertEqual(restored.decompose_candidates, [])


class TestAddNode(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_add_node_builds_full_chain(self):
        root = self.backlog.add_node("ultimate", "我的人生目标")
        domain = self.backlog.add_node("domain", "事业", parent_id=root.id)
        stage = self.backlog.add_node("stage", "未来一年", parent_id=domain.id)
        goal = self.backlog.add_node("goal", "拿下某项目", parent_id=stage.id)
        objective = self.backlog.add_node("objective", "写方案", parent_id=goal.id)

        self.backlog.load()
        reloaded_root = self.backlog.all_nodes()
        by_id = {n.id: n for n in reloaded_root}
        self.assertIn(domain.id, by_id[root.id].children_ids)
        self.assertIn(stage.id, by_id[domain.id].children_ids)
        self.assertIn(goal.id, by_id[stage.id].children_ids)
        self.assertIn(objective.id, by_id[goal.id].children_ids)
        self.assertEqual(objective.id.split("_")[0], "obj")
        self.assertEqual(goal.id.split("_")[0], "goal")

    def test_add_node_allows_skipping_stage(self):
        root = self.backlog.add_node("ultimate", "根")
        domain = self.backlog.add_node("domain", "健康", parent_id=root.id)
        goal = self.backlog.add_node("goal", "跑一次半马", parent_id=domain.id)
        self.assertEqual(goal.parent_id, domain.id)

    def test_add_node_rejects_invalid_hierarchy(self):
        root = self.backlog.add_node("ultimate", "根")
        with self.assertRaises(ValueError):
            self.backlog.add_node("objective", "不该直接挂根下", parent_id=root.id)

    def test_add_node_rejects_second_ultimate(self):
        self.backlog.add_node("ultimate", "根1")
        with self.assertRaises(ValueError):
            self.backlog.add_node("ultimate", "根2")

    def test_add_node_rejects_unknown_parent(self):
        with self.assertRaises(ValueError):
            self.backlog.add_node("domain", "x", parent_id="does_not_exist")

    def test_add_node_no_node_created_on_validation_failure(self):
        before = len(self.backlog.all_nodes())
        with self.assertRaises(ValueError):
            self.backlog.add_node("ultimate", "根", parent_id="whatever")
        self.assertEqual(len(self.backlog.all_nodes()), before)


class TestGetRootNode(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_creates_root_when_missing(self):
        root = self.backlog.get_root_node()
        self.assertEqual(root.level, "ultimate")
        self.backlog.load()
        ultimates = [n for n in self.backlog.all_nodes() if n.level == "ultimate"]
        self.assertEqual(len(ultimates), 1)

    def test_idempotent_returns_same_root(self):
        root1 = self.backlog.get_root_node()
        root2 = self.backlog.get_root_node()
        self.assertEqual(root1.id, root2.id)
        self.backlog.load()
        ultimates = [n for n in self.backlog.all_nodes() if n.level == "ultimate"]
        self.assertEqual(len(ultimates), 1)


class TestMigrateDirectionsToDomainNodes(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_dry_run_does_not_modify_state(self):
        direction = self.backlog.add_direction("工作项目", "关于工作的所有目标")
        goal = self.backlog.add_goal(title="做个 demo", source="user")
        self.backlog.assign_direction(goal.id, direction.id)

        report = self.backlog.migrate_directions_to_domain_nodes(dry_run=True)
        self.assertEqual(len(report["directions_migrated"]), 1)
        self.assertEqual(report["directions_migrated"][0]["direction_id"], direction.id)
        self.assertEqual(len(report["goals_reparented"]), 1)
        self.assertEqual(report["goals_reparented"][0]["goal_id"], goal.id)

        self.backlog.load()
        self.assertIsNone(self.backlog._nodes.get(direction.id))
        reloaded_goal = self.backlog._nodes[goal.id]
        self.assertIsNone(reloaded_goal.parent_id)

    def test_real_run_migrates_and_reparents(self):
        direction = self.backlog.add_direction("投资学习")
        goal = self.backlog.add_goal(title="读完一本书", source="user")
        self.backlog.assign_direction(goal.id, direction.id)

        report = self.backlog.migrate_directions_to_domain_nodes(dry_run=False)
        self.assertEqual(len(report["directions_migrated"]), 1)

        self.backlog.load()
        domain_node = self.backlog._nodes[direction.id]
        self.assertEqual(domain_node.level, "domain")
        self.assertEqual(domain_node.title, "投资学习")
        root = [n for n in self.backlog.all_nodes() if n.level == "ultimate"][0]
        self.assertIn(domain_node.id, root.children_ids)

        reloaded_goal = self.backlog._nodes[goal.id]
        self.assertEqual(reloaded_goal.parent_id, direction.id)
        # direction_id 保留，兼容读取
        self.assertEqual(reloaded_goal.direction_id, direction.id)

    def test_idempotent_second_run_is_noop(self):
        direction = self.backlog.add_direction("家庭")
        goal = self.backlog.add_goal(title="陪伴", source="user")
        self.backlog.assign_direction(goal.id, direction.id)

        self.backlog.migrate_directions_to_domain_nodes(dry_run=False)
        report2 = self.backlog.migrate_directions_to_domain_nodes(dry_run=False)
        self.assertEqual(report2["directions_migrated"], [])
        self.assertEqual(report2["goals_reparented"], [])


if __name__ == "__main__":
    unittest.main()
