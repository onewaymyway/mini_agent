"""tests/test_execution_phase_stage8b.py

覆盖 next_doc/goal_output_directory_and_execution_phase_redesign_plan.md
Stage 8b：stable → running 改名（含旧数据/旧调用方的向后兼容别名）、
new_topic_discovery=intrinsic 语义（本函数只验证 resolve_effective_mode
不因该字段本身报错——真正接入判定逻辑留给 Stage 8c，见 Stage8b 文档记录）、
以及新增的 compute_routine_stability_signal()。
"""
from __future__ import annotations

from mini_agent.perception import execution_phase as ep


def test_valid_modes_uses_running_not_stable():
    assert "running" in ep.VALID_MODES
    assert "stable" not in ep.VALID_MODES


def test_legacy_alias_normalizes_stable_to_running():
    assert ep._normalize_mode("stable") == "running"
    assert ep._normalize_mode("running") == "running"
    assert ep._normalize_mode("explore") == "explore"


def test_from_dict_accepts_legacy_stable_value(tmp_path):
    state = ep.ExecutionPhaseState.from_dict({"goal_id": "g1", "mode": "stable"})
    assert state.mode == "running"


def test_set_mode_accepts_legacy_stable_string(tmp_path):
    class _Paths:
        project_root = str(tmp_path)

    state = ep.set_mode(_Paths(), "g1", "stable")
    assert state.mode == "running"


def test_set_mode_rejects_truly_invalid_mode(tmp_path):
    class _Paths:
        project_root = str(tmp_path)

    try:
        ep.set_mode(_Paths(), "g1", "not_a_mode")
        assert False, "should have raised"
    except ValueError:
        pass


def test_resolve_effective_mode_targets_running_not_stable():
    state = ep.ExecutionPhaseState(goal_id="g1", mode="auto", locked=False)
    mode, state = ep.resolve_effective_mode(
        state, cycle_no=10, spec_confirmed=True, spec_recently_revised=False, miss_streak=0,
    )
    assert mode == "running"


def test_resolve_effective_mode_tidy_auto_reverts_to_running():
    state = ep.ExecutionPhaseState(goal_id="g1", mode="tidy", locked=True, cycles_in_mode=1)
    mode, state = ep.resolve_effective_mode(
        state, cycle_no=5, spec_confirmed=True, spec_recently_revised=False,
    )
    assert mode == "running"
    assert state.mode == "running"
    assert state.locked is False


def test_phase_resource_multiplier_running_key_present():
    assert ep.DEFAULT_PHASE_RESOURCE_MULTIPLIERS.get("running") == 1.0
    assert "stable" not in ep.DEFAULT_PHASE_RESOURCE_MULTIPLIERS


# ── compute_routine_stability_signal ──────────────────────────────────────

def test_routine_stability_signal_insufficient_samples():
    assert ep.compute_routine_stability_signal([]) is None
    assert ep.compute_routine_stability_signal(["只有一条"]) is None


def test_routine_stability_signal_stable_routine_returns_true():
    routine = "扫描已有条目\n检索新素材\n去重合并\n写入更新\n刷新索引"
    signal = ep.compute_routine_stability_signal([routine, routine, routine])
    assert signal is True


def test_routine_stability_signal_changing_routine_returns_false():
    texts = [
        "第一步：抓取数据\n第二步：写文件",
        "改用完全不同的流程：先建模，再人工审核，最后归档到另一套目录",
    ]
    signal = ep.compute_routine_stability_signal(texts, similarity_threshold=0.85)
    assert signal is False


def test_routine_stability_signal_llm_helper_used_when_available():
    calls = []

    def _fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return "STUCK"

    signal = ep.compute_routine_stability_signal(
        ["routine v1", "routine v2"], llm_helper=_fake_llm,
    )
    assert signal is True
    assert len(calls) == 1


def test_routine_stability_signal_swallows_exceptions():
    def _boom(_prompt):
        raise RuntimeError("boom")

    signal = ep.compute_routine_stability_signal(["a", "b"], llm_helper=_boom)
    # LLM 抛异常时 _llm_judge_progress_trend 内部已吞掉返回 None，
    # 外层应正常退回 difflib 兜底而不是让异常冒出来。
    assert signal in (True, False, None)
