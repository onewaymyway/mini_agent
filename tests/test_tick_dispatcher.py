"""tests/test_tick_dispatcher.py

[next_doc/tick_dispatch_only_execution_model_plan.md 阶段一]
覆盖 evolution/tick_dispatcher.py::TickDispatcher：
  1. dispatch() 立即返回，不等待 fn；
  2. 同 key 去重、并发数上限；
  3. 结果/异常通过 on_done 回传；
  4. reap_stale() 回收卡死任务，且迟到线程不会重复释放/重复回调；
  5. 回收后名额被释放，同 key 可再次派发；
  6. stats() 计数。
"""

from __future__ import annotations

import threading
import time
import unittest

from mini_agent.evolution.tick_dispatcher import (
    STATUS_EXCEPTION,
    STATUS_OK,
    STATUS_TIMEOUT,
    DispatchResult,
    TickDispatcher,
)


def _wait_until(pred, timeout=3.0, step=0.01):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


class _FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class TestDispatchBasics(unittest.TestCase):
    def test_dispatch_returns_immediately_while_fn_blocks(self):
        d = TickDispatcher(max_workers=2)
        gate = threading.Event()
        t0 = time.time()
        ok = d.dispatch("k", lambda: gate.wait(5), timeout=60)
        elapsed = time.time() - t0
        self.assertTrue(ok)
        self.assertLess(elapsed, 0.5, "dispatch() 必须立即返回，不能等待 fn")
        self.assertTrue(d.is_running("k"))
        gate.set()
        self.assertTrue(_wait_until(lambda: not d.is_running("k")))

    def test_same_key_rejected_while_running(self):
        d = TickDispatcher(max_workers=4)
        gate = threading.Event()
        self.assertTrue(d.dispatch("k", lambda: gate.wait(5), timeout=60))
        self.assertFalse(d.dispatch("k", lambda: None, timeout=60))
        self.assertEqual(d.stats()["rejected_busy_total"], 1)
        gate.set()
        self.assertTrue(_wait_until(lambda: not d.is_running("k")))
        # 跑完之后同 key 可以再次派发
        self.assertTrue(d.dispatch("k", lambda: None, timeout=60))

    def test_max_workers_limit(self):
        d = TickDispatcher(max_workers=2)
        gate = threading.Event()
        self.assertTrue(d.dispatch("a", lambda: gate.wait(5), timeout=60))
        self.assertTrue(d.dispatch("b", lambda: gate.wait(5), timeout=60))
        self.assertFalse(d.dispatch("c", lambda: None, timeout=60))
        self.assertEqual(d.stats()["rejected_full_total"], 1)
        gate.set()
        self.assertTrue(_wait_until(lambda: d.stats()["running"] == 0))


class TestOnDone(unittest.TestCase):
    def test_on_done_receives_value(self):
        d = TickDispatcher()
        got: list[DispatchResult] = []
        d.dispatch("k", lambda: 42, on_done=got.append, timeout=60)
        self.assertTrue(_wait_until(lambda: len(got) == 1))
        self.assertEqual(got[0].status, STATUS_OK)
        self.assertEqual(got[0].value, 42)
        self.assertEqual(d.stats()["completed_total"], 1)

    def test_on_done_receives_exception(self):
        d = TickDispatcher()
        got: list[DispatchResult] = []

        def _boom():
            raise ValueError("bad")

        d.dispatch("k", _boom, on_done=got.append, timeout=60)
        self.assertTrue(_wait_until(lambda: len(got) == 1))
        self.assertEqual(got[0].status, STATUS_EXCEPTION)
        self.assertIn("ValueError", got[0].error_repr)
        self.assertEqual(d.stats()["failed_total"], 1)
        # 异常后名额已释放
        self.assertFalse(d.is_running("k"))

    def test_on_done_exception_does_not_leak_slot(self):
        d = TickDispatcher(max_workers=1)

        def _bad_cb(_r):
            raise RuntimeError("cb boom")

        d.dispatch("k", lambda: 1, on_done=_bad_cb, timeout=60)
        self.assertTrue(_wait_until(lambda: not d.is_running("k")))
        self.assertTrue(d.dispatch("k2", lambda: 1, timeout=60))


class TestReapStale(unittest.TestCase):
    def test_reap_stale_releases_slot_and_calls_on_done_timeout(self):
        clock = _FakeClock()
        d = TickDispatcher(max_workers=1, grace_seconds=10, clock=clock)
        gate = threading.Event()
        got: list[DispatchResult] = []
        self.assertTrue(d.dispatch("k", lambda: gate.wait(10), on_done=got.append, timeout=100))

        # 未超过 timeout+grace：不回收
        clock.t += 105
        self.assertEqual(d.reap_stale(), [])
        self.assertTrue(d.is_running("k"))

        # 超过 timeout+grace：回收
        clock.t += 10
        self.assertEqual(d.reap_stale(), ["k"])
        self.assertFalse(d.is_running("k"))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].status, STATUS_TIMEOUT)
        self.assertEqual(d.stats()["timed_out_total"], 1)
        # 名额已释放，同 key 可重新派发
        self.assertTrue(d.dispatch("k", lambda: None, timeout=100))

        gate.set()  # 让孤儿线程结束

    def test_late_finisher_after_reap_does_not_double_release_or_callback(self):
        clock = _FakeClock()
        d = TickDispatcher(max_workers=2, grace_seconds=0, clock=clock)
        gate = threading.Event()
        got: list[DispatchResult] = []
        d.dispatch("k", lambda: gate.wait(10), on_done=got.append, timeout=10)
        clock.t += 20
        d.reap_stale()
        self.assertEqual(len(got), 1)  # timeout 回调

        # 同 key 的新一轮已经在跑
        gate2 = threading.Event()
        got2: list[DispatchResult] = []
        self.assertTrue(d.dispatch("k", lambda: gate2.wait(10), on_done=got2.append, timeout=10))

        # 旧线程迟到完成：不能释放新一轮的名额，也不能再回调
        gate.set()
        time.sleep(0.2)
        self.assertTrue(d.is_running("k"), "迟到的旧线程不应释放新一轮的记账")
        self.assertEqual(len(got), 1, "迟到的旧线程不应再次回调")

        gate2.set()
        self.assertTrue(_wait_until(lambda: len(got2) == 1))
        self.assertEqual(got2[0].status, STATUS_OK)

    def test_orphan_thread_counted_until_it_exits(self):
        clock = _FakeClock()
        d = TickDispatcher(grace_seconds=0, clock=clock)
        gate = threading.Event()
        d.dispatch("k", lambda: gate.wait(10), timeout=1)
        clock.t += 5
        d.reap_stale()
        self.assertEqual(d.stats()["orphan_threads_alive"], 1)
        gate.set()
        self.assertTrue(_wait_until(lambda: d.stats()["orphan_threads_alive"] == 0))


if __name__ == "__main__":
    unittest.main()
