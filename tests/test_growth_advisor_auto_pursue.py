"""tests/test_growth_advisor_auto_pursue.py — "采纳即启动"测试。

覆盖 `growth_advisor.auto_pursue_candidate()`：
  1. 候选没有报告 → 自动生成报告 → 落地成 Goal → 生成并确认执行规范
     （用 growth_pursuit 模板）→ 绑定周期性，全链路成功
  2. 已经落地过的候选（有 linked_goal_id）→ 复用已有 Goal，不重复创建
  3. 拿不到 GoalBacklog → 尽力而为，返回 errors，不抛异常
  4. 拿不到 CronScheduler（非 daemon 模式）→ Goal/执行规范仍然完成，
     errors 里提示跳过了绑定周期性
  5. 执行规范构建失败（LLM 路径异常）→ 不中断整条链路，Goal 仍然创建成功

以及 growth_pursuit 模板本身可以被 list_templates()/load_template() 正常读取。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.evolution import growth_advisor as ga
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.perception import goal_execution_spec as ges
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _pending_candidate(paths, title="数据分析", evidence_count=5):
    backlog = ga.GrowthBacklog(paths)
    cand = backlog.add_or_merge(
        title=title,
        rationale="你最近经常聊到这个方向",
        evidence_refs=[f"e{i}" for i in range(evidence_count)],
        min_evidence_count=3,
        max_pending=10,
        dismissed_cooldown_days=30,
    )
    return backlog.get(cand.candidate_id)


_FAKE_SPEC_JSON = json.dumps({
    "deliverables": [{"name": "wiki/growth/topic.md", "naming_pattern": "wiki/growth/topic.md"}],
    "handoff_fields": [{"key": "covered_subtopics"}],
    "sub_directories": [],
    "per_cycle_criteria": [{"text": "有实质性增量", "verification_method": "manual_review"}],
    "overall_completion_criteria": [],
    "special_constraints": [],
})


class TestGrowthPursuitTemplate(unittest.TestCase):
    def test_template_listed_and_loadable(self):
        templates = ges.list_templates()
        ids = [t["id"] for t in templates]
        self.assertIn("growth_pursuit", ids)
        tpl = ges.load_template("growth_pursuit")
        self.assertIsNotNone(tpl)
        skeleton = tpl["skeleton"]
        self.assertTrue(skeleton["deliverables"])
        handoff_keys = {f.get("name") for f in skeleton["handoff_fields"]}
        self.assertEqual(
            handoff_keys, {"covered_subtopics", "open_questions", "last_source_urls"}
        )
        self.assertTrue(skeleton["per_cycle_criteria"])


class TestAutoPursueCandidate(unittest.TestCase):
    def _run_full_chain(self, tmp, candidate_factory=_pending_candidate):
        paths = _make_paths(tmp)
        candidate = candidate_factory(paths)
        goal_backlog = GoalBacklog(paths)
        cron_scheduler = CronScheduler(paths, submit_fn=None)
        cfg = GrowthAdvisorConfig()

        with patch.object(
            ges.GoalExecutionSpecBuilder, "_run_builder", return_value=_FAKE_SPEC_JSON
        ):
            result = ga.auto_pursue_candidate(
                paths, candidate, goal_backlog=goal_backlog,
                cron_scheduler=cron_scheduler, cfg=cfg,
            )
        return paths, candidate, goal_backlog, cron_scheduler, result

    def test_full_chain_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, candidate, goal_backlog, cron_scheduler, result = self._run_full_chain(tmp)

            self.assertEqual(result["errors"], [])
            self.assertTrue(result["report_generated"])
            self.assertIsNotNone(result["goal"])
            self.assertIsNotNone(result["spec"])
            self.assertTrue(result["spec"].confirmed)
            self.assertIsNotNone(result["cron_job"])

            # 候选反向记 linked_goal_id，且状态流转成 accepted
            reloaded = ga.GrowthBacklog(paths).get(candidate.candidate_id)
            self.assertEqual(reloaded.linked_goal_id, result["goal"].id)
            self.assertEqual(reloaded.status, ga.STATUS_ACCEPTED)

            # Goal 已绑定周期性
            goal_backlog.load()
            goal = goal_backlog.get(result["goal"].id)
            self.assertTrue(goal.recurring)
            self.assertEqual(goal.recurrence_cron_job_id, result["cron_job"].id)

            # 执行规范已落盘且已确认
            saved_spec = ges.load_spec(paths, goal.id)
            self.assertIsNotNone(saved_spec)
            self.assertTrue(saved_spec.confirmed)

    def test_reuses_existing_linked_goal_without_duplicating(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, candidate, goal_backlog, cron_scheduler, result = self._run_full_chain(tmp)
            first_goal_id = result["goal"].id

            candidate2 = ga.GrowthBacklog(paths).get(candidate.candidate_id)
            with patch.object(
                ges.GoalExecutionSpecBuilder, "_run_builder", return_value=_FAKE_SPEC_JSON
            ):
                result2 = ga.auto_pursue_candidate(
                    paths, candidate2, goal_backlog=goal_backlog,
                    cron_scheduler=cron_scheduler, cfg=GrowthAdvisorConfig(),
                )
            self.assertEqual(result2["goal"].id, first_goal_id)
            # 没有重复创建 Goal
            all_goals = [n for n in goal_backlog._nodes.values() if n.is_goal]
            self.assertEqual(len(all_goals), 1)

    def test_no_goal_backlog_returns_error_not_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _pending_candidate(paths)
            with patch(
                "mini_agent.evolution.growth_advisor._load_goal_backlog_safely",
                return_value=None,
            ):
                result = ga.auto_pursue_candidate(
                    paths, candidate, goal_backlog=None, cron_scheduler=None,
                    cfg=GrowthAdvisorConfig(),
                )
            self.assertIsNone(result["goal"])
            self.assertTrue(result["errors"])

    def test_missing_cron_scheduler_still_creates_goal_and_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _pending_candidate(paths)
            goal_backlog = GoalBacklog(paths)
            with patch.object(
                ges.GoalExecutionSpecBuilder, "_run_builder", return_value=_FAKE_SPEC_JSON
            ):
                result = ga.auto_pursue_candidate(
                    paths, candidate, goal_backlog=goal_backlog, cron_scheduler=None,
                    cfg=GrowthAdvisorConfig(),
                )
            self.assertIsNotNone(result["goal"])
            self.assertIsNotNone(result["spec"])
            self.assertIsNone(result["cron_job"])
            self.assertTrue(any("周期性" in e for e in result["errors"]))

    def test_spec_build_failure_does_not_block_goal_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = _pending_candidate(paths)
            goal_backlog = GoalBacklog(paths)
            cron_scheduler = CronScheduler(paths, submit_fn=None)

            def _boom(*args, **kwargs):
                raise RuntimeError("llm unavailable")

            with patch.object(ges.GoalExecutionSpecBuilder, "_run_builder", side_effect=_boom):
                result = ga.auto_pursue_candidate(
                    paths, candidate, goal_backlog=goal_backlog,
                    cron_scheduler=cron_scheduler, cfg=GrowthAdvisorConfig(),
                )
            self.assertIsNotNone(result["goal"])
            self.assertIsNone(result["spec"])
            self.assertTrue(any("执行规范" in e for e in result["errors"]))
            # 即使执行规范失败，周期性绑定仍然照常进行（不依赖执行规范是否生成）
            self.assertIsNotNone(result["cron_job"])


if __name__ == "__main__":
    unittest.main()
