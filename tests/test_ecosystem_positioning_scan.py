"""tests/test_ecosystem_positioning_scan.py — P4 生态定位扫描测试。

覆盖：
  1. 无 llm_helper 时不产生任何调用、不推进轮转游标
  2. 种子列表为空（未配置）时直接跳过
  3. 种子池大于每周上限时按轮转游标截取，且游标正确前移/回绕
  4. web_search 结果正确批量喂给 LLM，entities/facts 落盘并打
     source_kind == "external_ecosystem"（与 tech_radar_search 的
     "external_search" 区分），source_entries 带上可追溯的 run_id/种子标记
  5. 单个种子 web_search 失败不阻塞其它种子
  6. 全部 web_search 失败或 LLM 调用失败时不推进游标
  7. 单条解析失败不阻塞其余条目
  8. ensure_ecosystem_positioning_scan_job() 注册时默认 disabled
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.external_input.ecosystem_positioning_scan import (
    JOB_ID,
    ensure_ecosystem_positioning_scan_job,
    run_ecosystem_positioning_scan_once,
)
from mini_agent.storage.paths import AgentPaths


class _FakeLLMHelper:
    def __init__(self, response: str):
        self._response = response
        self.calls = 0

    def ask(self, prompt: str) -> str:
        self.calls += 1
        return self._response


def _fake_web_search_factory(results_by_seed: dict, fail_seeds=None):
    fail_seeds = fail_seeds or set()

    def _fn(query: str, max_results: int = 5) -> str:
        if query in fail_seeds:
            raise RuntimeError(f"search failed for {query}")
        return results_by_seed.get(query, f"[web_search] no result for {query}")

    return _fn


class TestEcosystemPositioningScan(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_no_llm_helper_skips_and_does_not_advance_cursor(self):
        summary = run_ecosystem_positioning_scan_once(
            self.paths, llm_helper=None, seeds=["OtherAgentFramework"],
        )
        self.assertEqual(summary.seeds_processed, 0)
        self.assertEqual(summary.search_calls, 0)
        self.assertFalse(self.paths.external_input_ecosystem_positioning_state.exists())

    def test_empty_seeds_skips(self):
        helper = _FakeLLMHelper("{}")
        summary = run_ecosystem_positioning_scan_once(
            self.paths, llm_helper=helper, seeds=[],
            web_search_fn=_fake_web_search_factory({}),
        )
        self.assertEqual(summary.seeds_processed, 0)
        self.assertEqual(helper.calls, 0)
        self.assertFalse(self.paths.external_input_ecosystem_positioning_state.exists())

    def test_entities_and_facts_queued_with_external_ecosystem_source_kind(self):
        seeds = ["OtherAgentFramework"]
        response = json.dumps({
            "items": [
                {
                    "index": 1,
                    "entities": [
                        {"name": "OtherAgentFramework", "entity_type": "project",
                         "description": "一个同类 agent 框架"},
                    ],
                    "facts": [
                        {"statement": "OtherAgentFramework 新增了多智能体协作模式", "confidence": "inferred"},
                    ],
                },
            ]
        })
        helper = _FakeLLMHelper(response)
        web_search_fn = _fake_web_search_factory({
            "OtherAgentFramework": "1. Multi-agent collaboration release\n   https://example.com/other-agent-fw",
        })
        summary = run_ecosystem_positioning_scan_once(
            self.paths, llm_helper=helper, seeds=seeds,
            weekly_seed_limit=5, web_search_fn=web_search_fn,
            run_id="test-run-1",
        )
        self.assertEqual(summary.seeds_configured, 1)
        self.assertEqual(summary.search_calls, 1)
        self.assertEqual(summary.entities_queued, 1)
        self.assertEqual(summary.facts_queued, 1)

        pending = self.paths.world_candidates_pending_path
        rows = [json.loads(line) for line in pending.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["source_kind"], "external_ecosystem")
            self.assertTrue(any(s.startswith("ecosystem_positioning_scan:test-run-1:") for s in row["source_entries"]))
            self.assertIn("https://example.com/other-agent-fw", row["source_entries"])

        state = json.loads(self.paths.external_input_ecosystem_positioning_state.read_text(encoding="utf-8"))
        self.assertEqual(state["offset"], 0)
        self.assertEqual(state["last_run_id"], "test-run-1")

    def test_one_seed_search_failure_does_not_block_others(self):
        seeds = ["good_project", "bad_project"]
        response = json.dumps({
            "items": [
                {"index": 1, "entities": [
                    {"name": "GoodProject", "entity_type": "project", "description": "desc"},
                ], "facts": []},
            ]
        })
        helper = _FakeLLMHelper(response)
        web_search_fn = _fake_web_search_factory(
            {"good_project": "1. Something good\n   https://example.com/good"},
            fail_seeds={"bad_project"},
        )
        summary = run_ecosystem_positioning_scan_once(
            self.paths, llm_helper=helper, seeds=seeds,
            web_search_fn=web_search_fn, run_id="test-run-2",
        )
        self.assertEqual(summary.search_calls, 1)
        self.assertEqual(summary.search_failed_count, 1)
        self.assertEqual(summary.entities_queued, 1)

    def test_all_search_failures_do_not_advance_cursor(self):
        seeds = ["bad_a", "bad_b"]
        helper = _FakeLLMHelper("{}")
        web_search_fn = _fake_web_search_factory({}, fail_seeds={"bad_a", "bad_b"})
        summary = run_ecosystem_positioning_scan_once(
            self.paths, llm_helper=helper, seeds=seeds,
            web_search_fn=web_search_fn,
        )
        self.assertEqual(summary.search_failed_count, 2)
        self.assertEqual(helper.calls, 0)
        self.assertFalse(self.paths.external_input_ecosystem_positioning_state.exists())

    def test_llm_failure_does_not_advance_cursor(self):
        class _BrokenHelper:
            def ask(self, prompt: str) -> str:
                raise RuntimeError("llm down")

        seeds = ["some_project"]
        web_search_fn = _fake_web_search_factory({"some_project": "1. Something\n   https://example.com/x"})
        summary = run_ecosystem_positioning_scan_once(
            self.paths, llm_helper=_BrokenHelper(), seeds=seeds,
            web_search_fn=web_search_fn,
        )
        self.assertEqual(summary.search_calls, 1)
        self.assertFalse(self.paths.external_input_ecosystem_positioning_state.exists())

    def test_parse_failure_for_one_seed_does_not_block_others(self):
        seeds = ["proj_a", "proj_b"]
        response = json.dumps({
            "items": [
                {"index": 2, "entities": [
                    {"name": "Baz", "entity_type": "concept", "description": "desc"},
                ], "facts": []},
            ]
        })
        helper = _FakeLLMHelper(response)
        web_search_fn = _fake_web_search_factory({
            "proj_a": "1. A result\n   https://example.com/a",
            "proj_b": "1. B result\n   https://example.com/b",
        })
        summary = run_ecosystem_positioning_scan_once(
            self.paths, llm_helper=helper, seeds=seeds,
            web_search_fn=web_search_fn,
        )
        self.assertEqual(summary.parse_failed_count, 1)
        self.assertEqual(summary.entities_queued, 1)

    def test_rotation_state_persists_across_runs_when_pool_larger_than_limit(self):
        seeds = ["p1", "p2", "p3", "p4", "p5"]
        empty_response = json.dumps({"items": []})
        web_search_fn = _fake_web_search_factory({
            k: f"1. result for {k}\n   https://example.com/{k}" for k in seeds
        })

        helper = _FakeLLMHelper(empty_response)
        run_ecosystem_positioning_scan_once(
            self.paths, llm_helper=helper, seeds=seeds,
            weekly_seed_limit=2, web_search_fn=web_search_fn, run_id="run-a",
        )
        state1 = json.loads(self.paths.external_input_ecosystem_positioning_state.read_text(encoding="utf-8"))
        self.assertEqual(state1["offset"], 2)

        run_ecosystem_positioning_scan_once(
            self.paths, llm_helper=helper, seeds=seeds,
            weekly_seed_limit=2, web_search_fn=web_search_fn, run_id="run-b",
        )
        state2 = json.loads(self.paths.external_input_ecosystem_positioning_state.read_text(encoding="utf-8"))
        self.assertEqual(state2["offset"], 4)
        self.assertEqual(state2["last_run_id"], "run-b")

    def test_ensure_job_registers_disabled_by_default(self):
        scheduler = CronScheduler(self.paths)
        newly_added = ensure_ecosystem_positioning_scan_job(
            self.paths, scheduler,
            llm_helper_provider=lambda: None,
            seeds=[],
        )
        self.assertTrue(newly_added)
        job = next(j for j in scheduler.list_jobs() if j.id == JOB_ID)
        self.assertFalse(job.enabled)
        self.assertEqual(job.schedule, "interval:604800")

        newly_added_again = ensure_ecosystem_positioning_scan_job(
            self.paths, scheduler,
            llm_helper_provider=lambda: None,
            seeds=[],
        )
        self.assertFalse(newly_added_again)


if __name__ == "__main__":
    unittest.main()
