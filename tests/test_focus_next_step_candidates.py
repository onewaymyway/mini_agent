"""tests/test_focus_next_step_candidates.py — 焦点行动建议（阶段三）

覆盖 next_doc/goal_tree_research_and_action_recommendation_plan.md §4.3：
  - 焦点 goal/objective 已确认执行规范 -> "继续推进"建议，带最近进展摘要
  - 焦点 goal/objective 未确认执行规范 -> "先确认执行规范"建议
  - 焦点结构节点（domain/stage）有未处理分解候选 -> "有 N 个待确认候选"建议
  - 焦点节点关联的 focus_research 调研候选 pending -> "有新调研素材"建议
  - 非焦点节点不产出建议；cfg 未开启该规则时 generate_next_actions() 不
    包含 focus_next_step 候选（默认关闭，向后兼容）
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.focus_research_trigger import FocusResearchTrigger
from mini_agent.evolution.next_action_advisor import (
    _find_focus_next_step_candidates,
    generate_next_actions,
)
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


class TestFocusNextStepCandidates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))
        self.backlog = GoalBacklog(self.paths)

    def tearDown(self):
        self._tmp.cleanup()

    def test_focus_goal_without_execution_spec_suggests_confirm_spec(self):
        root = self.backlog.get_root_node()
        goal = self.backlog.add_goal("换工作", priority=2)
        self.backlog.update_fields(root.id, current_focus_ids=[goal.id])

        candidates = _find_focus_next_step_candidates(self.paths)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].kind, "focus_next_step")
        self.assertEqual(candidates[0].ref_id, f"{goal.id}:spec")
        self.assertIn("确认执行规范", candidates[0].reason)

    def test_focus_goal_with_execution_spec_suggests_continue_with_progress(self):
        root = self.backlog.get_root_node()
        goal = self.backlog.add_goal("学习 Rust", priority=2)
        self.backlog.update_fields(
            goal.id,
            execution_spec_confirmed=True,
            progress_notes="第一行进展\n最新一行：已完成第 3 章",
        )
        self.backlog.update_fields(root.id, current_focus_ids=[goal.id])

        candidates = _find_focus_next_step_candidates(self.paths)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].ref_id, f"{goal.id}:continue")
        self.assertIn("继续推进", candidates[0].reason)
        self.assertIn("已完成第 3 章", candidates[0].reason)

    def test_focus_structural_node_with_decompose_candidates(self):
        root = self.backlog.get_root_node()
        domain = self.backlog.add_node("domain", "健康", parent_id=root.id)
        self.backlog.append_decompose_candidates(
            domain.id,
            [
                {"id": "c1", "title": "定期体检", "description": "", "level": "goal",
                 "generated_at": 0.0, "reason": "test"},
            ],
        )
        self.backlog.update_fields(root.id, current_focus_ids=[domain.id])

        candidates = _find_focus_next_step_candidates(self.paths)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].ref_id, f"{domain.id}:decompose")
        self.assertIn("1 个待确认的分解候选", candidates[0].reason)

    def test_focus_node_with_pending_research_material(self):
        root = self.backlog.get_root_node()
        goal = self.backlog.add_goal("孩子择校", priority=2)
        self.backlog.update_fields(root.id, current_focus_ids=[goal.id])
        trigger = FocusResearchTrigger(self.paths, self.backlog)
        candidate = trigger.trigger(goal.id)
        self.assertIsNotNone(candidate)

        candidates = _find_focus_next_step_candidates(self.paths)

        kinds = {c.ref_id for c in candidates}
        self.assertIn(f"{goal.id}:research", kinds)
        self.assertIn(f"{goal.id}:spec", kinds)

    def test_non_focus_node_produces_no_candidate(self):
        self.backlog.add_goal("没被聚焦的目标", priority=5)

        candidates = _find_focus_next_step_candidates(self.paths)

        self.assertEqual(candidates, [])

    def test_generate_next_actions_gated_by_cfg_flag(self):
        root = self.backlog.get_root_node()
        goal = self.backlog.add_goal("换工作", priority=2)
        self.backlog.update_fields(root.id, current_focus_ids=[goal.id])

        result = generate_next_actions(self.paths)
        self.assertIsNone(result)

        from mini_agent.config.models import DigestAdvisorConfig

        cfg = DigestAdvisorConfig(next_action_focus_next_step_enabled=True)
        result = generate_next_actions(self.paths, cfg=cfg)
        self.assertIsNotNone(result)
        self.assertTrue(
            any(item["kind"] == "focus_next_step" for item in result["items"])
        )


if __name__ == "__main__":
    unittest.main()
