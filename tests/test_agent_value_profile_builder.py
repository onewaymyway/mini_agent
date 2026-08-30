"""tests/test_agent_value_profile_builder.py — Agent 自身价值观归纳
（self_awareness_identity_evolution_plan.md §2.1）专属单测。

覆盖：
  1. _load_risk_tier_evidence：无 commit 历史 → 空列表；正确从
     StateRepo commit 历史提取 [T0]-[T3] tier
  2. generate_agent_value_profile：证据不足 / llm_helper=None → None，
     不落盘；证据充足 + LLM 返回有效模式 → 落盘 state + agent_value_profile.md
  3. 证据不足 min_evidence_count 的候选模式被过滤（_llm_summarize_value_patterns）
  4. 矛盾/强化合并：同一模式多轮出现 → 置信度提升、evidence_refs 合并去重
  5. load_agent_value_profile：读取已落盘的 pattern 列表
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.state_repo import StateRepo
from mini_agent.evolution.agent_value_profile_builder import (
    _load_risk_tier_evidence,
    _llm_summarize_value_patterns,
    generate_agent_value_profile,
    load_agent_value_profile,
)


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class _FakeLLMHelper:
    def __init__(self, response: str):
        self._response = response
        self.calls = []

    def ask(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._response


class TestLoadRiskTierEvidence(unittest.TestCase):
    def test_no_commits_returns_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(_load_risk_tier_evidence(paths), [])

    def test_extracts_tier_from_commit_subject(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = StateRepo(Path(tmp))
            repo.apply(changes={"a.txt": "1"}, message="add a", meta={}, tier="T0")
            repo.apply(changes={"b.txt": "2"}, message="add b", meta={}, tier="T2")

            paths = _make_paths(tmp)
            evidence = _load_risk_tier_evidence(paths)

            self.assertEqual(len(evidence), 2)
            tiers = {e["tier"] for e in evidence}
            self.assertEqual(tiers, {"T0", "T2"})
            for e in evidence:
                self.assertTrue(e["commit"])
                self.assertTrue(e["subject"].startswith(f"[{e['tier']}]"))


class TestSummarizeValuePatternsEvidenceFilter(unittest.TestCase):
    def test_pattern_below_min_evidence_count_filtered(self):
        evidence = [
            {"commit": "c1", "tier": "T0", "subject": "[T0] x"},
            {"commit": "c2", "tier": "T0", "subject": "[T0] y"},
        ]
        # LLM 声称只有 1 条证据支持，低于 min_evidence_count=3
        llm = _FakeLLMHelper(json.dumps([
            {"pattern": "我倾向于保守修改", "evidence_refs": ["c1"]}
        ]))
        out = _llm_summarize_value_patterns(evidence, llm, min_evidence_count=3)
        self.assertEqual(out, [])

    def test_evidence_refs_not_in_input_are_dropped(self):
        evidence = [
            {"commit": "c1", "tier": "T0", "subject": "[T0] x"},
            {"commit": "c2", "tier": "T0", "subject": "[T0] y"},
            {"commit": "c3", "tier": "T0", "subject": "[T0] z"},
        ]
        # "c_fake" 不在输入证据里，应该被过滤掉，剩余 3 条真实证据仍满足阈值
        llm = _FakeLLMHelper(json.dumps([
            {"pattern": "我倾向于保守修改", "evidence_refs": ["c1", "c2", "c3", "c_fake"]}
        ]))
        out = _llm_summarize_value_patterns(evidence, llm, min_evidence_count=3)
        self.assertEqual(len(out), 1)
        self.assertEqual(sorted(out[0]["evidence_refs"]), ["c1", "c2", "c3"])


class TestGenerateAgentValueProfile(unittest.TestCase):
    def test_no_llm_helper_returns_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertIsNone(generate_agent_value_profile(paths, llm_helper=None))

    def test_insufficient_evidence_returns_none_without_llm_call(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = StateRepo(Path(tmp))
            repo.apply(changes={"a.txt": "1"}, message="add a", meta={}, tier="T0")
            paths = _make_paths(tmp)
            llm = _FakeLLMHelper("[]")
            result = generate_agent_value_profile(paths, llm_helper=llm, min_evidence_count=3)
            self.assertIsNone(result)
            self.assertEqual(llm.calls, [])  # 证据不足直接跳过，不浪费一次 LLM 调用

    def test_sufficient_evidence_generates_profile_md(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = StateRepo(Path(tmp))
            for i in range(3):
                repo.apply(changes={f"f{i}.txt": str(i)}, message=f"change {i}", meta={}, tier="T0")

            paths = _make_paths(tmp)

            def _prompt_to_response(prompt):
                # 从 prompt 里提取真实 commit id，构造一个满足证据数量的模式
                import re
                commits = re.findall(r'"commit": "([a-f0-9]+)"', prompt)
                return json.dumps([
                    {"pattern": "我倾向于选择保守的小步修改", "evidence_refs": commits}
                ])

            class _DynamicLLM:
                def ask(self, prompt):
                    return _prompt_to_response(prompt)

            state = generate_agent_value_profile(paths, llm_helper=_DynamicLLM(), min_evidence_count=3)

            self.assertIsNotNone(state)
            self.assertEqual(len(state["patterns"]), 1)
            self.assertTrue(paths.agent_value_profile_path.exists())
            content = paths.agent_value_profile_path.read_text(encoding="utf-8")
            self.assertIn("我倾向于选择保守的小步修改", content)
            self.assertIn("Agent 自身价值观", content)

    def test_reinforcement_increases_confidence_and_merges_refs(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = StateRepo(Path(tmp))
            for i in range(3):
                repo.apply(changes={f"f{i}.txt": str(i)}, message=f"change {i}", meta={}, tier="T0")
            paths = _make_paths(tmp)

            import re

            class _DynamicLLM:
                def ask(self, prompt):
                    commits = re.findall(r'"commit": "([a-f0-9]+)"', prompt)
                    return json.dumps([
                        {"pattern": "我倾向于选择保守的小步修改", "evidence_refs": commits}
                    ])

            state1 = generate_agent_value_profile(paths, llm_helper=_DynamicLLM(), min_evidence_count=3)
            conf1 = state1["patterns"][0]["confidence"]

            # 追加更多 commit，再跑一轮，同一模式应该被强化而不是新建
            for i in range(3, 6):
                repo.apply(changes={f"f{i}.txt": str(i)}, message=f"change {i}", meta={}, tier="T0")
            state2 = generate_agent_value_profile(paths, llm_helper=_DynamicLLM(), min_evidence_count=3)

            self.assertEqual(len(state2["patterns"]), 1)
            conf2 = state2["patterns"][0]["confidence"]
            self.assertGreaterEqual(conf2, conf1)
            self.assertGreaterEqual(len(state2["patterns"][0]["evidence_refs"]), len(state1["patterns"][0]["evidence_refs"]))


class TestLoadAgentValueProfile(unittest.TestCase):
    def test_returns_empty_when_no_state(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(load_agent_value_profile(paths), [])

    def test_returns_patterns_after_generation(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = StateRepo(Path(tmp))
            for i in range(3):
                repo.apply(changes={f"f{i}.txt": str(i)}, message=f"change {i}", meta={}, tier="T1")
            paths = _make_paths(tmp)

            import re

            class _DynamicLLM:
                def ask(self, prompt):
                    commits = re.findall(r'"commit": "([a-f0-9]+)"', prompt)
                    return json.dumps([{"pattern": "我愿意承担中等风险的变更", "evidence_refs": commits}])

            generate_agent_value_profile(paths, llm_helper=_DynamicLLM(), min_evidence_count=3)
            patterns = load_agent_value_profile(paths)
            self.assertEqual(len(patterns), 1)
            self.assertEqual(patterns[0]["pattern"], "我愿意承担中等风险的变更")


if __name__ == "__main__":
    unittest.main()
