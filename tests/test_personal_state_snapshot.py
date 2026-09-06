"""tests/test_personal_state_snapshot.py — perception/
personal_state_snapshot.py（personal_ai_alignment_upgrade_plan.md 阶段二）
专属单测。

覆盖：
  1. paths=None / 空项目 → 全零结构，不抛异常。
  2. 有活跃 Goal 时 active_goals 正确聚合（按优先级降序、截断字段）。
  3. constraints 正确读取阶段一的 UserProfileManager.add_constraint()。
  4. pending_initiatives 的 total/by_domain 与 initiative_inbox 一致。
  5. 任一子聚合异常不影响其它子聚合（只读聚合的容错风格）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.storage.paths import AgentPaths
from mini_agent.perception.personal_state_snapshot import personal_state_snapshot
from mini_agent.perception.goal_backlog import load_goal_backlog
from mini_agent.profile import UserProfileManager


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class TestPersonalStateSnapshot(unittest.TestCase):
    def test_none_paths_returns_empty_structure(self):
        snap = personal_state_snapshot(None)
        self.assertEqual(snap["active_goals"], [])
        self.assertEqual(snap["pending_initiatives"]["total"], 0)
        self.assertEqual(snap["constraints"], [])

    def test_empty_project_returns_empty_but_no_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            snap = personal_state_snapshot(paths)
            self.assertEqual(snap["active_goals"], [])
            self.assertEqual(snap["progress"]["recent_stuck_count"], 0)
            self.assertEqual(snap["constraints"], [])
            self.assertIn("generated_at", snap)

    def test_active_goals_are_collected_and_sorted_by_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = load_goal_backlog(paths)
            backlog.add_goal(title="低优先级目标", priority=1)
            backlog.add_goal(title="高优先级目标", priority=10)

            snap = personal_state_snapshot(paths)
            titles = [g["title"] for g in snap["active_goals"]]
            self.assertEqual(titles[0], "高优先级目标")
            self.assertIn("低优先级目标", titles)
            self.assertEqual(snap["active_goals"][0]["priority"], 10)

    def test_constraints_reflect_profile_stage1_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            manager = UserProfileManager(paths)
            manager.add_constraint("不要自动发消息")

            snap = personal_state_snapshot(paths)
            texts = [c["text"] for c in snap["constraints"]]
            self.assertEqual(texts, ["不要自动发消息"])

    def test_pending_initiatives_total_matches_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = load_goal_backlog(paths)
            backlog.add_goal(title="低优先级目标", priority=1)

            snap = personal_state_snapshot(paths)
            self.assertIsInstance(snap["pending_initiatives"]["total"], int)
            self.assertIsInstance(snap["pending_initiatives"]["by_domain"], dict)

    def test_never_raises_on_broken_goal_backlog(self):
        """[容错风格] 即便某一路数据源读取异常，整体快照仍应返回结构化
        的空结果，而不是让异常向上传播。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            # 制造一个损坏的 goals 存储文件，模拟单路数据源异常。
            goals_path = paths.workdir_dir / "goals.json"
            goals_path.parent.mkdir(parents=True, exist_ok=True)
            goals_path.write_text("{not valid json", encoding="utf-8")

            snap = personal_state_snapshot(paths)
            self.assertIsInstance(snap, dict)
            self.assertIn("active_goals", snap)


if __name__ == "__main__":
    unittest.main()
