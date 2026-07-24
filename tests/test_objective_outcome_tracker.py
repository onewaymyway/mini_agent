"""
tests/test_objective_outcome_tracker.py

覆盖 next_doc/kanban_and_autonomy_improvement_plan.md Track H
（效果回填闭环到目标推导优先级）：

- evolution/objective_outcome_tracker.py 本身：record_outcome() 滚动窗口、
  theme_failure_stats()/judge_theme() 的 skip/downweight/ok 三态判定。
- ObjectiveExecutor._on_objective_completed()/_on_objective_failed() 会把
  结果记到该模块（cancel() 不会）。
- SoftGoalDeriver.derive_candidates() 会依据历史失败率跳过/降权对应主题
  的候选，不影响其余候选。

运行方式（仓库暂无 pytest.ini/conftest.py 设置 PYTHONPATH，手动指定 src）：
    PYTHONPATH=src python3 -m pytest tests/test_objective_outcome_tracker.py -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution import objective_outcome_tracker as ot
from mini_agent.evolution.objective_executor import ObjectiveExecutor
from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver, _DeriveCandidate
from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
from mini_agent.storage.paths import AgentPaths


def _make_objective(backlog: GoalBacklog, title: str) -> GoalNode:
    goal = backlog.add_goal(title=f"{title}-goal", description="", source="user", priority=50)
    objs = backlog.add_objectives_for_goal(goal.id, [title])
    return objs[0]


class _FakeSubmitter:
    def __init__(self):
        self._n = 0

    def __call__(self, message: str, initiator: str, meta: dict):
        self._n += 1
        return f"turn_{self._n}"


class TestNormalizeAndStats(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_normalize_title_key_is_order_and_case_insensitive(self):
        self.assertEqual(
            ot.normalize_title_key("改善 Foo 的可靠性"),
            ot.normalize_title_key("的可靠性 改善 foo"),
        )

    def test_no_history_returns_none(self):
        self.assertIsNone(ot.theme_failure_stats(self.paths, "从未出现过的主题"))
        self.assertEqual(ot.judge_theme(self.paths, "从未出现过的主题"), "ok")

    def test_insufficient_samples_stay_ok(self):
        ot.record_outcome(self.paths, "改善 X 的可靠性", "failed")
        ot.record_outcome(self.paths, "改善 X 的可靠性", "failed")
        # 只有 2 个样本，< MIN_SAMPLES_FOR_JUDGEMENT(3)
        self.assertEqual(ot.judge_theme(self.paths, "改善 X 的可靠性"), "ok")

    def test_high_failure_ratio_triggers_skip(self):
        for outcome in ("failed", "failed", "failed", "completed"):
            ot.record_outcome(self.paths, "改善 Y 的可靠性", outcome)
        total, failed = ot.theme_failure_stats(self.paths, "改善 Y 的可靠性")
        self.assertEqual((total, failed), (4, 3))
        self.assertEqual(ot.judge_theme(self.paths, "改善 Y 的可靠性"), "skip")

    def test_medium_failure_ratio_triggers_downweight(self):
        for outcome in ("failed", "failed", "completed", "completed", "completed"):
            ot.record_outcome(self.paths, "改善 Z 的可靠性", outcome)
        # 2/5 = 0.4，明确落在 downweight 区间（>= 0.34 且 < 0.66）
        self.assertEqual(ot.judge_theme(self.paths, "改善 Z 的可靠性"), "downweight")

    def test_low_failure_ratio_stays_ok(self):
        for outcome in ("completed", "completed", "completed", "failed"):
            ot.record_outcome(self.paths, "改善 W 的可靠性", outcome)
        self.assertEqual(ot.judge_theme(self.paths, "改善 W 的可靠性"), "ok")

    def test_cancelled_outcome_is_ignored(self):
        ot.record_outcome(self.paths, "改善 V 的可靠性", "cancelled")
        self.assertIsNone(ot.theme_failure_stats(self.paths, "改善 V 的可靠性"))

    def test_rolling_window_bounds_history(self):
        for _ in range(ot.MAX_HISTORY_PER_THEME + 5):
            ot.record_outcome(self.paths, "改善 U 的可靠性", "completed")
        total, _ = ot.theme_failure_stats(self.paths, "改善 U 的可靠性")
        self.assertEqual(total, ot.MAX_HISTORY_PER_THEME)


class TestObjectiveExecutorRecordsOutcome(unittest.TestCase):
    """ObjectiveExecutor 在 completed/failed 收尾时会记录主题结果，
    cancel() 不会。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.backlog = GoalBacklog(self.paths)
        self.submitter = _FakeSubmitter()
        self.oe = ObjectiveExecutor(
            paths=self.paths,
            submit_fn=self.submitter,
            llm_decompose_fn=lambda obj: [f"{obj.title} - 单步"],
            goal_backlog=self.backlog,
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_completed_execution_records_completed_outcome(self):
        obj = _make_objective(self.backlog, "写测试报告")
        exec_id = self.oe.start(obj)
        ex = self.oe.get_execution(exec_id)
        turn_id = ex.current_step.turn_id
        self.oe.on_turn_done(turn_id, "完成了唯一的一步。")
        stats = ot.theme_failure_stats(self.paths, "写测试报告")
        self.assertEqual(stats, (1, 0))

    def test_failed_execution_records_failed_outcome(self):
        obj = _make_objective(self.backlog, "修复一个搞不定的 bug")
        exec_id = self.oe.start(obj)
        ex = self.oe.get_execution(exec_id)
        turn_id = ex.current_step.turn_id
        # 连续失败耗尽重试次数，且不提供 redecompose 回调 → 最终判 failed
        for _ in range(10):
            ex = self.oe.get_execution(exec_id)
            if ex.status == "failed":
                break
            cur_turn = ex.current_step.turn_id
            self.oe.on_turn_failed(cur_turn, "工具调用报错")
        self.assertEqual(self.oe.get_execution(exec_id).status, "failed")
        stats = ot.theme_failure_stats(self.paths, "修复一个搞不定的 bug")
        self.assertIsNotNone(stats)
        self.assertGreaterEqual(stats[1], 1)

    def test_cancel_does_not_record_outcome(self):
        obj = _make_objective(self.backlog, "一个会被取消的目标")
        exec_id = self.oe.start(obj)
        self.oe.cancel(exec_id)
        self.assertIsNone(ot.theme_failure_stats(self.paths, "一个会被取消的目标"))


class TestSoftGoalDeriverGating(unittest.TestCase):
    """SoftGoalDeriver._apply_objective_outcome_gating 按历史失败率跳过/降权。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))
        self.cfg = type("Cfg", (), {})()
        self.deriver = SoftGoalDeriver(self.paths, self.cfg)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _cand(self, title: str) -> _DeriveCandidate:
        return _DeriveCandidate(title=title, description="", source_tag="lesson", urgency=1.0)

    def test_high_failure_theme_is_skipped(self):
        for outcome in ("failed", "failed", "failed"):
            ot.record_outcome(self.paths, "反复失败的主题", outcome)
        survivors = self.deriver._apply_objective_outcome_gating([self._cand("反复失败的主题")])
        self.assertEqual(survivors, [])

    def test_medium_failure_theme_is_downweighted_not_removed(self):
        for outcome in ("failed", "failed", "completed", "completed", "completed"):
            ot.record_outcome(self.paths, "偶尔失败的主题", outcome)
        cand = self._cand("偶尔失败的主题")
        survivors = self.deriver._apply_objective_outcome_gating([cand])
        self.assertEqual(len(survivors), 1)
        self.assertLess(survivors[0].urgency, 1.0)

    def test_unrelated_candidate_is_untouched(self):
        for outcome in ("failed", "failed", "failed"):
            ot.record_outcome(self.paths, "反复失败的主题", outcome)
        cand = self._cand("完全不相关的新主题")
        survivors = self.deriver._apply_objective_outcome_gating([cand])
        self.assertEqual(len(survivors), 1)
        self.assertEqual(survivors[0].urgency, 1.0)

    def test_no_history_leaves_candidates_untouched(self):
        cands = [self._cand("从没出现过的主题 A"), self._cand("从没出现过的主题 B")]
        survivors = self.deriver._apply_objective_outcome_gating(cands)
        self.assertEqual(len(survivors), 2)
        self.assertTrue(all(c.urgency == 1.0 for c in survivors))


if __name__ == "__main__":
    unittest.main()
