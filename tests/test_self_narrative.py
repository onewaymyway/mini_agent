"""tests/test_self_narrative.py — 自我叙事生成
（self_awareness_identity_evolution_plan.md §2.2）专属单测。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from mini_agent.storage.paths import AgentPaths
from mini_agent.perception.global_knowledge import SelfProfile, save_self_profile, load_self_profile
from mini_agent.evolution.self_narrative import (
    generate_self_narrative,
    load_self_narrative_history,
    _has_any_evidence,
    _gather_evidence,
)


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class _FakeLLM:
    def __init__(self, response: str):
        self._response = response
        self.calls = []

    def ask(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._response


class TestHasAnyEvidence(unittest.TestCase):
    def test_no_identity_is_false(self):
        self.assertFalse(_has_any_evidence({"identity": None}))

    def test_identity_but_no_content_is_false(self):
        evidence = {
            "identity": {"purpose": ""},
            "self_assessment": {"strengths": [], "weak_areas": []},
            "capability_top_domains": [],
            "agent_value_patterns": [],
            "drift_signals": [],
            "recent_failure_patterns": [],
            "recent_sub_agent_experiences": [],
        }
        self.assertFalse(_has_any_evidence(evidence))

    def test_with_capability_content_is_true(self):
        evidence = {
            "identity": {"purpose": ""},
            "self_assessment": {},
            "capability_top_domains": [{"domain": "x", "confidence": 0.9}],
            "agent_value_patterns": [],
            "drift_signals": [],
            "recent_failure_patterns": [],
            "recent_sub_agent_experiences": [],
        }
        self.assertTrue(_has_any_evidence(evidence))


class TestGenerateSelfNarrative(unittest.TestCase):
    def test_no_llm_helper_returns_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertIsNone(generate_self_narrative(paths, llm_helper=None))

    def test_no_profile_returns_none_without_llm_call(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            llm = _FakeLLM("{}")
            result = generate_self_narrative(paths, llm_helper=llm)
            self.assertIsNone(result)
            self.assertEqual(llm.calls, [])

    def test_profile_with_no_content_returns_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            save_self_profile(paths, SelfProfile())  # 全空默认值
            llm = _FakeLLM("{}")
            result = generate_self_narrative(paths, llm_helper=llm)
            self.assertIsNone(result)
            self.assertEqual(llm.calls, [])

    def test_sufficient_evidence_generates_and_appends_and_updates_purpose(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = SelfProfile()
            profile.self_assessment.strengths = ["python_refactor"]
            save_self_profile(paths, profile)

            llm = _FakeLLM(json.dumps({
                "narrative": "我目前擅长 Python 重构，正在持续积累经验。",
                "purpose_summary": "专注打磨代码重构与自我认知能力",
            }))

            entry = generate_self_narrative(paths, llm_helper=llm)

            self.assertIsNotNone(entry)
            self.assertIn("Python", entry["narrative"])
            self.assertTrue(paths.self_narrative_log_path.exists())

            # 追加式存档：日志文件里应该有这一条
            history = load_self_narrative_history(paths)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["narrative"], entry["narrative"])

            # purpose 应该被回写到 self_profile.identity.purpose
            updated_profile = load_self_profile(paths)
            self.assertEqual(updated_profile.identity.purpose, "专注打磨代码重构与自我认知能力")

    def test_multiple_generations_are_appended_not_overwritten(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = SelfProfile()
            profile.self_assessment.strengths = ["x"]
            save_self_profile(paths, profile)

            llm1 = _FakeLLM(json.dumps({"narrative": "第一段叙事", "purpose_summary": "目标一"}))
            llm2 = _FakeLLM(json.dumps({"narrative": "第二段叙事", "purpose_summary": "目标二"}))

            generate_self_narrative(paths, llm_helper=llm1)
            generate_self_narrative(paths, llm_helper=llm2)

            history = load_self_narrative_history(paths, limit=10)
            self.assertEqual(len(history), 2)
            narratives = {h["narrative"] for h in history}
            self.assertEqual(narratives, {"第一段叙事", "第二段叙事"})

            # 最新一条应该排在最前
            self.assertEqual(history[0]["narrative"], "第二段叙事")

            # purpose 应该是最后一次生成的结果（最新覆盖）
            updated_profile = load_self_profile(paths)
            self.assertEqual(updated_profile.identity.purpose, "目标二")

    def test_malformed_llm_response_returns_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = SelfProfile()
            profile.self_assessment.strengths = ["x"]
            save_self_profile(paths, profile)

            llm = _FakeLLM("not even json")
            result = generate_self_narrative(paths, llm_helper=llm)
            self.assertIsNone(result)
            self.assertFalse(paths.self_narrative_log_path.exists())


class TestGatherEvidenceResilience(unittest.TestCase):
    def test_missing_subsystems_do_not_crash(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            evidence = _gather_evidence(paths)
            self.assertIsNone(evidence["identity"])
            self.assertEqual(evidence["capability_top_domains"], [])
            self.assertEqual(evidence["agent_value_patterns"], [])
            self.assertEqual(evidence["drift_signals"], [])
            self.assertEqual(evidence["recent_failure_patterns"], [])
            self.assertEqual(evidence["recent_sub_agent_experiences"], [])


if __name__ == "__main__":
    unittest.main()
