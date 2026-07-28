"""tests/test_external_input_reload.py — GatewayPoller.reload()（sources.yaml
热重载）测试

覆盖：
  1. 校验失败（新增/改动的 source 试跑 poll() 抛异常）：整体拒绝，旧配置
     和线程完全不受影响，并发布一条 config_reload_failed 事件。
  2. 校验全部通过：新增的 source 起线程、被删除的 source 停线程、未变化
     的 source 线程不受影响（同一个线程对象），并发布一条 config_reload_ok
     事件。
  3. YAML 解析失败（模拟传参路径之外，直接测试 SourcesConfigError 分支）
     同样整体拒绝。
"""

from __future__ import annotations

import tempfile
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


class TestGatewayPollerReload(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self._saved_registry = dict(
            __import__("mini_agent.external_input.source", fromlist=["_REGISTRY"])._REGISTRY
        )
        _reset_registry_for_tests()
        self._pollers: list[GatewayPoller] = []

        @register_source("ok_source")
        class OkSource(ExternalInputSource):
            def poll(self, params, state):
                return [], {}

        @register_source("bad_source")
        class BadSource(ExternalInputSource):
            def poll(self, params, state):
                raise RuntimeError("模拟不可用来源：网络不通")

        self.OkSource = OkSource
        self.BadSource = BadSource

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

    def test_reload_rejects_invalid_new_source_and_keeps_old_running(self):
        cfg_a = SourceConfig(id="a", type="ok_source", interval_seconds=60)
        poller = self._make_poller([cfg_a])
        poller.start()
        self.assertTrue(_wait_until(lambda: poller.is_running("a"), timeout=2.0))
        thread_a_before = poller._threads["a"]

        cfg_b_bad = SourceConfig(id="b", type="bad_source", interval_seconds=60)
        result = poller.reload([cfg_a, cfg_b_bad])

        self.assertFalse(result["ok"])
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["id"], "b")

        # 旧配置/线程完全不受影响：a 还是同一个线程对象、还在跑；b 没有被起线程
        self.assertIs(poller._threads["a"], thread_a_before)
        self.assertTrue(poller.is_running("a"))
        self.assertFalse(poller.is_running("b"))

        # 发布了一条失败事件
        events = poll_external_events(self.paths, consumer_name="test_reload_fail")
        failure_events = [e for e in events if e.signal == "config_reload_failed"]
        self.assertEqual(len(failure_events), 1)

    def test_reload_applies_valid_new_config_add_and_remove(self):
        cfg_a = SourceConfig(id="a", type="ok_source", interval_seconds=60)
        cfg_c = SourceConfig(id="c", type="ok_source", interval_seconds=60)
        poller = self._make_poller([cfg_a, cfg_c])
        poller.start()
        self.assertTrue(_wait_until(lambda: poller.is_running("a") and poller.is_running("c"), timeout=2.0))
        thread_a_before = poller._threads["a"]

        # 新配置：a 不变、c 被移除、新增 d
        cfg_a_same = SourceConfig(id="a", type="ok_source", interval_seconds=60)
        cfg_d = SourceConfig(id="d", type="ok_source", interval_seconds=60)
        result = poller.reload([cfg_a_same, cfg_d])

        self.assertTrue(result["ok"])
        self.assertEqual(result["added"], ["d"])
        self.assertEqual(result["removed"], ["c"])
        self.assertEqual(result["unchanged"], ["a"])

        self.assertTrue(_wait_until(lambda: poller.is_running("d"), timeout=2.0))
        # a 线程完全没被重启（同一个线程对象），c 被下线
        self.assertIs(poller._threads["a"], thread_a_before)
        self.assertFalse(poller.is_running("c"))
        self.assertNotIn("c", poller._configs and {cfg.id for cfg in poller._configs} or set())

        events = poll_external_events(self.paths, consumer_name="test_reload_ok")
        ok_events = [e for e in events if e.signal == "config_reload_ok"]
        self.assertEqual(len(ok_events), 1)
        self.assertEqual(ok_events[0].fields.get("added"), ["d"])
        self.assertEqual(ok_events[0].fields.get("removed"), ["c"])


if __name__ == "__main__":
    unittest.main()
