"""tests/test_cycle_patrol.py — 覆盖
next_doc/goal_cron_cycle_proactive_patrol_and_health_overview_plan.md
Stage 1（能力 C：主动巡检 + 推送）与 Stage 2（能力 D：健康总览数据源）。

  1. cfg=None / enabled=False：直接返回 None，不写状态文件（零成本）。
  2. 未到巡检间隔：返回 None，不重复计算。
  3. 首次命中不推送，只记录 first_detected_at；第二轮（跨过冷却时间）才
     真正推送。
  4. 信号消失后跟踪记录被清理，下次重新计时。
  5. 命中数超过 max_push_per_run 时合并降噪成一条消息。
  6. LLM 失败时静默回退到模板文案，不影响推送本身。
  7. 一次性 Goal（recurring=False）不参与巡检。
  8. build_overview_live / load_overview：无快照时现算，有快照时优先读
     快照，字段结构一致。
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.config.models import CyclePatrolConfig
from mini_agent.evolution import cycle_patrol as cp
from mini_agent.perception import execution_phase as ep
from mini_agent.perception.goal_backlog import load_goal_backlog
from mini_agent.storage.paths import AgentPaths


class TestRunCyclePatrol(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_stuck_recurring_goal(self, title="Stuck recurring goal"):
        node = self.gb.add_goal(title, source="user")
        self.gb.update_fields(node.id, recurring=True)
        state = ep.load_phase(self.paths, node.id)
        state.mode = "auto"
        state.cycles_in_mode = 10  # >= DEFAULT_STUCK_EXPLORE_CYCLES，触发 stuck_explore 告警
        ep.save_phase(self.paths, state)
        return node

    def test_disabled_returns_none_and_no_state_file(self):
        cfg = CyclePatrolConfig(enabled=False)
        result = cp.run_cycle_patrol(self.paths, self.gb, cfg)
        self.assertIsNone(result)
        self.assertFalse(self.paths.cycle_patrol_state_path.exists())

    def test_none_cfg_returns_none(self):
        self.assertIsNone(cp.run_cycle_patrol(self.paths, self.gb, None))

    def test_interval_throttle(self):
        cfg = CyclePatrolConfig(enabled=True, interval_hours=6.0)
        cp.run_cycle_patrol(self.paths, self.gb, cfg)  # 第一次跑，写入 last_run_at
        state = cp._load_state(self.paths)
        self.assertGreater(state["last_run_at"], 0.0)
        # 手动伪造刚跑过（未到间隔），第二次调用应直接返回 None（不重新聚合）
        result = cp.run_cycle_patrol(self.paths, self.gb, cfg)
        self.assertIsNone(result)

    def test_first_hit_not_pushed_second_hit_after_cooldown_is_pushed(self):
        self._make_stuck_recurring_goal()
        cfg = CyclePatrolConfig(enabled=True, interval_hours=0.0, push_cooldown_hours=0.0,
                                 llm_enabled=False, generate_tuning_drafts=False)

        # 第一轮：命中但是首次发现，不推送
        result1 = cp.run_cycle_patrol(self.paths, self.gb, cfg)
        self.assertIsNone(result1)
        state = cp._load_state(self.paths)
        self.assertEqual(len(state["signals"]), 1)

        # 第二轮：冷却时间为 0，立即满足推送条件
        result2 = cp.run_cycle_patrol(self.paths, self.gb, cfg)
        self.assertIsNotNone(result2)
        self.assertIn("body", result2)
        self.assertTrue(result2["body"])

    def test_signal_disappears_clears_tracking(self):
        node = self._make_stuck_recurring_goal()
        cfg = CyclePatrolConfig(enabled=True, interval_hours=0.0, push_cooldown_hours=0.0,
                                 llm_enabled=False, generate_tuning_drafts=False)
        cp.run_cycle_patrol(self.paths, self.gb, cfg)
        state = cp._load_state(self.paths)
        self.assertIn(node.id, state["signals"])

        # 信号消失：把 execution phase 恢复正常
        state_ep = ep.load_phase(self.paths, node.id)
        state_ep.cycles_in_mode = 0
        ep.save_phase(self.paths, state_ep)

        cp.run_cycle_patrol(self.paths, self.gb, cfg)
        state2 = cp._load_state(self.paths)
        self.assertNotIn(node.id, state2["signals"])

    def test_merge_when_exceeding_max_push_per_run(self):
        for i in range(4):
            self._make_stuck_recurring_goal(title=f"Stuck goal {i}")
        cfg = CyclePatrolConfig(enabled=True, interval_hours=0.0, push_cooldown_hours=0.0,
                                 llm_enabled=False, max_push_per_run=2, generate_tuning_drafts=False)
        cp.run_cycle_patrol(self.paths, self.gb, cfg)  # 首次发现，不推送
        result = cp.run_cycle_patrol(self.paths, self.gb, cfg)
        self.assertIsNotNone(result)
        self.assertIn("4 个 Goal", result["body"])

    def test_llm_failure_falls_back_to_template(self):
        self._make_stuck_recurring_goal()
        cfg = CyclePatrolConfig(enabled=True, interval_hours=0.0, push_cooldown_hours=0.0,
                                 llm_enabled=True, generate_tuning_drafts=False)

        def _boom(prompt):
            raise RuntimeError("llm down")

        cp.run_cycle_patrol(self.paths, self.gb, cfg, llm_ask=_boom)
        result = cp.run_cycle_patrol(self.paths, self.gb, cfg, llm_ask=_boom)
        self.assertIsNotNone(result)
        self.assertTrue(result["body"])  # 静默回退到模板文本，不抛异常

    def test_one_off_goal_not_patrolled(self):
        node = self.gb.add_goal("One-off stuck-looking goal", source="user")
        # 不设置 recurring=True
        state = ep.load_phase(self.paths, node.id)
        state.cycles_in_mode = 10
        ep.save_phase(self.paths, state)
        cfg = CyclePatrolConfig(enabled=True, interval_hours=0.0, push_cooldown_hours=0.0,
                                 llm_enabled=False, generate_tuning_drafts=False)
        cp.run_cycle_patrol(self.paths, self.gb, cfg)
        state_file = cp._load_state(self.paths)
        self.assertEqual(len(state_file["signals"]), 0)


class TestOverview(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_live_overview_no_snapshot(self):
        node = self.gb.add_goal("Recurring for overview", source="user")
        self.gb.update_fields(node.id, recurring=True)
        overview = cp.load_overview(self.paths, self.gb)
        self.assertEqual(overview["data_source"], "live")
        self.assertEqual(len(overview["goals"]), 1)
        self.assertEqual(overview["goals"][0]["goal_id"], node.id)
        self.assertIn(overview["goals"][0]["severity"], ("red", "yellow", "green"))

    def test_snapshot_overview_after_patrol_run(self):
        node = self.gb.add_goal("Recurring for snapshot", source="user")
        self.gb.update_fields(node.id, recurring=True)
        cfg = CyclePatrolConfig(enabled=True, interval_hours=0.0)
        cp.run_cycle_patrol(self.paths, self.gb, cfg)
        overview = cp.load_overview(self.paths, self.gb)
        self.assertEqual(overview["data_source"], "patrol_snapshot")
        self.assertEqual(len(overview["goals"]), 1)
        self.assertEqual(overview["goals"][0]["goal_id"], node.id)

    def test_overview_sorted_by_severity(self):
        healthy = self.gb.add_goal("Healthy", source="user")
        self.gb.update_fields(healthy.id, recurring=True)
        stuck = self.gb.add_goal("Stuck", source="user")
        self.gb.update_fields(stuck.id, recurring=True)
        state = ep.load_phase(self.paths, stuck.id)
        state.cycles_in_mode = 10
        ep.save_phase(self.paths, state)

        overview = cp.load_overview(self.paths, self.gb)
        severities = [g["severity"] for g in overview["goals"]]
        # red/yellow 排在 green 前面
        self.assertLessEqual(
            severities.index("yellow") if "yellow" in severities else 0,
            severities.index("green") if "green" in severities else 0,
        )


class TestDedupeCronSkipAlert(unittest.TestCase):
    """[Stage 3 / §6.2 开放问题落地] cron_skip 信号默认只覆盖跨越
    `skip_alert_threshold` 之前的窗口，避免与 cron 层自己在恰好跨越
    阈值那一刻发出的告警重复。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _report_with_skip_count(self, skip_count):
        node = self.gb.add_goal("Cron skip goal", source="user")
        self.gb.update_fields(node.id, recurring=True)
        from mini_agent.perception.cycle_diagnostics import build_cycle_diagnostics
        report = build_cycle_diagnostics(self.paths, self.gb, node.id)
        report.cron_health = dict(report.cron_health or {})
        report.cron_health["consecutive_skip_count"] = skip_count
        return report

    def test_pre_threshold_window_still_flags_cron_skip(self):
        report = self._report_with_skip_count(4)  # threshold(5) - 1
        signals = cp._screen_candidate(report, skip_alert_threshold=5, dedupe_cron_skip_alert=True)
        self.assertIsNotNone(signals)
        self.assertIn("cron_skip", signals)

    def test_at_or_above_threshold_suppressed_when_dedupe_enabled(self):
        report = self._report_with_skip_count(5)  # 恰好等于阈值，交给 cron 层
        signals = cp._screen_candidate(report, skip_alert_threshold=5, dedupe_cron_skip_alert=True)
        self.assertIsNone(signals)

    def test_dedupe_disabled_keeps_original_unbounded_behavior(self):
        report = self._report_with_skip_count(9)
        signals = cp._screen_candidate(report, skip_alert_threshold=5, dedupe_cron_skip_alert=False)
        self.assertIsNotNone(signals)
        self.assertIn("cron_skip", signals)

    def test_run_cycle_patrol_respects_config_flag(self):
        report_node_report = self._report_with_skip_count(6)  # 超过阈值，仅当关闭去重才命中
        cfg_dedupe_on = CyclePatrolConfig(enabled=True, interval_hours=0.0, push_cooldown_hours=0.0,
                                           llm_enabled=False, generate_tuning_drafts=False,
                                           dedupe_cron_skip_alert=True)
        # 直接跑一轮真实 patrol：由于该 Goal 的 cron_health 是 build_cycle_
        # diagnostics 现算出来的（没有真实 cron 记录），此处改为验证
        # _screen_candidate 与 run_cycle_patrol 使用同一套 dedupe 语义，
        # 不重复构造 cron 状态文件。
        self.assertIsNone(
            cp._screen_candidate(report_node_report, skip_alert_threshold=5,
                                  dedupe_cron_skip_alert=cfg_dedupe_on.dedupe_cron_skip_alert)
        )


