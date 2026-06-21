"""
tests/test_evolution_agent_profile.py — Stage 3.1 验证（Phase C 之四）

对应 self_evolution_implementation_plan.md Stage 3.1 / 设计文档 6.1 节
"角色分离：专职的'进化者' subagent"：

  .agent/agents/evolution-agent.md 必须能被 AgentProfileLoader 正确解析，
  工具集仅限于 lesson 审查 + skill_propose 所需的最小集合，且模板占位符
  与 /evolve review 实际传入的 inputs 字段一致。

本文件只测"这份 profile 文件本身是否符合预期结构"，不重复测试
spawn_named_agent / render_profile_prompt 的通用逻辑（那部分由
test_orchestrator.py 等既有测试覆盖）。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from mini_agent.orchestrator.agent_profiles import (
    _parse_profile,
    render_profile_prompt,
    validate_inputs,
)

PROFILE_PATH = Path(__file__).parent.parent / ".agent" / "agents" / "evolution-agent.md"


class TestEvolutionAgentProfile(unittest.TestCase):

    def setUp(self):
        self.profile = _parse_profile(PROFILE_PATH)

    def test_profile_file_exists(self):
        self.assertTrue(PROFILE_PATH.is_file())

    def test_profile_parses_successfully(self):
        self.assertIsNotNone(self.profile)

    def test_profile_name(self):
        self.assertEqual(self.profile.name, "evolution-agent")

    def test_profile_has_description(self):
        self.assertTrue(self.profile.description)

    def test_profile_tools_limited_to_review_and_propose(self):
        """设计文档 6.1 节：'仅暴露读 lesson memory、聚类、skill_propose、StateRepo
        操作、eval 跑分，不暴露普通任务工具'——Stage 3.1 范围内（无 eval 跑分集成）
        限定为 skill_propose + 只读检查工具。"""
        self.assertEqual(
            set(self.profile.tools),
            {"skill_propose", "read_file", "grep", "list_dir"},
        )

    def test_profile_does_not_expose_write_file_or_bash(self):
        """进化者不应该拿到通用写文件/执行命令的能力，所有写入必须经过
        skill_propose（进而经过 StateRepo 的 T1 校验流水线）。"""
        self.assertNotIn("write_file", self.profile.tools)
        self.assertNotIn("bash", self.profile.tools)
        self.assertNotIn("patch_file", self.profile.tools)
        self.assertNotIn("create_file", self.profile.tools)
        self.assertNotIn("delete_file", self.profile.tools)
        self.assertNotIn("spawn_agent", self.profile.tools)

    def test_profile_no_auto_trigger_role_type(self):
        """触发方式是 /evolve review（人工）或 SessionEnd hook，不是
        role_type 驱动的'每次输出后自动触发'机制（设计文档 6.1 节）。"""
        self.assertEqual(self.profile.role_type, "")

    def test_profile_declares_lessons_input_required(self):
        lessons_spec = next((i for i in self.profile.inputs if i.name == "lessons"), None)
        self.assertIsNotNone(lessons_spec)
        self.assertTrue(lessons_spec.required)
        self.assertEqual(lessons_spec.type, "array")

    def test_profile_declares_existing_skills_input_optional(self):
        spec = next((i for i in self.profile.inputs if i.name == "existing_skills"), None)
        self.assertIsNotNone(spec)
        self.assertFalse(spec.required)

    def test_validate_inputs_requires_lessons(self):
        err = validate_inputs(self.profile, {})
        self.assertIsNotNone(err)
        self.assertIn("lessons", err)

    def test_validate_inputs_passes_with_lessons_only(self):
        err = validate_inputs(self.profile, {"lessons": [{"trigger": "x"}]})
        self.assertIsNone(err)

    def test_render_includes_lessons_and_existing_skills(self):
        prompt = render_profile_prompt(
            self.profile,
            inputs={
                "lessons": [{"entry_id": "lesson_001", "trigger": "rm -rf incident"}],
                "existing_skills": ["code-review"],
            },
            context="Triggered by /evolve review",
        )
        self.assertIn("lesson_001", prompt)
        self.assertIn("rm -rf incident", prompt)
        self.assertIn("code-review", prompt)
        self.assertIn("Triggered by /evolve review", prompt)

    def test_render_with_default_empty_existing_skills(self):
        prompt = render_profile_prompt(
            self.profile,
            inputs={"lessons": [{"entry_id": "lesson_001"}]},
        )
        # existing_skills 未提供时使用 default: []
        self.assertIn("[]", prompt)

    def test_prompt_mentions_skill_propose_usage(self):
        """正文应该明确指导模型如何调用 skill_propose（含 source_lessons 字段）。"""
        self.assertIn("skill_propose", self.profile.system_prompt)
        self.assertIn("source_lessons", self.profile.system_prompt)


if __name__ == "__main__":
    unittest.main()
