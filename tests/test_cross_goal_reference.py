"""tests/test_cross_goal_reference.py

覆盖 next_doc/cross_goal_experience_reuse_plan.md：
`perception/cross_goal_reference.py::find_similar_confirmed_goals()`
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.perception.cross_goal_reference import find_similar_confirmed_goals
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.perception.goal_execution_spec import Deliverable, GoalExecutionSpec, save_spec
from mini_agent.storage.paths import AgentPaths


class TestFindSimilarConfirmedGoals(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _confirm_with_spec(self, goal, deliverable_name="report.md"):
        self.backlog.update_fields(goal.id, execution_spec_confirmed=True)
        spec = GoalExecutionSpec(
            goal_id=goal.id,
            deliverables=[Deliverable(name=deliverable_name, naming_pattern=deliverable_name)],
        )
        save_spec(self.paths, goal.id, spec)

    def test_none_backlog_returns_empty(self):
        self.assertEqual(find_similar_confirmed_goals(None, "标题"), [])

    def test_empty_query_returns_empty(self):
        result = find_similar_confirmed_goals(self.backlog, "", "")
        self.assertEqual(result, [])

    def test_no_confirmed_goals_returns_empty(self):
        self.backlog.add_goal(title="每周读书笔记整理", description="整理本周读的书")
        result = find_similar_confirmed_goals(self.backlog, "每周读书笔记整理", paths=self.paths)
        self.assertEqual(result, [])

    def test_similar_confirmed_goal_is_found_with_summary(self):
        goal = self.backlog.add_goal(title="每周读书笔记整理", description="整理本周读的书，产出周报")
        self._confirm_with_spec(goal)
        result = find_similar_confirmed_goals(
            self.backlog, "每周读书笔记整理", "整理本周读的书，产出周报", paths=self.paths,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["goal_id"], goal.id)
        self.assertGreater(result[0]["similarity"], 0.9)
        self.assertIn("report.md", result[0]["spec_summary"])

    def test_dissimilar_goal_is_not_returned(self):
        goal = self.backlog.add_goal(title="每周读书笔记整理", description="整理本周读的书")
        self._confirm_with_spec(goal)
        result = find_similar_confirmed_goals(
            self.backlog, "服务器磁盘空间巡检", "检查磁盘使用率是否超过阈值", paths=self.paths,
        )
        self.assertEqual(result, [])

    def test_excludes_goal_by_id(self):
        goal = self.backlog.add_goal(title="每周读书笔记整理", description="整理本周读的书")
        self._confirm_with_spec(goal)
        result = find_similar_confirmed_goals(
            self.backlog, "每周读书笔记整理", "整理本周读的书", paths=self.paths,
            exclude_goal_id=goal.id,
        )
        self.assertEqual(result, [])

    def test_top_k_limits_results(self):
        for i in range(5):
            g = self.backlog.add_goal(title="每周读书笔记整理", description="整理本周读的书")
            self._confirm_with_spec(g, deliverable_name=f"report_{i}.md")
        result = find_similar_confirmed_goals(
            self.backlog, "每周读书笔记整理", "整理本周读的书", paths=self.paths, top_k=2,
        )
        self.assertEqual(len(result), 2)

    def test_missing_spec_file_skips_candidate(self):
        """confirmed=True 但 spec 文件被删除/从未真正写入的情况——不应该
        展示一个读不到内容的推荐。"""
        goal = self.backlog.add_goal(title="每周读书笔记整理", description="整理本周读的书")
        self.backlog.update_fields(goal.id, execution_spec_confirmed=True)
        # 故意不调用 save_spec
        result = find_similar_confirmed_goals(
            self.backlog, "每周读书笔记整理", "整理本周读的书", paths=self.paths,
        )
        self.assertEqual(result, [])

    def test_no_paths_returns_empty(self):
        """paths 未传入时读不到任何 spec，应返回空列表而不是报错。"""
        goal = self.backlog.add_goal(title="每周读书笔记整理", description="整理本周读的书")
        self._confirm_with_spec(goal)
        result = find_similar_confirmed_goals(self.backlog, "每周读书笔记整理", "整理本周读的书")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
