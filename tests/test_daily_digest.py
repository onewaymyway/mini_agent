"""tests/test_daily_digest.py — Daily Digest 只读聚合视图专属单测。

对应 next_doc/personal_ai_alignment_upgrade_plan.md 阶段四 §4.4。

覆盖：
  1. paths=None → 返回全空结构，不报错
  2. 空项目 → 四段全部为空列表
  3. 今天最重要的事：取自 personal_state_snapshot 的 active_goals，按
     优先级降序、限制 top_n
  4. AI 已完成：goal_backlog 中 status=="completed" 的节点被列出，按
     last_touched_at 降序
  5. 需要你决定：只挑 confidence 低于阈值的候选，按置信度升序排列
  6. 风险：健康告警 + 卡住比例被如实转述，不重新计算
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.perception.daily_digest import daily_digest, _empty_digest
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class TestDailyDigest(unittest.TestCase):
    def test_paths_none_returns_empty(self):
        digest = daily_digest(None)
        self.assertEqual(digest["top_priorities"], [])
        self.assertEqual(digest["ai_completed"], [])
        self.assertEqual(digest["needs_your_decision"], [])
        self.assertEqual(digest["risks"], [])

    def test_empty_project_returns_empty_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            digest = daily_digest(paths)
            self.assertEqual(digest["top_priorities"], [])
            self.assertEqual(digest["ai_completed"], [])
            self.assertEqual(digest["needs_your_decision"], [])
            self.assertEqual(digest["risks"], [])

    def test_top_priorities_from_active_goals(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.perception.goal_backlog import load_goal_backlog

            backlog = load_goal_backlog(paths)
            backlog.add_goal(title="低优先级目标", priority=1)
            backlog.add_goal(title="高优先级目标", priority=9)

            digest = daily_digest(paths, top_n=5)
            titles = [g["title"] for g in digest["top_priorities"]]
            self.assertEqual(titles[0], "高优先级目标")
            self.assertIn("低优先级目标", titles)

    def test_ai_completed_lists_completed_goals(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.perception.goal_backlog import load_goal_backlog

            backlog = load_goal_backlog(paths)
            node = backlog.add_goal(title="已完成的目标", priority=5)
            backlog.set_status(node.id, "completed")

            digest = daily_digest(paths)
            titles = [g["title"] for g in digest["ai_completed"]]
            self.assertIn("已完成的目标", titles)
            # 已完成的目标不应出现在"今天最重要的事"里
            self.assertNotIn("已完成的目标", [g["title"] for g in digest["top_priorities"]])

    def test_needs_your_decision_only_low_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            def fake_snapshot(paths, **kwargs):
                return {
                    "items": [
                        {"item_id": "a", "domain": "user_growth", "title": "高置信度建议", "confidence": 0.9},
                        {"item_id": "b", "domain": "user_growth", "title": "低置信度建议", "confidence": 0.2},
                    ]
                }

            import mini_agent.perception.initiative_inbox as inbox_mod

            original = inbox_mod.initiative_inbox_snapshot
            inbox_mod.initiative_inbox_snapshot = fake_snapshot
            try:
                digest = daily_digest(paths)
            finally:
                inbox_mod.initiative_inbox_snapshot = original

            titles = [it["title"] for it in digest["needs_your_decision"]]
            self.assertIn("低置信度建议", titles)
            self.assertNotIn("高置信度建议", titles)

    def test_risks_reflect_progress_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            import mini_agent.perception.daily_digest as digest_mod

            def fake_state_snapshot(paths):
                return {
                    "active_goals": [],
                    "progress": {
                        "goals_with_health_alert": [{"goal_id": "g1", "alert_kind": "no_progress"}],
                        "recent_stuck_count": 2,
                        "stuck_ratio": 0.5,
                    },
                    "pending_initiatives": {"total": 0, "by_domain": {}, "urgent_count": 0},
                    "constraints": [],
                }

            import mini_agent.perception.personal_state_snapshot as pss_mod

            original = pss_mod.personal_state_snapshot
            pss_mod.personal_state_snapshot = fake_state_snapshot
            try:
                digest = digest_mod.daily_digest(paths)
            finally:
                pss_mod.personal_state_snapshot = original

            kinds = [r["kind"] for r in digest["risks"]]
            self.assertIn("health_alert", kinds)
            self.assertIn("stuck_ratio", kinds)

    def test_empty_digest_helper_shape(self):
        d = _empty_digest()
        self.assertEqual(set(d.keys()), {"generated_at", "top_priorities", "ai_completed", "needs_your_decision", "risks"})


if __name__ == "__main__":
    unittest.main()
