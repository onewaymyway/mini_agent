"""tests/test_anatomy_deepen.py — 第二十四轮 A5：深度模式 + 轻量提议协议。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.5 / §8 A5。
覆盖：触发（各分支）、上限与顺延、总上限、耗时预算（假时钟）、失败降级（A8）、越权提议（A12）、
与主调用同一套裁决（A1/A3）、新增子项/信号规则、多线合并先到先采纳、关闭时零痕迹、状态往返、展示数据。
无需真实 LLM：`_call_workflow` 用桩替换。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import anatomy as an
from world_simulator import anatomy_deepen as ad
from world_simulator import anatomy_engine as ae
from world_simulator import element_view as ev_view
from world_simulator.engine import mechanisms
from world_simulator.state_model import SimState


# ── 构造 ─────────────────────────────────────────────────────────────


def _metric(mid="m", value=100, slope=0):
    return {"id": mid, "name": mid, "current": {"value": value},
            "trend": {"kind": "linear", "params": {"slope_per_year": {"value": slope}}}}


def _line(lid, key=True, **extra):
    a = {"meta": {"key": key, "anatomy_status": "draft"}, "metrics": [_metric()]}
    line = {"id": lid, "label": lid, "kind": "element", "element_type": "technology", "anatomy": a}
    line.update(extra)
    return line


def _settings(*ids, **params):
    s = {"element_modeling_enabled": True, "anatomy_enabled": True, "causal_lines": [_line(i) for i in ids]}
    s["anatomy_params"] = {"deep_cadence_steps": 1, **params}  # 默认每步都够保底节奏，便于测试其它触发
    return s


def _manifest(s):
    return SimpleNamespace(settings=s)


def _settle(s, step=1, days=100):
    """跑一步轻量结算，返回写好 anatomy_trace/violations 的 next_state。"""
    st = SimState(step=step, summary="x", elapsed_days=float(days))
    st.anatomy_trace, st.anatomy_violations = ae.safe_apply_step(
        s, None, step=step, elapsed_days_raw=days, pre=ae.capture_pre(s), sim_id="sim", branch="main",
    )
    return st


def _fake(monkeypatch, replies):
    """`replies`：按调用顺序的回复（dict 或 Exception）；返回调用记录。"""
    calls = []

    def fake(cfg, root, inputs):
        calls.append(inputs)
        r = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(ad, "_call_workflow", fake)
    return calls


def _run(s, st, history=(), events=None):
    ad.run_in_step(None, Path("."), _manifest(s), st, history=list(history), sampled_events=events or [], sim_id="sim", branch="main")


def _ok(text="细节"):
    return {"detail_narrative": text}


def _meta(s, lid):
    return an.get_anatomy(next(x for x in s["causal_lines"] if x["id"] == lid))["meta"]


# ── 触发 ─────────────────────────────────────────────────────────────


def test_cadence_triggers_first_step_and_records_reason(monkeypatch):
    s = _settings("a")
    st = _settle(s)
    cands = ad.collect_triggers(s, st, [], [])
    assert [c["element"] for c in cands] == ["a"] and "cadence" in cands[0]["codes"]


def test_non_key_elements_never_trigger(monkeypatch):
    s = _settings("a")
    s["causal_lines"].append(_line("b", key=False))
    st = _settle(s)
    assert [c["element"] for c in ad.collect_triggers(s, st, [], [])] == ["a"]


def test_cadence_respects_last_deep_step():
    s = _settings("a", deep_cadence_steps=3)
    st = _settle(s, step=2)
    _meta(s, "a")["deep_last_step"] = 1
    assert ad.collect_triggers(s, st, [], []) == []  # 距上次 1 步 < 3


def test_deviation_triggers_even_within_cadence():
    s = _settings("a", deep_cadence_steps=99)
    _meta(s, "a")["deep_last_step"] = 1
    st = _settle(s, step=2)
    st.anatomy_trace.append({"element": "a", "kind": "metric", "source": "deviation"})
    c = ad.collect_triggers(s, st, [], [])
    assert c and "deviation" in c[0]["codes"]


def test_milestone_and_bottleneck_triggers():
    s = _settings("a", deep_cadence_steps=99)
    _meta(s, "a")["deep_last_step"] = 1
    st = _settle(s, step=2)
    st.anatomy_trace += [{"element": "a", "kind": "milestone"}, {"element": "a", "kind": "bottleneck", "field": "status"}]
    codes = ad.collect_triggers(s, st, [], [])[0]["codes"]
    assert "milestone" in codes and "bottleneck" in codes


def test_event_hit_triggers():
    s = _settings("a", deep_cadence_steps=99)
    _meta(s, "a")["deep_last_step"] = 1
    st = _settle(s, step=2)
    assert "event" in ad.collect_triggers(s, st, [{"id": "e", "affects": ["a"]}], [])[0]["codes"]
    assert ad.collect_triggers(s, st, [{"id": "e", "affects": ["a"], "suppressed_by_cap": True}], []) == []


def test_unsettled_element_is_not_triggered():
    s = _settings("a")
    st = _settle(s, step=1)
    st.step = 5  # meta.last_step=1 ≠ 5
    assert ad.collect_triggers(s, st, [], []) == []


def test_priority_orders_by_score_then_declaration():
    s = _settings("a", "b", deep_cadence_steps=1)
    st = _settle(s)
    st.anatomy_trace.append({"element": "b", "kind": "milestone"})
    assert [c["element"] for c in ad.collect_triggers(s, st, [], [])] == ["b", "a"]


# ── 上限 / 顺延 / 预算 ───────────────────────────────────────────────


def test_step_cap_defers_rest_and_pending_carries_over(monkeypatch):
    s = _settings("a", "b", "c", "d", deep_max_calls_per_step=2)
    calls = _fake(monkeypatch, [_ok()])
    st = _settle(s)
    _run(s, st)
    assert len(calls) == 2
    stat = [r["status"] for r in st.anatomy_deep]
    assert stat == ["ok", "ok", "deferred", "deferred"]
    assert _meta(s, "c").get("deep_pending") and not _meta(s, "a").get("deep_pending")
    # 下一步：被顺延的元素带着 pending 优先
    st2 = _settle(s, step=2)
    c = ad.collect_triggers(s, st2, [], [st])
    assert {x["element"] for x in c[:2]} == {"c", "d"} and all(x["pending"] for x in c[:2])


def test_total_cap_skips_and_clears_pending(monkeypatch):
    s = _settings("a", deep_max_calls_total=1)
    calls = _fake(monkeypatch, [_ok()])
    prev = SimState(step=1, summary="x")
    prev.anatomy_deep = [{"element": "a", "status": "ok"}]
    st = _settle(s, step=2)
    _meta(s, "a")["deep_pending"] = ["旧原因"]
    _run(s, st, [prev])
    assert calls == [] and st.anatomy_deep[0]["status"] == "skipped"
    assert "deep_pending" not in _meta(s, "a")


def test_failed_calls_count_toward_total_but_deferred_do_not():
    h = SimState(step=1, summary="x")
    h.anatomy_deep = [{"status": "failed"}, {"status": "ok"}, {"status": "deferred"}, {"status": "skipped"}]
    assert ad.calls_used([h]) == 2


def test_time_budget_defers_remaining(monkeypatch):
    s = _settings("a", "b", deep_time_budget_sec=10)
    clock = {"t": 0.0}
    monkeypatch.setattr(ad, "_clock", lambda: clock["t"])

    def fake(cfg, root, inputs):
        clock["t"] += 15  # 一次调用就耗尽预算
        return _ok()

    monkeypatch.setattr(ad, "_call_workflow", fake)
    st = _settle(s)
    _run(s, st)
    assert [r["status"] for r in st.anatomy_deep] == ["ok", "deferred"] and st.anatomy_deep[1]["why"] == "time_budget"


# ── 失败降级 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("reply", [ad.DeepenError("boom"), RuntimeError("x"), {}, {"bottleneck_proposals": "不是列表"}])
def test_failure_degrades_to_a8_without_touching_state(monkeypatch, reply):
    s = _settings("a")
    _fake(monkeypatch, [reply])
    st = _settle(s)
    before = copy.deepcopy(an.get_anatomy(s["causal_lines"][0])["metrics"])
    n = len(st.anatomy_trace)
    _run(s, st)
    assert st.anatomy_deep[0]["status"] == "failed"
    assert any(v["code"] == "A8" for v in st.anatomy_violations)
    assert an.get_anatomy(s["causal_lines"][0])["metrics"] == before and len(st.anatomy_trace) == n
    assert _meta(s, "a")["deep_last_step"] == 1  # 失败也重置节奏，不重试风暴


def test_a0_skips_deep(monkeypatch):
    s = _settings("a")
    calls = _fake(monkeypatch, [_ok()])
    st = _settle(s)
    st.anatomy_violations.append({"code": "A0", "severity": "error", "message": "x"})
    _run(s, st)
    assert calls == [] and not st.anatomy_deep


def test_safe_wrapper_never_raises(monkeypatch):
    s = _settings("a")
    monkeypatch.setattr(ad, "run_in_step", lambda *a, **k: (_ for _ in ()).throw(ValueError("oops")))
    st = _settle(s)
    ad.safe_run_in_step(None, Path("."), _manifest(s), st, history=[], sampled_events=[])
    assert any(v["code"] == "A8" for v in st.anatomy_violations)


# ── 关闭 / 零痕迹 ────────────────────────────────────────────────────


@pytest.mark.parametrize("params", [{"deep_enabled": False}, {"deep_max_calls_per_step": 0}])
def test_disabled_records_nothing(monkeypatch, params):
    s = _settings("a", **params)
    calls = _fake(monkeypatch, [_ok()])
    st = _settle(s)
    _run(s, st)
    assert calls == [] and not st.anatomy_deep and "anatomy_deep" not in st.to_dict()
    assert "deep_last_step" not in _meta(s, "a")


def test_state_roundtrip_and_empty_is_byte_compatible():
    st = SimState(step=1, summary="x")
    assert "anatomy_deep" not in st.to_dict()
    st.anatomy_deep = [{"element": "a", "status": "ok", "narrative": "n"}]
    back = SimState.from_dict(st.to_dict())
    assert back.anatomy_deep == st.anatomy_deep


# ── 提议裁决：与主调用同一套规则 ─────────────────────────────────────


def test_deep_deviation_without_reason_rejected_a1(monkeypatch):
    s = _settings("a")
    _fake(monkeypatch, [{"detail_narrative": "x", "metric_deviations": [{"metric": "m", "value": 999}]}])
    st = _settle(s)
    _run(s, st)
    assert any(v["code"] == "A1" for v in st.anatomy_violations)
    assert an.get_anatomy(s["causal_lines"][0])["metrics"][0]["current"]["value"] != 999


def test_deep_deviation_with_reason_accepted_and_traced(monkeypatch):
    s = _settings("a")
    _fake(monkeypatch, [{"metric_deviations": [{"metric": "m", "value": 105, "reason": "新材料"}]}])
    st = _settle(s)
    _run(s, st)
    assert st.anatomy_deep[0]["accepted"]["proposed"] == 1
    assert any(e.get("source") == "deviation" for e in st.anatomy_trace)


def test_cross_element_proposal_dropped_a12(monkeypatch):
    s = _settings("a", "b", deep_max_calls_per_step=1)
    _fake(monkeypatch, [{"detail_narrative": "x", "metric_deviations": [{"element": "b", "metric": "m", "value": 101, "reason": "r"}]}])
    st = _settle(s)
    _run(s, st)
    assert any(v["code"] == "A12" for v in st.anatomy_violations)
    assert not any(e.get("source") == "deviation" for e in st.anatomy_trace)


def test_proposals_forced_to_called_element():
    s = _settings("a")
    _n, props, _v = ad.parse_output(s, "a", {"signals": [{"watch": "盯产线", "means": "量产"}]})
    assert props["signals"][0]["element"] == "a"


def test_per_key_proposal_cap():
    s = _settings("a")
    _n, props, _v = ad.parse_output(s, "a", {"signals": [{"watch": f"w{i}"} for i in range(20)]})
    assert len(props["signals"]) == ad.MAX_PROPOSALS_PER_KEY


def test_narrative_is_plain_text_and_capped():
    out = ad.clean_narrative("<script>alert(1)</script>\x00\x07" + "字" * 5000)
    assert "\x00" not in out and "\x07" not in out and len(out) <= ad.MAX_NARRATIVE_CHARS
    assert "<script>" in out  # 不做 HTML 处理，界面负责转义


# ── 新增子项 / 信号 ──────────────────────────────────────────────────


def _adj(s, props, step=1):
    return ae.apply_adjudication(s, props, step=step, sim_id="sim", branch="main")


def test_new_subitem_needs_reason():
    s = _settings("a")
    _settle(s)
    item = {"id": "m2", "name": "新指标", "current": {"value": 1}}
    _t, v = _adj(s, {"new_subitems": [{"element": "a", "part": "metric", "item": item}]})
    assert any(x["code"] == "A12" for x in v)
    assert [m["id"] for m in an.get_anatomy(s["causal_lines"][0])["metrics"]] == ["m"]


def test_new_subitem_added_as_llm_prior_and_review_downgraded():
    s = _settings("a")
    an.get_anatomy(s["causal_lines"][0])["meta"]["anatomy_status"] = "reviewed"
    _settle(s)
    item = {"id": "m2", "name": "新指标", "current": {"value": 1}, "basis": {"state": "user_confirmed"}}
    t, _v = _adj(s, {"new_subitems": [{"element": "a", "part": "metric", "reason": "叙事出现", "item": item}]})
    a = an.get_anatomy(s["causal_lines"][0])
    new = next(m for m in a["metrics"] if m["id"] == "m2")
    assert an.basis_of(new)["state"] == "llm_prior" and a["meta"]["anatomy_status"] == "draft"
    assert any(e["kind"] == "subitem" for e in t)


def test_new_subitem_never_overwrites_existing_id():
    s = _settings("a")
    _settle(s)
    item = {"id": "m", "name": "冒名", "current": {"value": 5}}
    _t, v = _adj(s, {"new_subitems": [{"element": "a", "part": "metric", "reason": "r", "item": item}]})
    assert any(x["code"] == "A12" for x in v)
    assert an.get_anatomy(s["causal_lines"][0])["metrics"][0]["name"] == "m"


def test_new_subitem_cap_per_call():
    s = _settings("a")
    _settle(s)
    props = [{"element": "a", "part": "metric", "reason": "r", "item": {"id": f"n{i}", "name": "n", "current": {"value": 1}}} for i in range(ae.MAX_NEW_SUBITEMS + 2)]
    _adj(s, {"new_subitems": props})
    assert len(an.get_anatomy(s["causal_lines"][0])["metrics"]) == 1 + ae.MAX_NEW_SUBITEMS


def test_signals_dedupe_and_cap():
    s = _settings("a")
    _settle(s)
    sig = [{"element": "a", "watch": "盯产线", "means": "量产"}] * 2
    _t, v = _adj(s, {"signals": sig})
    assert sum(1 for x in v if x["code"] == "A12") >= 1
    assert len(an.get_anatomy(s["causal_lines"][0]).get("signals") or []) == 1


def test_adjudication_does_not_advance_time():
    s = _settings("a")
    _settle(s)
    clock = an.get_anatomy(s["causal_lines"][0])["meta"]["clock_day"]
    _adj(s, {"signals": [{"element": "a", "watch": "w", "means": "m"}]})
    assert an.get_anatomy(s["causal_lines"][0])["meta"]["clock_day"] == clock


# ── 多线合并（先到先采纳） ───────────────────────────────────────────


def test_merge_anatomy_updates_first_wins_with_a12_notes():
    d = {"element": "a", "metric": "m", "value": 1, "reason": "r"}
    merged, notes = mechanisms.merge_anatomy_updates([
        ("l1", {"anatomy_updates": {"metric_deviations": [d]}}),
        ("l2", {"anatomy_updates": {"metric_deviations": [{**d, "value": 2}, {**d, "metric": "m2"}]}}),
    ])
    assert [x["value"] for x in merged["metric_deviations"]] == [1, 1]
    assert [x["metric"] for x in merged["metric_deviations"]] == ["m", "m2"]
    assert len(notes) == 1 and notes[0]["code"] == "A12" and notes[0]["detail"]["lines"] == ["l1", "l2"]


def test_merge_anatomy_updates_empty_and_garbage():
    assert mechanisms.merge_anatomy_updates([("l", {}), ("m", {"anatomy_updates": "x"}), ("n", {"anatomy_updates": {"signals": ["s", 3]}})]) == ({}, [])


# ── 输入构造 / 展示 ──────────────────────────────────────────────────


def test_build_inputs_has_digest_and_reasons():
    s = _settings("a")
    st = _settle(s)
    inp = ad.build_inputs(s, "a", next_state=st, sampled_events=[], reasons=["里程碑本步达成"], manifest=_manifest(s))
    assert isinstance(inp, dict) and any("里程碑" in str(v) for v in inp.values())


def test_step_summary_counts_and_cap():
    recs = [{"status": "ok"}, {"status": "failed"}, {"status": "deferred"}]
    out = ad.step_summary(recs, _settings("a"))
    assert out["calls"] == 2 and out["deferred"] == 1 and out["cap"] == 3


def test_deep_view_levels(monkeypatch):
    s = _settings("a")
    _fake(monkeypatch, [_ok("长" * 300)])
    st = _settle(s)
    _run(s, st)
    v = ev_view.build_deep_view(s, [st], "a")
    assert v["has_deep"] and v["steps"][0]["calls"] == 1
    c = v["cards"][0]
    assert c["status_label"] == "已深度分析" and len(c["narrative_short"]) <= 121 and len(c["narrative"]) == 300
    assert ev_view.build_deep_view(s, [SimState(step=1, summary="x")], "a") == {"has_deep": False}


def test_branch_fork_does_not_inherit_future_calls():
    h = [SimState(step=i, summary="x") for i in range(3)]
    h[2].anatomy_deep = [{"status": "ok"}]
    assert ad.calls_used(h[:2]) == 0 and ad.calls_used(h) == 1


def test_deep_params_validation():
    s = {"anatomy_enabled": True, "anatomy_params": {"deep_time_budget_sec": 0, "deep_max_calls_per_step": "x"}}
    p = an.get_params(s)
    assert p["deep_time_budget_sec"] == 120 and p["deep_max_calls_per_step"] == 3
    assert "deep_time_budget_sec" in an.invalid_param_keys(s)
