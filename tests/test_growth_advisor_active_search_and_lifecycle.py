"""tests/test_growth_advisor_active_search_and_lifecycle.py — 覆盖
next_doc/growth_advisor_active_search_and_lifecycle_plan.md 两个方向：

  1. generate_growth_report() 在 report_active_search_enabled=True 且
     被动扫描无素材时，触发 web_search_fn 现查一次，并落一份 wiki 页面。
  2. growth_topic_lifecycle() 聚合发现/报告/反馈/Goal 状态为时间线。
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


def _fake_llm_extract(prompt: str) -> str:
    return (
        '{"index": 1, "entities": [{"name": "示例框架", "entity_type": "tool", '
        '"description": "一个示例检索结果"}], "facts": []}'
    )


class TestActiveSearch(unittest.TestCase):
    def test_active_search_skipped_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig()  # report_active_search_enabled 默认 False
            candidate = ga.GrowthCandidate(
                candidate_id="c1", title="数据分析", rationale="因为...",
                evidence_count=5, confidence=0.6,
            )
            calls = []

            def fake_search(query, max_results=5):
                calls.append(query)
                return "some search text"

            report = ga.generate_growth_report(
                paths, candidate, llm_helper=_fake_llm_extract,
                profile=None, cfg=cfg, web_search_fn=fake_search,
            )
            self.assertEqual(calls, [])  # 未开启开关，不触发检索
            self.assertIsNotNone(report)

    def test_active_search_triggers_when_no_passive_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig()
            cfg.report_include_external_context = True
            cfg.report_active_search_enabled = True
            candidate = ga.GrowthCandidate(
                candidate_id="c1", title="数据分析", rationale="因为...",
                evidence_count=5, confidence=0.6,
            )

            from mini_agent.profile import UserProfile
            profile = UserProfile()

            calls = []

            def fake_search(query, max_results=5):
                calls.append(query)
                return "关于数据分析的一些检索结果文本"

            captured_prompts = []

            def fake_llm(prompt):
                captured_prompts.append(prompt)
                if "参考" not in prompt and "web_search" not in prompt and "检索结果" not in prompt:
                    # report 正文生成阶段（非抽取 prompt）
                    return "# 报告正文"
                return _fake_llm_extract(prompt)

            report = ga.generate_growth_report(
                paths, candidate, llm_helper=fake_llm,
                profile=profile, cfg=cfg, web_search_fn=fake_search,
            )
            self.assertGreaterEqual(len(calls), 1)
            self.assertEqual(report.source, "llm")
            # 抽取到的实体应该已经写入 pending 队列
            pending_path = paths.world_candidates_pending_path
            self.assertTrue(pending_path.exists())
            content = pending_path.read_text(encoding="utf-8")
            self.assertIn("growth_advisor_active_search:c1", content)

    def test_active_search_silently_skips_on_search_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig()
            cfg.report_include_external_context = True
            cfg.report_active_search_enabled = True
            candidate = ga.GrowthCandidate(
                candidate_id="c1", title="数据分析", rationale="因为...",
                evidence_count=5, confidence=0.6,
            )

            def failing_search(query, max_results=5):
                raise RuntimeError("network down")

            report = ga.generate_growth_report(
                paths, candidate, llm_helper=lambda p: "# 报告正文",
                profile=None, cfg=cfg, web_search_fn=failing_search,
            )
            # 检索失败不应该让报告生成本身失败
            self.assertIsNotNone(report)


class TestTopicLifecycle(unittest.TestCase):
    def test_lifecycle_orders_events_and_skips_missing_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            candidate = ga.GrowthCandidate(
                candidate_id="c1", title="数据分析", rationale="因为...",
                evidence_count=5, confidence=0.6,
            )
            backlog.save_all([candidate])

            report = ga.generate_growth_report(paths, candidate, cfg=None)
            backlog.set_status("c1", ga.STATUS_ACCEPTED)
            ga.GrowthFeedbackLedger(paths).record("c1", ga.STATUS_ACCEPTED)

            events = ga.growth_topic_lifecycle(paths, candidate.dedupe_key())
            stages = [e["stage"] for e in events]
            self.assertIn("discovered", stages)
            self.assertIn("report_generated", stages)
            self.assertIn("accepted", stages)
            # 没有落地成 Goal，不应该出现 goal_* 事件
            self.assertNotIn("goal_linked", stages)
            # 时间应当单调不减
            ts_list = [e["ts"] for e in events]
            self.assertEqual(ts_list, sorted(ts_list))

    def test_lifecycle_empty_for_unknown_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(ga.growth_topic_lifecycle(paths, "unknown"), [])


if __name__ == "__main__":
    unittest.main()
