"""tests/test_growth_advisor_saturation_and_pursuit_visibility.py

覆盖 growth_advisor_autonomy_deepening_plan.md 的四个后续落地方向：

  B1: evaluate_cycle_increment() —— 规则式增量质量判断
  B2: record_pursuit_cycle_signal() / get_pursuit_saturation() —— 饱和度计数
      process_pursuit_cycle_completion() —— 组装入口 + goal_cron_bridge 钩子
  A1: reports_needing_refresh(goal_backlog=...) —— 已进入自主持续调研的
      候选不再出现在"待刷新报告"列表里
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import growth_advisor as ga
from mini_agent.evolution import output_workspace as ow
from mini_agent.evolution.goal_cron_bridge import reap_finished_cycles
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_cycle(paths, goal_id, cycle_no, covered_subtopics):
    base_dir = ow.goal_output_base_dir(paths, goal_id)
    cycle_dir = ow.allocate_cycle_dir(paths, goal_id, cycle_no)
    progress_note = (
        "本轮小结\n```handoff\n"
        + __import__("json").dumps({"covered_subtopics": covered_subtopics})
        + "\n```"
    )
    ow.write_manifest(base_dir, cycle_dir, progress_note=progress_note, status="completed")


class TestEvaluateCycleIncrement(unittest.TestCase):
    def test_insufficient_cycles_not_evaluated(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_cycle(paths, "g1", 1, ["topic_a"])
            result = ga.evaluate_cycle_increment(paths, "g1")
            self.assertFalse(result["evaluated"])
            self.assertFalse(result["low_increment"])

    def test_high_overlap_flagged_low_increment(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_cycle(paths, "g1", 1, ["topic_a", "topic_b"])
            _write_cycle(paths, "g1", 2, ["topic_a", "topic_b"])  # 完全没有新增
            result = ga.evaluate_cycle_increment(paths, "g1")
            self.assertTrue(result["evaluated"])
            self.assertTrue(result["low_increment"])
            self.assertEqual(result["new_subtopics_count"], 0)

    def test_low_overlap_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_cycle(paths, "g1", 1, ["topic_a"])
            _write_cycle(paths, "g1", 2, ["topic_b", "topic_c", "topic_d"])  # 大量新增
            result = ga.evaluate_cycle_increment(paths, "g1")
            self.assertTrue(result["evaluated"])
            self.assertFalse(result["low_increment"])

    def test_missing_handoff_not_evaluated(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            base_dir = ow.goal_output_base_dir(paths, "g1")
            for i in (1, 2):
                cycle_dir = ow.allocate_cycle_dir(paths, "g1", i)
                ow.write_manifest(base_dir, cycle_dir, progress_note="没有 handoff 块", status="completed")
            result = ga.evaluate_cycle_increment(paths, "g1")
            self.assertFalse(result["evaluated"])
            self.assertFalse(result["low_increment"])


class TestSaturationSignal(unittest.TestCase):
    def test_streak_accumulates_and_saturates(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            r1 = ga.record_pursuit_cycle_signal(paths, "g1", True, saturation_threshold=3)
            self.assertEqual(r1["streak"], 1)
            self.assertFalse(r1["saturated"])
            r2 = ga.record_pursuit_cycle_signal(paths, "g1", True, saturation_threshold=3)
            self.assertEqual(r2["streak"], 2)
            self.assertFalse(r2["saturated"])
            r3 = ga.record_pursuit_cycle_signal(paths, "g1", True, saturation_threshold=3)
            self.assertEqual(r3["streak"], 3)
            self.assertTrue(r3["saturated"])
            self.assertTrue(r3["newly_saturated"])
            # 再来一轮低增量：仍然饱和，但不重复提示
            r4 = ga.record_pursuit_cycle_signal(paths, "g1", True, saturation_threshold=3)
            self.assertTrue(r4["saturated"])
            self.assertFalse(r4["newly_saturated"])

    def test_non_low_increment_resets_streak_and_notified(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            for _ in range(3):
                ga.record_pursuit_cycle_signal(paths, "g1", True, saturation_threshold=3)
            reset = ga.record_pursuit_cycle_signal(paths, "g1", False, saturation_threshold=3)
            self.assertEqual(reset["streak"], 0)
            self.assertFalse(reset["saturated"])
            # 之后重新连续三轮低增量，应该能再次触发一次新的提示
            ga.record_pursuit_cycle_signal(paths, "g1", True, saturation_threshold=3)
            ga.record_pursuit_cycle_signal(paths, "g1", True, saturation_threshold=3)
            again = ga.record_pursuit_cycle_signal(paths, "g1", True, saturation_threshold=3)
            self.assertTrue(again["newly_saturated"])

    def test_get_pursuit_saturation_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            before = ga.get_pursuit_saturation(paths, "g1")
            self.assertEqual(before["streak"], 0)
            # get_pursuit_saturation() 用的是默认阈值（不持久化调用方自定义
            # 的 saturation_threshold），这里保持一致用默认阈值 3 来验证。
            ga.record_pursuit_cycle_signal(paths, "g1", True)
            ga.record_pursuit_cycle_signal(paths, "g1", True)
            ga.record_pursuit_cycle_signal(paths, "g1", True)
            after = ga.get_pursuit_saturation(paths, "g1")
            self.assertEqual(after["streak"], 3)
            self.assertTrue(after["saturated"])


class TestProcessPursuitCycleCompletion(unittest.TestCase):
    def test_non_growth_advisor_goal_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            class FakeGoal:
                id = "g1"
                title = "普通目标"
                tags: list[str] = []

            self.assertIsNone(ga.process_pursuit_cycle_completion(paths, FakeGoal()))

    def test_growth_advisor_goal_triggers_hint_when_saturated(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            class FakeGoal:
                id = "g1"
                title = "持续调研数据分析"
                tags = ["growth_advisor"]

            # 制造连续低增量的 manifest 历史（超过默认阈值 3）
            _write_cycle(paths, "g1", 1, ["a"])
            hint = None
            for i in range(2, 6):
                _write_cycle(paths, "g1", i, ["a"])  # 每轮都跟上一轮完全重复
                h = ga.process_pursuit_cycle_completion(paths, FakeGoal())
                if h is not None:
                    hint = h
            self.assertIsNotNone(hint)
            self.assertEqual(hint["goal_id"], "g1")
            self.assertIn("最近", hint["message"])

    def test_reap_finished_cycles_does_not_raise_on_saturation_check(self):
        """[集成] reap_finished_cycles() 走完整链路时，饱和度检查即使异常
        也不能影响主流程的计数结果——用一个没有子节点的 recurring Goal
        确认 reap 直接跳过、不抛异常。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            goal_backlog.add_goal(
                title="持续调研 X", description="", tags=["growth_advisor"],
            )
            # 没有绑定周期性/没有子节点时，reap 应该直接跳过，不抛异常
            reaped = reap_finished_cycles(goal_backlog)
            self.assertEqual(reaped, 0)


