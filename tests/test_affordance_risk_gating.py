"""
tests/test_affordance_risk_gating.py — 方案一：AffordanceMap 高风险域接入
自主探索门控测试。

覆盖：
  1. persist_affordance_map() / load_recent_high_risk_zones() 往返测试
  2. 超过 max_age_minutes 时返回空列表（过期判定）
  3. 文件不存在时返回空列表（不抛异常）
  4. SoftGoalDeriver._from_capability_map() 对高风险域候选的 urgency 降权
  5. risk_gating_enabled=False 时行为与改动前完全一致（回归防护）
  6. ExplorationSandbox 对高风险域探索的 token 上限收紧
"""

from __future__ import annotations

import time
import unittest
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

from mini_agent.perception.affordance_analyzer import (
    AffordanceMap,
    persist_affordance_map,
    load_recent_high_risk_zones,
)
from mini_agent.storage.paths import AgentPaths


@dataclass
class _Cfg:
    risk_gating_enabled: bool = True
    risk_downweight_factor: float = 0.4


@dataclass
class _AffordanceCfgHolder:
    affordance: _Cfg = field(default_factory=_Cfg)
    autonomy: object = None


class TestAffordancePersistence(unittest.TestCase):
    def test_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            paths = AgentPaths(Path(td))
            amap = AffordanceMap(high_risk_zones=["数据库迁移", "支付回调"])
            persist_affordance_map(paths, amap)
            zones = load_recent_high_risk_zones(paths)
            self.assertEqual(zones, ["数据库迁移", "支付回调"])

    def test_expired_returns_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            paths = AgentPaths(Path(td))
            amap = AffordanceMap(high_risk_zones=["数据库迁移"])
            persist_affordance_map(paths, amap)
            zones = load_recent_high_risk_zones(paths, max_age_minutes=-1)  # 强制过期
            self.assertEqual(zones, [])

    def test_missing_file_returns_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            paths = AgentPaths(Path(td))
            zones = load_recent_high_risk_zones(paths)
            self.assertEqual(zones, [])


class TestSoftGoalDeriverRiskGating(unittest.TestCase):
    def _make_deriver(self, tmp_path, risk_gating_enabled=True, factor=0.4):
        from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver

        paths = AgentPaths(Path(tmp_path))
        cfg = _AffordanceCfgHolder(affordance=_Cfg(
            risk_gating_enabled=risk_gating_enabled, risk_downweight_factor=factor,
        ))
        return SoftGoalDeriver(paths, cfg), paths

    def test_high_risk_domain_downweighted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            deriver, paths = self._make_deriver(td)
            amap = AffordanceMap(high_risk_zones=["数据库迁移"])
            persist_affordance_map(paths, amap)

            @dataclass
            class _CapEntry:
                capability_name: str
                confidence: float
                success_count: int
                total_calls: int

            def _fake_load_capability_map(_paths):
                return [_CapEntry("数据库迁移工具", 0.2, 1, 5)]

            import mini_agent.evolution.consolidation as consolidation
            orig = consolidation.load_capability_map
            consolidation.load_capability_map = _fake_load_capability_map
            try:
                candidates = deriver._from_capability_map()
            finally:
                consolidation.load_capability_map = orig

            self.assertEqual(len(candidates), 1)
            baseline_urgency = (0.35 - 0.2) * 10 + 5 * 0.1
            self.assertAlmostEqual(candidates[0].urgency, baseline_urgency * 0.4, places=5)

    def test_risk_gating_disabled_keeps_original_behavior(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            deriver, paths = self._make_deriver(td, risk_gating_enabled=False)
            amap = AffordanceMap(high_risk_zones=["数据库迁移"])
            persist_affordance_map(paths, amap)

            @dataclass
            class _CapEntry:
                capability_name: str
                confidence: float
                success_count: int
                total_calls: int

            def _fake_load_capability_map(_paths):
                return [_CapEntry("数据库迁移工具", 0.2, 1, 5)]

            import mini_agent.evolution.consolidation as consolidation
            orig = consolidation.load_capability_map
            consolidation.load_capability_map = _fake_load_capability_map
            try:
                candidates = deriver._from_capability_map()
            finally:
                consolidation.load_capability_map = orig

            baseline_urgency = (0.35 - 0.2) * 10 + 5 * 0.1
            self.assertAlmostEqual(candidates[0].urgency, baseline_urgency, places=5)


class TestExplorationSandboxRiskGating(unittest.TestCase):
    def _make_sandbox(self, tmp_path, high_risk_zones):
        from mini_agent.perception.exploration_sandbox import ExplorationSandbox

        paths = AgentPaths(Path(tmp_path))
        if high_risk_zones:
            persist_affordance_map(paths, AffordanceMap(high_risk_zones=high_risk_zones))
        cfg = _AffordanceCfgHolder(affordance=_Cfg())
        sandbox = ExplorationSandbox.__new__(ExplorationSandbox)
        sandbox._paths = paths
        sandbox._cfg = cfg
        return sandbox

    def test_high_risk_domain_tightens_token_limit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sandbox = self._make_sandbox(td, ["支付回调"])

            @dataclass
            class _RB:
                daily_token_budget: int = 200_000
                exploration_budget_ratio: float = 0.10

            @dataclass
            class _Profile:
                resource_budget: _RB = field(default_factory=_RB)

            import mini_agent.perception.global_knowledge as gk
            orig = gk.load_self_profile
            gk.load_self_profile = lambda _paths: _Profile()
            try:
                limit = sandbox._risk_adjusted_token_limit("支付回调对账工具")
            finally:
                gk.load_self_profile = orig

            self.assertIsNotNone(limit)
            self.assertEqual(limit, int(200_000 * 0.10 * 0.5))

    def test_non_high_risk_domain_no_limit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sandbox = self._make_sandbox(td, ["支付回调"])
            limit = sandbox._risk_adjusted_token_limit("完全无关的领域")
            self.assertIsNone(limit)

    def test_record_tokens_raises_when_limit_exceeded(self):
        from mini_agent.perception.exploration_sandbox import (
            _ExplorationContext,
            ExplorationReport,
            ExplorationTokenLimitExceeded,
        )

        report = ExplorationReport(sandbox_id="s1", capability_id="c1", goal="g")
        ctx = _ExplorationContext(
            sandbox_id="s1", worktree_path=None, report=report, token_limit_override=100,
        )
        ctx.record_tokens(50)  # 未超限
        with self.assertRaises(ExplorationTokenLimitExceeded):
            ctx.record_tokens(60)  # 累计 110 > 100，超限


if __name__ == "__main__":
    unittest.main()
