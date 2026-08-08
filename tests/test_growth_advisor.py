"""tests/test_growth_advisor.py — 成长顾问 Growth Advisor P1 测试
（对应 next_doc/growth_advisor_design.md）。

覆盖：
  1. growth_signal_scan 按关键词命中统计写回 profile.derived
  2. growth_candidate_derive 证据不足不生成候选；证据达标生成候选
  3. GrowthBacklog.add_or_merge 去重合并证据、pending 数量上限、
     dismissed 冷却期内不重新生成
  4. generate_growth_report 模板兜底路径正常生成并落盘、索引可查
  5. run_daily_cycle 端到端：扫描 → 候选 → Top-N 报告
  6. monthly_retrospective_summary 统计口径正确
  7. cron_scheduler 内置 job 注册了 sys:growth_advisor_daily /
     sys:growth_monthly_retrospective
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.evolution import growth_advisor as ga
from mini_agent.profile import UserProfile
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class _FakeEntry:
    def __init__(self, entry_id, summary, tags, created_at):
        self.entry_id = entry_id
        self.summary = summary
        self.tags = tags
        self.created_at = created_at


class _FakeMemoryStore:
    def __init__(self, entries):
        self._entries = entries

    def all_entries(self):
        return self._entries


class TestSignalScan(unittest.TestCase):
    def test_scan_hits_keywords_and_writes_profile_derived(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            entries = [
                _FakeEntry("e1", "讨论了 python packaging 的坑", ["python"], now - 10),
                _FakeEntry("e2", "写了个 pytest fixture", [], now - 20),
                _FakeEntry("e3", "和产品聊了排期", ["项目管理"], now - 30),
                _FakeEntry("e4", "一年前的老记录", ["python"], now - 200 * 86400),  # 窗口外
            ]
            store = _FakeMemoryStore(entries)
            profile = UserProfile()

            hits = ga.growth_signal_scan(paths, profile, store)

            self.assertIn("Python 工程实践", hits)
            self.assertEqual(sorted(hits["Python 工程实践"]), ["e1", "e2"])
            self.assertNotIn("e4", hits.get("Python 工程实践", []))
            self.assertIn("growth_focus_areas", profile.derived)
            self.assertEqual(profile.derived["growth_focus_areas"], hits)

    def test_scan_with_empty_store_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            hits = ga.growth_signal_scan(paths, profile, None)
            self.assertEqual(hits, {})


class TestCandidateDerive(unittest.TestCase):
    def _profile_with_focus(self, focus_areas: dict) -> UserProfile:
        p = UserProfile()
        p.derived = {"growth_focus_areas": focus_areas}
        return p

    def test_insufficient_evidence_no_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(min_evidence_count=3)
            profile = self._profile_with_focus({"数据分析": ["e1", "e2"]})  # 只有 2 条
            produced = ga.growth_candidate_derive(paths, cfg, profile)
            self.assertEqual(produced, [])
            self.assertEqual(ga.GrowthBacklog(paths).pending(), [])

    def test_sufficient_evidence_generates_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(min_evidence_count=3)
            profile = self._profile_with_focus({"数据分析": ["e1", "e2", "e3"]})
            produced = ga.growth_candidate_derive(paths, cfg, profile)
            self.assertEqual(len(produced), 1)
            self.assertEqual(produced[0].title, "数据分析")
            self.assertEqual(produced[0].evidence_count, 3)

    def test_excluded_topics_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(min_evidence_count=3, excluded_topics=["数据分析"])
            profile = self._profile_with_focus({"数据分析": ["e1", "e2", "e3"]})
            produced = ga.growth_candidate_derive(paths, cfg, profile)
            self.assertEqual(produced, [])


class TestGrowthBacklog(unittest.TestCase):
    def test_merge_dedup_by_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c1 = backlog.add_or_merge(
                "写作与表达", "理由A", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            self.assertIsNotNone(c1)
            c2 = backlog.add_or_merge(
                "写作与表达！！", "理由B", ["e3", "e4", "e5"],  # 标点差异，应归一化为同一 key
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            self.assertEqual(c1.candidate_id, c2.candidate_id)
            self.assertEqual(sorted(c2.evidence_refs), ["e1", "e2", "e3", "e4", "e5"])
            self.assertEqual(len(backlog.load_all()), 1)

    def test_max_pending_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            for i in range(3):
                backlog.add_or_merge(
                    f"主题{i}", "理由", ["e1", "e2", "e3"],
                    min_evidence_count=3, max_pending=2, dismissed_cooldown_days=30,
                )
            self.assertEqual(len(backlog.pending()), 2)

    def test_dismissed_cooldown_blocks_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "系统设计与架构", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(c.candidate_id, ga.STATUS_DISMISSED)
            again = backlog.add_or_merge(
                "系统设计与架构", "新理由", ["e4", "e5", "e6"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            self.assertIsNone(again)

    def test_expire_stale_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "AI/LLM 应用", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            all_c = backlog.load_all()
            all_c[0].created_at = time.time() - (ga.PENDING_TTL_DAYS + 1) * 86400
            backlog.save_all(all_c)
            n = backlog.expire_stale()
            self.assertEqual(n, 1)
            self.assertEqual(backlog.get(c.candidate_id).status, ga.STATUS_EXPIRED)


class TestReportGeneration(unittest.TestCase):
    def test_template_report_generated_and_indexed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                "前端与可视化", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            report = ga.generate_growth_report(paths, cand)
            self.assertEqual(report.source, "template")
            self.assertTrue(Path(report.body_path).exists())
            self.assertIn("前端与可视化", Path(report.body_path).read_text(encoding="utf-8"))

            reports = ga.list_reports(paths)
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0].report_id, report.report_id)

            refreshed = backlog.get(cand.candidate_id)
            self.assertEqual(refreshed.report_id, report.report_id)

    def test_llm_helper_used_when_provided(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                "项目管理", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            report = ga.generate_growth_report(
                paths, cand, llm_helper=lambda prompt: "# LLM 生成的报告正文"
            )
            self.assertEqual(report.source, "llm")
            self.assertIn("LLM 生成的报告正文", Path(report.body_path).read_text(encoding="utf-8"))

    def test_llm_helper_failure_falls_back_to_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                "写作与表达", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )

            def _boom(prompt):
                raise RuntimeError("llm unavailable")

            report = ga.generate_growth_report(paths, cand, llm_helper=_boom)
            self.assertEqual(report.source, "template")


class TestDailyCycleAndRetrospective(unittest.TestCase):
    def test_run_daily_cycle_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            entries = [
                _FakeEntry(f"e{i}", "python packaging pytest", ["python"], now - 10)
                for i in range(4)
            ]
            store = _FakeMemoryStore(entries)
            profile = UserProfile()
            cfg = GrowthAdvisorConfig(min_evidence_count=3, max_reports_per_run=2)

            result = ga.run_daily_cycle(paths, cfg, profile, store)

            self.assertFalse(result["skipped"])
            self.assertEqual(len(result["new_candidates"]), 1)
            self.assertEqual(len(result["reports"]), 1)

    def test_run_daily_cycle_disabled_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(enabled=False)
            profile = UserProfile()
            result = ga.run_daily_cycle(paths, cfg, profile, None)
            self.assertTrue(result["skipped"])

    def test_monthly_retrospective_summary_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c1 = backlog.add_or_merge(
                "写作与表达", "r", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            c2 = backlog.add_or_merge(
                "数据分析", "r", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(c1.candidate_id, ga.STATUS_ACCEPTED)
            backlog.set_status(c2.candidate_id, ga.STATUS_DISMISSED)
            ga.GrowthFeedbackLedger(paths).record(c1.candidate_id, "accepted")
            ga.GrowthFeedbackLedger(paths).record(c2.candidate_id, "dismissed")

            summary = ga.monthly_retrospective_summary(paths)
            self.assertEqual(summary["total_candidates"], 2)
            self.assertEqual(summary["accepted"], 1)
            self.assertEqual(summary["dismissed"], 1)
            self.assertEqual(summary["feedback_events"], 2)


class TestFeedbackWeighting(unittest.TestCase):
    """P2：反馈驱动的置信度调权（growth_advisor_design.md 第 6 节）。"""

    def test_dismissed_topic_regenerated_after_cooldown_with_lower_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "系统设计与架构", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            baseline_confidence = c.confidence
            backlog.set_status(c.candidate_id, ga.STATUS_DISMISSED)
            ga.GrowthFeedbackLedger(paths).record(c.candidate_id, ga.STATUS_DISMISSED)

            # 冷却期已过（把 updated_at 拨回 31 天前），应该重新生成候选，
            # 但因为历史上被 dismiss 过一次，默认置信度应低于同等证据数
            # 首次生成时的置信度。
            all_c = backlog.load_all()
            all_c[0].updated_at = time.time() - 31 * 86400
            backlog.save_all(all_c)

            multiplier = ga._feedback_multiplier(1)
            again = backlog.add_or_merge(
                "系统设计与架构", "新理由", ["e4", "e5", "e6"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
                confidence_multiplier=multiplier,
            )
            self.assertIsNotNone(again)
            self.assertLess(again.confidence, baseline_confidence)
            self.assertGreater(again.confidence, 0)

    def test_feedback_multiplier_has_floor(self):
        # 多次 dismiss 也不应该把置信度乘子打到 0（方案第 6 节：不是完全
        # 屏蔽，避免"用户当时忙、后来又感兴趣"被永久拒绝）。
        m = ga._feedback_multiplier(20)
        self.assertGreaterEqual(m, ga._MIN_FEEDBACK_MULTIPLIER)
        self.assertEqual(ga._feedback_multiplier(0), 1.0)

    def test_candidate_derive_applies_feedback_multiplier(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            old = backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(old.candidate_id, ga.STATUS_DISMISSED)
            all_c = backlog.load_all()
            all_c[0].updated_at = time.time() - 31 * 86400
            backlog.save_all(all_c)
            ga.GrowthFeedbackLedger(paths).record(old.candidate_id, ga.STATUS_DISMISSED)

            cfg = GrowthAdvisorConfig(min_evidence_count=3)
            profile = UserProfile()
            profile.derived = {"growth_focus_areas": {"数据分析": ["e4", "e5", "e6"]}}
            produced = ga.growth_candidate_derive(paths, cfg, profile)
            self.assertEqual(len(produced), 1)
            self.assertLess(produced[0].confidence, ga._confidence_from_evidence(3))


class TestNotificationThrottle(unittest.TestCase):
    """P2：推送节流接入 NotificationDispatcher（growth_advisor_design.md 第 4.2 节）。"""

    def test_kanban_only_never_dispatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            report = ga.generate_growth_report(paths, cand)
            cfg = GrowthAdvisorConfig(notification_frequency="kanban_only")
            result = ga._maybe_dispatch_notification(paths, cfg, {cand.candidate_id: cand}, [report])
            self.assertIsNone(result)

    def test_below_min_confidence_not_dispatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3"],  # 证据数少 -> 置信度低
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            report = ga.generate_growth_report(paths, cand)
            cfg = GrowthAdvisorConfig(notification_min_confidence=0.99)
            result = ga._maybe_dispatch_notification(paths, cfg, {cand.candidate_id: cand}, [report])
            self.assertIsNone(result)

    def test_high_confidence_dispatches_and_respects_daily_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                "数据分析", "理由", [f"e{i}" for i in range(8)],  # 满 cap -> confidence=1.0
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            report = ga.generate_growth_report(paths, cand)
            cfg = GrowthAdvisorConfig(notification_min_confidence=0.5, notification_max_per_day=1)

            first = ga._maybe_dispatch_notification(paths, cfg, {cand.candidate_id: cand}, [report])
            self.assertIsNotNone(first)
            self.assertEqual(first["report_id"], report.report_id)

            second = ga._maybe_dispatch_notification(paths, cfg, {cand.candidate_id: cand}, [report])
            self.assertIsNone(second)  # 当天已达上限

    def test_run_daily_cycle_includes_notification_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            entries = [
                _FakeEntry(f"e{i}", "python packaging pytest", ["python"], now - 10)
                for i in range(8)
            ]
            store = _FakeMemoryStore(entries)
            profile = UserProfile()
            cfg = GrowthAdvisorConfig(min_evidence_count=3, max_reports_per_run=1, notification_min_confidence=0.1)
            result = ga.run_daily_cycle(paths, cfg, profile, store)
            self.assertIn("notification", result)
            self.assertIsNotNone(result["notification"])


class TestWeeklyDigest(unittest.TestCase):
    """P3：`notification_frequency=weekly_digest` 的真实周摘要打包。"""

    def _make_report(self, paths, title, evidence_n=5):
        backlog = ga.GrowthBacklog(paths)
        cand = backlog.add_or_merge(
            title, "理由", [f"e{i}" for i in range(evidence_n)],
            min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
        )
        return ga.generate_growth_report(paths, cand)

    def test_first_call_packages_all_recent_reports_into_one_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            r1 = self._make_report(paths, "数据分析")
            r2 = self._make_report(paths, "系统设计与架构")

            result = ga._maybe_dispatch_weekly_digest(paths, GrowthAdvisorConfig())
            self.assertIsNotNone(result)
            self.assertEqual(result["count"], 2)
            self.assertEqual(set(result["report_ids"]), {r1.report_id, r2.report_id})

    def test_second_call_within_7_days_is_throttled(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._make_report(paths, "数据分析")
            first = ga._maybe_dispatch_weekly_digest(paths, GrowthAdvisorConfig())
            self.assertIsNotNone(first)

            self._make_report(paths, "系统设计与架构")
            second = ga._maybe_dispatch_weekly_digest(paths, GrowthAdvisorConfig())
            self.assertIsNone(second)  # 距上次推送不满 7 天

    def test_no_new_reports_in_window_skips_without_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = ga._maybe_dispatch_weekly_digest(paths, GrowthAdvisorConfig())
            self.assertIsNone(result)

    def test_ready_again_after_interval_elapses(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._make_report(paths, "数据分析")
            first = ga._maybe_dispatch_weekly_digest(paths, GrowthAdvisorConfig())
            self.assertIsNotNone(first)

            # 模拟 7 天已过：直接回拨状态里的时间戳
            state = ga._load_growth_state(paths)
            state["last_weekly_digest_at"] = time.time() - (ga.WEEKLY_DIGEST_INTERVAL_DAYS + 1) * 86400
            ga._save_growth_state(paths, state)

            self._make_report(paths, "系统设计与架构")
            second = ga._maybe_dispatch_weekly_digest(paths, GrowthAdvisorConfig())
            self.assertIsNotNone(second)
            # 窗口起点被回拨到早于两份报告的创建时间，两份都落在窗口内
            self.assertEqual(second["count"], 2)

    def test_run_daily_cycle_routes_weekly_digest_freq_to_weekly_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            entries = [
                _FakeEntry(f"e{i}", "python packaging pytest", ["python"], now - 10)
                for i in range(8)
            ]
            store = _FakeMemoryStore(entries)
            profile = UserProfile()
            cfg = GrowthAdvisorConfig(
                min_evidence_count=3, max_reports_per_run=1,
                notification_frequency="weekly_digest",
            )
            result = ga.run_daily_cycle(paths, cfg, profile, store)
            self.assertIsNotNone(result["notification"])
            self.assertIn("count", result["notification"])

    def test_daily_path_never_triggers_weekly_digest_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            report = self._make_report(paths, "数据分析", evidence_n=8)
            cand = ga.GrowthBacklog(paths).get(report.candidate_id)
            cfg = GrowthAdvisorConfig(notification_frequency="weekly_digest", notification_min_confidence=0.1)
            # _maybe_dispatch_notification 应对 weekly_digest 短路返回 None，
            # 防止调用方接错分支时被误当成 daily 逐条推送。
            result = ga._maybe_dispatch_notification(paths, cfg, {cand.candidate_id: cand}, [report])
            self.assertIsNone(result)


class TestMonthlyRetrospectiveAttribution(unittest.TestCase):
    """P2：月度复盘的采纳率与主题排行。"""

    def test_acceptance_rate_and_topic_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c1 = backlog.add_or_merge(
                "写作与表达", "r", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            c2 = backlog.add_or_merge(
                "数据分析", "r", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(c1.candidate_id, ga.STATUS_ACCEPTED)
            backlog.set_status(c2.candidate_id, ga.STATUS_DISMISSED)

            summary = ga.monthly_retrospective_summary(paths)
            self.assertEqual(summary["acceptance_rate"], 0.5)
            self.assertIn(("写作与表达", 1), summary["top_accepted_topics"])
            self.assertIn(("数据分析", 1), summary["top_dismissed_topics"])

    def test_acceptance_rate_none_when_no_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            summary = ga.monthly_retrospective_summary(paths)
            self.assertIsNone(summary["acceptance_rate"])


class TestGrowthTopicMap(unittest.TestCase):
    """P3：月度复盘的跨候选能力地图聚合（growth_topic_map）。"""

    def test_empty_backlog_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(ga.growth_topic_map(paths), [])

    def test_single_topic_aggregates_current_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                "数据分析", "r", ["e1", "e2", "e3", "e4"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(cand.candidate_id, ga.STATUS_ACCEPTED)

            rows = ga.growth_topic_map(paths)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["topic"], "数据分析")
            self.assertEqual(row["current_status"], ga.STATUS_ACCEPTED)
            self.assertEqual(row["times_accepted"], 1)
            self.assertEqual(row["times_dismissed"], 0)
            self.assertEqual(row["occurrences"], 1)

    def test_repeated_dismiss_and_regenerate_accumulates_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            # 第一轮：生成后被 dismiss
            c1 = backlog.add_or_merge(
                "系统设计与架构", "r", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(c1.candidate_id, ga.STATUS_DISMISSED)
            # 模拟冷却期已过：直接改 updated_at 到很久以前，再次生成会
            # 走"曾 dismissed 但已出冷却期"分支，产生第二条同标题记录
            all_c = backlog.load_all()
            for c in all_c:
                if c.candidate_id == c1.candidate_id:
                    c.updated_at = time.time() - 40 * 86400
            backlog.save_all(all_c)

            c2 = backlog.add_or_merge(
                "系统设计与架构", "r", ["e1", "e2", "e3", "e4", "e5"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            self.assertIsNotNone(c2)
            self.assertNotEqual(c1.candidate_id, c2.candidate_id)
            backlog.set_status(c2.candidate_id, ga.STATUS_ACCEPTED)

            rows = ga.growth_topic_map(paths)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["occurrences"], 2)
            self.assertEqual(row["times_dismissed"], 1)
            self.assertEqual(row["times_accepted"], 1)
            self.assertEqual(row["current_status"], ga.STATUS_ACCEPTED)
            self.assertGreaterEqual(row["peak_confidence"], row["current_confidence"])

    def test_included_in_monthly_retrospective_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            backlog.add_or_merge(
                "写作与表达", "r", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            summary = ga.monthly_retrospective_summary(paths)
            self.assertIn("topic_map", summary)
            self.assertEqual(len(summary["topic_map"]), 1)
            self.assertEqual(summary["topic_map"][0]["topic"], "写作与表达")


class TestFirstTouchNotice(unittest.TestCase):
    """P3：首次触达提示的跨会话持久化。"""

    def test_not_shown_initially(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertFalse(ga.first_touch_notice_shown(paths))

    def test_mark_shown_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ga.mark_first_touch_notice_shown(paths)
            self.assertTrue(ga.first_touch_notice_shown(paths))
            # 幂等：重复调用不报错，状态保持已展示
            ga.mark_first_touch_notice_shown(paths)
            self.assertTrue(ga.first_touch_notice_shown(paths))

    def test_shares_state_file_with_notification_throttle(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                "数据分析", "理由", [f"e{i}" for i in range(8)],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            report = ga.generate_growth_report(paths, cand)
            cfg = GrowthAdvisorConfig(notification_min_confidence=0.5)
            ga._maybe_dispatch_notification(paths, cfg, {cand.candidate_id: cand}, [report])
            ga.mark_first_touch_notice_shown(paths)
            # 两类状态同时落在 growth_advisor_state.json 里，互不覆盖
            state = ga._load_growth_state(paths)
            self.assertEqual(state.get("notify_count_today"), 1)
            self.assertTrue(state.get("first_touch_notice_shown"))


class TestCronJobsRegistered(unittest.TestCase):
    def test_builtin_jobs_include_growth_advisor(self):
        from mini_agent.evolution.cron_scheduler import _BUILTIN_JOBS

        ids = {j["id"] for j in _BUILTIN_JOBS}
        self.assertIn(ga.JOB_ID_DAILY, ids)
        self.assertIn(ga.JOB_ID_MONTHLY, ids)


class TestLlmSignalAugment(unittest.TestCase):
    """P3：growth_signal_scan 的 LLM 增强版归纳（默认关闭，opt-in）。"""

    def _entries(self, now):
        return [
            # 规则命中：Python 工程实践
            _FakeEntry("e1", "python packaging 踩坑记录", ["python"], now - 10),
            # 规则命中不到，但反复出现，指向同一个新主题
            _FakeEntry("e2", "又研究了一下摄影构图", [], now - 20),
            _FakeEntry("e3", "看了一篇讲摄影用光的文章", [], now - 30),
            _FakeEntry("e4", "周末去拍了张照片练手", [], now - 40),
        ]

    def test_no_llm_helper_keeps_rule_only_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            store = _FakeMemoryStore(self._entries(now))
            profile = UserProfile()

            hits = ga.growth_signal_scan(paths, profile, store)
            self.assertIn("Python 工程实践", hits)
            self.assertNotIn("摄影", hits)  # 没传 llm_helper，规则表覆盖不到

    def test_llm_helper_adds_new_topic_from_unmatched_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            store = _FakeMemoryStore(self._entries(now))
            profile = UserProfile()

            def fake_llm(prompt):
                return json.dumps([{"topic": "摄影", "entry_ids": ["e2", "e3", "e4"]}])

            hits = ga.growth_signal_scan(paths, profile, store, llm_helper=fake_llm)
            self.assertIn("Python 工程实践", hits)  # 规则结果保留
            self.assertIn("摄影", hits)
            self.assertEqual(sorted(hits["摄影"]), ["e2", "e3", "e4"])

    def test_llm_cannot_hallucinate_entry_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            store = _FakeMemoryStore(self._entries(now))
            profile = UserProfile()

            def fake_llm(prompt):
                # e999 不在提供的候选集合里，必须被过滤掉
                return json.dumps([{"topic": "摄影", "entry_ids": ["e2", "e999"]}])

            hits = ga.growth_signal_scan(paths, profile, store, llm_helper=fake_llm)
            self.assertEqual(hits.get("摄影"), ["e2"])

    def test_malformed_llm_output_falls_back_to_rule_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            store = _FakeMemoryStore(self._entries(now))
            profile = UserProfile()

            def broken_llm(prompt):
                return "不是 JSON，纯胡说"

            hits = ga.growth_signal_scan(paths, profile, store, llm_helper=broken_llm)
            self.assertIn("Python 工程实践", hits)
            self.assertNotIn("摄影", hits)

    def test_llm_exception_does_not_break_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            store = _FakeMemoryStore(self._entries(now))
            profile = UserProfile()

            def boom(prompt):
                raise RuntimeError("llm down")

            hits = ga.growth_signal_scan(paths, profile, store, llm_helper=boom)
            self.assertIn("Python 工程实践", hits)

    def test_too_few_unmatched_entries_skips_llm_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            # 只有 1 条命中不到规则表的条目，低于 _LLM_AUGMENT_MIN_UNMATCHED
            entries = [
                _FakeEntry("e1", "python packaging 踩坑记录", ["python"], now - 10),
                _FakeEntry("e2", "唯一一条没归类的杂事", [], now - 20),
            ]
            store = _FakeMemoryStore(entries)
            profile = UserProfile()
            called = {"n": 0}

            def fake_llm(prompt):
                called["n"] += 1
                return "[]"

            ga.growth_signal_scan(paths, profile, store, llm_helper=fake_llm)
            self.assertEqual(called["n"], 0)

    def test_new_topic_merges_with_existing_rule_topic_on_normalized_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            entries = self._entries(now) + [
                _FakeEntry("e5", "python 相关的边角料，规则表没命中的措辞", [], now - 15),
                _FakeEntry("e6", "又一条同类边角料", [], now - 25),
                _FakeEntry("e7", "再来一条凑够未命中阈值", [], now - 35),
            ]
            store = _FakeMemoryStore(entries)
            profile = UserProfile()

            def fake_llm(prompt):
                # LLM 归纳出的主题名跟规则表 key 完全一致（大小写/空白不同也应合并）
                return json.dumps([{"topic": "Python 工程实践", "entry_ids": ["e5", "e6", "e7"]}])

            hits = ga.growth_signal_scan(paths, profile, store, llm_helper=fake_llm)
            # 合并进同一个 key，不产生重复主题
            self.assertEqual(len([k for k in hits if ga.normalize_title_key(k) == ga.normalize_title_key("Python 工程实践")]), 1)
            self.assertIn("e5", hits["Python 工程实践"])
            self.assertIn("e1", hits["Python 工程实践"])

    def test_run_daily_cycle_gates_llm_augment_by_config_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            store = _FakeMemoryStore(self._entries(now))
            profile = UserProfile()
            called = {"n": 0}

            def fake_llm(prompt):
                called["n"] += 1
                return json.dumps([{"topic": "摄影", "entry_ids": ["e2", "e3", "e4"]}])

            # 默认 llm_signal_augment_enabled=False，即使传了 llm_helper 也不会调用
            cfg_off = GrowthAdvisorConfig(min_evidence_count=3)
            ga.run_daily_cycle(paths, cfg_off, profile, store, llm_helper=fake_llm)
            self.assertEqual(called["n"], 0)

            # 显式打开后才会调用
            cfg_on = GrowthAdvisorConfig(min_evidence_count=3, llm_signal_augment_enabled=True)
            ga.run_daily_cycle(paths, cfg_on, profile, store, llm_helper=fake_llm)
            self.assertGreaterEqual(called["n"], 1)


class TestDiagnosticsSnapshot(unittest.TestCase):
    """诊断快照——用户反馈"运行了一天数据都是 0"排查用的自检信息。"""

    def test_snapshot_before_any_scan_shows_empty_but_valid_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            cfg = GrowthAdvisorConfig()
            snap = ga.diagnostics_snapshot(paths, cfg, profile, None)
            self.assertIn("config", snap)
            self.assertIn("signal_scan", snap)
            self.assertIn("memory", snap)
            self.assertIsNone(snap["signal_scan"]["last_scan_at"])
            self.assertEqual(snap["signal_scan"]["topic_hit_counts"], {})
            self.assertEqual(snap["memory"]["total_entries"], 0)

    def test_snapshot_reflects_config_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            cfg = GrowthAdvisorConfig(
                enabled=False, min_evidence_count=5,
                notification_frequency="kanban_only",
                excluded_topics=["写作与表达"],
            )
            snap = ga.diagnostics_snapshot(paths, cfg, profile, None)
            self.assertFalse(snap["config"]["enabled"])
            self.assertEqual(snap["config"]["min_evidence_count"], 5)
            self.assertEqual(snap["config"]["notification_frequency"], "kanban_only")
            self.assertEqual(snap["config"]["excluded_topics"], ["写作与表达"])

    def test_snapshot_after_scan_shows_topic_hit_counts_and_memory_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            entries = [
                _FakeEntry("e1", "python packaging", ["python"], now - 10),
                _FakeEntry("e2", "pytest fixture", ["python"], now - 20),
                _FakeEntry("e3", "无关的杂事", [], now - 30),
                _FakeEntry("e4", "太老的记录", [], now - 200 * 86400),  # 窗口外
            ]
            store = _FakeMemoryStore(entries)
            profile = UserProfile()
            cfg = GrowthAdvisorConfig()

            ga.growth_signal_scan(paths, profile, store)
            snap = ga.diagnostics_snapshot(paths, cfg, profile, store)

            self.assertIsNotNone(snap["signal_scan"]["last_scan_at"])
            self.assertEqual(snap["signal_scan"]["topic_hit_counts"].get("Python 工程实践"), 2)
            self.assertEqual(snap["memory"]["total_entries"], 4)
            self.assertEqual(snap["memory"]["entries_in_scan_window"], 3)  # e4 在窗口外

    def test_snapshot_does_not_leak_raw_entry_ids_or_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            store = _FakeMemoryStore([_FakeEntry("secret-entry-id-xyz", "python", ["python"], now - 10)])
            profile = UserProfile()
            ga.growth_signal_scan(paths, profile, store)
            snap = ga.diagnostics_snapshot(paths, GrowthAdvisorConfig(), profile, store)
            snap_str = json.dumps(snap, ensure_ascii=False)
            self.assertNotIn("secret-entry-id-xyz", snap_str)

    def test_snapshot_handles_none_memory_store_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            snap = ga.diagnostics_snapshot(paths, GrowthAdvisorConfig(), profile, None)
            self.assertEqual(snap["memory"]["total_entries"], 0)


class TestPersistedKeywords(unittest.TestCase):
    """P4-1（next_doc/growth_advisor_improvement_plan_v2.md）：关键词表
    持久化——_effective_topic_keywords 合并/排除逻辑、LLM 归纳新主题写入
    profile、用户增删改关键词、confirmed_by_user 状态流转。"""

    def test_effective_keywords_merges_builtin_and_custom(self):
        profile = UserProfile()
        ga.add_custom_topic_keyword(profile, "摄影", "摄影, 构图,用光")
        effective = ga._effective_topic_keywords(profile)
        self.assertIn("Python 工程实践", effective)
        self.assertEqual(effective["Python 工程实践"]["source"], "built_in")
        self.assertTrue(effective["Python 工程实践"]["confirmed_by_user"])
        self.assertIn("摄影", effective)
        self.assertEqual(effective["摄影"]["keywords"], ["摄影", "构图", "用光"])
        self.assertEqual(effective["摄影"]["source"], "user_added")
        self.assertTrue(effective["摄影"]["confirmed_by_user"])

    def test_remove_topic_keyword_hides_builtin_and_removes_custom(self):
        profile = UserProfile()
        ga.add_custom_topic_keyword(profile, "摄影", ["摄影"])
        self.assertTrue(ga.remove_topic_keyword(profile, "项目管理"))
        self.assertTrue(ga.remove_topic_keyword(profile, "摄影"))
        effective = ga._effective_topic_keywords(profile)
        self.assertNotIn("项目管理", effective)
        self.assertNotIn("摄影", effective)
        # 幂等：再次删除不存在的主题不报错，返回 False
        self.assertFalse(ga.remove_topic_keyword(profile, "摄影"))

    def test_add_custom_topic_keyword_rejects_empty_input(self):
        profile = UserProfile()
        with self.assertRaises(ValueError):
            ga.add_custom_topic_keyword(profile, "", ["x"])
        with self.assertRaises(ValueError):
            ga.add_custom_topic_keyword(profile, "topic", [])

    def test_llm_augmented_topic_persists_to_profile_and_is_unconfirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            entries = [
                _FakeEntry(f"e{i}", "聊了摄影构图和用光技巧", [], now - i)
                for i in range(5)
            ]
            store = _FakeMemoryStore(entries)
            profile = UserProfile()

            def fake_llm(prompt: str) -> str:
                ids = [f"e{i}" for i in range(5)]
                return json.dumps([{"topic": "摄影", "entry_ids": ids}])

            ga.growth_signal_scan(paths, profile, store, llm_helper=fake_llm)
            custom = profile.derived.get("growth_topic_keywords", {})
            self.assertIn("摄影", custom)
            self.assertEqual(custom["摄影"]["source"], "llm_learned")
            self.assertFalse(custom["摄影"]["confirmed_by_user"])

            # 下次规则扫描（无 llm_helper）也能命中，因为已经持久化进关键词表
            profile2 = UserProfile(derived=dict(profile.derived))
            hits = ga.growth_signal_scan(paths, profile2, store)
            self.assertIn("摄影", hits)

    def test_confirm_topic_keyword_flips_flag_once(self):
        profile = UserProfile()
        ga._persist_learned_topics(profile, {"摄影": ["e1", "e2"]})
        self.assertFalse(profile.derived["growth_topic_keywords"]["摄影"]["confirmed_by_user"])
        self.assertTrue(ga.confirm_topic_keyword(profile, "摄影"))
        self.assertTrue(profile.derived["growth_topic_keywords"]["摄影"]["confirmed_by_user"])
        # 已经确认过，再次确认返回 False（无变化）
        self.assertFalse(ga.confirm_topic_keyword(profile, "摄影"))
        # 内置/不存在的主题是安全的空操作
        self.assertFalse(ga.confirm_topic_keyword(profile, "Python 工程实践"))
        self.assertFalse(ga.confirm_topic_keyword(profile, "不存在的主题"))

    def test_diagnostics_snapshot_exposes_topics_detail_and_user_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile(derived={
                "summary": "热衷 Python 后端开发",
                "tech_stack": ["python", "fastapi"],
                "habits": ["喜欢写测试"],
                "updated_at": time.time(),
            })
            ga.add_custom_topic_keyword(profile, "摄影", ["摄影"])
            snap = ga.diagnostics_snapshot(paths, GrowthAdvisorConfig(), profile, None)
            topics_detail = snap["signal_scan"]["topics_detail"]
            sources = {t["topic"]: t["source"] for t in topics_detail}
            self.assertEqual(sources.get("摄影"), "user_added")
            self.assertEqual(sources.get("Python 工程实践"), "built_in")
            self.assertEqual(snap["user_profile"]["summary"], "热衷 Python 后端开发")
            self.assertEqual(snap["user_profile"]["tech_stack"], ["python", "fastapi"])
            self.assertNotIn("preferences", snap["user_profile"])


class TestKeywordAutoConfirmStreak(unittest.TestCase):
    """P4-2（next_doc/growth_advisor_improvement_plan_v2.md 第 4 节）：
    待确认的 llm_learned 主题连续多次扫描都有命中后自动转正。"""

    def _scan_with_topic_hit(self, paths, profile, hit: bool):
        now = time.time()
        if hit:
            entries = [
                _FakeEntry(f"e{i}", "又聊到了摄影构图和用光", [], now - i)
                for i in range(3)
            ]
        else:
            entries = [
                _FakeEntry(f"e{i}", "今天只是聊了下午饭吃什么", [], now - i)
                for i in range(3)
            ]
        store = _FakeMemoryStore(entries)

        def fake_llm(prompt: str) -> str:
            if hit:
                return json.dumps([{"topic": "摄影", "entry_ids": [e.entry_id for e in entries]}])
            return json.dumps([])

        return ga.growth_signal_scan(paths, profile, store, llm_helper=fake_llm)

    def test_auto_confirms_after_consecutive_hits(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            # 第 1、2 次扫描命中：streak 累积到 2，尚未转正
            self._scan_with_topic_hit(paths, profile, hit=True)
            self._scan_with_topic_hit(paths, profile, hit=True)
            entry = profile.derived["growth_topic_keywords"]["摄影"]
            self.assertFalse(entry["confirmed_by_user"])
            self.assertEqual(entry["consecutive_scan_hits"], 2)

            # 第 3 次扫描命中：达到阈值 3，自动转正
            self._scan_with_topic_hit(paths, profile, hit=True)
            entry = profile.derived["growth_topic_keywords"]["摄影"]
            self.assertTrue(entry["confirmed_by_user"])
            self.assertTrue(entry["auto_confirmed"])
            self.assertEqual(entry["consecutive_scan_hits"], 0)

    def test_streak_resets_on_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            self._scan_with_topic_hit(paths, profile, hit=True)
            self._scan_with_topic_hit(paths, profile, hit=True)
            self.assertEqual(
                profile.derived["growth_topic_keywords"]["摄影"]["consecutive_scan_hits"], 2
            )
            # 未命中一次，streak 清零，即使主题仍然存在（关键词表兜底命中）
            with tempfile.TemporaryDirectory():
                pass
            store = _FakeMemoryStore([])
            ga.growth_signal_scan(paths, profile, store)
            self.assertEqual(
                profile.derived["growth_topic_keywords"]["摄影"]["consecutive_scan_hits"], 0
            )
            self.assertFalse(profile.derived["growth_topic_keywords"]["摄影"]["confirmed_by_user"])

    def test_manually_confirmed_topic_does_not_track_streak(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            ga.add_custom_topic_keyword(profile, "摄影", ["摄影"])  # user_added，直接已确认
            store = _FakeMemoryStore(
                [_FakeEntry("e1", "又聊到了摄影构图", [], time.time() - 1)]
            )
            ga.growth_signal_scan(paths, profile, store)
            entry = profile.derived["growth_topic_keywords"]["摄影"]
            # user_added 主题不参与 streak 计数，字段保持初始值
            self.assertEqual(entry.get("consecutive_scan_hits", 0), 0)
            self.assertFalse(entry.get("auto_confirmed", False))


class TestCategoryFeedbackWeighting(unittest.TestCase):
    """P4-3 第一条：同一类别下连续忽略多个主题，应该温和拖累同类新主题的
    初始置信度（比单主题衰减更温和，也不叠加进同一个乘子）。"""

    def test_category_multiplier_floor_and_identity(self):
        self.assertEqual(ga._category_feedback_multiplier(0), 1.0)
        m = ga._category_feedback_multiplier(50)
        self.assertGreaterEqual(m, ga._MIN_CATEGORY_MULTIPLIER)

    def test_category_of_maps_builtin_and_falls_back(self):
        self.assertEqual(ga._category_of("项目管理"), "管理类")
        self.assertEqual(ga._category_of("写作与表达"), "表达类")
        self.assertEqual(ga._category_of("Python 工程实践"), "技术类")
        self.assertEqual(ga._category_of("摄影"), "其他类")

    def test_dismissing_one_technical_topic_lowers_new_technical_topic_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            # 忽略"数据分析"（技术类），不做冷却处理，只关心它对同类别
            # 新主题"前端与可视化"的影响。
            old = backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(old.candidate_id, ga.STATUS_DISMISSED)
            ga.GrowthFeedbackLedger(paths).record(old.candidate_id, ga.STATUS_DISMISSED)

            cfg = GrowthAdvisorConfig(min_evidence_count=3)
            profile = UserProfile()
            profile.derived = {
                "growth_focus_areas": {"前端与可视化": ["e4", "e5", "e6"]}
            }
            produced = ga.growth_candidate_derive(paths, cfg, profile)
            self.assertEqual(len(produced), 1)
            # 同类别但不同主题：没有单主题 dismiss 记录，只受类别乘子影响，
            # 所以置信度应低于满分但高于"直接被 dismiss 过的同一主题"。
            self.assertLess(produced[0].confidence, ga._confidence_from_evidence(3))
            self.assertGreater(produced[0].confidence, 0)

    def test_dismissing_other_category_topic_does_not_affect(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            old = backlog.add_or_merge(
                "项目管理", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(old.candidate_id, ga.STATUS_DISMISSED)
            ga.GrowthFeedbackLedger(paths).record(old.candidate_id, ga.STATUS_DISMISSED)

            cfg = GrowthAdvisorConfig(min_evidence_count=3)
            profile = UserProfile()
            profile.derived = {"growth_focus_areas": {"数据分析": ["e4", "e5", "e6"]}}
            produced = ga.growth_candidate_derive(paths, cfg, profile)
            self.assertEqual(len(produced), 1)
            # "管理类"被忽略，不应该影响"技术类"新主题的置信度。
            self.assertEqual(produced[0].confidence, ga._confidence_from_evidence(3))


class TestAdoptionFollowup(unittest.TestCase):
    """P4-3 第二条：采纳后回访（progressed/stalled）。"""

    def test_accepted_at_set_on_first_transition_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "写作与表达", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            accepted = backlog.set_status(c.candidate_id, ga.STATUS_ACCEPTED)
            self.assertIsNotNone(accepted.accepted_at)
            first_ts = accepted.accepted_at
            backlog.attach_report(c.candidate_id, "report-1")
            reloaded = backlog.get(c.candidate_id)
            # attach_report 会更新 updated_at，但不应该改动 accepted_at。
            self.assertEqual(reloaded.accepted_at, first_ts)

    def test_pending_followups_respects_window_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            old = backlog.add_or_merge(
                "写作与表达", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(old.candidate_id, ga.STATUS_ACCEPTED)
            recent = backlog.add_or_merge(
                "数据分析", "理由", ["e4", "e5", "e6"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(recent.candidate_id, ga.STATUS_ACCEPTED)

            all_c = backlog.load_all()
            for c in all_c:
                if c.candidate_id == old.candidate_id:
                    c.accepted_at = time.time() - 31 * 86400  # 早于 30 天窗口
            backlog.save_all(all_c)

            cfg = GrowthAdvisorConfig(followup_review_days=30)
            due = ga.pending_followups(paths, cfg)
            due_ids = {c.candidate_id for c in due}
            self.assertIn(old.candidate_id, due_ids)
            self.assertNotIn(recent.candidate_id, due_ids)  # 刚采纳，未到窗口

    def test_record_followup_persists_and_ledger_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "写作与表达", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(c.candidate_id, ga.STATUS_ACCEPTED)
            updated = ga.record_followup(paths, c.candidate_id, "progressed")
            self.assertEqual(updated.followup_status, "progressed")
            entries = ga.GrowthFeedbackLedger(paths).all_entries()
            self.assertTrue(any(e.get("action") == "followup_progressed" for e in entries))
            # 回答过一次后不再出现在待回访列表里。
            due = ga.pending_followups(paths, GrowthAdvisorConfig())
            self.assertNotIn(c.candidate_id, {x.candidate_id for x in due})

    def test_record_followup_rejects_invalid_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            with self.assertRaises(ValueError):
                ga.record_followup(paths, "does-not-matter", "not-a-real-outcome")

    def test_followup_adjustment_stalled_and_progressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "写作与表达", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(c.candidate_id, ga.STATUS_ACCEPTED)
            ga.record_followup(paths, c.candidate_id, "stalled")
            adj = ga._followup_adjustment_by_dedupe_key(paths)
            self.assertLess(adj[c.dedupe_key()], 1.0)


class TestNotificationCategoryAndPriority(unittest.TestCase):
    """P4-5：类别静音 + 优先级分数（置信度 × 类别历史采纳率）。"""

    def test_category_notification_muted_only_recognizes_kanban_only(self):
        cfg = GrowthAdvisorConfig(category_notification_frequency={"技术类": "kanban_only"})
        self.assertTrue(ga._category_notification_muted(cfg, "数据分析"))  # 技术类
        self.assertFalse(ga._category_notification_muted(cfg, "项目管理"))  # 管理类，未覆盖
        cfg2 = GrowthAdvisorConfig(category_notification_frequency={"技术类": "daily"})
        # 目前只识别 kanban_only，其余值等价于未设置覆盖
        self.assertFalse(ga._category_notification_muted(cfg2, "数据分析"))

    def test_muted_category_never_dispatches_even_with_high_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                "数据分析", "理由", [f"e{i}" for i in range(8)],  # confidence=1.0
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            report = ga.generate_growth_report(paths, cand)
            cfg = GrowthAdvisorConfig(
                notification_min_confidence=0.1,
                category_notification_frequency={"技术类": "kanban_only"},
            )
            result = ga._maybe_dispatch_notification(paths, cfg, {cand.candidate_id: cand}, [report])
            self.assertIsNone(result)

    def test_priority_score_prefers_high_acceptance_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            # "技术类"历史上全部被采纳（用另一个技术类主题积累历史，
            # 避免跟下面的新候选撞同一个 dedupe_key 触发合并而不是新建）。
            good_history = backlog.add_or_merge(
                "数据分析", "理由", ["g1", "g2", "g3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(good_history.candidate_id, ga.STATUS_ACCEPTED)
            # "管理类"历史上全部被忽略（管理类内置只有"项目管理"一个主题，
            # 冷却期过后重新生成时是"替换"而不是"合并"，所以直接复用同一
            # 标题也没问题）。
            bad_history = backlog.add_or_merge(
                "项目管理", "理由", ["b1", "b2", "b3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(bad_history.candidate_id, ga.STATUS_DISMISSED)
            ga.GrowthFeedbackLedger(paths).record(bad_history.candidate_id, ga.STATUS_DISMISSED)
            all_c = backlog.load_all()
            for c in all_c:
                if c.candidate_id == bad_history.candidate_id:
                    c.updated_at = time.time() - 31 * 86400  # 冷却期已过
            backlog.save_all(all_c)

            # 两个新候选证据数相同（置信度相同）：一个技术类（历史高采纳），
            # 一个管理类（历史全忽略）。
            cand_good = backlog.add_or_merge(
                "前端与可视化", "理由2", ["h1", "h2", "h3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            cand_bad = backlog.add_or_merge(
                "项目管理", "理由2", ["k1", "k2", "k3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            self.assertIsNotNone(cand_good)
            self.assertIsNotNone(cand_bad)
            self.assertEqual(cand_good.confidence, cand_bad.confidence)  # 前提：置信度打平

            report_good = ga.generate_growth_report(paths, cand_good)
            report_bad = ga.generate_growth_report(paths, cand_bad)
            cfg = GrowthAdvisorConfig(notification_min_confidence=0.1, notification_max_per_day=1)
            candidates_by_id = {cand_good.candidate_id: cand_good, cand_bad.candidate_id: cand_bad}
            result = ga._maybe_dispatch_notification(
                paths, cfg, candidates_by_id, [report_bad, report_good]
            )
            self.assertIsNotNone(result)
            # 置信度打平的情况下，历史采纳率更高的类别应该被优先推送
            self.assertEqual(result["report_id"], report_good.report_id)

    def test_category_acceptance_rate_only_includes_decided_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )  # 仍是 pending，没有决策
            rates = ga._category_acceptance_rate(paths)
            self.assertNotIn("技术类", rates)

    def test_weekly_digest_excludes_muted_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            ga.generate_growth_report(paths, cand)
            cfg = GrowthAdvisorConfig(category_notification_frequency={"技术类": "kanban_only"})
            result = ga._maybe_dispatch_weekly_digest(paths, cfg)
            self.assertIsNone(result)  # 唯一一份报告的类别被静音，没有可打包的内容


class TestReportQualityAndRefresh(unittest.TestCase):
    """P4-4：报告质量分级（report_quality_llm_enabled）+ 增量刷新。"""

    def test_generate_report_snapshots_evidence_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            report = ga.generate_growth_report(paths, c)
            self.assertEqual(report.evidence_count_at_generation, 3)

    def test_reports_needing_refresh_respects_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            ga.generate_growth_report(paths, c)
            # 只多 1 条证据，不达到默认阈值 3，不应该出现在待刷新列表。
            backlog.add_or_merge(
                "数据分析", "新理由", ["e1", "e2", "e3", "e4"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            cfg = GrowthAdvisorConfig()
            self.assertEqual(ga.reports_needing_refresh(paths, cfg), [])

            # 再多几条，达到阈值，应该出现。
            backlog.add_or_merge(
                "数据分析", "新理由2", ["e1", "e2", "e3", "e4", "e5", "e6", "e7"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            rows = ga.reports_needing_refresh(paths, cfg)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["candidate_id"], c.candidate_id)
            self.assertEqual(rows[0]["new_evidence"], 4)

    def test_refresh_growth_report_creates_new_report_and_reattaches(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            first = ga.generate_growth_report(paths, c)
            backlog.add_or_merge(
                "数据分析", "新理由", ["e1", "e2", "e3", "e4", "e5", "e6"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            second = ga.refresh_growth_report(paths, c.candidate_id)
            self.assertNotEqual(second.report_id, first.report_id)
            self.assertEqual(second.evidence_count_at_generation, 6)

            reloaded = backlog.get(c.candidate_id)
            self.assertEqual(reloaded.report_id, second.report_id)
            # 旧报告仍在历史记录里，不会被删除。
            all_reports = {r.report_id for r in ga.list_reports(paths)}
            self.assertIn(first.report_id, all_reports)
            self.assertIn(second.report_id, all_reports)
            # 已经刷新过，不应该再出现在待刷新列表。
            self.assertEqual(ga.reports_needing_refresh(paths, GrowthAdvisorConfig()), [])

    def test_refresh_growth_report_unknown_candidate_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertIsNone(ga.refresh_growth_report(paths, "does-not-exist"))

    def test_legacy_report_missing_evidence_snapshot_not_flagged_for_refresh(self):
        """[P5-1] 迁移期回归测试：`evidence_count_at_generation` 字段引入
        之前生成的旧报告（反序列化时该 key 缺失），不应该被误判为"证据从 0
        涨到现在，该刷新了"。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3", "e4", "e5", "e6", "e7"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            report = ga.generate_growth_report(paths, c)
            # 模拟"字段引入之前"落盘的旧数据：直接改写 jsonl，去掉这个 key。
            rows = ga._read_jsonl(paths.growth_reports_index_path)
            for row in rows:
                row.pop("evidence_count_at_generation", None)
            ga._write_jsonl(paths.growth_reports_index_path, rows)

            reloaded = ga.list_reports(paths)[0]
            self.assertEqual(reloaded.evidence_count_at_generation, -1)  # 哨兵值，不是 0
            self.assertEqual(ga.reports_needing_refresh(paths, GrowthAdvisorConfig()), [])

    def test_run_daily_cycle_uses_template_by_default_even_with_llm_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            store = _FakeMemoryStore(
                [_FakeEntry(f"e{i}", "聊到了 pandas 数据分析", [], time.time() - i)
                 for i in range(4)]
            )
            cfg = GrowthAdvisorConfig(min_evidence_count=3)  # report_quality_llm_enabled 默认 False
            llm_helper = lambda prompt: "# LLM 生成的报告"
            result = ga.run_daily_cycle(paths, cfg, profile, store, llm_helper=llm_helper)
            report_ids = result.get("reports") or []
            self.assertTrue(report_ids)
            reports_by_id = {r.report_id: r for r in ga.list_reports(paths)}
            self.assertEqual(reports_by_id[report_ids[0]].source, "template")

    def test_run_daily_cycle_uses_llm_when_quality_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            store = _FakeMemoryStore(
                [_FakeEntry(f"e{i}", "聊到了 pandas 数据分析", [], time.time() - i)
                 for i in range(4)]
            )
            cfg = GrowthAdvisorConfig(min_evidence_count=3, report_quality_llm_enabled=True)
            llm_helper = lambda prompt: "# LLM 生成的报告"
            result = ga.run_daily_cycle(paths, cfg, profile, store, llm_helper=llm_helper)
            report_ids = result.get("reports") or []
            self.assertTrue(report_ids)
            reports_by_id = {r.report_id: r for r in ga.list_reports(paths)}
            self.assertEqual(reports_by_id[report_ids[0]].source, "llm")


