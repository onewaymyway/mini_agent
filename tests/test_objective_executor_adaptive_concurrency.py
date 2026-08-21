"""
tests/test_objective_executor_adaptive_concurrency.py

覆盖 next_doc/kanban_and_autonomy_improvement_plan.md Track K
（并发数自适应）：

- 未提供 cfg 时，`effective_max_concurrent()` 恒等于模块级常量
  `MAX_CONCURRENT_OBJECTIVES`（仅作为无 cfg 时的兜底默认值）。
- 提供 cfg 但 `adaptive_concurrency_enabled=False` 时，同样恒定返回
  `configured_cap()`（即 `autonomy.max_concurrent_objectives_cap`，没有
  额外的硬天花板/clamp）。
- 提供 cfg 且开启自适应：
  - 样本不足时不下调。
  - 最近失败率达到阈值 → 下调一档。
  - 最近平均耗时达到阈值 → 再下调一档（可与失败率信号叠加）。
  - 无论如何不会低于 `adaptive_concurrency_min`。
  - `max_concurrent_objectives_cap` 配置得比模块常量还大时，直接以配置值
    为准（[并发上限可配置化] 需求：配置项/看板热改都不再受模块常量限制，
    没有硬天花板）。

运行方式（仓库暂无 pytest.ini/conftest.py 设置 PYTHONPATH，手动指定 src）：
    PYTHONPATH=src python3 -m pytest tests/test_objective_executor_adaptive_concurrency.py -q
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.evolution.objective_executor import (
    MAX_CONCURRENT_OBJECTIVES,
    ObjectiveExecution,
    ObjectiveExecutor,
)
from mini_agent.storage.paths import AgentPaths


def _make_cfg(**autonomy_overrides) -> SimpleNamespace:
    """构造一个只带 `.autonomy` 属性的最小 cfg 替身，避免依赖完整
    AppConfig/AutonomyConfig 的构造成本——`effective_max_concurrent()`
    全程用 getattr 读取字段，鸭子类型即可。"""
    defaults = dict(
        adaptive_concurrency_enabled=True,
        max_concurrent_objectives_cap=MAX_CONCURRENT_OBJECTIVES,
        adaptive_concurrency_min=1,
        adaptive_concurrency_min_samples=3,
        adaptive_concurrency_failure_rate_threshold=0.5,
        adaptive_concurrency_slow_duration_seconds=1800.0,
        adaptive_concurrency_window=10,
    )
    defaults.update(autonomy_overrides)
    return SimpleNamespace(autonomy=SimpleNamespace(**defaults))


class TestAdaptiveConcurrency(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _executor(self, cfg=None) -> ObjectiveExecutor:
        return ObjectiveExecutor(paths=self.paths, cfg=cfg)

    def _seed_execution(self, oe: ObjectiveExecutor, status: str, duration: float, idx: int) -> None:
        now = time.time()
        ex = ObjectiveExecution(
            execution_id=f"ex_{idx}",
            objective_id=f"obj_{idx}",
            objective_title=f"目标 {idx}",
            status=status,
            started_at=now - duration,
            finished_at=now,
        )
        oe._executions[ex.execution_id] = ex

    def test_no_cfg_returns_static_constant(self):
        oe = self._executor(cfg=None)
        self.assertEqual(oe.effective_max_concurrent(), MAX_CONCURRENT_OBJECTIVES)

    def test_adaptive_disabled_returns_cap_only(self):
        cfg = _make_cfg(adaptive_concurrency_enabled=False, max_concurrent_objectives_cap=1)
        oe = self._executor(cfg=cfg)
        # 即使历史全是失败，关闭自适应也不应该下调——直接返回配置的 cap。
        for i in range(5):
            self._seed_execution(oe, "failed", duration=10, idx=i)
        self.assertEqual(oe.effective_max_concurrent(), 1)

    def test_insufficient_samples_no_downgrade(self):
        cfg = _make_cfg()
        oe = self._executor(cfg=cfg)
        self._seed_execution(oe, "failed", duration=10, idx=0)
        self._seed_execution(oe, "failed", duration=10, idx=1)
        # 只有 2 个样本 < min_samples(3)
        self.assertEqual(oe.effective_max_concurrent(), MAX_CONCURRENT_OBJECTIVES)

    def test_high_failure_rate_downgrades_one_step(self):
        cfg = _make_cfg()
        oe = self._executor(cfg=cfg)
        for i, status in enumerate(("failed", "failed", "completed")):
            self._seed_execution(oe, status, duration=10, idx=i)
        self.assertEqual(oe.effective_max_concurrent(), MAX_CONCURRENT_OBJECTIVES - 1)

    def test_slow_average_duration_downgrades_one_step(self):
        cfg = _make_cfg()
        oe = self._executor(cfg=cfg)
        # 全部完成（失败率 0），但平均耗时远超阈值。
        for i in range(3):
            self._seed_execution(oe, "completed", duration=3600, idx=i)
        self.assertEqual(oe.effective_max_concurrent(), MAX_CONCURRENT_OBJECTIVES - 1)

    def test_never_goes_below_floor(self):
        cfg = _make_cfg(adaptive_concurrency_min=1)
        oe = self._executor(cfg=cfg)
        # 同时踩中"高失败率"和"平均耗时长"两个下调信号，理论上要降 2 档，
        # 但 MAX_CONCURRENT_OBJECTIVES 只有 2，降到底也不能低于 floor=1。
        for i, status in enumerate(("failed", "failed", "completed")):
            self._seed_execution(oe, status, duration=3600, idx=i)
        self.assertEqual(oe.effective_max_concurrent(), 1)

    def test_configured_cap_can_exceed_static_constant(self):
        """[并发上限可配置化] 配置值可以超过模块常量——不再有硬天花板，
        `effective_max_concurrent()` 直接以配置值为准。"""
        cfg = _make_cfg(max_concurrent_objectives_cap=MAX_CONCURRENT_OBJECTIVES + 5)
        oe = self._executor(cfg=cfg)
        self.assertEqual(oe.effective_max_concurrent(), MAX_CONCURRENT_OBJECTIVES + 5)

    def test_can_start_new_respects_effective_limit(self):
        cfg = _make_cfg(max_concurrent_objectives_cap=1)
        oe = self._executor(cfg=cfg)
        self.assertTrue(oe.can_start_new())
        self._seed_execution(oe, "completed", duration=1, idx=0)
        # completed 不算 running，仍应可以开始新的。
        self.assertTrue(oe.can_start_new())
        running_ex = ObjectiveExecution(
            execution_id="running_1", objective_id="obj_running",
            objective_title="进行中的目标", status="running",
        )
        oe._executions[running_ex.execution_id] = running_ex
        self.assertFalse(oe.can_start_new())


if __name__ == "__main__":
    unittest.main()
