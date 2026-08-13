"""tests/test_growth_advisor_report_citation_check.py — 覆盖 next_doc/
growth_advisor_autonomous_search_and_material_improvement_plan.md 第 4 节
"生成后自检"（阶段三，本轮新增落地）：

  1. `_check_report_citations()` 纯函数行为：完全引用、部分引用、引用
     子串简写形式（对得上）、编造引用（对不上）、没有任何引用。
  2. `generate_growth_report()` 端到端：
     - 开启 `report_include_external_context` 且拿到非空摘录、LLM 正文
       引用了摘录 → `report.citation_check` 非空、字段符合预期
     - 没开启外部背景 / 没拿到摘录 → `citation_check` 为 `None`
     - 拿到摘录但走的是规则模板兜底（`llm_helper=None`）→ `citation_check`
       为 `None`（模板路径本来就不引用外部摘录，没有可核对的对象）
     - `citation_check` 会随 `to_dict()`/`from_dict()` 正确序列化/反序列化，
       旧数据缺该字段时反序列化落到 `None`（向后兼容）
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.cli.commands import growth_cmd
from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.evolution import growth_advisor as ga
from mini_agent.profile import UserProfile
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_external_page(paths, page_id, *, body, source_kind="external_search"):
    from mini_agent.wiki.writer import write_page
    write_page(
        paths, page_id=page_id, page_type="entity", body=body, tags=["tag"],
        extra_frontmatter={"source_kind": source_kind},
    )


def _candidate(title: str = "rust_async") -> "ga.GrowthCandidate":
    return ga.GrowthCandidate(
        candidate_id="c1", title=title, rationale="持续投入证据充分",
        confidence=0.8, evidence_count=5,
    )


def _profile_with_keywords(topic, keywords):
    profile = UserProfile()
    profile.derived = {
        "growth_topic_keywords": {
            topic: {"keywords": keywords, "source": "user_added", "confirmed_by_user": True},
        }
    }
    return profile


class TestCheckReportCitationsPureFunction(unittest.TestCase):
    def _excerpts(self):
        return [
            {"id": "rust-async-runtime", "date": "2026-01-01", "excerpt": "摘录A"},
            {"id": "active_search:tokio#entity:pandas", "date": "2026-01-02", "excerpt": "摘录B"},
        ]

    def test_full_citation_exact_id(self):
        body = "正文引用了第一条（参考：rust-async-runtime）。"
        result = ga._check_report_citations(body, self._excerpts())
        self.assertEqual(result["excerpts_total"], 2)
        self.assertEqual(result["cited_count"], 1)
        self.assertEqual(result["citation_mentions_total"], 1)
        self.assertEqual(result["hallucinated_refs"], [])

    def test_citation_as_short_substring_of_id_counts_as_matched(self):
        # LLM 只标注了 id 的一部分（"pandas"），仍然算对得上，不算编造。
        body = "正文提到了 pandas（参考：pandas）。"
        result = ga._check_report_citations(body, self._excerpts())
        self.assertEqual(result["cited_count"], 1)
        self.assertEqual(result["hallucinated_refs"], [])

    def test_hallucinated_reference_not_matching_any_excerpt(self):
        body = "正文编了一个来源（参考：某个不存在的页面id）。"
        result = ga._check_report_citations(body, self._excerpts())
        self.assertEqual(result["cited_count"], 0)
        self.assertEqual(result["citation_mentions_total"], 1)
        self.assertEqual(result["hallucinated_refs"], ["某个不存在的页面id"])

    def test_no_citation_mentions_at_all(self):
        body = "正文完全没有标注任何引用来源。"
        result = ga._check_report_citations(body, self._excerpts())
        self.assertEqual(result["cited_count"], 0)
        self.assertEqual(result["citation_mentions_total"], 0)
        self.assertEqual(result["hallucinated_refs"], [])

    def test_same_excerpt_cited_multiple_times_counts_once(self):
        body = "第一次提到（参考：rust-async-runtime）。第二次又提（参考：rust-async-runtime）。"
        result = ga._check_report_citations(body, self._excerpts())
        self.assertEqual(result["cited_count"], 1)
        self.assertEqual(result["citation_mentions_total"], 2)


class TestGenerateGrowthReportCitationCheck(unittest.TestCase):
    def test_citation_check_populated_when_excerpts_used_and_cited(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_external_page(
                paths, "rust-async-runtime",
                body="# Rust 异步运行时\n\ntokio 生态最近的一些进展值得关注。",
            )
            profile = _profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(report_include_external_context=True)

            def llm_helper(prompt):
                return "参考了外部资讯（参考：rust-async-runtime），建议如下……"

            report = ga.generate_growth_report(
                paths, _candidate(), llm_helper=llm_helper, profile=profile, cfg=cfg,
            )
            self.assertIsNotNone(report.citation_check)
            self.assertEqual(report.citation_check["excerpts_total"], 1)
            self.assertEqual(report.citation_check["cited_count"], 1)
            self.assertEqual(report.citation_check["hallucinated_refs"], [])

    def test_citation_check_flags_hallucinated_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_external_page(
                paths, "rust-async-runtime",
                body="# Rust 异步运行时\n\ntokio 生态最近的一些进展值得关注。",
            )
            profile = _profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(report_include_external_context=True)

            def llm_helper(prompt):
                return "正文（参考：一个编造的来源）没有真的引用摘录。"

            report = ga.generate_growth_report(
                paths, _candidate(), llm_helper=llm_helper, profile=profile, cfg=cfg,
            )
            self.assertIsNotNone(report.citation_check)
            self.assertEqual(report.citation_check["cited_count"], 0)
            self.assertEqual(report.citation_check["hallucinated_refs"], ["一个编造的来源"])

    def test_citation_check_none_when_external_context_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(report_include_external_context=False)

            def llm_helper(prompt):
                return "报告正文"

            report = ga.generate_growth_report(
                paths, _candidate(), llm_helper=llm_helper, cfg=cfg,
            )
            self.assertIsNone(report.citation_check)

    def test_citation_check_none_when_no_excerpts_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            # 没有写任何外部页面，_external_signal_excerpts_for_topic 拿不到摘录，
            # 也没传 web_search_fn，主动检索分支不会触发。
            profile = _profile_with_keywords("rust_async", ["rust"])
            cfg = GrowthAdvisorConfig(report_include_external_context=True)

            def llm_helper(prompt):
                return "报告正文，没有外部背景可引用。"

            report = ga.generate_growth_report(
                paths, _candidate(), llm_helper=llm_helper, profile=profile, cfg=cfg,
            )
            self.assertIsNone(report.citation_check)

    def test_citation_check_none_for_template_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_external_page(
                paths, "rust-async-runtime",
                body="# Rust 异步运行时\n\ntokio 生态最近的一些进展值得关注。",
            )
            profile = _profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(report_include_external_context=True)

            # llm_helper=None → 走规则模板兜底，不涉及任何 prompt/引用。
            report = ga.generate_growth_report(
                paths, _candidate(), llm_helper=None, profile=profile, cfg=cfg,
            )
            self.assertEqual(report.source, "template")
            self.assertIsNone(report.citation_check)

    def test_citation_check_roundtrips_through_to_dict_from_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_external_page(
                paths, "rust-async-runtime",
                body="# Rust 异步运行时\n\ntokio 生态最近的一些进展值得关注。",
            )
            profile = _profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(report_include_external_context=True)

            def llm_helper(prompt):
                return "参考了外部资讯（参考：rust-async-runtime），建议如下……"

            report = ga.generate_growth_report(
                paths, _candidate(), llm_helper=llm_helper, profile=profile, cfg=cfg,
            )
            d = report.to_dict()
            self.assertIn("citation_check", d)
            restored = ga.GrowthReport.from_dict(d)
            self.assertEqual(restored.citation_check, report.citation_check)

            # 旧数据缺 citation_check 字段时，反序列化落到 None。
            legacy = dict(d)
            legacy.pop("citation_check", None)
            restored_legacy = ga.GrowthReport.from_dict(legacy)
            self.assertIsNone(restored_legacy.citation_check)


class TestGrowthReportCLICitationCheckDisplay(unittest.TestCase):
    """[growth_advisor_autonomous_search_and_material_improvement_plan.md
    阶段三后续：生成后自检结果的展示] `/growth report` 打印报告正文后，
    若报告带 `citation_check`，追加一行自检摘要；不带该字段（多数场景）
    时不打印任何额外内容，不干扰既有输出。
    """

    def _write_external_page(self, paths, page_id, *, body):
        from mini_agent.wiki.writer import write_page
        write_page(
            paths, page_id=page_id, page_type="entity", body=body, tags=["tag"],
            extra_frontmatter={"source_kind": "external_search"},
        )

    def test_prints_citation_summary_when_citation_check_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._write_external_page(
                paths, "rust-async-runtime",
                body="# Rust 异步运行时\n\ntokio 生态最近的一些进展值得关注。",
            )
            from mini_agent.profile import UserProfile
            profile = UserProfile()
            profile.derived = {
                "growth_topic_keywords": {
                    "rust_async": {
                        "keywords": ["rust", "tokio"], "source": "user_added",
                        "confirmed_by_user": True,
                    },
                }
            }
            from mini_agent.config.models import GrowthAdvisorConfig
            cfg = GrowthAdvisorConfig(report_include_external_context=True)

            class _Agent:
                def __init__(self):
                    self._paths = paths

                    class _Cfg:
                        growth_advisor = cfg

                    self.cfg = _Cfg()
                llm_helper = None

            backlog = ga.GrowthBacklog(paths)
            candidate = backlog.add_or_merge(
                title="rust_async", rationale="持续投入证据充分",
                evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )

            def fake_llm(prompt):
                return "参考了外部资讯（参考：rust-async-runtime），建议如下……"

            report = ga.generate_growth_report(
                paths, candidate, llm_helper=fake_llm, profile=profile, cfg=cfg,
            )

            from unittest.mock import patch
            with patch("mini_agent.ui.renderer.print_info") as mock_info, \
                 patch("mini_agent.ui.renderer.console") as mock_console:
                growth_cmd.handle_growth_cmd(
                    ["report", candidate.candidate_id], agent=_Agent(),
                )
            info_texts = [str(c.args[0]) for c in mock_info.call_args_list]
            self.assertTrue(any("[自检]" in t and "未检测到编造引用" in t for t in info_texts))

    def test_no_citation_summary_when_citation_check_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.config.models import GrowthAdvisorConfig
            cfg = GrowthAdvisorConfig()

            class _Agent:
                def __init__(self):
                    self._paths = paths

                    class _Cfg:
                        growth_advisor = cfg

                    self.cfg = _Cfg()
                llm_helper = None

            backlog = ga.GrowthBacklog(paths)
            candidate = backlog.add_or_merge(
                title="rust_async", rationale="持续投入证据充分",
                evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )

            def fake_llm(prompt):
                return "报告正文，没有任何外部背景。"

            ga.generate_growth_report(paths, candidate, llm_helper=fake_llm, cfg=cfg)

            from unittest.mock import patch
            with patch("mini_agent.ui.renderer.print_info") as mock_info, \
                 patch("mini_agent.ui.renderer.console") as mock_console:
                growth_cmd.handle_growth_cmd(
                    ["report", candidate.candidate_id], agent=_Agent(),
                )
            info_texts = [str(c.args[0]) for c in mock_info.call_args_list]
            self.assertFalse(any("[自检]" in t for t in info_texts))


class TestCitationCheckDiagnosticsSummary(unittest.TestCase):
    """[growth_advisor_autonomous_search_and_material_improvement_plan.md
    阶段三后续] `diagnostics_snapshot()` 汇总最近报告的 `citation_check`。
    """

    def _write_external_page(self, paths, page_id, *, body):
        from mini_agent.wiki.writer import write_page
        write_page(
            paths, page_id=page_id, page_type="entity", body=body, tags=["tag"],
            extra_frontmatter={"source_kind": "external_search"},
        )

    def test_summary_defaults_when_no_reports_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            summary = ga._citation_check_diagnostics_summary(paths)
            self.assertEqual(summary["reports_checked"], 0)
            self.assertEqual(summary["reports_with_hallucination"], 0)
            self.assertIsNone(summary["citation_hit_rate"])

    def test_summary_aggregates_across_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._write_external_page(
                paths, "rust-async-runtime",
                body="# Rust 异步运行时\n\ntokio 生态最近的一些进展值得关注。",
            )
            profile = _profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(report_include_external_context=True)

            # 一份正确引用、一份编造引用。
            ga.generate_growth_report(
                paths, _candidate("rust_async"),
                llm_helper=lambda p: "引用了（参考：rust-async-runtime）。",
                profile=profile, cfg=cfg,
            )
            ga.generate_growth_report(
                paths, _candidate("rust_async"),
                llm_helper=lambda p: "编造了一个来源（参考：不存在的id）。",
                profile=profile, cfg=cfg,
            )

            summary = ga._citation_check_diagnostics_summary(paths)
            self.assertEqual(summary["reports_checked"], 2)
            self.assertEqual(summary["reports_with_hallucination"], 1)
            self.assertEqual(summary["total_excerpts_offered"], 2)
            self.assertEqual(summary["total_excerpts_cited"], 1)
            self.assertEqual(summary["citation_hit_rate"], 0.5)

    def test_diagnostics_snapshot_includes_citation_check_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = UserProfile()
            snap = ga.diagnostics_snapshot(paths, GrowthAdvisorConfig(), profile, None)
            self.assertIn("citation_check", snap)
            self.assertEqual(snap["citation_check"]["reports_checked"], 0)


if __name__ == "__main__":
    unittest.main()
