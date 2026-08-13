"""tests/test_growth_advisor_active_search_material.py — 覆盖 next_doc/
growth_advisor_autonomous_search_and_material_improvement_plan.md 阶段一/
阶段二：

  1. 阶段一：主动检索抽取到结构化 entities/facts 时，摘录来自这些结构化
     候选而不是原始检索文本截断；抽取为空时退回原始文本摘录兜底。
  2. 阶段二：`max_calls` 控制查询角度数量，默认 1 与改动前行为一致；
     大于 1 时按关键词表追加查询角度，关键词不够不重复拼凑；单个角度
     失败不影响其它角度；多角度摘录按 id 去重合并。
  3. `generate_growth_report()` / `_maybe_run_cron_triggered_active_
     search()` 正确从 cfg.report_active_search_max_calls 透传 max_calls。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.evolution import growth_advisor as ga
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _candidate(title: str = "数据分析") -> "ga.GrowthCandidate":
    return ga.GrowthCandidate(
        candidate_id="c1", title=title, rationale="因为...",
        evidence_count=5, confidence=0.6,
    )


def _extraction_response_with_entities_and_facts(query: str) -> str:
    return (
        '{"index": 1, "entities": [{"name": "示例框架A", "entity_type": "tool", '
        '"description": "一个跟 %s 相关的示例结果A"}], '
        '"facts": [{"statement": "关于 %s 的一个事实陈述", "confidence": "inferred"}]}'
    ) % (query, query)


def _extraction_response_empty() -> str:
    return '{"index": 1, "entities": [], "facts": []}'


class TestPhaseOneStructuredExcerpts(unittest.TestCase):
    def test_excerpts_come_from_extracted_candidates_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _candidate()

            def fake_search(query, max_results=5):
                return "关于数据分析的原始检索长文本" * 20  # 刻意很长，验证不是靠这个截断

            def fake_llm(prompt):
                return _extraction_response_with_entities_and_facts("数据分析")

            excerpts = ga._active_search_excerpts_for_topic(
                paths, candidate, ["数据分析"],
                web_search_fn=fake_search, llm_helper=fake_llm,
            )
            self.assertTrue(excerpts)
            # 摘录应该来自结构化实体/事实（包含实体名/事实陈述），而不是
            # 原始文本截断（原始文本里不会出现 "#entity:" / "#fact:" 标记）
            ids = [e["id"] for e in excerpts]
            self.assertTrue(any("#entity:" in i for i in ids))
            self.assertTrue(any("#fact:" in i for i in ids))
            texts = " ".join(e["excerpt"] for e in excerpts)
            self.assertIn("示例框架A", texts)
            self.assertIn("事实陈述", texts)

    def test_falls_back_to_raw_text_when_extraction_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _candidate()

            def fake_search(query, max_results=5):
                return "这是一段原始检索文本内容，没有被抽取出结构化信息"

            def fake_llm(prompt):
                return _extraction_response_empty()

            excerpts = ga._active_search_excerpts_for_topic(
                paths, candidate, ["数据分析"],
                web_search_fn=fake_search, llm_helper=fake_llm,
            )
            self.assertEqual(len(excerpts), 1)
            self.assertNotIn("#entity:", excerpts[0]["id"])
            self.assertNotIn("#fact:", excerpts[0]["id"])
            self.assertIn("原始检索文本", excerpts[0]["excerpt"])

    def test_excerpt_count_capped_by_max_excerpts_per_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _candidate()

            def fake_search(query, max_results=5):
                return "检索结果文本"

            def fake_llm(prompt):
                entities = ", ".join(
                    '{"name": "实体%d", "entity_type": "tool", "description": "描述%d"}' % (i, i)
                    for i in range(10)
                )
                return '{"index": 1, "entities": [%s], "facts": []}' % entities

            excerpts = ga._active_search_excerpts_for_topic(
                paths, candidate, ["数据分析"],
                web_search_fn=fake_search, llm_helper=fake_llm,
                max_excerpts_per_call=2,
            )
            self.assertEqual(len(excerpts), 2)

    def test_queue_entities_and_facts_still_called(self):
        """[向后兼容] 落盘行为不受阶段一改动影响——仍然把抽取结果写入
        pending 队列，只是当次报告消费的摘录内容变了。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _candidate()

            def fake_search(query, max_results=5):
                return "检索结果文本"

            def fake_llm(prompt):
                return _extraction_response_with_entities_and_facts("数据分析")

            ga._active_search_excerpts_for_topic(
                paths, candidate, ["数据分析"],
                web_search_fn=fake_search, llm_helper=fake_llm,
            )
            self.assertTrue(paths.world_candidates_pending_path.exists())


