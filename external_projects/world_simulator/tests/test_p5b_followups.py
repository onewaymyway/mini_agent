"""tests/test_p5b_followups.py — 第二十二轮 P5b：计划 §8 三个确认项的落地。

1. `relationship.py` 的延迟改用 elapsed 单位（新增 `delay_days`，`delay_steps` 作旧数据/降级兜底）；
2. 因果引擎的兑现统计回写 `knowledge_base`（realized→validated、countered→contradicted，幂等）；
3. 技术违规的独立 opt-in 修复调用（`tech_repair_enabled`，只修一次、受约束、重新裁决、可降级）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §8 第 2/3 问与 §9 P5b。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from world_simulator import causal_engine as ce
from world_simulator import knowledge_base as kb
from world_simulator import relationship as rel
from world_simulator import spec_generator as sg
from world_simulator import tech_model as tm
from world_simulator.engine.advance import advance
from world_simulator.engine.management import get_simulation
from world_simulator.state_model import SimState

from test_causal_engine import _edge, _settings as _csettings
from test_fast_forward import _default_payload, _patch_sequenced_advance_step_workflow
from test_tech_model import _adv, _codes, _make_sim, _node, _settings as _tsettings


def _states(*elapsed, start=1):
    """历史：第 start.. 步，每步给定 elapsed_days（None = 缺）。"""
    return [SimpleNamespace(step=start + i, elapsed_days=e) for i, e in enumerate(elapsed)]


# ═══════════════════════════════════════════════════════════════════
# 1. relationship.py：delay_days（elapsed 单位）
# ═══════════════════════════════════════════════════════════════════


def test_normalize_parses_delay_days_and_omits_key_when_absent():
    out = rel.normalize_relationships([
        {"from": "a", "to": "b", "delay_days": 45},
        {"from": "a", "to": "c", "delay_days": "30.5"},
        {"from": "a", "to": "d", "delay_days": 0},
        {"from": "a", "to": "e", "delay_steps": 2},  # 旧格式
    ])
    assert out[0]["delay_days"] == 45.0 and out[1]["delay_days"] == 30.5 and out[2]["delay_days"] == 0.0
    assert "delay_days" not in out[3] and out[3]["delay_steps"] == 2  # 旧关系归一化结果不变


@pytest.mark.parametrize("bad", [-5, "abc", True, float("nan"), float("inf"), None])
def test_normalize_invalid_delay_days_is_treated_as_undeclared(bad):
    out = rel.normalize_relationships([{"from": "a", "to": "b", "delay_days": bad}])
    assert "delay_days" not in out[0]


def test_needs_elapsed_only_when_some_relationship_declares_delay_days():
    assert rel.needs_elapsed([{"from": "a", "to": "b", "delay_days": 10}])
    assert not rel.needs_elapsed([{"from": "a", "to": "b", "delay_steps": 3}])
    assert not rel.needs_elapsed(None) and not rel.needs_elapsed([])


def test_queue_pending_effect_copies_delay_days_but_legacy_entry_is_unchanged():
    new = rel.queue_pending_effect(None, relationships=[{"from": "a", "to": "b", "delay_days": 60}],
                                   relationship_ref="0", triggered_at_step=3)
    assert new[0]["delay_days"] == 60.0 and new[0]["due_step"] == 3
    old = rel.queue_pending_effect(None, relationships=[{"from": "a", "to": "b", "delay_steps": 2}],
                                   relationship_ref="0", triggered_at_step=3)
    assert old == [{"relationship_ref": "0", "triggered_at_step": 3, "due_step": 5}]  # 逐字节不变


def _p(**kw):
    base = {"relationship_ref": "0", "triggered_at_step": 1, "due_step": 1, "delay_days": 60.0}
    base.update(kw)
    return [base]


def test_due_by_days_waits_for_elapsed_even_when_due_step_has_passed():
    hist = _states(30, 10)  # 第 1、2 步；生成第 3 步：累计 (1,2] = 10 天
    assert rel.due_pending_effects(_p(), current_step=3, history=hist) == []
    hist2 = _states(30, 40, 30)  # 生成第 4 步：(1,3] = 70 天 ≥ 60
    due = rel.due_pending_effects(_p(), current_step=4, history=hist2)
    assert len(due) == 1 and due[0]["precision"] == "days" and due[0]["elapsed_days_since"] == 70.0


def test_same_delay_days_is_due_at_different_steps_when_step_length_varies():
    """delay_steps 的老问题：同样 2 步，步长不同时间差很大。delay_days 不受步长影响。"""
    short = _states(30, 5, 5)  # 步很短
    long_ = _states(30, 100, 100)  # 步很长
    assert rel.due_pending_effects(_p(), current_step=4, history=short) == []
    assert len(rel.due_pending_effects(_p(), current_step=4, history=long_)) == 1


def test_delay_days_without_history_or_with_missing_elapsed_degrades_to_steps():
    no_hist = rel.due_pending_effects(_p(due_step=2), current_step=3)  # 不传 history
    assert len(no_hist) == 1 and no_hist[0]["precision"] == "steps"
    gap = rel.due_pending_effects(_p(due_step=2), current_step=4, history=_states(30, None, 30))
    assert len(gap) == 1 and gap[0]["precision"] == "steps"
    not_yet = rel.due_pending_effects(_p(due_step=9), current_step=4, history=_states(30, None, 30))
    assert not_yet == []  # 降级后仍按 due_step


def test_legacy_pending_entries_ignore_history_entirely():
    legacy = [{"relationship_ref": "0", "triggered_at_step": 1, "due_step": 3}]
    assert rel.due_pending_effects(legacy, current_step=3, history=_states(1, 1)) == legacy
    assert rel.due_pending_effects(legacy, current_step=2, history=_states(999)) == []


def test_zero_day_delay_is_due_on_the_next_step():
    assert len(rel.due_pending_effects(_p(delay_days=0.0), current_step=2, history=_states(30))) == 1


def test_hint_shows_days_asks_elapsed_only_when_nobody_else_does_and_marks_degradation():
    settings = {"relationships": [{"id": "r1", "from": "A", "to": "B", "delay_days": 60}]}
    hint = sg._resolve_relationship_hint(settings, current_step=1, history=[])
    assert "延迟约 60 天后才体现" in hint and "elapsed_days" in hint
    for other in ({"tech_model_enabled": True}, {"event_sampling_enabled": True},
                  {"causal_engine_enabled": True, "declared_causal_graph": [_edge(delay_days=5)]}):
        assert "elapsed_days" not in sg._resolve_relationship_hint({**settings, **other}, current_step=1,
                                                                    history=[])  # 已有人索要，不重复
    # 只声明 delay_steps：不索要、文案与改动前一致
    old = sg._resolve_relationship_hint({"relationships": [{"from": "A", "to": "B", "delay_steps": 2}]},
                                        current_step=1)
    assert "延迟 2 步后才体现" in old and "elapsed_days" not in old
    # 精度降级提示
    settings["relationship_pending_effects"] = [
        {"relationship_ref": "r1", "triggered_at_step": 1, "due_step": 2, "delay_days": 60.0}]
    degraded = sg._resolve_relationship_hint(settings, current_step=3, history=_states(None, None))
    assert "精度降级" in degraded and "延迟期已到" in degraded


def test_advance_relationship_delay_days_end_to_end(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, {"relationships": [{"id": "r1", "from": "A", "to": "B", "delay_days": 60}]})
    cap: dict = {}
    s1 = _adv(monkeypatch, tmp_path, data_dir,
              _default_payload(1, elapsed_days=30, triggered_relationships=["r1"]), cap)
    assert "elapsed_days" in cap["inputs_list"][0]["relationship_hint"]  # 只声明天数延迟也要索要
    assert s1.elapsed_days == 30.0  # 仅关系声明天数延迟也落盘 elapsed_days
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.settings["relationship_pending_effects"][0]["delay_days"] == 60.0

    def hint_for(step_no, elapsed):
        c: dict = {}
        _adv(monkeypatch, tmp_path, data_dir, _default_payload(step_no, elapsed_days=elapsed), c)
        return c["inputs_list"][0]["relationship_hint"]

    # 触发在第 1 步；累计的是触发**之后**各步的 elapsed_days（不含触发那一步自己的 30 天）。
    assert "延迟期已到" not in hint_for(2, 25)  # 生成第 2 步：(1,1] 为空 → 0 天
    assert "延迟期已到" not in hint_for(3, 25)  # 生成第 3 步：第 2 步的 25 天
    assert "延迟期已到" not in hint_for(4, 10)  # 生成第 4 步：25 + 25 = 50 < 60
    assert "延迟期已到" in hint_for(5, 10)      # 生成第 5 步：25 + 25 + 10 = 60 ≥ 60
    assert store.load_history("main")[-1].step == 5


def test_advance_legacy_delay_steps_is_unchanged_and_does_not_record_elapsed(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, {"relationships": [{"id": "r1", "from": "A", "to": "B", "delay_steps": 1}]})
    cap: dict = {}
    s1 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=30, triggered_relationships=["r1"]), cap)
    assert s1.elapsed_days is None and "elapsed_days" not in cap["inputs_list"][0]["relationship_hint"]
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.settings["relationship_pending_effects"] == [
        {"relationship_ref": "r1", "triggered_at_step": 1, "due_step": 2}]


# ═══════════════════════════════════════════════════════════════════
# 2. 兑现统计回写 knowledge_base
# ═══════════════════════════════════════════════════════════════════


def _o(outcome="realized", edge="e1", cause="降息", effect="投资增加", mechanism="资金成本下降"):
    return {"edge_id": edge, "cause": cause, "effect": effect, "mechanism": mechanism, "outcome": outcome}


def _rec(data_dir, outcomes, step=2, branch="main"):
    return kb.record_edge_outcomes(data_dir, sim_id="s", template="t", branch=branch, step=step, outcomes=outcomes)


def test_kb_realized_and_countered_map_to_the_right_counters_and_create_hypothesis_items(tmp_path):
    r = _rec(tmp_path, [_o("realized"), _o("countered", edge="e2", cause="加息", effect="房价下跌")])
    assert r == {"validated": 1, "contradicted": 1, "created": 2, "skipped": 0}
    items = {i.cause: i for i in kb.load_all(tmp_path)}
    assert items["降息"].validated_count == 1 and items["降息"].contradicted_count == 0
    assert items["加息"].contradicted_count == 1 and items["加息"].validated_count == 0
    assert items["降息"].confidence == "hypothesis" and items["降息"].mechanism == "资金成本下降"
    assert items["降息"].evidence == ["s@main#step2:e1"]


def test_kb_other_outcomes_are_ignored(tmp_path):
    r = _rec(tmp_path, [_o("dampened"), _o("postponed"), _o("expired"), _o("unaddressed"), _o("bogus")])
    assert r["created"] == 0 and kb.load_all(tmp_path) == []


def test_kb_is_idempotent_per_branch_step_edge_but_counts_new_occurrences(tmp_path):
    _rec(tmp_path, [_o()], step=2)
    again = _rec(tmp_path, [_o()], step=2)
    assert again["skipped"] == 1 and kb.load_all(tmp_path)[0].validated_count == 1
    _rec(tmp_path, [_o()], step=5)  # 另一步：算新的一次，且落到同一个条目
    _rec(tmp_path, [_o()], step=2, branch="b2")  # 另一分支同一步：也算
    items = kb.load_all(tmp_path)
    assert len(items) == 1 and items[0].validated_count == 3


def test_kb_exact_text_match_prevents_duplicates_even_when_mechanism_dilutes_jaccard(tmp_path):
    long_mech = "这是一段很长的机制说明，包含大量与因果文本无关的词汇用来稀释相似度" * 3
    for step in (1, 2, 3):
        _rec(tmp_path, [_o(mechanism=long_mech)], step=step)
    assert len(kb.load_all(tmp_path)) == 1 and kb.load_all(tmp_path)[0].validated_count == 3


def test_kb_falls_back_to_jaccard_to_join_an_item_written_by_record_causal_links(tmp_path):
    kb.record_causal_links(tmp_path, sim_id="s0", template="t", step=1,
                           causal_links=[{"driver": "央行降息", "effect": "企业投资增加"}])
    _rec(tmp_path, [_o(cause="央行降息", effect="企业投资增加")], step=2)
    items = kb.load_all(tmp_path)
    assert len(items) == 1 and items[0].validated_count == 1  # record_causal_links 本身首次写入计 0


def test_kb_ignores_incomplete_outcomes_and_never_writes_when_nothing_changes(tmp_path):
    _rec(tmp_path, [_o(cause=""), _o(effect=""), _o(edge="")])
    assert not kb.knowledge_path(tmp_path).exists()


def _graph_settings(**extra):
    s = _csettings(_edge("econ", "industry", mechanism="降息带动投资"),
                   causal_lines=[{"id": "econ", "label": "宏观经济"}])
    s.update(extra)
    return s


def _disp(d="realized", auto=False, edge="econ->industry"):
    row = {"pending_id": f"{edge}@1", "edge_id": edge, "disposition": d, "reason": "r", "closed": True}
    if auto:
        row["auto"] = True
    return row


def test_kb_outcomes_selection_naming_and_switches():
    s = _graph_settings()
    out = ce.kb_outcomes(s, [_disp("realized"), _disp("countered"), _disp("dampened"), _disp("postponed"),
                              _disp("expired", auto=True), _disp("realized", auto=True), _disp("realized", edge="ghost")])
    assert [o["outcome"] for o in out] == ["realized", "countered"]
    assert out[0]["cause"] == "宏观经济" and out[0]["effect"] == "industry"  # 线用 label，查不到用 id
    assert out[0]["mechanism"] == "降息带动投资"
    assert ce.kb_writeback_enabled(s)  # 默认开
    assert ce.kb_outcomes({**s, "causal_kb_writeback": False}, [_disp()]) == []
    assert ce.kb_outcomes({**s, "causal_engine_enabled": False}, [_disp()]) == []
    assert ce.kb_outcomes(s, None) == []


def test_kb_outcomes_uses_tech_node_name_for_tech_endpoints():
    s = _csettings(_edge("battery", "ev"), tech_model_enabled=True,
                   tech_state={"nodes": [_node("battery", name="固态电池")]})
    out = ce.kb_outcomes(s, [_disp(edge="battery->ev")])
    assert out[0]["cause"] == "固态电池"


def _run_edge_flow(monkeypatch, tmp_path, settings, disposition):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, settings)
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, line_updates={"econ": {}}))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, effect_dispositions=[disposition]))
    return data_dir


def test_advance_writes_realized_back_to_knowledge_base(tmp_path, monkeypatch):
    data_dir = _run_edge_flow(monkeypatch, tmp_path, _graph_settings(),
                              {"pending_id": "econ->industry@1", "disposition": "realized"})
    items = kb.load_all(data_dir)
    assert len(items) == 1 and items[0].validated_count == 1
    assert items[0].cause == "宏观经济" and items[0].evidence == ["sim1@main#step2:econ->industry"]


def test_advance_writes_countered_back_as_contradiction(tmp_path, monkeypatch):
    data_dir = _run_edge_flow(monkeypatch, tmp_path, _graph_settings(),
                              {"pending_id": "econ->industry@1", "disposition": "countered", "reason": "被制裁抵消"})
    items = kb.load_all(data_dir)
    assert items[0].contradicted_count == 1 and items[0].validated_count == 0


def test_advance_does_not_write_when_switch_off_or_dampened(tmp_path, monkeypatch):
    d1 = _run_edge_flow(monkeypatch, tmp_path / "a", _graph_settings(causal_kb_writeback=False),
                        {"pending_id": "econ->industry@1", "disposition": "realized"})
    assert not kb.knowledge_path(d1).exists()
    d2 = _run_edge_flow(monkeypatch, tmp_path / "b", _graph_settings(),
                        {"pending_id": "econ->industry@1", "disposition": "dampened", "reason": "只体现一部分"})
    assert not kb.knowledge_path(d2).exists()


def test_kb_failure_never_breaks_the_step(tmp_path, monkeypatch):
    monkeypatch.setattr(kb, "record_edge_outcomes", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _graph_settings())
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, line_updates={"econ": {}}))
    s2 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        2, effect_dispositions=[{"pending_id": "econ->industry@1", "disposition": "realized"}]))
    assert s2.step == 2 and s2.effect_dispositions[0]["disposition"] == "realized"


# ═══════════════════════════════════════════════════════════════════
# 3. 技术违规修复调用
# ═══════════════════════════════════════════════════════════════════

_MISSING = object()


def _patch_with_repair(monkeypatch, tmp_path, advance_payloads, reply, cap):
    """advance_step 依次返回 `advance_payloads`；tech_repair 返回 `reply`：
    dict → JSON；str → 原样文本；`_MISSING` → workflow 不存在；Exception → 运行时抛出。"""
    cap.update(calls=0, repair_calls=0, repair_inputs=[], loads=[])
    ok = SimpleNamespace(value="done")

    class _Step:
        def __init__(self, sid):
            self.id, self.skill_name = sid, None

    class _WF:
        def __init__(self, sid):
            self.steps = [_Step(sid)]

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            cap["loads"].append(name)
            if name == "advance_step":
                return _WF("step")
            if name == "tech_repair":
                return None if reply is _MISSING else _WF("tech_repair")
            raise AssertionError(name)

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            if wf.steps[0].id == "step":
                payload = advance_payloads[min(cap["calls"], len(advance_payloads) - 1)]
                cap["calls"] += 1
                p = tmp_path / f"adv_{cap['calls']}.json"
                p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                return SimpleNamespace(status="done", step_results=[
                    SimpleNamespace(step_id="step", status=ok, result_file=str(p))])
            cap["repair_calls"] += 1
            cap["repair_inputs"].append(inputs)
            if isinstance(reply, Exception):
                raise reply
            text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
            return SimpleNamespace(status="done", step_results=[
                SimpleNamespace(step_id="tech_repair", status=ok, output=text)])

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)


def _go(monkeypatch, tmp_path, settings, payload, reply, *, step_no=1):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, settings)
    cap: dict = {}
    _patch_with_repair(monkeypatch, tmp_path, [payload], reply, cap)
    state = advance(object(), tmp_path, data_dir, "sim1")
    return state, cap, data_dir, store


def _node_of(data_dir, tid="ai"):
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    return next(n for n in manifest.settings["tech_state"]["nodes"] if n["id"] == tid)


def _t4_payload(**extra):
    return _default_payload(1, elapsed_days=10, tech_updates=[{"id": "ai", "stage": "lab"}], **extra)


def _t4_settings(**extra):
    return _tsettings(_node("ai", stage="expert", dwell=100), tech_repair_enabled=True, **extra)


# ── 纯函数 ──


def test_repair_is_off_by_default_and_requires_the_tech_model():
    assert not tm.repair_enabled({"tech_model_enabled": True})
    assert tm.repair_enabled({"tech_model_enabled": True, "tech_repair_enabled": True})
    assert not tm.repair_enabled({"tech_repair_enabled": True})  # 技术模型没开


def test_repairable_violations_are_only_t4_to_t7_warns():
    vs = [{"code": c, "severity": "warn"} for c in ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8")]
    vs += [{"code": "T4", "severity": "info"}, {"code": "T0", "severity": "info"}]
    assert [v["code"] for v in tm.repairable_violations(vs)] == ["T4", "T5", "T6", "T7"]
    assert tm.repairable_violations(None) == []


def test_constrain_repair_drops_new_ids_restores_protected_keys_and_clamps_stage():
    original = [{"id": "ai", "stage": "developer", "investment": "low", "requires": [{"tech_id": "x"}]},
                {"id": "chip", "stage": "lab"}]
    repaired = [
        {"id": "ai", "stage": "infrastructure", "investment": "high", "investment_reason": "x",
         "requires": [], "typical_dwell_days": {"lab": 1}, "regression_reason": "有依据"},
        {"id": "ghost", "name": "凭空新增"},
    ]
    out, notes = tm.constrain_repair(original, repaired)
    ai = next(o for o in out if o["id"] == "ai")
    assert ai["stage"] == "developer"  # 不得高于原声明
    assert ai["investment"] == "low" and "investment_reason" not in ai  # 受保护字段还原
    assert ai["requires"] == [{"tech_id": "x"}] and "typical_dwell_days" not in ai
    assert ai["regression_reason"] == "有依据"  # 白名单字段取修复值
    assert [o["id"] for o in out] == ["ai", "chip"] and out[1] == {"id": "chip", "stage": "lab"}  # 未返回的保留原样
    assert any("ghost" in n for n in notes) and any("高于原声明" in n for n in notes)


def test_constrain_repair_omitting_an_editable_key_withdraws_that_claim():
    out, _ = tm.constrain_repair([{"id": "ai", "stage": "lab", "cost_index": 2.0}], [{"id": "ai"}])
    assert out == [{"id": "ai"}]


def test_constrain_repair_tolerates_garbage():
    assert tm.constrain_repair([{"id": "a"}], "nonsense")[0] == [{"id": "a"}]
    assert tm.constrain_repair(None, [{"id": "a"}])[0] == []
    assert tm.constrain_repair([{"id": "a"}], [None, 3, {"x": 1}])[0] == [{"id": "a"}]


def test_workflow_file_is_valid_and_has_every_placeholder_the_code_supplies():
    import yaml

    wf = yaml.safe_load((Path(__file__).resolve().parent.parent / "workflows" / "tech_repair.yaml")
                        .read_text(encoding="utf-8"))
    prompt = wf["steps"][0]["prompt"]
    assert wf["steps"][0]["id"] == "tech_repair" and wf["steps"][0]["type"] == "agent"
    for key in ("narrative_hint", "tech_state_hint", "proposals_json", "violations_json"):
        assert "{" + key + "}" in prompt
    prompt.format(narrative_hint="", tech_state_hint="", proposals_json="", violations_json="")  # 没有悬空花括号


# ── advance() 端到端 ──


def test_repair_accepts_a_justified_regression_and_reruns_adjudication_from_the_pre_state(tmp_path, monkeypatch):
    reply = {"tech_updates": [{"id": "ai", "stage": "lab", "regression_reason": "融资断裂，项目停摆"}], "note": "补了原因"}
    state, cap, data_dir, store = _go(monkeypatch, tmp_path, _t4_settings(), _t4_payload(), reply)
    assert cap["repair_calls"] == 1
    assert state.tech_repair["status"] == "accepted" and state.tech_repair["codes_before"] == ["T4"]
    assert state.tech_repair["codes_after"] == [] and state.tech_violations == []
    assert state.tech_repair["violations_before"][0]["code"] == "T4"
    n = _node_of(data_dir)
    assert n["stage"] == "lab" and n["progress"] == 0.5  # 倒退被接受 → 进度置为 regression_progress
    assert "regression" in [a["action"] for a in state.tech_updates]
    # 修复调用拿到的是本步开始前的权威状态（专家阶段、进度 0），而不是第一次裁决之后的
    inputs = cap["repair_inputs"][0]
    assert "阶段 expert" in inputs["tech_state_hint"] and "进度 0%" in inputs["tech_state_hint"]
    assert "T4" in inputs["violations_json"] and "regression_reason" not in inputs["proposals_json"]
    # 落盘的快照与最终状态一致（回滚/重裁没有让快照错位）
    persisted = store.load_history("main")[-1]
    snap = next(x for x in persisted.dynamic_snapshot["tech_state"]["nodes"] if x["id"] == "ai")
    assert snap["stage"] == "lab" and snap["progress"] == 0.5
    assert persisted.tech_repair["status"] == "accepted"
    assert SimState.from_dict(json.loads(json.dumps(persisted.to_dict()))).tech_repair == persisted.tech_repair


def test_repair_may_withdraw_the_claim_instead_of_inventing_a_reason(tmp_path, monkeypatch):
    state, cap, data_dir, _ = _go(monkeypatch, tmp_path, _t4_settings(), _t4_payload(),
                                  {"tech_updates": [{"id": "ai"}], "note": "叙事里没有倒退依据，撤回"})
    assert state.tech_repair["status"] == "accepted" and state.tech_violations == []
    assert _node_of(data_dir)["stage"] == "expert"  # 没倒退


def test_repair_cannot_smuggle_in_investment_new_techs_or_a_higher_stage(tmp_path, monkeypatch):
    settings = _tsettings(tech_repair_enabled=True)
    payload = _default_payload(1, elapsed_days=10, tech_updates=[{"id": "chip", "name": "芯片", "stage": "consumer"}])
    reply = {"tech_updates": [
        {"id": "chip", "name": "芯片", "stage": "infrastructure", "preexisting": True,
         "investment": "high", "investment_reason": "x"},
        {"id": "ghost", "name": "凭空新增"}]}
    state, cap, data_dir, _ = _go(monkeypatch, tmp_path, settings, payload, reply)
    assert state.tech_repair["status"] == "accepted" and "T5" in state.tech_repair["codes_before"]
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    nodes = {n["id"]: n for n in manifest.settings["tech_state"]["nodes"]}
    assert set(nodes) == {"chip"}  # ghost 被丢弃
    assert nodes["chip"]["stage"] == "consumer" and nodes["chip"]["preexisting"] is True  # 阶段没被抬高
    assert any("ghost" in n for n in state.tech_repair["notes"])


def test_repair_for_t7_can_supply_a_shock_reason_and_for_t6_can_fix_adoption(tmp_path, monkeypatch):
    s7 = _tsettings(_node("ai", cost_index=1.0), tech_repair_enabled=True)
    p7 = _default_payload(1, elapsed_days=10, tech_updates=[{"id": "ai", "cost_index": 2.0}])
    state, _cap, d7, _ = _go(monkeypatch, tmp_path / "a", s7, p7, {"tech_updates": [
        {"id": "ai", "cost_index": 2.0, "cost_shock_reason": "出口管制导致原料涨价"}]})
    assert state.tech_repair["status"] == "accepted" and _node_of(d7)["cost_index"] == 2.0

    s6 = _tsettings(_node("a", market="m", adoption=0.6), _node("b", market="m", adoption=0.3),
                    tech_repair_enabled=True)
    p6 = _default_payload(1, elapsed_days=10, tech_updates=[{"id": "b", "adoption": 0.8}])
    state6, _c6, d6, _ = _go(monkeypatch, tmp_path / "b", s6, p6, {"tech_updates": [{"id": "b", "adoption": 0.4}]})
    assert "T6" in state6.tech_repair["codes_before"] and state6.tech_violations == []
    assert _node_of(d6, "b")["adoption"] == 0.4


def test_repair_that_does_not_improve_is_rejected_and_first_adjudication_is_kept(tmp_path, monkeypatch):
    same = {"tech_updates": [{"id": "ai", "stage": "lab"}]}  # 原样返回，违规不变
    state, cap, data_dir, _ = _go(monkeypatch, tmp_path, _t4_settings(), _t4_payload(), same)
    assert cap["repair_calls"] == 1 and state.tech_repair["status"] == "rejected"
    assert _codes(state.tech_violations) == ["T4"]
    n = _node_of(data_dir)
    assert n["stage"] == "expert" and abs(n["progress"] - 0.1) < 1e-9  # 恢复为第一次裁决后的状态


@pytest.mark.parametrize("reply", [_MISSING, "这不是 JSON", {"no_key": 1}, RuntimeError("boom")])
def test_repair_failures_degrade_to_the_first_adjudication(tmp_path, monkeypatch, reply):
    state, cap, data_dir, _ = _go(monkeypatch, tmp_path, _t4_settings(), _t4_payload(), reply)
    assert state.step == 1 and state.tech_repair["status"] == "failed"
    assert _codes(state.tech_violations) == ["T4"] and state.tech_repair["notes"]
    n = _node_of(data_dir)
    assert n["stage"] == "expert" and abs(n["progress"] - 0.1) < 1e-9


def test_failure_during_the_re_adjudication_restores_the_first_adjudication_state(tmp_path, monkeypatch):
    """回滚之后重新裁决时出错：技术状态必须恢复为第一次裁决的结果，而不是停在\"回滚后\"的半成品上。"""
    real = tm.apply_step
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("re-adjudication exploded")
        return real(*a, **k)

    monkeypatch.setattr(tm, "apply_step", flaky)
    reply = {"tech_updates": [{"id": "ai", "stage": "lab", "regression_reason": "有依据"}]}
    state, cap, data_dir, _ = _go(monkeypatch, tmp_path, _t4_settings(), _t4_payload(), reply)
    assert calls["n"] == 2 and state.tech_repair["status"] == "failed"
    assert "re-adjudication exploded" in state.tech_repair["notes"][0]
    assert _codes(state.tech_violations) == ["T4"]
    n = _node_of(data_dir)
    assert n["stage"] == "expert" and abs(n["progress"] - 0.1) < 1e-9


