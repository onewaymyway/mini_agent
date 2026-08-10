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

    def test_lifecycle_shows_goal_reopened_from_status_history(self):
        """[next_doc/growth_advisor_cron_search_and_status_history_plan.md
        方向三] Goal 完成后又被重新打开，时间线应该能看出这个往复
        （goal_completed -> goal_reopened），而不是只剩最后的 active。"""
        from mini_agent.perception.goal_backlog import GoalBacklog as GB

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            candidate = ga.GrowthCandidate(
                candidate_id="c2", title="学习 Rust", rationale="因为...",
                evidence_count=5, confidence=0.6,
            )
            backlog.save_all([candidate])

            goal_backlog = GB(paths)
            goal = goal_backlog.add_goal(title="学习 Rust", source="user")
            backlog.set_linked_goal("c2", goal.id)

            goal_backlog.set_status(goal.id, "completed")
            goal_backlog.set_status(goal.id, "active")

            events = ga.growth_topic_lifecycle(paths, candidate.dedupe_key(), goal_backlog=goal_backlog)
            stages = [e["stage"] for e in events]
            self.assertIn("goal_completed", stages)
            self.assertIn("goal_reopened", stages)
            self.assertLess(stages.index("goal_completed"), stages.index("goal_reopened"))

    def test_lifecycle_falls_back_to_current_status_without_history(self):
        """没有 status_history（旧数据/从未经历过一次 set_status）时退回
        原来的"只看当前状态"路径，向后兼容。"""
        from mini_agent.perception.goal_backlog import GoalBacklog as GB, GoalNode

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            candidate = ga.GrowthCandidate(
                candidate_id="c3", title="学习 Go", rationale="因为...",
                evidence_count=5, confidence=0.6,
            )
            backlog.save_all([candidate])

            goal_backlog = GB(paths)
            node = GoalNode(id="g_old", level="goal", title="学习 Go", source="user", status="completed")
            goal_backlog._nodes[node.id] = node  # 直接注入，模拟没有经过 set_status 的旧数据
            backlog.set_linked_goal("c3", node.id)

            events = ga.growth_topic_lifecycle(paths, candidate.dedupe_key(), goal_backlog=goal_backlog)
            stages = [e["stage"] for e in events]
            self.assertIn("goal_completed", stages)
            self.assertNotIn("goal_reopened", stages)


class TestCronTriggeredActiveSearch(unittest.TestCase):
    """[next_doc/growth_advisor_cron_search_and_status_history_plan.md
    方向一] cron 路径的主动检索预算调度。"""

    def _fake_web_search(self, query: str, max_results: int = 5) -> str:
        return f"[web_search] result for {query}\nhttps://example.com/x"

    def test_disabled_by_default_does_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig()
            candidate = ga.GrowthCandidate(
                candidate_id="c1", title="数据分析", rationale="因为...",
                evidence_count=5, confidence=0.9,
            )
            result = ga._maybe_run_cron_triggered_active_search(
                paths, cfg, [candidate],
                llm_helper=_fake_llm_extract, web_search_fn=self._fake_web_search,
            )
            self.assertIsNone(result)

    def test_enabled_triggers_search_for_top_candidate_without_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig()
            cfg.cron_triggered_active_search_enabled = True
            cfg.cron_triggered_active_search_daily_limit = 1
            candidates = [
                ga.GrowthCandidate(candidate_id="low", title="冷门方向", rationale="r", evidence_count=3, confidence=0.3),
                ga.GrowthCandidate(candidate_id="high", title="热门方向", rationale="r", evidence_count=8, confidence=0.9),
            ]
            result = ga._maybe_run_cron_triggered_active_search(
                paths, cfg, candidates,
                llm_helper=_fake_llm_extract, web_search_fn=self._fake_web_search,
            )
            self.assertIsNotNone(result)
            self.assertEqual(result["triggered_candidate_ids"], ["high"])

    def test_daily_budget_respected_across_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig()
            cfg.cron_triggered_active_search_enabled = True
            cfg.cron_triggered_active_search_daily_limit = 1
            candidates = [
                ga.GrowthCandidate(candidate_id="a", title="方向 A", rationale="r", evidence_count=5, confidence=0.8),
                ga.GrowthCandidate(candidate_id="b", title="方向 B", rationale="r", evidence_count=5, confidence=0.7),
            ]
            ga._maybe_run_cron_triggered_active_search(
                paths, cfg, candidates,
                llm_helper=_fake_llm_extract, web_search_fn=self._fake_web_search,
            )
            # 当天预算已用完，第二次调用不应该再触发。
            result2 = ga._maybe_run_cron_triggered_active_search(
                paths, cfg, candidates,
                llm_helper=_fake_llm_extract, web_search_fn=self._fake_web_search,
            )
            self.assertIsNone(result2)

    def test_missing_web_search_fn_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig()
            cfg.cron_triggered_active_search_enabled = True
            candidate = ga.GrowthCandidate(
                candidate_id="c1", title="数据分析", rationale="因为...",
                evidence_count=5, confidence=0.9,
            )
            result = ga._maybe_run_cron_triggered_active_search(
                paths, cfg, [candidate],
                llm_helper=_fake_llm_extract, web_search_fn=None,
            )
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
