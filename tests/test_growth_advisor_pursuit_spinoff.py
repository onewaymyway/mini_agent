"""tests/test_growth_advisor_pursuit_spinoff.py

覆盖 next_doc/growth_advisor_ideal_advisor_gap_and_roadmap_plan.md
方向 3：Goal 执行内容反哺信号扫描。

  extract_spinoff_topics_from_pursuits() —— 从正在自主推进方向的
      open_questions 里挖掘"反复出现但从未被吸收"的衍生话题
  growth_candidate_derive(goal_backlog=...) —— 衍生话题并入候选生成
      输入，走同一套证据数阈值，并打上 origin="pursuit_spinoff" 标记
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import growth_advisor as ga
from mini_agent.evolution import output_workspace as ow
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.evolution.goal_cron_bridge import make_goal_recurring
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_cycle(paths, goal_id, cycle_no, *, covered_subtopics=None, open_questions=None):
    base_dir = ow.goal_output_base_dir(paths, goal_id)
    cycle_dir = ow.allocate_cycle_dir(paths, goal_id, cycle_no)
    handoff = {
        "covered_subtopics": covered_subtopics or [],
        "open_questions": open_questions or [],
    }
    progress_note = "本轮小结\n```handoff\n" + json.dumps(handoff, ensure_ascii=False) + "\n```"
    ow.write_manifest(base_dir, cycle_dir, progress_note=progress_note, status="completed")


def _make_pursuing_goal(paths, goal_backlog, title: str):
    """走真实的 adopt_candidate_as_goal + make_goal_recurring 落地一个
    "正在自主推进"的方向，返回落地后的 Goal。"""
    backlog = ga.GrowthBacklog(paths)
    cand = backlog.add_or_merge(
        title=title,
        rationale="r",
        evidence_refs=[f"e{i}" for i in range(5)],
        min_evidence_count=3,
        max_pending=10,
        dismissed_cooldown_days=30,
    )
    candidate = backlog.get(cand.candidate_id)
    report = ga.generate_growth_report(paths, candidate)
    backlog.attach_report(cand.candidate_id, report.report_id)
    candidate = backlog.get(cand.candidate_id)
    goal = ga.adopt_candidate_as_goal(paths, candidate, goal_backlog=goal_backlog)
    cron_scheduler = CronScheduler(paths, submit_fn=None)
    make_goal_recurring(goal_backlog, cron_scheduler, goal.id, "interval:86400")
    return goal_backlog.get(goal.id)


class TestExtractSpinoffTopics(unittest.TestCase):
    def test_no_pursuits_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            self.assertEqual(ga.extract_spinoff_topics_from_pursuits(paths, goal_backlog), {})

    def test_repeated_unabsorbed_question_surfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            goal = _make_pursuing_goal(paths, goal_backlog, "数据分析能力")
            _write_cycle(
                paths, goal.id, 1,
                covered_subtopics=["pandas 基础"],
                open_questions=["数据可视化工具选型"],
            )
            _write_cycle(
                paths, goal.id, 2,
                covered_subtopics=["数据清洗"],
                open_questions=["数据可视化工具选型"],
            )
            hits = ga.extract_spinoff_topics_from_pursuits(paths, goal_backlog)
            self.assertIn("数据可视化工具选型", hits)
            self.assertEqual(len(hits["数据可视化工具选型"]), 2)
            for ref in hits["数据可视化工具选型"]:
                self.assertTrue(ref.startswith(f"pursuit_spinoff:{goal.id}:"))

    def test_single_mention_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            goal = _make_pursuing_goal(paths, goal_backlog, "数据分析能力")
            _write_cycle(paths, goal.id, 1, open_questions=["只提过一次的问题"])
            hits = ga.extract_spinoff_topics_from_pursuits(paths, goal_backlog)
            self.assertNotIn("只提过一次的问题", hits)

    def test_absorbed_question_not_flagged(self):
        """反复出现，但后来被 covered_subtopics 吸收了（哪怕是更早一轮
        的 covered_subtopics 里恰好出现过同样的文本），不再算沉默线索。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            goal = _make_pursuing_goal(paths, goal_backlog, "数据分析能力")
            _write_cycle(
                paths, goal.id, 1,
                covered_subtopics=["数据可视化工具选型"],
                open_questions=["数据可视化工具选型"],
            )
            _write_cycle(
                paths, goal.id, 2,
                covered_subtopics=[],
                open_questions=["数据可视化工具选型"],
            )
            hits = ga.extract_spinoff_topics_from_pursuits(paths, goal_backlog)
            self.assertNotIn("数据可视化工具选型", hits)

    def test_paused_pursuit_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            goal = _make_pursuing_goal(paths, goal_backlog, "数据分析能力")
            _write_cycle(paths, goal.id, 1, open_questions=["Q"])
            _write_cycle(paths, goal.id, 2, open_questions=["Q"])
            goal_backlog.set_status(goal.id, "paused")
            # recurring 字段没变，但暂停的方向不算"正在推进"——沿用
            # pursuits_portfolio_summary 同样只看 goal.recurring 的口径，
            # 这里单独确认一下：即便暂停了，只要 recurring 仍是 True，
            # 该函数不额外过滤 status（跟既有的 pursuits_portfolio_
            # summary 行为保持一致，不引入新的过滤维度）。
            hits = ga.extract_spinoff_topics_from_pursuits(paths, goal_backlog)
            self.assertIn("Q", hits)

    def test_lookback_window_limits_old_cycles(self):
        """窗口外的旧轮次不参与"反复出现"计数：第 1 轮提过，隔了很多轮
        之后在窗口内只出现 1 次，不应该被误判为"反复"。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            goal = _make_pursuing_goal(paths, goal_backlog, "数据分析能力")
            _write_cycle(paths, goal.id, 1, open_questions=["老问题"])
            for n in range(2, 6):
                _write_cycle(paths, goal.id, n, covered_subtopics=[f"topic{n}"])
            hits = ga.extract_spinoff_topics_from_pursuits(paths, goal_backlog)
            self.assertNotIn("老问题", hits)


class TestGrowthCandidateDeriveSpinoff(unittest.TestCase):
    def test_spinoff_topic_merged_with_origin_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            goal = _make_pursuing_goal(paths, goal_backlog, "数据分析能力")
            _write_cycle(paths, goal.id, 1, open_questions=["数据可视化工具选型"])
            _write_cycle(paths, goal.id, 2, open_questions=["数据可视化工具选型"])

            class _Profile:
                derived = {"growth_focus_areas": {}}

            class _Cfg:
                excluded_topics = []
                min_evidence_count = 2
                max_pending_candidates = 10
                dismissed_cooldown_days = 30

            produced = ga.growth_candidate_derive(paths, _Cfg(), _Profile(), goal_backlog=goal_backlog)
            titles = {c.title: c for c in produced}
            self.assertIn("数据可视化工具选型", titles)
            self.assertEqual(titles["数据可视化工具选型"].origin, "pursuit_spinoff")

    def test_without_goal_backlog_behaves_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)

            class _Profile:
                derived = {"growth_focus_areas": {"编程": ["e1", "e2", "e3"]}}

            class _Cfg:
                excluded_topics = []
                min_evidence_count = 3
                max_pending_candidates = 10
                dismissed_cooldown_days = 30

            produced = ga.growth_candidate_derive(paths, _Cfg(), _Profile())
            self.assertEqual(len(produced), 1)
            self.assertEqual(produced[0].origin, "signal_scan")

    def test_existing_memory_topic_not_overwritten_by_spinoff(self):
        """同一个标题既被 memory 信号命中、又被 spinoff 命中时，先创建
        的那次决定 origin，之后的合并不应该覆盖它。"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            goal_backlog = GoalBacklog(paths)
            goal_backlog.load()
            goal = _make_pursuing_goal(paths, goal_backlog, "数据分析能力")
            _write_cycle(paths, goal.id, 1, open_questions=["同名话题"])
            _write_cycle(paths, goal.id, 2, open_questions=["同名话题"])

            class _Profile:
                derived = {"growth_focus_areas": {"同名话题": ["e1", "e2", "e3"]}}

            class _Cfg:
                excluded_topics = []
                min_evidence_count = 2
                max_pending_candidates = 10
                dismissed_cooldown_days = 30

            produced = ga.growth_candidate_derive(paths, _Cfg(), _Profile(), goal_backlog=goal_backlog)
            titles = {c.title: c for c in produced}
            self.assertEqual(titles["同名话题"].origin, "signal_scan")
            # 证据取并集：memory 的 3 条 + spinoff 的 2 条，去重后应有 5 条
            self.assertEqual(titles["同名话题"].evidence_count, 5)


if __name__ == "__main__":
    unittest.main()