def test_repair_is_attempted_only_once_per_step(tmp_path, monkeypatch):
    _state, cap, _d, _ = _go(monkeypatch, tmp_path, _t4_settings(), _t4_payload(), {"tech_updates": [{"id": "ai", "stage": "lab"}]})
    assert cap["repair_calls"] == 1


def test_no_repair_call_when_switch_is_off_even_with_a_repairable_violation(tmp_path, monkeypatch):
    settings = _tsettings(_node("ai", stage="expert", dwell=100))  # 没开 tech_repair_enabled
    state, cap, _d, store = _go(monkeypatch, tmp_path, settings, _t4_payload(), {"tech_updates": []})
    assert cap["repair_calls"] == 0 and "tech_repair" not in cap["loads"]
    assert _codes(state.tech_violations) == ["T4"] and state.tech_repair is None
    assert "tech_repair" not in store.load_history("main")[-1].to_dict()  # 旧格式逐字节不变


def test_no_repair_call_for_violations_a_re_proposal_cannot_fix(tmp_path, monkeypatch):
    settings = _tsettings(_node("ai", dwell=100), tech_repair_enabled=True)
    payload = _default_payload(1, elapsed_days=10, tech_updates=[{"id": "ai", "stage": "expert"}])  # T2：进度未满
    state, cap, _d, _ = _go(monkeypatch, tmp_path, settings, payload, {"tech_updates": []})
    assert _codes(state.tech_violations) == ["T2"] and cap["repair_calls"] == 0 and state.tech_repair is None


