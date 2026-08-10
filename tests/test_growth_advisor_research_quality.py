"""tests/test_growth_advisor_research_quality.py — 调研信息获取与整理
改进测试（对应 next_doc/growth_advisor_research_quality_plan.md）。

覆盖：
  阶段 1：_external_signal_excerpts_for_topic() 摘录提取 +
          _external_signal_count_for_topic() 重构后行为不变
  阶段 3：外部资讯摘录被拼进 prompt 且带来源标注要求
  阶段 4：忽略原因（report_not_useful）驱动 prompt 追加针对性提醒
  阶段 2：两段式生成（提纲 → 填充），及提纲失败时优雅退化
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta

from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.evolution import growth_advisor as ga
from mini_agent.profile import UserProfile
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    from pathlib import Path
    return AgentPaths(project_root=Path(tmp))


def _write_external_page(paths, page_id, *, body, source_kind="external_search", updated=None):
    from mini_agent.wiki.writer import write_page
    kwargs = {"extra_frontmatter": {"source_kind": source_kind}}
    if updated is not None:
        kwargs["updated"] = updated
    write_page(paths, page_id=page_id, page_type="entity", body=body, tags=["tag"], **kwargs)


def _make_candidate(title="rust_async", rationale="持续投入证据充分"):
    return ga.GrowthCandidate(
        candidate_id="c1", title=title, rationale=rationale, confidence=0.8, evidence_count=5,
    )


class TestExternalSignalExcerpts(unittest.TestCase):
    def test_excerpts_extracted_from_matching_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_external_page(
                paths, "rust-async-runtime",
                body="# Rust 异步运行时\n\ntokio 生态最近发布了新的运行时改进，值得关注。",
            )
            excerpts = ga._external_signal_excerpts_for_topic(paths, "rust_async", ["rust", "tokio"])
            self.assertEqual(len(excerpts), 1)
            self.assertEqual(excerpts[0]["id"], "rust-async-runtime")
            self.assertIn("tokio", excerpts[0]["excerpt"])

    def test_excerpts_respects_max_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            for i in range(4):
                _write_external_page(paths, f"rust-page-{i}", body=f"# Rust 消息 {i}\n\nrust 相关内容 {i}。")
            excerpts = ga._external_signal_excerpts_for_topic(
                paths, "rust_async", ["rust"], max_excerpts=2
            )
            self.assertEqual(len(excerpts), 2)

    def test_no_matching_pages_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            excerpts = ga._external_signal_excerpts_for_topic(paths, "rust_async", ["rust"])
            self.assertEqual(excerpts, [])

    def test_count_after_refactor_matches_excerpt_source_pages(self):
        """重构后 `_external_signal_count_for_topic()` 必须仍然只是
        `_external_signal_matching_pages()` 结果的 `len()`，行为跟重构前
        完全一致（复用既有测试覆盖的过滤规则，这里只验证重构没有改变
        计数口径）。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_external_page(paths, "rust-a", body="# Rust A\n\nrust 相关。")
            _write_external_page(paths, "rust-b", body="# Rust B\n\nrust 相关。")
            count = ga._external_signal_count_for_topic(paths, "rust_async", ["rust"])
            pages = ga._external_signal_matching_pages(paths, "rust_async", ["rust"])
            self.assertEqual(count, len(pages))
            self.assertEqual(count, 2)

    def test_old_page_excluded_from_excerpts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            old_date = (date.today() - timedelta(days=200)).isoformat()
            _write_external_page(paths, "rust-old", body="# Rust 旧闻\n\nrust 老资讯。", updated=old_date)
            excerpts = ga._external_signal_excerpts_for_topic(paths, "rust_async", ["rust"], window_days=30)
            self.assertEqual(excerpts, [])


class TestReportExcerptsInPrompt(unittest.TestCase):
    """阶段 1/3：外部资讯摘录被拼进 prompt，且要求标注来源。"""

    def _profile_with_keywords(self, topic, keywords):
        profile = UserProfile()
        profile.derived = {
            "growth_topic_keywords": {
                topic: {"keywords": keywords, "source": "user_added", "confirmed_by_user": True},
            }
        }
        return profile

    def test_excerpt_content_and_citation_instruction_in_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_external_page(
                paths, "rust-async-runtime",
                body="# Rust 异步运行时\n\ntokio 生态最近的一些进展值得关注。",
            )
            profile = self._profile_with_keywords("rust_async", ["rust", "tokio"])
            cfg = GrowthAdvisorConfig(report_include_external_context=True)
            captured = {}

            def llm_helper(prompt):
                captured["prompt"] = prompt
                return "报告正文"

            ga.generate_growth_report(
                paths, _make_candidate(), llm_helper=llm_helper, profile=profile, cfg=cfg,
            )
            prompt = captured["prompt"]
            self.assertIn("参考：rust-async-runtime", prompt)
            self.assertIn("标注来源", prompt)

    def test_disabled_flag_has_no_excerpts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_external_page(paths, "rust-a", body="# Rust A\n\nrust 相关。")
            profile = self._profile_with_keywords("rust_async", ["rust"])
            cfg = GrowthAdvisorConfig(report_include_external_context=False)
            captured = {}

            def llm_helper(prompt):
                captured["prompt"] = prompt
                return "报告正文"

            ga.generate_growth_report(
                paths, _make_candidate(), llm_helper=llm_helper, profile=profile, cfg=cfg,
            )
            self.assertNotIn("参考：", captured["prompt"])


