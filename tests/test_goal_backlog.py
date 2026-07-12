"""
tests/test_goal_backlog.py — GoalBacklog / GoalNode 基础测试

重点覆盖本次修复：GoalNode 此前没有 description 字段，但
soft_goal_deriver.commit_goals() 与 api/routes.py 的 /goals 接口一直在
调用 add_goal(description=...)，该关键字参数根本不存在——每次调用都
TypeError，被更外层 except Exception 静默吞掉，"软目标自动推导"从未真正
提交成功过一个目标节点。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
from mini_agent.storage.paths import AgentPaths


class TestGoalNodeDescription(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_add_goal_accepts_description(self):
        """回归防护：add_goal(description=...) 此前必然 TypeError。"""
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(
            title="改善某能力", description="这是一段描述", source="agent_derived", priority=25,
        )
        self.assertEqual(goal.description, "这是一段描述")

    def test_add_goal_description_defaults_to_empty(self):
        """不传 description 时默认空字符串，不影响老调用方（cli/commands/goals.py
        用位置参数只传 title，其余全用关键字）。"""
        backlog = GoalBacklog(self.paths)
        goal = backlog.add_goal(title="仅标题", source="user")
        self.assertEqual(goal.description, "")

    def test_description_roundtrip_through_to_dict_from_dict(self):
        node = GoalNode(
            id="goal_x", level="goal", title="t", source="user", status="active",
            description="desc",
        )
        d = node.to_dict()
        self.assertEqual(d["description"], "desc")
        restored = GoalNode.from_dict(d)
        self.assertEqual(restored.description, "desc")

    def test_from_dict_backward_compatible_without_description_key(self):
        """老的 goal_backlog.json（改动前写入、没有 description 字段）
        加载时不应该报错，应该静默回退成空字符串。"""
        old_dict = {
            "id": "goal_old", "level": "goal", "title": "老目标",
            "source": "user", "status": "active",
        }
        node = GoalNode.from_dict(old_dict)
        self.assertEqual(node.description, "")

    def test_description_persists_across_save_and_reload(self):
        backlog = GoalBacklog(self.paths)
        backlog.add_goal(title="持久化测试", description="需要落盘再读回", source="user")
        backlog.save()

        reloaded = GoalBacklog(self.paths)
        reloaded.load()
        goals = [g for g in reloaded.active_goals() if g.title == "持久化测试"]
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0].description, "需要落盘再读回")


if __name__ == "__main__":
    unittest.main()