def test_no_repair_call_when_there_are_no_violations(tmp_path, monkeypatch):
    settings = _tsettings(_node("ai", dwell=100), tech_repair_enabled=True)
    state, cap, _d, _ = _go(monkeypatch, tmp_path, settings, _default_payload(1, elapsed_days=10), {"tech_updates": []})
    assert cap["repair_calls"] == 0 and state.tech_violations == [] and state.tech_repair is None


def test_repair_works_when_the_seed_had_no_tech_state_at_all(tmp_path, monkeypatch):
    """pre_tech_state 为 None 的分支：回滚 = 移除 tech_state。"""
    settings = {"tech_model_enabled": True, "tech_repair_enabled": True}
    payload = _default_payload(1, elapsed_days=10, tech_updates=[{"id": "chip", "name": "芯片", "stage": "consumer"}])
    state, cap, data_dir, _ = _go(monkeypatch, tmp_path, settings, payload,
                                  {"tech_updates": [{"id": "chip", "name": "芯片", "stage": "lab"}]})
    assert state.tech_repair["status"] == "accepted"
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert [n["id"] for n in manifest.settings["tech_state"]["nodes"]] == ["chip"]


def test_ui_renders_the_repair_record_and_escapes_it():
    import app

    st_ = SimpleNamespace(
        tech_violations=[],
        tech_updates=[],
        tech_repair={"status": "accepted", "violations_before": [{"code": "T4", "message": "<b>倒退</b>"}],
                     "notes": ["<i>x</i>"]},
    )
    out = app._tech_violations_html(st_)
    assert "技术违规修复调用" in out and "已采纳" in out and "&lt;b&gt;倒退&lt;/b&gt;" in out and "<b>倒退" not in out
    assert "没有改写叙事" in out
    assert app._tech_violations_html(SimpleNamespace(tech_violations=[], tech_updates=[], tech_repair=None)) == ""
