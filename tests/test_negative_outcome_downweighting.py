"""
tests/test_negative_outcome_downweighting.py — 方案四：AgentSelfModel 接入
SoftGoalDeriver 候选打分（单场景验证：负面回填域降权）测试。

覆盖：
  1. AgentSelfModel.recent_negative_outcome_domains() 与
     outcome_tracker.get_revert_candidates() 返回结果一致性（经 _infer_domain 转换）
  2. 异常降级返回空列表
  3. SoftGoalDeriver.derive_candidates() 中落在负面域的候选 urgency 被
     降到 0.15 倍；未落在负面域的候选不受影响（回归防护）
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from mini_agent.perception.self_model import AgentSelfModel
from mini_agent.storage.paths import AgentPaths


@dataclass
class _TrackedCommitStub:
    commit_id: str
    trigger_lesson_group_id: str = ""
    commit_summary: str = ""
    verdict: str = "worsened"


class TestRecentNegativeOutcomeDomains(unittest.TestCase):
    def test_matches_get_revert_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            paths = AgentPaths(Path(td))
            import mini_agent.evolution.outcome_tracker as ot

            stub = _TrackedCommitStub(commit_id="c1", commit_summary="修复一个崩溃 bug")
            orig = ot.get_revert_candidates
            ot.get_revert_candidates = lambda _paths: [stub]
            try:
                model = AgentSelfModel()
                domains = model.recent_negative_outcome_domains(paths=paths)
            finally:
                ot.get_revert_candidates = orig
            self.assertEqual(domains, ["bug_fix"])

    def test_exception_degrades_to_empty_list(self):
        with tempfile.TemporaryDirectory() as td:
            paths = AgentPaths(Path(td))
            import mini_agent.evolution.outcome_tracker as ot

            def _raise(_paths):
                raise RuntimeError("boom")

            orig = ot.get_revert_candidates
            ot.get_revert_candidates = _raise
            try:
                model = AgentSelfModel()
                domains = model.recent_negative_outcome_domains(paths=paths)
            finally:
                ot.get_revert_candidates = orig
            self.assertEqual(domains, [])


@dataclass
class _AutonomyCfg:
    novelty_weight: float = 0.5


@dataclass
class _Cfg:
    autonomy: _AutonomyCfg = field(default_factory=_AutonomyCfg)


class _FakeGoalBacklog:
    def active_goals(self):
        return []


class TestDeriveCandidatesNegativeDownweighting(unittest.TestCase):
    def test_candidate_in_negative_domain_downweighted(self):
        from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver, _DeriveCandidate

        with tempfile.TemporaryDirectory() as td:
            paths = AgentPaths(Path(td))
            deriver = SoftGoalDeriver(paths, _Cfg())

            deriver._from_capability_map = lambda: [
                _DeriveCandidate(
                    title="改善 bug_fix 相关工具的执行可靠性",
                    description="", source_tag="capability", urgency=10.0,
                )
            ]
            deriver._from_work_index = lambda: []
            deriver._from_lesson_review = lambda: []
            deriver._from_unexplored_capabilities = lambda: [
                _DeriveCandidate(
                    title="探索未知能力：完全无关的领域",
                    description="", source_tag="capability", urgency=5.0,
                )
            ]
            deriver._recent_negative_outcome_domains = lambda: ["bug_fix"]

            cap, other = deriver.derive_candidates(_FakeGoalBacklog())
            by_title = {c.title: c for c in cap + other}
            self.assertAlmostEqual(
                by_title["改善 bug_fix 相关工具的执行可靠性"].urgency, 10.0 * 0.15, places=5
            )
            # 未落在负面域的候选不受影响
            self.assertAlmostEqual(
                by_title["探索未知能力：完全无关的领域"].urgency, 5.0, places=5
            )


if __name__ == "__main__":
    unittest.main()
