"""tests/test_external_input_poller.py — GatewayPoller（P2）测试

覆盖：
  1. 基本轮询：source.poll() 返回的事件被发布到 system_events
  2. state 在多轮之间正确传递并落盘持久化
  3. 未注册的 source type：线程记健康状态后直接退出，不无限重试
  4. 连续失败达到阈值后 circuit_open=True，并发布一条
     "external.<type>.source_unhealthy" 健康事件（tier=cron）
  5. 恢复成功后 consecutive_failures 清零、circuit_open=False
  6. enabled=false 的 source 不会被 start() 起线程
  7. stop() 能让所有线程退出
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from mini_agent.external_input.config import SourceConfig
from mini_agent.external_input.gateway import poll_external_events
from mini_agent.external_input.poller import GatewayPoller
from mini_agent.external_input.source import (
    ExternalInputEvent,
    ExternalInputSource,
    _reset_registry_for_tests,
    register_source,
)
from mini_agent.storage.paths import AgentPaths


def _wait_until(predicate, timeout=3.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestGatewayPoller(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self._saved_registry = dict(
            __import__("mini_agent.external_input.source", fromlist=["_REGISTRY"])._REGISTRY
        )
        _reset_registry_for_tests()
        self._pollers: list[GatewayPoller] = []

    def tearDown(self):
        for p in self._pollers:
            p.stop(timeout=2.0)
        _reset_registry_for_tests()
        mod = __import__("mini_agent.external_input.source", fromlist=["_REGISTRY"])
        mod._REGISTRY.update(self._saved_registry)
        self._tmpdir.cleanup()

    def _make_poller(self, configs, **kwargs) -> GatewayPoller:
        p = GatewayPoller(self.paths, configs=configs, **kwargs)
        self._pollers.append(p)
        return p

    def test_basic_polling_publishes_events(self):
        call_count = {"n": 0}

        @register_source("counting")
        class CountingSource(ExternalInputSource):
            def poll(self, params, state):
                call_count["n"] += 1
                evt = ExternalInputEvent(
                    id=f"e{call_count['n']}", source_id="s1", source_type="counting",
                    signal="tick", title="hello",
                )
                return [evt], {"calls": call_count["n"]}

        cfg = SourceConfig(id="s1", type="counting", interval_seconds=1, enabled=True)
        # 用极短的自定义 interval 加速测试：直接改 cfg
        cfg.interval_seconds = 1
        poller = self._make_poller([cfg])
        poller.start()

        self.assertTrue(_wait_until(lambda: call_count["n"] >= 1, timeout=2.0))
        poller.stop()

        events = poll_external_events(self.paths, consumer_name="test1")
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0].source_id, "s1")

    def test_state_persists_across_polls(self):
        seen_states = []

        @register_source("stateful")
        class StatefulSource(ExternalInputSource):
            def poll(self, params, state):
                seen_states.append(dict(state))
                n = state.get("n", 0) + 1
                return [], {"n": n}

        cfg = SourceConfig(id="s2", type="stateful", interval_seconds=0)
        cfg.interval_seconds = 1
        poller = self._make_poller([cfg])
        poller.start()

        self.assertTrue(_wait_until(lambda: len(seen_states) >= 2, timeout=3.0))
        poller.stop()

        # 第二次调用应该看到第一次返回的 state（n=1），证明 state 被正确传递
        self.assertGreaterEqual(seen_states[1].get("n", 0), 1)

        saved = (self.paths.external_input_state_dir / "s2.json").read_text(encoding="utf-8")
        self.assertIn('"n"', saved)

    def test_unregistered_source_type_records_health_and_exits(self):
        cfg = SourceConfig(id="s3", type="does_not_exist")
        poller = self._make_poller([cfg])
        poller.start()

        self.assertTrue(_wait_until(lambda: poller.get_health("s3") is not None))
        # 线程应该很快退出（不是活的轮询线程），因为类型查找失败是致命的配置错误
        self.assertTrue(_wait_until(lambda: not poller.is_running("s3"), timeout=2.0))
        health = poller.get_health("s3")
        self.assertTrue(health["circuit_open"])

    def test_circuit_breaker_trips_after_threshold_and_recovers(self):
        fail_until = {"n": 0}

        @register_source("flaky")
        class FlakySource(ExternalInputSource):
            def poll(self, params, state):
                fail_until["n"] += 1
                if fail_until["n"] <= 3:
                    raise RuntimeError(f"boom {fail_until['n']}")
                return [], state

        cfg = SourceConfig(id="s4", type="flaky", interval_seconds=1)
        # 用极小的初始 backoff：monkeypatch 不必要，interval_seconds=1 已经
        # 足够小；threshold=3 让熔断在合理时间内触发。
        poller = self._make_poller([cfg], failure_threshold=3, max_backoff_seconds=1)
        poller.start()

        self.assertTrue(_wait_until(
            lambda: (poller.get_health("s4") or {}).get("circuit_open"), timeout=5.0
        ))
        health = poller.get_health("s4")
        self.assertEqual(health["consecutive_failures"], 3)

        # 应该发布了一条 source_unhealthy 事件（用 _wait_until 而不是一次性
        # 断言：health.circuit_open=True 和事件真正落盘之间隔着一次函数调用，
        # 在线程调度繁忙时二者不是严格同时可见的）。
        unhealthy = []

        def _check_unhealthy():
            nonlocal unhealthy
            events = poll_external_events(self.paths, consumer_name="test_cb", advance_cursor=False)
            unhealthy = [e for e in events if e.signal == "source_unhealthy"]
            return len(unhealthy) >= 1

        self.assertTrue(_wait_until(_check_unhealthy, timeout=5.0))
        self.assertEqual(len(unhealthy), 1)
        self.assertEqual(unhealthy[0].source_id, "s4")

        # 之后 poll() 恢复成功，consecutive_failures 应该清零
        self.assertTrue(_wait_until(
            lambda: not (poller.get_health("s4") or {}).get("circuit_open", True),
            timeout=5.0,
        ))
        health2 = poller.get_health("s4")
        self.assertEqual(health2["consecutive_failures"], 0)
        poller.stop()

    def test_disabled_source_not_started(self):
        @register_source("noop")
        class NoopSource(ExternalInputSource):
            def poll(self, params, state):
                return [], state

        cfg = SourceConfig(id="s5", type="noop", enabled=False)
        poller = self._make_poller([cfg])
        poller.start()
        time.sleep(0.1)
        self.assertFalse(poller.is_running("s5"))

    def test_stop_terminates_threads(self):
        @register_source("simple")
        class SimpleSource(ExternalInputSource):
            def poll(self, params, state):
                return [], state

        cfg = SourceConfig(id="s6", type="simple", interval_seconds=1)
        poller = self._make_poller([cfg])
        poller.start()
        self.assertTrue(_wait_until(lambda: poller.is_running("s6"), timeout=1.0))
        poller.stop(timeout=2.0)
        self.assertFalse(poller.is_running("s6"))


if __name__ == "__main__":
    unittest.main()
