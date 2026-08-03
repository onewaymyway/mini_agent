"""tests/test_decision_consumption.py — 决策消费校验器（F1）专属单测。

补齐 next_doc/system_connectivity_gaps_and_missing_capabilities_plan.md
P0 建议里指出的技术债：此前该模块只跑通了周边集成测试（goal_judge/
wiki_utility_audit），自己没有专属单测。

覆盖：
  1. wiki_decisions_dir 不存在 / query 为空 → 直接返回空结果，不报错
  2. 真实写入一个 decision 页面后，find_relevant_decisions 能检索到并
     正确截断 summary（MAX_SUMMARY_CHARS）
  3. 检索命中非 decision 页面（无 decision 标识）会被粗筛过滤掉
  4. record_consumption：has_hits=False 时不写日志（幂等/无效调用）
  5. record_consumption + decision_consumption_rate：混合"引用"和"未引用"
     记录时统计正确
  6. decision_consumption_rate：日志文件不存在 → 返回 None（区分于 0 命中）
  7. decision_consumption_rate：日志文件内容损坏（非 JSON 行）不崩溃，
     跳过坏行继续统计
  8. to_prompt_block：无命中返回空字符串；有命中包含 page_id 与摘要
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.decision_consumption import (
    MAX_SUMMARY_CHARS,
    DecisionConsumptionQuery,
    RelevantDecision,
    decision_consumption_rate,
    find_relevant_decisions,
    record_consumption,
)
from mini_agent.wiki.writer import write_page


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class TestFindRelevantDecisions(unittest.TestCase):
    def test_missing_decisions_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = find_relevant_decisions(paths, "要不要引入某个新依赖")
            self.assertFalse(result.has_hits)
            self.assertEqual(result.to_prompt_block(), "")

    def test_empty_query_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            paths.wiki_decisions_dir.mkdir(parents=True, exist_ok=True)
            result = find_relevant_decisions(paths, "")
            self.assertFalse(result.has_hits)

    def test_hits_decision_page_and_truncates_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            paths.ensure_wiki_dirs()
            long_body = "ClientPool dependency choice rationale. " + "详细论证内容 " * 60  # 远超 150 字
            write_page(
                paths,
                page_id="decision_dep_choice",
                page_type="decision",
                body=long_body,
                tags=["dependency", "choice"],
                status="active",
            )
            result = find_relevant_decisions(paths, "ClientPool dependency choice", k=3)
            self.assertTrue(result.has_hits)
            self.assertLessEqual(len(result.decisions[0].summary), MAX_SUMMARY_CHARS)
            block = result.to_prompt_block()
            self.assertIn("decision_dep_choice", block)

    def test_non_decision_pages_are_filtered_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            # 写一个实体页（非 decision），id/路径都不带 decision 标识
            write_page(
                paths,
                page_id="entity_some_tool",
                page_type="entity",
                body="这是一个工具实体页面，讨论依赖选型工具本身",
                tags=["依赖"],
                status="active",
            )
            result = find_relevant_decisions(paths, "依赖 选型")
            self.assertFalse(result.has_hits)

    def test_search_exception_returns_empty_not_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            paths.wiki_decisions_dir.mkdir(parents=True, exist_ok=True)
            from unittest import mock

            with mock.patch(
                "mini_agent.wiki.search.wiki_shelf_search",
                side_effect=RuntimeError("boom"),
            ):
                result = find_relevant_decisions(paths, "任意查询")
            self.assertFalse(result.has_hits)


class TestRecordConsumptionAndRate(unittest.TestCase):
    def test_record_consumption_noop_when_no_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            query = DecisionConsumptionQuery(decisions=[], query="q")
            record_consumption(paths, query, referenced_page_ids=[])
            log_path = paths.wiki_dir / "decision_consumption_log.jsonl"
            self.assertFalse(log_path.exists())

    def test_rate_returns_none_when_log_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertIsNone(decision_consumption_rate(paths))

    def test_rate_computed_from_mixed_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            decisions = [RelevantDecision(page_id="d1", title="t1", summary="s1")]
            query = DecisionConsumptionQuery(decisions=decisions, query="q")

            # 一次被引用，一次未被引用
            record_consumption(paths, query, referenced_page_ids=["d1"])
            record_consumption(paths, query, referenced_page_ids=[])

            stats = decision_consumption_rate(paths)
            self.assertIsNotNone(stats)
            self.assertEqual(stats["total_retrievals"], 2)
            self.assertEqual(stats["consumed"], 1)
            self.assertEqual(stats["consumption_rate"], 0.5)

    def test_rate_skips_corrupted_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            log_path = paths.wiki_dir / "decision_consumption_log.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            decisions = [RelevantDecision(page_id="d1", title="t1", summary="s1")]
            query = DecisionConsumptionQuery(decisions=decisions, query="q")
            record_consumption(paths, query, referenced_page_ids=["d1"])
            # 追加一行损坏的 JSON
            with log_path.open("a", encoding="utf-8") as f:
                f.write("{not valid json\n")

            stats = decision_consumption_rate(paths)
            self.assertIsNotNone(stats)
            self.assertEqual(stats["total_retrievals"], 1)
            self.assertEqual(stats["consumed"], 1)


if __name__ == "__main__":
    unittest.main()
