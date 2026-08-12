"""tests/test_growth_advisor_pursuit_style_reclassify_and_report_upgrade.py

覆盖 next_doc/growth_advisor_ideal_advisor_gap_and_roadmap_plan.md
新追加的两个方向：

  方向 6 动态修正 —— maybe_reclassify_pursuit_style()：累计满 N 轮后
    用实际产出内容重新分类调研风格，而不是只依赖落地时的候选标题。
  方向 7 —— 报告质量自动闭环：_should_auto_upgrade_report_quality() +
    generate_growth_report(quality_auto_upgraded=...)。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mini_agent.evolution import growth_advisor as ga
from mini_agent.evolution import output_workspace as ow
from mini_agent.perception.goal_backlog import GoalBacklog
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


class TestRecentCoveredSubtopicsText(unittest.TestCase):
    def test_empty_when_no_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            text = ga._recent_covered_subtopics_text(paths, "goal-x", 5)
            self.assertEqual(text, "")

    def test_aggregates_recent_cycles(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_cycle(paths, "goal-x", 1, ["编程 基础语法"])
            _write_cycle(paths, "goal-x", 2, ["实战 项目"])
            text = ga._recent_covered_subtopics_text(paths, "goal-x", 5)
            self.assertIn("编程", text)
            self.assertIn("实战", text)


class TestMaybeReclassifyPursuitStyle(unittest.TestCase):
    def _make_goal(self, tmp, style="知识理论类", title="宏观经济学思考"):
        paths = _make_paths(tmp)
        backlog = GoalBacklog(paths)
        goal = backlog.add_goal(title=title, description="", tags=["growth_advisor"])
        backlog.update_fields(goal.id, growth_pursuit_style=style)
        goal.growth_pursuit_style = style
        return paths, backlog, goal

    def test_skips_non_growth_advisor_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            backlog = GoalBacklog(paths)
            goal = backlog.add_goal(title="随便什么", description="", tags=["other"])
            result = ga.maybe_reclassify_pursuit_style(paths, backlog, goal, 8, cfg=None)
            self.assertIsNone(result)

    def test_skips_when_cycle_not_multiple(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, backlog, goal = self._make_goal(tmp)
            _write_cycle(paths, goal.id, 1, ["编程 项目实战"])
            for n in (1, 2, 3, 7, 9):
                self.assertIsNone(
                    ga.maybe_reclassify_pursuit_style(paths, backlog, goal, n, cfg=None)
                )

    def test_skips_when_disabled_via_cfg(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, backlog, goal = self._make_goal(tmp)
            _write_cycle(paths, goal.id, 1, ["编程 项目实战"])
            cfg = SimpleNamespace(pursuit_style_reclassify_every_n_cycles=0)
            self.assertIsNone(
                ga.maybe_reclassify_pursuit_style(paths, backlog, goal, 8, cfg=cfg)
            )

    def test_no_change_returns_none_and_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, backlog, goal = self._make_goal(tmp, style="技能实操类")
            _write_cycle(paths, goal.id, 1, ["编程 项目实战 代码"])
            result = ga.maybe_reclassify_pursuit_style(paths, backlog, goal, 8, cfg=None)
            self.assertIsNone(result)  # 规则式重新分类结果仍是"技能实操类"，未变化

    def test_reclassifies_based_on_actual_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 初始按标题猜成"知识理论类"，但实际产出内容明显偏习惯打卡。
            paths, backlog, goal = self._make_goal(tmp, style="知识理论类", title="早起这件事")
            _write_cycle(paths, goal.id, 1, ["习惯 打卡 坚持 早起"])
            result = ga.maybe_reclassify_pursuit_style(paths, backlog, goal, 8, cfg=None)
            self.assertEqual(result, "习惯养成类")
            self.assertEqual(goal.growth_pursuit_style, "习惯养成类")
            reloaded = backlog.get(goal.id)
            self.assertEqual(reloaded.growth_pursuit_style, "习惯养成类")

    def test_no_available_text_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, backlog, goal = self._make_goal(tmp)
            # 没有任何 manifest，_recent_covered_subtopics_text 返回空
            result = ga.maybe_reclassify_pursuit_style(paths, backlog, goal, 8, cfg=None)
            self.assertIsNone(result)


class TestShouldAutoUpgradeReportQuality(unittest.TestCase):
    def _make_candidate(self, paths, title="React 学习"):
        backlog = ga.GrowthBacklog(paths)
        candidate = ga.GrowthCandidate(
            candidate_id="cand-1", title=title, rationale="值得关注",
        )
        backlog.save_all([candidate])
        return candidate

    def test_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = self._make_candidate(paths)
            self.assertFalse(ga._should_auto_upgrade_report_quality(paths, candidate, cfg=None))

    def test_enabled_but_below_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = self._make_candidate(paths)
            ledger = ga.GrowthFeedbackLedger(paths)
            ledger.record(
                candidate.candidate_id, ga.STATUS_DISMISSED,
                reason=ga.DISMISS_REASON_REPORT_NOT_USEFUL,
            )
            cfg = SimpleNamespace(
                report_quality_auto_upgrade_enabled=True,
                report_quality_auto_upgrade_threshold=2,
            )
            self.assertFalse(ga._should_auto_upgrade_report_quality(paths, candidate, cfg=cfg))

    def test_enabled_and_meets_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = self._make_candidate(paths)
            ledger = ga.GrowthFeedbackLedger(paths)
            for _ in range(2):
                ledger.record(
                    candidate.candidate_id, ga.STATUS_DISMISSED,
                    reason=ga.DISMISS_REASON_REPORT_NOT_USEFUL,
                )
            cfg = SimpleNamespace(
                report_quality_auto_upgrade_enabled=True,
                report_quality_auto_upgrade_threshold=2,
            )
            self.assertTrue(ga._should_auto_upgrade_report_quality(paths, candidate, cfg=cfg))

    def test_zero_threshold_treated_as_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = self._make_candidate(paths)
            ledger = ga.GrowthFeedbackLedger(paths)
            ledger.record(
                candidate.candidate_id, ga.STATUS_DISMISSED,
                reason=ga.DISMISS_REASON_REPORT_NOT_USEFUL,
            )
            cfg = SimpleNamespace(
                report_quality_auto_upgrade_enabled=True,
                report_quality_auto_upgrade_threshold=0,
            )
            self.assertFalse(ga._should_auto_upgrade_report_quality(paths, candidate, cfg=cfg))


class TestGenerateGrowthReportQualityAutoUpgraded(unittest.TestCase):
    def test_default_false_no_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = ga.GrowthCandidate(
                candidate_id="cand-1", title="React 学习", rationale="值得关注",
            )
            report = ga.generate_growth_report(paths, candidate)
            self.assertFalse(report.quality_auto_upgraded)
            body = Path(report.body_path).read_text(encoding="utf-8")
            self.assertNotIn("自动换成了更详细的生成方式", body)

    def test_true_adds_note_and_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            candidate = ga.GrowthCandidate(
                candidate_id="cand-1", title="React 学习", rationale="值得关注",
            )
            report = ga.generate_growth_report(
                paths, candidate,
                llm_helper=lambda p: "详细正文内容",
                quality_auto_upgraded=True,
            )
            self.assertTrue(report.quality_auto_upgraded)
            body = Path(report.body_path).read_text(encoding="utf-8")
            self.assertIn("自动换成了更详细的生成方式", body)

    def test_from_dict_backfills_default_false(self):
        d = {
            "report_id": "r1", "candidate_id": "c1", "title": "t",
            "slug": "s", "summary": "sum", "body_path": "/tmp/x",
        }
        report = ga.GrowthReport.from_dict(d)
        self.assertFalse(report.quality_auto_upgraded)


if __name__ == "__main__":
    unittest.main()
