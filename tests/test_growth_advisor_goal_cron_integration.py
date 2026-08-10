"""tests/test_growth_advisor_goal_cron_integration.py — 成长顾问 × Goal/Cron
打通测试（对应 next_doc/growth_advisor_goal_cron_integration_plan.md）。

覆盖：
  阶段 A：goal_growth_alignment() 找出未匹配兴趣 / 已关联但停滞的目标
  阶段 B：adopt_candidate_as_goal() 候选落地成 Goal，反向写 linked_goal_id
  阶段 C：pending_followups() / followup_question_hint() 优先使用 Goal
          真实状态，且在未传 goal_backlog 时完全向后兼容
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.evolution import growth_advisor as ga
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.profile import UserProfile
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _accepted_candidate_with_report(paths, title="数据分析", evidence_count=5, confidence=0.7):
    backlog = ga.GrowthBacklog(paths)
    cand = backlog.add_or_merge(
        title=title,
        rationale="你最近经常聊到这个方向",
        evidence_refs=[f"e{i}" for i in range(evidence_count)],
        min_evidence_count=3,
        max_pending=10,
        dismissed_cooldown_days=30,
    )
    backlog.set_status(cand.candidate_id, ga.STATUS_ACCEPTED)
    report = ga.GrowthReport(
        report_id="r1",
        candidate_id=cand.candidate_id,
        title=title,
        slug="data-analysis",
        summary="调研摘要",
        body_path=str(Path(paths.workdir_dir) / "growth_reports" / "r1.md"),
    )
    body_path = Path(report.body_path)
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_text("# 报告正文", encoding="utf-8")
    ga._append_jsonl(paths.growth_reports_index_path, report.to_dict())
    backlog.attach_report(cand.candidate_id, report.report_id)
    return backlog.get(cand.candidate_id)


def _force_last_touched(goal_backlog, goal_id, ts):
    """`GoalBacklog.update_fields()` 会在末尾无条件把 last_touched_at
    刷成当前时间（"任意字段更新都算一次 touch"的既有语义），测试里需要
    构造"很久没动"的场景时不能用它，直接操作内部节点 + save()。"""
    goal_backlog.load()
    node = goal_backlog._nodes[goal_id]
    node.last_touched_at = ts
    goal_backlog.save()


class TestGoalGrowthAlignment(unittest.TestCase):
    def test_unmatched_interest_when_no_matching_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            profile.derived["growth_focus_areas"] = {"数据分析": ["e1", "e2", "e3"]}
            cfg = GrowthAdvisorConfig()
            result = ga.goal_growth_alignment(paths, profile, cfg=cfg)
            self.assertTrue(result["enabled"])
            topics = [r["topic"] for r in result["unmatched_interests"]]
            self.assertIn("数据分析", topics)
            self.assertEqual(result["linked_goals"], [])

    def test_matched_goal_by_title_keyword_not_unmatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            profile.derived["growth_focus_areas"] = {"数据分析": ["e1", "e2", "e3"]}
            goal_backlog = GoalBacklog(paths)
            goal_backlog.add_goal(title="数据分析", description="提升数据分析能力")
            cfg = GrowthAdvisorConfig()
            result = ga.goal_growth_alignment(paths, profile, cfg=cfg, goal_backlog=goal_backlog)
            topics = [r["topic"] for r in result["unmatched_interests"]]
            self.assertNotIn("数据分析", topics)

    def test_linked_goal_stalled_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            cand = _accepted_candidate_with_report(paths, title="数据分析")
            goal_backlog = GoalBacklog(paths)
            goal = goal_backlog.add_goal(title="数据分析", description="d")
            # 手动把 last_touched_at 拨到很久以前，模拟停滞。
            _force_last_touched(goal_backlog, goal.id, time.time() - 40 * 86400)
            ga.GrowthBacklog(paths).set_linked_goal(cand.candidate_id, goal.id)

            cfg = GrowthAdvisorConfig(goal_alignment_stalled_days=21)
            result = ga.goal_growth_alignment(paths, profile, cfg=cfg, goal_backlog=goal_backlog)
            linked = {r["goal_id"]: r for r in result["linked_goals"]}
            self.assertIn(goal.id, linked)
            self.assertTrue(linked[goal.id]["stalled"])

    def test_disabled_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            profile.derived["growth_focus_areas"] = {"数据分析": ["e1", "e2", "e3"]}
            cfg = GrowthAdvisorConfig(goal_alignment_enabled=False)
            result = ga.goal_growth_alignment(paths, profile, cfg=cfg)
            self.assertFalse(result["enabled"])
            self.assertEqual(result["unmatched_interests"], [])
            self.assertEqual(result["linked_goals"], [])


class TestAdoptCandidateAsGoal(unittest.TestCase):
    def test_requires_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                title="没有报告的方向",
                rationale="r",
                evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3,
                max_pending=10,
                dismissed_cooldown_days=30,
            )
            goal_backlog = GoalBacklog(paths)
            with self.assertRaises(ValueError):
                ga.adopt_candidate_as_goal(paths, cand, goal_backlog=goal_backlog)

    def test_adopt_creates_goal_and_links_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand = _accepted_candidate_with_report(paths, title="数据分析")
            goal_backlog = GoalBacklog(paths)
            goal = ga.adopt_candidate_as_goal(paths, cand, goal_backlog=goal_backlog)

            self.assertEqual(goal.title, "数据分析")
            self.assertIn("growth_advisor", goal.tags)
            reloaded_goal = goal_backlog.get(goal.id)
            self.assertIsNotNone(reloaded_goal)

            reloaded_cand = ga.GrowthBacklog(paths).get(cand.candidate_id)
            self.assertEqual(reloaded_cand.linked_goal_id, goal.id)
            self.assertEqual(reloaded_cand.status, ga.STATUS_ACCEPTED)

    def test_adopt_pending_candidate_transitions_to_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                title="表达能力",
                rationale="r",
                evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3,
                max_pending=10,
                dismissed_cooldown_days=30,
            )
            self.assertEqual(cand.status, ga.STATUS_PENDING)
            report = ga.GrowthReport(
                report_id="r2",
                candidate_id=cand.candidate_id,
                title="表达能力",
                slug="expr",
                summary="摘要",
                body_path=str(Path(paths.workdir_dir) / "r2.md"),
            )
            Path(report.body_path).write_text("正文", encoding="utf-8")
            ga._append_jsonl(paths.growth_reports_index_path, report.to_dict())
            backlog.attach_report(cand.candidate_id, report.report_id)
            cand = backlog.get(cand.candidate_id)

            goal_backlog = GoalBacklog(paths)
            ga.adopt_candidate_as_goal(paths, cand, goal_backlog=goal_backlog)
            reloaded = backlog.get(cand.candidate_id)
            self.assertEqual(reloaded.status, ga.STATUS_ACCEPTED)


class TestFollowupUsesGoalSignal(unittest.TestCase):
    def test_completed_goal_auto_records_progressed_without_showing_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand = _accepted_candidate_with_report(paths, title="数据分析")
            goal_backlog = GoalBacklog(paths)
            goal = goal_backlog.add_goal(title="数据分析", description="d")
            goal_backlog.set_status(goal.id, "completed")
            ga.GrowthBacklog(paths).set_linked_goal(cand.candidate_id, goal.id)
            # 让候选进入回访窗口
            backlog = ga.GrowthBacklog(paths)
            all_c = backlog.load_all()
            for c in all_c:
                if c.candidate_id == cand.candidate_id:
                    c.accepted_at = time.time() - 40 * 86400
            backlog.save_all(all_c)

            cfg = GrowthAdvisorConfig(followup_review_days=30)
            result = ga.pending_followups(paths, cfg, goal_backlog=goal_backlog)
            self.assertEqual(result, [])  # 已完成，不需要展示回访卡片
            reloaded = ga.GrowthBacklog(paths).get(cand.candidate_id)
            self.assertEqual(reloaded.followup_status, "progressed")

    def test_stalled_goal_shows_followup_card_with_goal_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand = _accepted_candidate_with_report(paths, title="数据分析")
            goal_backlog = GoalBacklog(paths)
            goal = goal_backlog.add_goal(title="数据分析", description="d")
            _force_last_touched(goal_backlog, goal.id, time.time() - 100 * 86400)
            ga.GrowthBacklog(paths).set_linked_goal(cand.candidate_id, goal.id)
            backlog = ga.GrowthBacklog(paths)
            all_c = backlog.load_all()
            for c in all_c:
                if c.candidate_id == cand.candidate_id:
                    c.accepted_at = time.time() - 40 * 86400
            backlog.save_all(all_c)

            cfg = GrowthAdvisorConfig(followup_review_days=30, goal_alignment_stalled_days=21)
            result = ga.pending_followups(paths, cfg, goal_backlog=goal_backlog)
            self.assertEqual(len(result), 1)
            hint = ga.followup_question_hint(paths, result[0], cfg=cfg, goal_backlog=goal_backlog)
            self.assertIn("目标", hint)

    def test_no_goal_backlog_passed_behaves_like_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand = _accepted_candidate_with_report(paths, title="数据分析")
            goal_backlog = GoalBacklog(paths)
            goal = goal_backlog.add_goal(title="数据分析", description="d")
            ga.GrowthBacklog(paths).set_linked_goal(cand.candidate_id, goal.id)
            backlog = ga.GrowthBacklog(paths)
            all_c = backlog.load_all()
            for c in all_c:
                if c.candidate_id == cand.candidate_id:
                    c.accepted_at = time.time() - 40 * 86400
            backlog.save_all(all_c)

            cfg = GrowthAdvisorConfig(followup_review_days=30)
            # 不传 goal_backlog：应完全退化到原有 memory 证据数走势逻辑，
            # 不因为候选存在 linked_goal_id 就报错或行为异常。
            result = ga.pending_followups(paths, cfg)
            self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main()
