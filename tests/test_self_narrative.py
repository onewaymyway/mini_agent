"""tests/test_self_narrative.py — 自我叙事生成
（self_awareness_identity_evolution_plan.md §2.2，
next_doc/self_narrative_incremental_evolution_plan.md 阶段一）专属单测。
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
    get_current_narrative,
    _has_any_evidence,
    _gather_evidence,
    _snapshot_fingerprint,
    _delta_is_empty,
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


class TestDeltaAndFingerprint(unittest.TestCase):
    def test_delta_empty_when_all_appendable_sources_empty(self):
        evidence = {
            "agent_value_patterns": [],
            "recent_failure_patterns": [],
            "recent_sub_agent_experiences": [],
        }
        self.assertTrue(_delta_is_empty(evidence))

    def test_delta_not_empty_when_any_appendable_source_has_content(self):
        evidence = {
            "agent_value_patterns": [],
            "recent_failure_patterns": [{"task_category": "x"}],
            "recent_sub_agent_experiences": [],
        }
        self.assertFalse(_delta_is_empty(evidence))

    def test_fingerprint_stable_for_same_snapshot(self):
        evidence = {
            "identity": {"purpose": "x"},
            "self_assessment": {"strengths": ["a"]},
            "capability_top_domains": [{"domain": "x"}],
            "drift_signals": [],
            "lineage": None,
        }
        self.assertEqual(_snapshot_fingerprint(evidence), _snapshot_fingerprint(dict(evidence)))

    def test_fingerprint_changes_when_snapshot_changes(self):
        evidence1 = {
            "identity": {"purpose": "x"},
            "self_assessment": {"strengths": ["a"]},
            "capability_top_domains": [],
            "drift_signals": [],
            "lineage": None,
        }
        evidence2 = dict(evidence1)
        evidence2["capability_top_domains"] = [{"domain": "y"}]
        self.assertNotEqual(_snapshot_fingerprint(evidence1), _snapshot_fingerprint(evidence2))


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
                "capability_focus_suggestions": ["深入学习测试驱动开发"],
            }))

            entry = generate_self_narrative(paths, llm_helper=llm)

            self.assertIsNotNone(entry)
            self.assertIn("Python", entry["narrative"])
            self.assertEqual(entry["capability_focus_suggestions"], ["深入学习测试驱动开发"])
            self.assertIn("evidence_cursor", entry)
            self.assertIn("snapshot_fingerprint", entry)
            self.assertTrue(paths.self_narrative_log_path.exists())

            # 追加式存档：日志文件里应该有这一条
            history = load_self_narrative_history(paths)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["narrative"], entry["narrative"])

            # purpose 应该被回写到 self_profile.identity.purpose
            updated_profile = load_self_profile(paths)
            self.assertEqual(updated_profile.identity.purpose, "专注打磨代码重构与自我认知能力")

    def test_second_generation_without_new_evidence_is_skipped(self):
        """阶段一核心行为：没有新增证据、快照未变时不生成同质化的新版本。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = SelfProfile()
            profile.self_assessment.strengths = ["x"]
            save_self_profile(paths, profile)

            llm1 = _FakeLLM(json.dumps({"narrative": "第一段叙事", "purpose_summary": "目标一"}))
            entry1 = generate_self_narrative(paths, llm_helper=llm1)
            self.assertIsNotNone(entry1)

            llm2 = _FakeLLM(json.dumps({"narrative": "不应该被写入", "purpose_summary": "不应该被写入"}))
            entry2 = generate_self_narrative(paths, llm_helper=llm2)
            self.assertIsNone(entry2)
            self.assertEqual(llm2.calls, [])  # 应该在调用 LLM 之前就判断跳过

            history = load_self_narrative_history(paths, limit=10)
            self.assertEqual(len(history), 1)

    def test_second_generation_with_new_capability_uses_edit_style_prompt(self):
        """快照型证据（capability_map）变化时应该触发生成，且 prompt 里
        应该包含上一版叙事全文（编辑式 prompt）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            profile = SelfProfile()
            profile.self_assessment.strengths = ["x"]
            save_self_profile(paths, profile)

            llm1 = _FakeLLM(json.dumps({"narrative": "第一段叙事", "purpose_summary": "目标一"}))
            generate_self_narrative(paths, llm_helper=llm1)

            # 改变快照型证据（self_assessment），指纹应该变化，触发第二次生成
            profile2 = load_self_profile(paths)
            profile2.self_assessment.strengths = ["x", "y"]
            save_self_profile(paths, profile2)

            llm2 = _FakeLLM(json.dumps({"narrative": "第二段叙事（编辑版）", "purpose_summary": "目标二"}))
            entry2 = generate_self_narrative(paths, llm_helper=llm2)

            self.assertIsNotNone(entry2)
            self.assertEqual(len(llm2.calls), 1)
            self.assertIn("第一段叙事", llm2.calls[0])  # 编辑式 prompt 应带上一版全文
            self.assertIn("编辑更新", llm2.calls[0])

            history = load_self_narrative_history(paths, limit=10)
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["narrative"], "第二段叙事（编辑版）")

    def test_get_current_narrative_returns_latest(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertIsNone(get_current_narrative(paths))

            profile = SelfProfile()
            profile.self_assessment.strengths = ["x"]
            save_self_profile(paths, profile)
            llm = _FakeLLM(json.dumps({"narrative": "唯一一段", "purpose_summary": "目标"}))
            generate_self_narrative(paths, llm_helper=llm)

            current = get_current_narrative(paths)
            self.assertIsNotNone(current)
            self.assertEqual(current["narrative"], "唯一一段")

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

    def test_since_cursor_filters_appendable_sub_agent_experiences(self):
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            from mini_agent.evolution.sub_agent_experience import maybe_record_experience

            maybe_record_experience(
                paths, task_id="t1", task_name="old", status="FAILED", error="boom",
            )
            cursor = time.time()
            maybe_record_experience(
                paths, task_id="t2", task_name="new", status="FAILED", error="boom",
            )

            evidence = _gather_evidence(paths, since_cursor=cursor)
            task_ids = [e["task_id"] for e in evidence["recent_sub_agent_experiences"]]
            self.assertIn("t2", task_ids)
            self.assertNotIn("t1", task_ids)

            # since_cursor=0 时（首次生成）应该看到全部
            evidence_full = _gather_evidence(paths, since_cursor=0.0)
            task_ids_full = [e["task_id"] for e in evidence_full["recent_sub_agent_experiences"]]
            self.assertIn("t1", task_ids_full)
            self.assertIn("t2", task_ids_full)


if __name__ == "__main__":
    unittest.main()
