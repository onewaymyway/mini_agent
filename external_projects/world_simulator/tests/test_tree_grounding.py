"""tests/test_tree_grounding.py — WP3 P5c（第二十二轮 / 阶段 P5c）：因果树接地（3c）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §4 WP3 的 3c。
引擎做：前置强制（G1）、互斥组（G2/G3）、结构化触发条件（建议 / 子开关自动 G4）、互斥落败者
（建议 / 自动 G5）、likelihood 校准账本；不改任何数值、不判断条件语义。这里逐条验证，并验证
`advance()` 端到端接入、与因果引擎/一致性守卫的联动、开关关闭时与之前逐字节一致。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import causal_tree
from world_simulator import consistency_guard as cg
from world_simulator import tree_grounding as tg
from world_simulator.engine.management import get_simulation
from world_simulator.state_model import SimState

from test_fast_forward import _default_payload
from test_tech_model import _adv, _make_sim, _node


def _b(bid, status="dormant", **extra):
    d = {"id": bid, "description": bid, "likelihood": "medium", "status": status}
    d.update(extra)
    return d


def _line(lid, *branches):
    return {"id": lid, "label": lid, "future_tree": {"as_of_step": 0, "branches": list(branches)}}


def _run(before, after, *, vars_=None, settings=None, auto=False, audit=None, step=1):
    """`before`/`after` 都是 causal_lines；返回 (lines, audit, entries)。"""
    return tg.enforce_step(
        after, tg.status_index(before), vars_=vars_ or {}, settings=settings or {}, step=step,
        tree_updates=audit, auto=auto,
    )


def _st(lines, lid, bid):
    line = next(x for x in lines if x["id"] == lid)
    return next(b for b in line["future_tree"]["branches"] if b["id"] == bid)["status"]


def _codes(entries):
    return [e["code"] for e in entries]


def _on(**extra):
    s = {"tree_grounding_enabled": True}
    s.update(extra)
    return s


# ── 开关 / 规整 ─────────────────────────────────────────────────────


def test_disabled_is_a_noop():
    assert not tg.is_enabled(None) and not tg.is_enabled({}) and not tg.is_enabled({"tree_auto_transition": True})
    assert not tg.auto_transition_enabled({"tree_auto_transition": True})  # 子开关需要总开关
    assert tg.build_hint({"tree_auto_transition": True}, [_line("a", _b("x"))], {}) == ""
    assert tg.safe_build_hint(None) == ""


def test_auto_requires_master_switch():
    assert tg.is_enabled(_on()) and not tg.auto_transition_enabled(_on())
    assert tg.auto_transition_enabled(_on(tree_auto_transition=True))


def test_normalize_branch_new_fields_only_present_when_set():
    plain = causal_tree._normalize_branch({"id": "a", "description": "d"}, used_ids=set())
    assert "trigger_condition" not in plain and "exclusive_group" not in plain  # 旧形状逐字节不变
    full = causal_tree._normalize_branch(
        {"id": "b", "description": "d", "trigger_condition": {"var": "a.b", "op": ">=", "value": 5},
         "exclusive_group": "  路线  "}, used_ids=set())
    assert full["trigger_condition"] == {"var": "a.b", "op": ">=", "value": 5}
    assert full["exclusive_group"] == "路线"


def test_normalize_branch_ignores_bad_trigger_condition_shapes():
    for bad in ("x>5", 5, [], {}, ["x", 1], None):
        n = causal_tree._normalize_branch({"id": "a", "description": "d", "trigger_condition": bad}, used_ids=set())
        assert "trigger_condition" not in n, bad
    n = causal_tree._normalize_branch(
        {"id": "a", "description": "d", "trigger_condition": [{"var": "x", "op": ">", "value": 1}, "junk"]},
        used_ids=set())
    assert n["trigger_condition"] == [{"var": "x", "op": ">", "value": 1}]


def test_default_tree_and_legacy_data_shape_unchanged():
    tree = causal_tree.build_default_future_tree("线")
    assert all("trigger_condition" not in b and "exclusive_group" not in b for b in tree["branches"])


def test_status_index_only_top_level_and_canonical():
    lines = [_line("a", _b("x", "open"), _b("y", "confirmed", children=[_b("z", "active")]))]
    assert tg.status_index(lines) == {("a", "x"): "dormant", ("a", "y"): "resolved"}


# ── 前置解析 ────────────────────────────────────────────────────────


def test_unmet_prerequisites_resolution_rules():
    lines = [
        _line("a", _b("p1", "resolved"), _b("p2", "active"), _b("dup", "resolved"),
              _b("t", "active", prerequisites=["p1", "p2", "free text", "t", "dup"])),
        _line("b", _b("dup", "dormant"), _b("only_b", "dormant")),
    ]
    idx = tg.status_index(lines)
    branch = lines[0]["future_tree"]["branches"][3]
    unmet, unv = tg.unmet_prerequisites("a", branch, idx)
    assert unmet == ["p2"]  # p1 已 resolved；p2 只是 active（严格口径）
    assert set(unv) == {"free text", "t"}  # 自由文本、自指无法核验
    # 同线 id 优先于跨线；dup 在本线 resolved → 满足
    assert "dup" not in unmet
    # 跨线唯一命中
    b2 = _b("q", "active", prerequisites=["only_b"])
    unmet2, _ = tg.unmet_prerequisites("a", b2, idx)
    assert unmet2 == ["only_b"]
    # 跨线重名且本线没有 → 歧义 → 无法核验
    lines3 = [_line("a"), _line("b", _b("d")), _line("c", _b("d"))]
    unmet3, unv3 = tg.unmet_prerequisites("a", _b("q", prerequisites=["d"]), tg.status_index(lines3))
    assert unmet3 == [] and unv3 == ["d"]


# ── G1：前置强制 ────────────────────────────────────────────────────


def test_g1_new_active_with_unmet_prerequisite_is_downgraded_and_audit_patched():
    before = [_line("a", _b("p"), _b("t", prerequisites=["p"]))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][1]["status"] = "active"
    audit = [{"line_id": "a", "status_updates": [{"branch_id": "t", "status": "active"}]}]
    lines, new_audit, entries = _run(before, after, audit=audit)
    assert _st(lines, "a", "t") == "emerging"
    assert _codes(entries) == ["G1"] and entries[0]["action"] == "downgraded"
    assert entries[0]["detail"]["unmet_prerequisites"] == ["p"]
    assert new_audit[0]["status_updates"][0]["status"] == "emerging"  # 审计反映实际生效的状态
    assert new_audit[0]["status_updates"][0]["grounded"] is True
    assert audit[0]["status_updates"][0]["status"] == "active"  # 入参不被修改
    assert after[0]["future_tree"]["branches"][1]["status"] == "active"


def test_g1_satisfied_or_unverifiable_prerequisite_does_not_block():
    before = [_line("a", _b("p", "resolved"), _b("t", prerequisites=["p", "自由文本"]))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][1]["status"] = "active"
    lines, _a, entries = _run(before, after)
    assert _st(lines, "a", "t") == "active" and entries == []


def test_g1_does_not_retroactively_downgrade_already_active_branch():
    before = [_line("a", _b("p"), _b("t", "active", prerequisites=["p"]))]
    lines, _a, entries = _run(before, copy.deepcopy(before))
    assert _st(lines, "a", "t") == "active" and entries == []


def test_g1_brand_new_branch_created_active_is_also_checked():
    before = [_line("a", _b("p"))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"].append(_b("n", "active", prerequisites=["p"]))
    lines, _a, entries = _run(before, after)
    assert _st(lines, "a", "n") == "emerging" and _codes(entries) == ["G1"]


def test_resolved_with_unmet_prerequisite_is_not_blocked_documented_boundary():
    before = [_line("a", _b("p"), _b("t", prerequisites=["p"]))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][1]["status"] = "resolved"
    lines, _a, entries = _run(before, after)
    assert _st(lines, "a", "t") == "resolved" and entries == []


# ── G2/G3：互斥组 ───────────────────────────────────────────────────


def test_g2_new_active_blocked_when_group_already_held():
    before = [_line("a", _b("x", "active", exclusive_group="g"), _b("y", exclusive_group="g"))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][1]["status"] = "active"
    lines, _a, entries = _run(before, after)
    assert _st(lines, "a", "y") == "emerging" and _st(lines, "a", "x") == "active"
    assert _codes(entries) == ["G2"] and entries[0]["detail"]["held_by"] == ["x"]


def test_g2_holder_released_in_same_step_means_no_conflict():
    before = [_line("a", _b("x", "active", exclusive_group="g"), _b("y", exclusive_group="g"))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][0]["status"] = "invalidated"
    after[0]["future_tree"]["branches"][1]["status"] = "active"
    lines, _a, entries = _run(before, after)
    assert _st(lines, "a", "y") == "active" and entries == []


def test_g2_two_new_actives_in_same_group_first_wins():
    before = [_line("a", _b("x", exclusive_group="g"), _b("y", exclusive_group="g"))]
    after = copy.deepcopy(before)
    for b in after[0]["future_tree"]["branches"]:
        b["status"] = "active"
    lines, _a, entries = _run(before, after)
    assert [_st(lines, "a", "x"), _st(lines, "a", "y")] == ["active", "emerging"]
    assert _codes(entries) == ["G2"] and entries[0]["branch_id"] == "y"


def test_g2_groups_are_scoped_per_line():
    before = [_line("a", _b("x", "active", exclusive_group="g")), _line("b", _b("y", exclusive_group="g"))]
    after = copy.deepcopy(before)
    after[1]["future_tree"]["branches"][0]["status"] = "active"
    lines, _a, entries = _run(before, after)
    assert _st(lines, "b", "y") == "active" and entries == []


def test_g3_new_resolved_with_holder_is_flagged_not_rewritten():
    before = [_line("a", _b("x", "active", exclusive_group="g"), _b("y", exclusive_group="g"))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][1]["status"] = "resolved"
    lines, _a, entries = _run(before, after)
    assert _st(lines, "a", "y") == "resolved"  # resolved 是对已发生的陈述，不改
    assert _codes(entries) == ["G3"] and entries[0]["action"] == "flagged"


def test_new_resolved_claims_the_group_before_new_active_in_same_step():
    before = [_line("a", _b("x", exclusive_group="g"), _b("y", exclusive_group="g"))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][0]["status"] = "active"
    after[0]["future_tree"]["branches"][1]["status"] = "resolved"
    lines, _a, entries = _run(before, after)
    assert _st(lines, "a", "x") == "emerging" and _codes(entries) == ["G2"]


def test_resolving_the_holder_itself_is_not_a_conflict():
    before = [_line("a", _b("x", "active", exclusive_group="g"))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][0]["status"] = "resolved"
    _l, _a, entries = _run(before, after)
    assert entries == []


# ── G4/G5：触发条件 / 落败者（建议 vs 自动）─────────────────────────


def _cond(var="age", op=">=", value=25):
    return {"var": var, "op": op, "value": value}


def test_trigger_met_is_only_a_suggestion_when_auto_is_off():
    lines = [_line("a", _b("t", trigger_condition=_cond()))]
    out, audit, entries = _run(lines, copy.deepcopy(lines), vars_={"age": 30}, auto=False)
    assert _st(out, "a", "t") == "dormant" and entries == [] and audit == []
    sugs = tg.pending_suggestions(lines, {"age": 30}, {})
    assert [(s["kind"], s["suggested_status"]) for s in sugs] == [("trigger_met", "active")]


def test_g4_auto_activates_when_condition_met_and_adds_audit_entry():
    lines = [_line("a", _b("t", "emerging", trigger_condition=_cond()))]
    out, audit, entries = _run(lines, copy.deepcopy(lines), vars_={"age": 30}, auto=True)
    assert _st(out, "a", "t") == "active" and _codes(entries) == ["G4"]
    assert entries[0]["detail"]["from_status"] == "emerging"
    assert audit[-1]["line_id"] == "a" and audit[-1]["auto"] is True
    assert audit[-1]["status_updates"] == [{"branch_id": "t", "status": "active", "auto": True}]


def test_g4_not_met_missing_var_or_bad_condition_do_nothing():
    for vars_, cond in (({"age": 20}, _cond()), ({}, _cond()), ({"age": 30}, {"var": "age", "op": "~", "value": 1}),
                        ({"age": "x"}, _cond())):
        lines = [_line("a", _b("t", trigger_condition=cond))]
        out, _a, entries = _run(lines, copy.deepcopy(lines), vars_=vars_, auto=True)
        assert _st(out, "a", "t") == "dormant" and entries == [], (vars_, cond)


def test_g4_blocked_by_unmet_prerequisite_or_held_group():
    lines = [_line("a", _b("p"), _b("t", trigger_condition=_cond(), prerequisites=["p"]))]
    out, _a, entries = _run(lines, copy.deepcopy(lines), vars_={"age": 30}, auto=True)
    assert _st(out, "a", "t") == "dormant" and entries == []
    sug = tg.pending_suggestions(lines, {"age": 30}, {})
    assert {s["branch_id"]: s["kind"] for s in sug} == {"t": "trigger_blocked"}
    assert "前置 p" in sug[0]["reasons"][0]

    lines2 = [_line("a", _b("x", "active", exclusive_group="g"), _b("t", trigger_condition=_cond(), exclusive_group="g"))]
    out2, _a, entries2 = _run(lines2, copy.deepcopy(lines2), vars_={"age": 30}, auto=True)
    assert _st(out2, "a", "t") == "dormant" and entries2 == []
    assert tg.pending_suggestions(lines2, {"age": 30}, {})[0]["kind"] == "trigger_blocked"


def test_g4_two_triggered_in_same_group_only_first_activates():
    lines = [_line("a", _b("x", trigger_condition=_cond(), exclusive_group="g"),
                   _b("y", trigger_condition=_cond(), exclusive_group="g"))]
    out, _a, entries = _run(lines, copy.deepcopy(lines), vars_={"age": 30}, auto=True)
    assert [_st(out, "a", "x"), _st(out, "a", "y")] == ["active", "dormant"]
    assert _codes(entries) == ["G4"]


def test_g4_tech_stage_condition_reads_settings_tech_state():
    settings = {"tech_state": {"nodes": [_node("ai", stage="developer")]}}
    lines = [_line("a", _b("t", trigger_condition={"tech": "ai", "min_stage": "developer"}))]
    out, _a, entries = _run(lines, copy.deepcopy(lines), settings=settings, auto=True)
    assert _st(out, "a", "t") == "active" and _codes(entries) == ["G4"]
    low = {"tech_state": {"nodes": [_node("ai", stage="expert")]}}
    out2, _a, entries2 = _run(lines, copy.deepcopy(lines), settings=low, auto=True)
    assert _st(out2, "a", "t") == "dormant" and entries2 == []


def test_g4_condition_list_requires_all():
    lines = [_line("a", _b("t", trigger_condition=[_cond(), _cond("score", ">", 10)]))]
    out, _a, _e = _run(lines, copy.deepcopy(lines), vars_={"age": 30, "score": 5}, auto=True)
    assert _st(out, "a", "t") == "dormant"
    out, _a, _e = _run(lines, copy.deepcopy(lines), vars_={"age": 30, "score": 11}, auto=True)
    assert _st(out, "a", "t") == "active"


def test_g5_losers_auto_invalidated_only_when_auto_and_winner_newly_resolved():
    before = [_line("a", _b("w", "active", exclusive_group="g"), _b("l1", exclusive_group="g"),
                    _b("l2", "emerging", exclusive_group="g"), _b("other"))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][0]["status"] = "resolved"
    off, a_off, e_off = _run(before, after, auto=False)
    assert e_off == [] and _st(off, "a", "l1") == "dormant"
    on, a_on, e_on = _run(before, after, auto=True)
    assert [_st(on, "a", x) for x in ("l1", "l2", "other")] == ["invalidated", "invalidated", "dormant"]
    assert _codes(e_on) == ["G5", "G5"] and all(x["auto"] for x in a_on)
    # 建议口径（不依赖是否自动）
    kinds = {(s["branch_id"], s["kind"]) for s in tg.pending_suggestions(after, {}, {})}
    assert kinds == {("l1", "exclusive_loser"), ("l2", "exclusive_loser")}


def test_g5_never_rewrites_siblings_that_are_already_terminal_or_active():
    before = [_line("a", _b("w", "active", exclusive_group="g"), _b("old", "expired", exclusive_group="g"),
                    _b("done", "resolved", exclusive_group="g"), _b("gone", "invalidated", exclusive_group="g"),
                    _b("l", exclusive_group="g"))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][0]["status"] = "resolved"
    out, _a, entries = _run(before, after, auto=True)
    assert [_st(out, "a", x) for x in ("old", "done", "gone", "l")] == ["expired", "resolved", "invalidated", "invalidated"]
    assert [e["branch_id"] for e in entries if e["code"] == "G5"] == ["l"]  # 只动 dormant/emerging


def test_enforce_step_never_mutates_inputs_and_returns_equal_when_nothing_to_do():
    lines = [_line("a", _b("x", "active", exclusive_group="g"), _b("y", trigger_condition=_cond()))]
    snap = copy.deepcopy(lines)
    out, audit, entries = _run(lines, lines, vars_={"age": 30}, auto=True, audit=[{"line_id": "a"}])
    assert lines == snap  # 入参没变
    assert entries and _st(out, "a", "y") == "active"
    out2, _a2, e2 = _run(snap, copy.deepcopy(snap), vars_={"age": 1}, auto=True)
    assert e2 == [] and out2 == snap


def test_safe_enforce_step_returns_g0_and_leaves_tree_alone_on_internal_error(monkeypatch):
    lines = [_line("a", _b("x"))]
    monkeypatch.setattr(tg, "enforce_step", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out, audit, entries = tg.safe_enforce_step(lines, {}, vars_={}, settings={}, step=1,
                                               tree_updates=[{"line_id": "a"}], auto=True)
    assert out == lines and audit == [{"line_id": "a"}] and _codes(entries) == ["G0"]


# ── 提示词 ──────────────────────────────────────────────────────────


def test_hint_has_protocol_and_wording_differs_by_auto():
    manual = tg.build_hint(_on(), [], {})
    auto = tg.build_hint(_on(tree_auto_transition=True), [], {})
    for text in (manual, auto):
        assert "因果树接地已开启" in text and "trigger_condition" in text and "exclusive_group" in text
    assert "只给**建议**" in manual and "自动**置为 active" not in manual
    assert "自动**置为 active" in auto


def test_hint_lists_current_suggestions_by_kind():
    lines = [_line("a", _b("p"), _b("blocked", prerequisites=["p"]), _b("t", trigger_condition=_cond()),
                   _b("w", "resolved", exclusive_group="g"), _b("l", exclusive_group="g"))]
    text = tg.build_hint(_on(), lines, {"age": 30})
    assert "触发条件已满足（建议考虑置为 active）：a/t" in text
    assert "互斥落败者（建议考虑置为 invalidated）：a/l" in text
    assert "前置未满足（现在不能 active）：a/blocked" in text


# ── 校准账本 ────────────────────────────────────────────────────────


def _snap_state(step, lines):
    return SimpleNamespace(step=step, dynamic_snapshot={"causal_lines": lines})


def test_ledger_records_likelihood_at_termination_and_skips_first_appearance():
    s0 = [_line("a", _b("h", likelihood="high"), _b("m", likelihood="medium"))]
    s1 = [_line("a", _b("h", "resolved", likelihood="high"), _b("m", likelihood="medium"),
                _b("born_done", "resolved", likelihood="low"))]
    s2 = [_line("a", _b("h", "resolved", likelihood="high"), _b("m", "expired", likelihood="low"),
                _b("born_done", "resolved", likelihood="low"))]  # m 终结时 likelihood 已被改成 low
    hist = [SimpleNamespace(step=0, dynamic_snapshot=None), _snap_state(1, s0), _snap_state(2, s1), _snap_state(3, s2)]
    ledger = tg.build_likelihood_ledger(hist)
    assert [(e["branch_id"], e["likelihood"], e["outcome"], e["step"]) for e in ledger] == [
        ("h", "high", "resolved", 2), ("m", "low", "expired", 3)]  # 取终结那一刻的档位；新出现就终结的不记


def test_ledger_ignores_terminal_to_terminal_changes_and_missing_snapshots():
    s0 = [_line("a", _b("x", "resolved"))]
    s1 = [_line("a", _b("x", "invalidated"))]
    assert tg.build_likelihood_ledger([_snap_state(1, s0), _snap_state(2, s1)]) == []
    assert tg.build_likelihood_ledger([]) == [] and tg.build_likelihood_ledger(None) == []


def _entries(*triples):
    return [{"line_id": "a", "branch_id": f"b{i}", "likelihood": lk, "outcome": oc, "step": i}
            for i, (lk, oc) in enumerate(triples)]


def test_summary_hit_rate_min_n_and_inversion():
    ledger = _entries(*([("high", "expired")] * 3 + [("medium", "resolved")] * 3 + [("low", "resolved")]))
    out = tg.summarize_ledger(ledger, min_n=3)
    assert out["by_likelihood"]["high"]["hit_rate"] == 0.0 and out["by_likelihood"]["medium"]["hit_rate"] == 1.0
    assert out["by_likelihood"]["low"]["enough_samples"] is False
    assert [(i["higher"], i["lower"]) for i in out["inversions"]] == [("high", "medium")]  # low 样本不足不参与
    assert out["total_terminal"] == 7


def test_summary_no_inversion_when_ordered_and_nominal_gap_only_when_given():
    ledger = _entries(*([("high", "resolved")] * 3 + [("medium", "resolved"), ("medium", "expired"), ("medium", "expired")]
                        + [("low", "expired")] * 3))
    out = tg.summarize_ledger(ledger, min_n=3)
    assert out["inversions"] == [] and "gap" not in out["by_likelihood"]["high"]
    out2 = tg.summarize_ledger(ledger, nominal={"high": 0.8, "medium": "bad", "low": 1.5}, min_n=3)
    assert abs(out2["by_likelihood"]["high"]["gap"] - 0.2) < 1e-9
    assert "expected" not in out2["by_likelihood"]["medium"] and "expected" not in out2["by_likelihood"]["low"]


def test_analyze_history_exposes_ledger_and_survives_errors(monkeypatch):
    hist = [_snap_state(1, [_line("a", _b("x", likelihood="high"))]),
            _snap_state(2, [_line("a", _b("x", "resolved", likelihood="high"))])]
    rep = cg.analyze_history(hist, causal_lines=hist[-1].dynamic_snapshot["causal_lines"],
                             config={"likelihood_nominal": {"high": 0.5}})
    led = rep["c8_likelihood_ledger"]
    assert led["total_terminal"] == 1 and led["by_likelihood"]["high"]["expected"] == 0.5
    monkeypatch.setattr(tg, "build_likelihood_ledger", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    rep2 = cg.analyze_history(hist, causal_lines=None)
    assert rep2["c8_likelihood_ledger"]["entries"] == [] and "c8_tree_calibration" in rep2


# ── SimState 序列化 ─────────────────────────────────────────────────


def test_simstate_roundtrip_and_empty_is_omitted():
    st = SimState(step=1, summary="x", vars={}, tree_grounding=[{"code": "G1", "action": "downgraded"}])
    back = SimState.from_dict(json.loads(json.dumps(st.to_dict())))
    assert back.tree_grounding == st.tree_grounding
    assert "tree_grounding" not in SimState(step=1, summary="x", vars={}).to_dict()
    assert SimState.from_dict({"step": 1}).tree_grounding == []


# ── advance() 端到端 ────────────────────────────────────────────────


def _sim(tmp_path, settings):
    data_dir = tmp_path / "data"
    return data_dir, _make_sim(data_dir, settings)


def _tree_settings(*lines, **extra):
    s = {"causal_lines": list(lines)}
    s.update(extra)
    return s


def _tu(line_id, branch_id, status):
    return {"line_id": line_id, "status_updates": [{"branch_id": branch_id, "status": status}]}


def test_advance_downgrades_and_persists_everywhere(tmp_path, monkeypatch):
    line = _line("econ", _b("p"), _b("t", prerequisites=["p"]))
    data_dir, store = _sim(tmp_path, _tree_settings(line, **_on()))
    cap: dict = {}
    state = _adv(monkeypatch, tmp_path, data_dir,
                 _default_payload(1, tree_updates=[_tu("econ", "t", "active")]), cap)
    assert "因果树接地已开启" in cap["inputs_list"][0]["tree_grounding_hint"]
    assert _codes(state.tree_grounding) == ["G1"]
    assert state.tree_updates[0]["status_updates"][0]["status"] == "emerging"  # 审计 = 实际生效
    persisted = store.load_history("main")[-1]
    assert _codes(persisted.tree_grounding) == ["G1"]
    snap_lines = persisted.dynamic_snapshot["causal_lines"]  # 分支作用域快照里也是降级后的结果
    assert _st(snap_lines, "econ", "t") == "emerging"
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert _st(manifest.settings["causal_lines"], "econ", "t") == "emerging"
    assert persisted.consistency_warnings == []  # 守卫看到的是降级后的树，没有 C3


def test_advance_with_switch_off_is_byte_identical_and_c3_still_fires(tmp_path, monkeypatch):
    line = _line("econ", _b("p"), _b("t", prerequisites=["p"]))
    data_dir, store = _sim(tmp_path, _tree_settings(line))  # 没开
    cap: dict = {}
    state = _adv(monkeypatch, tmp_path, data_dir,
                 _default_payload(1, tree_updates=[_tu("econ", "t", "active")]), cap)
    assert cap["inputs_list"][0]["tree_grounding_hint"] == ""
    assert state.tree_grounding == [] and "tree_grounding" not in state.to_dict()
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert _st(manifest.settings["causal_lines"], "econ", "t") == "active"  # 没拦
    assert "C3" in [w["code"] for w in state.consistency_warnings]  # 守卫只告警（既有行为）


def test_advance_auto_activation_uses_next_vars_and_feeds_the_causal_queue(tmp_path, monkeypatch):
    line = _line("econ", _b("t", trigger_condition={"var": "age", "op": ">=", "value": 25}))
    settings = _tree_settings(line, **_on(tree_auto_transition=True), causal_engine_enabled=True,
                              declared_causal_graph=[{"from_line_id": "econ", "to_line_id": "industry"}])
    data_dir, _store = _sim(tmp_path, settings)
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, next_vars={"age": 30}))
    assert _codes(state.tree_grounding) == ["G4"]
    assert state.tree_updates[-1]["auto"] is True
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert _st(manifest.settings["causal_lines"], "econ", "t") == "active"
    assert [q["trigger_reason"] for q in state.causal_queued] == ["tree_branch"]  # 自动迁移被触发源看见


def test_advance_condition_not_met_only_hints_next_step(tmp_path, monkeypatch):
    line = _line("econ", _b("t", trigger_condition={"var": "age", "op": ">=", "value": 25}))
    data_dir, _store = _sim(tmp_path, _tree_settings(line, **_on()))  # 手动模式
    s1 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, next_vars={"age": 30}))
    assert s1.tree_grounding == []  # 只建议，不动
    cap: dict = {}
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, next_vars={"age": 31}), cap)
    assert "econ/t" in cap["inputs_list"][0]["tree_grounding_hint"]
    assert "触发条件已满足" in cap["inputs_list"][0]["tree_grounding_hint"]


def test_advance_internal_error_does_not_break_the_step(tmp_path, monkeypatch):
    line = _line("econ", _b("p"), _b("t", prerequisites=["p"]))
    data_dir, _store = _sim(tmp_path, _tree_settings(line, **_on()))
    monkeypatch.setattr(tg, "enforce_step", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, tree_updates=[_tu("econ", "t", "active")]))
    assert state.step == 1 and _codes(state.tree_grounding) == ["G0"]
    assert state.tree_updates[0]["status_updates"][0]["status"] == "active"  # 出错 = 不改树


def test_grounded_tree_is_branch_scoped(tmp_path, monkeypatch):
    """主线上 t 被降级为 emerging；分叉后在新分支让前置 p 先 resolved 再 active t（合法），
    切回主线 t 仍是 emerging——接地结果跟着分支快照走，不互相污染。"""
    from world_simulator import branch_manager as bm

    line = _line("econ", _b("p"), _b("t", prerequisites=["p"]))
    data_dir, store = _sim(tmp_path, _tree_settings(line, **_on()))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, tree_updates=[_tu("econ", "t", "active")]))
    alt = bm.fork_branch(data_dir, "sim1", from_step=1)
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.branch == alt and _st(manifest.settings["causal_lines"], "econ", "t") == "emerging"

    s2 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        2, tree_updates=[{"line_id": "econ", "confirmed_branch": "p", "status_updates": [{"branch_id": "t", "status": "active"}]}]))
    assert s2.tree_grounding == []  # p 本步 resolved，前置已满足，t 合法 active
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert _st(manifest.settings["causal_lines"], "econ", "t") == "active"

    bm.switch_branch(data_dir, "sim1", "main")
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert _st(manifest.settings["causal_lines"], "econ", "t") == "emerging"
    assert _st(manifest.settings["causal_lines"], "econ", "p") == "dormant"


# ── 文档/模板接线 ───────────────────────────────────────────────────


def test_placeholder_present_in_both_workflows_and_skills_document_the_fields():
    root = Path(__file__).resolve().parent.parent
    for name in ("advance_step.yaml", "world_evolve.yaml"):
        text = (root / "workflows" / name).read_text(encoding="utf-8")
        assert "{tree_grounding_hint}" in text, name
    for tpl in ("life-sim", "group-evolution", "negotiation"):
        text = (root / "skills" / f"{tpl}-template" / "SKILL.md").read_text(encoding="utf-8")
        assert "trigger_condition" in text and "exclusive_group" in text and "因果树接地已开启" in text, tpl
