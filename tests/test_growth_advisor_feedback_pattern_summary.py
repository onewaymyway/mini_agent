"""tests/test_growth_advisor_feedback_pattern_summary.py

覆盖 next_doc/growth_advisor_ideal_advisor_gap_and_roadmap_plan.md
方向 2 第一步：反馈模式统计展示（纯统计，不接入排序）。

  growth_feedback_pattern_summary() —— 对最近若干条 dismiss 反馈做
      分组统计，产出人类可读摘要，不产出任何排序/加权数值
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import growth_advisor as ga
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _make_candidate(paths, title: str):
    backlog = ga.GrowthBacklog(paths)
    cand = backlog.add_or_merge(
        title=title,
        rationale="r",
        evidence_refs=["e1", "e2", "e3"],
        min_evidence_count=3,
        max_pending=50,
        dismissed_cooldown_days=0,
    )
    return cand


class TestGrowthFeedbackPatternSummary(unittest.TestCase):
    def test_no_dismiss_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            result = ga.growth_feedback_pattern_summary(paths)
            self.assertFalse(result["has_enough_data"])
            self.assertEqual(result["sample_size"], 0)
            self.assertEqual(result["reason_distribution"], {})

    def test_accepted_entries_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cand = _make_candidate(paths, "话题A")
            ga.GrowthFeedbackLedger(paths).record(cand.candidate_id, "accepted")
            result = ga.growth_feedback_pattern_summary(paths)
            self.assertEqual(result["sample_size"], 0)

    def test_below_min_sample_no_summary_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ledger = ga.GrowthFeedbackLedger(paths)
            for i in range(2):
                cand = _make_candidate(paths, f"话题{i}")
                ledger.record(cand.candidate_id, "dismissed", reason="not_interested")
            result = ga.growth_feedback_pattern_summary(paths)
            self.assertFalse(result["has_enough_data"])
            self.assertEqual(result["sample_size"], 2)
            self.assertIn("样本还太少", result["summary_text"])
            # 计数本身仍然照常统计，供看板按需展示原始分布
            self.assertEqual(result["reason_distribution"], {"not_interested": 2})

    def test_dominant_reason_surfaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ledger = ga.GrowthFeedbackLedger(paths)
            for i in range(6):
                cand = _make_candidate(paths, f"话题{i}")
                ledger.record(cand.candidate_id, "dismissed", reason="not_interested")
            for i in range(6, 8):
                cand = _make_candidate(paths, f"话题{i}")
                ledger.record(cand.candidate_id, "dismissed", reason="bad_timing")
            result = ga.growth_feedback_pattern_summary(paths)
            self.assertTrue(result["has_enough_data"])
            self.assertEqual(result["sample_size"], 8)
            self.assertIn("不感兴趣", result["summary_text"])
            self.assertEqual(result["reason_distribution"]["not_interested"], 6)
            self.assertEqual(result["reason_distribution"]["bad_timing"], 2)

    def test_no_dominant_pattern_generic_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ledger = ga.GrowthFeedbackLedger(paths)
            entries = [
                ("not_interested", "Python 工程实践"),
                ("not_interested", "前端与可视化"),
                ("bad_timing", "项目管理"),
                ("bad_timing", "项目管理"),
                ("report_not_useful", "写作与表达"),
            ]
            for reason, title in entries:
                cand = _make_candidate(paths, title)
                ledger.record(cand.candidate_id, "dismissed", reason=reason)
            result = ga.growth_feedback_pattern_summary(paths)
            self.assertTrue(result["has_enough_data"])
            self.assertIn("没有看出明显的共性模式", result["summary_text"])

    def test_recent_window_limits_old_entries(self):
        """超出 _FEEDBACK_PATTERN_RECENT_WINDOW 窗口的旧记录不参与统计
        （只看最近的倾向，不是全部历史）。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ledger = ga.GrowthFeedbackLedger(paths)
            # 先写 20 条 bad_timing（撑满窗口），再写 6 条 not_interested——
            # 最终窗口内只应该看到最近 20 条，即全部是 not_interested 之前
            # 的部分被挤出窗口。
            for i in range(20):
                cand = _make_candidate(paths, f"old{i}")
                ledger.record(cand.candidate_id, "dismissed", reason="bad_timing")
            for i in range(6):
                cand = _make_candidate(paths, f"new{i}")
                ledger.record(cand.candidate_id, "dismissed", reason="not_interested")
            result = ga.growth_feedback_pattern_summary(paths)
            self.assertEqual(result["sample_size"], ga._FEEDBACK_PATTERN_RECENT_WINDOW)
            # 窗口 20 条：14 条 bad_timing（20 旧 - 6 挤出）+ 6 条 not_interested
            self.assertEqual(result["reason_distribution"].get("bad_timing"), 14)
            self.assertEqual(result["reason_distribution"].get("not_interested"), 6)

    def test_category_distribution_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ledger = ga.GrowthFeedbackLedger(paths)
            for i in range(6):
                cand = _make_candidate(paths, "Python 工程实践")
                ledger.record(cand.candidate_id, "dismissed", reason="not_interested")
            result = ga.growth_feedback_pattern_summary(paths)
            self.assertIn("技术类", result["category_distribution"])
            self.assertEqual(result["category_distribution"]["技术类"], 6)

    def test_diagnostics_snapshot_includes_feedback_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            class _Profile:
                derived = {}

            class _Cfg:
                enabled = True
                min_evidence_count = 3
                max_pending_candidates = 10
                dismissed_cooldown_days = 30
                notification_frequency = "daily"
                notification_min_confidence = 0.5
                excluded_topics = []
                llm_signal_augment_enabled = False

            snap = ga.diagnostics_snapshot(paths, _Cfg(), _Profile(), None)
            self.assertIn("feedback_pattern", snap)
            self.assertFalse(snap["feedback_pattern"]["has_enough_data"])


