"""tests/test_self_model_drift.py — 自我模型漂移检测
（self_awareness_identity_evolution_plan.md §2.6）专属单测。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.self_model_drift import compute_belief_drift_signals


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class _FakeCapEntry:
    def __init__(self, domain, confidence):
        self.domain = domain
        self.confidence = confidence


class TestComputeBeliefDriftSignals(unittest.TestCase):
    def test_no_profile_returns_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(compute_belief_drift_signals(paths), [])

    def test_no_belief_data_returns_empty(self):
        import tempfile

        from mini_agent.perception.global_knowledge import SelfProfile, save_self_profile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            save_self_profile(paths, SelfProfile())  # confidence_by_domain 为空
            self.assertEqual(compute_belief_drift_signals(paths), [])

    def test_filters_small_deltas_and_sorts_by_abs_delta(self):
        import tempfile

        from mini_agent.perception.global_knowledge import SelfProfile, save_self_profile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = SelfProfile()
            profile.self_assessment.confidence_by_domain = {
                "python_refactor": 0.8,   # 实测 0.85，差距小，应被过滤
                "bash_scripting": 0.9,    # 实测 0.2，差距大
                "web_scraping": 0.3,      # 实测 0.75，差距大（正向）
                "only_belief_domain": 0.5,  # 实测没有这个 domain，跳过
            }
            save_self_profile(paths, profile)

            fake_entries = [
                _FakeCapEntry("python_refactor", 0.85),
                _FakeCapEntry("bash_scripting", 0.2),
                _FakeCapEntry("web_scraping", 0.75),
                _FakeCapEntry("only_in_actual", 0.5),
            ]
            with patch(
                "mini_agent.evolution.consolidation.build_capability_map",
                return_value=fake_entries,
            ):
                signals = compute_belief_drift_signals(paths, threshold=0.3)

            domains = [s.domain for s in signals]
            self.assertNotIn("python_refactor", domains)
            self.assertNotIn("only_belief_domain", domains)
            self.assertNotIn("only_in_actual", domains)
            self.assertEqual(domains, ["bash_scripting", "web_scraping"])  # 降序按 |delta|

            bash_signal = next(s for s in signals if s.domain == "bash_scripting")
            self.assertAlmostEqual(bash_signal.delta, 0.2 - 0.9, places=3)

    def test_capability_map_error_returns_empty(self):
        import tempfile

        from mini_agent.perception.global_knowledge import SelfProfile, save_self_profile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = SelfProfile()
            profile.self_assessment.confidence_by_domain = {"x": 0.5}
            save_self_profile(paths, profile)

            with patch(
                "mini_agent.evolution.consolidation.build_capability_map",
                side_effect=RuntimeError("boom"),
            ):
                self.assertEqual(compute_belief_drift_signals(paths), [])


if __name__ == "__main__":
    unittest.main()
