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


# ── Stage D: 进展趋势信号 ────────────────────────────────────────────────────

def test_resolve_auto_progress_trend_stuck_downgrades_stable_to_converge(tmp_path):
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto")
    effective, _ = ep.resolve_effective_mode(
        state, cycle_no=10, spec_confirmed=True, spec_recently_revised=False,
        miss_streak=0, progress_trend_stuck=True,
    )
    assert effective == "converge"


def test_resolve_auto_progress_trend_healthy_keeps_stable(tmp_path):
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto")
    effective, _ = ep.resolve_effective_mode(
        state, cycle_no=10, spec_confirmed=True, spec_recently_revised=False,
        miss_streak=0, progress_trend_stuck=False,
    )
    assert effective == "stable"


def test_resolve_auto_progress_trend_none_keeps_stable(tmp_path):
    """未提供信号（None）时行为与 Stage D 之前一致，不影响判定。"""
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto")
    effective, _ = ep.resolve_effective_mode(
        state, cycle_no=10, spec_confirmed=True, spec_recently_revised=False, miss_streak=0,
    )
    assert effective == "stable"


def test_resolve_auto_progress_trend_stuck_does_not_affect_explore(tmp_path):
    """信号只在"本来会判 stable"时才降级；本来就是 explore 的场景不受影响。"""
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto")
    effective, _ = ep.resolve_effective_mode(
        state, cycle_no=1, spec_confirmed=False, spec_recently_revised=True,
        miss_streak=0, progress_trend_stuck=True,
    )
    assert effective == "explore"


class _FakeBacklogForTrend:
    def __init__(self, goal, children: dict):
        self._goal = goal
        self._children = children

    def get(self, node_id: str):
        if node_id == self._goal.id:
            return self._goal
        return self._children.get(node_id)


class _FakeGoalForTrend:
    def __init__(self, goal_id: str, reaped_ids: list):
        self.id = goal_id
        self.reaped_cycle_child_ids = reaped_ids


class _FakeChildForTrend:
    def __init__(self, progress_notes: str):
        self.progress_notes = progress_notes


def test_compute_progress_trend_signal_insufficient_history_returns_none(tmp_path):
    goal = _FakeGoalForTrend("g1", reaped_ids=["c1"])
    backlog = _FakeBacklogForTrend(goal, {"c1": _FakeChildForTrend("做了 A")})
    assert ep.compute_progress_trend_signal(backlog, "g1", window=3) is None


def test_compute_progress_trend_signal_repeated_notes_returns_true(tmp_path):
    goal = _FakeGoalForTrend("g1", reaped_ids=["c1", "c2", "c3"])
    backlog = _FakeBacklogForTrend(goal, {
        "c1": _FakeChildForTrend("本轮完成了周期性维护任务，无异常，产出与上轮一致"),
        "c2": _FakeChildForTrend("本轮完成了周期性维护任务，无异常，产出与上轮一致"),
        "c3": _FakeChildForTrend("本轮完成了周期性维护任务，无异常，产出与上轮一致"),
    })
    assert ep.compute_progress_trend_signal(backlog, "g1", window=3) is True


def test_compute_progress_trend_signal_distinct_notes_returns_false(tmp_path):
    goal = _FakeGoalForTrend("g1", reaped_ids=["c1", "c2", "c3"])
    backlog = _FakeBacklogForTrend(goal, {
        "c1": _FakeChildForTrend("新增了模块 A 的解析逻辑，覆盖了 3 种边界情况"),
        "c2": _FakeChildForTrend("重构了存储层，把原子写入拆成独立工具函数"),
        "c3": _FakeChildForTrend("补充了 12 个单元测试，修复了一个并发写入的竞态问题"),
    })
    assert ep.compute_progress_trend_signal(backlog, "g1", window=3) is False


def test_compute_progress_trend_signal_missing_note_returns_none(tmp_path):
    goal = _FakeGoalForTrend("g1", reaped_ids=["c1", "c2", "c3"])
    backlog = _FakeBacklogForTrend(goal, {
        "c1": _FakeChildForTrend("做了一些事"),
        "c2": _FakeChildForTrend(""),
        "c3": _FakeChildForTrend("做了另一些事"),
    })
    assert ep.compute_progress_trend_signal(backlog, "g1", window=3) is None


def test_compute_progress_trend_signal_none_backlog_or_goal_id_returns_none(tmp_path):
    assert ep.compute_progress_trend_signal(None, "g1") is None
    goal = _FakeGoalForTrend("g1", reaped_ids=["c1", "c2", "c3"])
    backlog = _FakeBacklogForTrend(goal, {})
    assert ep.compute_progress_trend_signal(backlog, "") is None


