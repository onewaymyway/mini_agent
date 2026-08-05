"""tests/test_cleanup_external_goal_candidates.py

覆盖 scripts/cleanup_external_goal_candidates.py 的 bugfix：默认不再
无条件删除 source=external_input 的 Goal 下面的所有子节点，只删除子节点
自身 source 也是 external_input 的那些；source 是 "user"/"agent_derived"
等其它值的子节点会被保留（parent_id 清空），除非显式传 --force。

背景：改造前的版本会把 `goal.children_ids` 里的每一个 id 都无条件塞进
删除集合，不管子节点自己的 source 是什么——如果用户后来在一个（原本由
旧版 goal_candidate 落点自动创建的）external_input Goal 下手动加了自己
的 Objective，运行脚本会把用户自己创建的内容一起删掉，这是实际发生过
的 bug。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
_SRC = _REPO_ROOT / "src"
for p in (str(_SRC), str(_SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from mini_agent.perception.goal_backlog import GoalBacklog  # noqa: E402
from mini_agent.storage.paths import AgentPaths  # noqa: E402

import cleanup_external_goal_candidates as cleanup_script  # noqa: E402


class TestCollectRemovalSet(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_only_deletes_external_input_children_by_default(self):
        # 一个旧版 goal_candidate 落点创建的 Goal。
        goal = self.backlog.add_goal(
            title="外部资讯自动建的目标", source="external_input", priority=10,
        )
        # 子节点 1：也是旧版落点创建的（如果历史上真的存在这种数据）。
        legacy_child = self.backlog.add_objective(
            "旧版落点子节点", parent_id=goal.id, source="external_input",
        )
        # 子节点 2：用户后来手动加的——这是 bug 场景，不应该被删。
        user_child = self.backlog.add_objective(
            "用户自己加的子任务", parent_id=goal.id, source="user",
        )
        # 子节点 3：daemon 自动拆解产生的——同样不应该被删。
        derived_child = self.backlog.add_objective(
            "daemon自动拆解的子任务", parent_id=goal.id, source="agent_derived",
        )

        self.backlog.load()
        stale = cleanup_script.find_stale_goals(self.backlog)
        self.assertEqual(len(stale), 1)

        removal_ids, skipped = cleanup_script.collect_removal_set(self.backlog, stale)

        self.assertIn(goal.id, removal_ids)
        self.assertIn(legacy_child.id, removal_ids)
        self.assertNotIn(user_child.id, removal_ids)
        self.assertNotIn(derived_child.id, removal_ids)

        skipped_ids = {c["id"] for c in skipped}
        self.assertEqual(skipped_ids, {user_child.id, derived_child.id})

    def test_force_style_removal_includes_skipped_children(self):
        """调用方（main()）在 --force 时会把 skipped 的 id 并入
        removal_ids——这里直接验证 collect_removal_set 返回的 skipped
        列表内容足以支撑这个合并逻辑（main() 的集成行为见脚本本身，不在
        这个单测范围内重复用 subprocess 跑一遍 CLI）。"""
        goal = self.backlog.add_goal(title="目标", source="external_input", priority=10)
        user_child = self.backlog.add_objective("用户子任务", parent_id=goal.id, source="user")

        self.backlog.load()
        stale = cleanup_script.find_stale_goals(self.backlog)
        removal_ids, skipped = cleanup_script.collect_removal_set(self.backlog, stale)

        forced_ids = removal_ids | {c["id"] for c in skipped}
        self.assertIn(user_child.id, forced_ids)

    def test_no_stale_goals_returns_empty(self):
        self.backlog.add_goal(title="用户自己的目标", source="user", priority=10)
        self.backlog.load()
        stale = cleanup_script.find_stale_goals(self.backlog)
        self.assertEqual(stale, [])

    def test_dangling_child_reference_does_not_crash(self):
        """children_ids 里有一个已经不存在的 id（数据历史遗留的悬挂引用）
        时，collect_removal_set 应该跳过它，不报错、不出现在 skipped 里。"""
        goal = self.backlog.add_goal(title="目标", source="external_input", priority=10)
        self.backlog.load()
        goal_in_memory = self.backlog._nodes[goal.id]  # noqa: SLF001
        goal_in_memory.children_ids.append("obj_does_not_exist")

        stale = cleanup_script.find_stale_goals(self.backlog)
        removal_ids, skipped = cleanup_script.collect_removal_set(self.backlog, stale)

        self.assertIn(goal.id, removal_ids)
        self.assertNotIn("obj_does_not_exist", removal_ids)
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main()
