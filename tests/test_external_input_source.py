"""tests/test_external_input_source.py — External Input Gateway P1 测试

覆盖：
  1. ExternalInputEvent 非法 suggested_tier 拒绝构造
  2. event_type() / to_payload() / from_payload() 往返
  3. register_source / get_source_class registry 基本行为
  4. get_source_class 查找未注册类型时报错信息包含已注册列表
  5. gateway.publish_event 把 ExternalInputEvent 写进 system_events，
     event_type 命名符合 "external.<source_type>.<signal>"
  6. gateway.publish_event 的兜底去重（同一 event.id 重复调用只写一次）
  7. gateway.poll_external_events 只返回 external.* 事件，且能正确还原
     成 ExternalInputEvent 对象
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.external_input import gateway
from mini_agent.external_input.source import (
    ExternalInputEvent,
    ExternalInputSource,
    _reset_registry_for_tests,
    get_source_class,
    register_source,
    registered_source_types,
)
from mini_agent.perception import system_events as se
from mini_agent.storage.paths import AgentPaths


class TestExternalInputEvent(unittest.TestCase):
    def test_invalid_suggested_tier_rejected(self):
        with self.assertRaises(ValueError):
            ExternalInputEvent(
                id="1", source_id="s1", source_type="watch", signal="x",
                title="t", suggested_tier="not_a_tier",
            )

    def test_event_type_naming(self):
        evt = ExternalInputEvent(
            id="1", source_id="s1", source_type="watch", signal="new_episode",
            title="t",
        )
        self.assertEqual(evt.event_type(), "external.watch.new_episode")

    def test_payload_roundtrip(self):
        evt = ExternalInputEvent(
            id="abc", source_id="my_watch", source_type="watch", signal="price_drop",
            title="降价了", detail="从100降到80", url="https://example.com",
            fields={"priority": "high"}, suggested_tier="instant",
        )
        restored = ExternalInputEvent.from_payload(evt.to_payload())
        self.assertEqual(restored.id, evt.id)
        self.assertEqual(restored.source_id, evt.source_id)
        self.assertEqual(restored.source_type, evt.source_type)
        self.assertEqual(restored.signal, evt.signal)
        self.assertEqual(restored.title, evt.title)
        self.assertEqual(restored.detail, evt.detail)
        self.assertEqual(restored.url, evt.url)
        self.assertEqual(restored.fields, evt.fields)


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self._saved = dict(__import__(
            "mini_agent.external_input.source", fromlist=["_REGISTRY"]
        )._REGISTRY)
        _reset_registry_for_tests()

    def tearDown(self):
        _reset_registry_for_tests()
        mod = __import__("mini_agent.external_input.source", fromlist=["_REGISTRY"])
        mod._REGISTRY.update(self._saved)

    def test_register_and_get(self):
        @register_source("dummy")
        class DummySource(ExternalInputSource):
            def poll(self, params, state):
                return [], state

        self.assertIs(get_source_class("dummy"), DummySource)
        self.assertIn("dummy", registered_source_types())

    def test_get_unregistered_raises_with_helpful_message(self):
        @register_source("known")
        class KnownSource(ExternalInputSource):
            def poll(self, params, state):
                return [], state

        with self.assertRaises(KeyError) as ctx:
            get_source_class("unknown_type")
        self.assertIn("known", str(ctx.exception))

    def test_register_rejects_non_subclass(self):
        with self.assertRaises(TypeError):
            register_source("bad")(object)


class TestGatewayPublish(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        # 每个测试用例独立的去重缓存，避免跨测试污染
        gateway._dedup_cache = gateway._RecentIdCache()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_event(self, event_id="e1", signal="new_item", tier="tick"):
        return ExternalInputEvent(
            id=event_id, source_id="my_watch", source_type="watch", signal=signal,
            title="标题", suggested_tier=tier,
        )

    def test_publish_event_writes_to_system_events(self):
        ok = gateway.publish_event(self.paths, self._make_event())
        self.assertTrue(ok)

        raw = se.poll_since(self.paths, consumer_name="c1")
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0].event_type, "external.watch.new_item")
        self.assertEqual(raw[0].tier, "tick")
        self.assertEqual(raw[0].source, "external:my_watch")
        self.assertEqual(raw[0].payload["id"], "e1")

    def test_publish_event_dedup_same_id(self):
        evt = self._make_event(event_id="dup1")
        first = gateway.publish_event(self.paths, evt)
        second = gateway.publish_event(self.paths, evt)
        self.assertTrue(first)
        self.assertFalse(second, "同一 event.id 重复发布应被兜底去重拦截")

        raw = se.poll_since(self.paths, consumer_name="c1")
        self.assertEqual(len(raw), 1)

    def test_publish_events_batch_returns_count(self):
        events = [self._make_event(event_id=f"e{i}") for i in range(3)]
        count = gateway.publish_events(self.paths, events)
        self.assertEqual(count, 3)

    def test_poll_external_events_filters_non_external(self):
        gateway.publish_event(self.paths, self._make_event(event_id="ext1"))
        se.publish(self.paths, source="other", event_type="proprioception.x", tier="tick")

        result = gateway.poll_external_events(self.paths, consumer_name="policy")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], ExternalInputEvent)
        self.assertEqual(result[0].id, "ext1")

    def test_poll_external_events_event_types_filter(self):
        gateway.publish_event(self.paths, self._make_event(event_id="a", signal="new_item"))
        gateway.publish_event(self.paths, self._make_event(event_id="b", signal="price_drop"))

        result = gateway.poll_external_events(
            self.paths, consumer_name="policy2",
            event_types=["external.watch.price_drop"],
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].signal, "price_drop")

    def test_poll_external_events_advances_cursor(self):
        gateway.publish_event(self.paths, self._make_event(event_id="a"))
        first = gateway.poll_external_events(self.paths, consumer_name="policy3")
        second = gateway.poll_external_events(self.paths, consumer_name="policy3")
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)


if __name__ == "__main__":
    unittest.main()