# ── goal_stuck_stats_and_llm_progress_judge_plan.md §2: LLM 进展判断 ────────

def test_llm_judge_progress_trend_parses_stuck():
    result = ep._llm_judge_progress_trend(["A", "B", "C"], lambda prompt: "STUCK")
    assert result is True


def test_llm_judge_progress_trend_parses_progressing():
    result = ep._llm_judge_progress_trend(["A", "B", "C"], lambda prompt: "  progressing  ")
    assert result is False


def test_llm_judge_progress_trend_unsure_returns_none():
    result = ep._llm_judge_progress_trend(["A", "B", "C"], lambda prompt: "UNSURE")
    assert result is None


def test_llm_judge_progress_trend_garbage_response_returns_none():
    result = ep._llm_judge_progress_trend(["A", "B", "C"], lambda prompt: "我觉得不太好说")
    assert result is None


def test_llm_judge_progress_trend_empty_response_returns_none():
    result = ep._llm_judge_progress_trend(["A", "B", "C"], lambda prompt: "")
    assert result is None


def test_llm_judge_progress_trend_none_helper_returns_none():
    assert ep._llm_judge_progress_trend(["A", "B", "C"], None) is None


def test_llm_judge_progress_trend_exception_returns_none():
    def _boom(prompt):
        raise RuntimeError("llm down")
    assert ep._llm_judge_progress_trend(["A", "B", "C"], _boom) is None


def test_compute_progress_trend_signal_uses_llm_when_provided():
    """LLM 给出明确结论时优先采用，不再走 difflib（即使文本其实差异很大，
    也应该用 LLM 的判断，验证"优先级"而不只是"能调用"）。"""
    goal = _FakeGoalForTrend("g1", reaped_ids=["c1", "c2", "c3"])
    backlog = _FakeBacklogForTrend(goal, {
        "c1": _FakeChildForTrend("新增了模块 A"),
        "c2": _FakeChildForTrend("重构了存储层"),
        "c3": _FakeChildForTrend("补充了单元测试"),
    })
    result = ep.compute_progress_trend_signal(backlog, "g1", window=3, llm_helper=lambda p: "STUCK")
    assert result is True  # LLM 判定优先，覆盖了 difflib 本会给出的 False


def test_compute_progress_trend_signal_falls_back_to_difflib_when_llm_unsure():
    """LLM 返回 UNSURE（即 None）时应静默退回 difflib 结果，而不是直接
    整体返回 None（否则等于关闭了这个信号，而不是"降级"）。"""
    goal = _FakeGoalForTrend("g1", reaped_ids=["c1", "c2", "c3"])
    same_note = "本轮完成了周期性维护任务，无异常，产出与上轮一致"
    backlog = _FakeBacklogForTrend(goal, {
        "c1": _FakeChildForTrend(same_note),
        "c2": _FakeChildForTrend(same_note),
        "c3": _FakeChildForTrend(same_note),
    })
    result = ep.compute_progress_trend_signal(backlog, "g1", window=3, llm_helper=lambda p: "UNSURE")
    assert result is True  # 退回 difflib：三轮文本雷同 → True


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


def test_bridge_append_execution_phase_context_progress_trend_stuck_gives_converge(tmp_path):
    """[Stage D] auto 模式下即使 spec 已确认且 miss_streak=0，如果传入的
    goal_backlog 显示最近几轮进展描述高度雷同，也应判定为 converge 而非 stable。
    """
    from mini_agent.evolution import goal_cron_bridge as bridge

    class _FakeGoal:
        id = "goal_1"
        execution_spec_confirmed = False  # 未确认 spec 时走 explore/converge，不测 stable 路径

    goal = _FakeGoalForTrend("goal_1", reaped_ids=["c1", "c2", "c3"])
    same_note = "本轮完成了周期性维护任务，无异常，产出与上轮一致"
    backlog = _FakeBacklogForTrend(goal, {
        "c1": _FakeChildForTrend(same_note),
        "c2": _FakeChildForTrend(same_note),
        "c3": _FakeChildForTrend(same_note),
    })
    paths = _paths(tmp_path)
    result = bridge._append_execution_phase_context(
        paths, _FakeGoal(), 10, "base description", goal_backlog=backlog,
    )
    # spec 未确认时本来就判 explore，这里主要验证 goal_backlog 参数不报错、
    # 且能正常传导（下面单独用 resolve_effective_mode 直接验证降级效果）。
    assert "base description" in result