class TestReportsIndexArchive(unittest.TestCase):
    """P5-0：growth_reports_index.jsonl 归档（compact_reports_index_storage）。"""

    def _make_candidate_with_report(self, paths, title="数据分析"):
        backlog = ga.GrowthBacklog(paths)
        cand = backlog.add_or_merge(
            title, "理由", ["e1", "e2", "e3"],
            min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
        )
        report = ga.generate_growth_report(paths, cand)
        return cand, report

    def test_currently_attached_report_never_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand, report = self._make_candidate_with_report(paths)
            # 人为把这条报告的 created_at 改得很旧（超过归档窗口）。
            rows = ga._read_jsonl(paths.growth_reports_index_path)
            rows[0]["created_at"] = time.time() - 400 * 86400
            ga._write_jsonl(paths.growth_reports_index_path, rows)
            archived = ga.compact_reports_index_storage(paths)
            self.assertEqual(archived, 0)  # 仍是候选当前挂着的那份，不归档
            self.assertEqual(len(ga.list_reports(paths)), 1)

    def test_replaced_old_report_gets_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand, old_report = self._make_candidate_with_report(paths)
            # 刷新出一份新报告，候选的 report_id 转指新报告，旧报告不再
            # 被任何候选挂着。
            new_report = ga.refresh_growth_report(paths, cand.candidate_id)
            self.assertIsNotNone(new_report)
            rows = ga._read_jsonl(paths.growth_reports_index_path)
            for r in rows:
                if r["report_id"] == old_report.report_id:
                    r["created_at"] = time.time() - 400 * 86400
            ga._write_jsonl(paths.growth_reports_index_path, rows)

            archived = ga.compact_reports_index_storage(paths)
            self.assertEqual(archived, 1)
            active_ids = {r.report_id for r in ga.list_reports(paths)}
            self.assertNotIn(old_report.report_id, active_ids)
            self.assertIn(new_report.report_id, active_ids)

    def test_replaced_but_recent_report_not_archived_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand, old_report = self._make_candidate_with_report(paths)
            ga.refresh_growth_report(paths, cand.candidate_id)
            # 旧报告刚被替换，created_at 仍是"现在"，没超过归档窗口。
            archived = ga.compact_reports_index_storage(paths)
            self.assertEqual(archived, 0)
            self.assertEqual(len(ga.list_reports(paths)), 2)

    def test_get_report_by_id_falls_back_to_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand, old_report = self._make_candidate_with_report(paths)
            ga.refresh_growth_report(paths, cand.candidate_id)
            rows = ga._read_jsonl(paths.growth_reports_index_path)
            for r in rows:
                if r["report_id"] == old_report.report_id:
                    r["created_at"] = time.time() - 400 * 86400
            ga._write_jsonl(paths.growth_reports_index_path, rows)
            ga.compact_reports_index_storage(paths)

            # 归档之后仍然能按 id 查到（不是 404）。
            found = ga.get_report_by_id(paths, old_report.report_id)
            self.assertIsNotNone(found)
            self.assertEqual(found.report_id, old_report.report_id)
            self.assertIsNone(ga.get_report_by_id(paths, "not-a-real-id"))

    def test_reports_needing_refresh_unaffected_by_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand, old_report = self._make_candidate_with_report(paths)
            ga.refresh_growth_report(paths, cand.candidate_id)
            rows = ga._read_jsonl(paths.growth_reports_index_path)
            for r in rows:
                if r["report_id"] == old_report.report_id:
                    r["created_at"] = time.time() - 400 * 86400
            ga._write_jsonl(paths.growth_reports_index_path, rows)
            ga.compact_reports_index_storage(paths)
            # 归档不影响"当前挂着的报告要不要刷新"这条只读聚合。
            self.assertEqual(ga.reports_needing_refresh(paths), [])

    def test_monthly_retrospective_reports_generated_counts_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand, old_report = self._make_candidate_with_report(paths)
            ga.refresh_growth_report(paths, cand.candidate_id)
            rows = ga._read_jsonl(paths.growth_reports_index_path)
            for r in rows:
                if r["report_id"] == old_report.report_id:
                    r["created_at"] = time.time() - 400 * 86400
            ga._write_jsonl(paths.growth_reports_index_path, rows)
            ga.compact_reports_index_storage(paths)
            summary = ga.monthly_retrospective_summary(paths)
            # 归档 1 份 + 活跃 1 份 = 2，累计总数不因为归档而"变少"。
            self.assertEqual(summary["reports_generated"], 2)

    def test_compact_reports_index_storage_noop_on_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(ga.compact_reports_index_storage(paths), 0)


