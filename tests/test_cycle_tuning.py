"""
tests/test_cycle_tuning.py — 覆盖
next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md Stage 2

  1. build_tuning_proposal：白名单校验（WhitelistViolation）、正常构造
  2. save/load/list_proposals 往返
  3. confirm/reject/apply 状态机流转与非法状态转换报错
  4. apply_tuning_proposal 逐项应用白名单参数：priority/execution_phase/
     schedule/task_template，某一项失败不影响其它项
  5. reject 时追加 progress_notes 留痕
  6. suggest_tuning_from_diagnostics：cron 连续跳过 → 建议放宽 interval
     schedule；stuck_explore 告警 → 建议 regenerate_spec；都不命中时返回 None
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.evolution.goal_cron_bridge import make_goal_recurring
from mini_agent.perception import cycle_diagnostics as cd
from mini_agent.perception import cycle_tuning as ct
from mini_agent.perception import execution_phase as ep
from mini_agent.perception.goal_backlog import load_goal_backlog
from mini_agent.storage.paths import AgentPaths


class TestBuildTuningProposal(unittest.TestCase):
    def test_rejects_non_whitelisted_param(self):
        with self.assertRaises(ct.WhitelistViolation):
            ct.build_tuning_proposal("goal_1", [{"param": "title", "to": "new title"}])

    def test_rejects_empty_changes(self):
        with self.assertRaises(ValueError):
            ct.build_tuning_proposal("goal_1", [])

    def test_builds_valid_proposal(self):
        proposal = ct.build_tuning_proposal(
            "goal_1", [{"param": "priority", "to": 5, "reason": "bump"}],
        )
        self.assertEqual(proposal.status, "draft")
        self.assertEqual(proposal.source, "user_request")
        self.assertEqual(len(proposal.proposed_changes), 1)
        self.assertEqual(proposal.proposed_changes[0].param, "priority")


class TestProposalStorage(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_save_load_roundtrip(self):
        proposal = ct.build_tuning_proposal("goal_1", [{"param": "priority", "to": 3}])
        ct.save_proposal(self.paths, proposal)
        loaded = ct.load_proposal(self.paths, "goal_1", proposal.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.id, proposal.id)
        self.assertEqual(loaded.proposed_changes[0].to_value, 3)

    def test_load_missing_returns_none(self):
        self.assertIsNone(ct.load_proposal(self.paths, "goal_1", "nope"))

    def test_list_proposals_sorted_by_created_at(self):
        p1 = ct.build_tuning_proposal("goal_1", [{"param": "priority", "to": 1}])
        ct.save_proposal(self.paths, p1)
        p2 = ct.build_tuning_proposal("goal_1", [{"param": "priority", "to": 2}])
        p2.created_at = p1.created_at + 10
        ct.save_proposal(self.paths, p2)
        listed = ct.list_proposals(self.paths, "goal_1")
        self.assertEqual([p.id for p in listed], [p1.id, p2.id])


class TestConfirmRejectApply(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)
        self.cs = CronScheduler(self.paths)
        self.cs.load()
        self.node = self.gb.add_goal("Tuning goal", source="user")

    def tearDown(self):
        self._tmpdir.cleanup()

    def _new_proposal(self, changes):
        p = ct.build_tuning_proposal(self.node.id, changes)
        ct.save_proposal(self.paths, p)
        return p

    def test_confirm_requires_draft_status(self):
        p = self._new_proposal([{"param": "priority", "to": 4}])
        ct.confirm_tuning_proposal(self.paths, self.node.id, p.id)
        with self.assertRaises(ValueError):
            ct.confirm_tuning_proposal(self.paths, self.node.id, p.id)

    def test_reject_leaves_no_side_effects_and_appends_progress_note(self):
        p = self._new_proposal([{"param": "priority", "to": 9}])
        ct.reject_tuning_proposal(self.paths, self.gb, self.node.id, p.id, reason="not now")
        node = self.gb.get(self.node.id)
        self.assertEqual(node.priority, 0)  # 未生效
        self.assertIn("已拒绝", node.progress_notes)
        self.assertIn("not now", node.progress_notes)

    def test_apply_requires_confirmed_status(self):
        p = self._new_proposal([{"param": "priority", "to": 4}])
        with self.assertRaises(ValueError):
            ct.apply_tuning_proposal(self.paths, self.gb, self.cs, self.node.id, p.id)

    def test_apply_priority_change(self):
        p = self._new_proposal([{"param": "priority", "to": 8}])
        ct.confirm_tuning_proposal(self.paths, self.node.id, p.id)
        applied = ct.apply_tuning_proposal(self.paths, self.gb, self.cs, self.node.id, p.id)
        self.assertEqual(applied.status, "applied")
        self.assertTrue(applied.apply_results[0]["ok"])
        self.assertEqual(self.gb.get(self.node.id).priority, 8)

    def test_apply_execution_phase_change(self):
        p = self._new_proposal([{"param": "execution_phase", "to": "stable"}])
        ct.confirm_tuning_proposal(self.paths, self.node.id, p.id)
        applied = ct.apply_tuning_proposal(self.paths, self.gb, self.cs, self.node.id, p.id)
        self.assertTrue(applied.apply_results[0]["ok"])
        state = ep.load_phase(self.paths, self.node.id)
        self.assertEqual(state.mode, "stable")

    def test_apply_schedule_change_without_existing_recurrence(self):
        p = self._new_proposal([{"param": "schedule", "to": "interval:3600"}])
        ct.confirm_tuning_proposal(self.paths, self.node.id, p.id)
        applied = ct.apply_tuning_proposal(self.paths, self.gb, self.cs, self.node.id, p.id)
        self.assertTrue(applied.apply_results[0]["ok"])
        node = self.gb.get(self.node.id)
        self.assertTrue(node.recurring)

    def test_apply_task_template_fails_without_bound_cron_job(self):
        p = self._new_proposal([{"param": "task_template", "to": "new template"}])
        ct.confirm_tuning_proposal(self.paths, self.node.id, p.id)
        applied = ct.apply_tuning_proposal(self.paths, self.gb, self.cs, self.node.id, p.id)
        self.assertEqual(applied.status, "applied")
        self.assertFalse(applied.apply_results[0]["ok"])
        self.assertIn("尚未绑定", applied.apply_results[0]["detail"])

    def test_apply_task_template_succeeds_with_bound_cron_job(self):
        make_goal_recurring(self.gb, self.cs, self.node.id, "interval:60")
        p = self._new_proposal([{"param": "task_template", "to": "updated template"}])
        ct.confirm_tuning_proposal(self.paths, self.node.id, p.id)
        applied = ct.apply_tuning_proposal(self.paths, self.gb, self.cs, self.node.id, p.id)
        self.assertTrue(applied.apply_results[0]["ok"])
        node = self.gb.get(self.node.id)
        job = self.cs.get(node.recurrence_cron_job_id)
        self.assertEqual(job.task_template, "updated template")

    def test_apply_regenerate_spec_fails_without_cfg(self):
        p = self._new_proposal([{"param": "regenerate_spec", "to": True}])
        ct.confirm_tuning_proposal(self.paths, self.node.id, p.id)
        applied = ct.apply_tuning_proposal(self.paths, self.gb, self.cs, self.node.id, p.id)
        self.assertEqual(applied.status, "applied")
        self.assertFalse(applied.apply_results[0]["ok"])

    def test_apply_partial_failure_does_not_block_other_changes(self):
        p = self._new_proposal([
            {"param": "priority", "to": 3},
            {"param": "task_template", "to": "x"},  # 没有绑定 cron job，会失败
        ])
        ct.confirm_tuning_proposal(self.paths, self.node.id, p.id)
        applied = ct.apply_tuning_proposal(self.paths, self.gb, self.cs, self.node.id, p.id)
        self.assertEqual(applied.status, "applied")
        results_by_param = {r["param"]: r["ok"] for r in applied.apply_results}
        self.assertTrue(results_by_param["priority"])
        self.assertFalse(results_by_param["task_template"])
        self.assertEqual(self.gb.get(self.node.id).priority, 3)

    def test_apply_appends_progress_note_with_success_count(self):
        p = self._new_proposal([{"param": "priority", "to": 6}])
        ct.confirm_tuning_proposal(self.paths, self.node.id, p.id)
        ct.apply_tuning_proposal(self.paths, self.gb, self.cs, self.node.id, p.id)
        node = self.gb.get(self.node.id)
        self.assertIn("1/1", node.progress_notes)


class TestSuggestTuningFromDiagnostics(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)
        self.cs = CronScheduler(self.paths)
        self.cs.load()
        self.node = self.gb.add_goal("Suggest goal", source="user")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_no_signal_returns_none(self):
        report = cd.build_cycle_diagnostics(self.paths, self.gb, self.node.id)
        self.assertIsNone(ct.suggest_tuning_from_diagnostics(report))

    def test_consecutive_skip_suggests_wider_interval(self):
        make_goal_recurring(self.gb, self.cs, self.node.id, "interval:60")
        node = self.gb.get(self.node.id)
        job = self.cs.get(node.recurrence_cron_job_id)
        job.consecutive_skip_count = ct.DEFAULT_SKIP_SUGGEST_THRESHOLD
        self.cs.save()

        report = cd.build_cycle_diagnostics(self.paths, self.gb, self.node.id)
        suggestion = ct.suggest_tuning_from_diagnostics(report)
        self.assertIsNotNone(suggestion)
        self.assertEqual(suggestion.source, "rule_suggested")
        schedule_change = next(c for c in suggestion.proposed_changes if c.param == "schedule")
        self.assertEqual(schedule_change.to_value, "interval:120")

    def test_cron_expr_schedule_not_touched_by_skip_suggestion(self):
        # cron: 格式不做"翻倍"这种确定性改写，不应该生成 schedule 建议。
        make_goal_recurring(self.gb, self.cs, self.node.id, "cron:0 9 * * 1")
        node = self.gb.get(self.node.id)
        job = self.cs.get(node.recurrence_cron_job_id)
        job.consecutive_skip_count = ct.DEFAULT_SKIP_SUGGEST_THRESHOLD
        self.cs.save()

        report = cd.build_cycle_diagnostics(self.paths, self.gb, self.node.id)
        suggestion = ct.suggest_tuning_from_diagnostics(report)
        self.assertIsNone(suggestion)

    def test_stuck_explore_suggests_regenerate_spec(self):
        state = ep.load_phase(self.paths, self.node.id)
        state.mode = "auto"
        state.cycles_in_mode = 10
        ep.save_phase(self.paths, state)

        report = cd.build_cycle_diagnostics(self.paths, self.gb, self.node.id)
        suggestion = ct.suggest_tuning_from_diagnostics(report)
        self.assertIsNotNone(suggestion)
        self.assertTrue(any(c.param == "regenerate_spec" for c in suggestion.proposed_changes))

    def test_locked_phase_does_not_trigger_regenerate_suggestion(self):
        ep.set_mode(self.paths, self.node.id, "explore", lock=True, reason="user")
        state = ep.load_phase(self.paths, self.node.id)
        state.cycles_in_mode = 10
        ep.save_phase(self.paths, state)

        report = cd.build_cycle_diagnostics(self.paths, self.gb, self.node.id)
        suggestion = ct.suggest_tuning_from_diagnostics(report)
        # locked 手动指定阶段不算异常，check_phase_health 本身就不会告警，
        # 因此不应该有 regenerate_spec 建议。
        self.assertIsNone(suggestion)


class TestParseNLRequestToChanges(unittest.TestCase):
    """Stage 3（可选）：自然语言解析层——白名单过滤、失败回退、便捷组合函数。"""

    def _report(self):
        return cd.CycleDiagnosticsReport(
            goal_id="g1", goal_title="Test Goal", found=True,
            recurring=True, schedule="interval:3600", execution_phase_mode="stable",
        )

    def test_llm_ask_none_returns_none(self):
        self.assertIsNone(ct.parse_nl_request_to_changes("跑快一点", self._report(), None))

    def test_empty_text_returns_none(self):
        self.assertIsNone(ct.parse_nl_request_to_changes("   ", self._report(), lambda p: "[]"))

    def test_llm_exception_falls_back_to_none(self):
        def broken_ask(prompt):
            raise RuntimeError("llm down")
        self.assertIsNone(ct.parse_nl_request_to_changes("跑快一点", self._report(), broken_ask))

    def test_llm_returns_empty_array_means_no_mapping(self):
        self.assertIsNone(ct.parse_nl_request_to_changes("暂停一阵子", self._report(), lambda p: "[]"))

    def test_llm_valid_json_maps_to_whitelisted_change(self):
        def fake_ask(prompt):
            self.assertIn("schedule", prompt)
            return '[{"param": "schedule", "to": "interval:1800", "reason": "跑快一倍"}]'
        changes = ct.parse_nl_request_to_changes("跑快一点", self._report(), fake_ask)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["param"], "schedule")
        self.assertEqual(changes[0]["to"], "interval:1800")

    def test_non_whitelisted_param_is_dropped_not_raised(self):
        def fake_ask(prompt):
            return (
                '[{"param": "schedule", "to": "interval:1800", "reason": "ok"}, '
                '{"param": "delete_goal", "to": true, "reason": "越权"}]'
            )
        changes = ct.parse_nl_request_to_changes("随便改点什么", self._report(), fake_ask)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["param"], "schedule")

    def test_malformed_json_returns_none(self):
        self.assertIsNone(ct.parse_nl_request_to_changes("跑快一点", self._report(), lambda p: "不是 JSON"))

    def test_prose_wrapped_json_array_is_extracted(self):
        def fake_ask(prompt):
            return '这是我的建议：\n[{"param": "priority", "to": 5, "reason": "提高优先级"}]\n谢谢'
        changes = ct.parse_nl_request_to_changes("提高优先级", self._report(), fake_ask)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["param"], "priority")


class TestBuildTuningProposalFromNL(unittest.TestCase):
    def _report(self):
        return cd.CycleDiagnosticsReport(goal_id="g1", goal_title="Test Goal", found=True)

    def test_returns_none_when_parse_fails(self):
        proposal = ct.build_tuning_proposal_from_nl("g1", "暂停一阵子", self._report(), lambda p: "[]")
        self.assertIsNone(proposal)

    def test_returns_none_when_llm_ask_missing(self):
        proposal = ct.build_tuning_proposal_from_nl("g1", "跑快一点", self._report(), None)
        self.assertIsNone(proposal)

    def test_success_builds_draft_proposal_with_user_request_source(self):
        def fake_ask(prompt):
            return '[{"param": "priority", "to": 9, "reason": "更紧急"}]'
        proposal = ct.build_tuning_proposal_from_nl("g1", "优先级调高", self._report(), fake_ask)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.status, "draft")
        self.assertEqual(proposal.source, "user_request")
        self.assertEqual(proposal.goal_id, "g1")
        self.assertEqual(len(proposal.proposed_changes), 1)
        self.assertEqual(proposal.proposed_changes[0].param, "priority")


if __name__ == "__main__":
    unittest.main()