class TestReportsNeedingRefreshSkipsPursuingCandidates(unittest.TestCase):
    def test_recurring_linked_goal_excluded_from_refresh_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                title="持续调研 Y",
                rationale="r",
                evidence_refs=[f"e{i}" for i in range(5)],
                min_evidence_count=3,
                max_pending=10,
                dismissed_cooldown_days=30,
            )
            candidate = backlog.get(cand.candidate_id)
            report = ga.generate_growth_report(paths, candidate)
            backlog.attach_report(cand.candidate_id, report.report_id)

            # 手动制造一次"证据显著增长"，确保不加过滤时会出现在待刷新列表
            # （add_or_merge 要求单次调用的 evidence_refs 长度 >=
            # min_evidence_count，才会走到"合并证据"分支）
            backlog.add_or_merge(
                title="持续调研 Y",
                rationale="r",
                evidence_refs=[f"e{i}" for i in range(5, 15)],
                min_evidence_count=3,
                max_pending=10,
                dismissed_cooldown_days=30,
            )

            without_filter = ga.reports_needing_refresh(paths)
            self.assertTrue(any(r["candidate_id"] == cand.candidate_id for r in without_filter))

            # 走真实的 adopt_candidate_as_goal + make_goal_recurring 落地
            # 一遍，而不是手工拼装内部字段——跟 auto_pursue_candidate 用的
            # 是同一条路径，行为更贴近真实场景。
            goal_backlog = GoalBacklog(paths)
            candidate = backlog.get(cand.candidate_id)
            goal = ga.adopt_candidate_as_goal(paths, candidate, goal_backlog=goal_backlog)
            from mini_agent.evolution.goal_cron_bridge import make_goal_recurring
            from mini_agent.evolution.cron_scheduler import CronScheduler
            cron_scheduler = CronScheduler(paths, submit_fn=None)
            make_goal_recurring(goal_backlog, cron_scheduler, goal.id, "interval:86400")

            with_filter = ga.reports_needing_refresh(paths, goal_backlog=goal_backlog)
            self.assertFalse(any(r["candidate_id"] == cand.candidate_id for r in with_filter))