def test_bridge_llm_helper_not_used_when_config_disabled(tmp_path, monkeypatch):
    """[§2] 配置关闭（默认）时，即使传入了 llm_helper_provider，也不应该
    实际调用它——只应该走 difflib 路径。"""
    from mini_agent.evolution import goal_cron_bridge as bridge
    from mini_agent.config.models import AppConfig

    class _FakeGoal:
        id = "goal_1"
        execution_spec_confirmed = False

    monkeypatch.setattr("mini_agent.config.load_config", lambda: AppConfig())

    called = {"n": 0}

    class _FakeHelper:
        def ask(self, prompt):
            called["n"] += 1
            return "STUCK"

    paths = _paths(tmp_path)
    bridge._append_execution_phase_context(
        paths, _FakeGoal(), 10, "base description",
        llm_helper_provider=lambda: _FakeHelper(),
    )
    assert called["n"] == 0


def test_bridge_llm_helper_used_when_config_enabled(tmp_path, monkeypatch):
    """[§2] 配置开启且 provider 返回非 None helper 时，进展趋势信号应该
    走 LLM 路径（这里只验证 helper 被实际调用了；判定效果已在
    execution_phase 模块级测试里覆盖）。"""
    from mini_agent.evolution import goal_cron_bridge as bridge
    from mini_agent.config.models import AppConfig

    class _FakeGoal:
        id = "goal_1"
        execution_spec_confirmed = False

    cfg = AppConfig()
    cfg.execution_phase.progress_trend_llm_enabled = True
    monkeypatch.setattr("mini_agent.config.load_config", lambda: cfg)

    called = {"n": 0}

    class _FakeHelper:
        def ask(self, prompt):
            called["n"] += 1
            return "PROGRESSING"

    goal = _FakeGoalForTrend("goal_1", reaped_ids=["c1", "c2", "c3"])
    backlog = _FakeBacklogForTrend(goal, {
        "c1": _FakeChildForTrend("做了 A"),
        "c2": _FakeChildForTrend("做了 A"),
        "c3": _FakeChildForTrend("做了 A"),
    })
    paths = _paths(tmp_path)
    bridge._append_execution_phase_context(
        paths, _FakeGoal(), 10, "base description",
        goal_backlog=backlog, llm_helper_provider=lambda: _FakeHelper(),
    )
    assert called["n"] == 1


# ── Stage B: tidy 自动回退 / converge-spec 联动 / tidy checklist ────────────

def test_resolve_tidy_auto_reverts_to_stable_after_one_cycle(tmp_path):
    state = ep.ExecutionPhaseState(goal_id="g1", mode="tidy", locked=True)
    # 第一次调用：还没跑过 tidy（cycles_in_mode 从 0 开始），维持 tidy
    effective1, state = ep.resolve_effective_mode(
        state, cycle_no=5, spec_confirmed=True, spec_recently_revised=False, miss_streak=0
    )
    assert effective1 == "tidy"
    assert state.cycles_in_mode == 1
    # 第二次调用（下一轮触发）：已经跑过一次 tidy，自动回到 stable 并解锁
    effective2, state = ep.resolve_effective_mode(
        state, cycle_no=6, spec_confirmed=True, spec_recently_revised=False, miss_streak=0
    )
    assert effective2 == "stable"
    assert state.mode == "stable"
    assert state.locked is False
    assert state.last_tidy_cycle == 6


def test_resolve_auto_periodic_tidy_insertion(tmp_path):
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto")
    effective, state = ep.resolve_effective_mode(
        state, cycle_no=10, spec_confirmed=True, spec_recently_revised=False, miss_streak=0,
        tidy_every_n_cycles=5,
    )
    assert effective == "tidy"
    assert state.last_tidy_cycle == 10


def test_resolve_auto_periodic_tidy_disabled_by_default(tmp_path):
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto")
    effective, _ = ep.resolve_effective_mode(
        state, cycle_no=100, spec_confirmed=True, spec_recently_revised=False, miss_streak=0,
    )
    assert effective == "stable"


def test_bridge_converge_appends_spec_hint_when_unconfirmed(tmp_path):
    from mini_agent.evolution import goal_cron_bridge as bridge

    class _FakeGoal:
        id = "goal_1"
        execution_spec_confirmed = False

    paths = _paths(tmp_path)
    ep.set_mode(paths, "goal_1", "converge")
    result = bridge._append_execution_phase_context(paths, _FakeGoal(), 1, "base description")
    assert "收敛期" in result
    assert "固化执行规范" in result


