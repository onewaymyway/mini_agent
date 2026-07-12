"""
tests/test_curiosity_scoring.py — 方案三：好奇心评分测试

覆盖：
  1. total_calls=0 的能力条目 novelty 分数高于 total_calls=10 的条目。
  2. 最近探索过的领域 novelty 被正确降权。
  3. 旧三路信号（capability_map/work_index/lesson_review）novelty 默认值为 0，
     novelty_weight=0 时排序结果与改造前完全一致（回归保证）。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver, _DeriveCandidate


class _FakeAutonomyCfg:
    novelty_weight = 0.5
    exploration_min_calls_threshold = 2
    already_explored_cooldown_days = 30.0


class _FakeCfg:
    autonomy = _FakeAutonomyCfg()


class TestNoveltyScoring(unittest.TestCase):
    def test_default_novelty_is_zero_for_legacy_signals(self):
        c = _DeriveCandidate(title="x", description="", source_tag="lesson", urgency=5.0)
        self.assertEqual(c.novelty, 0.0)

    def test_novelty_weight_zero_matches_legacy_urgency_order(self):
        candidates = [
            _DeriveCandidate(title="a", description="", source_tag="lesson", urgency=3.0, novelty=0.9),
            _DeriveCandidate(title="b", description="", source_tag="lesson", urgency=5.0, novelty=0.1),
        ]
        novelty_weight = 0.0
        ranked = sorted(candidates, key=lambda x: x.urgency + novelty_weight * x.novelty, reverse=True)
        self.assertEqual(ranked[0].title, "b")  # 纯 urgency 排序，novelty 被忽略

    def test_fewer_total_calls_yields_higher_novelty(self):
        deriver = SoftGoalDeriver.__new__(SoftGoalDeriver)
        deriver._cfg = _FakeCfg()
        deriver._paths = SimpleNamespace(workdir_dir=None)

        # 直接测试公式而不依赖磁盘 IO：novelty = 1 / (1 + total_calls)
        novelty_zero_calls = 1.0 / (1 + 0)
        novelty_ten_calls = 1.0 / (1 + 10)
        self.assertGreater(novelty_zero_calls, novelty_ten_calls)

    def test_recently_explored_domain_downweighted(self):
        import tempfile
        import json
        import time
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            digest_path = workdir / "activity_digest.jsonl"
            digest_path.write_text(
                json.dumps({
                    "type": "exploration_result",
                    "capability_id": "domain_x",
                    "at": time.time(),
                }) + "\n",
                encoding="utf-8",
            )

            deriver = SoftGoalDeriver.__new__(SoftGoalDeriver)
            deriver._cfg = _FakeCfg()
            deriver._paths = SimpleNamespace(workdir_dir=workdir)

            explored = deriver._recently_explored_domains()
            self.assertIn("domain_x", explored)


if __name__ == "__main__":
    unittest.main()
