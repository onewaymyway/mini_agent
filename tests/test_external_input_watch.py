"""tests/test_external_input_watch.py — WatchInputSource（P4）测试

覆盖：
  1. rss：抓取 -> 找出新条目 -> 可选关键词前置过滤 -> seen_ids 跨轮询累积
  2. json_api：field_change 模式检测字段变化；threshold 模式命中即发一次、
     持续命中不重复发
  3. html_diff：内容摘要变化检测 + 可选关键词前置过滤
  4. 首次轮询不因为"没有历史值"而误报变化
  5. 未知 fetcher 抛错；get_by_path 对非法路径返回 None 而不是抛异常
  6. registry：import builtin.watch 后 "watch" 出现在 registered_source_types()

所有 HTTP 请求都用 monkeypatch 替换掉 requests.get，不发真实网络请求。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from mini_agent.external_input.builtin.watch import (
    RuleEngine,
    WatchFetchError,
    WatchInputSource,
    get_by_path,
)
from mini_agent.external_input.source import registered_source_types


def _fake_response(*, content: bytes = b"", json_data=None, text: str = "", status_ok=True):
    resp = SimpleNamespace()
    resp.content = content
    resp.text = text
    resp.status_code = 200 if status_ok else 500

    def _raise_for_status():
        if not status_ok:
            raise RuntimeError("boom")

    resp.raise_for_status = _raise_for_status
    if json_data is not None:
        resp.json = lambda: json_data
    return resp


RSS_XML = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><guid>a1</guid><title>Episode 1</title><link>http://x/a1</link></item>
  <item><guid>a2</guid><title>Special Episode 2</title><link>http://x/a2</link></item>
</channel></rss>
"""

RSS_XML_ONE_NEW = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><guid>a1</guid><title>Episode 1</title><link>http://x/a1</link></item>
  <item><guid>a2</guid><title>Special Episode 2</title><link>http://x/a2</link></item>
  <item><guid>a3</guid><title>Episode 3</title><link>http://x/a3</link></item>
