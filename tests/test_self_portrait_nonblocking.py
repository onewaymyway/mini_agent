"""
tests/test_self_portrait_nonblocking.py

/v1/self/portrait 超时修复的回归测试：
  - 同步聚合被抽成 _collect_self_portrait，处理函数通过线程池执行；
  - 聚合期间事件循环必须仍能处理其它协程（否则同进程的 HTTP 请求会一起超时）；
  - 返回结构与修复前保持一致。
"""

from __future__ import annotations

import asyncio
import time
import types
import unittest
from unittest import mock

from mini_agent.api import routes

EXPECTED_KEYS = {
    "identity", "self_assessment", "operating_state", "capability_map",
    "capability_trend", "skills", "agent_value_profile", "body_inventory",
    "self_narrative", "drift_signals", "lineage",
}


class _Bridge:
    agent = None


def _reset_portrait_state():
    routes._portrait_cache.update(at=0.0, result=None)
    routes._lineage_cache.update(at=0.0, value=None)
    routes._portrait_inflight = None


class _ResetMixin:
    def setUp(self):
        _reset_portrait_state()


class TestSelfPortraitShape(_ResetMixin, unittest.TestCase):
    def test_collect_returns_expected_keys_without_agent(self):
        srv = types.SimpleNamespace(bridge=_Bridge())
        out = routes._collect_self_portrait(srv)
        self.assertLessEqual(EXPECTED_KEYS, set(out.keys()))  # 原有字段都在（允许新增 timings_ms）


class TestSelfPortraitNonBlocking(_ResetMixin, unittest.TestCase):
    def test_event_loop_keeps_running_while_collecting(self):
        def slow_collect(_srv):
            time.sleep(0.3)  # 模拟同步 IO / 计算
            return {k: None for k in EXPECTED_KEYS}

        class _Req:
            pass

        req = _Req()
        req.app = types.SimpleNamespace(
            state=types.SimpleNamespace(http_server=types.SimpleNamespace(bridge=_Bridge()))
        )

        async def run():
            ticks = 0
            stop = False

            async def ticker():
                nonlocal ticks
                while not stop:
                    ticks += 1
                    await asyncio.sleep(0.01)

            t = asyncio.create_task(ticker())
            with mock.patch.object(routes, "_collect_self_portrait", slow_collect), \
                 mock.patch.object(routes, "_require_owner", lambda _r: None):
                result = await routes.get_self_portrait(req)
            stop = True
            await t
            return ticks, result

        ticks, result = asyncio.run(run())
        # 若处理函数直接在事件循环上同步执行，ticker 在 0.3s 内几乎不会推进
        self.assertGreaterEqual(ticks, 10)
        self.assertLessEqual(EXPECTED_KEYS, set(result.keys()))


def _make_req():
    class _Req:
        pass
    req = _Req()
    req.app = types.SimpleNamespace(
        state=types.SimpleNamespace(http_server=types.SimpleNamespace(bridge=_Bridge()))
    )
    return req


def _call(collect, *, wait=None):
    """以给定的收集函数调用处理函数，返回 (结果, 耗时秒)。"""
    async def run():
        with mock.patch.object(routes, "_collect_self_portrait", collect), \
             mock.patch.object(routes, "_require_owner", lambda _r: None):
            if wait is not None:
                with mock.patch.object(routes, "_PORTRAIT_WAIT_SECONDS", wait):
                    return await routes.get_self_portrait(_make_req())
            return await routes.get_self_portrait(_make_req())
    t = time.time()
    res = asyncio.run(run())
    return res, time.time() - t


class TestSelfPortraitBoundedResponse(_ResetMixin, unittest.TestCase):
    def test_fast_compute_returns_fresh_result(self):
        res, _ = _call(lambda _s: {**{k: None for k in EXPECTED_KEYS}, "timings_ms": {"total": 1}})
        self.assertFalse(res["stale"])
        self.assertFalse(res["refreshing"])
        self.assertEqual(res["timings_ms"], {"total": 1})

    def test_slow_compute_returns_within_wait_with_placeholder(self):
        def slow(_s):
            time.sleep(1.0)
            return {k: None for k in EXPECTED_KEYS}
        res, elapsed = _call(slow, wait=0.1)
        self.assertLess(elapsed, 0.8)            # 不等慢计算完成
        self.assertTrue(res["pending"])
        self.assertTrue(res["refreshing"])
        self.assertEqual(set(res) & EXPECTED_KEYS, EXPECTED_KEYS)  # 占位结构完整
        time.sleep(1.1)                           # 等后台计算结束，避免影响后续用例

    def test_slow_compute_serves_expired_cache_as_stale(self):
        routes._portrait_cache.update(at=time.time() - 3600, result={**{k: None for k in EXPECTED_KEYS}, "identity": {"purpose": "old"}})
        def slow(_s):
            time.sleep(1.0)
            return {k: None for k in EXPECTED_KEYS}
        res, elapsed = _call(slow, wait=0.1)
        self.assertLess(elapsed, 0.8)
        self.assertTrue(res["stale"])
        self.assertEqual(res["identity"], {"purpose": "old"})
        time.sleep(1.1)

    def test_fresh_cache_skips_recompute(self):
        calls = {"n": 0}
        def counting(_s):
            calls["n"] += 1
            return {k: None for k in EXPECTED_KEYS}
        _call(counting)
        _call(counting)
        _call(counting)
        self.assertEqual(calls["n"], 1)


class TestLineageCache(_ResetMixin, unittest.TestCase):
    def test_lineage_computed_once_within_ttl(self):
        calls = {"n": 0}
        def compute():
            calls["n"] += 1
            return {"active_variants": []}
        for _ in range(4):
            self.assertEqual(routes._cached_lineage(compute), {"active_variants": []})
        self.assertEqual(calls["n"], 1)

    def test_lineage_recomputed_after_ttl(self):
        calls = {"n": 0}
        def compute():
            calls["n"] += 1
            return {"n": calls["n"]}
        routes._cached_lineage(compute)
        routes._lineage_cache["at"] -= routes._LINEAGE_TTL_SECONDS + 1
        routes._cached_lineage(compute)
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
