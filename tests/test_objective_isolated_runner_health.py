"""
tests/test_objective_isolated_runner_health.py

覆盖 next_doc/daemon_task_hang_recovery_and_watchdog_hardening_plan.md
阶段四：ObjectiveIsolatedRunner.check_health() ——

  - 未超时的 in-flight turn 不会被判定为卡死，也不触发重建
  - 卡死数 < max_workers 时只计数（stale_turn_count 递增），不重建
  - 卡死数 >= max_workers 时整体重建线程池（_executor 对象被替换、
    pool_rebuild_count 递增、被判定卡死的 turn_id 从 _inflight 摘除）
  - force=True 时跳过超时判定，直接按当前 in-flight 数量判断
  - 重建后仍可以正常 submit() 提交到新池子

测试直接操作 _inflight 字典（白盒），不构造真实 Agent/LLM client，
避免依赖网络/API key，也不需要真的让线程卡住。
"""

from __future__ import annotations

import time
import unittest

from mini_agent.evolution.objective_agent_bridge import ObjectiveIsolatedRunner


class _FakeAutonomyCfg:
    objective_isolated_max_workers = 2
    objective_step_stale_timeout_seconds = 60
    objective_isolated_pool_rebuild_grace_seconds = 0  # 简化：不加宽限期


class _FakeAppConfig:
    autonomy = _FakeAutonomyCfg()


def _make_runner(max_workers: int = 2) -> ObjectiveIsolatedRunner:
    return ObjectiveIsolatedRunner(
        base_cfg=_FakeAppConfig(),
        on_done=lambda *a, **k: None,
        on_failed=lambda *a, **k: None,
        max_workers=max_workers,
    )


class TestObjectiveIsolatedRunnerHealth(unittest.TestCase):
    def test_no_stale_turn_no_op(self):
        runner = _make_runner(max_workers=2)
        runner._inflight["t1"] = time.time()
        result = runner.check_health(now=time.time() + 1)
        self.assertEqual(result["stale_turn_ids"], [])
        self.assertFalse(result["rebuilt"])
        self.assertEqual(runner.pool_rebuild_count, 0)
        self.assertEqual(runner.stale_turn_count, 0)

    def test_stale_below_max_workers_only_counts(self):
        runner = _make_runner(max_workers=3)
        now = time.time()
        runner._inflight["t1"] = now - 1000  # 远超 60s 阈值
        runner._inflight["t2"] = now  # 正常运行中
        old_executor = runner._executor
        result = runner.check_health(now=now)
        self.assertEqual(result["stale_turn_ids"], ["t1"])
        self.assertFalse(result["rebuilt"])
        self.assertEqual(runner.pool_rebuild_count, 0)
        self.assertEqual(runner.stale_turn_count, 1)
        # 池子没有被重建（卡死数 1 < max_workers 3）
        self.assertIs(runner._executor, old_executor)
        # 但卡死的这条 in-flight 记账在只计数模式下依然保留（还没被重建
        # 清理），只有触发重建时才会从 _inflight 摘除。
        self.assertIn("t1", runner._inflight)

    def test_stale_reaches_max_workers_triggers_rebuild(self):
        runner = _make_runner(max_workers=2)
        now = time.time()
        runner._inflight["t1"] = now - 1000
        runner._inflight["t2"] = now - 1000
        old_executor = runner._executor
        result = runner.check_health(now=now)
        self.assertEqual(sorted(result["stale_turn_ids"]), ["t1", "t2"])
        self.assertTrue(result["rebuilt"])
        self.assertEqual(runner.pool_rebuild_count, 1)
        self.assertEqual(runner.stale_turn_count, 2)
        self.assertIsNot(runner._executor, old_executor)
        self.assertNotIn("t1", runner._inflight)
        self.assertNotIn("t2", runner._inflight)

    def test_force_skips_timeout_judgement(self):
        runner = _make_runner(max_workers=1)
        now = time.time()
        runner._inflight["t1"] = now  # 刚提交，远未超时
        result = runner.check_health(now=now, force=True)
        self.assertEqual(result["stale_turn_ids"], ["t1"])
        self.assertTrue(result["rebuilt"])
        self.assertEqual(runner.pool_rebuild_count, 1)

    def test_submit_after_rebuild_still_works(self):
        runner = _make_runner(max_workers=1)
        now = time.time()
        runner._inflight["t1"] = now - 1000
        runner.check_health(now=now)
        self.assertEqual(runner.pool_rebuild_count, 1)
        turn_id = runner.submit("hello", initiator="autonomous", meta={})
        self.assertIsNotNone(turn_id)
        runner.shutdown(wait=False)


if __name__ == "__main__":
    unittest.main()
