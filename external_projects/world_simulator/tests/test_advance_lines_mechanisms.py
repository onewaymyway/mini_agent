"""tests/test_advance_lines_mechanisms.py — 第二十二轮 P8：独立推进路径 `advance_lines()` 接入新机制。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §10 P8，
以及用户对三个设计点的确认：① 全局步的 `elapsed_days` = 到点各线申报跨度的**最大值**；
② P8 一次做完；③ 事件投放：`affects` 与线 id/owned_vars 有交集的投给对应线，`affects` 为空的投给所有到点线。

用桩 LLM（monkeypatch `mini_agent.workflow.{store,runner}`），不真的调用模型，验证：
引擎传给每条线的提示词输入、线的输出如何合并、机制链是否按 `advance()` 的顺序与规则裁决，
以及机制全关时与 P8 之前一致。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import world_simulator.engine as engine_mod
from world_simulator import tech_model as tm
from world_simulator.engine import mechanisms
from world_simulator.engine.advance import advance
from world_simulator.state_model import SimManifest, SimState
from world_simulator.store import SimStore, now_iso

from test_fast_forward import _default_payload, _patch_sequenced_advance_step_workflow

LINES = [
    {"id": "A", "label": "线A", "owned_vars": ["a"], "advance_every_n_steps": 1, "local_step": 0},
    {"id": "B", "label": "线B", "owned_vars": ["b"], "advance_every_n_steps": 1, "local_step": 0},
]


# ── 工具 ─────────────────────────────────────────────────────────────


class _Status:
    def __init__(self, value: str) -> None:
        self.value = value


class _Step:
    def __init__(self, step_id: str) -> None:
        self.id = step_id
        self.skill_name = None


class _WF:
    def __init__(self, steps):
        self.steps = steps


def _make_sim(data_dir: Path, *, settings: Dict[str, Any], lines=None, vars_=None, sim_id="sim1") -> SimStore:
    store = SimStore.for_root(data_dir, sim_id)
    ts = now_iso()
    full = dict(settings)
    if lines is not None:
        full["causal_lines"] = [dict(x) for x in lines]
        full["independent_line_advance"] = True
    store.save_manifest(SimManifest(sim_id=sim_id, template="life_sim", intent="i", title="t",
                                    created_at=ts, updated_at=ts, settings=full))
    store.append_state(SimState(step=0, summary="s0", vars=vars_ if vars_ is not None else {"a": 1, "b": 2},
                                options=[]))
    return store


def _install_runner(monkeypatch, tmp_path: Path, outputs: Dict[str, Any], seen: List[Dict[str, Any]]) -> None:
    """`outputs[line_id]` 是该线这一步的输出（dict）；`seen` 收集每次调用的 inputs。"""

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return _WF([_Step("line_evolve")])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            seen.append(dict(inputs))
            lid = inputs["line_id"]
            payload = {"summary": f"{lid} 推进", "narrative": f"{lid} 的叙事", "next_vars": {}}
            payload.update(outputs.get(lid, {}))
            p = tmp_path / f"line_{lid}_{len(seen)}.json"
            p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return SimpleNamespace(status="done", step_results=[
                SimpleNamespace(step_id="line_evolve", status=_Status("done"), result_file=str(p))])

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)


def _lines_step(tmp_path, data_dir, sim_id="sim1"):
    return engine_mod.advance_lines(cfg=object(), workspace_root=tmp_path / "ws", data_dir=data_dir, sim_id=sim_id)


def _hint(seen, line_id) -> str:
    return next(i for i in seen if i["line_id"] == line_id)["line_mechanism_hint"]


def _codes(violations) -> List[str]:
    return [v["code"] for v in violations]


def _tech_settings(**extra) -> Dict[str, Any]:
    s: Dict[str, Any] = {"tech_model_enabled": True, "tech_state": {"nodes": [{
        "id": "ai", "name": "ai", "stage": "lab", "progress": 0.0,
        "typical_dwell_days": {st: 100 for st in tm.STAGES}, "dwell_source": "user"}]}}
    s.update(extra)
    return s


def _event_settings(priors, **extra) -> Dict[str, Any]:
    s: Dict[str, Any] = {"event_sampling_enabled": True, "event_priors": priors}
    s.update(extra)
    return s


def _sure_event(eid, affects) -> Dict[str, Any]:
    # 年率极大 → 一步内几乎必然发生，让测试不依赖具体随机数
    return {"id": eid, "description": f"事件{eid}", "rate_per_year": 1e9, "affects": affects, "severity": "high"}


# ── 机制全关：与 P8 之前一致 ─────────────────────────────────────────


def test_mechanisms_off_hint_is_empty_and_state_has_no_new_fields(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, settings={}, lines=LINES)
    seen: List[Dict[str, Any]] = []
    _install_runner(monkeypatch, tmp_path, {"A": {"elapsed_days": 50}}, seen)
    state = _lines_step(tmp_path, data_dir)
    assert [i["line_mechanism_hint"] for i in seen] == ["", ""]
    assert state.elapsed_days is None  # 没有任何机制需要时间 → 线申报了也不记
    assert state.sampled_events == [] and state.tech_updates == [] and state.causal_queued == []
    assert "elapsed_days" not in state.line_updates["A"]


def test_mechanisms_off_does_not_run_post_llm_chain(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, settings={}, lines=LINES)
    _install_runner(monkeypatch, tmp_path, {}, [])
    called = []
    monkeypatch.setattr(mechanisms, "apply_post_llm", lambda *a, **k: called.append(1))
    _lines_step(tmp_path, data_dir)
    assert called == []  # 一致性守卫默认开，但它不算\"需要处理链\"的机制


def test_empty_tick_with_mechanisms_on_neither_samples_nor_calls(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    lines = [dict(LINES[0], local_step=1, advance_every_n_steps=3)]  # 1 % 3 != 0，不到点
    _make_sim(data_dir, settings={**_tech_settings(), **_event_settings([_sure_event("e", [])])}, lines=lines)
    seen: List[Dict[str, Any]] = []
    _install_runner(monkeypatch, tmp_path, {}, seen)
    state = _lines_step(tmp_path, data_dir)
    assert seen == [] and state.step == 1
    assert state.sampled_events == [] and state.elapsed_days is None and state.tech_updates == []


# ── 时间跨度：取各线申报的最大值 ─────────────────────────────────────


def test_global_elapsed_is_max_of_reported_line_spans(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, settings=_tech_settings(), lines=LINES)
    seen: List[Dict[str, Any]] = []
    _install_runner(monkeypatch, tmp_path, {"A": {"elapsed_days": 30}, "B": {"elapsed_days": 90}}, seen)
    state = _lines_step(tmp_path, data_dir)
    assert state.elapsed_days == 90.0
    assert state.line_updates["A"]["elapsed_days"] == 30.0
    assert state.line_updates["B"]["elapsed_days"] == 90.0
    time_entry = next(a for a in state.tech_updates if a["action"] == "time")
    assert time_entry["elapsed_days"] == 90.0 and time_entry["elapsed_source"] == "reported"
    assert "elapsed_days" in _hint(seen, "A")  # 提示词里要了这个字段


@pytest.mark.parametrize("bad", [None, -5, 0, "abc", True, float("nan")])
def test_invalid_reported_spans_are_ignored(tmp_path, monkeypatch, bad):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, settings=_tech_settings(), lines=LINES)
    _install_runner(monkeypatch, tmp_path, {"A": {"elapsed_days": bad}, "B": {"elapsed_days": 40}}, [])
    state = _lines_step(tmp_path, data_dir)
    assert state.elapsed_days == 40.0
    assert "elapsed_days" not in state.line_updates["A"]


def test_no_valid_span_falls_back_visibly(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, settings=_tech_settings(), lines=LINES)
    _install_runner(monkeypatch, tmp_path, {}, [])
    state = _lines_step(tmp_path, data_dir)
    assert state.elapsed_days is None
    time_entry = next(a for a in state.tech_updates if a["action"] == "time")
    assert time_entry["elapsed_source"] == "fallback"


# ── 外生事件：抽样一次，按规则投放 ───────────────────────────────────


def test_event_delivery_by_owned_var_line_id_and_empty_affects(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    priors = [_sure_event("by_var", ["a"]), _sure_event("by_line", ["B"]), _sure_event("for_all", [])]
    _make_sim(data_dir, settings=_event_settings(priors, event_params={"max_events_per_step": 10}), lines=LINES)
    seen: List[Dict[str, Any]] = []
    _install_runner(monkeypatch, tmp_path, {}, seen)
    state = _lines_step(tmp_path, data_dir)
    delivered = {e["id"]: e["delivered_to"] for e in state.sampled_events}
    assert delivered == {"by_var": ["A"], "by_line": ["B"], "for_all": ["A", "B"]}
    a_hint, b_hint = _hint(seen, "A"), _hint(seen, "B")
    assert "[by_var]" in a_hint and "[for_all]" in a_hint and "[by_line]" not in a_hint
    assert "[by_line]" in b_hint and "[for_all]" in b_hint and "[by_var]" not in b_hint


def test_event_for_a_line_that_is_not_due_is_recorded_but_not_delivered(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    lines = [LINES[0], dict(LINES[1], local_step=1, advance_every_n_steps=3)]  # B 不到点
    _make_sim(data_dir, settings=_event_settings([_sure_event("only_b", ["B"])]), lines=lines)
    seen: List[Dict[str, Any]] = []
    _install_runner(monkeypatch, tmp_path, {}, seen)
    state = _lines_step(tmp_path, data_dir)
    assert [e["id"] for e in state.sampled_events] == ["only_b"]
    assert state.sampled_events[0]["delivered_to"] == []  # 可审计：发生了，但没有线能写它
    assert "[only_b]" not in _hint(seen, "A")
    assert "没有与这条线相关" in _hint(seen, "A")


def test_event_sampling_is_reproducible_and_only_once_per_step(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    prior = {"id": "coin", "description": "d", "rate_per_year": 3.0, "affects": []}
    runs = []
    for sim_id in ("s1", "s1b"):
        _make_sim(data_dir, settings=_event_settings([prior], event_sampling_salt="x"), lines=LINES, sim_id=sim_id)
    _install_runner(monkeypatch, tmp_path, {}, [])
    st = _lines_step(tmp_path, data_dir, "s1")
    # 两条线都到点，但抽样只做一次：每个事件 id 至多出现一次
    ids = [e["id"] for e in st.sampled_events]
    assert len(ids) == len(set(ids))
    # 种子含 sim_id，所以不同实例相互独立；同实例同步重跑一致（用 sampler 直接复算）
    from world_simulator import event_sampler
    again = event_sampler.sample_step(
        SimStore.for_root(data_dir, "s1").load_manifest().settings, [], {"a": 1, "b": 2},
        sim_id="s1", branch="main", step=1)
    assert [e["id"] for e in again] == ids


# ── 技术模型 ─────────────────────────────────────────────────────────


def test_line_tech_proposal_is_adjudicated_and_snapshotted(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, settings=_tech_settings(), lines=LINES)
    seen: List[Dict[str, Any]] = []
    _install_runner(monkeypatch, tmp_path, {"A": {
        "elapsed_days": 60, "tech_updates": [{"id": "chip", "name": "芯片", "stage": "lab"}]}}, seen)
    state = _lines_step(tmp_path, data_dir)
    assert "registered" in [a["action"] for a in state.tech_updates]
    saved = store.load_manifest().settings
    assert {n["id"] for n in tm.get_nodes(saved)} == {"ai", "chip"}
    assert "tech_state" in (state.dynamic_snapshot or {})
    assert "[ai]" in _hint(seen, "A") and "只提议与**这条线**直接相关" in _hint(seen, "A")


def test_same_tech_proposed_by_two_lines_first_wins_and_is_recorded(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, settings=_tech_settings(), lines=LINES)
    _install_runner(monkeypatch, tmp_path, {
        "A": {"elapsed_days": 10, "tech_updates": [{"id": "chip", "name": "芯片A", "stage": "lab"}]},
        "B": {"elapsed_days": 10, "tech_updates": [{"id": "chip", "name": "芯片B", "stage": "lab"}]},
    }, [])
    state = _lines_step(tmp_path, data_dir)
    names = [n["name"] for n in tm.get_nodes(SimStore.for_root(data_dir, "sim1").load_manifest().settings)
             if n["id"] == "chip"]
    assert names == ["芯片A"]
    dup = [v for v in state.tech_violations if v["code"] == mechanisms.TECH_DUPLICATE_CODE]
    assert len(dup) == 1 and dup[0]["tech_id"] == "chip" and dup[0]["detail"]["lines"] == ["A", "B"]


def test_tech_off_ignores_tech_updates_from_lines(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, settings={}, lines=LINES)
    _install_runner(monkeypatch, tmp_path, {"A": {"tech_updates": [{"id": "x", "stage": "lab"}]}}, [])
    state = _lines_step(tmp_path, data_dir)
    assert state.tech_updates == [] and state.tech_violations == []


# ── 因果引擎 ─────────────────────────────────────────────────────────


def _pending(pid="e@0", to="B", frm="A", triggered=0, **extra):
    p = {"pending_id": pid, "edge_id": "e", "from_line_id": frm, "to_line_id": to, "mechanism": "m",
         "triggered_at_step": triggered, "trigger_reason": "line_advanced", "delay_days": None,
         "delay_steps": 0, "retrigger_count": 0, "postponed_count": 0, "ignored_count": 0}
    p.update(extra)
    return p


def test_source_line_advance_queues_pending_effect(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, settings={
        "causal_engine_enabled": True,
        "declared_causal_graph": [{"from_line_id": "A", "to_line_id": "B", "delay_steps": 1}]}, lines=LINES)
    _install_runner(monkeypatch, tmp_path, {}, [])
    state = _lines_step(tmp_path, data_dir)
    assert [q["action"] for q in state.causal_queued] == ["queued"]
    saved = store.load_manifest().settings
    assert [p["pending_id"] for p in saved["causal_pending"]] == ["A->B@1"] or len(saved["causal_pending"]) == 1
    assert "causal_pending" in (state.dynamic_snapshot or {})  # 分支隔离（WP0）：队列进了快照


def test_due_pending_is_shown_only_to_its_target_line(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, settings={"causal_engine_enabled": True, "causal_pending": [_pending()]}, lines=LINES)
    seen: List[Dict[str, Any]] = []
    _install_runner(monkeypatch, tmp_path, {}, seen)
    _lines_step(tmp_path, data_dir)
    assert "[e@0]" in _hint(seen, "B")
    assert "[e@0]" not in _hint(seen, "A")


def test_target_line_reports_disposition_and_closes_pending(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, settings={"causal_engine_enabled": True, "causal_pending": [_pending()]}, lines=LINES)
    _install_runner(monkeypatch, tmp_path, {"B": {"effect_dispositions": [
        {"pending_id": "e@0", "disposition": "realized", "reason": "体现了"}]}}, [])
    state = _lines_step(tmp_path, data_dir)
    assert [(d["pending_id"], d["disposition"], d["closed"]) for d in state.effect_dispositions] == \
        [("e@0", "realized", True)]
    assert store.load_manifest().settings["causal_pending"] == []


def test_pending_whose_target_is_not_due_is_held_not_ignored(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    lines = [LINES[0], dict(LINES[1], local_step=1, advance_every_n_steps=3)]  # 目标线 B 不到点
    store = _make_sim(data_dir, settings={
        "causal_engine_enabled": True, "causal_params": {"max_ignored": 1},
        "causal_pending": [_pending()]}, lines=lines)
    _install_runner(monkeypatch, tmp_path, {}, [])
    state = _lines_step(tmp_path, data_dir)
    kept = store.load_manifest().settings["causal_pending"]
    assert [p["pending_id"] for p in kept] == ["e@0"]  # 没被\"到期未交代\"自动结案（max_ignored=1 本会结案）
    assert kept[0]["ignored_count"] == 0
    assert state.effect_dispositions == []


def test_main_path_still_counts_unaddressed_due_items(tmp_path, monkeypatch):
    """对照：`advance()` 路径行为没变——到期没被交代就累计忽略次数。"""
    data_dir = tmp_path / "data"
    store = SimStore.for_root(data_dir, "sim1")
    ts = now_iso()
    store.save_manifest(SimManifest(sim_id="sim1", template="life_sim", intent="i", title="t", created_at=ts,
                                    updated_at=ts, settings={"causal_engine_enabled": True,
                                                             "causal_params": {"max_ignored": 1},
                                                             "causal_pending": [_pending()]}))
    store.append_state(SimState(step=0, summary="s0", vars={"age": 20}, options=[]))
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, [_default_payload(1)], {})
    advance(object(), tmp_path, data_dir, "sim1")
    assert store.load_manifest().settings["causal_pending"] == []  # 自动结案


# ── 树接地 / 一致性守卫 / 快照 ───────────────────────────────────────


def test_tree_grounding_runs_on_line_path_after_tech(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    lines = [dict(LINES[0], tree={"branches": []}), LINES[1]]
    store = _make_sim(data_dir, settings={"tree_grounding_enabled": True}, lines=lines)
    _install_runner(monkeypatch, tmp_path, {}, [])
    seen_args = {}

    def fake_enforce(causal_lines, before, **kw):
        seen_args["before_is_dict"] = isinstance(before, dict)
        seen_args["vars"] = dict(kw["vars_"])
        return [dict(x) for x in causal_lines], [{"line_id": "A", "marker": 1}], [{"code": "G9", "marker": True}]

    monkeypatch.setattr(mechanisms.tree_grounding, "safe_enforce_step", fake_enforce)
    state = _lines_step(tmp_path, data_dir)
    assert seen_args == {"before_is_dict": True, "vars": {"a": 1, "b": 2}}
    assert state.tree_grounding == [{"code": "G9", "marker": True}]
    assert state.tree_updates == [{"line_id": "A", "marker": 1}]
    assert store.load_manifest().settings["causal_lines"]


def test_consistency_guard_runs_on_line_path(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, settings={}, lines=LINES)
    _install_runner(monkeypatch, tmp_path, {}, [])
    calls = []

    def fake_check(history, next_state, settings, **kw):
        calls.append((len(history), next_state.step))
        return [{"code": "X", "marker": True}]

    monkeypatch.setattr(mechanisms.consistency_guard, "safe_check_step", fake_check)
    state = _lines_step(tmp_path, data_dir)
    assert calls == [(1, 1)]  # 看到的是\"推进前\"的历史（只有 step 0）
    assert state.consistency_warnings == [{"code": "X", "marker": True}]


def test_consistency_guard_can_be_turned_off(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, settings={"consistency_guard_enabled": False}, lines=LINES)
    _install_runner(monkeypatch, tmp_path, {}, [])
    state = _lines_step(tmp_path, data_dir)
    assert state.consistency_warnings == []


# ── 与 advance() 行为对等 ────────────────────────────────────────────


def test_tech_outcome_matches_main_path_for_equivalent_input(tmp_path, monkeypatch):
    proposals = [{"id": "ai", "stage": "expert"},  # 进度只有 0.6 → 驳回（T2）
                 {"id": "chip", "name": "芯片", "stage": "lab"}]
    # 主路径
    d1 = tmp_path / "d1"
    s1 = SimStore.for_root(d1, "m")
    ts = now_iso()
    s1.save_manifest(SimManifest(sim_id="m", template="life_sim", intent="i", title="t", created_at=ts,
                                 updated_at=ts, settings=_tech_settings()))
    s1.append_state(SimState(step=0, summary="s0", vars={"age": 20}, options=[]))
    _patch_sequenced_advance_step_workflow(
        monkeypatch, tmp_path, [_default_payload(1, elapsed_days=60, tech_updates=proposals)], {})
    main_state = advance(object(), tmp_path, d1, "m")
    # 独立推进路径：一条线拥有全部变量，输出同样的跨度与提议
    d2 = tmp_path / "d2"
    _make_sim(d2, settings=_tech_settings(), lines=[
        {"id": "main", "owned_vars": ["age"], "advance_every_n_steps": 1, "local_step": 0}],
        vars_={"age": 20}, sim_id="l")
    _install_runner(monkeypatch, tmp_path, {"main": {"elapsed_days": 60, "tech_updates": proposals}}, [])
    line_state = _lines_step(tmp_path, d2, "l")

    assert line_state.elapsed_days == main_state.elapsed_days == 60.0
    assert _codes(line_state.tech_violations) == _codes(main_state.tech_violations) == ["T2"]
    assert line_state.tech_updates == main_state.tech_updates
    nodes_main = tm.get_nodes(SimStore.for_root(d1, "m").load_manifest().settings)
    nodes_line = tm.get_nodes(SimStore.for_root(d2, "l").load_manifest().settings)
    assert nodes_line == nodes_main


# ── 纯函数 ───────────────────────────────────────────────────────────


def test_event_delivered_to_rules():
    due = [{"id": "A", "owned_vars": ["a", "a2"]}, {"id": "B", "owned_vars": ["b"]}]
    f = mechanisms.event_delivered_to
    assert f({"affects": []}, due) == ["A", "B"]
    assert f({"affects": ["a2"]}, due) == ["A"]
    assert f({"affects": ["B", "zzz"]}, due) == ["B"]
    assert f({"affects": ["zzz"]}, due) == []
    assert f({"affects": [], "suppressed_by_cap": True}, due) == []


def test_pending_routing_and_hold_out():
    owned = {"A", "B"}
    assert mechanisms.pending_routed_to({"to_line_id": "B"}, "B", owned) is True
    assert mechanisms.pending_routed_to({"to_line_id": "B"}, "A", owned) is False
    # 目标不是可独立推进的线 → 所有到点线都能交代
    assert mechanisms.pending_routed_to({"to_line_id": "ghost"}, "A", owned) is True
    hold = mechanisms.hold_out_predicate(owned, {"A"})
    assert hold({"to_line_id": "B"}) is True      # 目标线本步没到点
    assert hold({"to_line_id": "A"}) is False     # 目标线到点
    assert hold({"to_line_id": "ghost"}) is False  # 不属于可推进的线 → 不挂起


def test_merge_line_outputs_rules():
    merged, notes, reported = mechanisms.merge_line_outputs([
        ("A", {"elapsed_days": 12, "tech_updates": [{"id": "t"}, {"id": "u"}], "narrative": "na",
               "effect_dispositions": [{"pending_id": "p1"}]}),
        ("B", {"elapsed_days": "x", "tech_updates": [{"id": "t"}, "junk"], "summary": "sb",
               "effect_dispositions": [{"pending_id": "p2"}]}),
        ("C", {"elapsed_days": 7}),
    ])
    assert reported == {"A": 12.0, "C": 7.0}
    assert merged["elapsed_days"] == 12.0
    assert [p["id"] if isinstance(p, dict) else p for p in merged["tech_updates"]] == ["t", "u", "junk"]
    assert [n["code"] for n in notes] == ["T10"]
    assert [d["pending_id"] for d in merged["effect_dispositions"]] == ["p1", "p2"]
    assert merged["narrative"] == "na\nsb"
    none_merged, _, _ = mechanisms.merge_line_outputs([("A", {})])
    assert none_merged["elapsed_days"] is None


# ── 补充：被上限压掉的事件 / 时间跨度提示 / 技术种子锚定 ─────────────


def test_event_suppressed_by_cap_is_not_delivered(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    priors = [_sure_event("e1", []), _sure_event("e2", [])]
    _make_sim(data_dir, settings=_event_settings(priors, event_params={"max_events_per_step": 1}), lines=LINES)
    seen: List[Dict[str, Any]] = []
    _install_runner(monkeypatch, tmp_path, {}, seen)
    state = _lines_step(tmp_path, data_dir)
    kept = [e for e in state.sampled_events if not e.get("suppressed_by_cap")]
    dropped = [e for e in state.sampled_events if e.get("suppressed_by_cap")]
    assert len(kept) == 1 and len(dropped) == 1
    assert "delivered_to" not in dropped[0]  # 没发生的事件不投放
    assert f"[{dropped[0]['id']}]" not in _hint(seen, "A")


def test_causal_only_asks_line_span_once_with_line_wording(tmp_path, monkeypatch):
    """只开因果引擎（边按天数延迟）：提示词里要的是\"这条线这一次推进\"的跨度，且不重复索要。"""
    data_dir = tmp_path / "data"
    _make_sim(data_dir, settings={
        "causal_engine_enabled": True,
        "declared_causal_graph": [{"from_line_id": "A", "to_line_id": "B", "delay_days": 30}],
        "causal_pending": [_pending(delay_days=30)]}, lines=LINES)
    seen: List[Dict[str, Any]] = []
    _install_runner(monkeypatch, tmp_path, {"A": {"elapsed_days": 45}}, seen)
    state = _lines_step(tmp_path, data_dir)
    hint = _hint(seen, "B")
    assert "【时间跨度】" in hint and "这条线这一次推进" in hint
    assert "部分因果边的延迟以天数声明" not in hint  # 主路径那句\"这一步跨越天数\"不能再出现
    assert state.elapsed_days == 45.0


def test_tech_seed_is_anchored_on_branch_head_before_first_line_step(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, settings=_tech_settings(), lines=LINES)
    _install_runner(monkeypatch, tmp_path, {"A": {"elapsed_days": 50}}, [])
    _lines_step(tmp_path, data_dir)
    head0 = store.load_history("main")[0]
    # 与主路径一致：种子被写进推进之前那一步的快照，从那里分叉才能回到种子而不是\"已推进过\"的状态
    assert head0.dynamic_snapshot and head0.dynamic_snapshot["tech_state"]["nodes"][0]["progress"] == 0.0


def test_no_tech_seed_means_no_anchor(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, settings={"tech_model_enabled": True}, lines=LINES)
    _install_runner(monkeypatch, tmp_path, {"A": {"elapsed_days": 50}}, [])
    _lines_step(tmp_path, data_dir)
    assert store.load_history("main")[0].dynamic_snapshot is None
