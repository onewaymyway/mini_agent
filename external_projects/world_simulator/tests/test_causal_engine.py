"""tests/test_causal_engine.py — WP3 P5a（第二十二轮 / 阶段 P5a）：因果边升级 + 待兑现因果队列。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §4 WP3 的 3a + 3b。
引擎只做"入队 → 到期提醒 → 校验 LLM 回报 → 统计"，不改任何数值；这里逐条验证：
边规整（旧格式兼容）、四种触发源、同边去重、延迟（天数/按步降级）、处置校验 E1–E4/E6、
自动结案、兑现统计，以及 `advance()` 端到端接入、分支隔离、开关关闭时与今天完全一致。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import branch_manager as bm
from world_simulator import causal_engine as ce
from world_simulator.engine.management import get_simulation
from world_simulator.state_model import SimState

from test_fast_forward import _default_payload
from test_tech_model import _adv, _make_sim, _node


def _edge(src="econ", dst="industry", **extra):
    e = {"from_line_id": src, "to_line_id": dst}
    e.update(extra)
    return e


def _settings(*edges, **extra):
    s = {"causal_engine_enabled": True, "declared_causal_graph": list(edges)}
    s.update(extra)
    return s


def _state(step, *, line_updates=None, tree_updates=None, sampled_events=None, tech_updates=None,
           elapsed=None, vars_=None):
    return SimState(step=step, summary="x", vars=vars_ or {}, line_updates=line_updates or {},
                    tree_updates=tree_updates or [], sampled_events=sampled_events or [],
                    tech_updates=tech_updates or [], elapsed_days=elapsed)


def _queue(settings, pending, state):
    return ce.queue_effects(settings, pending, state)


def _codes(violations):
    return [v["code"] for v in violations]


def _pending_entry(edge_id="econ->industry", step=1, **extra):
    p = {"pending_id": f"{edge_id}@{step}", "edge_id": edge_id, "from_line_id": "econ",
         "to_line_id": "industry", "triggered_at_step": step, "delay_days": None, "delay_steps": 0,
         "retrigger_count": 0, "postponed_count": 0, "ignored_count": 0}
    p.update(extra)
    return p


# ── 开关 / 规整（3a）────────────────────────────────────────────────


def test_disabled_is_a_noop_everywhere():
    s = {"declared_causal_graph": [_edge()], "causal_pending": [_pending_entry()]}
    assert not ce.is_enabled(s) and not ce.is_enabled(None)
    assert ce.build_hint(s, [], 5) == "" and ce.safe_build_hint(None, [], 1) == ""
    assert not ce.needs_elapsed(s)
    assert ce.summarize_open(s, [], 5) == []


def test_old_format_edge_still_works_and_new_fields_default_to_undeclared():
    edge, why = ce.normalize_edge({"from_line_id": "a", "to_line_id": "b", "note": "旧格式"})
    assert why is None and edge["id"] == "a->b" and edge["note"] == "旧格式"
    assert edge["sign"] is None and edge["strength"] is None and edge["confidence"] is None
    assert edge["delay_days"] is None and edge["delay_steps"] == 0 and edge["enabled"] is True


def test_new_fields_normalize_and_unknown_values_become_undeclared():
    edge, _ = ce.normalize_edge(_edge(id="e1", mechanism="降息→投资", sign="+", strength="HIGH",
                                      delay_days=90, confidence="low", condition={"var": "x", "op": ">", "value": 1}))
    assert edge["id"] == "e1" and edge["sign"] == "positive" and edge["strength"] == "high"
    assert edge["delay_days"] == 90.0 and edge["confidence"] == "low" and edge["condition"]
    bad, _ = ce.normalize_edge(_edge(sign="sideways", strength="huge", delay_days=-5, confidence="?",
                                     condition="nope"))
    assert bad["sign"] is None and bad["strength"] is None and bad["delay_days"] is None
    assert bad["confidence"] is None and bad["condition"] is None


def test_invalid_edges_are_dropped_with_reason_and_duplicates_keep_first():
    edges, problems = ce.get_edges(_settings(
        _edge("a", "b"), {"from_line_id": "a"}, _edge("c", "c"), "x", _edge("a", "b", note="dup"),
    ))
    assert [e["id"] for e in edges] == ["a->b"] and edges[0]["note"] == ""
    assert len(problems) == 4
    assert any("重复" in p for p in problems)


# ── 触发源（3b）──────────────────────────────────────────────────────


def test_trigger_line_advanced_and_explicit_false_does_not_trigger():
    s = _settings(_edge())
    pending, queued, viol = _queue(s, [], _state(1, line_updates={"econ": {"summary": "x"}}))
    assert [p["pending_id"] for p in pending] == ["econ->industry@1"] and viol == []
    assert queued == [{"pending_id": "econ->industry@1", "edge_id": "econ->industry",
                       "action": "queued", "trigger_reason": "line_advanced"}]
    none, q2, _ = _queue(s, [], _state(1, line_updates={"econ": {"advanced": False}}))
    assert none == [] and q2 == []
    # 目标线有进展不触发（方向性）
    none2, _q, _v = _queue(s, [], _state(1, line_updates={"industry": {"summary": "x"}}))
    assert none2 == []


def test_trigger_tree_branch_confirmed_or_activated():
    s = _settings(_edge())
    p1, q1, _ = _queue(s, [], _state(1, tree_updates=[{"line_id": "econ", "confirmed_branch": "b1"}]))
    assert q1[0]["trigger_reason"] == "tree_branch" and len(p1) == 1
    p2, q2, _ = _queue(s, [], _state(1, tree_updates=[
        {"line_id": "econ", "confirmed_branch": "", "status_updates": [{"branch_id": "b", "status": "active"}]}]))
    assert q2[0]["trigger_reason"] == "tree_branch"
    p3, _q, _ = _queue(s, [], _state(1, tree_updates=[
        {"line_id": "econ", "status_updates": [{"branch_id": "b", "status": "dormant"}]}]))
    assert p3 == []
    p4, _q, _ = _queue(s, [], _state(1, tree_updates=[{"line_id": "other", "confirmed_branch": "b1"}]))
    assert p4 == []


def test_trigger_sampled_event_affects_unless_suppressed():
    s = _settings(_edge())
    ev = {"id": "crash", "affects": ["econ"]}
    p, q, _ = _queue(s, [], _state(1, sampled_events=[ev]))
    assert q[0]["trigger_reason"] == "sampled_event" and len(p) == 1
    p2, _q, _ = _queue(s, [], _state(1, sampled_events=[{**ev, "suppressed_by_cap": True}]))
    assert p2 == []


def test_trigger_tech_transition_only_not_held():
    s = _settings(_edge("chip", "industry"))
    p, q, _ = _queue(s, [], _state(1, tech_updates=[{"action": "transition", "tech_id": "chip"}]))
    assert q[0]["trigger_reason"] == "tech_transition" and len(p) == 1
    p2, _q, _ = _queue(s, [], _state(1, tech_updates=[{"action": "held", "tech_id": "chip"}]))
    assert p2 == []


def test_same_edge_retrigger_accumulates_instead_of_duplicating():
    s = _settings(_edge())
    st = _state(1, line_updates={"econ": {}})
    p1, _q, _ = _queue(s, [], st)
    p2, q2, _ = _queue(s, p1, _state(2, line_updates={"econ": {}}))
    p3, _q3, _ = _queue(s, p2, _state(3, line_updates={"econ": {}}))
    assert len(p3) == 1 and p3[0]["pending_id"] == "econ->industry@1" and p3[0]["retrigger_count"] == 2
    assert q2[0]["action"] == "retriggered"


def test_edge_condition_disabled_edge_and_queue_cap():
    cond = {"var": "gdp", "op": ">=", "value": 10}
    s = _settings(_edge(condition=cond), _edge("a", "b", enabled=False))
    st_lo = _state(1, line_updates={"econ": {}, "a": {}}, vars_={"gdp": 5})
    assert _queue(s, [], st_lo)[0] == []
    st_hi = _state(1, line_updates={"econ": {}, "a": {}}, vars_={"gdp": 15})
    assert [p["edge_id"] for p in _queue(s, [], st_hi)[0]] == ["econ->industry"]
    cap = _settings(_edge("a", "b"), _edge("c", "d"), causal_params={"max_pending": 1})
    pend, _q, viol = _queue(cap, [], _state(1, line_updates={"a": {}, "c": {}}))
    assert len(pend) == 1 and _codes(viol) == ["E6"]


# ── 延迟 / 到期 ──────────────────────────────────────────────────────


def _hist(*pairs):
    return [SimState(step=s, summary="x", elapsed_days=d) for s, d in pairs]


def test_zero_delay_is_due_at_the_step_right_after_the_trigger():
    """触发发生在第 2 步（已落盘）；下一次生成的是第 3 步，延迟 0 → 此时就到期。
    延迟 1 步 → 要等到生成第 4 步。"""
    h = _hist((0, None), (1, 30), (2, 30))
    assert ce.due_entries([_pending_entry(step=2)], h, 3)
    p1 = _pending_entry(step=2, delay_steps=1)
    assert not ce.due_entries([p1], h, 3)
    assert ce.due_entries([p1], h + _hist((3, 30)), 4)


def test_delay_days_uses_cumulative_elapsed_and_marks_precision():
    p = _pending_entry(step=1, delay_days=90.0)
    h = _hist((1, 30), (2, 40))
    assert ce.due_entries([p], h, 3) == []  # 40 < 90
    h2 = _hist((1, 30), (2, 40), (3, 60))
    due = ce.due_entries([p], h2, 4)
    assert due and due[0]["precision"] == "days" and due[0]["elapsed_days_since"] == 100.0


def test_delay_days_degrades_to_step_count_when_elapsed_missing():
    p = _pending_entry(step=1, delay_days=1000.0)
    h = _hist((1, None), (2, None))  # 触发后第 2 步缺 elapsed_days
    due = ce.due_entries([p], h, 3)
    assert due and due[0]["precision"] == "steps" and due[0]["elapsed_days_since"] is None
    # 触发后一步都没过 → 区间为空（完整），按天数算：0 < 1000，还没到
    assert ce.due_entries([p], _hist((1, None)), 2) == []
    # 降级后若同时声明了 delay_steps，则按它等待，而不是缺数据就立刻到期
    p3 = _pending_entry(step=1, delay_days=1000.0, delay_steps=3)
    assert ce.due_entries([p3], _hist((1, None), (2, None)), 3) == []
    assert ce.due_entries([p3], _hist((1, None), (2, None), (3, None), (4, None)), 5)


def test_delay_steps_legacy_still_counts_steps():
    p = _pending_entry(step=1, delay_steps=2)
    assert ce.due_entries([p], _hist((1, None), (2, None)), 3) == []
    assert ce.due_entries([p], _hist((1, None), (2, None), (3, None)), 4)


def test_needs_elapsed_only_when_a_declared_edge_has_delay_days():
    assert not ce.needs_elapsed(_settings(_edge()))
    assert ce.needs_elapsed(_settings(_edge(delay_days=30)))
    assert not ce.needs_elapsed(_settings(_edge(delay_days=30, enabled=False)))


# ── 处置校验 ─────────────────────────────────────────────────────────


def _apply(pending, raw, *, step=3, settings=None, history=None):
    return ce.apply_dispositions(settings or _settings(_edge()), pending, raw,
                                 history=history if history is not None else _hist((1, 30), (2, 30)), step=step)


def test_realized_closes_and_dampened_needs_reason():
    p = _pending_entry(step=1)
    pend, audit, viol = _apply([p], [{"pending_id": p["pending_id"], "disposition": "realized"}])
    assert pend == [] and viol == [] and audit[0]["disposition"] == "realized" and audit[0]["closed"] is True
    pend, audit, viol = _apply([p], [{"pending_id": p["pending_id"], "disposition": "dampened"}])
    assert _codes(viol) == ["E4"] and audit == []
    assert len(pend) == 1 and pend[0]["ignored_count"] == 1  # 被忽略=等同没交代


def test_dampened_and_countered_with_reason_close():
    for disp in ("dampened", "countered"):
        p = _pending_entry(step=1)
        pend, audit, viol = _apply([p], [{"pending_id": p["pending_id"], "disposition": disp, "reason": "政策对冲"}])
        assert pend == [] and viol == [] and audit[0]["reason"] == "政策对冲"


def test_invalid_reference_and_disposition_are_violations_not_crashes():
    p = _pending_entry(step=1)
    pend, audit, viol = _apply([p], [
        {"pending_id": "ghost@9", "disposition": "realized"},
        {"pending_id": p["pending_id"], "disposition": "maybe"},
        "not a dict",
    ])
    assert sorted(_codes(viol)) == ["E1", "E1", "E2"]
    assert len(pend) == 1  # 没被处置


def test_not_yet_due_disposition_is_rejected_E3():
    p = _pending_entry(step=1, delay_days=500.0)
    pend, audit, viol = _apply([p], [{"pending_id": p["pending_id"], "disposition": "realized"}])
    assert _codes(viol) == ["E3"] and len(pend) == 1 and audit == []
    assert pend[0]["ignored_count"] == 0  # 没到期，不算"被忽略"


def test_edge_id_fallback_when_single_open_entry():
    p = _pending_entry(step=1)
    pend, audit, viol = _apply([p], [{"pending_id": "econ->industry", "disposition": "realized"}])
    assert pend == [] and viol == [] and audit[0]["pending_id"] == p["pending_id"]


def test_duplicate_report_for_same_item_uses_first():
    p = _pending_entry(step=1)
    raw = [{"pending_id": p["pending_id"], "disposition": "realized"},
           {"pending_id": p["pending_id"], "disposition": "countered", "reason": "x"}]
    _pend, audit, _v = _apply([p], raw)
    assert [a["disposition"] for a in audit] == ["realized"]


def test_postponed_stays_open_then_expires_after_max_postpone():
    s = _settings(_edge(), causal_params={"max_postpone": 2})
    p = _pending_entry(step=1)
    raw = lambda: [{"pending_id": p["pending_id"], "disposition": "postponed", "reason": "等政策"}]  # noqa: E731
    for n in (1, 2):
        pend, audit, _v = _apply([p], raw(), settings=s)
        assert len(pend) == 1 and audit[0]["closed"] is False
        p = pend[0]
        assert p["postponed_count"] == n
    pend, audit, _v = _apply([p], raw(), settings=s)
    assert pend == [] and audit[0]["disposition"] == "expired" and audit[0]["auto"] is True


def test_ignored_due_entry_is_auto_closed_unaddressed_after_limit():
    s = _settings(_edge(), causal_params={"max_ignored": 2})
    p = _pending_entry(step=1)
    pend, audit, _v = _apply([p], None, settings=s)
    assert len(pend) == 1 and pend[0]["ignored_count"] == 1 and audit == []
    pend, audit, _v = _apply(pend, None, settings=s)
    assert pend == [] and audit[0]["disposition"] == "unaddressed" and audit[0]["auto"] is True


def test_dispositions_do_not_touch_items_queued_after_them():
    """顺序保证：先处置再入队——本步新入队的项 id 里的步号 = 本步，不在 pending 里被处置。"""
    s = _settings(_edge())
    old = _pending_entry(step=1)
    pend, audit, _v = ce.apply_dispositions(
        s, [old], [{"pending_id": "econ->industry@3", "disposition": "realized"}],
        history=_hist((1, 30), (2, 30)), step=3)
    assert _codes(_v) == ["E1"]  # 本步才入队的项，此时还不存在


# ── 统计 ─────────────────────────────────────────────────────────────


def test_edge_stats_counts_and_rate_from_history():
    def st(step, queued=None, disp=None):
        return SimState(step=step, summary="x", causal_queued=queued or [], effect_dispositions=disp or [])
    hist = [
        st(1, queued=[{"edge_id": "e", "action": "queued"}, {"edge_id": "e", "action": "retriggered"}]),
        st(2, disp=[{"edge_id": "e", "disposition": "realized", "closed": True}]),
        st(3, queued=[{"edge_id": "e", "action": "queued"}],
           disp=[{"edge_id": "e", "disposition": "countered", "closed": True}]),
        st(4, disp=[{"edge_id": "e", "disposition": "unaddressed", "closed": True, "auto": True},
                    {"edge_id": "e", "disposition": "postponed", "closed": False}]),
    ]
    s = ce.edge_stats(hist)["e"]
    assert s["queued"] == 2  # retriggered 不计
    assert s["realized"] == 1 and s["countered"] == 1 and s["unaddressed"] == 1 and s["postponed"] == 1
    assert s["concluded"] == 2 and s["realized_rate"] == 0.5  # 自动结案/推迟不进分母
    assert ce.edge_stats([]) == {}
    assert ce.edge_stats([SimState(step=1, summary="x")]) == {}


# ── 提示词 ───────────────────────────────────────────────────────────


def test_hint_empty_without_due_items_and_contains_protocol_when_due():
    s = _settings(_edge(mechanism="降息带动投资", sign="+", strength="high"))
    assert ce.build_hint(s, [], 2) == ""  # 没有 pending
    s["causal_pending"] = [_pending_entry(step=1, mechanism="降息带动投资", sign="positive", strength="high",
                                          trigger_reason="line_advanced", retrigger_count=2)]
    hint = ce.build_hint(s, _hist((1, 30)), 2)
    assert "[econ->industry@1]" in hint and "降息带动投资" in hint and "正向" in hint and "强" in hint
    assert "effect_dispositions" in hint and "必须给 reason" in hint and "又出现进展 2 次" in hint
    s["causal_pending"][0]["delay_days"] = 999.0
    assert ce.build_hint(s, _hist((1, 30)), 2) == ""  # 未到期


def test_hint_asks_elapsed_days_only_when_needed_and_not_already_asked():
    s = _settings(_edge(delay_days=30))
    assert "elapsed_days" in ce.build_hint(s, [], 1)
    assert ce.build_hint({**s, "tech_model_enabled": True}, [], 1) == ""  # 技术模型提示已索要
    assert ce.build_hint({**s, "event_sampling_enabled": True}, [], 1) == ""
    s2 = _settings(_edge())
    assert ce.build_hint(s2, [], 1) == ""


def test_hint_shows_history_rate_only_after_enough_conclusions():
    s = _settings(_edge())
    s["causal_pending"] = [_pending_entry(step=5)]
    hist = [SimState(step=i, summary="x", effect_dispositions=[
        {"edge_id": "econ->industry", "disposition": "realized" if i < 3 else "countered", "closed": True}])
        for i in range(1, 5)]
    assert "本世界历史：4 次有结论中兑现 2 次" in ce.build_hint(s, hist, 6)
    assert "本世界历史" not in ce.build_hint(s, hist[:2], 6)


def test_summarize_open_marks_due():
    s = _settings(_edge())
    s["causal_pending"] = [_pending_entry(step=1), _pending_entry("b->c", step=1, delay_days=500.0)]
    rows = ce.summarize_open(s, _hist((1, 30)), 2)
    assert {r["edge_id"]: r["due"] for r in rows} == {"econ->industry": True, "b->c": False}


# ── 兜底 ─────────────────────────────────────────────────────────────


def test_safe_wrappers_swallow_errors_and_report_E0(monkeypatch):
    monkeypatch.setattr(ce, "queue_effects", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    pend, queued, viol = ce.safe_queue_effects({}, [{"pending_id": "k"}], _state(1))
    assert pend == [{"pending_id": "k"}] and queued == [] and _codes(viol) == ["E0"]
    monkeypatch.setattr(ce, "apply_dispositions", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    pend, audit, viol = ce.safe_apply_dispositions({}, [{"pending_id": "k"}], [], history=[], step=1)
    assert pend == [{"pending_id": "k"}] and audit == [] and _codes(viol) == ["E0"]
    monkeypatch.setattr(ce, "build_hint", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert ce.safe_build_hint({}, [], 1) == ""


# ── SimState 序列化 ─────────────────────────────────────────────────


def test_simstate_omits_empty_fields_and_roundtrips_filled_ones():
    d = SimState(step=1, summary="x").to_dict()
    for key in ("causal_queued", "effect_dispositions", "causal_violations"):
        assert key not in d
    st = SimState(step=2, summary="x", causal_queued=[{"pending_id": "a@2"}],
                  effect_dispositions=[{"pending_id": "a@1", "disposition": "realized"}],
                  causal_violations=[{"code": "E1"}])
    back = SimState.from_dict(json.loads(json.dumps(st.to_dict())))
    assert back.causal_queued == st.causal_queued and back.effect_dispositions == st.effect_dispositions
    assert back.causal_violations == st.causal_violations
    assert SimState.from_dict({"step": 1}).causal_queued == []  # 旧数据


# ── advance() 端到端 ────────────────────────────────────────────────


def _sim(tmp_path, settings):
    data_dir = tmp_path / "data"
    return data_dir, _make_sim(data_dir, settings)


def test_advance_queues_then_hints_then_closes_across_steps(tmp_path, monkeypatch):
    data_dir, store = _sim(tmp_path, _settings(_edge(mechanism="降息带动投资")))
    cap: dict = {}
    s1 = _adv(monkeypatch, tmp_path, data_dir,
              _default_payload(1, line_updates={"econ": {"summary": "降息", "time_label": "t1"}}), cap)
    assert cap["inputs_list"][0]["causal_pending_hint"] == ""  # 第 1 步还没有待兑现项
    assert [q["action"] for q in s1.causal_queued] == ["queued"] and s1.effect_dispositions == []
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert [p["pending_id"] for p in manifest.settings["causal_pending"]] == ["econ->industry@1"]
    assert store.load_history("main")[-1].dynamic_snapshot["causal_pending"][0]["edge_id"] == "econ->industry"

    cap2: dict = {}
    s2 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        2, effect_dispositions=[{"pending_id": "econ->industry@1", "disposition": "realized"}]), cap2)
    assert "[econ->industry@1]" in cap2["inputs_list"][0]["causal_pending_hint"]  # 下一步到期并提醒
    assert s2.effect_dispositions[0]["disposition"] == "realized" and s2.causal_violations == []
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.settings["causal_pending"] == []
    hist = store.load_history("main")
    assert ce.edge_stats(hist)["econ->industry"]["realized_rate"] == 1.0


def test_advance_same_step_source_progress_is_not_consumed_by_same_step_report(tmp_path, monkeypatch):
    data_dir, _store = _sim(tmp_path, _settings(_edge()))
    s1 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        1, line_updates={"econ": {}},
        effect_dispositions=[{"pending_id": "econ->industry@1", "disposition": "realized"}]))
    assert _codes(s1.causal_violations) == ["E1"]  # 引用的是本步才入队的项 → 不存在
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert len(manifest.settings["causal_pending"]) == 1  # 入队没被吃掉


def test_advance_records_violation_for_dampened_without_reason(tmp_path, monkeypatch):
    data_dir, _store = _sim(tmp_path, _settings(_edge()))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, line_updates={"econ": {}}))
    s2 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        2, effect_dispositions=[{"pending_id": "econ->industry@1", "disposition": "dampened"}]))
    assert _codes(s2.causal_violations) == ["E4"] and s2.effect_dispositions == []
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.settings["causal_pending"][0]["ignored_count"] == 1


def test_advance_delay_days_needs_elapsed_and_records_it(tmp_path, monkeypatch):
    """延迟 90 天：第 1 步触发后，累计的是**之后各步**的 elapsed_days（不含触发那一步）。"""
    data_dir, _store = _sim(tmp_path, _settings(_edge(delay_days=90)))
    cap: dict = {}
    s1 = _adv(monkeypatch, tmp_path, data_dir,
              _default_payload(1, elapsed_days=30, line_updates={"econ": {}}), cap)
    assert "elapsed_days" in cap["inputs_list"][0]["causal_pending_hint"]  # 只声明天数延迟也要索要
    assert s1.elapsed_days == 30.0  # 仅开因果引擎也落盘 elapsed_days
    due_marker = "[econ->industry@1]"
    cap2: dict = {}
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, elapsed_days=30), cap2)
    assert due_marker not in cap2["inputs_list"][0]["causal_pending_hint"]  # 生成第 2 步：累计 0
    cap3: dict = {}
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(3, elapsed_days=70), cap3)
    assert due_marker not in cap3["inputs_list"][0]["causal_pending_hint"]  # 生成第 3 步：累计 30 < 90
    cap4: dict = {}
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(4, elapsed_days=10), cap4)
    hint4 = cap4["inputs_list"][0]["causal_pending_hint"]
    assert due_marker in hint4 and "约 100 天" in hint4  # 生成第 4 步：30 + 70 = 100 ≥ 90


def test_advance_switch_off_is_identical_to_before(tmp_path, monkeypatch):
    data_dir, store = _sim(tmp_path, {"declared_causal_graph": [_edge()],
                                      "causal_pending": [_pending_entry()]})  # 有数据但开关未开
    cap: dict = {}
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        1, line_updates={"econ": {}}, effect_dispositions=[{"pending_id": "x", "disposition": "realized"}]), cap)
    assert cap["inputs_list"][0]["causal_pending_hint"] == ""
    assert state.causal_queued == [] and state.effect_dispositions == [] and state.causal_violations == []
    d = store.load_history("main")[-1].to_dict()
    for key in ("causal_queued", "effect_dispositions", "causal_violations", "elapsed_days"):
        assert key not in d
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.settings["causal_pending"] == [_pending_entry()]  # 未开启时不读也不改


def test_advance_engine_failure_does_not_break_the_step(tmp_path, monkeypatch):
    data_dir, _store = _sim(tmp_path, _settings(_edge()))
    monkeypatch.setattr(ce, "_source_reason", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, line_updates={"econ": {}}))
    assert state.step == 1 and _codes(state.causal_violations) == ["E0"] and state.causal_queued == []


def test_hint_placeholder_present_in_both_workflows_and_skills_document_the_field():
    root = Path(__file__).resolve().parent.parent
    for name in ("advance_step.yaml", "world_evolve.yaml"):
        text = (root / "workflows" / name).read_text(encoding="utf-8")
        assert "{causal_pending_hint}" in text and "effect_dispositions" in text, name
    for tpl in ("life-sim", "group-evolution", "negotiation"):
        text = (root / "skills" / f"{tpl}-template" / "SKILL.md").read_text(encoding="utf-8")
        assert "effect_dispositions" in text and "因果引擎已开启" in text, tpl


# ── 分支隔离（WP0 × P5a）────────────────────────────────────────────


def test_fork_rolls_pending_queue_back_and_branches_do_not_pollute_each_other(tmp_path, monkeypatch):
    data_dir, _store = _sim(tmp_path, _settings(_edge()))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, line_updates={"econ": {}}))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        2, effect_dispositions=[{"pending_id": "econ->industry@1", "disposition": "realized"}]))

    def _ids():
        manifest, _c, _h = get_simulation(data_dir, "sim1")
        return [p["pending_id"] for p in manifest.settings.get("causal_pending") or []]

    assert _ids() == []
    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1)
    assert _ids() == ["econ->industry@1"]  # 回滚到分叉那一刻：此时项还没结案
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        2, effect_dispositions=[{"pending_id": "econ->industry@1", "disposition": "countered", "reason": "政策"}]))
    assert _ids() == []
    bm.switch_branch(data_dir, "sim1", "main")
    assert _ids() == []  # 主线此时也已结案，但是各自独立结案的
    store = _store
    main_disp = store.load_history("main")[-1].effect_dispositions
    new_disp = store.load_history(new_branch)[-1].effect_dispositions
    assert main_disp[0]["disposition"] == "realized" and new_disp[0]["disposition"] == "countered"
    assert ce.edge_stats(store.load_history("main"))["econ->industry"]["realized"] == 1
    assert ce.edge_stats(store.load_history(new_branch))["econ->industry"]["countered"] == 1


# ── 界面（app.py）────────────────────────────────────────────────────


def test_ui_violations_html_escapes_and_handles_old_state():
    import app

    st_ = SimpleNamespace(causal_violations=[{"code": "E4", "severity": "warn", "message": "<b>缺 reason</b>"}])
    out = app._causal_violations_html(st_)
    assert "&lt;b&gt;缺 reason&lt;/b&gt;" in out and "<b>缺 reason" not in out and "E4" in out
    assert app._causal_violations_html(SimpleNamespace(causal_violations=[])) == ""
    assert app._causal_violations_html(SimpleNamespace()) == ""
