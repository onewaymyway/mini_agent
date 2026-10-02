"""tests/test_tree_effects.py — WP3 P5d（第二十二轮 / 阶段 P5d）：树影响世界（3d）。

分支可声明 `effects_if_active`；分支**本步新变为 active**（且接地后仍是 active）时，这些声明入队成待兑现项，
之后与因果边同一条路径（到期提示 → `effect_dispositions` → 结案/统计）。引擎不改数值、不判断声明是否合理。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import causal_engine as ce
from world_simulator import causal_tree
from world_simulator import tree_effects as te
from world_simulator import tree_grounding as tg
from world_simulator.engine.management import get_simulation

from test_fast_forward import _default_payload
from test_tech_model import _adv, _make_sim
from test_tree_grounding import _b, _line, _tu, _st


def _eff(to="industry", **extra):
    d = {"to_line_id": to, "mechanism": "m", "sign": "positive", "strength": "high"}
    d.update(extra)
    return d


def _on(**extra):
    s = {"causal_engine_enabled": True, "tree_effects_enabled": True}
    s.update(extra)
    return s


def _ns(step=1, vars_=None):
    return SimpleNamespace(step=step, vars=vars_ or {})


def _queue(lines, before, *, settings=None, pending=None, step=1, vars_=None):
    st = settings if settings is not None else _on(causal_lines=lines)
    return te.queue_tree_effects(st, pending, _ns(step, vars_), tg.status_index(before), lines)


# ── causal_tree 形状规整 ────────────────────────────────────────────


def test_branch_normalization_keeps_effects_only_when_filled():
    used: set = set()
    plain = causal_tree._normalize_branch({"id": "a", "description": "d"}, used_ids=used)
    assert "effects_if_active" not in plain  # 旧形状逐字节不变
    raw = {"id": "b", "description": "d", "effects_if_active": [
        _eff(delay_days="90", junk=1), {"mechanism": "没目标"}, "不是对象", {"to_line_id": "x", "delay_days": -3},
        {"to_line_id": "y", "delay_days": True}, {"to_line_id": "z", "condition": "bad"},
    ]}
    b = causal_tree._normalize_branch(raw, used_ids=used)
    effs = b["effects_if_active"]
    assert [e["to_line_id"] for e in effs] == ["industry", "x", "y", "z"]
    assert effs[0]["delay_days"] == 90.0 and "junk" not in effs[0]
    assert "delay_days" not in effs[1] and "delay_days" not in effs[2] and "condition" not in effs[3]
    assert "effects_if_active" not in causal_tree._normalize_branch(
        {"id": "c", "description": "d", "effects_if_active": "x"}, used_ids=used)


def test_normalize_effect_semantics():
    e, note = te.normalize_effect({"to_line_id": "t", "sign": "+", "strength": "HIGH", "delay_steps": 2.7})
    assert e["sign"] == "positive" and e["strength"] == "high" and e["delay_steps"] == 2 and note is None
    e, note = te.normalize_effect({"to_line_id": "t", "sign": "wat", "strength": "huge"})
    assert e["sign"] is None and e["strength"] is None and "sign" in note and "strength" in note
    assert te.normalize_effect({"mechanism": "x"})[0] is None and te.normalize_effect("x")[0] is None


def test_enabled_requires_both_switches():
    assert not te.is_enabled({"tree_effects_enabled": True})
    assert not te.is_enabled({"causal_engine_enabled": True})
    assert te.is_enabled(_on()) and te.is_requested({"tree_effects_enabled": True})
    assert te.build_hint({"tree_effects_enabled": True}) == "" and "effects_if_active" in te.build_hint(_on())


# ── 入队口径 ────────────────────────────────────────────────────────


def test_new_active_queues_and_stays_quiet_afterwards():
    before = [_line("econ", _b("t", effects_if_active=[_eff()]))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][0]["status"] = "active"
    pend, queued, viol = _queue(after, before, settings=_on(causal_lines=after, tech_state={"nodes": []}))
    assert [p["pending_id"] for p in pend] == ["tree:econ/t->industry@1"]
    p = pend[0]
    assert p["source"] == "tree" and p["branch_id"] == "t" and p["from_line_id"] == "econ"
    assert p["trigger_reason"] == "tree_effect" and p["sign"] == "positive"
    assert [q["action"] for q in queued] == ["queued"]
    assert [v["code"] for v in viol] == ["E7"]  # industry 没登记 → 疑似笔误，仍入队
    # 此后一直 active：不重复入队
    pend2, queued2, _v = _queue(after, after, pending=pend, step=2)
    assert len(pend2) == 1 and queued2 == []


def test_not_queued_when_downgraded_or_not_active_or_switch_off():
    before = [_line("econ", _b("t", effects_if_active=[_eff()]))]
    dormant = copy.deepcopy(before)
    assert _queue(dormant, before)[0] == []
    active = copy.deepcopy(before)
    active[0]["future_tree"]["branches"][0]["status"] = "active"
    assert _queue(active, before, settings={"tree_effects_enabled": True})[0] == []  # 因果引擎没开
    assert _queue(active, before, settings=_on())[0] != []
    assert te.queue_tree_effects(_on(), [], _ns(), None, active)[0] == []  # 没有 status_before


def test_new_branch_added_directly_as_active_counts():
    after = [_line("econ", _b("t", "active", effects_if_active=[_eff()]))]
    pend, _q, _v = _queue(after, [_line("econ")])
    assert len(pend) == 1


def test_reactivation_accumulates_retrigger_on_open_entry():
    before = [_line("econ", _b("t", effects_if_active=[_eff()]))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][0]["status"] = "active"
    pend, _q, _v = _queue(after, before)
    pend2, queued2, _v = _queue(after, before, pending=pend, step=2)  # 离开 active 又回来
    assert len(pend2) == 1 and pend2[0]["retrigger_count"] == 1 and queued2[0]["action"] == "retriggered"


def test_effect_condition_gate_and_multiple_effects():
    before = [_line("econ", _b("t", effects_if_active=[
        _eff("a", condition={"var": "x", "op": ">=", "value": 5}), _eff("b")]))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][0]["status"] = "active"
    pend, _q, _v = _queue(after, before, vars_={"x": 1})
    assert [p["to_line_id"] for p in pend] == ["b"]
    pend, _q, _v = _queue(after, before, vars_={"x": 9})
    assert [p["to_line_id"] for p in pend] == ["a", "b"]


def test_queue_full_E6_shared_cap_and_E5_ignored_field():
    before = [_line("econ", _b("t", effects_if_active=[_eff("a", sign="wat")]))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][0]["status"] = "active"
    full = [{"pending_id": f"e{i}@0", "edge_id": f"e{i}"} for i in range(2)]
    st = _on(causal_params={"max_pending": 2})
    pend, _q, viol = _queue(after, before, settings=st, pending=full)
    assert len(pend) == 2 and "E6" in [v["code"] for v in viol]
    pend, _q, viol = _queue(after, before, settings=_on())
    assert len(pend) == 1 and pend[0]["sign"] is None and "E5" in [v["code"] for v in viol]


def test_known_targets_suppress_E7():
    before = [_line("econ", _b("t", effects_if_active=[_eff("industry")])), _line("industry")]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][0]["status"] = "active"
    _p, _q, viol = _queue(after, before, settings=_on(causal_lines=after))
    assert viol == []


def test_input_not_mutated_and_safe_wrapper():
    before = [_line("econ", _b("t", effects_if_active=[_eff()]))]
    after = copy.deepcopy(before)
    after[0]["future_tree"]["branches"][0]["status"] = "active"
    snap = copy.deepcopy(after)
    pending = [{"pending_id": "x@0", "edge_id": "x", "retrigger_count": 0}]
    _queue(after, before, pending=pending)
    assert after == snap and pending == [{"pending_id": "x@0", "edge_id": "x", "retrigger_count": 0}]
    out, queued, viol = te.safe_queue_tree_effects(_on(), pending, None, tg.status_index(before), after)
    assert out == pending and queued == [] and viol[0]["code"] == "E0"


def test_declared_effects_summary_and_delay_detection():
    lines = [_line("econ", _b("t", "active", effects_if_active=[_eff(delay_days=30)]), _b("u"))]
    d = te.declared_effects(lines)
    assert d[0]["line_id"] == "econ" and d[0]["branch_id"] == "t" and d[0]["status"] == "active"
    assert te.has_day_delays(_on(causal_lines=lines)) and not te.has_day_delays({"causal_lines": lines})
    assert ce.needs_elapsed(_on(causal_lines=lines))  # 树声明的 delay_days 也要索要 elapsed_days
    assert not ce.needs_elapsed(_on(causal_lines=[_line("econ", _b("t", "active", effects_if_active=[_eff()]))]))


def test_summarize_open_filters_tree_items():
    st = _on(causal_pending=[{"pending_id": "a", "edge_id": "tree:x/y->z", "source": "tree"},
                             {"pending_id": "b", "edge_id": "e1"}])
    assert [p["pending_id"] for p in te.summarize_open(st)] == ["a"]
    assert te.summarize_open({"causal_pending": st["causal_pending"]}) == []


# ── 与因果引擎的衔接（提示词/处置/统计）──────────────────────────────


def test_due_hint_names_branch_and_dispositions_close_tree_items():
    entry = {"pending_id": "tree:econ/t->industry@1", "edge_id": "tree:econ/t->industry", "source": "tree",
             "branch_id": "t", "from_line_id": "econ", "to_line_id": "industry", "mechanism": "m",
             "sign": "positive", "strength": "high", "note": "", "triggered_at_step": 1,
             "trigger_reason": "tree_effect", "delay_days": None, "delay_steps": 0,
             "retrigger_count": 0, "postponed_count": 0, "ignored_count": 0}
    st = _on(causal_pending=[entry])
    hint = ce.build_hint(st, [], 2)
    assert "econ/t → industry" in hint and "未来树分支被激活" in hint
    pend, audit, viol = ce.apply_dispositions(
        st, [entry], [{"pending_id": entry["pending_id"], "disposition": "realized"}], history=[], step=2)
    assert pend == [] and audit[0]["disposition"] == "realized" and viol == []
    assert ce.kb_outcomes(st, audit) == []  # 树声明不回写知识库


# ── advance() 端到端 ────────────────────────────────────────────────


def _sim(tmp_path, settings):
    data_dir = tmp_path / "data"
    return data_dir, _make_sim(data_dir, settings)


def _settings(*lines, **extra):
    s = {"causal_lines": list(lines)}
    s.update(extra)
    return s


def test_advance_activation_queues_then_next_step_hints_and_closes(tmp_path, monkeypatch):
    line = _line("econ", _b("t", effects_if_active=[_eff("industry", mechanism="补贴拉动")]), )
    data_dir, store = _sim(tmp_path, _settings(line, _line("industry"), **_on()))
    cap: dict = {}
    s1 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, tree_updates=[_tu("econ", "t", "active")]), cap)
    assert "树影响世界已开启" in cap["inputs_list"][0]["tree_effects_hint"]
    assert [q["edge_id"] for q in s1.causal_queued] == ["tree:econ/t->industry"]
    persisted = store.load_history("main")[-1]
    assert [p["pending_id"] for p in persisted.dynamic_snapshot["causal_pending"]] == ["tree:econ/t->industry@1"]
    cap2: dict = {}
    s2 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, effect_dispositions=[
        {"pending_id": "tree:econ/t->industry@1", "disposition": "realized", "reason": "ok"}]), cap2)
    assert "econ/t → industry" in cap2["inputs_list"][0]["causal_pending_hint"]
    assert [d["disposition"] for d in s2.effect_dispositions] == ["realized"]
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert not manifest.settings.get("causal_pending")
    hist = store.load_history("main")
    assert ce.edge_stats(hist)["tree:econ/t->industry"]["realized"] == 1


def test_advance_switch_off_is_byte_identical(tmp_path, monkeypatch):
    line = _line("econ", _b("t", effects_if_active=[_eff()]))
    for extra in ({}, {"tree_effects_enabled": True}, {"causal_engine_enabled": True}):
        data_dir, _store = _sim(tmp_path / str(len(extra)) / "x", _settings(line, **extra))
        cap: dict = {}
        st = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, tree_updates=[_tu("econ", "t", "active")]), cap)
        assert cap["inputs_list"][0]["tree_effects_hint"] == ""
        assert st.causal_queued == [] and "causal_queued" not in st.to_dict()
        manifest, _c, _h = get_simulation(data_dir, "sim1")
        assert not manifest.settings.get("causal_pending")


def test_advance_works_without_tree_grounding_and_with_auto_activation(tmp_path, monkeypatch):
    line = _line("econ", _b("t", trigger_condition={"var": "age", "op": ">=", "value": 25},
                            effects_if_active=[_eff("industry", delay_days=60)]))
    data_dir, _store = _sim(tmp_path, _settings(
        line, _line("industry"), **_on(tree_grounding_enabled=True, tree_auto_transition=True)))
    s1 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, next_vars={"age": 30}, elapsed_days=30))
    assert [q["edge_id"] for q in s1.causal_queued if q["action"] == "queued"] == ["tree:econ/t->industry"]
    assert s1.elapsed_days == 30  # 树声明了 delay_days → 索要并落盘 elapsed_days
    cap: dict = {}
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, elapsed_days=20), cap)  # 提示词只看到第 2 步之前的跨度，未到期
    assert cap["inputs_list"][0]["causal_pending_hint"].count("tree:econ") == 0
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(3, elapsed_days=50))
    cap4: dict = {}  # 第 4 步的提示词看到第 2、3 步累计 70 天 ≥ 60（触发步自己的跨度不算）
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(4, elapsed_days=10), cap4)
    assert "tree:econ/t->industry@1" in cap4["inputs_list"][0]["causal_pending_hint"]


def test_advance_grounding_downgrade_prevents_queueing(tmp_path, monkeypatch):
    line = _line("econ", _b("p"), _b("t", prerequisites=["p"], effects_if_active=[_eff()]))
    data_dir, _store = _sim(tmp_path, _settings(line, **_on(tree_grounding_enabled=True)))
    s1 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, tree_updates=[_tu("econ", "t", "active")]))
    assert [e["code"] for e in s1.tree_grounding] == ["G1"] and s1.causal_queued == []
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert _st(manifest.settings["causal_lines"], "econ", "t") == "emerging"


def test_effects_alone_does_not_turn_on_grounding(tmp_path, monkeypatch):
    """只开树影响世界、没开接地：前置未满足的分支照样 active（接地不运行），也照样入队。"""
    line = _line("econ", _b("p"), _b("t", prerequisites=["p"], effects_if_active=[_eff()]))
    data_dir, _store = _sim(tmp_path, _settings(line, **_on()))
    s1 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, tree_updates=[_tu("econ", "t", "active")]))
    assert s1.tree_grounding == []
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert _st(manifest.settings["causal_lines"], "econ", "t") == "active"
    assert [q["edge_id"] for q in s1.causal_queued] == ["tree:econ/t->industry"]


def test_advance_error_in_queue_does_not_break_step(tmp_path, monkeypatch):
    line = _line("econ", _b("t", effects_if_active=[_eff()]))
    data_dir, _store = _sim(tmp_path, _settings(line, **_on()))
    monkeypatch.setattr(te, "queue_tree_effects", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    s1 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, tree_updates=[_tu("econ", "t", "active")]))
    assert s1.step == 1 and [v["code"] for v in s1.causal_violations] == ["E0"]


def test_pending_tree_effects_are_branch_scoped(tmp_path, monkeypatch):
    """主线第 1 步激活并入队、第 2 步交代结案；从第 1 步分叉的新分支回到\"刚入队\"的状态，
    在新分支上推进不污染主线；切回主线仍是已结案。"""
    from world_simulator import branch_manager as bm

    line = _line("econ", _b("t", effects_if_active=[_eff()]))
    data_dir, store = _sim(tmp_path, _settings(line, **_on()))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, tree_updates=[_tu("econ", "t", "active")]))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, effect_dispositions=[
        {"pending_id": "tree:econ/t->industry@1", "disposition": "realized"}]))
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert not manifest.settings.get("causal_pending")
    alt = bm.fork_branch(data_dir, "sim1", from_step=1)
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.branch == alt
    assert [p["pending_id"] for p in manifest.settings["causal_pending"]] == ["tree:econ/t->industry@1"]
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, effect_dispositions=[
        {"pending_id": "tree:econ/t->industry@1", "disposition": "countered", "reason": "政策取消"}]))
    bm.switch_branch(data_dir, "sim1", "main")
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert not manifest.settings.get("causal_pending")
    assert ce.edge_stats(store.load_history("main"))["tree:econ/t->industry"]["realized"] == 1
    assert ce.edge_stats(store.load_history(alt))["tree:econ/t->industry"]["countered"] == 1


# ── 文档/模板接线 ───────────────────────────────────────────────────


def test_placeholder_present_in_both_workflows_and_skills_document_the_field():
    root = Path(__file__).resolve().parent.parent
    for wf in ("advance_step.yaml", "world_evolve.yaml"):
        assert "{tree_effects_hint}" in (root / "workflows" / wf).read_text(encoding="utf-8")
    for t in ("life-sim", "group-evolution", "negotiation"):
        text = (root / "skills" / f"{t}-template" / "SKILL.md").read_text(encoding="utf-8")
        assert "effects_if_active" in text and "树影响世界已开启" in text
