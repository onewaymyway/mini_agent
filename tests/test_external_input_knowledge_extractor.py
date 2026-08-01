"""tests/test_external_input_knowledge_extractor.py — P1 外部事件 → wiki
抽取管道测试。

覆盖：
  1. 无 llm_helper 时不产生任何调用、不消费游标
  2. 只处理 channel == agent_watch 的事件，其它 channel（如 weather）被过滤
  3. LLM 返回的 entities/facts 正确落进 world_writer 的 pending 队列，
     且 source_kind == "external_watch"
  4. 单条解析失败不阻塞其余事件
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.external_input.gateway import publish_event
from mini_agent.external_input.knowledge_extractor import (
    run_external_knowledge_extraction_once,
)
from mini_agent.external_input.source import ExternalInputEvent
from mini_agent.storage.paths import AgentPaths


class _FakeLLMHelper:
    def __init__(self, response: str):
        self._response = response
        self.calls = 0

    def ask(self, prompt: str) -> str:
        self.calls += 1
        return self._response


class TestKnowledgeExtractor(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _publish(self, id_, title, channel="agent_watch", detail="detail"):
        evt = ExternalInputEvent(
            id=id_, source_id="arxiv_cs_ai", source_type="watch",
            signal="new_item", title=title, detail=detail, channel=channel,
        )
        publish_event(self.paths, evt)

    def test_no_llm_helper_skips_and_does_not_advance_cursor(self):
        self._publish("noop-e1", "Some paper")
        summary = run_external_knowledge_extraction_once(self.paths, llm_helper=None)
        self.assertEqual(summary.scanned_events, 0)
        self.assertEqual(summary.llm_batches, 0)
        pending = self.paths.world_candidates_pending_path
        self.assertFalse(pending.exists())

    def test_only_agent_watch_channel_is_processed(self):
        self._publish("chan-e1", "Weather today", channel="weather")
        self._publish("chan-e2", "New arxiv paper on agents", channel="agent_watch")
        response = json.dumps({
            "items": [
                {"index": 1, "entities": [
                    {"name": "AgentFoo", "entity_type": "concept",
                     "description": "一个新的 agent 架构"},
                ], "facts": []},
            ]
        })
        helper = _FakeLLMHelper(response)
        summary = run_external_knowledge_extraction_once(self.paths, llm_helper=helper)
        self.assertEqual(summary.scanned_events, 1)
        self.assertEqual(helper.calls, 1)

    def test_entities_and_facts_queued_with_external_watch_source_kind(self):
        self._publish("queue-e1", "New arxiv paper on agents")
        response = json.dumps({
            "items": [
                {
                    "index": 1,
                    "entities": [
                        {"name": "AgentFoo", "entity_type": "concept",
                         "description": "一个新的 agent 架构"},
                    ],
                    "facts": [
                        {"statement": "AgentFoo 发布了 v2 版本", "confidence": "inferred"},
                    ],
                },
            ]
        })
        helper = _FakeLLMHelper(response)
        summary = run_external_knowledge_extraction_once(self.paths, llm_helper=helper)
        self.assertEqual(summary.entities_queued, 1)
        self.assertEqual(summary.facts_queued, 1)

        pending = self.paths.world_candidates_pending_path
        rows = [json.loads(line) for line in pending.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["source_kind"], "external_watch")

    def test_parse_failure_for_one_event_does_not_block_others(self):
        self._publish("fail-e1", "First paper")
        self._publish("fail-e2", "Second paper")
        # 只返回 index=2 的结果，index=1 视为解析失败
        response = json.dumps({
            "items": [
                {"index": 2, "entities": [
                    {"name": "Bar", "entity_type": "concept", "description": "desc"},
                ], "facts": []},
            ]
        })
        helper = _FakeLLMHelper(response)
        summary = run_external_knowledge_extraction_once(self.paths, llm_helper=helper)
        self.assertEqual(summary.parse_failed_count, 1)
        self.assertEqual(summary.entities_queued, 1)

    def _write_topic_page(self, page_id: str, label: str = "AI Agent 架构动态"):
        from mini_agent.wiki.writer import write_page
        write_page(
            self.paths,
            page_id=page_id,
            page_type="topic",
            body="## 概述\n\n关于 AI agent 架构动态的专题页。\n",
            tags=["ai-agent"],
            confidence=0.5,
            extra_frontmatter={"topic_label": label, "source_tag": "ai-agent"},
        )

    def test_topic_digest_injected_and_topic_id_hit_appends_to_topic_page(self):
        self._write_topic_page("topic-ai-agent")
        self._publish("topic-e1", "New agent framework released")
        response = json.dumps({
            "items": [
                {"index": 1, "topic_id": "topic-ai-agent", "entities": [], "facts": []},
            ]
        })
        helper = _FakeLLMHelper(response)
        summary = run_external_knowledge_extraction_once(self.paths, llm_helper=helper)

        # 命中专题页时不应该再产生独立的 entity/fact 候选
        self.assertEqual(summary.topic_appended, 1)
        self.assertEqual(summary.entities_queued, 0)
        self.assertEqual(summary.facts_queued, 0)
        pending = self.paths.world_candidates_pending_path
        self.assertFalse(pending.exists())

        topic_path = self.paths.wiki_type_dir("topic") / "topic-ai-agent.md"
        body = topic_path.read_text(encoding="utf-8")
        self.assertIn("外部资讯", body)
        self.assertIn("New agent framework released", body)

    def test_unmatched_topic_id_falls_back_to_entity_queue(self):
        self._write_topic_page("topic-ai-agent-2")
        self._publish("topic-e2", "Some unrelated paper")
        # 模型给了一个不存在的 topic_id（幻觉/专题页已被删除），应忽略并
        # 退回 entity 兜底逻辑，而不是报错中断。
        response = json.dumps({
            "items": [
                {"index": 1, "topic_id": "topic-does-not-exist", "entities": [
                    {"name": "Baz", "entity_type": "concept", "description": "desc"},
                ], "facts": []},
            ]
        })
        helper = _FakeLLMHelper(response)
        summary = run_external_knowledge_extraction_once(self.paths, llm_helper=helper)
        self.assertEqual(summary.topic_appended, 0)
        self.assertEqual(summary.entities_queued, 1)


if __name__ == "__main__":
    unittest.main()
