"""
tests/test_unified_arbitration_p5_step3.py

对应 next_doc/goal_cron_unified_scheduler_improvement_plan.md P5 第 3 步
（接管仲裁裁决）：

- `scheduler.unified_arbitration_enabled=False`（默认）时，
  `ObjectiveExecutor.effective_max_concurrent()`/
  `CronJobRunner.effective_max_concurrent()` 的 degraded 行为与本 Track
  之前完全一致（各自独立读固定配置值）。
- `unified_arbitration_enabled=True` 时，两条通道的 degraded 上限改由
  `allocate_weighted_slots()` 按 `channel_weights`/`degraded_total_slots`/
  `cron.reserved_min_concurrent` 统一计算，且两条通道的分配结果互相一致
  （构造相同的 scheduler/cron 配置时，goal 拿到的 + cron 拿到的 == 
  degraded_total_slots）。
- 计算过程异常时静默降级为改造前的独立裁决，不影响 degraded 收紧本身
  仍然生效。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_unified_arbitration_p5_step3.py -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.evolution.cron_job_runner import CronJobRunner
from mini_agent.evolution.objective_executor import (
    MAX_CONCURRENT_OBJECTIVES,
    ObjectiveExecutor,
)
from mini_agent.storage.paths import AgentPaths


class _FakePaths:
    def __init__(self, root):
        self.project_root = str(root)
        self._root = root

    @property
    def workdir_dir(self):
        d = self._root / ".agent"
        d.mkdir(parents=True, exist_ok=True)
        return d


def _make_cfg(
    *,
    unified_arbitration_enabled=False,
    degraded_total_slots=2,
    channel_weights=None,
    reserved_min_concurrent=1,
    autonomy_degraded_cap=1,
    cron_degraded_cap=1,
):
    return SimpleNamespace(
        autonomy=SimpleNamespace(
            resource_gating_degraded_enabled=True,
            resource_gating_degraded_max_concurrent=autonomy_degraded_cap,
            max_concurrent_objectives_cap=MAX_CONCURRENT_OBJECTIVES,
            adaptive_concurrency_enabled=False,
        ),
        cron=SimpleNamespace(
            degraded_max_concurrent=cron_degraded_cap,
            reserved_min_concurrent=reserved_min_concurrent,
            skip_alert_threshold=5,
        ),
        scheduler=SimpleNamespace(
            unified_arbitration_enabled=unified_arbitration_enabled,
            degraded_total_slots=degraded_total_slots,
            channel_weights=channel_weights or {"goal": 1.0, "cron": 1.0, "goal_cycle": 1.0},
        ),
    )


class TestUnifiedArbitrationDisabledByDefault(unittest.TestCase):
    """开关关闭时，两条通道的 degraded 行为与改造前完全一致。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_objective_executor_unchanged_when_disabled(self):
        cfg = _make_cfg(unified_arbitration_enabled=False, autonomy_degraded_cap=1)
        ex = ObjectiveExecutor(self.paths, cfg=cfg)
        ex.set_gating_degraded(True)
        self.assertEqual(ex.effective_max_concurrent(), 1)

    def test_cron_job_runner_unchanged_when_disabled(self):
        cfg = _make_cfg(unified_arbitration_enabled=False, cron_degraded_cap=1)
        runner = CronJobRunner(cfg, _FakePaths(Path(self._tmpdir.name)), max_concurrent=3)
        runner.set_gating_degraded(True)
        self.assertEqual(runner.effective_max_concurrent(), 1)


