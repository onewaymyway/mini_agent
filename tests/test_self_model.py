"""
tests/test_self_model.py — [具身改进 C1] AgentSelfModel 单元测试

覆盖：
  1. AgentSelfModel 基础结构：is_empty() / to_dict() / to_system_prompt_fragment()
  2. to_system_prompt_fragment() 内容正确性
     - 无数据时返回空字符串（不塞空标题占 context）
     - capability_snapshot 高/低置信度分类显示
     - affordance_summary 注入
     - internal_state 各维度阈值过滤（只在值显著时出现）
     - active_skill_count=0 时的提示
  3. update_internal_state() 更新快变量
  4. AgentSelfModelBuilder.build() 输入输出契约
     - 传入空 project_root（capability_map 加载失败）→ 优雅降级
     - 传入 affordance_map=None → 不崩溃
     - affordance_summary 只取 top 2
  5. ContextBuilder 接入：self_model_getter 被调用时返回 fragment
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch


# ── Stubs ────────────────────────────────────────────────────────────────────

@dataclass
class _FakeInternalState:
    cognitive_load: float = 0.0
    frustration: float = 0.0
    energy_budget_ratio: float = 1.0
    uncertainty: float = 0.0
    risk_perception: float = 0.0

    def to_dict(self) -> dict:
        return {
            "cognitive_load": round(self.cognitive_load, 3),
            "frustration": round(self.frustration, 3),
            "energy_budget_ratio": round(self.energy_budget_ratio, 3),
            "uncertainty": round(self.uncertainty, 3),
            "risk_perception": round(self.risk_perception, 3),
        }


@dataclass
class _FakeAffordanceMap:
    top_opportunities: list = field(default_factory=list)
    unexplored_areas: list = field(default_factory=list)


from mini_agent.perception.self_model import AgentSelfModel, AgentSelfModelBuilder


# ── AgentSelfModel 基础结构 ──────────────────────────────────────────────────

class TestAgentSelfModelBasic(unittest.TestCase):

    def test_empty_model_fragment_is_empty_string(self):
        """无任何数据时 to_system_prompt_fragment() 应返回空字符串。"""
        # active_skill_count > 0 且无其他数据 → 无需注入
        m = AgentSelfModel(active_skill_count=1)
        self.assertEqual(m.to_system_prompt_fragment(), "")

    def test_no_skill_note_when_zero_skills(self):
        """active_skill_count=0 时应提示当前无激活 skill。"""
        m = AgentSelfModel(active_skill_count=0)
        frag = m.to_system_prompt_fragment()
        self.assertIn("无激活 skill", frag)

    def test_update_internal_state_reflects_in_fragment(self):
        """update_internal_state 后，fragment 应包含该状态信息（若超阈值）。"""
        m = AgentSelfModel(active_skill_count=1)
        state = _FakeInternalState(cognitive_load=0.9)
        m.update_internal_state(state)
        frag = m.to_system_prompt_fragment()
        self.assertIn("认知负荷", frag)

    def test_update_internal_state_below_threshold_not_shown(self):
        """internal_state 值很低时不应注入（避免噪音）。"""
        m = AgentSelfModel(active_skill_count=1)
        state = _FakeInternalState(cognitive_load=0.1, frustration=0.05, uncertainty=0.1)
        m.update_internal_state(state)
        frag = m.to_system_prompt_fragment()
        # 低于阈值，不应有 "## 当前内部状态" 标题
        self.assertNotIn("当前内部状态", frag)


class TestAgentSelfModelCapabilitySnapshot(unittest.TestCase):

    def test_high_confidence_domain_shown_as_strong(self):
        snap = {"python": 0.9, "bash_scripting": 0.75}
        m = AgentSelfModel(capability_snapshot=snap, active_skill_count=1)
        frag = m.to_system_prompt_fragment()
        self.assertIn("高置信度", frag)
        self.assertIn("python", frag)

    def test_low_confidence_domain_shown_as_weak(self):
        snap = {"infra_ops": 0.2}
        m = AgentSelfModel(capability_snapshot=snap, active_skill_count=1)
        frag = m.to_system_prompt_fragment()
        self.assertIn("待加强", frag)
        self.assertIn("infra_ops", frag)

    def test_mid_confidence_not_shown(self):
        """0.5 <= confidence < 0.7 的领域不属于强也不属于弱，不出现在 fragment。"""
        snap = {"api_dev": 0.6}
        m = AgentSelfModel(capability_snapshot=snap, active_skill_count=1)
        frag = m.to_system_prompt_fragment()
        # 既不在高置信度里，也不在待加强里
        self.assertNotIn("api_dev", frag)

    def test_strong_domains_limited_to_3(self):
        snap = {f"domain_{i}": 0.9 for i in range(10)}
        m = AgentSelfModel(capability_snapshot=snap, active_skill_count=1)
        frag = m.to_system_prompt_fragment()
        # 不超过 3 个高置信度领域
        count = sum(1 for i in range(10) if f"domain_{i}" in frag)
        self.assertLessEqual(count, 3)


class TestAgentSelfModelInternalState(unittest.TestCase):

    def test_high_cognitive_load_shown(self):
        m = AgentSelfModel(active_skill_count=1)
        m.update_internal_state(_FakeInternalState(cognitive_load=0.85))
        frag = m.to_system_prompt_fragment()
        self.assertIn("认知负荷", frag)

    def test_high_frustration_shown(self):
        m = AgentSelfModel(active_skill_count=1)
        m.update_internal_state(_FakeInternalState(frustration=0.6))
        frag = m.to_system_prompt_fragment()
        self.assertIn("挫败感", frag)

    def test_low_energy_shown(self):
        m = AgentSelfModel(active_skill_count=1)
        m.update_internal_state(_FakeInternalState(energy_budget_ratio=0.1))
        frag = m.to_system_prompt_fragment()
        self.assertIn("剩余 turn 预算", frag)

    def test_high_uncertainty_shown(self):
        m = AgentSelfModel(active_skill_count=1)
        m.update_internal_state(_FakeInternalState(uncertainty=0.6))
        frag = m.to_system_prompt_fragment()
        self.assertIn("不确定性", frag)

    def test_normal_state_not_shown(self):
        """正常状态（所有值低于阈值）时，不注入内部状态块。"""
        m = AgentSelfModel(active_skill_count=1)
        m.update_internal_state(_FakeInternalState(
            cognitive_load=0.3,
            frustration=0.1,
            energy_budget_ratio=0.8,
            uncertainty=0.2,
        ))
        frag = m.to_system_prompt_fragment()
        self.assertNotIn("当前内部状态", frag)


class TestAgentSelfModelAffordanceSummary(unittest.TestCase):

    def test_affordance_summary_injected(self):
        m = AgentSelfModel(
            affordance_summary="当前最值得关注：修复支付 bug；探索 infra_ops",
            active_skill_count=1,
        )
        frag = m.to_system_prompt_fragment()
        self.assertIn("修复支付 bug", frag)

    def test_empty_affordance_summary_not_injected(self):
        m = AgentSelfModel(affordance_summary="", active_skill_count=1)
        frag = m.to_system_prompt_fragment()
        self.assertNotIn("当前最值得关注", frag)


# ── AgentSelfModelBuilder ────────────────────────────────────────────────────

class TestAgentSelfModelBuilder(unittest.TestCase):

    def test_build_with_bad_project_root_returns_model(self):
        """capability_map 加载失败（路径不存在）→ 不崩溃，返回空 snapshot。"""
        model = AgentSelfModelBuilder().build(
            project_root=Path("/nonexistent/project"),
            affordance_map=None,
            active_skill_count=2,
            use_capability_map=True,
        )
        self.assertIsInstance(model, AgentSelfModel)
        self.assertEqual(model.capability_snapshot, {})
        self.assertEqual(model.active_skill_count, 2)

    def test_build_with_affordance_map_takes_top2(self):
        """affordance_map 有多个 opportunities → 只取前 2 条。"""
        amap = _FakeAffordanceMap(
            top_opportunities=["A", "B", "C", "D"],
        )
        model = AgentSelfModelBuilder().build(
            project_root=Path("/nonexistent"),
            affordance_map=amap,
            active_skill_count=1,
        )
        # summary 只包含 A 和 B（top 2），不包含 C
        self.assertIn("A", model.affordance_summary)
        self.assertIn("B", model.affordance_summary)
        self.assertNotIn("C", model.affordance_summary)

    def test_build_without_affordance_map(self):
        """affordance_map=None 时不崩溃，affordance_summary 为空字符串。"""
        model = AgentSelfModelBuilder().build(
            project_root=Path("/nonexistent"),
            affordance_map=None,
            active_skill_count=1,
        )
        self.assertEqual(model.affordance_summary, "")

    def test_use_capability_map_false_skips_loading(self):
        """use_capability_map=False 时不尝试加载，capability_snapshot 为空。"""
        model = AgentSelfModelBuilder().build(
            project_root=Path("/tmp"),
            affordance_map=None,
            active_skill_count=1,
            use_capability_map=False,
        )
        self.assertEqual(model.capability_snapshot, {})

    def test_build_sets_session_start_at(self):
        """session_start_at 应该在构建时被设置为当前时间（不为 0）。"""
        import time
        before = time.time()
        model = AgentSelfModelBuilder().build(
            project_root=Path("/nonexistent"),
            active_skill_count=0,
        )
        after = time.time()
        self.assertGreaterEqual(model.session_start_at, before)
        self.assertLessEqual(model.session_start_at, after)


# ── ContextBuilder 接入验证 ──────────────────────────────────────────────────

class TestContextBuilderSelfModelGetter(unittest.TestCase):
    """验证 ContextBuilder 能通过 self_model_getter 获取 fragment 并注入。"""

    def test_self_model_getter_called_during_build(self):
        """build() 时如果 self_model_getter 返回非空字符串，应被追加到 base。"""
        import sys
        sys.path.insert(0, 'src')
        from mini_agent.context_builder import ContextBuilder

        # minimal cfg mock
        cfg = MagicMock()
        cfg.skill_chunking_enabled = False
        cfg.global_knowledge_enabled = False
        cfg.workdir_knowledge_enabled = False
        cfg.memory_top_k = 3
        cfg.agent_name = "test-agent"
        cfg.system_extra = ""
        cfg.project_root = Path("/tmp")

        sentinel = "## 当前项目能力分布（本 workdir 实测）\n- 高置信度：python(90%)"

        with patch("mini_agent.config.build_system_prompt", return_value="BASE_SYSTEM"):
            ctx = ContextBuilder(
                cfg=cfg,
                self_model_getter=lambda: sentinel,
            )
            result = ctx.build(history=[])

        self.assertIn("BASE_SYSTEM", result)
        self.assertIn(sentinel, result)

    def test_self_model_getter_none_does_not_error(self):
        """self_model_getter=None 时 build() 不出错。"""
        import sys
        sys.path.insert(0, 'src')
        from mini_agent.context_builder import ContextBuilder

        cfg = MagicMock()
        cfg.skill_chunking_enabled = False
        cfg.global_knowledge_enabled = False
        cfg.workdir_knowledge_enabled = False
        cfg.memory_top_k = 3
        cfg.agent_name = "test-agent"
        cfg.system_extra = ""
        cfg.project_root = Path("/tmp")

        with patch("mini_agent.config.build_system_prompt", return_value="BASE"):
            ctx = ContextBuilder(cfg=cfg, self_model_getter=None)
            result = ctx.build(history=[])

        self.assertIn("BASE", result)

    def test_self_model_getter_exception_is_silenced(self):
        """self_model_getter 抛出异常时，build() 不崩溃（感知层失败不阻断）。"""
        import sys
        sys.path.insert(0, 'src')
        from mini_agent.context_builder import ContextBuilder

        cfg = MagicMock()
        cfg.skill_chunking_enabled = False
        cfg.global_knowledge_enabled = False
        cfg.workdir_knowledge_enabled = False
        cfg.memory_top_k = 3
        cfg.agent_name = "test-agent"
        cfg.system_extra = ""
        cfg.project_root = Path("/tmp")

        def bad_getter():
            raise RuntimeError("mock failure")

        with patch("mini_agent.config.build_system_prompt", return_value="BASE"):
            ctx = ContextBuilder(cfg=cfg, self_model_getter=bad_getter)
            result = ctx.build(history=[])

        self.assertIn("BASE", result)


if __name__ == "__main__":
    unittest.main()