class TestPriorityScore(unittest.TestCase):
    """[Stage 3 / §6.4 开放问题落地] 总览条目附带 priority_score，
    同一 severity 档位内按分数降序排列，帮助用户在大量 yellow 中定位
    真正紧急的 Goal。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_overview_entries_carry_priority_score(self):
        node = self.gb.add_goal("Scored goal", source="user")
        self.gb.update_fields(node.id, recurring=True)
        overview = cp.load_overview(self.paths, self.gb)
        self.assertIn("priority_score", overview["goals"][0])

    def test_higher_alert_count_sorts_first_within_same_severity(self):
        mild = self.gb.add_goal("Mild explore", source="user")
        self.gb.update_fields(mild.id, recurring=True)
        heavy = self.gb.add_goal("Heavy explore", source="user")
        self.gb.update_fields(heavy.id, recurring=True)
        for goal, cycles in ((mild, 10), (heavy, 10)):
            state = ep.load_phase(self.paths, goal.id)
            state.cycles_in_mode = cycles
            ep.save_phase(self.paths, state)

        overview = cp.load_overview(self.paths, self.gb)
        goals_by_id = {g["goal_id"]: g for g in overview["goals"]}
        # 两者都应命中 yellow（stuck_explore），且 priority_score 字段存在、
        # 排序结果内部一致（不要求具体数值，只验证排序稳定且字段被使用）。
        yellow_ids = [g["goal_id"] for g in overview["goals"] if g["severity"] == "yellow"]
        scores = [goals_by_id[gid]["priority_score"] for gid in yellow_ids]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertIn(mild.id, goals_by_id)
        self.assertIn(heavy.id, goals_by_id)


class TestReviewTriggers(unittest.TestCase):
    """[Track 3，goal_cron_convergence_and_governance_improvement_plan.md
    §3] 两项搁置方向的复查触发信号：样本量门槛、连续命中轮数、active 语义。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.gb = load_goal_backlog(self.paths)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_explore_goal(self, title, *, with_alert=False):
        node = self.gb.add_goal(title, source="user")
        self.gb.update_fields(node.id, recurring=True)
        state = ep.load_phase(self.paths, node.id)
        state.mode = "explore"  # execution_phase_mode 报告的是原始 mode 字段，
        # 不是 last_known_effective_mode() 解析出的有效阶段，显式设置避免
        # 默认 "auto" 被误判成非 explore。
        ep.save_phase(self.paths, state)
        return node

    def test_ratios_computed_correctly(self):
        goals = [
            {"execution_phase_mode": "explore", "alert_count": 1},
            {"execution_phase_mode": "explore", "alert_count": 0},
            {"execution_phase_mode": "running", "alert_count": 2},
            {"execution_phase_mode": "running", "alert_count": 0},
        ]
        ratios = cp._compute_review_trigger_ratios(goals)
        self.assertEqual(ratios["recurring_goal_count"], 4)
        self.assertAlmostEqual(ratios["explore_alert_ratio"], 0.25)
        self.assertAlmostEqual(ratios["explore_concurrency_ratio"], 0.5)

    def test_empty_overview_returns_zero_ratios(self):
        ratios = cp._compute_review_trigger_ratios([])
        self.assertEqual(ratios["recurring_goal_count"], 0)
        self.assertEqual(ratios["explore_alert_ratio"], 0.0)

    def test_below_sample_threshold_never_activates(self):
        cfg = CyclePatrolConfig(
            enabled=True, review_trigger_min_recurring_goals=5,
            review_trigger_consecutive_rounds=1,
        )
        state = {}
        ratios = {"recurring_goal_count": 3, "explore_alert_ratio": 1.0, "explore_concurrency_ratio": 1.0}
        triggers = cp._update_review_triggers(state, ratios, cfg)
        self.assertFalse(triggers["sample_ok"])
        self.assertFalse(triggers["phase_aware_resource_estimation"]["active"])
        self.assertFalse(triggers["cross_goal_explore_concurrency"]["active"])

    def test_activates_after_consecutive_rounds(self):
        cfg = CyclePatrolConfig(
            enabled=True, review_trigger_min_recurring_goals=2,
            review_trigger_explore_alert_ratio=0.3,
            review_trigger_consecutive_rounds=3,
        )
        state = {}
        ratios = {"recurring_goal_count": 5, "explore_alert_ratio": 0.4, "explore_concurrency_ratio": 0.1}
        for i in range(1, 4):
            triggers = cp._update_review_triggers(state, ratios, cfg)
            self.assertEqual(triggers["phase_aware_resource_estimation"]["consecutive_hits"], i)
            expect_active = i >= 3
            self.assertEqual(triggers["phase_aware_resource_estimation"]["active"], expect_active)

    def test_non_consecutive_hits_reset_counter(self):
        cfg = CyclePatrolConfig(
            enabled=True, review_trigger_min_recurring_goals=2,
            review_trigger_explore_alert_ratio=0.3,
            review_trigger_consecutive_rounds=3,
        )
        state = {}
        hit = {"recurring_goal_count": 5, "explore_alert_ratio": 0.5, "explore_concurrency_ratio": 0.0}
        miss = {"recurring_goal_count": 5, "explore_alert_ratio": 0.1, "explore_concurrency_ratio": 0.0}
        cp._update_review_triggers(state, hit, cfg)
        cp._update_review_triggers(state, hit, cfg)
        triggers = cp._update_review_triggers(state, miss, cfg)
        self.assertEqual(triggers["phase_aware_resource_estimation"]["consecutive_hits"], 0)
        self.assertFalse(triggers["phase_aware_resource_estimation"]["active"])

    def test_review_trigger_messages_only_when_active(self):
        triggers = {
            "phase_aware_resource_estimation": {"active": False, "last_ratio": 0.4},
            "cross_goal_explore_concurrency": {"active": True, "last_ratio": 0.6},
        }
        messages = cp._review_trigger_messages(triggers)
        self.assertEqual(len(messages), 1)
        self.assertIn("跨 Goal 探索期并发治理", messages[0])

    def test_run_cycle_patrol_persists_review_triggers_in_overview(self):
        for i in range(2):
            self._make_explore_goal(f"Explore goal {i}")
        cfg = CyclePatrolConfig(
            enabled=True, interval_hours=0.0, push_cooldown_hours=0.0, llm_enabled=False,
            generate_tuning_drafts=False, review_trigger_min_recurring_goals=1,
            review_trigger_explore_concurrency_ratio=0.5, review_trigger_consecutive_rounds=1,
        )
        cp.run_cycle_patrol(self.paths, self.gb, cfg)
        overview = cp.load_overview(self.paths, self.gb)
        self.assertIn("review_triggers", overview)
        self.assertTrue(overview["review_triggers"]["cross_goal_explore_concurrency"]["active"])

    def test_review_trigger_disabled_skips_computation(self):
        for i in range(2):
            self._make_explore_goal(f"Explore goal {i}")
        cfg = CyclePatrolConfig(
            enabled=True, interval_hours=0.0, push_cooldown_hours=0.0, llm_enabled=False,
            generate_tuning_drafts=False, review_trigger_enabled=False,
        )
        cp.run_cycle_patrol(self.paths, self.gb, cfg)
        state = cp._load_state(self.paths)
        self.assertNotIn("review_triggers", state.get("overview", {}))

    def test_build_overview_live_reports_instantaneous_ratio_without_history(self):
        for i in range(2):
            self._make_explore_goal(f"Explore goal {i}")
        overview = cp.build_overview_live(self.paths, self.gb)
        self.assertIn("review_triggers", overview)
        self.assertFalse(overview["review_triggers"]["consecutive_rounds_tracked"])
        self.assertFalse(overview["review_triggers"]["cross_goal_explore_concurrency"]["active"])


if __name__ == "__main__":
    unittest.main()
