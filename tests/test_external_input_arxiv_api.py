"""tests/test_external_input_arxiv_api.py — ArxivApiInputSource（外部数据知识化计划 P5）测试

覆盖：
  1. 抓取 Atom 响应 -> 解析出结构化 title/summary/authors/published
  2. 首次轮询：所有条目都产生事件（无历史 seen_ids）
  3. 第二次轮询只对新增条目产生事件，已见过的不重复
  4. keywords 前置过滤：命中的产生事件，未命中的仍计入 seen_ids 但不产生事件
  5. 缺少 category 参数时抛错
  6. seen_ids 超过上限时只保留最近若干条
  7. registry：import builtin.arxiv_api 后 "arxiv_api" 出现在 registered_source_types()
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from mini_agent.external_input.builtin.arxiv_api import (
    ArxivApiInputSource,
    ArxivFetchError,
    fetch_arxiv_entries,
)
from mini_agent.external_input.source import registered_source_types


def _fake_response(content: bytes, status_ok: bool = True):
    resp = SimpleNamespace()
    resp.content = content

    def _raise_for_status():
        if not status_ok:
            raise RuntimeError("boom")

    resp.raise_for_status = _raise_for_status
    return resp


ATOM_XML_TWO_ENTRIES = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>  A cool paper about agents  </title>
    <summary>This paper studies agentic reasoning.</summary>
    <published>2024-01-01T00:00:00Z</published>
    <author><name>Alice</name></author>
    <author><name>Bob</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00002v1</id>
    <title>Something about unrelated topic</title>
    <summary>Not about agents.</summary>
    <published>2024-01-02T00:00:00Z</published>
    <author><name>Carol</name></author>
  </entry>
</feed>
"""

ATOM_XML_THREE_ENTRIES = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>A cool paper about agents</title>
    <summary>This paper studies agentic reasoning.</summary>
    <published>2024-01-01T00:00:00Z</published>
    <author><name>Alice</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00002v1</id>
    <title>Something about unrelated topic</title>
    <summary>Not about agents.</summary>
    <published>2024-01-02T00:00:00Z</published>
    <author><name>Carol</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00003v1</id>
    <title>A new agent framework release</title>
    <summary>Introduces a new framework.</summary>
    <published>2024-01-03T00:00:00Z</published>
    <author><name>Dave</name></author>
  </entry>
</feed>
"""


class TestFetchArxivEntries(unittest.TestCase):
    def test_parses_entries_correctly(self):
        with mock.patch("requests.get", return_value=_fake_response(ATOM_XML_TWO_ENTRIES)):
            entries = fetch_arxiv_entries("cs.AI")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["title"], "A cool paper about agents")
        self.assertEqual(entries[0]["authors"], ["Alice", "Bob"])
        self.assertEqual(entries[0]["id"], "http://arxiv.org/abs/2401.00001v1")

    def test_http_failure_raises_arxiv_fetch_error(self):
        with mock.patch("requests.get", side_effect=RuntimeError("network down")):
            with self.assertRaises(ArxivFetchError):
                fetch_arxiv_entries("cs.AI")

    def test_malformed_xml_raises_arxiv_fetch_error(self):
        with mock.patch("requests.get", return_value=_fake_response(b"not xml")):
            with self.assertRaises(ArxivFetchError):
                fetch_arxiv_entries("cs.AI")


class TestArxivApiInputSourcePoll(unittest.TestCase):
    def test_missing_category_raises(self):
        source = ArxivApiInputSource()
        with self.assertRaises(ArxivFetchError):
            source.poll({}, {})

    def test_first_poll_produces_events_for_all_entries_without_keywords(self):
        source = ArxivApiInputSource()
        with mock.patch("requests.get", return_value=_fake_response(ATOM_XML_TWO_ENTRIES)):
            events, new_state = source.poll({"category": "cs.AI"}, {})
        self.assertEqual(len(events), 2)
        self.assertEqual(set(new_state["seen_ids"]), {
            "http://arxiv.org/abs/2401.00001v1",
            "http://arxiv.org/abs/2401.00002v1",
        })

    def test_second_poll_only_new_entries(self):
        source = ArxivApiInputSource()
        with mock.patch("requests.get", return_value=_fake_response(ATOM_XML_TWO_ENTRIES)):
            _, state = source.poll({"category": "cs.AI"}, {})
        with mock.patch("requests.get", return_value=_fake_response(ATOM_XML_THREE_ENTRIES)):
            events, new_state = source.poll({"category": "cs.AI"}, state)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["id"] if isinstance(events[0], dict) else events[0].id,
                          "http://arxiv.org/abs/2401.00003v1")
        self.assertIn("http://arxiv.org/abs/2401.00003v1", new_state["seen_ids"])

    def test_keyword_filter_only_matching_titles_produce_events(self):
        source = ArxivApiInputSource()
        with mock.patch("requests.get", return_value=_fake_response(ATOM_XML_TWO_ENTRIES)):
            events, new_state = source.poll(
                {"category": "cs.AI", "keywords": ["agent"]}, {},
            )
        # 只有标题含 "agent" 的那条产生事件
        self.assertEqual(len(events), 1)
        self.assertIn("agent", events[0].title.lower())
        # 但两条都计入 seen_ids（去重游标跟"是否命中关键词"无关）
        self.assertEqual(len(new_state["seen_ids"]), 2)

    def test_seen_ids_truncated_to_max(self):
        source = ArxivApiInputSource()
        state = {"seen_ids": [f"http://arxiv.org/abs/old-{i}" for i in range(600)]}
        with mock.patch("requests.get", return_value=_fake_response(ATOM_XML_TWO_ENTRIES)):
            _, new_state = source.poll({"category": "cs.AI"}, state)
        self.assertLessEqual(len(new_state["seen_ids"]), 500)

    def test_registered_in_source_registry(self):
        import mini_agent.external_input.builtin.arxiv_api  # noqa: F401
        self.assertIn("arxiv_api", registered_source_types())


if __name__ == "__main__":
    unittest.main()
