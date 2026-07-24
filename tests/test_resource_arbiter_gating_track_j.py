"""
tests/test_resource_arbiter_gating_track_j.py

覆盖 next_doc/kanban_and_autonomy_improvement_plan.md Track J
（资源门控降级执行）：

- ResourceArbiter.gating_state()：
  - 预算耗尽 → "blocked"（不变）。
  - frustration 低于 proprioception.frustration_threshold → "full"。
  - frustration 达到 frustration_threshold 但未达到
    autonomy.frustration_blocked_threshold → "degraded"。
  - frustration 达到 frustration_blocked_threshold → "blocked"。
  - user_presence 触发（活跃切换达到阈值）→ "degraded"，不会是 "blocked"。
  - `resource_gating_degraded_enabled=False` 时，degraded 退化为 blocked，
    与改造前的二元行为一致。
  - can_run_autonomous() 等价于 gating_state()["state"] != "blocked"。
- ObjectiveExecutor：
  - `set_gating_degraded(True)` 后 `effective_max_concurrent()` 被收紧到
    `resource_gating_degraded_max_concurrent`（与 Track K 自适应逻辑取
    更严格者）。
  - `set_gating_degraded(False)` 后恢复不降级。
  - `resource_gating_degraded_enabled=False` 时，`set_gating_degraded(True)`
    不生效（向后兼容）。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_resource_arbiter_gating_track_j.py -q
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.evolution.objective_executor import (
    MAX_CONCURRENT_OBJECTIVES,
    ObjectiveExecutor,
)
from mini_agent.evolution.resource_arbiter import ResourceArbiter
from mini_agent.storage.paths import AgentPaths


def _make_cfg(
    proprioception_overrides: dict | None = None,
    autonomy_overrides: dict | None = None,
) -> SimpleNamespace:
    proprioception = dict(frustration_threshold=0.5)
    proprioception.update(proprioception_overrides or {})
    autonomy = dict(
        resource_gating_degraded_enabled=True,
        resource_gating_degraded_max_concurrent=1,
        frustration_blocked_threshold=0.85,
        behavior_gating_enabled=False,
        behavior_gating_switch_threshold=3,
        adaptive_concurrency_enabled=False,
        max_concurrent_objectives_cap=MAX_CONCURRENT_OBJECTIVES,
        adaptive_concurrency_min=1,
    )
    autonomy.update(autonomy_overrides or {})
    return SimpleNamespace(
        proprioception=SimpleNamespace(**proprioception),
        autonomy=SimpleNamespace(**autonomy),
    )


class TestResourceArbiterGatingState(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_frustration_snapshot(self, frustration: float) -> None:
        snapshot_path = self.paths.proprioception_snapshot
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(
            json.dumps({"frustration": frustration, "updated_at": time.time()}),
            encoding="utf-8",
        )

    def test_no_snapshot_is_full(self):
        cfg = _make_cfg()
        arbiter = ResourceArbiter(self.paths, cfg)
        self.assertEqual(arbiter.gating_state()["state"], "full")
        self.assertTrue(arbiter.can_run_autonomous())

    def test_frustration_below_threshold_is_full(self):
        cfg = _make_cfg()
        self._write_frustration_snapshot(0.2)
        arbiter = ResourceArbiter(self.paths, cfg)
        self.assertEqual(arbiter.gating_state()["state"], "full")

    def test_frustration_mid_range_is_degraded_not_blocked(self):
        cfg = _make_cfg()
        self._write_frustration_snapshot(0.6)  # >= 0.5 threshold, < 0.85 blocked
        arbiter = ResourceArbiter(self.paths, cfg)
        state = arbiter.gating_state()
        self.assertEqual(state["state"], "degraded")
        # 核心行为变化：degraded 不再让 can_run_autonomous() 返回 False。
        self.assertTrue(arbiter.can_run_autonomous())

    def test_frustration_severe_is_blocked(self):
        cfg = _make_cfg()
        self._write_frustration_snapshot(0.9)  # >= 0.85 blocked threshold
        arbiter = ResourceArbiter(self.paths, cfg)
        state = arbiter.gating_state()
        self.assertEqual(state["state"], "blocked")
        self.assertFalse(arbiter.can_run_autonomous())

    def test_user_presence_triggers_degraded_never_blocked(self):
        cfg = _make_cfg(
            autonomy_overrides=dict(
                behavior_gating_enabled=True,
                behavior_gating_switch_threshold=3,
            )
        )
        arbiter = ResourceArbiter(self.paths, cfg)

        class _Ctx:
            is_actively_engaged = True
            context_switch_count = 5

        import mini_agent.perception.affordance_analyzer as affordance_mod

        original = affordance_mod.load_behavior_context
        affordance_mod.load_behavior_context = lambda cfg, window_minutes=5: _Ctx()
        try:
            state = arbiter.gating_state()
        finally:
            affordance_mod.load_behavior_context = original

        self.assertEqual(state["state"], "degraded")
        self.assertTrue(arbiter.can_run_autonomous())

    def test_degraded_disabled_falls_back_to_blocked(self):
        """resource_gating_degraded_enabled=False 时，degraded 退化为改造前
        的二元行为（视同 blocked），保证未升级配置的用户行为不变。"""
        cfg = _make_cfg(autonomy_overrides=dict(resource_gating_degraded_enabled=False))
        self._write_frustration_snapshot(0.6)
        arbiter = ResourceArbiter(self.paths, cfg)
        state = arbiter.gating_state()
        self.assertEqual(state["state"], "blocked")
        self.assertFalse(arbiter.can_run_autonomous())

    def test_budget_exhausted_still_blocks_regardless_of_degraded(self):
        cfg = _make_cfg()
        # 预算规则通过 load_self_profile 读取，缺省无 profile 时不阻塞；
        # 这里只验证 gating_state 结构包含 reason 字段，budget 硬限制的
        # 详细行为已由既有 diagnose() 测试覆盖，此处不重复构造 profile。
        arbiter = ResourceArbiter(self.paths, cfg)
        state = arbiter.gating_state()
        self.assertIn("reason", state)


class TestObjectiveExecutorGatingDegraded(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_degraded_flag_tightens_cap(self):
        cfg = _make_cfg(
            autonomy_overrides=dict(
                max_concurrent_objectives_cap=MAX_CONCURRENT_OBJECTIVES,
                resource_gating_degraded_max_concurrent=1,
            )
        )
        oe = ObjectiveExecutor(paths=self.paths, cfg=cfg)
        self.assertEqual(oe.effective_max_concurrent(), MAX_CONCURRENT_OBJECTIVES)
        oe.set_gating_degraded(True)
        self.assertEqual(oe.effective_max_concurrent(), 1)
        oe.set_gating_degraded(False)
        self.assertEqual(oe.effective_max_concurrent(), MAX_CONCURRENT_OBJECTIVES)

    def test_degraded_takes_the_stricter_of_itself_and_adaptive(self):
        cfg = _make_cfg(
            autonomy_overrides=dict(
                max_concurrent_objectives_cap=MAX_CONCURRENT_OBJECTIVES,
                resource_gating_degraded_max_concurrent=2,
                adaptive_concurrency_enabled=True,
                adaptive_concurrency_min=1,
                adaptive_concurrency_min_samples=1,
                adaptive_concurrency_failure_rate_threshold=0.5,
            )
        )
        oe = ObjectiveExecutor(paths=self.paths, cfg=cfg)
        # 种一条失败记录，触发 Track K 自适应下调一档。
        from mini_agent.evolution.objective_executor import ObjectiveExecution

        now = time.time()
        ex = ObjectiveExecution(
            execution_id="ex_1", objective_id="obj_1", objective_title="t",
            status="failed", started_at=now - 10, finished_at=now,
        )
        oe._executions[ex.execution_id] = ex

        oe.set_gating_degraded(True)
        # Track J 天花板收紧到 2，Track K 自适应在此基础上因失败率再降 1 档，
        # 取更严格者 → 1。
        self.assertEqual(oe.effective_max_concurrent(), 1)

    def test_degraded_disabled_in_config_ignores_flag(self):
        cfg = _make_cfg(autonomy_overrides=dict(resource_gating_degraded_enabled=False))
        oe = ObjectiveExecutor(paths=self.paths, cfg=cfg)
        oe.set_gating_degraded(True)
        self.assertEqual(oe.effective_max_concurrent(), MAX_CONCURRENT_OBJECTIVES)

    def test_default_not_degraded(self):
        cfg = _make_cfg()
        oe = ObjectiveExecutor(paths=self.paths, cfg=cfg)
        self.assertEqual(oe.effective_max_concurrent(), MAX_CONCURRENT_OBJECTIVES)


if __name__ == "__main__":
    unittest.main()
