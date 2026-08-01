"""tests/test_external_trend_capability_link.py — P4 外部知识接入自我改进候选生成测试。

覆盖：
  1. 无 llm_helper 时不产生任何调用
  2. 外部知识页面或薄弱能力任一为空时直接跳过（不调用 LLM）
  3. LLM 匹配结果里 capability_domain/wiki_page_ids 必须真实存在于输入，
     否则该条候选被过滤掉
  4. 候选正确落盘进状态文件与人类可读草稿 md
  5. 同一 (capability_domain, wiki_page_ids) 组合在 14 天去重窗口内不重复产出
  6. `load_external_trend_candidates()` 只返回未过期候选
  7. `soft_goal_deriver.SoftGoalDeriver._from_external_knowledge()` 正确转换候选
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mini_agent.evolution.external_trend_capability_link import (
    TrendCapabilityCandidate,
    run_external_trend_capability_link_once,
    load_external_trend_candidates,
    STALE_CANDIDATE_TTL_SECONDS,
)
from mini_agent.storage.paths import AgentPaths


class _FakeLLMHelper:
    def __init__(self, response: str):
        self._response = response
        self.calls = 0

    def ask(self, prompt: str) -> str:
        self.calls += 1
        return self._response


class _FakePage:
    def __init__(self, id_, body=""):
        self.id = id_
        self.body = body


class _FakeCapability:
    def __init__(self, domain, confidence=0.1, total_calls=1):
        self.capability_name = domain
        self.confidence = confidence
        self.total_calls = total_calls


def _make_paths() -> AgentPaths:
    tmp = tempfile.mkdtemp()
    return AgentPaths(project_root=Path(tmp))


class TestExternalTrendCapabilityLink(unittest.TestCase):
    def test_no_llm_helper_skips(self):
        paths = _make_paths()
        summary = run_external_trend_capability_link_once(paths, llm_helper=None)
        self.assertEqual(summary.candidates_produced, 0)
        self.assertFalse(summary.llm_called)

    def test_empty_pages_or_capabilities_skips_llm_call(self):
        paths = _make_paths()
        helper = _FakeLLMHelper("[]")
        with mock.patch(
            "mini_agent.evolution.external_trend_capability_link._load_external_knowledge_pages",
            return_value=[],
        ), mock.patch(
            "mini_agent.evolution.external_trend_capability_link._load_weak_capabilities",
            return_value=[_FakeCapability("python_refactor")],
        ):
            summary = run_external_trend_capability_link_once(paths, llm_helper=helper)
        self.assertEqual(helper.calls, 0)
        self.assertFalse(summary.llm_called)

    def test_invalid_llm_reference_is_filtered(self):
        paths = _make_paths()
        pages = [_FakePage("topics/ai-agent-arch.md")]
        caps = [_FakeCapability("python_refactor")]
        # LLM 引用了不存在的 page id / capability domain，应该被过滤掉
        response = json.dumps([
            {
                "capability_domain": "made_up_domain",
                "wiki_page_ids": ["topics/ai-agent-arch.md"],
                "rationale": "not real domain",
            },
            {
                "capability_domain": "python_refactor",
                "wiki_page_ids": ["does/not/exist.md"],
                "rationale": "not real page",
            },
        ])
        helper = _FakeLLMHelper(response)
        with mock.patch(
            "mini_agent.evolution.external_trend_capability_link._load_external_knowledge_pages",
            return_value=pages,
        ), mock.patch(
            "mini_agent.evolution.external_trend_capability_link._load_weak_capabilities",
            return_value=caps,
        ):
            summary = run_external_trend_capability_link_once(paths, llm_helper=helper)
        self.assertTrue(summary.llm_called)
        self.assertEqual(summary.candidates_produced, 0)

    def test_valid_candidate_persists_and_writes_markdown(self):
        paths = _make_paths()
        pages = [_FakePage("topics/ai-agent-arch.md", body="some new agent framework release")]
        caps = [_FakeCapability("python_refactor")]
        response = json.dumps([
            {
                "capability_domain": "python_refactor",
                "wiki_page_ids": ["topics/ai-agent-arch.md"],
                "rationale": "the new framework has refactor tooling ideas",
            },
        ])
        helper = _FakeLLMHelper(response)
        with mock.patch(
            "mini_agent.evolution.external_trend_capability_link._load_external_knowledge_pages",
            return_value=pages,
        ), mock.patch(
            "mini_agent.evolution.external_trend_capability_link._load_weak_capabilities",
            return_value=caps,
        ):
            summary = run_external_trend_capability_link_once(paths, llm_helper=helper)

        self.assertEqual(summary.candidates_produced, 1)
        self.assertTrue(paths.external_trend_capability_link_state_path.exists())
        self.assertTrue(paths.external_trend_capability_candidates_path.exists())

        md_text = paths.external_trend_capability_candidates_path.read_text(encoding="utf-8")
        self.assertIn("python_refactor", md_text)
        self.assertIn("topics/ai-agent-arch.md", md_text)

        candidates = load_external_trend_candidates(paths)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].capability_domain, "python_refactor")

    def test_duplicate_combination_within_ttl_is_skipped(self):
        paths = _make_paths()
        pages = [_FakePage("topics/x.md")]
        caps = [_FakeCapability("bash_scripting")]
        response = json.dumps([
            {
                "capability_domain": "bash_scripting",
                "wiki_page_ids": ["topics/x.md"],
                "rationale": "r",
            },
        ])
        helper = _FakeLLMHelper(response)
        with mock.patch(
            "mini_agent.evolution.external_trend_capability_link._load_external_knowledge_pages",
            return_value=pages,
        ), mock.patch(
            "mini_agent.evolution.external_trend_capability_link._load_weak_capabilities",
            return_value=caps,
        ):
            first = run_external_trend_capability_link_once(paths, llm_helper=helper)
            second = run_external_trend_capability_link_once(paths, llm_helper=helper)

        self.assertEqual(first.candidates_produced, 1)
        self.assertEqual(second.candidates_produced, 0)
        self.assertEqual(second.candidates_skipped_duplicate, 1)

    def test_expired_candidate_not_returned(self):
        paths = _make_paths()
        state_path = paths.external_trend_capability_link_state_path
        state_path.parent.mkdir(parents=True, exist_ok=True)
        stale_ts = time.time() - STALE_CANDIDATE_TTL_SECONDS - 100
        state = {
            "last_scan_at": stale_ts,
            "candidates": [
                {
                    "capability_domain": "old_domain",
                    "wiki_page_ids": ["a.md"],
                    "rationale": "old",
                    "produced_at": stale_ts,
                }
            ],
            "produced_keys": {},
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        candidates = load_external_trend_candidates(paths)
        self.assertEqual(candidates, [])


class TestSoftGoalDeriverExternalKnowledgeSignal(unittest.TestCase):
    def test_from_external_knowledge_converts_candidates(self):
        from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver

        paths = _make_paths()
        cfg = mock.Mock()
        deriver = SoftGoalDeriver(paths, cfg)

        fake_candidates = [
            TrendCapabilityCandidate(
                capability_domain="python_refactor",
                wiki_page_ids=["topics/ai-agent-arch.md"],
                rationale="因为xyz",
                produced_at=time.time(),
            )
        ]
        with mock.patch(
            "mini_agent.evolution.external_trend_capability_link.load_external_trend_candidates",
            return_value=fake_candidates,
        ):
            derived = deriver._from_external_knowledge()

        self.assertEqual(len(derived), 1)
        self.assertEqual(derived[0].source_tag, "external_knowledge")
        self.assertIn("python_refactor", derived[0].title)


if __name__ == "__main__":
    unittest.main()
