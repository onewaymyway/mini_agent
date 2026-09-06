"""tests/test_evidence_pattern.py — evolution/evidence_pattern.py
（personal_ai_alignment_upgrade_plan.md 阶段一）专属单测。"""

from __future__ import annotations

import unittest

from mini_agent.evolution.evidence_pattern import merge_evidence_patterns


class TestMergeEvidencePatterns(unittest.TestCase):
    def test_new_pattern_gets_capped_confidence(self):
        merged = merge_evidence_patterns(
            [], [{"pattern": "我倾向于稳妥", "evidence_refs": ["a", "b", "c", "d", "e"]}],
            now_label="2026-01-01",
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["confidence"], 0.6)  # 5 * 0.15 = 0.75，封顶 0.6
        self.assertEqual(merged[0]["first_observed"], "2026-01-01")

    def test_reinforcement_increases_confidence_and_merges_refs(self):
        existing = [{
            "pattern": "我倾向于稳妥", "evidence_refs": ["a", "b", "c"],
            "confidence": 0.45, "first_observed": "2026-01-01",
            "last_reinforced": "2026-01-01", "contradicted_by": [],
        }]
        merged = merge_evidence_patterns(
            existing, [{"pattern": "我倾向于稳妥", "evidence_refs": ["c", "d"]}],
            now_label="2026-02-01",
        )
        self.assertEqual(len(merged), 1)
        node = merged[0]
        self.assertEqual(sorted(node["evidence_refs"]), ["a", "b", "c", "d"])
        self.assertAlmostEqual(node["confidence"], 0.55)  # +0.1 * 1 个新证据
        self.assertEqual(node["last_reinforced"], "2026-02-01")

    def test_no_new_evidence_does_not_touch_last_reinforced(self):
        existing = [{
            "pattern": "我倾向于稳妥", "evidence_refs": ["a", "b", "c"],
            "confidence": 0.45, "first_observed": "2026-01-01",
            "last_reinforced": "2026-01-01", "contradicted_by": [],
        }]
        merged = merge_evidence_patterns(
            existing, [{"pattern": "我倾向于稳妥", "evidence_refs": ["a"]}],
            now_label="2026-02-01",
        )
        self.assertEqual(merged[0]["last_reinforced"], "2026-01-01")
        self.assertEqual(merged[0]["confidence"], 0.45)

    def test_different_pattern_does_not_overwrite(self):
        existing = [{
            "pattern": "我倾向于稳妥", "evidence_refs": ["a", "b", "c"],
            "confidence": 0.45, "first_observed": "2026-01-01",
            "last_reinforced": "2026-01-01", "contradicted_by": [],
        }]
        merged = merge_evidence_patterns(
            existing, [{"pattern": "我愿意承担更高风险", "evidence_refs": ["x", "y", "z"]}],
            now_label="2026-02-01",
        )
        patterns = {p["pattern"] for p in merged}
        self.assertEqual(patterns, {"我倾向于稳妥", "我愿意承担更高风险"})

    def test_blank_pattern_skipped(self):
        merged = merge_evidence_patterns([], [{"pattern": "  ", "evidence_refs": ["a"]}], now_label="2026-01-01")
        self.assertEqual(merged, [])


if __name__ == "__main__":
    unittest.main()
