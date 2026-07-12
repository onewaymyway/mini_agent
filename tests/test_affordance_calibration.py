"""
tests/test_affordance_calibration.py — 方案四：Affordance 闭环校准测试

覆盖：
  1. 构造已 resolved 的 outcome_tracking 记录，验证权重正确上调/下调。
  2. 验证权重不会突破 [0.3, 2.0] 边界。
  3. 关联失败（找不到对应来源）时不影响其它来源权重。
  4. weights=None 时 AffordanceAnalyzer.analyze() 结果与改造前完全一致。
"""

from __future__ import annotations

import unittest
from unittest import mock

from mini_agent.perception.affordance_calibration import (
    AffordanceWeights,
    WEIGHT_MAX,
    WEIGHT_MIN,
    _classify_source,
    calibrate,
)
from mini_agent.perception.affordance_analyzer import AffordanceAnalyzer


class _FakeTrackedCommit:
    def __init__(self, commit_summary, verdict, status="resolved"):
        self.commit_summary = commit_summary
        self.verdict = verdict
        self.status = status


class TestClassifySource(unittest.TestCase):
    def test_classifies_known_issue_text(self):
        self.assertEqual(_classify_source("修复了一个 bug"), "known_issues")

    def test_classifies_unexplored_text(self):
        self.assertEqual(_classify_source("探索能力盲区：python_refactor"), "unexplored_areas")

    def test_classifies_risk_text(self):
        self.assertEqual(_classify_source("误删了文件，已回退"), "high_risk_zones")

    def test_no_match_returns_empty(self):
        self.assertEqual(_classify_source("普通的重构工作"), "")


class TestCalibrate(unittest.TestCase):
    def test_weights_adjust_on_improved_and_worsened(self):
        records = [
            _FakeTrackedCommit("修复了一个已知 bug", "improved"),
            _FakeTrackedCommit("误删文件后已回退", "worsened"),
        ]

        # [修复] 此前用 mock.patch.dict("sys.modules", ...) 整体替换模块，
        # 对 "from package import submodule" 形式的导入不是导入顺序无关的
        # ——一旦 outcome_tracker 之前已被真实导入过（任何测试文件在收集
        # 阶段 import 它都会触发），parent package 上已缓存的真实模块属性
        # 会被 getattr() 直接命中，完全绕过 sys.modules 补丁，calibrate()
        # 拿到的是真实的 outcome_tracker.get_all，而不是这里构造的假记录。
        # 直接 patch 真实模块对象上的 get_all 属性，不依赖导入时序。
        with mock.patch(
            "mini_agent.evolution.outcome_tracker.get_all", return_value=records
        ):
            paths = mock.MagicMock()
            paths.workdir_dir = mock.MagicMock()
            fake_path_obj = mock.MagicMock()
            fake_path_obj.exists.return_value = False
            paths.workdir_dir.__truediv__.return_value = fake_path_obj

            weights = calibrate(paths)
            self.assertGreater(weights.known_issues_weight, 1.0)
            self.assertLess(weights.high_risk_zones_weight, 1.0)

    def test_weights_clamped_within_bounds(self):
        w = AffordanceWeights(known_issues_weight=10.0, unexplored_areas_weight=0.0001, high_risk_zones_weight=1.0)
        w.clamp()
        self.assertLessEqual(w.known_issues_weight, WEIGHT_MAX)
        self.assertGreaterEqual(w.unexplored_areas_weight, WEIGHT_MIN)

    def test_unassociated_record_does_not_affect_weights(self):
        records = [_FakeTrackedCommit("普通的重构工作", "improved")]

        # 同上：改为直接 patch 真实模块属性，不依赖导入时序。
        with mock.patch(
            "mini_agent.evolution.outcome_tracker.get_all", return_value=records
        ):
            paths = mock.MagicMock()
            fake_path_obj = mock.MagicMock()
            fake_path_obj.exists.return_value = False
            paths.workdir_dir.__truediv__.return_value = fake_path_obj

            weights = calibrate(paths)
            self.assertEqual(weights.known_issues_weight, 1.0)
            self.assertEqual(weights.unexplored_areas_weight, 1.0)
            self.assertEqual(weights.high_risk_zones_weight, 1.0)


class TestAffordanceAnalyzerWeightsRegression(unittest.TestCase):
    def test_none_weights_matches_default_weights(self):
        open_threads = [
            type("OT", (), {"status": "open", "title": "修复登录 bug", "priority": "high", "type": "bug"})()
        ]
        result_none = AffordanceAnalyzer().analyze(open_threads=open_threads, weights=None)
        result_default = AffordanceAnalyzer().analyze(open_threads=open_threads, weights=AffordanceWeights())
        self.assertEqual(result_none.top_opportunities, result_default.top_opportunities)


if __name__ == "__main__":
    unittest.main()
