"""tests/test_external_input_channel_p7.py — External Input Gateway P7 测试

覆盖：
  1. SourceConfig.channel 缺省回退成 type，显式配置时按配置值
  2. GatewayPoller 轮询时用 cfg.channel 回填 event.channel（来源没设置时）
  3. PolicyRule.matches 支持 "channel" 匹配维度
  4. group_events_by_channel 分组正确、保序，未设置 channel 归入 "default"
  5. run_ingestion_policy_once 的 PolicyRunSummary.by_channel 统计正确
  6. WeatherInputSource：降雨/极端气温阈值边沿触发、不重复告警、
     daily_forecast 一天只发一次
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_agent.external_input.config import SourceConfig
from mini_agent.external_input.policy import (
    PolicyRule,
    group_events_by_channel,
    run_ingestion_policy_once,
)
from mini_agent.external_input.poller import GatewayPoller
from mini_agent.external_input.source import (
    ExternalInputEvent,
    ExternalInputSource,
    _reset_registry_for_tests,
    register_source,
)
from mini_agent.storage.paths import AgentPaths


class TestSourceConfigChannel(unittest.TestCase):
    def test_channel_defaults_to_type(self):
        cfg = SourceConfig.from_dict({"id": "s1", "type": "watch"})
        self.assertEqual(cfg.channel, "watch")

    def test_channel_explicit_value_kept(self):
        cfg = SourceConfig.from_dict({"id": "s1", "type": "watch", "channel": "news"})
        self.assertEqual(cfg.channel, "news")


class _DummySource(ExternalInputSource):
    source_type = "_p7_dummy"

    def poll(self, params, state):
        return (
            [
                ExternalInputEvent(
                    id="p7_dummy_evt1", source_id="p7_dummy_src", source_type="_p7_dummy",
                    signal="ping", title="t",
                )
            ],
            {"polled": True},
        )


class TestPollerChannelStamping(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        # 其它测试模块（如 test_external_input_source.py）的用例可能调用
        # _reset_registry_for_tests() 清空全局 registry；本测试不依赖
        # import 时的模块级注册是否还在，每次 setUp 都重新注册一次，
        # 避免与其它测试文件按什么顺序跑产生耦合。
        register_source("_p7_dummy")(_DummySource)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_poller_stamps_channel_from_config(self):
        import threading

        class _StopAfterOnePoll(threading.Event):
            """第一次 is_set() 返回 False（进入循环体跑一次 poll），
            wait() 被调用（循环体跑完一轮后的 sleep 点）时才真正置位，
            让 _run_source_loop 只跑一轮就退出。"""

            def wait(self, timeout=None):
                self.set()
                return True

        cfg = SourceConfig(id="s1", type="_p7_dummy", channel="alerts")
        poller = GatewayPoller(self.paths, configs=[cfg])
        stop_event = _StopAfterOnePoll()
        poller._run_source_loop(cfg, stop_event)

        from mini_agent.external_input.gateway import poll_external_events

        events = poll_external_events(self.paths, consumer_name="test_channel_stamp")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].channel, "alerts")


class TestPolicyRuleChannelMatch(unittest.TestCase):
    def test_matches_channel(self):
        rule = PolicyRule(match={"channel": "weather"}, action="notify_only")
        evt_match = ExternalInputEvent(
            id="1", source_id="s", source_type="weather", signal="rain_alert",
            title="t", channel="weather",
        )
        evt_other = ExternalInputEvent(
            id="2", source_id="s", source_type="weather", signal="rain_alert",
            title="t", channel="other",
        )
        self.assertTrue(rule.matches(evt_match))
        self.assertFalse(rule.matches(evt_other))


class TestGroupEventsByChannel(unittest.TestCase):
    def test_groups_and_preserves_order(self):
        events = [
            ExternalInputEvent(id="1", source_id="s", source_type="t", signal="x", title="a", channel="weather"),
            ExternalInputEvent(id="2", source_id="s", source_type="t", signal="x", title="b", channel="news"),
            ExternalInputEvent(id="3", source_id="s", source_type="t", signal="x", title="c", channel="weather"),
            ExternalInputEvent(id="4", source_id="s", source_type="t", signal="x", title="d", channel=""),
        ]
        grouped = group_events_by_channel(events)
        self.assertEqual([e.id for e in grouped["weather"]], ["1", "3"])
        self.assertEqual([e.id for e in grouped["news"]], ["2"])
        self.assertEqual([e.id for e in grouped["default"]], ["4"])


class TestRunIngestionPolicyByChannel(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_summary_by_channel(self):
        from mini_agent.external_input.gateway import publish_events

        events = [
            ExternalInputEvent(id="1", source_id="s", source_type="weather", signal="rain_alert", title="a", channel="weather"),
            ExternalInputEvent(id="2", source_id="s", source_type="watch", signal="new_item", title="b", channel="news"),
            ExternalInputEvent(id="3", source_id="s", source_type="weather", signal="rain_alert", title="c", channel="weather"),
        ]
        publish_events(self.paths, events)
        summary = run_ingestion_policy_once(self.paths, consumer_name="test_by_channel")
        self.assertEqual(summary.processed, 3)
        self.assertEqual(summary.by_channel, {"weather": 2, "news": 1})


class TestWeatherInputSource(unittest.TestCase):
    # 不在这里 _reset_registry_for_tests()：其它测试文件（比如
    # test_external_input_source.py）的用例也会 reset 全局 registry，
    # 而 Python import 是幂等的——已经 import 过的模块再次 import 不会
    # 重新触发 @register_source 装饰器。本类测试的是 WeatherInputSource
    # 这个类本身的行为（直接实例化调用），不依赖它是否注册在全局
    # registry 里，所以不需要 reset/重新 import。

    def _fake_response(self, temps, rain_probs):
        import time as _time
        hour_str = _time.strftime("%Y-%m-%dT%H:00")
        return {
            "hourly": {
                "time": [hour_str] * len(temps),
                "temperature_2m": temps,
                "precipitation_probability": rain_probs,
            }
        }

    def test_rain_alert_edge_triggered_once(self):
        from mini_agent.external_input.builtin.weather import WeatherInputSource

        src = WeatherInputSource()
        params = {"latitude": 1.0, "longitude": 2.0, "rain_probability_threshold": 50}

        with patch(
            "mini_agent.external_input.builtin.weather._fetch_hourly_forecast",
            return_value=self._fake_response([20, 21], [70, 80]),
        ):
            events, state = src.poll(params, {})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].signal, "rain_alert")
        self.assertTrue(state["rain_hit"])

        # 第二次仍然命中阈值：不应重复告警
        with patch(
            "mini_agent.external_input.builtin.weather._fetch_hourly_forecast",
            return_value=self._fake_response([20, 21], [70, 80]),
        ):
            events2, state2 = src.poll(params, state)
        self.assertEqual(events2, [])

        # 概率回落之后再次超过阈值：应该再触发一次
        with patch(
            "mini_agent.external_input.builtin.weather._fetch_hourly_forecast",
            return_value=self._fake_response([20, 21], [10, 20]),
        ):
            events3, state3 = src.poll(params, state2)
        self.assertEqual(events3, [])
        self.assertFalse(state3["rain_hit"])

        with patch(
            "mini_agent.external_input.builtin.weather._fetch_hourly_forecast",
            return_value=self._fake_response([20, 21], [70, 80]),
        ):
            events4, _ = src.poll(params, state3)
        self.assertEqual(len(events4), 1)
        self.assertEqual(events4[0].signal, "rain_alert")

    def test_daily_summary_once_per_day(self):
        from mini_agent.external_input.builtin.weather import WeatherInputSource

        src = WeatherInputSource()
        params = {"latitude": 1.0, "longitude": 2.0, "daily_summary": True}

        with patch(
            "mini_agent.external_input.builtin.weather._fetch_hourly_forecast",
            return_value=self._fake_response([10, 20], [5, 5]),
        ):
            events, state = src.poll(params, {})
        signals = [e.signal for e in events]
        self.assertIn("daily_forecast", signals)

        with patch(
            "mini_agent.external_input.builtin.weather._fetch_hourly_forecast",
            return_value=self._fake_response([10, 20], [5, 5]),
        ):
            events2, _ = src.poll(params, state)
        self.assertNotIn("daily_forecast", [e.signal for e in events2])

    def test_missing_lat_lng_raises(self):
        from mini_agent.external_input.builtin.weather import (
            WeatherFetchError,
            WeatherInputSource,
        )

        src = WeatherInputSource()
        with self.assertRaises(WeatherFetchError):
            src.poll({}, {})


if __name__ == "__main__":
    unittest.main()
