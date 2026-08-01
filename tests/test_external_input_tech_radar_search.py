"""tests/test_external_input_tech_radar_search.py — P3 主动检索反哺 wiki 测试。

覆盖：
  1. 无 llm_helper 时不产生任何调用、不推进轮转游标
  2. 种子池 = gap_scanner 缺口 + 手工关键词（去重），无种子时直接跳过
  3. 种子池大于每日上限时按轮转游标截取，且游标正确前移/回绕
  4. web_search 结果正确批量喂给 LLM，entities/facts 落盘并打
     source_kind == "external_search"，source_entries 带上可追溯的
     run_id/种子标记
  5. 单个种子 web_search 失败不阻塞其它种子
  6. 全部 web_search 失败或 LLM 调用失败时不推进游标（下次重新处理同一批）
  7. 单条解析失败不阻塞其余条目
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.external_input.tech_radar_search import (
    run_tech_radar_search_once,
    _select_seeds_for_this_run,
)
from mini_agent.storage.paths import AgentPaths


class _FakeLLMHelper:
    def __init__(self, response: str):
        self._response = response
        self.calls = 0
        self.last_prompt = ""

    def ask(self, prompt: str) -> str:
        self.calls += 1
        self.last_prompt = prompt
        return self._response


def _fake_web_search_factory(results_by_seed: dict, fail_seeds=None):
    fail_seeds = fail_seeds or set()

    def _fn(query: str, max_results: int = 5) -> str:
        if query in fail_seeds:
            raise RuntimeError(f"search failed for {query}")
        return results_by_seed.get(query, f"[web_search] no result for {query}")

    return _fn


class TestSelectSeedsForThisRun(unittest.TestCase):
    def test_pool_smaller_than_limit_returns_all_and_resets_offset(self):
        selected, next_offset = _select_seeds_for_this_run(["a", "b"], limit=5, offset=3)
        self.assertEqual(selected, ["a", "b"])
        self.assertEqual(next_offset, 0)

    def test_pool_larger_than_limit_rotates_and_wraps(self):
        seeds = ["a", "b", "c", "d", "e"]
        selected, next_offset = _select_seeds_for_this_run(seeds, limit=2, offset=4)
        # offset=4 -> 取 seeds[4], seeds[0]（回绕）
        self.assertEqual(selected, ["e", "a"])
        self.assertEqual(next_offset, 1)

    def test_empty_pool_returns_empty(self):
        selected, next_offset = _select_seeds_for_this_run([], limit=5, offset=0)
        self.assertEqual(selected, [])
        self.assertEqual(next_offset, 0)


class TestTechRadarSearch(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_no_llm_helper_skips_and_does_not_advance_cursor(self):
        summary = run_tech_radar_search_once(
            self.paths, llm_helper=None, keywords=["LLM 长上下文"],
        )
        self.assertEqual(summary.seeds_processed, 0)
        self.assertEqual(summary.search_calls, 0)
        self.assertFalse(self.paths.external_input_tech_radar_state.exists())

    def test_no_seeds_available_skips(self):
        helper = _FakeLLMHelper("{}")
        summary = run_tech_radar_search_once(
            self.paths, llm_helper=helper, keywords=[],
            web_search_fn=_fake_web_search_factory({}),
        )
        self.assertEqual(summary.seeds_processed, 0)
        self.assertEqual(helper.calls, 0)

    def test_entities_and_facts_queued_with_external_search_source_kind(self):
        keywords = ["AI Agent 架构"]
        response = json.dumps({
            "items": [
                {
                    "index": 1,
                    "entities": [
                        {"name": "AgentBar", "entity_type": "project",
                         "description": "一个新的 agent 项目"},
                    ],
                    "facts": [
                        {"statement": "AgentBar 发布了 v3", "confidence": "inferred"},
                    ],
                },
            ]
        })
        helper = _FakeLLMHelper(response)
        web_search_fn = _fake_web_search_factory({
            "AI Agent 架构": "1. AgentBar v3 Release\n   https://example.com/agentbar-v3\n   发布说明",
        })
        summary = run_tech_radar_search_once(
            self.paths, llm_helper=helper, keywords=keywords,
            daily_seed_limit=5, web_search_fn=web_search_fn,
            run_id="test-run-1",
        )
        self.assertEqual(summary.search_calls, 1)
        self.assertEqual(summary.entities_queued, 1)
        self.assertEqual(summary.facts_queued, 1)

        pending = self.paths.world_candidates_pending_path
        rows = [json.loads(line) for line in pending.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["source_kind"], "external_search")
            self.assertTrue(any(s.startswith("tech_radar_search:test-run-1:") for s in row["source_entries"]))
            self.assertIn("https://example.com/agentbar-v3", row["source_entries"])

        # 游标应该已经推进（种子池 <= limit，回绕到 0）
        state = json.loads(self.paths.external_input_tech_radar_state.read_text(encoding="utf-8"))
        self.assertEqual(state["offset"], 0)
        self.assertEqual(state["last_run_id"], "test-run-1")

    def test_one_seed_search_failure_does_not_block_others(self):
        keywords = ["good_seed", "bad_seed"]
        response = json.dumps({
            "items": [
                {"index": 1, "entities": [
                    {"name": "GoodThing", "entity_type": "concept", "description": "desc"},
                ], "facts": []},
            ]
        })
        helper = _FakeLLMHelper(response)
        web_search_fn = _fake_web_search_factory(
            {"good_seed": "1. Something good\n   https://example.com/good"},
            fail_seeds={"bad_seed"},
        )
        summary = run_tech_radar_search_once(
            self.paths, llm_helper=helper, keywords=keywords,
            web_search_fn=web_search_fn, run_id="test-run-2",
        )
        self.assertEqual(summary.search_calls, 1)
        self.assertEqual(summary.search_failed_count, 1)
        self.assertEqual(summary.entities_queued, 1)

    def test_all_search_failures_do_not_advance_cursor(self):
        keywords = ["bad_seed_a", "bad_seed_b"]
        helper = _FakeLLMHelper("{}")
        web_search_fn = _fake_web_search_factory(
            {}, fail_seeds={"bad_seed_a", "bad_seed_b"},
        )
        summary = run_tech_radar_search_once(
            self.paths, llm_helper=helper, keywords=keywords,
            web_search_fn=web_search_fn,
        )
        self.assertEqual(summary.search_failed_count, 2)
        self.assertEqual(helper.calls, 0)
        self.assertFalse(self.paths.external_input_tech_radar_state.exists())

    def test_llm_failure_does_not_advance_cursor(self):
        class _BrokenHelper:
            def ask(self, prompt: str) -> str:
                raise RuntimeError("llm down")

        keywords = ["some_seed"]
        web_search_fn = _fake_web_search_factory({"some_seed": "1. Something\n   https://example.com/x"})
        summary = run_tech_radar_search_once(
            self.paths, llm_helper=_BrokenHelper(), keywords=keywords,
            web_search_fn=web_search_fn,
        )
        self.assertEqual(summary.search_calls, 1)
        self.assertFalse(self.paths.external_input_tech_radar_state.exists())

    def test_parse_failure_for_one_seed_does_not_block_others(self):
        keywords = ["seed_a", "seed_b"]
        # 只返回 index=2 的结果，index=1 视为解析失败
        response = json.dumps({
            "items": [
                {"index": 2, "entities": [
                    {"name": "Baz", "entity_type": "concept", "description": "desc"},
                ], "facts": []},
            ]
        })
        helper = _FakeLLMHelper(response)
        web_search_fn = _fake_web_search_factory({
            "seed_a": "1. A result\n   https://example.com/a",
            "seed_b": "1. B result\n   https://example.com/b",
        })
        summary = run_tech_radar_search_once(
            self.paths, llm_helper=helper, keywords=keywords,
            web_search_fn=web_search_fn,
        )
        self.assertEqual(summary.parse_failed_count, 1)
        self.assertEqual(summary.entities_queued, 1)

    def test_rotation_state_persists_across_runs_when_pool_larger_than_limit(self):
        keywords = ["s1", "s2", "s3", "s4", "s5"]
        empty_response = json.dumps({"items": []})
        web_search_fn = _fake_web_search_factory({
            k: f"1. result for {k}\n   https://example.com/{k}" for k in keywords
        })

        helper = _FakeLLMHelper(empty_response)
        run_tech_radar_search_once(
            self.paths, llm_helper=helper, keywords=keywords,
            daily_seed_limit=2, web_search_fn=web_search_fn, run_id="run-a",
        )
        state1 = json.loads(self.paths.external_input_tech_radar_state.read_text(encoding="utf-8"))
        self.assertEqual(state1["offset"], 2)

        run_tech_radar_search_once(
            self.paths, llm_helper=helper, keywords=keywords,
            daily_seed_limit=2, web_search_fn=web_search_fn, run_id="run-b",
        )
        state2 = json.loads(self.paths.external_input_tech_radar_state.read_text(encoding="utf-8"))
        self.assertEqual(state2["offset"], 4)
        self.assertEqual(state2["last_run_id"], "run-b")


if __name__ == "__main__":
    unittest.main()
