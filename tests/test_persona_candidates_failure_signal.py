"""tests/test_persona_candidates_failure_signal.py — 人格候选"失败驱动"信号
（self_awareness_identity_evolution_plan.md §2.7）专属单测。

覆盖：
  1. _collect_failure_signals：按 min_occurrence 过滤 + occurrence_count
     降序截断 top_n，字段透传正确
  2. _build_extraction_prompt：三路信号都为空时对应区块显示"（无）"；
     有失败信号时提示文本包含 task_category/root_cause_tag/次数
  3. _parse_llm_candidates_json：source 字段合法值原样保留，非法/缺失值
     回退为 manual_scan
  4. scan_persona_candidates：三路信号都空时直接返回空列表，不调用 LLM；
     只有失败信号时仍能正常触发一轮扫描，candidate.source 采用 LLM
     返回的 source，evidence_refs 里带 failure_pattern: 前缀
"""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.failure_pattern_store import FailurePattern, _save_store
from mini_agent.evolution.persona_candidates import (
    PersonaCandidateStore,
    _build_extraction_prompt,
    _collect_failure_signals,
    _parse_llm_candidates_json,
    scan_persona_candidates,
)


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_failure_pattern(paths: AgentPaths, *, category: str, tag: str, count: int) -> None:
    """在已有 store 基础上追加一条 pattern（用真实的 FailurePattern +
    _save_store，而不是手写文件格式，避免和实现细节耦合走偏）。"""
    from mini_agent.evolution.failure_pattern_store import load_failure_patterns

    existing = [FailurePattern.from_dict(d) for d in load_failure_patterns(paths)]
    existing.append(
        FailurePattern(
            pattern_id=f"{category}:{tag}",
            source="objective",
            task_category=category,
            root_cause_tag=tag,
            occurrence_count=count,
            first_seen=time.time(),
            last_seen=time.time(),
            example_summary="示例摘要",
        )
    )
    _save_store(paths, existing)


class TestCollectFailureSignals(unittest.TestCase):
    def test_filters_below_min_occurrence_and_sorts_desc(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_failure_pattern(paths, category="写周报", tag="timeout", count=2)
            _write_failure_pattern(paths, category="发邮件", tag="permission", count=5)
            _write_failure_pattern(paths, category="抓取网页", tag="tool_missing", count=3)

            signals = _collect_failure_signals(paths, top_n=8, min_occurrence=3)

            categories = [s["task_category"] for s in signals]
            self.assertNotIn("写周报", categories)  # count=2 < min_occurrence=3
            self.assertEqual(categories, ["发邮件", "抓取网页"])  # 降序

    def test_top_n_truncation(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            for i in range(5):
                _write_failure_pattern(paths, category=f"任务{i}", tag="other", count=10 - i)
            signals = _collect_failure_signals(paths, top_n=2, min_occurrence=1)
            self.assertEqual(len(signals), 2)

    def test_empty_store_returns_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(_collect_failure_signals(paths, top_n=8, min_occurrence=3), [])


class TestBuildExtractionPrompt(unittest.TestCase):
    def test_all_empty_shows_placeholder(self):
        prompt = _build_extraction_prompt([], [], [])
        self.assertIn("（无）", prompt)

    def test_failure_signal_included(self):
        prompt = _build_extraction_prompt(
            [], [],
            [{"task_category": "发邮件", "root_cause_tag": "permission", "occurrence_count": 5, "example_summary": "权限不足"}],
        )
        self.assertIn("发邮件", prompt)
        self.assertIn("permission", prompt)
        self.assertIn("5", prompt)
        self.assertIn("反复暴露的短板", prompt)


class TestParseLlmCandidatesSource(unittest.TestCase):
    def test_valid_source_preserved(self):
        raw = json.dumps([
            {"title": "标题", "persona_desc": "描述", "rationale": "理由", "source": "failure_pattern"}
        ])
        out = _parse_llm_candidates_json(raw)
        self.assertEqual(out[0]["source"], "failure_pattern")

    def test_invalid_source_falls_back_to_manual_scan(self):
        raw = json.dumps([
            {"title": "标题", "persona_desc": "描述", "rationale": "理由", "source": "not_a_real_source"}
        ])
        out = _parse_llm_candidates_json(raw)
        self.assertEqual(out[0]["source"], "manual_scan")

    def test_missing_source_falls_back_to_manual_scan(self):
        raw = json.dumps([{"title": "标题", "persona_desc": "描述", "rationale": "理由"}])
        out = _parse_llm_candidates_json(raw)
        self.assertEqual(out[0]["source"], "manual_scan")


class TestScanPersonaCandidatesFailureOnly(unittest.TestCase):
    def test_no_signals_returns_empty_without_llm_call(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            calls = []

            def llm_helper(prompt):
                calls.append(prompt)
                return "[]"

            cfg = SimpleNamespace(
                max_pending_candidates=10, dismissed_cooldown_days=30,
                topic_signal_top_n=8, wiki_miss_signal_top_n=8,
                failure_signal_top_n=8, failure_signal_min_occurrence=3,
            )
            result = scan_persona_candidates(paths, cfg, profile=None, llm_helper=llm_helper)
            self.assertEqual(result, [])
            self.assertEqual(calls, [])

    def test_failure_only_signal_produces_candidate_with_llm_source(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_failure_pattern(paths, category="发邮件", tag="permission", count=5)

            def llm_helper(prompt):
                if "反复暴露的短板" in prompt and "发邮件" in prompt:
                    return json.dumps([
                        {
                            "title": "邮件权限排障专家",
                            "persona_desc": "专门补强邮件发送权限相关问题的处理能力",
                            "rationale": "反复因权限问题失败",
                            "source": "failure_pattern",
                        }
                    ])
                return "NONE"

            cfg = SimpleNamespace(
                max_pending_candidates=10, dismissed_cooldown_days=30,
                topic_signal_top_n=8, wiki_miss_signal_top_n=8,
                failure_signal_top_n=8, failure_signal_min_occurrence=3,
            )
            created = scan_persona_candidates(paths, cfg, profile=None, llm_helper=llm_helper)

            self.assertEqual(len(created), 1)
            self.assertEqual(created[0].source, "failure_pattern")
            self.assertTrue(any(r.startswith("failure_pattern:") for r in created[0].evidence_refs))

            store = PersonaCandidateStore(paths)
            self.assertEqual(len(store.list_candidates(status="pending")), 1)


if __name__ == "__main__":
    unittest.main()
