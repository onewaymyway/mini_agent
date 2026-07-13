"""
tests/test_resource_arbiter_behavior_gating.py — 方案二：BehaviorContext
接入自主任务调度门控测试。

覆盖：
  1. behavior_gating_enabled=False（默认）时 _check_user_presence() 恒真，
     can_run_autonomous() 结果与改动前一致
  2. behavior_gating_enabled=True + 高活跃度 mock → can_run_autonomous() 返回 False
  3. behavior_gating_enabled=True + load_behavior_context() 返回 None
     （collector 未开启）→ 不阻塞
  4. load_behavior_context() 抛异常时保守放行（不向上传播）
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.evolution.resource_arbiter import ResourceArbiter
from mini_agent.storage.paths import AgentPaths


@dataclass
class _AutonomyCfg:
    behavior_gating_enabled: bool = False
    behavior_gating_switch_threshold: int = 3


@dataclass
class _Cfg:
    autonomy: _AutonomyCfg = field(default_factory=_AutonomyCfg)


@dataclass
class _FakeBehaviorContext:
    is_actively_engaged: Optional[bool] = None
    context_switch_count: int = 0
    recent_git_touched_paths: list = field(default_factory=list)
    recent_terminal_commands: list = field(default_factory=list)


class TestResourceArbiterBehaviorGating(unittest.TestCase):
    def _make_arbiter(self, tmp_path, autonomy_cfg=None):
        paths = AgentPaths(Path(tmp_path))
        cfg = _Cfg(autonomy=autonomy_cfg or _AutonomyCfg())
        arbiter = ResourceArbiter(paths, cfg)
        return arbiter

    def test_default_disabled_is_noop(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            arbiter = self._make_arbiter(td)
            self.assertTrue(arbiter._check_user_presence())

    def test_enabled_high_activity_blocks(self):
        import tempfile
        import mini_agent.perception.affordance_analyzer as aa

        with tempfile.TemporaryDirectory() as td:
            arbiter = self._make_arbiter(
                td, _AutonomyCfg(behavior_gating_enabled=True, behavior_gating_switch_threshold=3)
            )
            orig = aa.load_behavior_context
            aa.load_behavior_context = lambda cfg, window_minutes=30: _FakeBehaviorContext(
                is_actively_engaged=True, context_switch_count=5
            )
            try:
                self.assertFalse(arbiter._check_user_presence())
            finally:
                aa.load_behavior_context = orig

    def test_enabled_no_signal_does_not_block(self):
        import tempfile
        import mini_agent.perception.affordance_analyzer as aa

        with tempfile.TemporaryDirectory() as td:
            arbiter = self._make_arbiter(td, _AutonomyCfg(behavior_gating_enabled=True))
            orig = aa.load_behavior_context
            aa.load_behavior_context = lambda cfg, window_minutes=30: None
            try:
                self.assertTrue(arbiter._check_user_presence())
            finally:
                aa.load_behavior_context = orig

    def test_enabled_exception_falls_back_to_allow(self):
        import tempfile
        import mini_agent.perception.affordance_analyzer as aa

        with tempfile.TemporaryDirectory() as td:
            arbiter = self._make_arbiter(td, _AutonomyCfg(behavior_gating_enabled=True))

            def _raise(*a, **kw):
                raise RuntimeError("boom")

            orig = aa.load_behavior_context
            aa.load_behavior_context = _raise
            try:
                self.assertTrue(arbiter._check_user_presence())
            finally:
                aa.load_behavior_context = orig


if __name__ == "__main__":
    unittest.main()