class TestFollowupHintDistinguishesFailureVsSaturation(unittest.TestCase):
    """[growth_advisor_autonomy_deepening_plan.md 方向 A2] Goal 停滞时，
    对已绑定周期性执行的 Goal 区分"素材饱和"和"执行本身没跑起来"两类
    原因，措辞应该不一样——不应该让"执行卡住了"被误读成"这个方向不值得
    继续"。"""

    def _setup_recurring_stalled_candidate(self, paths, title="持续调研 Z"):
        from mini_agent.evolution.cron_scheduler import CronScheduler
        from mini_agent.evolution.goal_cron_bridge import make_goal_recurring

        backlog = ga.GrowthBacklog(paths)
        cand = backlog.add_or_merge(
            title=title, rationale="r", evidence_refs=[f"e{i}" for i in range(5)],
            min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
        )
        candidate = backlog.get(cand.candidate_id)
        report = ga.generate_growth_report(paths, candidate)
        backlog.attach_report(cand.candidate_id, report.report_id)
        candidate = backlog.get(cand.candidate_id)

        goal_backlog = GoalBacklog(paths)
        goal = ga.adopt_candidate_as_goal(paths, candidate, goal_backlog=goal_backlog)
        cron_scheduler = CronScheduler(paths, submit_fn=None)
        make_goal_recurring(goal_backlog, cron_scheduler, goal.id, "interval:86400")
        # 让 Goal 显式停滞（很久没有 touch）
        goal_backlog.load()
        node = goal_backlog.get(goal.id)
        node.last_touched_at = 0.0
        goal_backlog.save()
        goal_backlog.load()

        cand_final = ga.GrowthBacklog(paths).get(cand.candidate_id)
        cand_final.accepted_at = 0.0  # 确保落在回访窗口内
        ga.GrowthBacklog(paths).save_all([
            (cand_final if x.candidate_id == cand.candidate_id else x)
            for x in ga.GrowthBacklog(paths).load_all()
        ])
        return cand_final, goal_backlog, goal

    def test_execution_issue_wording_when_not_saturated(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand, goal_backlog, goal = self._setup_recurring_stalled_candidate(paths)
            hint = ga.followup_question_hint(paths, cand, goal_backlog=goal_backlog)
            self.assertIn("执行环节", hint)
            self.assertNotIn("了解得差不多", hint)

    def test_saturation_wording_when_saturated(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand, goal_backlog, goal = self._setup_recurring_stalled_candidate(paths)
            for _ in range(3):
                ga.record_pursuit_cycle_signal(paths, goal.id, True)
            hint = ga.followup_question_hint(paths, cand, goal_backlog=goal_backlog)
            self.assertIn("了解得差不多", hint)
            self.assertNotIn("执行环节", hint)

    def test_non_recurring_goal_keeps_original_wording(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                title="一次性目标", rationale="r", evidence_refs=[f"e{i}" for i in range(5)],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            candidate = backlog.get(cand.candidate_id)
            report = ga.generate_growth_report(paths, candidate)
            backlog.attach_report(cand.candidate_id, report.report_id)
            candidate = backlog.get(cand.candidate_id)

            goal_backlog = GoalBacklog(paths)
            goal = ga.adopt_candidate_as_goal(paths, candidate, goal_backlog=goal_backlog)
            goal_backlog.load()
            node = goal_backlog.get(goal.id)
            node.last_touched_at = 0.0
            goal_backlog.save()
            goal_backlog.load()

            cand_final = ga.GrowthBacklog(paths).get(cand.candidate_id)
            hint = ga.followup_question_hint(paths, cand_final, goal_backlog=goal_backlog)
            self.assertIn("要不要先放一放", hint)


if __name__ == "__main__":
    unittest.main()
