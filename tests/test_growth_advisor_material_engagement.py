"""tests/test_growth_advisor_material_engagement.py

覆盖 next_doc/growth_advisor_ideal_advisor_gap_and_roadmap_plan.md
方向 1：素材参与度信号。

  record_pursuit_material_view()      —— 记一次"用户查看时素材处于第几轮"
  get_pursuit_material_engagement()   —— 只读查询"素材已经比上次查看新了几轮"
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import growth_advisor as ga
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class TestPursuitMaterialEngagement(unittest.TestCase):
    def test_never_viewed_returns_none_and_full_cycle_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            eng = ga.get_pursuit_material_engagement(paths, "g1", current_cycle=5)
            self.assertIsNone(eng["last_viewed_cycle"])
            self.assertEqual(eng["current_cycle"], 5)
            # 从未查看过，视为"从头到现在都没看过"
            self.assertEqual(eng["cycles_since_last_view"], 5)

    def test_record_view_then_engagement_reflects_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            recorded = ga.record_pursuit_material_view(paths, "g1", cycle_count=3)
            self.assertEqual(recorded["goal_id"], "g1")
            self.assertEqual(recorded["last_viewed_cycle"], 3)
            self.assertIn("viewed_at", recorded)

            eng = ga.get_pursuit_material_engagement(paths, "g1", current_cycle=3)
            self.assertEqual(eng["last_viewed_cycle"], 3)
            self.assertEqual(eng["cycles_since_last_view"], 0)

            eng2 = ga.get_pursuit_material_engagement(paths, "g1", current_cycle=7)
            self.assertEqual(eng2["last_viewed_cycle"], 3)
            self.assertEqual(eng2["cycles_since_last_view"], 4)

    def test_second_view_overwrites_last_viewed_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ga.record_pursuit_material_view(paths, "g1", cycle_count=2)
            ga.record_pursuit_material_view(paths, "g1", cycle_count=6)
            eng = ga.get_pursuit_material_engagement(paths, "g1", current_cycle=6)
            self.assertEqual(eng["last_viewed_cycle"], 6)
            self.assertEqual(eng["cycles_since_last_view"], 0)

    def test_engagement_is_per_goal_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ga.record_pursuit_material_view(paths, "g1", cycle_count=4)
            eng_g2 = ga.get_pursuit_material_engagement(paths, "g2", current_cycle=4)
            self.assertIsNone(eng_g2["last_viewed_cycle"])
            self.assertEqual(eng_g2["cycles_since_last_view"], 4)

    def test_current_cycle_less_than_last_viewed_does_not_go_negative(self):
        # 理论上不该出现（轮次只增不减），但防御式保证不返回负数。
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            ga.record_pursuit_material_view(paths, "g1", cycle_count=5)
            eng = ga.get_pursuit_material_engagement(paths, "g1", current_cycle=2)
            self.assertEqual(eng["cycles_since_last_view"], 0)


if __name__ == "__main__":
    unittest.main()
