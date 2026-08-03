"""
tests/test_reap_stale_steps_worker_release.py

覆盖 next_doc/daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
§7.5：reap_stale_steps() 判定 step 超时（很可能对应 worker 线程仍卡死在
run_turn() 里没返回）后，必须在 resubmit 之前调用 release_worker_fn 释放该
execution 的专属 worker——否则对 ObjectivePersistentRunner 这类"每个
execution_id 独占单线程池"的接线方式，重试/重新分解提交的新 step 会被排进
同一个卡死线程背后的队列，永远排不上，等同于该 execution 永久死锁。

测试全部用 fake release_worker_fn/submit_fn，不依赖真实 Agent/线程池，只验证
ObjectiveExecutor 一侧的调用时序/次数是否正确。

运行方式（仓库暂无 pytest.ini/conftest.py 设置 PYTHONPATH，手动指定 src）：
    PYTHONPATH=src python3 -m pytest tests/test_reap_stale_steps_worker_release.py -q
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.evolution.objective_executor import (
    MAX_STEP_RETRIES,
    ExecutionStep,
    ObjectiveExecution,
    ObjectiveExecutor,
)
from mini_agent.storage.paths import AgentPaths


class TestReapStaleStepsReleasesWorker(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.release_calls: list[str] = []
        self.submit_calls: list[tuple] = []
        self._next_turn_id = 0

    def tearDown(self):
        self._tmpdir.cleanup()

    def _fake_submit(self, message: str, initiator: str, meta: dict):
        self.submit_calls.append((message, initiator, dict(meta)))
        self._next_turn_id += 1
        return f"turn-{self._next_turn_id}"

    def _fake_release_worker(self, execution_id: str) -> None:
        self.release_calls.append(execution_id)

    def _executor(self, release_worker_fn=None) -> ObjectiveExecutor:
        return ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self._fake_submit,
            release_worker_fn=release_worker_fn,
        )

    def _seed_stuck_execution(self, oe: ObjectiveExecutor, exec_id: str, timeout: float) -> ObjectiveExecution:
        """构造一个 status=running、current step 也 running 且早已超过
        timeout 的 execution，模拟"上一次 turn 卡死没有回调"的场景。"""
        now = time.time()
        step0 = ExecutionStep(
            step_id=f"{exec_id}_s0", step_index=0, description="第一步",
            status="running", started_at=now - timeout - 5, turn_id="stuck-turn-id",
        )
        step1 = ExecutionStep(step_id=f"{exec_id}_s1", step_index=1, description="第二步")
        ex = ObjectiveExecution(
            execution_id=exec_id,
            objective_id=f"{exec_id}-obj",
            objective_title="测试目标",
            steps=[step0, step1],
            status="running",
            started_at=now - timeout - 5,
            current_step_idx=0,
        )
        oe._executions[exec_id] = ex
        oe._turn_to_exec["stuck-turn-id"] = (exec_id, 0)
        return ex

    def test_release_worker_called_before_retry_resubmit(self):
        """重试分支（retry_count < MAX_STEP_RETRIES）：release_worker_fn 必须
        在 resubmit 之前被调用一次，且指向发生卡死的 execution_id。"""
        oe = self._executor(release_worker_fn=self._fake_release_worker)
        self._seed_stuck_execution(oe, "exec_a", timeout=1.0)

        reaped = oe.reap_stale_steps(timeout_seconds=1.0)

        self.assertEqual(reaped, ["exec_a"])
        self.assertEqual(self.release_calls, ["exec_a"])
        # resubmit 确实发生了（重试路径）
        self.assertEqual(len(self.submit_calls), 1)
        # 旧的卡死 turn_id 映射必须已被清理，防止孤儿线程迟到回调误伤新 step
        self.assertNotIn("stuck-turn-id", oe._turn_to_exec)

    def test_release_worker_called_exactly_once_per_reap(self):
        """同一次 reap 只应该释放一次 worker，不应该因为内部多次 resubmit
        尝试（重试/重新分解）而重复调用。"""
        oe = self._executor(release_worker_fn=self._fake_release_worker)
        self._seed_stuck_execution(oe, "exec_b", timeout=1.0)

        oe.reap_stale_steps(timeout_seconds=1.0)

        self.assertEqual(self.release_calls.count("exec_b"), 1)

    def test_no_release_worker_fn_is_noop_backward_compatible(self):
        """未提供 release_worker_fn（共享队列/隔离 runner 默认路径）时，
        reap_stale_steps() 行为必须与改造前完全一致：不报错，正常重试。"""
        oe = self._executor(release_worker_fn=None)
        self._seed_stuck_execution(oe, "exec_c", timeout=1.0)

        reaped = oe.reap_stale_steps(timeout_seconds=1.0)

        self.assertEqual(reaped, ["exec_c"])
        self.assertEqual(len(self.submit_calls), 1)

    def test_release_worker_exception_does_not_break_reap(self):
        """release_worker_fn 内部抛异常时，reap_stale_steps() 仍必须继续完成
        重试提交——不能因为释放 worker 失败就让整个存活性回收流程连带失败，
        这本身也是一种潜在死锁（永远卡在 running 状态排不上重试）。"""
        def _boom(execution_id: str) -> None:
            raise RuntimeError("release boom")

        oe = self._executor(release_worker_fn=_boom)
        self._seed_stuck_execution(oe, "exec_d", timeout=1.0)

        reaped = oe.reap_stale_steps(timeout_seconds=1.0)

        self.assertEqual(reaped, ["exec_d"])
        self.assertEqual(len(self.submit_calls), 1)

    def test_release_worker_called_on_exhausted_retries_before_redecompose_or_fail(self):
        """重试次数已耗尽（直接判定失败/尝试重新分解）的分支同样需要先释放
        worker——重新分解后提交的新 step 同样是同一个 execution_id，同样会被
        排到卡死线程背后。"""
        oe = self._executor(release_worker_fn=self._fake_release_worker)
        ex = self._seed_stuck_execution(oe, "exec_e", timeout=1.0)
        ex.current_step.retry_count = MAX_STEP_RETRIES  # 已耗尽重试次数

        oe.reap_stale_steps(timeout_seconds=1.0)

        # 两次调用都是预期行为：§7.5 新增的"超时判定后先释放一次"+ 阶段一
        # 既有的"_on_objective_failed() 终止收尾时再释放一次"——释放两次是
        # 幂等安全的（第二次面对的是已经不存在的 execution_id，runner 内部
        # 会静默忽略），不会因为重复调用产生副作用。
        self.assertEqual(self.release_calls, ["exec_e", "exec_e"])
        # 没有配置 llm_redecompose_fn，_attempt_redecompose 直接返回 False，
        # 最终应该判定 Objective failed，且期间不应该再次 resubmit。
        self.assertEqual(oe._executions["exec_e"].status, "failed")
        self.assertEqual(self.submit_calls, [])


if __name__ == "__main__":
    unittest.main()
