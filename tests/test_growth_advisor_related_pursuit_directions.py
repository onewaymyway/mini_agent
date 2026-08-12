"""tests/test_growth_advisor_related_pursuit_directions.py

覆盖 next_doc/growth_advisor_ideal_advisor_gap_and_roadmap_plan.md
规划维度局限分析追加的候选：调研路径关联信号
（`related_pursuit_directions()`）。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import growth_advisor as ga
from mini_agent.evolution import output_workspace as ow
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.profile import UserProfile
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_cycle(paths, goal_id, cycle_no, covered_subtopics):
    base_dir = ow.goal_output_base_dir(paths, goal_id)
    cycle_dir = ow.allocate_cycle_dir(paths, goal_id, cycle_no)
    progress_note = (
        "本轮小结\n```handoff\n"
        + json.dumps({"covered_subtopics": covered_subtopics})
        + "\n```"
    )
    ow.write_manifest(base_dir, cycle_dir, progress_note=progress_note, status="completed")


def _pursue(paths, backlog, title, evidence_n=8):
    gbacklog = ga.GrowthBacklog(paths)
    cand = gbacklog.add_or_merge(
        title, "理由", [f"e{i}" for i in range(evidence_n)],
        min_evidence_count=3, max_pending=10, dismissed_cooldown_days=30,
    )
    goal = backlog.add_goal(title=title, description="", tags=["growth_advisor"])
    backlog.update_fields(goal.id, recurring=True)
    goal.recurring = True
    all_c = gbacklog.load_all()
    for c in all_c:
        if c.candidate_id == cand.candidate_id:
            c.linked_goal_id = goal.id
    gbacklog.save_all(all_c)
    return cand, goal


class TestRelatedPursuitDirections(unittest.TestCase):
    def test_empty_when_fewer_than_two_pursued(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            _pursue(paths, backlog, "React 学习")
            relations = ga.related_pursuit_directions(paths, backlog, profile=None)
            self.assertEqual(relations, [])

    def test_no_relation_without_keyword_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            _, goal_a = _pursue(paths, backlog, "摄影")
            _, goal_b = _pursue(paths, backlog, "烘焙")
            _write_cycle(paths, goal_a.id, 1, ["光圈 快门 构图"])
            profile = UserProfile(derived={
                "growth_topic_keywords": {
                    "烘焙": {"keywords": ["面粉", "发酵", "烤箱"]},
                },
            })
            relations = ga.related_pursuit_directions(paths, backlog, profile=profile)
            self.assertEqual(relations, [])

    def test_detects_relation_when_keywords_co_occur(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            _, goal_a = _pursue(paths, backlog, "数据分析")
            _, goal_b = _pursue(paths, backlog, "Python 编程")
            _write_cycle(paths, goal_a.id, 1, ["用 python 写脚本", "常见的 pandas 用法"])
            profile = UserProfile(derived={
                "growth_topic_keywords": {
                    "Python 编程": {"keywords": ["python", "pandas", "脚本"]},
                },
            })
            relations = ga.related_pursuit_directions(paths, backlog, profile=profile)
            self.assertEqual(len(relations), 1)
            rel = relations[0]
            self.assertEqual(rel["title"], "数据分析")
            self.assertEqual(rel["related_title"], "Python 编程")
            self.assertIn("python", rel["shared_keywords"])

    def test_below_threshold_not_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            _, goal_a = _pursue(paths, backlog, "数据分析")
            _, goal_b = _pursue(paths, backlog, "Python 编程")
            _write_cycle(paths, goal_a.id, 1, ["用 python 写点小东西"])  # 只命中 1 个关键词
            profile = UserProfile(derived={
                "growth_topic_keywords": {
                    "Python 编程": {"keywords": ["python", "pandas", "脚本"]},
                },
            })
            relations = ga.related_pursuit_directions(paths, backlog, profile=profile)
            self.assertEqual(relations, [])

    def test_paused_goal_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            _, goal_a = _pursue(paths, backlog, "数据分析")
            _, goal_b = _pursue(paths, backlog, "Python 编程")
            backlog.update_fields(goal_b.id, recurring=False)  # 暂停
            _write_cycle(paths, goal_a.id, 1, ["python pandas 脚本"])
            profile = UserProfile(derived={
                "growth_topic_keywords": {
                    "Python 编程": {"keywords": ["python", "pandas", "脚本"]},
                },
            })
            relations = ga.related_pursuit_directions(paths, backlog, profile=profile)
            self.assertEqual(relations, [])

    def test_no_profile_yields_no_relations(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            _, goal_a = _pursue(paths, backlog, "数据分析")
            _pursue(paths, backlog, "Python 编程")
            _write_cycle(paths, goal_a.id, 1, ["python pandas 脚本"])
            relations = ga.related_pursuit_directions(paths, backlog, profile=None)
            self.assertEqual(relations, [])

    def test_direction_is_not_symmetric(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            _, goal_a = _pursue(paths, backlog, "数据分析")
            _, goal_b = _pursue(paths, backlog, "Python 编程")
            # 只有 A 的内容提到 B 的关键词，B 没有产出内容
            _write_cycle(paths, goal_a.id, 1, ["python pandas 脚本"])
            profile = UserProfile(derived={
                "growth_topic_keywords": {
                    "Python 编程": {"keywords": ["python", "pandas", "脚本"]},
                    "数据分析": {"keywords": ["统计", "可视化", "建模"]},
                },
            })
            relations = ga.related_pursuit_directions(paths, backlog, profile=profile)
            # 只应该有一条：A -> B，不应该出现 B -> A（B 没有产出内容）
            self.assertEqual(len(relations), 1)
            self.assertEqual(relations[0]["title"], "数据分析")


if __name__ == "__main__":
    unittest.main()
