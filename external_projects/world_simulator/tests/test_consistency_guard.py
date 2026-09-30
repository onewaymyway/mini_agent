"""tests/test_consistency_guard.py — WP4（第二十二轮）：一致性守卫 + 真实性体检。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md`
§2.5 / §4 WP4。守卫只记录、不阻断、不改数值；这里逐项验证 C1–C8 的
判定边界、`advance()` 的端到端接入、开关、序列化兼容。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import causal_tree, consistency_guard as cg, quality_signals
from world_simulator.engine.advance import advance
from world_simulator.engine.management import get_simulation
from world_simulator.state_model import SimManifest, SimState
from world_simulator.store import SimStore, now_iso

from test_fast_forward import _default_payload, _patch_sequenced_advance_step_workflow


def _cap(name, stage, **extra):
    return {"capability": name, "maturity_stage": stage, **extra}


def _st(step, **kw):
    return SimState(step=step, summary=f"s{step}", **kw)


def _codes(warnings):
    return [w["code"] for w in warnings]


def _line(branches, line_id="L1"):
    return {"id": line_id, "label": line_id, "future_tree": {"as_of_step": 0, "branches": branches}}


def _br(bid, status="dormant", likelihood="medium", prerequisites=None):
    return {"id": bid, "description": bid, "status": status, "likelihood": likelihood,
            "prerequisites": prerequisites or []}


def _transitions(before_lines, after_lines, step=5):
    return cg.check_tree_transitions(cg.tree_index(before_lines), cg.tree_index(after_lines), step)


# ── C1 ───────────────────────────────────────────────────────────────


def test_c1_jump_regression_and_normal_progress():
    t = cg.StageTracker()
    assert t.feed(_st(1, capabilities_gained=[_cap("量子计算", "lab")])) == []
    assert t.feed(_st(2, capabilities_gained=[_cap("量子计算", "expert")])) == []  # +1 正常
    jump = t.feed(_st(4, capabilities_gained=[_cap("量子计算", "consumer")]))  # 跳过 developer
    assert _codes(jump) == ["C1"] and jump[0]["detail"]["kind"] == "jump"
    back = t.feed(_st(6, capabilities_gained=[_cap("量子计算", "lab")]))
    assert back[0]["detail"]["kind"] == "regression"


def test_c1_regression_with_reason_is_not_warned_and_unknown_stage_ignored():
    t = cg.StageTracker()
    t.feed(_st(1, capabilities_gained=[_cap("X", "consumer")]))
    assert t.feed(_st(2, capabilities_gained=[_cap("X", "lab", regression_reason="被禁用")])) == []
    assert t.feed(_st(3, capabilities_gained=[_cap("X", "not_a_stage"), _cap("", "lab")])) == []


def test_c1_tracks_by_name_case_insensitively():
    t = cg.StageTracker()
    t.feed(_st(1, capabilities_gained=[_cap("Fusion", "lab")]))
    assert _codes(t.feed(_st(2, capabilities_gained=[_cap("fusion", "developer")]))) == ["C1"]


# ── C2 / C3 / C4 ─────────────────────────────────────────────────────


def test_c2_revival_of_terminal_branch():
    found, _ = _transitions([_line([_br("a", "resolved")])], [_line([_br("a", "active")])])
    assert _codes(found) == ["C2"]
    found, _ = _transitions([_line([_br("a", "expired")])], [_line([_br("a", "emerging")])])
    assert _codes(found) == ["C2"]


def test_c4_terminal_to_terminal_and_active_to_dormant():
    found, _ = _transitions([_line([_br("a", "resolved")])], [_line([_br("a", "invalidated")])])
    assert _codes(found) == ["C4"]
    found, _ = _transitions([_line([_br("a", "active")])], [_line([_br("a", "dormant")])])
    assert _codes(found) == ["C4"]
    found, _ = _transitions([_line([_br("a", "resolved")])], [_line([_br("a", "dormant")])])
    assert _codes(found) == ["C4"]


def test_normal_transitions_and_new_branches_are_silent():
    ok = [("dormant", "emerging"), ("emerging", "active"), ("active", "resolved"),
          ("dormant", "expired"), ("active", "emerging"), ("emerging", "dormant")]
    for old, new in ok:
        found, _ = _transitions([_line([_br("a", old)])], [_line([_br("a", new)])])
        assert found == [], (old, new)
    found, _ = _transitions([_line([_br("a")])], [_line([_br("a"), _br("b", "active")])])
    assert found == []  # 新出现的分支不算迁移


def test_c3_unmet_prerequisite_and_satisfied_and_unverifiable():
    before = [_line([_br("p", "dormant"), _br("a", "emerging", prerequisites=["p"])])]
    after_bad = [_line([_br("p", "dormant"), _br("a", "active", prerequisites=["p"])])]
    found, unv = _transitions(before, after_bad)
    assert _codes(found) == ["C3"] and found[0]["detail"]["unmet_prerequisites"] == ["p"] and unv == 0

    after_ok = [_line([_br("p", "resolved"), _br("a", "active", prerequisites=["p"])])]
    before_ok = [_line([_br("p", "resolved"), _br("a", "emerging", prerequisites=["p"])])]
    assert _transitions(before_ok, after_ok)[0] == []

    free_text = [_line([_br("a", "active", prerequisites=["政策放开"])])]
    found, unv = _transitions([_line([_br("a", "emerging", prerequisites=["政策放开"])])], free_text)
    assert found == [] and unv == 1  # 自由文本无法核验：不告警，只计数


def test_c3_cross_line_unique_match_and_ambiguous_id():
    def lines(p_status, a_status, dup=False):
        l1 = _line([_br("a", a_status, prerequisites=["p"])], "L1")
        l2 = _line([_br("p", p_status)], "L2")
        out = [l1, l2]
        if dup:
            out.append(_line([_br("p", "dormant")], "L3"))
        return out

    found, _ = _transitions(lines("dormant", "emerging"), lines("dormant", "active"))
    assert _codes(found) == ["C3"]  # 跨线唯一命中
    found, unv = _transitions(lines("dormant", "emerging", True), lines("dormant", "active", True))
    assert found == [] and unv == 1  # 跨线重名有歧义：无法核验


# ── C6 ───────────────────────────────────────────────────────────────


def _vol_history(values, **kw):
    return [_st(i, vars={"gdp": v}, **kw) for i, v in enumerate(values)]


def test_c6_spike_flagged_and_steady_not():
    steady = _vol_history([100, 102, 101, 103, 102, 104, 103, 105])
    t = cg.VolatilityTracker(4.0, 5)
    assert sum(len(t.feed(s)) for s in steady) == 0
    t = cg.VolatilityTracker(4.0, 5)
    for s in steady:
        t.feed(s)
    spike = t.feed(_st(8, vars={"gdp": 500}))
    assert _codes(spike) == ["C6"] and spike[0]["detail"]["field"] == "gdp"


def test_c6_constant_counter_jump_flagged_and_needs_min_samples():
    ages = _vol_history([20, 21, 22, 23, 24, 25, 26])
    t = cg.VolatilityTracker(4.0, 5)
    for s in ages:
        assert t.feed(s) == []
    assert _codes(t.feed(_st(7, vars={"gdp": 40}))) == ["C6"]  # 匀速计数器突然 +14
    t2 = cg.VolatilityTracker(4.0, 5)
    for s in _vol_history([1, 2, 3]):
        t2.feed(s)
    assert t2.feed(_st(3, vars={"gdp": 999})) == []  # 样本不足不判断


def test_c6_skips_granularity_change_and_bad_leaves():
    t = cg.VolatilityTracker(4.0, 5)
    for s in _vol_history([20, 21, 22, 23, 24, 25]):
        t.feed(s)
    assert t.feed(_st(6, vars={"gdp": 200}, granularity_changed=True)) == []
    t3 = cg.VolatilityTracker(4.0, 5)
    assert t3.feed(_st(0, vars={"flag": True, "name": "x", "nest": {"a": 1}})) == []


# ── C5 / C7 / C8 / 体检汇总 ───────────────────────────────────────────


def test_c5_event_density_runs():
    hist = [_st(0), _st(1, major_decision=True), _st(2, structural_change={"k": 1}),
            _st(3, capabilities_gained=[_cap("a", "lab")]), _st(4), _st(5, major_decision=True)]
    d = cg.analyze_history(hist)["c5_event_density"]
    assert (d["advanced_steps"], d["dramatic_steps"]) == (5, 4)
    assert d["longest_run"] == 3 and d["runs_at_or_above_threshold"] == 1
    assert cg.analyze_history([_st(0)])["c5_event_density"]["ratio"] is None


def test_c7_edge_coverage():
    declared = [{"from_line_id": "A", "to_line_id": "B"}]
    hist = [_st(1, causal_links=[
        {"line_id": "B", "source_line_id": "A"},   # 已声明
        {"line_id": "C", "source_line_id": "A"},   # 未声明
        {"line_id": "B", "source_line_id": ""},     # 无发起线：不计
        {"line_id": "B", "source_line_id": "B"},    # 自环：不计
    ])]
    c7 = cg.analyze_history(hist, declared_causal_graph=declared)["c7_edge_coverage"]
    assert (c7["cross_line_links"], c7["undeclared"], c7["ratio"]) == (2, 1, 0.5)
    assert cg.analyze_history(hist)["c7_edge_coverage"]["ratio"] is None  # 没声明图：无分母


def test_c8_calibration_by_likelihood():
    lines = [_line([
        _br("a", "resolved", "high"), _br("b", "expired", "high"), _br("c", "resolved", "high"),
        _br("d", "dormant", "high"), _br("e", "invalidated", "low"), _br("f", "active", "low"),
    ])]
    cal = cg.tree_calibration(lines)
    assert (cal["high"]["terminal"], cal["high"]["resolved"], cal["high"]["pending"]) == (3, 2, 1)
    assert abs(cal["high"]["hit_rate"] - 2 / 3) < 1e-9
    assert cal["low"]["hit_rate"] == 0.0 and cal["medium"]["hit_rate"] is None


def test_analyze_history_uses_snapshot_chain_and_reports_coverage():
    def snap(status):
        return {"causal_lines": [_line([_br("a", status)])]}

    hist = [
        _st(0),
        _st(1, dynamic_snapshot=snap("resolved")),
        _st(2),  # 无快照：沿用上一份
        _st(3, dynamic_snapshot=snap("active")),  # 复活
    ]
    res = cg.analyze_history(hist, causal_lines=snap("active")["causal_lines"])
    assert res["warning_counts"] == {"C2": 1}
    assert res["warnings"][0]["step"] == 3
    assert res["coverage"]["snapshot_steps"] == 2
    assert res["coverage"]["tree_transition_pairs_checked"] == 1
    assert "不是语义真实性评分" in res["disclaimer"]


def test_analyze_history_on_legacy_history_has_no_tree_coverage():
    res = cg.analyze_history([_st(0), _st(1), _st(2)])
    assert res["coverage"]["snapshot_steps"] == 0 and res["warnings"] == []


def test_quality_signals_wrapper_and_existing_summary_unchanged():
    assert quality_signals.summarize_realism_health([_st(0)])["total_steps"] == 1
    assert set(quality_signals.summarize_quality_signals([_st(0)])) == {
        "total_steps", "explainability", "cross_line_influence", "branch_diversity",
        "expansion_usage", "uncertainty_coverage",
    }


# ── 开关 / 兜底 / 序列化 ─────────────────────────────────────────────


def test_safe_check_step_switch_and_exception_fallback(monkeypatch):
    hist = [_st(1, capabilities_gained=[_cap("X", "lab")])]
    new = _st(2, capabilities_gained=[_cap("X", "consumer")])
    assert _codes(cg.safe_check_step(hist, new, {})) == ["C1"]  # 默认开
    assert cg.safe_check_step(hist, new, {"consistency_guard_enabled": False}) == []

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cg, "check_step", boom)
    assert cg.safe_check_step(hist, new, {}) == []  # 守卫自己出错不能连累推进


def test_settings_can_override_thresholds():
    hist = _vol_history([100, 102, 101, 103, 102, 104])
    new = _st(6, vars={"gdp": 120})
    assert cg.safe_check_step(hist, new, {"z_threshold": 1000}) == []
    assert _codes(cg.safe_check_step(hist, new, {"z_threshold": 2.0})) == ["C6"]


def test_consistency_warnings_serialization_roundtrip_and_omission():
    plain = SimState(step=1, summary="s")
    assert "consistency_warnings" not in plain.to_dict()  # 空 → 不输出，旧格式逐字节不变
    assert SimState.from_dict(plain.to_dict()).consistency_warnings == []
    w = [{"code": "C1", "severity": "warn", "step": 1, "message": "m", "detail": {"a": 1}}]
    restored = SimState.from_dict(SimState(step=1, summary="s", consistency_warnings=w).to_dict())
    assert restored.consistency_warnings == w and restored.consistency_warnings[0] is not w[0]


# ── advance() 端到端 ────────────────────────────────────────────────


def _make_sim(data_dir, settings):
    store = SimStore.for_root(data_dir, "sim1")
    ts = now_iso()
    store.save_manifest(SimManifest(sim_id="sim1", template="life_sim", intent="i", title="t",
                                    created_at=ts, updated_at=ts, settings=settings))
    store.append_state(SimState(step=0, summary="s0", vars={"age": 20}, options=[]))
    return store


def _lines_settings():
    lines = causal_tree.ensure_future_trees([{"id": "L1", "label": "线1", "time_granularity": ""}], as_of_step=0)
    return {"causal_lines": lines}, lines[0]["future_tree"]["branches"][0]["id"]


def _run(monkeypatch, tmp_path, data_dir, payload):
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, [payload], {})
    return advance(object(), tmp_path, data_dir, "sim1")


def test_advance_records_c2_when_resolved_branch_is_revived(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    settings, bid = _lines_settings()
    store = _make_sim(data_dir, settings)
    _run(monkeypatch, tmp_path, data_dir, _default_payload(1, tree_updates=[{"line_id": "L1", "confirmed_branch": bid}]))
    revive = _default_payload(2, tree_updates=[{"line_id": "L1", "status_updates": [{"branch_id": bid, "status": "active"}]}])
    state = _run(monkeypatch, tmp_path, data_dir, revive)
    assert _codes(state.consistency_warnings) == ["C2"]
    assert state.consistency_warnings[0]["detail"]["branch_id"] == bid
    persisted = store.load_history("main")[-1]
    assert _codes(persisted.consistency_warnings) == ["C2"]  # 已落盘
    # 只记录不阻断：树上这次复活照常生效
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    branch = next(b for b in manifest.settings["causal_lines"][0]["future_tree"]["branches"] if b["id"] == bid)
    assert branch["status"] == "active"
    # 体检从历史重算得到同一条告警（快照链）
    report = quality_signals.summarize_realism_health(
        store.load_history("main"), causal_lines=manifest.settings["causal_lines"])
    assert report["warning_counts"] == {"C2": 1}


def test_advance_switch_off_and_normal_step_produce_no_warnings(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    settings, bid = _lines_settings()
    settings["consistency_guard_enabled"] = False
    store = _make_sim(data_dir, settings)
    _run(monkeypatch, tmp_path, data_dir, _default_payload(1, tree_updates=[{"line_id": "L1", "confirmed_branch": bid}]))
    revive = _default_payload(2, tree_updates=[{"line_id": "L1", "status_updates": [{"branch_id": bid, "status": "active"}]}])
    state = _run(monkeypatch, tmp_path, data_dir, revive)
    assert state.consistency_warnings == []
    assert "consistency_warnings" not in store.load_history("main")[-1].to_dict()


def test_advance_without_causal_lines_is_unaffected(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, {})
    state = _run(monkeypatch, tmp_path, data_dir, _default_payload(1))
    assert state.consistency_warnings == []
