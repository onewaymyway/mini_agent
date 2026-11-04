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


class TestSelfPortraitShape(unittest.TestCase):
    def test_collect_returns_expected_keys_without_agent(self):
        srv = types.SimpleNamespace(bridge=_Bridge())
        out = routes._collect_self_portrait(srv)
        self.assertEqual(set(out.keys()), EXPECTED_KEYS)


class TestSelfPortraitNonBlocking(unittest.TestCase):
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
        self.assertEqual(set(result.keys()), EXPECTED_KEYS)


if __name__ == "__main__":
    unittest.main()
