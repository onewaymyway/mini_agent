"""
tests/test_objective_runner_sched_lock.py

覆盖 next_doc/daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
§7.1（"共享锁没有覆盖到持久 Worker 的回调路径"）的修复：

  - ObjectivePersistentRunner / ObjectiveIsolatedRunner 新增的 sched_lock
    参数，在 _run_step() 回调 on_done/on_failed 时确实会持有传入的锁
    （而不是像修复前那样完全绕过它）。
  - sched_lock=None（默认，未开启心跳解耦）时行为与改造前完全一致——
    不会因为新增的加锁逻辑而报错或死锁。
  - 心跳线程持锁期间，runner 的回调会等锁而不是提前跑完（用一个可控的
    "先持锁 sleep 一段时间再放锁"的场景验证 happens-before 关系）。

测试里全部用 fake Agent（不构造真实 Agent/LLM client），避免依赖网络/API key。
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from mini_agent.evolution import objective_agent_bridge as bridge_mod
from mini_agent.evolution.objective_agent_bridge import (
    ObjectiveIsolatedRunner,
    ObjectivePersistentRunner,
)


class _FakeAgent:
    _instances_built = 0

    def __init__(self, sleep_seconds: float = 0.0, raise_exc: bool = False):
        _FakeAgent._instances_built += 1
        self._sleep_seconds = sleep_seconds
        self._raise_exc = raise_exc
        self._last_turn_result_invalid = False

    def run_turn(self, message: str) -> str:
        if self._sleep_seconds:
            time.sleep(self._sleep_seconds)
        if self._raise_exc:
            raise RuntimeError("boom")
        return f"done: {message[:20]}"


class _FakeAppConfig:
    pass


def _make_callbacks():
    events: list[tuple] = []

    def on_done(turn_id, summary, valid=True):
        events.append(("done", turn_id, time.time()))

    def on_failed(turn_id, error):
        events.append(("failed", turn_id, time.time()))

    return events, on_done, on_failed


class TestPersistentRunnerSchedLock(unittest.TestCase):
    def setUp(self):
        _FakeAgent._instances_built = 0

    def test_none_lock_behaves_like_before(self):
        """sched_lock=None（默认）时不应该报错，行为与改造前一致。"""
        with patch.object(bridge_mod, "build_objective_agent", side_effect=lambda *a, **kw: _FakeAgent()):
            events, on_done, on_failed = _make_callbacks()
            runner = ObjectivePersistentRunner(
                base_cfg=_FakeAppConfig(), on_done=on_done, on_failed=on_failed,
                sched_lock=None,
            )
            try:
                turn_id = runner.submit("hi", "autonomous", {"execution_id": "e1"})
                deadline = time.time() + 5
                while not events and time.time() < deadline:
                    time.sleep(0.01)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0][0], "done")
                self.assertEqual(events[0][1], turn_id)
            finally:
                runner.shutdown(wait=True)

    def test_callback_waits_for_shared_lock(self):
        """心跳线程持锁期间，runner 的 on_done 回调必须等锁释放之后才能
        跑完——验证的正是 §7.1 要修的问题：修复前这个回调完全不经过锁，
        这个测试在修复前会失败（回调会在心跳释放锁之前就完成）。"""
        shared_lock = threading.Lock()
        release_at: list[float] = []

        with patch.object(bridge_mod, "build_objective_agent", side_effect=lambda *a, **kw: _FakeAgent()):
            events, on_done, on_failed = _make_callbacks()
            runner = ObjectivePersistentRunner(
                base_cfg=_FakeAppConfig(), on_done=on_done, on_failed=on_failed,
                sched_lock=shared_lock,
            )
            try:
                shared_lock.acquire()

                def _release_later():
                    time.sleep(0.3)
                    release_at.append(time.time())
                    shared_lock.release()

                releaser = threading.Thread(target=_release_later)
                releaser.start()

                turn_id = runner.submit("hi", "autonomous", {"execution_id": "e1"})
                deadline = time.time() + 5
                while not events and time.time() < deadline:
                    time.sleep(0.01)
                releaser.join(timeout=5)

                self.assertEqual(len(events), 1)
                self.assertEqual(events[0][1], turn_id)
                # on_done 的完成时间必须晚于（或约等于）锁被释放的时间点，
                # 证明回调确实等了锁，而不是绕开它先跑完。
                self.assertGreaterEqual(events[0][2], release_at[0] - 0.05)
            finally:
                runner.shutdown(wait=True)

    def test_on_failed_also_uses_lock(self):
        """Agent 构建失败/run_turn 抛异常两条路径的 on_failed 回调也要经过
        同一把锁（不只是成功路径）。"""
        shared_lock = threading.Lock()
        with patch.object(
            bridge_mod, "build_objective_agent",
            side_effect=lambda *a, **kw: _FakeAgent(raise_exc=True),
        ):
            events, on_done, on_failed = _make_callbacks()
            runner = ObjectivePersistentRunner(
                base_cfg=_FakeAppConfig(), on_done=on_done, on_failed=on_failed,
                sched_lock=shared_lock,
            )
            try:
                turn_id = runner.submit("hi", "autonomous", {"execution_id": "e1"})
                deadline = time.time() + 5
                while not events and time.time() < deadline:
                    time.sleep(0.01)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0][0], "failed")
                self.assertEqual(events[0][1], turn_id)
                # 锁必须仍然可用（没有被死锁占住）
                acquired = shared_lock.acquire(timeout=1)
                self.assertTrue(acquired)
                if acquired:
                    shared_lock.release()
            finally:
                runner.shutdown(wait=True)


class TestIsolatedRunnerSchedLock(unittest.TestCase):
    def setUp(self):
        _FakeAgent._instances_built = 0

    def test_none_lock_behaves_like_before(self):
        with patch.object(bridge_mod, "build_objective_agent", side_effect=lambda *a, **kw: _FakeAgent()):
            events, on_done, on_failed = _make_callbacks()
            runner = ObjectiveIsolatedRunner(
                base_cfg=_FakeAppConfig(), on_done=on_done, on_failed=on_failed,
                max_workers=1, sched_lock=None,
            )
            try:
                turn_id = runner.submit("hi", "autonomous", {"execution_id": "e1"})
                deadline = time.time() + 5
                while not events and time.time() < deadline:
                    time.sleep(0.01)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0][1], turn_id)
            finally:
                runner.shutdown(wait=True)

    def test_callback_waits_for_shared_lock(self):
        shared_lock = threading.Lock()
        release_at: list[float] = []

        with patch.object(bridge_mod, "build_objective_agent", side_effect=lambda *a, **kw: _FakeAgent()):
            events, on_done, on_failed = _make_callbacks()
            runner = ObjectiveIsolatedRunner(
                base_cfg=_FakeAppConfig(), on_done=on_done, on_failed=on_failed,
                max_workers=1, sched_lock=shared_lock,
            )
            try:
                shared_lock.acquire()

                def _release_later():
                    time.sleep(0.3)
                    release_at.append(time.time())
                    shared_lock.release()

                releaser = threading.Thread(target=_release_later)
                releaser.start()

                turn_id = runner.submit("hi", "autonomous", {"execution_id": "e1"})
                deadline = time.time() + 5
                while not events and time.time() < deadline:
                    time.sleep(0.01)
                releaser.join(timeout=5)

                self.assertEqual(len(events), 1)
                self.assertEqual(events[0][1], turn_id)
                self.assertGreaterEqual(events[0][2], release_at[0] - 0.05)
            finally:
                runner.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
