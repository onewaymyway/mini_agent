"""tests/test_execution_phase.py

覆盖 next_doc/goal_execution_phase_improvement_plan.md 的核心行为：
  1. ExecutionPhaseState 数据模型：to_dict/from_dict 往返
  2. 存储：load_phase（默认状态）/ save_phase / 独立文件不进 goals.json
  3. set_mode / unlock_mode：手动切换、隐式锁定、显式 lock 参数
  4. resolve_effective_mode：锁定/非 auto 直接返回；auto 模式下的规则判定
  5. goal_cron_bridge._append_execution_phase_context：未初始化时不改变
     description、有 phase 状态时正确拼进对应片段
"""

from __future__ import annotations

from mini_agent.perception import execution_phase as ep
from mini_agent.storage.paths import AgentPaths


def _paths(tmp_path) -> AgentPaths:
    return AgentPaths(project_root=tmp_path)


# ── 数据模型 / 存储 ──────────────────────────────────────────────────────────

def test_load_phase_default_when_missing(tmp_path):
    paths = _paths(tmp_path)
    state = ep.load_phase(paths, "goal_1")
    assert state.mode == "auto"
    assert state.locked is False
    assert state.goal_id == "goal_1"


def test_save_and_load_roundtrip(tmp_path):
    paths = _paths(tmp_path)
    state = ep.ExecutionPhaseState(goal_id="goal_1", mode="stable", locked=True, stability_score=0.9)
    ep.save_phase(paths, state)

    loaded = ep.load_phase(paths, "goal_1")
    assert loaded.mode == "stable"
    assert loaded.locked is True
    assert loaded.stability_score == 0.9

    # 独立文件，不进 goals.json
    phase_file = tmp_path / ".agent" / "goal_execution_phase" / "goal_1.json"
    assert phase_file.exists()
    goals_json = tmp_path / ".agent" / "goals.json"
    assert not goals_json.exists()


def test_load_phase_corrupted_file_falls_back_to_default(tmp_path):
    paths = _paths(tmp_path)
    d = tmp_path / ".agent" / "goal_execution_phase"
    d.mkdir(parents=True)
    (d / "goal_1.json").write_text("{not valid json", encoding="utf-8")
    state = ep.load_phase(paths, "goal_1")
    assert state.mode == "auto"


# ── 手动切换 ─────────────────────────────────────────────────────────────────

def test_set_mode_non_auto_implicitly_locks(tmp_path):
    paths = _paths(tmp_path)
    state = ep.set_mode(paths, "goal_1", "stable")
    assert state.mode == "stable"
    assert state.locked is True


def test_set_mode_auto_defaults_unlocked(tmp_path):
    paths = _paths(tmp_path)
    ep.set_mode(paths, "goal_1", "stable")
    state = ep.set_mode(paths, "goal_1", "auto")
    assert state.mode == "auto"
    assert state.locked is False


def test_set_mode_explicit_lock_override(tmp_path):
    paths = _paths(tmp_path)
    state = ep.set_mode(paths, "goal_1", "explore", lock=False)
    assert state.mode == "explore"
    assert state.locked is False


def test_set_mode_invalid_raises(tmp_path):
    paths = _paths(tmp_path)
    try:
        ep.set_mode(paths, "goal_1", "not_a_mode")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_unlock_mode(tmp_path):
    paths = _paths(tmp_path)
    ep.set_mode(paths, "goal_1", "stable")
    state = ep.unlock_mode(paths, "goal_1")
    assert state.locked is False
    assert state.mode == "stable"


def test_mode_history_recorded(tmp_path):
    paths = _paths(tmp_path)
    ep.set_mode(paths, "goal_1", "explore")
    state = ep.set_mode(paths, "goal_1", "stable")
    assert len(state.mode_history) >= 1
    assert state.mode_history[-1].to_mode == "stable"


# ── 自动判定 ─────────────────────────────────────────────────────────────────

def test_resolve_locked_state_ignores_rules(tmp_path):
    state = ep.ExecutionPhaseState(goal_id="g1", mode="tidy", locked=True)
    effective, _ = ep.resolve_effective_mode(
        state, cycle_no=99, spec_confirmed=True, spec_recently_revised=False, miss_streak=0
    )
    assert effective == "tidy"


def test_resolve_explicit_non_auto_mode_ignores_rules(tmp_path):
    state = ep.ExecutionPhaseState(goal_id="g1", mode="converge", locked=False)
    effective, _ = ep.resolve_effective_mode(
        state, cycle_no=1, spec_confirmed=False, spec_recently_revised=True, miss_streak=5
    )
    assert effective == "converge"


def test_resolve_auto_early_cycles_explore(tmp_path):
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto")
    effective, _ = ep.resolve_effective_mode(
        state, cycle_no=1, spec_confirmed=False, spec_recently_revised=True, miss_streak=0
    )
    assert effective == "explore"


def test_resolve_auto_confirmed_stable_spec_gives_stable(tmp_path):
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto")
    effective, _ = ep.resolve_effective_mode(
        state, cycle_no=10, spec_confirmed=True, spec_recently_revised=False, miss_streak=0
    )
    assert effective == "stable"
    assert state.stability_score == 1.0


def test_resolve_auto_high_miss_streak_stays_explore(tmp_path):
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto")
    effective, _ = ep.resolve_effective_mode(
        state, cycle_no=10, spec_confirmed=True, spec_recently_revised=False, miss_streak=3
    )
    assert effective == "explore"


# ── goal_cron_bridge 接入 ────────────────────────────────────────────────────

def test_bridge_append_execution_phase_context_default_state(tmp_path):
    from mini_agent.evolution import goal_cron_bridge as bridge

    class _FakeGoal:
        id = "goal_1"
        execution_spec_confirmed = False

    paths = _paths(tmp_path)
    result = bridge._append_execution_phase_context(paths, _FakeGoal(), 1, "base description")
    assert "base description" in result
    assert "探索期" in result  # 早期轮次默认判定为 explore


def test_bridge_append_execution_phase_context_paths_none_noop():
    from mini_agent.evolution import goal_cron_bridge as bridge

    class _FakeGoal:
        id = "goal_1"
        execution_spec_confirmed = False

    result = bridge._append_execution_phase_context(None, _FakeGoal(), 1, "base description")
    assert result == "base description"


def test_bridge_append_execution_phase_context_locked_stable(tmp_path):
    from mini_agent.evolution import goal_cron_bridge as bridge

    class _FakeGoal:
        id = "goal_1"
        execution_spec_confirmed = False

    paths = _paths(tmp_path)
    ep.set_mode(paths, "goal_1", "stable")
    result = bridge._append_execution_phase_context(paths, _FakeGoal(), 1, "base description")
    assert "稳定期" in result
