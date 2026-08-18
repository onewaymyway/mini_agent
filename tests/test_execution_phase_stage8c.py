"""tests/test_execution_phase_stage8c.py

覆盖 next_doc/goal_output_directory_and_execution_phase_redesign_plan.md
Stage 8c：`compute_routine_stability_signal()` 接入 `resolve_effective_mode()`
判定路径——仅在 target 因 `soft_check_miss_streak==1` 被粗规则兜底判为
converge 时，`routine_stability=True` 才把它提升为 running；explore、以及
被 `progress_trend_stuck` 降级出的 converge，均不受影响。
"""
from __future__ import annotations

from mini_agent.perception import execution_phase as ep


def _auto_state() -> ep.ExecutionPhaseState:
    return ep.ExecutionPhaseState(goal_id="g1", mode="auto", locked=False)


class TestRoutineStabilityUpgradesMissStreakConverge:
    def test_routine_stability_true_upgrades_miss_streak_converge_to_running(self):
        state = _auto_state()
        mode, _ = ep.resolve_effective_mode(
            state,
            cycle_no=10,
            spec_confirmed=True,
            spec_recently_revised=False,
            miss_streak=1,
            routine_stability=True,
        )
        assert mode == "running"

    def test_routine_stability_false_keeps_converge(self):
        state = _auto_state()
        mode, _ = ep.resolve_effective_mode(
            state,
            cycle_no=10,
            spec_confirmed=True,
            spec_recently_revised=False,
            miss_streak=1,
            routine_stability=False,
        )
        assert mode == "converge"

    def test_routine_stability_none_keeps_converge_default_behavior(self):
        state = _auto_state()
        mode, _ = ep.resolve_effective_mode(
            state,
            cycle_no=10,
            spec_confirmed=True,
            spec_recently_revised=False,
            miss_streak=1,
        )
        assert mode == "converge"


class TestRoutineStabilityDoesNotAffectOtherPaths:
    def test_routine_stability_true_does_not_pull_explore_forward(self):
        state = _auto_state()
        mode, _ = ep.resolve_effective_mode(
            state,
            cycle_no=1,
            spec_confirmed=False,
            spec_recently_revised=True,
            miss_streak=0,
            routine_stability=True,
        )
        assert mode == "explore"

    def test_routine_stability_true_does_not_override_progress_trend_downgrade(self):
        """running 被 progress_trend_stuck 降级出的 converge，
        routine_stability=True 不应把它拉回 running——这是两个不同层级的
        信号，避免互相拉扯出抖动。"""
        state = _auto_state()
        mode, _ = ep.resolve_effective_mode(
            state,
            cycle_no=10,
            spec_confirmed=True,
            spec_recently_revised=False,
            miss_streak=0,
            progress_trend_stuck=True,
            routine_stability=True,
        )
        assert mode == "converge"

    def test_routine_stability_true_no_effect_when_already_running(self):
        state = _auto_state()
        mode, _ = ep.resolve_effective_mode(
            state,
            cycle_no=10,
            spec_confirmed=True,
            spec_recently_revised=False,
            miss_streak=0,
            routine_stability=True,
        )
        assert mode == "running"

    def test_locked_manual_mode_ignores_routine_stability(self):
        state = ep.ExecutionPhaseState(goal_id="g1", mode="converge", locked=True)
        mode, _ = ep.resolve_effective_mode(
            state,
            cycle_no=10,
            spec_confirmed=True,
            spec_recently_revised=False,
            miss_streak=1,
            routine_stability=True,
        )
        assert mode == "converge"