class TestGrowthFeedbackPatternLlmInsight(unittest.TestCase):
    """[growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 2 第二步]"""

    class _Cfg:
        feedback_pattern_llm_enabled = True

    def _seed_dominant_pattern(self, paths):
        ledger = ga.GrowthFeedbackLedger(paths)
        for i in range(6):
            cand = _make_candidate(paths, f"话题{i}")
            ledger.record(cand.candidate_id, "dismissed", reason="not_interested")

    def test_disabled_by_default_even_with_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._seed_dominant_pattern(paths)
            calls = []

            def helper(prompt):
                calls.append(prompt)
                return "看起来都不感兴趣"

            # cfg=None（默认）→ feedback_pattern_llm_enabled 视为 False
            result = ga.growth_feedback_pattern_summary(paths, llm_helper=helper)
            self.assertEqual(result["llm_insight"], "")
            self.assertEqual(calls, [])

    def test_enabled_without_helper_stays_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._seed_dominant_pattern(paths)
            result = ga.growth_feedback_pattern_summary(paths, cfg=self._Cfg())
            self.assertEqual(result["llm_insight"], "")

    def test_enabled_with_helper_populates_insight(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._seed_dominant_pattern(paths)

            def helper(prompt):
                self.assertIn("不感兴趣", prompt)
                self.assertIn("6", prompt)
                return "你最近忽略的方向大多是因为不感兴趣。"

            result = ga.growth_feedback_pattern_summary(paths, cfg=self._Cfg(), llm_helper=helper)
            self.assertEqual(result["llm_insight"], "你最近忽略的方向大多是因为不感兴趣。")
            # 规则式摘要不应该被覆盖，两者并存
            self.assertIn("不感兴趣", result["summary_text"])

    def test_insufficient_sample_skips_llm_call(self):
        """样本不够（has_enough_data=False）时，即便开启也不该多花一次
        LLM 调用——数字本身就不够归纳。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ledger = ga.GrowthFeedbackLedger(paths)
            cand = _make_candidate(paths, "话题0")
            ledger.record(cand.candidate_id, "dismissed", reason="not_interested")
            calls = []

            def helper(prompt):
                calls.append(prompt)
                return "不该被调用"

            result = ga.growth_feedback_pattern_summary(paths, cfg=self._Cfg(), llm_helper=helper)
            self.assertFalse(result["has_enough_data"])
            self.assertEqual(result["llm_insight"], "")
            self.assertEqual(calls, [])

    def test_llm_empty_response_falls_back_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._seed_dominant_pattern(paths)
            result = ga.growth_feedback_pattern_summary(
                paths, cfg=self._Cfg(), llm_helper=lambda prompt: "   "
            )
            self.assertEqual(result["llm_insight"], "")
            # 规则式部分不受影响
            self.assertTrue(result["has_enough_data"])

    def test_llm_exception_does_not_propagate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._seed_dominant_pattern(paths)

            def helper(prompt):
                raise RuntimeError("boom")

            result = ga.growth_feedback_pattern_summary(paths, cfg=self._Cfg(), llm_helper=helper)
            self.assertEqual(result["llm_insight"], "")
            self.assertTrue(result["has_enough_data"])

    def test_diagnostics_snapshot_passes_through_llm_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._seed_dominant_pattern(paths)

            class _FullCfg(self._Cfg):
                enabled = True
                min_evidence_count = 3
                max_pending_candidates = 10
                dismissed_cooldown_days = 30
                notification_frequency = "daily"
                notification_min_confidence = 0.5
                excluded_topics = []
                llm_signal_augment_enabled = False

            class _Profile:
                derived = {}

            snap = ga.diagnostics_snapshot(
                paths, _FullCfg(), _Profile(), None,
                llm_helper=lambda prompt: "归纳结果",
            )
            self.assertEqual(snap["feedback_pattern"]["llm_insight"], "归纳结果")


if __name__ == "__main__":
    unittest.main()
