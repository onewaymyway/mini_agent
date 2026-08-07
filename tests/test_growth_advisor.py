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


if __name__ == "__main__":
    unittest.main()
