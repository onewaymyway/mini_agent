"""
tests/test_blocking_guard.py

针对 next_doc/http_server_blocking_call_guard_plan.md 的单测：

  - 正常同步调用能拿到正确结果（且真的跑在线程池，不阻塞事件循环）
  - 超时：无 fallback 时抛 HTTPException(504)，有 fallback 时返回 fallback
  - 业务异常原样透传（不吞、不改造成 HTTPException）
  - 连续失败达到阈值后熔断打开，短路后续调用；冷却结束后恢复
"""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fastapi import HTTPException

from mini_agent.utils.blocking_guard import (
    run_blocking,
    get_blocking_call_health_snapshot,
    _reset_blocking_call_health_for_tests,
)


def _run(coro):
    return asyncio.run(coro)


class TestRunBlockingBasic(unittest.TestCase):
    def setUp(self):
        _reset_blocking_call_health_for_tests()

    def test_normal_call_returns_result(self):
        def add(a, b):
            return a + b

        result = _run(run_blocking(add, 1, 2, where="basic_add"))
        self.assertEqual(result, 3)

    def test_does_not_block_event_loop(self):
        """核心场景回归：同步阻塞调用不应该卡住其他协程。"""
        order = []

        def blocking_sleep():
            time.sleep(0.3)
            order.append("blocking_done")
            return "ok"

        async def other_coro():
            await asyncio.sleep(0.05)
            order.append("other_done")

        async def main():
            await asyncio.gather(
                run_blocking(blocking_sleep, where="evt_loop_check"),
                other_coro(),
            )

        _run(main())
        # 如果事件循环被同步调用卡住，other_coro 只能在 blocking_sleep 之后才跑完，
        # 顺序会变成 [blocking_done, other_done]；用 to_thread 挪走之后
        # other_coro（0.05s）应该先于 blocking_sleep（0.3s）完成。
        self.assertEqual(order, ["other_done", "blocking_done"])

    def test_business_exception_propagates(self):
        def boom():
            raise ValueError("business error")

        with self.assertRaises(ValueError):
            _run(run_blocking(boom, where="basic_boom"))


class TestRunBlockingTimeout(unittest.TestCase):
    def setUp(self):
        _reset_blocking_call_health_for_tests()

    def test_timeout_without_fallback_raises_504(self):
        def slow():
            time.sleep(0.5)
            return "done"

        with self.assertRaises(HTTPException) as ctx:
            _run(run_blocking(slow, where="timeout_no_fallback", timeout=0.05))
        self.assertEqual(ctx.exception.status_code, 504)

    def test_timeout_with_fallback_returns_fallback(self):
        def slow():
            time.sleep(0.5)
            return "done"

        result = _run(
            run_blocking(slow, where="timeout_with_fallback", timeout=0.05, fallback="FB")
        )
        self.assertEqual(result, "FB")

    def test_timeout_with_none_fallback_distinguished_from_unset(self):
        """fallback=None 显式传入时也要走 fallback 分支，不能跟'没传'混淆。"""
        def slow():
            time.sleep(0.5)

        result = _run(
            run_blocking(slow, where="timeout_none_fallback", timeout=0.05, fallback=None)
        )
        self.assertIsNone(result)


class TestCircuitBreaker(unittest.TestCase):
    def setUp(self):
        _reset_blocking_call_health_for_tests()

    def test_opens_after_consecutive_failures_then_short_circuits(self):
        def slow():
            time.sleep(0.3)

        where = "circuit_open_test"
        for _ in range(3):
            with self.assertRaises(HTTPException):
                _run(run_blocking(slow, where=where, timeout=0.01, failure_threshold=3))

        # 熔断应已打开：即便这次调用本身很快、传了充足的 timeout，也应该被短路
        def fast():
            return "should not run"

        with self.assertRaises(HTTPException) as ctx:
            _run(run_blocking(fast, where=where, timeout=5, failure_threshold=3, cooldown_seconds=60))
        self.assertEqual(ctx.exception.status_code, 503)

        snapshot = get_blocking_call_health_snapshot()
        self.assertTrue(snapshot[where]["circuit_open"])

    def test_recovers_after_cooldown(self):
        def slow():
            time.sleep(0.3)

        where = "circuit_cooldown_test"
        for _ in range(3):
            with self.assertRaises(HTTPException):
                _run(run_blocking(slow, where=where, timeout=0.01, failure_threshold=3, cooldown_seconds=0.1))

        time.sleep(0.15)  # 等冷却结束

        def fast():
            return "recovered"

        result = _run(
            run_blocking(fast, where=where, timeout=5, failure_threshold=3, cooldown_seconds=0.1)
        )
        self.assertEqual(result, "recovered")
        snapshot = get_blocking_call_health_snapshot()
        self.assertFalse(snapshot[where]["circuit_open"])

    def test_success_resets_failure_count(self):
        where = "circuit_reset_test"

        def boom():
            raise RuntimeError("x")

        def ok():
            return "ok"

        with self.assertRaises(RuntimeError):
            _run(run_blocking(boom, where=where, failure_threshold=3))
        with self.assertRaises(RuntimeError):
            _run(run_blocking(boom, where=where, failure_threshold=3))
        # 还没到阈值 3，且这次成功了，应该清零，不会莫名其妙在后面失败一次就熔断
        _run(run_blocking(ok, where=where, failure_threshold=3))
        snapshot = get_blocking_call_health_snapshot()
        self.assertEqual(snapshot[where]["consecutive_failures"], 0)


if __name__ == "__main__":
    unittest.main()