</channel></rss>
"""


class RegistryTest(unittest.TestCase):
    def test_watch_registered(self):
        self.assertIn("watch", registered_source_types())


class RssSourceTest(unittest.TestCase):
    def setUp(self):
        self.source = WatchInputSource()

    def test_first_poll_returns_all_as_new_and_records_seen_ids(self):
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(content=RSS_XML),
        ):
            events, state = self.source.poll(
                {"fetcher": "rss", "url": "http://feed", "source_id": "s1"}, {}
            )
        self.assertEqual({e.id for e in events}, {"a1", "a2"})
        self.assertEqual(set(state["seen_ids"]), {"a1", "a2"})
        self.assertTrue(all(e.signal == "new_item" for e in events))
        self.assertTrue(all(e.source_type == "watch" for e in events))

    def test_second_poll_only_returns_new_item(self):
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(content=RSS_XML),
        ):
            _, state = self.source.poll(
                {"fetcher": "rss", "url": "http://feed", "source_id": "s1"}, {}
            )
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(content=RSS_XML_ONE_NEW),
        ):
            events, state2 = self.source.poll(
                {"fetcher": "rss", "url": "http://feed", "source_id": "s1"}, state
            )
        self.assertEqual([e.id for e in events], ["a3"])
        self.assertEqual(set(state2["seen_ids"]), {"a1", "a2", "a3"})

    def test_keyword_filter(self):
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(content=RSS_XML),
        ):
            events, _ = self.source.poll(
                {
                    "fetcher": "rss",
                    "url": "http://feed",
                    "source_id": "s1",
                    "keywords": ["special"],
                },
                {},
            )
        self.assertEqual([e.id for e in events], ["a2"])

    def test_max_seen_ids_caps_state_size(self):
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(content=RSS_XML),
        ):
            _, state = self.source.poll(
                {
                    "fetcher": "rss",
                    "url": "http://feed",
                    "source_id": "s1",
                    "max_seen_ids": 1,
                },
                {},
            )
        self.assertEqual(len(state["seen_ids"]), 1)


class JsonApiSourceTest(unittest.TestCase):
    def setUp(self):
        self.source = WatchInputSource()

    def test_field_change_no_event_on_first_poll(self):
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(json_data={"data": {"price": 100}}),
        ):
            events, state = self.source.poll(
                {"fetcher": "json_api", "url": "http://api", "field_path": "data.price"},
                {},
            )
        self.assertEqual(events, [])
        self.assertEqual(state["last_value"], 100)

    def test_field_change_detected_on_second_poll(self):
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(json_data={"data": {"price": 100}}),
        ):
            _, state = self.source.poll(
                {"fetcher": "json_api", "url": "http://api", "field_path": "data.price"},
                {},
            )
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(json_data={"data": {"price": 80}}),
        ):
            events, state2 = self.source.poll(
                {"fetcher": "json_api", "url": "http://api", "field_path": "data.price"},
                state,
            )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].signal, "field_changed")
        self.assertEqual(state2["last_value"], 80)

    def test_threshold_fires_once_then_suppressed_while_still_hit(self):
        params = {
            "fetcher": "json_api",
            "url": "http://api",
            "field_path": "data.price",
            "mode": "threshold",
            "op": "lt",
            "threshold": 90,
        }
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(json_data={"data": {"price": 80}}),
        ):
            events, state = self.source.poll(params, {})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].signal, "threshold")

        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(json_data={"data": {"price": 70}}),
        ):
            events2, state2 = self.source.poll(params, state)
        self.assertEqual(events2, [])  # 仍然命中阈值，但不重复发

        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(json_data={"data": {"price": 200}}),
        ):
            events3, state3 = self.source.poll(params, state2)
        self.assertEqual(events3, [])
        self.assertFalse(state3["threshold_hit"])

        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(json_data={"data": {"price": 50}}),
        ):
            events4, _ = self.source.poll(params, state3)
        self.assertEqual(len(events4), 1)  # 重新跌破阈值，再次触发


class HtmlDiffSourceTest(unittest.TestCase):
    def setUp(self):
        self.source = WatchInputSource()

    def test_no_event_on_first_poll(self):
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(text="<html><body>hello</body></html>"),
        ):
            events, state = self.source.poll(
                {"fetcher": "html_diff", "url": "http://page"}, {}
            )
        self.assertEqual(events, [])
        self.assertIn("digest", state)

    def test_change_detected(self):
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(text="<html><body>hello</body></html>"),
        ):
            _, state = self.source.poll({"fetcher": "html_diff", "url": "http://page"}, {})
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(text="<html><body>world</body></html>"),
        ):
            events, state2 = self.source.poll(
                {"fetcher": "html_diff", "url": "http://page"}, state
            )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].signal, "page_changed")
        self.assertNotEqual(state["digest"], state2["digest"])

    def test_keyword_filter_suppresses_non_matching_change(self):
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(text="<html><body>hello</body></html>"),
        ):
            _, state = self.source.poll({"fetcher": "html_diff", "url": "http://page"}, {})
        with mock.patch(
            "mini_agent.external_input.builtin.watch._http_get",
            return_value=_fake_response(text="<html><body>world</body></html>"),
        ):
            events, _ = self.source.poll(
                {"fetcher": "html_diff", "url": "http://page", "keywords": ["urgent"]},
                state,
            )
        self.assertEqual(events, [])


class MiscTest(unittest.TestCase):
    def test_unknown_fetcher_raises(self):
        with self.assertRaises(WatchFetchError):
            WatchInputSource().poll({"fetcher": "nope", "url": "x"}, {})

    def test_get_by_path_missing_returns_none(self):
        self.assertIsNone(get_by_path({"a": {"b": 1}}, "a.c"))
        self.assertIsNone(get_by_path({"a": [1, 2]}, "a.9"))
        self.assertIsNone(get_by_path(None, "a.b"))
        self.assertEqual(get_by_path({"a": [1, 2]}, "a.1"), 2)

    def test_rule_engine_keyword_hits_case_insensitive(self):
        hits = RuleEngine.keyword_hits("Big SALE today", ["sale"])
        self.assertEqual(hits, ["sale"])

    def test_rule_engine_threshold_invalid_op(self):
        self.assertFalse(RuleEngine.threshold_hit(10, "nope", 5))


if __name__ == "__main__":
    unittest.main()
