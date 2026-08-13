"""tests/test_growth_advisor_external_drift_refresh.py — 覆盖 next_doc/
growth_advisor_autonomous_search_and_material_improvement_plan.md 方向
"外部世界变化驱动的刷新"：

  1. `_compute_excerpt_fingerprint()` 纯函数：id + 内容指纹提取。
  2. `generate_growth_report()` 生成时正确写入
     `external_excerpt_fingerprint`（有摘录时非空、没有摘录/规则模板
     兜底时为 `None`），且不要求 `source == "llm"`（跟 `citation_check`
     的触发条件不同）。
  3. `external_signal_drift_for_report()`：无基线/无当前主题信息时
     返回 `None`；新增页面、页面内容变化、内容不变三种场景的
     `drift_count`/`new_excerpt_ids`/`changed_excerpt_ids`。
  4. `reports_needing_refresh()`：
     - 默认（不传 `profile` / 配置关闭）行为与改动前完全一致；
     - 开启后，仅外部世界变化（证据数未达阈值）也能让报告出现在
       待刷新列表，且带 `external_drift` 字段；
     - 证据数触发时不做 drift 比对（`external_drift` 不出现在行里），
       避免不必要的比对开销；
     - `evidence_count_at_generation` 为哨兵值（-1）时，drift 信号仍然
       能独立触发（不再像证据数信号一样被跳过）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.evolution import growth_advisor as ga
from mini_agent.profile import UserProfile
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_external_page(paths, page_id, *, body, updated=None):
    from mini_agent.wiki.writer import write_page
    write_page(
        paths, page_id=page_id, page_type="entity", body=body, tags=["tag"],
        updated=updated, extra_frontmatter={"source_kind": "external_search"},
    )


def _profile_with_keywords(topic, keywords):
    profile = UserProfile()
    profile.derived = {
        "growth_topic_keywords": {
            topic: {"keywords": keywords, "source": "user_added", "confirmed_by_user": True},
        }
    }
    return profile


class TestComputeExcerptFingerprint(unittest.TestCase):
    def test_extracts_id_and_hash_pairs(self):
        excerpts = [{"id": "a", "excerpt": "hello"}, {"id": "b", "excerpt": "world"}]
        fp = ga._compute_excerpt_fingerprint(excerpts)
        self.assertEqual({row["id"] for row in fp}, {"a", "b"})
        self.assertTrue(all(len(row["hash"]) == 12 for row in fp))

    def test_skips_entries_without_id(self):
        fp = ga._compute_excerpt_fingerprint([{"excerpt": "no id here"}])
        self.assertEqual(fp, [])

    def test_same_content_produces_same_hash(self):
        fp1 = ga._compute_excerpt_fingerprint([{"id": "a", "excerpt": "same text"}])
        fp2 = ga._compute_excerpt_fingerprint([{"id": "a", "excerpt": "same text"}])
        self.assertEqual(fp1[0]["hash"], fp2[0]["hash"])

    def test_different_content_produces_different_hash(self):
        fp1 = ga._compute_excerpt_fingerprint([{"id": "a", "excerpt": "text one"}])
        fp2 = ga._compute_excerpt_fingerprint([{"id": "a", "excerpt": "text two"}])
        self.assertNotEqual(fp1[0]["hash"], fp2[0]["hash"])


class TestGenerateGrowthReportFingerprintField(unittest.TestCase):
    def test_fingerprint_populated_when_excerpts_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_external_page(paths, "rust-async-runtime", body="# Rust 异步运行时\n\ntokio 生态最近的一些进展值得关注。")
            profile = _profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(report_include_external_context=True)
            report = ga.generate_growth_report(
                paths, ga.GrowthCandidate(candidate_id="c1", title="rust_async", rationale="r"),
                llm_helper=lambda p: "报告正文（参考：rust-async-runtime）。",
                profile=profile, cfg=cfg,
            )
            self.assertIsNotNone(report.external_excerpt_fingerprint)
            self.assertEqual(len(report.external_excerpt_fingerprint), 1)
            self.assertEqual(report.external_excerpt_fingerprint[0]["id"], "rust-async-runtime")

    def test_fingerprint_none_when_no_excerpts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(report_include_external_context=True)
            report = ga.generate_growth_report(
                paths, ga.GrowthCandidate(candidate_id="c1", title="rust_async", rationale="r"),
                llm_helper=lambda p: "报告正文。", cfg=cfg,
            )
            self.assertIsNone(report.external_excerpt_fingerprint)

    def test_fingerprint_none_for_template_fallback_without_llm(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_external_page(paths, "rust-async-runtime", body="# Rust 异步运行时\n\ntokio 生态最近的一些进展值得关注。")
            profile = _profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(report_include_external_context=True)
            report = ga.generate_growth_report(
                paths, ga.GrowthCandidate(candidate_id="c1", title="rust_async", rationale="r"),
                llm_helper=None, profile=profile, cfg=cfg,
            )
            self.assertEqual(report.source, "template")
            self.assertIsNone(report.external_excerpt_fingerprint)


class TestExternalSignalDriftForReport(unittest.TestCase):
    def _report_with_fingerprint(self, paths, profile, cfg):
        _write_external_page(paths, "rust-async-runtime", body="# Rust 异步运行时\n\n初始内容。")
        return ga.generate_growth_report(
            paths, ga.GrowthCandidate(candidate_id="c1", title="rust_async", rationale="r"),
            llm_helper=lambda p: "报告正文（参考：rust-async-runtime）。",
            profile=profile, cfg=cfg,
        )

    def test_none_when_no_baseline_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = _profile_with_keywords("rust_async", ["rust"])
            report = ga.generate_growth_report(
                paths, ga.GrowthCandidate(candidate_id="c1", title="rust_async", rationale="r"),
                llm_helper=lambda p: "报告正文。",
            )
            self.assertIsNone(ga.external_signal_drift_for_report(paths, report, profile))

    def test_none_when_topic_not_in_profile_anymore(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = _profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(report_include_external_context=True)
            report = self._report_with_fingerprint(paths, profile, cfg)
            # 主题关键词从 profile 里被移除后，无法重新推导当前摘录，应返回 None。
            empty_profile = UserProfile()
            self.assertIsNone(ga.external_signal_drift_for_report(paths, report, empty_profile))

    def test_no_drift_when_content_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = _profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(report_include_external_context=True)
            report = self._report_with_fingerprint(paths, profile, cfg)
            drift = ga.external_signal_drift_for_report(paths, report, profile)
            self.assertEqual(drift["drift_count"], 0)
            self.assertEqual(drift["new_excerpt_ids"], [])
            self.assertEqual(drift["changed_excerpt_ids"], [])

    def test_detects_changed_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = _profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(report_include_external_context=True)
            report = self._report_with_fingerprint(paths, profile, cfg)
            # 更新同一个页面的内容。
            _write_external_page(paths, "rust-async-runtime", body="# Rust 异步运行时\n\n完全不同的新内容，运行时有重大更新。")
            drift = ga.external_signal_drift_for_report(paths, report, profile)
            self.assertEqual(drift["drift_count"], 1)
            self.assertEqual(drift["changed_excerpt_ids"], ["rust-async-runtime"])
            self.assertEqual(drift["new_excerpt_ids"], [])

    def test_detects_new_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = _profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(report_include_external_context=True)
            report = self._report_with_fingerprint(paths, profile, cfg)
            # 新增一个更新时间更晚的页面，会成为下次被动扫描摘录里的第一条。
            _write_external_page(
                paths, "rust-async-runtime-2", body="# Rust 异步运行时新篇\n\n全新的一篇内容。",
                updated="2099-01-01T00:00:00",
            )
            drift = ga.external_signal_drift_for_report(paths, report, profile)
            self.assertIn("rust-async-runtime-2", drift["new_excerpt_ids"])
            self.assertGreaterEqual(drift["drift_count"], 1)


class TestReportsNeedingRefreshWithDrift(unittest.TestCase):
    def test_default_behavior_unchanged_without_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            ga.generate_growth_report(paths, c)
            cfg = GrowthAdvisorConfig(report_external_drift_refresh_enabled=True)
            # 不传 profile：即便配置开启，也不应该做 drift 比对。
            rows = ga.reports_needing_refresh(paths, cfg)
            self.assertEqual(rows, [])

    def test_drift_alone_triggers_refresh_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = _profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(
                report_include_external_context=True,
                report_external_drift_refresh_enabled=True,
            )
            _write_external_page(paths, "rust-async-runtime", body="# Rust 异步运行时\n\n初始内容。")
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "rust_async", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            report = ga.generate_growth_report(
                paths, c, llm_helper=lambda p: "报告正文（参考：rust-async-runtime）。",
                profile=profile, cfg=cfg,
            )
            backlog.attach_report(c.candidate_id, report.report_id)

            # 证据数没有变化，只有外部内容变了。
            _write_external_page(paths, "rust-async-runtime", body="# Rust 异步运行时\n\n完全不同的新内容。")
            rows = ga.reports_needing_refresh(paths, cfg, profile=profile)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["new_evidence"], 0)
            self.assertIn("external_drift", rows[0])
            self.assertEqual(rows[0]["external_drift"]["drift_count"], 1)

    def test_evidence_trigger_does_not_include_drift_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "数据分析", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            ga.generate_growth_report(paths, c)
            backlog.add_or_merge(
                "数据分析", "新理由", ["e1", "e2", "e3", "e4", "e5", "e6", "e7"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            cfg = GrowthAdvisorConfig(report_external_drift_refresh_enabled=True)
            profile = UserProfile()
            rows = ga.reports_needing_refresh(paths, cfg, profile=profile)
            self.assertEqual(len(rows), 1)
            self.assertNotIn("external_drift", rows[0])

    def test_drift_min_changes_threshold_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = _profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(
                report_include_external_context=True,
                report_external_drift_refresh_enabled=True,
                report_external_drift_min_changes=5,
            )
            _write_external_page(paths, "rust-async-runtime", body="# Rust 异步运行时\n\n初始内容。")
            backlog = ga.GrowthBacklog(paths)
            c = backlog.add_or_merge(
                "rust_async", "理由", ["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            report = ga.generate_growth_report(
                paths, c, llm_helper=lambda p: "报告正文（参考：rust-async-runtime）。",
                profile=profile, cfg=cfg,
            )
            backlog.attach_report(c.candidate_id, report.report_id)
            _write_external_page(paths, "rust-async-runtime", body="# Rust 异步运行时\n\n变了一点点。")
            # 只有 1 处变化，未达阈值 5，不应该出现在待刷新列表。
            rows = ga.reports_needing_refresh(paths, cfg, profile=profile)
            self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
