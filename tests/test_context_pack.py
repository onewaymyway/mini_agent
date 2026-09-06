"""tests/test_context_pack.py — Context Pack 组装器专属单测。

对应 next_doc/personal_ai_alignment_upgrade_plan.md 阶段三 §4.3。

覆盖：
  1. paths=None → 只返回 goal_summary 的最小 ContextPack，不报错
  2. 空项目（无 wiki/无 profile/无 goal）→ 各字段留空，to_prompt_block()
     只保留 Goal 一段
  3. Current Evidence：user_stated/ai_observation 与 ai_inference 严格分列，
     后者带"推测，非事实"提示
  4. Relevant Decisions：真实写入 decision 页面后能被组装进 Context Pack
  5. Current State：接入阶段二 personal_state_snapshot 的活跃 Goal 摘要
  6. to_prompt_block()：字段为空的小节整体省略，不留空标题
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.context_builder import ContextPack, build_context_pack
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class TestBuildContextPack(unittest.TestCase):
    def test_paths_none_returns_minimal_pack(self):
        pack = build_context_pack(None, "写一份周报")
        self.assertEqual(pack.goal_summary, "写一份周报")
        self.assertEqual(pack.current_state_summary, "")
        self.assertEqual(pack.relevant_decisions, [])
        self.assertIn("Goal: 写一份周报", pack.to_prompt_block())

    def test_empty_project_all_fields_blank_except_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            pack = build_context_pack(paths, "上线新功能")
            self.assertEqual(pack.current_state_summary, "")
            self.assertEqual(pack.relevant_decisions, [])
            self.assertEqual(pack.relevant_experience, [])
            self.assertEqual(pack.world_context, [])
            self.assertEqual(pack.evidence, {"factual": [], "inferred": []})
            block = pack.to_prompt_block()
            self.assertIn("Goal: 上线新功能", block)
            self.assertNotIn("Current State", block)
            self.assertNotIn("Relevant Decisions", block)
            self.assertNotIn("Current Evidence", block)

    def test_current_evidence_separates_factual_and_inferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.profile import UserProfileManager

            manager = UserProfileManager(paths)
            manager.add_constraint("不要自动发消息")  # source=user_stated
            profile = manager.load()
            profile.derived["values"] = [
                {
                    "text": "我倾向于让 AI 自主推进",
                    "source": "ai_inference",
                    "confidence": 0.8,
                    "last_confirmed_at": 0.0,
                }
            ]
            manager.save()

            pack = build_context_pack(paths, "评估目标")
            self.assertTrue(any("不要自动发消息" in f for f in pack.evidence["factual"]))
            self.assertTrue(any("自主推进" in i for i in pack.evidence["inferred"]))

            block = pack.to_prompt_block()
            self.assertIn("Current Evidence", block)
            self.assertIn("推测，非用户明确事实", block)
            # 事实条目应出现在"推测"提示之前
            self.assertLess(block.index("不要自动发消息"), block.index("推测，非用户明确事实"))

    def test_relevant_decisions_populated_from_wiki(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            paths.ensure_wiki_dirs()
            from mini_agent.wiki.writer import write_page

            write_page(
                paths,
                page_id="decision_sqlite_choice",
                page_type="decision",
                body="经过评估，选择用 SQLite 而不是文件存储，更适合当前的并发写入场景。",
                tags=["storage", "sqlite"],
                status="active",
            )

            pack = build_context_pack(paths, "选择用 SQLite 而不是文件存储的场景")
            block = pack.to_prompt_block()
            if pack.relevant_decisions:
                self.assertIn("Relevant Decisions", block)
                self.assertIn("decision_sqlite_choice", block)

    def test_current_state_reflects_active_goals(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.perception.goal_backlog import load_goal_backlog

            backlog = load_goal_backlog(paths)
            backlog.add_goal(title="修复登录 bug", priority=9)

            pack = build_context_pack(paths, "修复登录 bug")
            self.assertIn("修复登录 bug", pack.current_state_summary)
            self.assertIn("Current State", pack.to_prompt_block())

    def test_context_pack_to_prompt_block_omits_empty_sections(self):
        pack = ContextPack(goal_summary="目标 A")
        block = pack.to_prompt_block()
        self.assertEqual(block, "Goal: 目标 A")


if __name__ == "__main__":
    unittest.main()
