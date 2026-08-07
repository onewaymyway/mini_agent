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


class TestCronJobsRegistered(unittest.TestCase):
    def test_builtin_jobs_include_growth_advisor(self):
        from mini_agent.evolution.cron_scheduler import _BUILTIN_JOBS

        ids = {j["id"] for j in _BUILTIN_JOBS}
        self.assertIn(ga.JOB_ID_DAILY, ids)
        self.assertIn(ga.JOB_ID_MONTHLY, ids)


if __name__ == "__main__":
    unittest.main()
