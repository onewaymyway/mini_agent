"""
tests/test_progress_tracker.py — ProgressTracker（§3.2 伪进展趋势识别）单元测试

覆盖范围：
  - 窗口未填满时不判定
  - 平缓但非零的分数序列 → 判定为伪进展
  - 分数有明显上升趋势 → 不判定为伪进展
  - 窗口内出现过高分（>= max_score_cap） → 不判定为伪进展（说明确实有过实质推进）
  - replay() 从历史分数重建窗口状态，不触发误判
  - StuckDetector.trigger_recovery() 与 observe()/observe_signal() 共享同一份
    恢复额度计数
"""

from __future__ import annotations

from mini_agent.role_agents.stuck_detector import ProgressTracker, StuckDetector, StuckSignal


def test_progress_tracker_returns_false_until_window_filled():
    tracker = ProgressTracker(window=5)
    for score in [0.1, 0.1, 0.1, 0.1]:
        assert tracker.observe(score) is False


def test_progress_tracker_detects_flat_but_nonzero_scores():
    """连续多轮都有一点点非零进展分数，但趋势没有实质抬升——应判定为伪进展。"""
    tracker = ProgressTracker(window=6, stagnation_score_threshold=0.15, max_score_cap=0.5)
    scores = [0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
    result = None
    for s in scores:
        result = tracker.observe(s)
    assert result is True


def test_progress_tracker_does_not_flag_upward_trend():
    """分数呈现明显上升趋势时，不应该被判定为伪进展。"""
    tracker = ProgressTracker(window=6, stagnation_score_threshold=0.15, max_score_cap=0.5)
    scores = [0.0, 0.1, 0.2, 0.5, 0.6, 0.7]
    result = None
    for s in scores:
        result = tracker.observe(s)
    assert result is False


def test_progress_tracker_high_score_in_window_prevents_false_positive():
    """窗口内出现过明显高分（>= max_score_cap），说明确实发生过实质推进，
    即使早期/后期均值差很小也不应该判定为伪进展。"""
    tracker = ProgressTracker(window=5, stagnation_score_threshold=0.15, max_score_cap=0.5)
    scores = [0.9, 0.05, 0.05, 0.05, 0.05]
    result = None
    for s in scores:
        result = tracker.observe(s)
    assert result is False


def test_progress_tracker_replay_reconstructs_window_without_triggering():
    """replay() 用于从落盘的历史分数重建窗口状态，本身不应该有返回值副作用
    （不是 observe() 循环），重建之后紧接着 observe() 才应该按正常逻辑判断。"""
    tracker = ProgressTracker(window=5, stagnation_score_threshold=0.15, max_score_cap=0.5)
    tracker.replay([0.1, 0.1, 0.1, 0.1])
    # 重建后窗口里已经有 4 个 0.1，再 observe 一个 0.1 填满窗口，应判定为伪进展
    assert tracker.observe(0.1) is True


def test_progress_tracker_reset_clears_window():
    tracker = ProgressTracker(window=3)
    tracker.observe(0.1)
    tracker.observe(0.1)
    tracker.reset()
    # reset 之后窗口清空，再观察 2 个值不足以填满 window=3，应该是 False
    assert tracker.observe(0.1) is False
    assert tracker.observe(0.1) is False


def test_stuck_detector_trigger_recovery_shares_budget_with_observe():
    """trigger_recovery() 应该和 observe()/observe_signal() 共享同一份
    max_recoveries 额度，不会因为触发路径不同而获得额外的恢复次数。"""
    detector = StuckDetector(consecutive_limit=3, max_recoveries=1)

    # 先用 observe_signal 正常消耗一次恢复额度
    assert detector.observe_signal(is_same=True) is StuckSignal.NONE
    assert detector.observe_signal(is_same=True) is StuckSignal.RECOVER
    assert detector.recoveries_used == 1

    # 额度已耗尽，trigger_recovery() 应该直接 GIVE_UP，而不是重新获得一次额度
    assert detector.trigger_recovery() is StuckSignal.GIVE_UP


def test_stuck_detector_trigger_recovery_consumes_budget_independently():
    """trigger_recovery() 本身在额度充足时也能正常消耗一次额度并重置连续计数。"""
    detector = StuckDetector(consecutive_limit=3, max_recoveries=2)
    assert detector.trigger_recovery() is StuckSignal.RECOVER
    assert detector.recoveries_used == 1
    assert detector.consecutive_same == 0
    assert detector.trigger_recovery() is StuckSignal.RECOVER
    assert detector.recoveries_used == 2
    assert detector.trigger_recovery() is StuckSignal.GIVE_UP
