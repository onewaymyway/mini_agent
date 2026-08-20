"""tests/test_growth_advisor_duplicate_direction_check.py

覆盖成长顾问候选生成的两项改动：
  1. `_llm_find_duplicate_direction()` / `GrowthBacklog.add_or_merge(llm_helper=...)`
     / `growth_candidate_derive(llm_helper=...)`：没有字面重复但和已存在
     的 pending/accepted 候选或已采纳 Goal 语义上是同一方向时，不再重复
     创建候选（命中候选则合并证据，只命中 Goal 则直接跳过）。
  2. 新增 dismiss reason `already_exists`（已存在该主题），不参与方向/
     类别置信度衰减。
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.config.models import GrowthAdvisorConfig
from mini_agent.evolution import growth_advisor as ga
from mini_agent.profile import UserProfile
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class TestLLMFindDuplicateDirection(unittest.TestCase):
    def test_empty_existing_titles_returns_none(self):
        result = ga._llm_find_duplicate_direction("学习 Rust", [], llm_helper=lambda p: "NONE")
        self.assertIsNone(result)

    def test_llm_none_response_returns_none(self):
        result = ga._llm_find_duplicate_direction(
            "学习 Rust 异步编程", ["Python 工程实践"], llm_helper=lambda p: "NONE"
        )
        self.assertIsNone(result)

    def test_llm_exact_match_returns_original_title(self):
        result = ga._llm_find_duplicate_direction(
            "掌握 Rust async/await",
            ["学习 Rust 异步编程", "Python 工程实践"],
            llm_helper=lambda p: "学习 Rust 异步编程",
        )
        self.assertEqual(result, "学习 Rust 异步编程")

    def test_llm_non_matching_output_returns_none(self):
        # LLM 输出了不在列表里的内容（格式漂移），应该退回 None 而不是瞎猜
        result = ga._llm_find_duplicate_direction(
            "学习 Rust", ["Python 工程实践"], llm_helper=lambda p: "某个不存在的标题"
        )
        self.assertIsNone(result)

    def test_llm_exception_returns_none(self):
        def _boom(p):
            raise RuntimeError("llm down")
        result = ga._llm_find_duplicate_direction("学习 Rust", ["Python 工程实践"], llm_helper=_boom)
        self.assertIsNone(result)


class TestAddOrMergeDuplicateCheck(unittest.TestCase):
    def test_no_llm_helper_creates_new_candidate_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            backlog.add_or_merge(
                title="Python 工程实践", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            cand = backlog.add_or_merge(
                title="Python 最佳实践", rationale="r", evidence_refs=["e4", "e5", "e6"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            # 没传 llm_helper，即使语义上明显重复也照常新建（行为不变）
            self.assertIsNotNone(cand)
            self.assertEqual(len(backlog.load_all()), 2)

    def test_llm_helper_merges_into_matching_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            first = backlog.add_or_merge(
                title="Python 工程实践", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            merged = backlog.add_or_merge(
                title="Python 最佳实践", rationale="r", evidence_refs=["e4", "e5", "e6"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
                llm_helper=lambda p: "Python 工程实践",
            )
            self.assertEqual(merged.candidate_id, first.candidate_id)
            self.assertEqual(set(merged.evidence_refs), {"e1", "e2", "e3", "e4", "e5", "e6"})
            # 没有产生第二条候选
            self.assertEqual(len(backlog.load_all()), 1)

    def test_llm_helper_matches_goal_title_skips_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                title="学习 Rust 异步编程", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
                llm_helper=lambda p: "掌握 Rust async 编程",
                existing_goal_titles=["掌握 Rust async 编程"],
            )
            self.assertIsNone(cand)
            self.assertEqual(len(backlog.load_all()), 0)

    def test_llm_helper_no_match_creates_new_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            cand = backlog.add_or_merge(
                title="学习 Rust 异步编程", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
                llm_helper=lambda p: "NONE",
                existing_goal_titles=["Python 工程实践"],
            )
            self.assertIsNotNone(cand)
            self.assertEqual(len(backlog.load_all()), 1)

    def test_llm_helper_matches_recently_dismissed_candidate_skips_creation(self):
        """[待处理候选反复出现已忽略过的相似方向] 语义相同、措辞不同的
        方向如果之前已经被 dismiss 且仍在冷却期内，也应该被 LLM 判重
        拦住，而不是只依赖字面完全一致的 dedupe_key。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            first = backlog.add_or_merge(
                title="学习 Rust 异步编程", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(first.candidate_id, ga.STATUS_DISMISSED)

            cand = backlog.add_or_merge(
                title="掌握 Rust async/await", rationale="r", evidence_refs=["e4", "e5", "e6"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
                llm_helper=lambda p: "学习 Rust 异步编程",
            )
            self.assertIsNone(cand)
            # 没有产生新候选，也没有把证据合并回已被忽略的候选上
            all_c = backlog.load_all()
            self.assertEqual(len(all_c), 1)
            self.assertEqual(set(all_c[0].evidence_refs), {"e1", "e2", "e3"})

    def test_llm_helper_ignores_dismissed_candidate_past_cooldown(self):
        """冷却期已过的 dismissed 候选不应该再拦住语义相似的新候选——
        跟原有的字面 dedupe_key 冷却期语义保持一致。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)
            first = backlog.add_or_merge(
                title="学习 Rust 异步编程", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            backlog.set_status(first.candidate_id, ga.STATUS_DISMISSED)
            all_c = backlog.load_all()
            all_c[0].updated_at = time.time() - 31 * 86400  # 冷却期已过
            backlog.save_all(all_c)

            cand = backlog.add_or_merge(
                title="掌握 Rust async/await", rationale="r", evidence_refs=["e4", "e5", "e6"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
                # LLM 只会在候选池里看到"学习 Rust 异步编程"时才判定匹配，
                # 冷却期已过的它不应该再出现在池子里，因此这里的 helper
                # 判定不重复。
                llm_helper=lambda p: "NONE",
            )
            self.assertIsNotNone(cand)
            self.assertEqual(len(backlog.load_all()), 2)

    def test_llm_helper_exception_falls_back_to_creating(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = ga.GrowthBacklog(paths)

            def _boom(p):
                raise RuntimeError("llm down")

            cand = backlog.add_or_merge(
                title="学习 Rust 异步编程", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
                llm_helper=_boom,
                existing_goal_titles=["Python 工程实践"],
            )
            self.assertIsNotNone(cand)


class TestGrowthCandidateDeriveDuplicateCheckWiring(unittest.TestCase):
    def _profile_with_focus(self, focus_areas: dict) -> UserProfile:
        p = UserProfile()
        p.derived = {"growth_focus_areas": focus_areas}
        return p

    def test_disabled_by_default_ignores_llm_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(min_evidence_count=3)  # duplicate_direction_llm_check_enabled 默认 False
            ga.GrowthBacklog(paths).add_or_merge(
                title="Python 工程实践", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            profile = self._profile_with_focus({"Python 最佳实践": ["e4", "e5", "e6"]})
            produced = ga.growth_candidate_derive(
                paths, cfg, profile, llm_helper=lambda p: "Python 工程实践",
            )
            # 开关关闭，即使传了 llm_helper 也不做语义判重，照常新建
            self.assertEqual(len(produced), 1)
            self.assertEqual(len(ga.GrowthBacklog(paths).load_all()), 2)

    def test_enabled_merges_semantic_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(
                min_evidence_count=3, duplicate_direction_llm_check_enabled=True,
            )
            first = ga.GrowthBacklog(paths).add_or_merge(
                title="Python 工程实践", rationale="r", evidence_refs=["e1", "e2", "e3"],
                min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
            )
            profile = self._profile_with_focus({"Python 最佳实践": ["e4", "e5", "e6"]})
            produced = ga.growth_candidate_derive(
                paths, cfg, profile, llm_helper=lambda p: "Python 工程实践",
            )
            self.assertEqual(len(produced), 1)
            self.assertEqual(produced[0].candidate_id, first.candidate_id)
            self.assertEqual(len(ga.GrowthBacklog(paths).load_all()), 1)

    def test_enabled_skips_when_matches_active_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            cfg = GrowthAdvisorConfig(
                min_evidence_count=3, duplicate_direction_llm_check_enabled=True,
            )
            profile = self._profile_with_focus({"学习 Rust 异步编程": ["e1", "e2", "e3"]})

            fake_goal = SimpleNamespace(title="掌握 Rust async 编程")
            fake_goal_backlog = SimpleNamespace(active_goals=lambda: [fake_goal])

            produced = ga.growth_candidate_derive(
                paths, cfg, profile,
                goal_backlog=fake_goal_backlog,
                llm_helper=lambda p: "掌握 Rust async 编程",
            )
            self.assertEqual(produced, [])
            self.assertEqual(len(ga.GrowthBacklog(paths).load_all()), 0)


class TestAlreadyExistsDismissReason(unittest.TestCase):
    def test_valid_reason_accepted(self):
        self.assertIn(ga.DISMISS_REASON_ALREADY_EXISTS, ga._VALID_DISMISS_REASONS)

    def test_not_direction_negative(self):
        self.assertNotIn(ga.DISMISS_REASON_ALREADY_EXISTS, ga._DIRECTION_NEGATIVE_DISMISS_REASONS)

    def test_does_not_decay_dismiss_counts_by_dedupe_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ledger = ga.GrowthFeedbackLedger(paths)
            ledger.record(
                "cand1", ga.STATUS_DISMISSED, reason=ga.DISMISS_REASON_ALREADY_EXISTS,
            )
            counts = ga._dismiss_counts_by_dedupe_key(paths)
            # already_exists 不计入方向级衰减统计
            self.assertEqual(counts, {})

    def test_record_rejects_unknown_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ledger = ga.GrowthFeedbackLedger(paths)
            with self.assertRaises(ValueError):
                ledger.record("cand1", ga.STATUS_DISMISSED, reason="not_a_real_reason")


if __name__ == "__main__":
    unittest.main()
