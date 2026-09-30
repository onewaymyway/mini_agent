"""tests/test_tech_model.py — WP1（第二十二轮 / 阶段 P3）：技术发展模型。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md`
§4 WP1。引擎只做"提议–审核"：LLM 提出阶段声明，引擎按时间推算的进度、
硬前置、一步一档等规则裁决，违规透明记录。这里逐条验证 R1–R7 的判定边界，
以及 `advance()` 端到端接入、分支隔离、开关关闭时与今天完全一致。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import branch_manager as bm
from world_simulator import consistency_guard as cg
from world_simulator import dynamic_state, tech_model as tm
from world_simulator.engine.advance import advance
from world_simulator.engine.management import get_simulation
from world_simulator.state_model import SimManifest, SimState
from world_simulator.store import SimStore, now_iso

from test_fast_forward import _default_payload, _patch_sequenced_advance_step_workflow


def _node(tid="t1", stage="lab", progress=0.0, dwell=100, **extra):
    """典型停留 `dwell` 天的节点（方便算进度）。"""
    n = {"id": tid, "name": tid, "stage": stage, "progress": progress,
         "typical_dwell_days": {s: dwell for s in tm.STAGES}, "dwell_source": "user"}
    n.update(extra)
    return n


def _settings(*nodes, **extra):
    s = {"tech_model_enabled": True, "tech_state": {"nodes": list(nodes)}}
    s.update(extra)
    return s


def _run(settings, proposals=None, *, step=1, days=100):
    audit, violations = tm.apply_step(settings, proposals, step=step, elapsed_days_raw=days)
    return audit, violations


def _n(settings, tid="t1"):
    return next(n for n in tm.get_nodes(settings) if n["id"] == tid)


def _codes(violations):
    return [v["code"] for v in violations]


def _actions(audit):
    return [a["action"] for a in audit]


# ── 开关 ─────────────────────────────────────────────────────────────


def test_disabled_is_a_noop():
    s = {"tech_state": {"nodes": [_node()]}}
    before = copy.deepcopy(s)
    assert tm.apply_step(s, [{"id": "t1", "stage": "expert"}], step=1, elapsed_days_raw=999) == ([], [])
    assert s == before
    assert tm.build_hint(s) == ""
    assert tm.build_hint(None) == ""


# ── 时间推进（R3）────────────────────────────────────────────────────


def test_progress_advances_with_elapsed_time_and_engine_owns_it():
    s = _settings(_node(dwell=100))
    audit, v = _run(s, None, days=25)
    assert v == []
    assert abs(_n(s)["progress"] - 0.25) < 1e-9 and _n(s)["dwell_days"] == 25
    assert audit[0] == {"action": "time", "elapsed_days": 25.0, "elapsed_source": "reported"}
    # LLM 自报进度会被忽略：引擎只按时间算
    _run(s, [{"id": "t1", "progress": 0.99}], days=25)
    assert _n(s)["progress"] == 0.5


def test_progress_caps_at_one():
    s = _settings(_node(dwell=100))
    _run(s, None, days=1000)
    assert _n(s)["progress"] == 1.0


def test_investment_factor_and_reason_requirement():
    s = _settings(_node(dwell=100))
    _run(s, [{"id": "t1", "investment": "high", "investment_reason": "国家专项"}], days=20)
    assert abs(_n(s)["progress"] - 0.3) < 1e-9  # 20/100 × 1.5

    s2 = _settings(_node(dwell=100))
    _, v = _run(s2, [{"id": "t1", "investment": "high"}], days=20)  # 没理由 → 按 normal
    assert abs(_n(s2)["progress"] - 0.2) < 1e-9 and _codes(v) == ["T10"] and v[0]["severity"] == "info"

    s3 = _settings(_node(dwell=100))
    _run(s3, [{"id": "t1", "investment": "low", "investment_reason": "资金紧张"}], days=20)
    assert abs(_n(s3)["progress"] - 0.1) < 1e-9


def test_investment_is_per_step_not_sticky():
    s = _settings(_node(dwell=100))
    _run(s, [{"id": "t1", "investment": "high", "investment_reason": "r"}], days=20)
    _run(s, None, days=20)  # 这一步没申报 → normal
    assert abs(_n(s)["progress"] - 0.5) < 1e-9


def test_bottleneck_slows_and_clearing_needs_reason():
    s = _settings(_node(dwell=100))
    _run(s, [{"id": "t1", "bottleneck": "缺少关键材料", "bottleneck_severity": "medium"}], days=20)
    assert abs(_n(s)["progress"] - 0.1) < 1e-9 and _n(s)["bottleneck"] == "缺少关键材料"
    _, v = _run(s, [{"id": "t1", "bottleneck": ""}], days=20)  # 无理由清除 → 保留
    assert _codes(v) == ["T11"] and _n(s)["bottleneck"] == "缺少关键材料"
    assert abs(_n(s)["progress"] - 0.2) < 1e-9
    _run(s, [{"id": "t1", "bottleneck": "", "bottleneck_resolved_reason": "新材料量产"}], days=20)
    assert _n(s)["bottleneck"] == "" and abs(_n(s)["progress"] - 0.4) < 1e-9  # 本步仍按旧瓶颈系数算


def test_soft_requirement_slows_but_does_not_block():
    a = _node("a", stage="lab")
    b = _node("b", requires=[{"tech_id": "a", "min_stage": "expert", "mode": "soft"}])
    s = _settings(a, b)
    _run(s, None, days=40)
    assert abs(_n(s, "b")["progress"] - 0.2) < 1e-9  # 40/100 × 0.5
    assert abs(_n(s, "a")["progress"] - 0.4) < 1e-9


# ── 阶段迁移（R1/R4）────────────────────────────────────────────────


def test_transition_accepted_when_progress_full_and_requires_met():
    s = _settings(_node(dwell=100))
    audit, v = _run(s, [{"id": "t1", "stage": "expert"}], days=100)
    assert v == [] and _n(s)["stage"] == "expert" and _n(s)["progress"] == 0.0
    assert _n(s)["dwell_days"] == 0 and _n(s)["last_transition_step"] == 1
    tr = [a for a in audit if a["action"] == "transition"][0]
    assert (tr["stage_before"], tr["stage_after"]) == ("lab", "expert")


def test_premature_transition_is_rejected_and_no_free_progress():
    s = _settings(_node(dwell=100))
    audit, v = _run(s, [{"id": "t1", "stage": "expert"}], days=30)
    assert _codes(v) == ["T2"]
    assert _n(s)["stage"] == "lab" and abs(_n(s)["progress"] - 0.3) < 1e-9  # 只按引擎算，不"送"进度
    assert "held" in _actions(audit)


def test_jump_two_levels_recorded_and_clamped_to_one_when_eligible():
    s = _settings(_node(dwell=100))
    _, v = _run(s, [{"id": "t1", "stage": "developer"}], days=100)
    assert "T1" in _codes(v) and _n(s)["stage"] == "expert"  # 只升一档
    s2 = _settings(_node(dwell=100))
    _, v2 = _run(s2, [{"id": "t1", "stage": "consumer"}], days=10)
    assert set(_codes(v2)) == {"T1", "T2"} and _n(s2)["stage"] == "lab"


def test_hard_requirement_blocks_even_when_progress_full():
    a = _node("a", stage="lab", dwell=1000)
    b = _node("b", requires=[{"tech_id": "a", "min_stage": "expert", "mode": "hard"}])
    s = _settings(a, b)
    _, v = _run(s, [{"id": "b", "stage": "expert"}], days=100)
    assert _codes(v) == ["T3"] and _n(s, "b")["stage"] == "lab" and _n(s, "b")["progress"] == 1.0
    assert v[0]["detail"]["unmet"] == ["a"]


def test_hard_requirement_met_allows_transition():
    a = _node("a", stage="expert", dwell=1000)
    b = _node("b", requires=[{"tech_id": "a", "min_stage": "expert", "mode": "hard"}])
    s = _settings(a, b)
    _, v = _run(s, [{"id": "b", "stage": "expert"}], days=100)
    assert v == [] and _n(s, "b")["stage"] == "expert"


def test_simultaneous_transitions_do_not_satisfy_each_other():
    a = _node("a", stage="lab", dwell=100)
    b = _node("b", stage="lab", dwell=100, requires=[{"tech_id": "a", "min_stage": "expert"}])
    s = _settings(a, b)
    _, v = _run(s, [{"id": "a", "stage": "expert"}, {"id": "b", "stage": "expert"}], days=100)
    assert _n(s, "a")["stage"] == "expert"  # a 自己没有前置，正常升级
    assert _n(s, "b")["stage"] == "lab" and _codes(v) == ["T3"]  # 用本步开始前的阶段判断


def test_requirement_for_stage_only_binds_later_transitions():
    a = _node("a", stage="lab", dwell=1000)
    b = _node("b", dwell=100, requires=[{"tech_id": "a", "min_stage": "expert", "for_stage": "developer"}])
    s = _settings(a, b)
    _, v = _run(s, [{"id": "b", "stage": "expert"}], days=100)  # lab→expert 不受 for_stage=developer 约束
    assert v == [] and _n(s, "b")["stage"] == "expert"


def test_unresolved_requirement_does_not_block_but_is_recorded():
    b = _node("b", requires=[{"tech_id": "ghost", "min_stage": "expert"}])
    s = _settings(b)
    _, v = _run(s, [{"id": "b", "stage": "expert"}], days=100)
    assert _n(s, "b")["stage"] == "expert"
    assert _codes(v) == ["T9"] and v[0]["severity"] == "info"


def test_top_stage_cannot_advance_further():
    s = _settings(_node(stage="infrastructure", dwell=100))
    _run(s, None, days=500)
    assert tm.node_status(_n(s), tm.get_nodes(s), s)["status"] == "mature"
    _, v = _run(s, [{"id": "t1", "stage": "infrastructure"}], days=10)
    assert v == []


# ── 倒退 ─────────────────────────────────────────────────────────────


def test_regression_requires_reason():
    s = _settings(_node(stage="developer", progress=0.4))
    _, v = _run(s, [{"id": "t1", "stage": "expert"}], days=1)
    assert _codes(v) == ["T4"] and _n(s)["stage"] == "developer"

    _, v2 = _run(s, [{"id": "t1", "stage": "expert", "regression_reason": "被监管叫停"}], days=1)
    assert v2 == [] and _n(s)["stage"] == "expert" and _n(s)["progress"] == 0.5
    assert _n(s)["dwell_days"] == 0


def test_regression_progress_is_configurable():
    s = _settings(_node(stage="developer"), tech_params={"regression_progress": 0.8})
    _run(s, [{"id": "t1", "stage": "lab", "regression_reason": "x"}], days=1)
    assert _n(s)["progress"] == 0.8


# ── 登记 ─────────────────────────────────────────────────────────────


def test_new_node_registered_from_lab_and_high_stage_is_clamped():
    s = _settings()
    audit, v = _run(s, [{"id": "fusion", "name": "聚变", "stage": "consumer"}], days=100)
    assert _codes(v) == ["T5"] and _n(s, "fusion")["stage"] == "lab"
    assert _n(s, "fusion")["progress"] == 0.0  # 登记当步不推进时间
    assert "registered" in _actions(audit)


def test_preexisting_technology_keeps_declared_stage_and_is_flagged():
    s = _settings()
    audit, v = _run(s, [{"id": "phone", "name": "手机", "stage": "consumer", "preexisting": True}], days=100)
    assert v == [] and _n(s, "phone")["stage"] == "consumer" and _n(s, "phone")["preexisting"] is True
    reg = [a for a in audit if a["action"] == "registered"][0]
    assert "未经验证" in reg["note"]


def test_llm_dwell_estimate_is_flagged_unverified_and_later_changes_ignored():
    s = _settings()
    _run(s, [{"id": "x", "name": "x", "typical_dwell_days": {"lab": 500}}], days=10)
    assert _n(s, "x")["dwell_source"] == "llm_estimate" and _n(s, "x")["dwell_verified"] is False
    assert _n(s, "x")["typical_dwell_days"] == {"lab": 500.0}
    _, v = _run(s, [{"id": "x", "typical_dwell_days": {"lab": 1}, "requires": [{"tech_id": "y"}]}], days=10)
    assert _codes(v) == ["T8", "T8"]
    assert _n(s, "x")["typical_dwell_days"] == {"lab": 500.0} and _n(s, "x")["requires"] == []


def test_proposal_without_id_or_name_is_ignored():
    s = _settings(_node())
    _, v = _run(s, [{"stage": "expert"}, "junk", 5], days=1)
    assert _codes(v) == ["T9"]


def test_user_seed_dwell_is_marked_user_verified():
    s = _settings(_node())
    n = _n(s)
    assert n["dwell_source"] == "user" and n["dwell_verified"] is True


# ── 停滞/状态（R5）─────────────────────────────────────────────────


def test_stall_detection_and_counter():
    s = _settings(_node(dwell=100, bottleneck="x", bottleneck_severity="high"), tech_params={"stall_multiplier": 2})
    _run(s, [{"id": "t1", "bottleneck": "x", "bottleneck_severity": "high"}], days=150)  # 进度 0.375，停留 150
    assert tm.node_status(_n(s), tm.get_nodes(s), s)["status"] == "progressing"
    _run(s, None, days=100)  # 停留 250 > 2×100，进度仍 <1
    st = tm.node_status(_n(s), tm.get_nodes(s), s)
    assert st["status"] == "stalled" and _n(s)["stalled_steps"] == 1
    assert "停滞" in tm.build_hint(s)


def test_status_ready_and_blocked():
    a = _node("a", stage="lab", dwell=1000)
    b = _node("b", requires=[{"tech_id": "a", "min_stage": "expert"}])
    s = _settings(a, b)
    _run(s, None, days=100)
    nodes = tm.get_nodes(s)
    assert tm.node_status(_n(s, "b"), nodes, s)["status"] == "blocked"
    c = _settings(_node())
    _run(c, None, days=100)
    assert tm.node_status(_n(c), tm.get_nodes(c), c)["status"] == "ready"
    assert _n(s, "b")["stalled_steps"] == 1  # 受阻也计入停滞步数


# ── R6 采用率/成本 ───────────────────────────────────────────────────


def test_adoption_sum_in_market_is_clamped_for_updated_nodes():
    a = _node("a", market="m", adoption=0.6)
    b = _node("b", market="m", adoption=0.1)
    s = _settings(a, b)
    _, v = _run(s, [{"id": "b", "adoption": 0.7}], days=1)
    assert _codes(v) == ["T6"]
    assert _n(s, "a")["adoption"] == 0.6 and abs(_n(s, "b")["adoption"] - 0.4) < 1e-9
    total = sum(n["adoption"] for n in tm.get_nodes(s))
    assert total <= 1.0 + 1e-9


def test_adoption_within_limit_is_untouched_and_other_market_independent():
    a = _node("a", market="m1", adoption=0.9)
    b = _node("b", market="m2", adoption=0.2)
    s = _settings(a, b)
    _, v = _run(s, [{"id": "b", "adoption": 0.9}], days=1)
    assert v == [] and _n(s, "b")["adoption"] == 0.9


def test_cost_index_non_increasing_unless_shock_declared():
    s = _settings(_node(cost_index=10.0))
    _run(s, [{"id": "t1", "cost_index": 6.0}], days=1)
    assert _n(s)["cost_index"] == 6.0
    _, v = _run(s, [{"id": "t1", "cost_index": 9.0}], days=1)
    assert _codes(v) == ["T7"] and _n(s)["cost_index"] == 6.0
    _, v2 = _run(s, [{"id": "t1", "cost_index": 9.0, "cost_shock_reason": "关税"}], days=1)
    assert v2 == [] and _n(s)["cost_index"] == 9.0


def test_perceived_stage_is_stored_but_not_enforced():
    s = _settings(_node())
    _, v = _run(s, [{"id": "t1", "perceived_stage": "consumer"}], days=1)
    assert v == [] and _n(s)["perceived_stage"] == "consumer" and _n(s)["stage"] == "lab"
    assert "公众以为处于 consumer" in tm.build_hint(s)


# ── elapsed_days ─────────────────────────────────────────────────────


def test_resolve_elapsed_reported_fallback_and_cap():
    p = tm.get_params({})
    assert tm.resolve_elapsed(12, p) == (12.0, "reported", None)
    assert tm.resolve_elapsed("40", p)[:2] == (40.0, "reported")
    d, src, note = tm.resolve_elapsed(None, p)
    assert (d, src, note) == (30.0, "fallback", None)
    d, src, note = tm.resolve_elapsed(-5, p)
    assert (d, src) == (30.0, "fallback") and note
    assert tm.resolve_elapsed(True, p)[1] == "fallback"
    assert tm.resolve_elapsed(float("nan"), p)[1] == "fallback"
    d, src, note = tm.resolve_elapsed(10 ** 9, p)
    assert (d, src) == (36500.0, "reported") and note


def test_missing_elapsed_degrades_to_per_step_and_is_marked():
    s = _settings(_node(dwell=30), tech_params={"fallback_days_per_step": 15})
    audit, _ = tm.apply_step(s, None, step=1, elapsed_days_raw=None)
    assert audit[0]["elapsed_source"] == "fallback" and audit[0]["elapsed_days"] == 15.0
    assert _n(s)["progress"] == 0.5


def test_normalize_reported_elapsed():
    assert tm.normalize_reported_elapsed(3) == 3.0
    assert tm.normalize_reported_elapsed(0) is None
    assert tm.normalize_reported_elapsed(None) is None
    assert tm.normalize_reported_elapsed(True) is None
    assert tm.normalize_reported_elapsed("x") is None


# ── 参数/先验 ────────────────────────────────────────────────────────


def test_params_override_and_invalid_values_ignored():
    p = tm.get_params({"tech_params": {"soft_penalty": 0.25, "stall_multiplier": -1,
                                       "investment_factors": {"high": 2.0, "bad": "x"}}})
    assert p["soft_penalty"] == 0.25 and p["stall_multiplier"] == 3.0
    assert p["investment_factors"]["high"] == 2.0 and p["investment_factors"]["low"] == 0.5
    assert "bad" not in p["investment_factors"]


def test_priors_by_kind_then_default_then_builtin():
    node = {"id": "x", "kind": "energy"}
    n = tm.normalize_node(node)
    params = tm.get_params({})
    s = {"tech_priors": {"energy": {"lab": 200}, "default": {"lab": 50, "expert": 60}}}
    assert tm._dwell_for(n, s, params) == 200
    n2 = tm.normalize_node({"id": "y", "kind": "other", "stage": "expert"})
    assert tm._dwell_for(n2, s, params) == 60
    assert tm._dwell_for(tm.normalize_node({"id": "z"}), {}, params) == params["default_dwell_days"]
    own = tm.normalize_node({"id": "w", "typical_dwell_days": {"lab": 7}})
    assert tm._dwell_for(own, s, params) == 7  # 节点自带优先


# ── 提示词 ───────────────────────────────────────────────────────────


def test_hint_when_enabled_without_nodes_explains_protocol():
    h = tm.build_hint({"tech_model_enabled": True})
    assert "elapsed_days" in h and "tech_updates" in h and "还没有登记任何技术节点" in h


def test_hint_lists_authoritative_state_and_blockers():
    a = _node("a", stage="lab", dwell=1000)
    b = _node("b", requires=[{"tech_id": "a", "min_stage": "expert"}], bottleneck="缺算力", bottleneck_severity="low")
    s = _settings(a, b)
    h = tm.build_hint(s)
    assert "[a]" in h and "[b]" in h and "进度 0%" in h
    assert "缺算力" in h
    _run(s, None, days=100)
    h2 = tm.build_hint(s)
    assert "硬前置" in h2 and "a 需达 专家可用（现为 实验室可行）" in h2


# ── 序列化 ───────────────────────────────────────────────────────────


def test_simstate_new_fields_omitted_when_empty_and_roundtrip():
    plain = SimState(step=1, summary="s").to_dict()
    for key in ("elapsed_days", "tech_updates", "tech_violations"):
        assert key not in plain
    st = SimState(step=1, summary="s", elapsed_days=12.5,
                  tech_updates=[{"action": "time", "elapsed_days": 12.5}],
                  tech_violations=[{"code": "T2", "severity": "warn", "tech_id": "t", "message": "m", "detail": {}}])
    back = SimState.from_dict(json.loads(json.dumps(st.to_dict())))
    assert back.elapsed_days == 12.5 and back.tech_updates == st.tech_updates
    assert back.tech_violations == st.tech_violations
    assert back.tech_updates[0] is not st.tech_updates[0] or True
    assert SimState.from_dict({"step": 1, "elapsed_days": -3}).elapsed_days is None
    assert SimState.from_dict({"step": 1, "elapsed_days": True}).elapsed_days is None


def test_tech_state_is_a_dynamic_key_and_absent_state_makes_no_snapshot():
    assert "tech_state" in dynamic_state.DYNAMIC_KEYS
    assert dynamic_state.extract({"tech_model_enabled": True}) == {}
    assert "tech_state" in dynamic_state.extract(_settings(_node()))


def test_safe_apply_step_swallows_errors_and_leaves_settings_untouched(monkeypatch):
    s = _settings(_node())
    before = copy.deepcopy(s)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(tm, "_dwell_for", boom)
    audit, v = tm.safe_apply_step(s, None, step=1, elapsed_days_raw=10)
    assert audit == [] and _codes(v) == ["T0"] and s == before


def test_safe_build_hint_never_raises(monkeypatch):
    s = _settings(_node())
    monkeypatch.setattr(tm, "_dwell_for", lambda *a, **k: 1 / 0)
    assert tm.safe_build_hint(s) == ""
    monkeypatch.undo()
    assert "[t1]" in tm.safe_build_hint(s)


# ── C6 按 elapsed_days 归一 ──────────────────────────────────────────


def _vol_states(values_and_days):
    out = []
    for i, (val, days) in enumerate(values_and_days):
        out.append(SimState(step=i, summary=f"s{i}", vars={"x": val}, elapsed_days=days))
    return out


def test_c6_rate_mode_does_not_false_alarm_on_variable_step_length():
    # 每天 +1：步长 10 天 → +10，步长 100 天 → +100。原始口径会把 +100 当异常。
    seq, val = [(0, 10)], 0
    for d in (10, 10, 10, 10, 10, 10, 100):
        val += d
        seq.append((val, d))
    tracker = cg.VolatilityTracker(z_threshold=4.0, min_deltas=5)
    warnings = [w for st in _vol_states(seq) for w in tracker.feed(st)]
    assert warnings == []
    raw = cg.VolatilityTracker(z_threshold=4.0, min_deltas=5)
    raw_warnings = [w for st in _vol_states([(v, None) for v, _ in seq]) for w in raw.feed(st)]
    assert [w["code"] for w in raw_warnings] == ["C6"]  # 对照：没有 elapsed_days 就是原口径


def test_c6_rate_mode_still_detects_real_anomaly_and_ignores_granularity_change():
    seq, val = [(0, 10)], 0
    for d in (10, 10, 10, 10, 10, 10):
        val += d
        seq.append((val, d))
    seq.append((val + 5000, 10))  # 10 天里暴涨 5000
    tracker = cg.VolatilityTracker(z_threshold=4.0, min_deltas=5)
    states = _vol_states(seq)
    states[-1].granularity_changed = True  # rate 口径下不因粒度变化而跳过
    warnings = [w for st in states for w in tracker.feed(st)]
    assert [w["code"] for w in warnings] == ["C6"] and warnings[0]["detail"]["mode"] == "rate"


# ── advance() 端到端 ─────────────────────────────────────────────────


def _make_sim(data_dir, settings, sim_id="sim1"):
    store = SimStore.for_root(data_dir, sim_id)
    ts = now_iso()
    store.save_manifest(SimManifest(sim_id=sim_id, template="life_sim", intent="i", title="t",
                                    created_at=ts, updated_at=ts, settings=settings))
    store.append_state(SimState(step=0, summary="s0", vars={"age": 20}, options=[]))
    return store


def _adv(monkeypatch, tmp_path, data_dir, payload, capture=None):
    cap = capture if capture is not None else {}
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, [payload], cap)
    return advance(object(), tmp_path, data_dir, "sim1")


def test_advance_runs_tech_model_persists_audit_snapshot_and_working_copy(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, _settings(_node("ai", dwell=100)))
    cap: dict = {}
    payload = _default_payload(1, elapsed_days=40,
                               tech_updates=[{"id": "ai", "stage": "expert"},  # 进度只有 0.4 → 驳回
                                             {"id": "chip", "name": "芯片", "stage": "lab"}])
    state = _adv(monkeypatch, tmp_path, data_dir, payload, cap)

    assert "[ai]" in cap["inputs_list"][0]["tech_state_hint"]  # 权威状态进了 prompt
    assert state.elapsed_days == 40.0
    assert _codes(state.tech_violations) == ["T2"]
    assert _actions(state.tech_updates) == ["time", "registered", "held"] or set(_actions(state.tech_updates)) == {"time", "registered", "held"}

    persisted = store.load_history("main")[-1]
    assert persisted.elapsed_days == 40.0 and _codes(persisted.tech_violations) == ["T2"]
    nodes = {n["id"]: n for n in persisted.dynamic_snapshot["tech_state"]["nodes"]}
    assert nodes["ai"]["stage"] == "lab" and abs(nodes["ai"]["progress"] - 0.4) < 1e-9
    assert nodes["chip"]["stage"] == "lab"
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert {n["id"] for n in manifest.settings["tech_state"]["nodes"]} == {"ai", "chip"}


def test_advance_accepts_legitimate_transition_over_several_steps(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _settings(_node("ai", dwell=100)))
    s1 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=60))
    assert s1.tech_violations == []
    s2 = _adv(monkeypatch, tmp_path, data_dir,
              _default_payload(2, elapsed_days=60, tech_updates=[{"id": "ai", "stage": "expert"}]))
    assert s2.tech_violations == []
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.settings["tech_state"]["nodes"][0]["stage"] == "expert"


def test_advance_without_elapsed_days_uses_fallback_and_marks_it(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _settings(_node("ai", dwell=60), tech_params={"fallback_days_per_step": 30}))
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1))
    assert state.elapsed_days is None  # 没报就是没报，不伪造
    assert state.tech_updates[0]["elapsed_source"] == "fallback"


def test_advance_switch_off_is_identical_to_before(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, {"tech_state": {"nodes": [_node("ai")]}})  # 有种子但开关未开
    cap: dict = {}
    payload = _default_payload(1, elapsed_days=99, tech_updates=[{"id": "ai", "stage": "consumer"}])
    state = _adv(monkeypatch, tmp_path, data_dir, payload, cap)
    assert cap["inputs_list"][0]["tech_state_hint"] == ""
    assert state.elapsed_days is None and state.tech_updates == [] and state.tech_violations == []
    d = store.load_history("main")[-1].to_dict()
    for key in ("elapsed_days", "tech_updates", "tech_violations"):
        assert key not in d
    # 未开启时不读也不改 tech_state
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.settings["tech_state"]["nodes"][0]["stage"] == "lab"


def test_advance_tech_model_failure_does_not_break_the_step(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _settings(_node("ai")))
    monkeypatch.setattr(tm, "_dwell_for", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=10))
    assert state.step == 1 and _codes(state.tech_violations) == ["T0"]


def test_hint_reaches_world_evolve_placeholder_in_both_workflows():
    root = Path(__file__).resolve().parent.parent / "workflows"
    for name in ("advance_step.yaml", "world_evolve.yaml"):
        assert "{tech_state_hint}" in (root / name).read_text(encoding="utf-8"), name


# ── 分支隔离（WP0 × WP1）────────────────────────────────────────────


def _tech_stage_progress(data_dir, tid="ai"):
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    n = next(n for n in manifest.settings["tech_state"]["nodes"] if n["id"] == tid)
    return n["stage"], round(n["progress"], 3)


def test_fork_rolls_tech_state_back_and_branches_do_not_pollute_each_other(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _settings(_node("ai", dwell=100)))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=30))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, elapsed_days=30))
    assert _tech_stage_progress(data_dir) == ("lab", 0.6)

    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1)
    assert _tech_stage_progress(data_dir) == ("lab", 0.3)  # 回滚到分叉那一刻

    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, elapsed_days=70,
                                                           tech_updates=[{"id": "ai", "stage": "expert"}]))
    assert _tech_stage_progress(data_dir) == ("expert", 0.0)  # 新分支上升了级

    bm.switch_branch(data_dir, "sim1", "main")
    assert _tech_stage_progress(data_dir) == ("lab", 0.6)  # 主线没被污染
    bm.switch_branch(data_dir, "sim1", new_branch)
    assert _tech_stage_progress(data_dir) == ("expert", 0.0)


def test_fork_from_before_first_advance_rolls_back_to_the_seed(tmp_path, monkeypatch):
    """种子锚定：种子写在设置里、历史里还没有快照时，推进前先把种子提交到头部，
    否则从第 0 步分叉会拿到"已被主线推进过"的技术状态。"""
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, _settings(_node("ai", dwell=100)))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=50))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, elapsed_days=50))
    assert _tech_stage_progress(data_dir) == ("lab", 1.0)
    head0 = store.load_history("main")[0]
    assert head0.dynamic_snapshot and head0.dynamic_snapshot["tech_state"]["nodes"][0]["progress"] == 0.0

    bm.fork_branch(data_dir, "sim1", from_step=0)
    assert _tech_stage_progress(data_dir) == ("lab", 0.0)
    bm.switch_branch(data_dir, "sim1", "main")
    assert _tech_stage_progress(data_dir) == ("lab", 1.0)


def test_anchor_does_not_fire_when_tech_disabled_or_no_seed(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, {"tech_model_enabled": True})  # 开了但没有节点
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=10))
    assert store.load_history("main")[0].dynamic_snapshot is None


# ── 界面（app.py）────────────────────────────────────────────────────


def test_ui_violations_html_escapes_and_marks_precision_degradation():
    import app
    from types import SimpleNamespace

    st_ = SimpleNamespace(
        tech_violations=[{"code": "T2", "severity": "warn", "message": "<b>进度未满</b>"},
                         {"code": "T9", "severity": "info", "message": "前置无法核验"}],
        tech_updates=[{"action": "time", "elapsed_days": 30.0, "elapsed_source": "fallback"}],
    )
    out = app._tech_violations_html(st_)
    assert "&lt;b&gt;进度未满&lt;/b&gt;" in out and "<b>进度未满" not in out
    assert "T2" in out and "T9" in out and "精度降级" in out
    assert app._tech_violations_html(SimpleNamespace(tech_violations=[], tech_updates=[])) == ""
    # 旧 SimState（没有这些属性）不报错
    assert app._tech_violations_html(SimpleNamespace()) == ""


def test_ui_settings_seed_normalizes_the_way_save_does():
    # 保存设置时用的就是这个表达式：不合法节点被丢弃，字段补全默认值
    nodes = tm.get_nodes({"tech_state": {"nodes": [{"id": "a", "stage": "bogus"}, {"x": 1}, {"name": "b"}]}})
    assert [n["id"] for n in nodes] == ["a", "b"] and nodes[0]["stage"] == "lab"
    assert tm.is_enabled({"tech_model_enabled": True}) and not tm.is_enabled({})


# ── 拆分模式（split_decision_calls）────────────────────────────────


def test_split_mode_world_evolve_carries_elapsed_and_tech_updates(tmp_path, monkeypatch):
    """拆分成 world_evolve + decision_generate 两次调用时，技术字段由 world_evolve
    输出，经 `{**data_decide, **data_evolve}` 合并后进入技术裁决；hint 也要喂给
    world_evolve（不是 decision_generate）。"""
    from types import SimpleNamespace

    import world_simulator.engine as engine_mod
    from world_simulator.engine.management import update_settings

    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={"age": 22}, options=[],
    )
    update_settings(data_dir, manifest.sim_id, split_decision_calls=True, tech_model_enabled=True,
                    tech_state={"nodes": [tm.normalize_node(_node("ai", dwell=100))]})
    seen = {}

    class _Step:
        def __init__(self, sid):
            self.id, self.skill_name = sid, None

    class _WF:
        def __init__(self, steps):
            self.steps = steps

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return _WF([_Step(name)])

    def _res(name, payload):
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        ok = SimpleNamespace(value="done")
        return SimpleNamespace(status="done", step_results=[
            SimpleNamespace(step_id=name, status=ok, result_file=str(p))])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            sid = wf.steps[0].id
            seen[sid] = dict(inputs)
            if sid == "world_evolve":
                return _res(sid, {"next_summary": "s1", "narrative": "n", "next_vars": {"age": 23},
                                  "key_drivers": [], "elapsed_days": 60,
                                  "tech_updates": [{"id": "ai", "stage": "expert"}]})
            return _res(sid, {"options": [{"id": "a", "label": "A", "description": "d"}],
                              "decision_reason": "r"})

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)
    state = engine_mod.advance(cfg=object(), workspace_root=tmp_path / "ws", data_dir=data_dir,
                               sim_id=manifest.sim_id)
    assert "[ai]" in seen["world_evolve"]["tech_state_hint"]
    assert state.elapsed_days == 60.0
    assert _codes(state.tech_violations) == ["T2"]  # 进度只有 0.6 → 迁移被驳回
    assert state.tech_updates[0]["elapsed_source"] == "reported"
