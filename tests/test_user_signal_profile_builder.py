"""tests/test_user_signal_profile_builder.py — evolution/
user_signal_profile_builder.py（personal_ai_alignment_upgrade_plan.md
阶段一）专属单测。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.suggestion_feedback_ledger import record_outcome
from mini_agent.evolution.user_signal_profile_builder import (
    _load_ledger_evidence,
    generate_user_signal_profile,
)
from mini_agent.profile import UserProfileManager


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class _FakeLLMHelper:
    def __init__(self, response: str):
        self._response = response
        self.calls = []

    def ask(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._response


class TestLoadLedgerEvidence(unittest.TestCase):
    def test_empty_ledger_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(_load_ledger_evidence(paths), [])

    def test_reads_accepted_and_rejected_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            record_outcome(paths, "cat_a", "accepted")
            record_outcome(paths, "cat_a", "accepted")
            record_outcome(paths, "cat_b", "rejected")
            evidence = _load_ledger_evidence(paths)
            by_cat = {e["category"]: e for e in evidence}
            self.assertEqual(by_cat["cat_a"]["accepted"], 2)
            self.assertEqual(by_cat["cat_b"]["rejected"], 1)


class TestGenerateUserSignalProfile(unittest.TestCase):
    def _seed_ledger(self, paths, categories):
        for cat in categories:
            record_outcome(paths, cat, "accepted")

    def test_no_llm_helper_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._seed_ledger(paths, ["a", "b", "c"])
            self.assertIsNone(generate_user_signal_profile(paths, llm_helper=None))

    def test_insufficient_evidence_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._seed_ledger(paths, ["a"])  # 少于 MIN_EVIDENCE_COUNT=3
            helper = _FakeLLMHelper("{}")
            self.assertIsNone(generate_user_signal_profile(paths, llm_helper=helper))
            self.assertEqual(helper.calls, [])  # 证据不足直接不调用 LLM

    def test_writes_into_profile_derived_with_ai_inference_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._seed_ledger(paths, ["a", "b", "c", "d"])
            resp = json.dumps({
                "values": [{"pattern": "我倾向于让 AI 自主推进", "evidence_refs": ["a", "b", "c"]}],
                "risk_preference": [{"pattern": "我偏好低风险建议", "evidence_refs": ["a", "b", "d"]}],
            })
            helper = _FakeLLMHelper(resp)
            result = generate_user_signal_profile(paths, llm_helper=helper)
            self.assertIsNotNone(result)
            self.assertIn("values", result)
            self.assertIn("risk_preference", result)

            manager = UserProfileManager(paths)
            profile = manager.load()
            values = profile.derived["values"]
            self.assertEqual(len(values), 1)
            self.assertEqual(values[0]["source"], "ai_inference")
            self.assertEqual(values[0]["text"], "我倾向于让 AI 自主推进")
            self.assertGreater(values[0]["confidence"], 0.0)
            self.assertEqual(sorted(values[0]["evidence_refs"]), ["a", "b", "c"])

            risk = profile.derived["risk_preference"]
            self.assertEqual(risk[0]["source"], "ai_inference")

    def test_pattern_below_min_evidence_count_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._seed_ledger(paths, ["a", "b", "c"])
            resp = json.dumps({
                "values": [{"pattern": "证据不足的模式", "evidence_refs": ["a"]}],
                "risk_preference": [],
            })
            helper = _FakeLLMHelper(resp)
            result = generate_user_signal_profile(paths, llm_helper=helper)
            self.assertIsNone(result)  # 唯一候选模式证据不足，整体归纳为空

    def test_reinforcement_across_two_runs_preserves_and_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self._seed_ledger(paths, ["a", "b", "c", "d"])
            resp1 = json.dumps({
                "values": [{"pattern": "我倾向于稳妥", "evidence_refs": ["a", "b", "c"]}],
                "risk_preference": [],
            })
            generate_user_signal_profile(paths, llm_helper=_FakeLLMHelper(resp1))

            manager = UserProfileManager(paths)
            first_conf = manager.load().derived["values"][0]["confidence"]
            first_ts = manager.load().derived["values"][0]["last_confirmed_at"]

            resp2 = json.dumps({
                "values": [{"pattern": "我倾向于稳妥", "evidence_refs": ["b", "c", "d"]}],
                "risk_preference": [],
            })
            generate_user_signal_profile(paths, llm_helper=_FakeLLMHelper(resp2))

            manager2 = UserProfileManager(paths)
            second = manager2.load().derived["values"][0]
            self.assertGreater(second["confidence"], first_conf)
            self.assertGreaterEqual(second["last_confirmed_at"], first_ts)
            self.assertEqual(sorted(second["evidence_refs"]), ["a", "b", "c", "d"])


if __name__ == "__main__":
    unittest.main()