class TestTopicTrend(unittest.TestCase):
    """P4-6：证据数走势快照（growth_topic_trend.jsonl）。"""

    def test_candidate_derive_records_trend_snapshot_even_below_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            profile.derived = {"growth_focus_areas": {"数据分析": ["e1", "e2"]}}  # 不达标（阈值 3）
            cfg = GrowthAdvisorConfig(min_evidence_count=3)
            produced = ga.growth_candidate_derive(paths, cfg, profile)
            self.assertEqual(produced, [])  # 没有生成候选
            series = ga._topic_trend_series(paths, ga.normalize_title_key("数据分析"))
            self.assertEqual(len(series), 1)
            self.assertEqual(series[0]["evidence_count"], 2)
            self.assertIsNone(series[0]["confidence"])  # 没有候选，置信度为空

    def test_multiple_scans_accumulate_trend_points_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            cfg = GrowthAdvisorConfig(min_evidence_count=3)
            for n in (3, 5, 8):
                profile.derived = {
                    "growth_focus_areas": {"数据分析": [f"e{i}" for i in range(n)]}
                }
                ga.growth_candidate_derive(paths, cfg, profile)
                time.sleep(0.001)
            series = ga._topic_trend_series(paths, ga.normalize_title_key("数据分析"))
            self.assertEqual([pt["evidence_count"] for pt in series], [3, 5, 8])
            self.assertTrue(series[0]["scanned_at"] <= series[-1]["scanned_at"])

    def test_topic_trend_series_limit_keeps_most_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            for n in range(5):
                ga._record_topic_trend_snapshot(paths, "数据分析", n, None)
            series = ga._topic_trend_series(paths, ga.normalize_title_key("数据分析"), limit=2)
            self.assertEqual([pt["evidence_count"] for pt in series], [3, 4])

    def test_compact_topic_trend_storage_downsamples_old_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            key = ga.normalize_title_key("数据分析")
            # 90 天前起，同一周内塞 3 条快照（应该被压缩成 1 条：最新的那条）。
            old_week_start = now - 90 * 86400
            for i, offset in enumerate((0, 1 * 86400, 2 * 86400)):
                ga._append_jsonl(
                    paths.growth_topic_trend_path,
                    {
                        "dedupe_key": key,
                        "topic": "数据分析",
                        "scanned_at": old_week_start + offset,
                        "evidence_count": i,
                        "confidence": None,
                    },
                )
            # 近期（窗口内）快照，不应被压缩。
            for i in range(3):
                ga._append_jsonl(
                    paths.growth_topic_trend_path,
                    {
                        "dedupe_key": key,
                        "topic": "数据分析",
                        "scanned_at": now - i,
                        "evidence_count": 10 + i,
                        "confidence": None,
                    },
                )
            removed = ga.compact_topic_trend_storage(paths, now=now)
            self.assertEqual(removed, 2)  # 3 条旧快照压缩成 1 条
            rows = ga._read_jsonl(paths.growth_topic_trend_path)
            self.assertEqual(len(rows), 4)  # 1 条压缩后的旧点 + 3 条近期点
            old_rows = [r for r in rows if r["scanned_at"] < now - ga._TREND_RAW_WINDOW_DAYS * 86400]
            self.assertEqual(len(old_rows), 1)
            self.assertEqual(old_rows[0]["evidence_count"], 2)  # 保留的是最新那条（offset 最大）

    def test_compact_topic_trend_storage_noop_when_nothing_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ga._record_topic_trend_snapshot(paths, "数据分析", 3, 0.5)
            removed = ga.compact_topic_trend_storage(paths)
            self.assertEqual(removed, 0)
            self.assertEqual(len(ga._read_jsonl(paths.growth_topic_trend_path)), 1)

    def test_compact_topic_trend_storage_noop_on_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            removed = ga.compact_topic_trend_storage(paths)
            self.assertEqual(removed, 0)

    def test_candidate_derive_compacts_trend_storage_each_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            key = ga.normalize_title_key("数据分析")
            old_week_start = now - 90 * 86400
            for offset in (0, 86400):
                ga._append_jsonl(
                    paths.growth_topic_trend_path,
                    {
                        "dedupe_key": key,
                        "topic": "数据分析",
                        "scanned_at": old_week_start + offset,
                        "evidence_count": 1,
                        "confidence": None,
                    },
                )
            profile = UserProfile()
            profile.derived = {"growth_focus_areas": {"数据分析": ["e1", "e2", "e3"]}}
            cfg = GrowthAdvisorConfig(min_evidence_count=3)
            ga.growth_candidate_derive(paths, cfg, profile)
            rows = ga._read_jsonl(paths.growth_topic_trend_path)
            old_rows = [r for r in rows if r["scanned_at"] < now - ga._TREND_RAW_WINDOW_DAYS * 86400]
            self.assertEqual(len(old_rows), 1)  # 2 条旧点被压缩成 1 条

    def test_growth_topic_map_includes_evidence_trend(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            ga._record_topic_trend_snapshot(paths, "数据分析", 3, 0.5)
            rows = ga.growth_topic_map(paths)
            self.assertEqual(len(rows), 1)
            self.assertIn("evidence_trend", rows[0])
            self.assertEqual(len(rows[0]["evidence_trend"]), 1)

    def test_diagnostics_snapshot_includes_topic_hit_counts_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            cfg = GrowthAdvisorConfig()
            snap = ga.diagnostics_snapshot(paths, cfg, profile, None)
            self.assertIn("topic_hit_counts_note", snap)
            self.assertTrue(snap["topic_hit_counts_note"])


class TestBuiltinTopicHideRestore(unittest.TestCase):
    """P4-7：内置主题隐藏/恢复的对称操作。"""

    def test_hidden_builtin_topics_empty_by_default(self):
        profile = UserProfile()
        self.assertEqual(ga.hidden_builtin_topics(profile), [])

    def test_remove_then_hidden_list_and_restore(self):
        profile = UserProfile()
        topic = next(iter(ga._TOPIC_KEYWORDS.keys()))
        self.assertTrue(ga.remove_topic_keyword(profile, topic))
        self.assertIn(topic, ga.hidden_builtin_topics(profile))

        # 恢复后不再出现在隐藏列表，且能被重新扫描到
        self.assertTrue(ga.restore_builtin_topic_keyword(profile, topic))
        self.assertNotIn(topic, ga.hidden_builtin_topics(profile))
        effective = ga._effective_topic_keywords(profile)
        self.assertIn(topic, effective)

    def test_restore_unknown_or_not_hidden_topic_returns_false(self):
        profile = UserProfile()
        self.assertFalse(ga.restore_builtin_topic_keyword(profile, "不存在的主题"))
        self.assertFalse(ga.restore_builtin_topic_keyword(profile, ""))
        topic = next(iter(ga._TOPIC_KEYWORDS.keys()))
        self.assertFalse(ga.restore_builtin_topic_keyword(profile, topic))  # 本来没隐藏

    def test_restore_does_not_create_custom_keyword_entry(self):
        profile = UserProfile()
        topic = next(iter(ga._TOPIC_KEYWORDS.keys()))
        ga.remove_topic_keyword(profile, topic)
        ga.restore_builtin_topic_keyword(profile, topic)
        custom = (getattr(profile, "derived", {}) or {}).get("growth_topic_keywords") or {}
        self.assertNotIn(topic, custom)  # 恢复不应该把内置主题转成自定义条目

    def test_diagnostics_snapshot_exposes_hidden_builtin_topics(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            topic = next(iter(ga._TOPIC_KEYWORDS.keys()))
            ga.remove_topic_keyword(profile, topic)
            cfg = GrowthAdvisorConfig()
            snap = ga.diagnostics_snapshot(paths, cfg, profile, None)
            self.assertIn(topic, snap["hidden_builtin_topics"])


if __name__ == "__main__":
    unittest.main()


class TestTopicCategoryLLM(unittest.TestCase):
    """[P5-3] 自定义/学习到的主题也能参与类别系统（LLM 归类，不用
    embedding）。next_doc/growth_advisor_improvement_plan_v3.md P5-3。"""

    def test_custom_topic_defaults_to_other_category_without_profile(self):
        # 不传 profile 时行为跟改动前完全一致：自定义主题一律"其他类"。
        self.assertEqual(ga._category_of("摄影"), "其他类")

    def test_classify_topic_category_llm_parses_known_label(self):
        helper = lambda prompt: "这个主题属于：技术类"
        result = ga.classify_topic_category_llm("摄影后期", ["修图", "调色"], helper)
        self.assertEqual(result, "技术类")

    def test_classify_topic_category_llm_returns_none_on_unknown_label(self):
        helper = lambda prompt: "娱乐类"  # 不在 4 选 1 里
        result = ga.classify_topic_category_llm("摄影", ["修图"], helper)
        self.assertIsNone(result)

    def test_classify_topic_category_llm_returns_none_on_exception(self):
        def helper(prompt):
            raise RuntimeError("boom")
        result = ga.classify_topic_category_llm("摄影", ["修图"], helper)
        self.assertIsNone(result)

    def test_maybe_classify_noop_when_disabled(self):
        profile = UserProfile()
        cfg = GrowthAdvisorConfig(topic_category_llm_enabled=False)
        helper = lambda prompt: "技术类"
        result = ga.maybe_classify_topic_category(profile, "摄影", ["修图"], cfg, llm_helper=helper)
        self.assertIsNone(result)
        self.assertEqual(ga._category_of("摄影", profile), "其他类")

    def test_maybe_classify_noop_without_llm_helper(self):
        profile = UserProfile()
        cfg = GrowthAdvisorConfig(topic_category_llm_enabled=True)
        result = ga.maybe_classify_topic_category(profile, "摄影", ["修图"], cfg, llm_helper=None)
        self.assertIsNone(result)

    def test_maybe_classify_persists_and_category_of_picks_it_up(self):
        profile = UserProfile()
        cfg = GrowthAdvisorConfig(topic_category_llm_enabled=True)
        helper = lambda prompt: "技术类"
        result = ga.maybe_classify_topic_category(profile, "摄影后期", ["修图", "调色"], cfg, llm_helper=helper)
        self.assertEqual(result, "技术类")
        self.assertEqual(ga._category_of("摄影后期", profile), "技术类")
        # 不传 profile 仍然拿不到（向后兼容，不影响旧调用方）。
        self.assertEqual(ga._category_of("摄影后期"), "其他类")

    def test_maybe_classify_does_not_reclassify_already_classified_topic(self):
        profile = UserProfile()
        cfg = GrowthAdvisorConfig(topic_category_llm_enabled=True)
        calls = []

        def helper(prompt):
            calls.append(prompt)
            return "技术类"

        ga.maybe_classify_topic_category(profile, "摄影后期", ["修图"], cfg, llm_helper=helper)
        self.assertEqual(len(calls), 1)
        # 第二次调用（比如另一轮 cron scan 又遇到这个主题）不应该重复问 LLM。
        result = ga.maybe_classify_topic_category(profile, "摄影后期", ["修图"], cfg, llm_helper=helper)
        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)

    def test_maybe_classify_skips_builtin_topics(self):
        profile = UserProfile()
        cfg = GrowthAdvisorConfig(topic_category_llm_enabled=True)
        helper = lambda prompt: "管理类"  # 故意给一个跟内置类别不同的答案
        builtin_topic = "写作与表达"
        result = ga.maybe_classify_topic_category(profile, builtin_topic, ["写作"], cfg, llm_helper=helper)
        self.assertIsNone(result)
        # 内置主题的类别始终由硬编码表决定，不会被 LLM 归类结果覆盖。
        self.assertEqual(ga._category_of(builtin_topic, profile), "表达类")

    def test_add_custom_topic_keyword_triggers_classification_when_enabled(self):
        profile = UserProfile()
        cfg = GrowthAdvisorConfig(topic_category_llm_enabled=True)
        helper = lambda prompt: "管理类"
        ga.add_custom_topic_keyword(profile, "敏捷实践", ["scrum", "站会"], cfg=cfg, llm_helper=helper)
        self.assertEqual(ga._category_of("敏捷实践", profile), "管理类")

    def test_add_custom_topic_keyword_backward_compatible_without_cfg(self):
        # 不传 cfg/llm_helper（旧调用方式，如 API 路由）行为不变。
        profile = UserProfile()
        entry = ga.add_custom_topic_keyword(profile, "摄影", ["修图"])
        self.assertEqual(entry["source"], "user_added")
        self.assertEqual(ga._category_of("摄影", profile), "其他类")

    def test_confirm_topic_keyword_triggers_classification_when_enabled(self):
        profile = UserProfile()
        cfg = GrowthAdvisorConfig(topic_category_llm_enabled=True)
        derived = dict(getattr(profile, "derived", {}) or {})
        derived["growth_topic_keywords"] = {
            "机器学习": {"keywords": ["ml", "深度学习"], "source": "llm_learned", "confirmed_by_user": False}
        }
        profile.derived = derived
        helper = lambda prompt: "技术类"
        changed = ga.confirm_topic_keyword(profile, "机器学习", cfg=cfg, llm_helper=helper)
        self.assertTrue(changed)
        self.assertEqual(ga._category_of("机器学习", profile), "技术类")

    def test_category_feedback_learning_applies_to_classified_custom_topic(self):
        """归类结果不是摆设：接入现有的类别级反馈调权（P4-3），跟内置主题
        享有同样的"同类被忽略过会拖累新主题初始置信度"待遇。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            cfg = GrowthAdvisorConfig(topic_category_llm_enabled=True, min_evidence_count=3)
            helper = lambda prompt: "技术类"
            # 先手动写一条已归类的自定义主题类别，模拟"之前已经分类过"。
            ga._persist_topic_category(profile, "摄影后期", "技术类")

            # 制造"技术类"下 Python 工程实践被 dismiss 的历史。
            backlog = ga.GrowthBacklog(paths)
            c1 = backlog.add_or_merge(
                "Python 工程实践", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(c1.candidate_id, ga.STATUS_DISMISSED)
            ga.GrowthFeedbackLedger(paths).record(c1.candidate_id, ga.STATUS_DISMISSED)

            profile.derived = dict(profile.derived or {})
            profile.derived["growth_focus_areas"] = {"摄影后期": ["e4", "e5", "e6"]}
            candidates = ga.growth_candidate_derive(paths, cfg, profile)
            cand = next(c for c in candidates if c.title == "摄影后期")
            # 同类别历史 dismiss 应该让新主题的初始置信度打折（< 未打折的
            # _confidence_from_evidence(3)）。
            self.assertLess(cand.confidence, ga._confidence_from_evidence(3))


class TestEvidenceDistribution(unittest.TestCase):
    """P5-2：置信度模型引入"证据分布度"（growth_advisor_improvement_plan_v3.md P5-2）。"""

    def test_distribution_multiplier_neutral_without_timestamps(self):
        # 查不到时间戳（或只有 0/1 条能查到）时退化为中性值 1.0，不惩罚也不加成。
        self.assertEqual(ga._distribution_multiplier(["e1", "e2", "e3"], {}), 1.0)
        self.assertEqual(ga._distribution_multiplier(["e1"], {"e1": time.time()}), 1.0)

    def test_distribution_multiplier_concentrated_is_discounted(self):
        now = time.time()
        ts = {"e1": now, "e2": now + 10, "e3": now + 20}  # 全部同一天
        multiplier = ga._distribution_multiplier(["e1", "e2", "e3"], ts)
        self.assertLess(multiplier, 1.0)
        self.assertGreaterEqual(multiplier, ga._DISTRIBUTION_MIN_MULTIPLIER)

    def test_distribution_multiplier_spread_is_boosted(self):
        now = time.time()
        ts = {"e1": now, "e2": now - 7 * 86400, "e3": now - 14 * 86400}  # 分散在 3 周
        multiplier = ga._distribution_multiplier(["e1", "e2", "e3"], ts)
        self.assertGreaterEqual(multiplier, 1.0)
        self.assertLessEqual(multiplier, ga._DISTRIBUTION_MAX_MULTIPLIER)

    def test_distribution_multiplier_ignores_unknown_refs(self):
        now = time.time()
        # e3 没有时间戳记录，应该被忽略，不影响 e1/e2 的分桶计算。
        ts = {"e1": now, "e2": now}
        multiplier_with_unknown = ga._distribution_multiplier(["e1", "e2", "e3"], ts)
        multiplier_without_unknown = ga._distribution_multiplier(["e1", "e2"], ts)
        self.assertEqual(multiplier_with_unknown, multiplier_without_unknown)

    def test_signal_scan_persists_evidence_timestamps_for_window_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            now = time.time()
            entries = [
                _FakeEntry("e1", "讨论了 python packaging 的坑", ["python"], now - 10),
                _FakeEntry("e2", "一年前的老记录", ["python"], now - 200 * 86400),  # 窗口外
            ]
            store = _FakeMemoryStore(entries)
            profile = UserProfile()
            ga.growth_signal_scan(paths, profile, store)
            ts_map = profile.derived.get("growth_evidence_timestamps", {})
            self.assertIn("e1", ts_map)
            self.assertNotIn("e2", ts_map)  # 窗口外的条目不写入时间戳表

    def test_candidate_derive_applies_distribution_multiplier(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(min_evidence_count=3)
            now = time.time()

            # 场景 A：证据集中在一天内。
            profile_concentrated = UserProfile()
            profile_concentrated.derived = {
                "growth_focus_areas": {"数据分析": ["e1", "e2", "e3"]},
                "growth_evidence_timestamps": {"e1": now, "e2": now + 5, "e3": now + 10},
            }
            produced_a = ga.growth_candidate_derive(paths, cfg, profile_concentrated)

            # 场景 B：证据分散在 3 周内（换一个不冲突的主题避免 backlog 合并）。
            profile_spread = UserProfile()
            profile_spread.derived = {
                "growth_focus_areas": {"系统设计与架构": ["e4", "e5", "e6"]},
                "growth_evidence_timestamps": {
                    "e4": now, "e5": now - 7 * 86400, "e6": now - 14 * 86400,
                },
            }
            produced_b = ga.growth_candidate_derive(paths, cfg, profile_spread)

            self.assertEqual(len(produced_a), 1)
            self.assertEqual(len(produced_b), 1)
            # 分布更分散的候选置信度应该更高（其余乘子在两个场景下都是 1.0，
            # 唯一差异就是证据分布度）。
            self.assertGreater(produced_b[0].confidence, produced_a[0].confidence)

    def test_candidate_derive_without_timestamp_data_unaffected(self):
        # 向后兼容：不带 growth_evidence_timestamps 的 profile（旧数据/大量既有
        # 测试用例的写法）行为跟改动前完全一致。
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(min_evidence_count=3)
            profile = UserProfile()
            profile.derived = {"growth_focus_areas": {"数据分析": ["e1", "e2", "e3"]}}
            produced = ga.growth_candidate_derive(paths, cfg, profile)
            self.assertEqual(len(produced), 1)
            self.assertEqual(produced[0].confidence, ga._confidence_from_evidence(3))


class TestFollowupAndRefreshPassiveSignals(unittest.TestCase):
    """P5-2 后续 P5-4：回访/报告刷新接入被动信号（growth_topic_trend 快照）。"""

    def _accepted_candidate(self, paths, title, refs, *, accepted_days_ago):
        backlog = ga.GrowthBacklog(paths)
        c = backlog.add_or_merge(
            title, "理由", refs,
            min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
        )
        backlog.set_status(c.candidate_id, ga.STATUS_ACCEPTED)
        all_c = backlog.load_all()
        for cand in all_c:
            if cand.candidate_id == c.candidate_id:
                cand.accepted_at = time.time() - accepted_days_ago * 86400
        backlog.save_all(all_c)
        return backlog.get(c.candidate_id)

    def test_topic_trend_rising_none_with_insufficient_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertIsNone(ga._topic_trend_rising(paths, "no-such-topic", window_days=30))
            ga._record_topic_trend_snapshot(paths, "写作与表达", 3, None)
            # 只有 1 个快照点，仍然判断不了走势。
            self.assertIsNone(
                ga._topic_trend_rising(paths, ga.normalize_title_key("写作与表达"), window_days=30)
            )

    def test_topic_trend_rising_true_and_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            key = ga.normalize_title_key("写作与表达")
            now = time.time()
            for i, (offset, count) in enumerate([(20 * 86400, 2), (10 * 86400, 5), (0, 8)]):
                ga._append_jsonl(
                    paths.growth_topic_trend_path,
                    {
                        "dedupe_key": key, "topic": "写作与表达",
                        "scanned_at": now - offset, "evidence_count": count, "confidence": None,
                    },
                )
            self.assertTrue(ga._topic_trend_rising(paths, key, window_days=30))

            paths2 = _make_paths(tmp + "-2") if False else paths  # keep same paths, new key
            key2 = ga.normalize_title_key("项目管理")
            for offset, count in [(20 * 86400, 8), (10 * 86400, 6), (0, 5)]:
                ga._append_jsonl(
                    paths.growth_topic_trend_path,
                    {
                        "dedupe_key": key2, "topic": "项目管理",
                        "scanned_at": now - offset, "evidence_count": count, "confidence": None,
                    },
                )
            self.assertFalse(ga._topic_trend_rising(paths, key2, window_days=30))

    def test_pending_followups_defers_when_evidence_still_rising(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            due = self._accepted_candidate(
                paths, "写作与表达", ["e1", "e2", "e3"], accepted_days_ago=31
            )
            key = due.dedupe_key()
            now = time.time()
            for offset, count in [(20 * 86400, 2), (10 * 86400, 5), (0, 9)]:
                ga._append_jsonl(
                    paths.growth_topic_trend_path,
                    {
                        "dedupe_key": key, "topic": "写作与表达",
                        "scanned_at": now - offset, "evidence_count": count, "confidence": None,
                    },
                )
            cfg = GrowthAdvisorConfig(followup_review_days=30)
            out = ga.pending_followups(paths, cfg)
            self.assertNotIn(due.candidate_id, {c.candidate_id for c in out})

    def test_pending_followups_shows_when_evidence_flat(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            due = self._accepted_candidate(
                paths, "写作与表达", ["e1", "e2", "e3"], accepted_days_ago=31
            )
            key = due.dedupe_key()
            now = time.time()
            for offset, count in [(20 * 86400, 5), (10 * 86400, 5), (0, 5)]:
                ga._append_jsonl(
                    paths.growth_topic_trend_path,
                    {
                        "dedupe_key": key, "topic": "写作与表达",
                        "scanned_at": now - offset, "evidence_count": count, "confidence": None,
                    },
                )
            cfg = GrowthAdvisorConfig(followup_review_days=30)
            out = ga.pending_followups(paths, cfg)
            self.assertIn(due.candidate_id, {c.candidate_id for c in out})

    def test_pending_followups_shows_when_no_trend_data(self):
        # 没有任何趋势快照（数据不足以判断）时仍按原逻辑展示，不因为"判断
        # 不了"就被当成"在涨"处理，保持向后兼容。
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            due = self._accepted_candidate(
                paths, "写作与表达", ["e1", "e2", "e3"], accepted_days_ago=31
            )
            cfg = GrowthAdvisorConfig(followup_review_days=30)
            out = ga.pending_followups(paths, cfg)
            self.assertIn(due.candidate_id, {c.candidate_id for c in out})

    def test_followup_question_hint_changes_wording_when_flat(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            due = self._accepted_candidate(
                paths, "写作与表达", ["e1", "e2", "e3"], accepted_days_ago=31
            )
            key = due.dedupe_key()
            now = time.time()
            for offset, count in [(20 * 86400, 5), (10 * 86400, 5), (0, 5)]:
                ga._append_jsonl(
                    paths.growth_topic_trend_path,
                    {
                        "dedupe_key": key, "topic": "写作与表达",
                        "scanned_at": now - offset, "evidence_count": count, "confidence": None,
                    },
                )
            cfg = GrowthAdvisorConfig(followup_review_days=30)
            hint = ga.followup_question_hint(paths, due, cfg=cfg)
            self.assertIn("变少了", hint)

    def test_followup_question_hint_default_wording_without_trend_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            due = self._accepted_candidate(
                paths, "写作与表达", ["e1", "e2", "e3"], accepted_days_ago=31
            )
            hint = ga.followup_question_hint(paths, due, cfg=GrowthAdvisorConfig())
            self.assertIn("有没有真的推进", hint)

    def test_recent_evidence_delta_none_with_insufficient_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertIsNone(
                ga._recent_evidence_delta(paths, "no-such-topic", window_days=14)
            )

    def test_reports_needing_refresh_prioritizes_recent_burst(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)

            # 候选 A：总新增证据多（7 条），但发生在很久以前，最近 14 天没有新增。
            a = backlog.add_or_merge(
                "数据分析", "理由", ["a1", "a2", "a3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            ga.generate_growth_report(paths, a)
            a = backlog.add_or_merge(
                "数据分析", "理由2", [f"a{i}" for i in range(1, 11)],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            key_a = a.dedupe_key()
            now = time.time()
            for offset, count in [(60 * 86400, 3), (40 * 86400, 10)]:
                ga._append_jsonl(
                    paths.growth_topic_trend_path,
                    {
                        "dedupe_key": key_a, "topic": "数据分析",
                        "scanned_at": now - offset, "evidence_count": count, "confidence": None,
                    },
                )

            # 候选 B：总新增证据较少（4 条），但发生在最近几天，明显是突增。
            b = backlog.add_or_merge(
                "写作与表达", "理由", ["b1", "b2", "b3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            ga.generate_growth_report(paths, b)
            b = backlog.add_or_merge(
                "写作与表达", "理由2", ["b1", "b2", "b3", "b4", "b5", "b6", "b7"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            key_b = b.dedupe_key()
            for offset, count in [(60 * 86400, 3), (2 * 86400, 7)]:
                ga._append_jsonl(
                    paths.growth_topic_trend_path,
                    {
                        "dedupe_key": key_b, "topic": "写作与表达",
                        "scanned_at": now - offset, "evidence_count": count, "confidence": None,
                    },
                )

            rows = ga.reports_needing_refresh(paths, GrowthAdvisorConfig())
            self.assertEqual(len(rows), 2)
            # B 的 new_evidence 总量（4）比 A（7）少，但最近突增更明显，应该排在前面。
            self.assertEqual(rows[0]["candidate_id"], b.candidate_id)
            self.assertGreater(rows[0]["recent_evidence_delta"], rows[1]["recent_evidence_delta"])

    def test_reports_needing_refresh_falls_back_to_new_evidence_without_trend_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            ga.generate_growth_report(paths, c)
            backlog.add_or_merge(
                "数据分析", "理由2", ["e1", "e2", "e3", "e4", "e5", "e6"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            rows = ga.reports_needing_refresh(paths, GrowthAdvisorConfig())
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]["recent_evidence_delta"])
            self.assertEqual(rows[0]["new_evidence"], 3)