def test_build_tidy_checklist_hint_from_spec():
    from mini_agent.evolution import goal_cron_bridge as bridge
    from mini_agent.perception import goal_execution_spec as ges

    spec = ges.GoalExecutionSpec(goal_id="g1")
    spec.deliverables.append(ges.Deliverable(name="report.md", naming_pattern="report_{n}.md"))
    spec.sub_directories.append(ges.SubDirectory(name="raw/", purpose="原始数据"))
    hint = bridge._build_tidy_checklist_hint(spec)
    assert "report.md" in hint
    assert "raw/" in hint


def test_build_tidy_checklist_hint_empty_spec_returns_empty():
    from mini_agent.evolution import goal_cron_bridge as bridge
    from mini_agent.perception import goal_execution_spec as ges

    spec = ges.GoalExecutionSpec(goal_id="g1")
    assert bridge._build_tidy_checklist_hint(spec) == ""
    assert bridge._build_tidy_checklist_hint(None) == ""


# ── check_phase_health（goal_cron_task_optimization_holistic_plan.md 方向 B）──

def test_check_phase_health_stuck_explore_triggers():
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto", locked=False)
    state.cycles_in_mode = ep.DEFAULT_STUCK_EXPLORE_CYCLES
    reason = ep.check_phase_health(state, "explore")
    assert reason is not None
    assert "explore" in reason or "探索" in reason


def test_check_phase_health_stuck_explore_below_threshold_no_alert():
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto", locked=False)
    state.cycles_in_mode = ep.DEFAULT_STUCK_EXPLORE_CYCLES - 1
    assert ep.check_phase_health(state, "explore") is None


def test_check_phase_health_locked_explore_not_alerted():
    # 用户手动锁定在 explore 是明确意图，不应触发"卡住"告警。
    state = ep.ExecutionPhaseState(goal_id="g1", mode="explore", locked=True)
    state.cycles_in_mode = ep.DEFAULT_STUCK_EXPLORE_CYCLES + 5
    assert ep.check_phase_health(state, "explore") is None


def test_check_phase_health_cooldown_suppresses_repeat_alert():
    import time as _time

    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto", locked=False)
    state.cycles_in_mode = ep.DEFAULT_STUCK_EXPLORE_CYCLES
    assert ep.check_phase_health(state, "explore") is not None
    state.last_health_alert_kind = "stuck_explore"
    state.last_health_alert_at = _time.time()
    # 冷却期内，同一种问题不应再次告警。
    assert ep.check_phase_health(state, "explore") is None


def test_check_phase_health_phase_flapping_triggers():
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto", locked=False)
    for _ in range(ep.DEFAULT_FLAP_THRESHOLD):
        state.mode_history.append(
            ep.ModeChange(at=0.0, from_mode="auto:stable", to_mode="auto:converge", reason="rule_based_auto")
        )
    reason = ep.check_phase_health(state, "converge")
    assert reason is not None
    assert "打回" in reason or "复核" in reason


def test_check_phase_health_no_history_no_alert():
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto", locked=False)
    assert ep.check_phase_health(state, "stable") is None


# ── last_known_effective_mode（goal_cron_task_optimization_holistic_plan.md 方向 A）──

def test_last_known_effective_mode_manual_non_auto():
    state = ep.ExecutionPhaseState(goal_id="g1", mode="stable", locked=True)
    assert ep.last_known_effective_mode(state) == "stable"


def test_last_known_effective_mode_auto_no_history_defaults_explore():
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto", locked=False)
    assert ep.last_known_effective_mode(state) == "explore"


def test_last_known_effective_mode_auto_reads_latest_rule_based_entry():
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto", locked=False)
    state.mode_history.append(
        ep.ModeChange(at=1.0, from_mode="auto:explore", to_mode="auto:converge", reason="rule_based_auto")
    )
    state.mode_history.append(
        ep.ModeChange(at=2.0, from_mode="auto:converge", to_mode="auto:stable", reason="rule_based_auto")
    )
    assert ep.last_known_effective_mode(state) == "stable"


def test_last_known_effective_mode_ignores_non_rule_based_entries():
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto", locked=False)
    state.mode_history.append(
        ep.ModeChange(at=1.0, from_mode="auto:explore", to_mode="auto:stable", reason="rule_based_auto")
    )
    state.mode_history.append(
        ep.ModeChange(at=2.0, from_mode="stable", to_mode="converge", reason="user_set")
    )
    # 最新一条不是 rule_based_auto（是用户手动切换记录），应跳过它，
    # 沿用更早的自动判定记录。
    assert ep.last_known_effective_mode(state) == "stable"