class TestUnifiedArbitrationEnabled(unittest.TestCase):
    """开关开启时，两条通道改由 allocate_weighted_slots() 统一裁决。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_equal_weights_matches_default_total_slots(self):
        # degraded_total_slots=2、权重 1:1、cron 保底 1 —— 复现改造前的
        # goal=1/cron=1，是"接入但不改变现状"的默认场景。
        cfg = _make_cfg(unified_arbitration_enabled=True, degraded_total_slots=2)
        ex = ObjectiveExecutor(self.paths, cfg=cfg)
        ex.set_gating_degraded(True)
        runner = CronJobRunner(cfg, _FakePaths(Path(self._tmpdir.name)), max_concurrent=3)
        runner.set_gating_degraded(True)

        self.assertEqual(ex.effective_max_concurrent(), 1)
        self.assertEqual(runner.effective_max_concurrent(), 1)

    def test_goal_weighted_higher_gets_more_slots_cron_still_floored(self):
        # degraded_total_slots 特意选 3（而不是更大的值）：goal 通道自身还有
        # 一个更早生效的模块级绝对天花板 MAX_CONCURRENT_OBJECTIVES=2（见
        # objective_executor.py 顶部常量），是"安全阀不能被配置突破"这条
        # 既有规则（Track K 文档字符串）的延伸——`allocate_weighted_slots()`
        # 算出的份额如果超过这个天花板，会先被那道更早的 min() 收紧，
        # 所以这里选一个不会撞到该天花板的场景，验证"统一裁决生效"这件事
        # 本身，天花板与统一裁决如何叠加是另一件事，不在本用例范围内。
        cfg = _make_cfg(
            unified_arbitration_enabled=True,
            degraded_total_slots=3,
            channel_weights={"goal": 9.0, "cron": 1.0, "goal_cycle": 1.0},
            reserved_min_concurrent=1,
        )
        ex = ObjectiveExecutor(self.paths, cfg=cfg)
        ex.set_gating_degraded(True)
        runner = CronJobRunner(cfg, _FakePaths(Path(self._tmpdir.name)), max_concurrent=5)
        runner.set_gating_degraded(True)

        goal_cap = ex.effective_max_concurrent()
        cron_cap = runner.effective_max_concurrent()
        self.assertGreaterEqual(cron_cap, 1)  # 保底不受权重压低
        self.assertGreater(goal_cap, cron_cap)  # 权重更高分到更多
        self.assertEqual(goal_cap + cron_cap, 3)  # 两条通道分完全部槽位

    def test_allocation_beyond_goal_ceiling_is_clamped_by_module_cap(self):
        # 与上一个用例互补：degraded_total_slots 给得比 goal 通道能吸收的
        # 还多时（MAX_CONCURRENT_OBJECTIVES=2），goal 侧仍会被更早的模块级
        # 天花板收紧——这是既有"只降不升"安全阀的既有行为，统一裁决不改变
        # 它，只是可能算出一个"用不完"的份额（多出的槽位当前设计下不会被
        # 转给别的通道，属于已知的、留给未来评估的开放问题，见改进计划
        # 待讨论问题）。这里只断言 goal 侧确实被封顶在 2，不对总和做假设。
        cfg = _make_cfg(
            unified_arbitration_enabled=True,
            degraded_total_slots=4,
            channel_weights={"goal": 9.0, "cron": 1.0, "goal_cycle": 1.0},
            reserved_min_concurrent=1,
        )
        ex = ObjectiveExecutor(self.paths, cfg=cfg)
        ex.set_gating_degraded(True)
        self.assertEqual(ex.effective_max_concurrent(), MAX_CONCURRENT_OBJECTIVES)

    def test_full_state_unaffected_by_unified_arbitration(self):
        cfg = _make_cfg(unified_arbitration_enabled=True, degraded_total_slots=2)
        ex = ObjectiveExecutor(self.paths, cfg=cfg)
        runner = CronJobRunner(cfg, _FakePaths(Path(self._tmpdir.name)), max_concurrent=3)
        # 未调用 set_gating_degraded(True)，仍是 full 态——不受统一裁决影响
        self.assertEqual(ex.effective_max_concurrent(), MAX_CONCURRENT_OBJECTIVES)
        self.assertEqual(runner.effective_max_concurrent(), 3)

    def test_construction_cap_still_wins_over_allocation(self):
        # cron 构造上限 1，即使统一裁决算出更高的份额，也不能超过构造上限
        # （effective_max_concurrent 的"只降不升"安全阀）。
        cfg = _make_cfg(
            unified_arbitration_enabled=True,
            degraded_total_slots=4,
            channel_weights={"goal": 1.0, "cron": 9.0, "goal_cycle": 1.0},
        )
        runner = CronJobRunner(cfg, _FakePaths(Path(self._tmpdir.name)), max_concurrent=1)
        runner.set_gating_degraded(True)
        self.assertEqual(runner.effective_max_concurrent(), 1)


if __name__ == "__main__":
    unittest.main()