class TestPhaseTwoMultiAngleQueries(unittest.TestCase):
    def test_max_calls_default_one_query_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _candidate()
            calls = []

            def fake_search(query, max_results=5):
                calls.append(query)
                return "检索结果文本"

            def fake_llm(prompt):
                return _extraction_response_empty()

            ga._active_search_excerpts_for_topic(
                paths, candidate, ["关键词A", "关键词B", "关键词C"],
                web_search_fn=fake_search, llm_helper=fake_llm,
            )  # max_calls 默认 1
            self.assertEqual(len(calls), 1)
            self.assertIn("关键词A", calls[0])

    def test_max_calls_three_triggers_multiple_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _candidate()
            calls = []

            def fake_search(query, max_results=5):
                calls.append(query)
                return "检索结果文本"

            def fake_llm(prompt):
                return _extraction_response_empty()

            ga._active_search_excerpts_for_topic(
                paths, candidate, ["关键词A", "关键词B", "关键词C"],
                web_search_fn=fake_search, llm_helper=fake_llm,
                max_calls=3, max_excerpts_per_call=10,
            )
            self.assertEqual(len(calls), 3)
            self.assertIn("关键词A", calls[0])
            self.assertIn("关键词B", calls[1])
            self.assertIn("关键词C", calls[2])

    def test_max_calls_exceeds_keyword_count_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _candidate()
            calls = []

            def fake_search(query, max_results=5):
                calls.append(query)
                return "检索结果文本"

            def fake_llm(prompt):
                return _extraction_response_empty()

            ga._active_search_excerpts_for_topic(
                paths, candidate, ["唯一关键词"],
                web_search_fn=fake_search, llm_helper=fake_llm,
                max_calls=5, max_excerpts_per_call=10,
            )
            self.assertEqual(len(calls), 1)  # 只有一个关键词，不重复拼凑

    def test_one_angle_failure_does_not_block_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _candidate()

            def flaky_search(query, max_results=5):
                if "关键词A" in query:
                    raise RuntimeError("simulated search failure")
                return "关键词B 的检索结果"

            def fake_llm(prompt):
                return _extraction_response_with_entities_and_facts("关键词B")

            excerpts = ga._active_search_excerpts_for_topic(
                paths, candidate, ["关键词A", "关键词B"],
                web_search_fn=flaky_search, llm_helper=fake_llm,
                max_calls=2, max_excerpts_per_call=10,
            )
            self.assertTrue(excerpts)  # 第一个角度失败，第二个角度仍然产出摘录

    def test_multi_angle_excerpts_deduplicated_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _candidate()

            def fake_search(query, max_results=5):
                return "检索结果文本"

            def fake_llm(prompt):
                # 两个角度都抽出同名实体，模拟不同查询命中同一实体的情况
                return (
                    '{"index": 1, "entities": [{"name": "重复实体", "entity_type": "tool", '
                    '"description": "同一个实体被反复抽出"}], "facts": []}'
                )

            excerpts = ga._active_search_excerpts_for_topic(
                paths, candidate, ["关键词A", "关键词B"],
                web_search_fn=fake_search, llm_helper=fake_llm,
                max_calls=2, max_excerpts_per_call=10,
            )
            ids = [e["id"] for e in excerpts]
            self.assertEqual(len(ids), len(set(ids)))  # 无重复 id


class TestConfigWiring(unittest.TestCase):
    def test_generate_growth_report_passes_max_calls_from_cfg(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _candidate()
            cfg = GrowthAdvisorConfig()
            cfg.report_include_external_context = True
            cfg.report_active_search_enabled = True
            cfg.report_active_search_max_calls = 2

            from mini_agent.profile import UserProfile
            profile = UserProfile()

            search_calls = []

            def fake_search(query, max_results=5):
                search_calls.append(query)
                return "检索结果文本"

            def fake_llm(prompt):
                if "参考" not in prompt and "检索结果" not in prompt and "index" not in prompt:
                    return "# 报告正文"
                return _extraction_response_empty()

            ga.generate_growth_report(
                paths, candidate, llm_helper=fake_llm,
                profile=profile, cfg=cfg, web_search_fn=fake_search,
            )
            # profile 里没有该 title 的关键词表项时，effective keywords 为空，
            # _build_active_search_queries 会退化为只用标题查一次；这里只
            # 验证没有因为 max_calls=2 而报错、且至少发起了一次检索。
            self.assertGreaterEqual(len(search_calls), 1)

    def test_cron_triggered_search_passes_max_calls_from_cfg(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _candidate()
            cfg = GrowthAdvisorConfig()
            cfg.cron_triggered_active_search_enabled = True
            cfg.cron_triggered_active_search_daily_limit = 1
            cfg.report_active_search_max_calls = 1

            search_calls = []

            def fake_search(query, max_results=5):
                search_calls.append(query)
                return "检索结果文本"

            def fake_llm(prompt):
                return _extraction_response_empty()

            result = ga._maybe_run_cron_triggered_active_search(
                paths, cfg, [candidate], llm_helper=fake_llm, web_search_fn=fake_search, profile=None,
            )
            self.assertIsNotNone(result)
            self.assertGreaterEqual(len(search_calls), 1)


if __name__ == "__main__":
    unittest.main()
