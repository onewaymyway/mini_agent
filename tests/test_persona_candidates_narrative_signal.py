"""tests/test_persona_candidates_narrative_signal.py — 人格候选"自我叙事驱动"
信号（next_doc/self_narrative_incremental_evolution_plan.md §2.5）专属单测。

覆盖：
  1. _collect_narrative_signals：无当前叙事时返回空；有
     capability_focus_suggestions 时按 top_n 截断
  2. _build_extraction_prompt：四路信号都为空时对应区块显示"（无）"；
     有叙事信号时提示文本包含建议内容、区块标题
  3. _parse_llm_candidates_json：source="narrative_reflection" 合法保留
  4. scan_persona_candidates：只有叙事信号时仍能正常触发一轮扫描，
     evidence_refs 里带 narrative_reflection: 前缀
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.persona_candidates import (
    PersonaCandidateStore,
    _build_extraction_prompt,
    _collect_narrative_signals,
    _parse_llm_candidates_json,
    scan_persona_candidates,
)


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_current_narrative(paths: AgentPaths, suggestions: list[str]) -> None:
    """直接落一条 self_narrative_log.jsonl 记录，不依赖真实 LLM 生成——
    与 self_narrative.py 落盘的字段结构保持一致即可。"""
    import time

    entry = {
        "at": time.time(),
        "narrative": "示例叙事",
        "purpose_summary": "示例目标",
        "capability_focus_suggestions": suggestions,
        "evidence_cursor": time.time(),
        "snapshot_fingerprint": "fake",
    }
    p = paths.self_narrative_log_path
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False))
        f.write("\n")


class TestCollectNarrativeSignals(unittest.TestCase):
    def test_no_narrative_returns_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(_collect_narrative_signals(paths, top_n=5), [])

    def test_narrative_with_no_suggestions_returns_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_current_narrative(paths, [])
            self.assertEqual(_collect_narrative_signals(paths, top_n=5), [])

    def test_top_n_truncation_and_field_shape(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_current_narrative(paths, ["方向一", "方向二", "方向三"])
            signals = _collect_narrative_signals(paths, top_n=2)
            self.assertEqual(len(signals), 2)
            self.assertEqual(signals[0], {"suggestion": "方向一"})

    def test_only_latest_narrative_is_consulted(self):
        """自我叙事是"当前状态"，应该只取最新一条，不合并历史版本。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_current_narrative(paths, ["旧建议"])
            _write_current_narrative(paths, ["新建议"])
            signals = _collect_narrative_signals(paths, top_n=5)
            self.assertEqual(signals, [{"suggestion": "新建议"}])


class TestBuildExtractionPromptNarrative(unittest.TestCase):
    def test_all_empty_shows_placeholder(self):
        prompt = _build_extraction_prompt([], [], [], [])
        self.assertIn("（无）", prompt)
        self.assertIn("自我叙事综合判断后认为值得补强的方向", prompt)

    def test_narrative_signal_included(self):
        prompt = _build_extraction_prompt(
            [], [], [], [{"suggestion": "深入学习测试驱动开发"}],
        )
        self.assertIn("深入学习测试驱动开发", prompt)
        self.assertIn("narrative_reflection", prompt)


class TestParseLlmCandidatesNarrativeSource(unittest.TestCase):
    def test_narrative_reflection_source_preserved(self):
        raw = json.dumps([
            {"title": "标题", "persona_desc": "描述", "rationale": "理由", "source": "narrative_reflection"}
        ])
        out = _parse_llm_candidates_json(raw)
        self.assertEqual(out[0]["source"], "narrative_reflection")


class TestScanPersonaCandidatesNarrativeOnly(unittest.TestCase):
    def _base_cfg(self):
        return SimpleNamespace(
            max_pending_candidates=10, dismissed_cooldown_days=30,
            topic_signal_top_n=8, wiki_miss_signal_top_n=8,
            failure_signal_top_n=8, failure_signal_min_occurrence=3,
            narrative_signal_top_n=5,
        )

    def test_no_signals_returns_empty_without_llm_call(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            calls = []

            def llm_helper(prompt):
                calls.append(prompt)
                return "[]"

            result = scan_persona_candidates(paths, self._base_cfg(), profile=None, llm_helper=llm_helper)
            self.assertEqual(result, [])
            self.assertEqual(calls, [])

    def test_narrative_only_signal_produces_candidate(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_current_narrative(paths, ["深入学习测试驱动开发"])

            def llm_helper(prompt):
                if "自我叙事综合判断" in prompt and "测试驱动开发" in prompt:
                    return json.dumps([
                        {
                            "title": "测试驱动开发实践者",
                            "persona_desc": "专注打磨 TDD 相关的实践能力",
                            "rationale": "叙事综合判断值得补强",
                            "source": "narrative_reflection",
                        }
                    ])
                return "NONE"

            created = scan_persona_candidates(paths, self._base_cfg(), profile=None, llm_helper=llm_helper)

            self.assertEqual(len(created), 1)
            self.assertEqual(created[0].source, "narrative_reflection")
            self.assertTrue(any(r.startswith("narrative_reflection:") for r in created[0].evidence_refs))

            store = PersonaCandidateStore(paths)
            self.assertEqual(len(store.list_candidates(status="pending")), 1)


if __name__ == "__main__":
    unittest.main()