class TestDismissReasonAdaptivePrompt(unittest.TestCase):
    """阶段 4：报告曾被标 report_not_useful 时，prompt 追加针对性提醒。"""

    def test_no_prior_dismissal_no_extra_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig()
            captured = {}

            def llm_helper(prompt):
                captured["prompt"] = prompt
                return "报告正文"

            ga.generate_growth_report(paths, _make_candidate(), llm_helper=llm_helper, cfg=cfg)
            self.assertNotIn("内容太笼统", captured["prompt"])

    def test_prior_report_not_useful_dismissal_adds_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                title="rust_async", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            ga.GrowthFeedbackLedger(paths).record(
                cand.candidate_id, ga.STATUS_DISMISSED, reason=ga.DISMISS_REASON_REPORT_NOT_USEFUL,
            )
            cfg = GrowthAdvisorConfig()
            captured = {}

            def llm_helper(prompt):
                captured["prompt"] = prompt
                return "报告正文"

            ga.generate_growth_report(
                paths, _make_candidate(title="rust_async"), llm_helper=llm_helper, cfg=cfg,
            )
            self.assertIn("内容太笼统", captured["prompt"])

    def test_flag_disabled_suppresses_note_even_with_prior_dismissal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                title="rust_async", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            ga.GrowthFeedbackLedger(paths).record(
                cand.candidate_id, ga.STATUS_DISMISSED, reason=ga.DISMISS_REASON_REPORT_NOT_USEFUL,
            )
            cfg = GrowthAdvisorConfig(report_dismiss_reason_adaptive_enabled=False)
            captured = {}

            def llm_helper(prompt):
                captured["prompt"] = prompt
                return "报告正文"

            ga.generate_growth_report(
                paths, _make_candidate(title="rust_async"), llm_helper=llm_helper, cfg=cfg,
            )
            self.assertNotIn("内容太笼统", captured["prompt"])


class TestTwoStageReportGeneration(unittest.TestCase):
    """阶段 2：先提纲后填充；默认关闭；提纲失败时优雅退化。"""

    def test_disabled_by_default_single_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            calls = []

            def llm_helper(prompt):
                calls.append(prompt)
                return "报告正文"

            cfg = GrowthAdvisorConfig()  # report_two_stage_enabled 默认 False
            ga.generate_growth_report(paths, _make_candidate(), llm_helper=llm_helper, cfg=cfg)
            self.assertEqual(len(calls), 1)

    def test_enabled_calls_outline_then_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            calls = []

            def llm_helper(prompt):
                calls.append(prompt)
                if "提出 3-4 个" in prompt:
                    return '["具体问题一", "具体问题二"]'
                return "报告正文"

            cfg = GrowthAdvisorConfig(report_two_stage_enabled=True)
            ga.generate_growth_report(paths, _make_candidate(), llm_helper=llm_helper, cfg=cfg)
            self.assertEqual(len(calls), 2)
            self.assertIn("具体问题一", calls[1])
            self.assertIn("逐一具体回答", calls[1])

    def test_outline_empty_response_falls_back_to_single_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            calls = []

            def llm_helper(prompt):
                calls.append(prompt)
                if "提出 3-4 个" in prompt:
                    return ""
                return "报告正文"

            cfg = GrowthAdvisorConfig(report_two_stage_enabled=True)
            report = ga.generate_growth_report(paths, _make_candidate(), llm_helper=llm_helper, cfg=cfg)
            self.assertEqual(len(calls), 2)
            self.assertIn("为什么值得关注", calls[1])
            self.assertEqual(report.source, "llm")

    def test_outline_malformed_json_falls_back_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            def llm_helper(prompt):
                if "提出 3-4 个" in prompt:
                    return "不是 JSON"
                return "报告正文"

            cfg = GrowthAdvisorConfig(report_two_stage_enabled=True)
            report = ga.generate_growth_report(paths, _make_candidate(), llm_helper=llm_helper, cfg=cfg)
            self.assertEqual(report.source, "llm")

    def test_outline_exception_falls_back_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            def llm_helper(prompt):
                if "提出 3-4 个" in prompt:
                    raise RuntimeError("boom")
                return "报告正文"

            cfg = GrowthAdvisorConfig(report_two_stage_enabled=True)
            report = ga.generate_growth_report(paths, _make_candidate(), llm_helper=llm_helper, cfg=cfg)
            self.assertEqual(report.source, "llm")

    def test_outline_questions_capped_at_max(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            calls = []

            def llm_helper(prompt):
                calls.append(prompt)
                if "提出 3-4 个" in prompt:
                    import json
                    return json.dumps([f"问题{i}" for i in range(10)])
                return "报告正文"

            cfg = GrowthAdvisorConfig(report_two_stage_enabled=True)
            ga.generate_growth_report(paths, _make_candidate(), llm_helper=llm_helper, cfg=cfg)
            # 正文 prompt 里最多出现 _REPORT_OUTLINE_MAX_QUESTIONS 个编号问题
            body_prompt = calls[1]
            self.assertIn(f"{ga._REPORT_OUTLINE_MAX_QUESTIONS}. ", body_prompt)
            self.assertNotIn(f"{ga._REPORT_OUTLINE_MAX_QUESTIONS + 1}. ", body_prompt)


if __name__ == "__main__":
    unittest.main()
