"""tests/test_anatomy_engine.py — 第二十四轮 A4：引擎定量骨架。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.3 / §8 A4。
覆盖：白名单表达式（含恶意输入）、每种趋势的数值、瓶颈路径抽样（种子复现/分支独立/回退/耗尽）、里程碑/门槛、
阶段派生与 A3/A7、`A0–A11` 各码、流水自洽、条件语法扩展、提示词与预览、端到端 `advance()`、关闭时逐字节不变。
无需真实 LLM。
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import anatomy as an
from world_simulator import anatomy_engine as ae
from world_simulator import event_sampler as es
from world_simulator import tech_model as tm
from world_simulator.engine.advance import advance
from world_simulator.engine.management import get_simulation
from world_simulator.state_model import SimState

from test_fast_forward import _default_payload
from test_tech_model import _adv, _make_sim


# ── 构造 ─────────────────────────────────────────────────────────────


def _metric(mid="m", value=100, kind="linear", params=None, **extra):
    trend = {"kind": kind, "params": {k: {"value": v} for k, v in (params or {}).items()}}
    trend.update(extra.pop("trend_extra", {}))
    m = {"id": mid, "name": mid, "current": {"value": value}, "trend": trend}
    m.update(extra)
    return m


def _path(pid="p", p=1.0, low=100, mode=100, high=100, **extra):
    d = {"id": pid, "p_success": p, "duration_days": {"low": low, "mode": mode, "high": high}}
    d.update(extra)
    return d


def _settings(anatomy, *, tech=False, lines_extra=None, **extra):
    line = {"id": "el", "label": "元素", "kind": "element", "element_type": "technology", "anatomy": anatomy}
    line.update(lines_extra or {})
    s = {"element_modeling_enabled": True, "anatomy_enabled": True, "causal_lines": [line]}
    if tech:
        s["tech_model_enabled"] = True
        s["causal_lines"][0]["lifecycle"] = {
            "stage": "lab", "progress": 0.0, "dwell_days": 0.0, "adoption": None, "market": "",
            "typical_dwell_days": {st: 100 for st in tm.STAGES}, "dwell_source": "user", "sub_kind": "",
        }
    s.update(extra)
    return s


def _run(s, proposals=None, *, step=1, days=100, **kw):
    kw.setdefault("sim_id", "sim")
    kw.setdefault("branch", "main")
    return ae.apply_step(s, proposals, step=step, elapsed_days_raw=days, pre=ae.capture_pre(s), **kw)


def _a(s):
    return an.get_anatomy(s["causal_lines"][0])


def _item(s, part, iid):
    return next(x for x in _a(s)[part] if x["id"] == iid)


def _codes(v):
    return [x["code"] for x in v]


def _val(s, mid="m"):
    return ae.metric_value(_item(s, "metrics", mid))


YEAR = ae.DAYS_PER_YEAR


# ── 表达式求值：白名单，不用 eval ─────────────────────────────────────


def test_expr_basic_arithmetic_and_scopes():
    assert ae.eval_expr("v0 + params.k * t / 365.25", names={"t": 365.25, "v0": 10}, params={"k": 2}) == pytest.approx(12)
    assert ae.eval_expr("metric.a * 2 + max(1, 2, 3)", metrics={"a": 4}) == 11
    assert ae.eval_expr("1 if t > 5 else 0", names={"t": 6}) == 1
    assert ae.eval_expr("not 0 and 2 > 1") == 1
    assert ae.eval_expr("sqrt(16) + exp(0) + log(1) + abs(-2) + min(5, 3)") == pytest.approx(4 + 1 + 0 + 2 + 3)


@pytest.mark.parametrize("bad", [
    "__import__('os').system('x')", "().__class__", "params", "metric", "a.b", "params.x.y", "[1, 2]", "{1: 2}",
    "lambda: 1", "'abc'", "x[0]", "max(a=1)", "foo(1)", "max(1, 2, 3, 4)", "1 if 2", "a := 1", "f'{1}'", "",
    "-" * 40 + "1", " + ".join(["1"] * 70),
])
def test_expr_rejects_malicious_or_unsupported_syntax(bad):
    assert ae.validate_expr(bad) is not None
    with pytest.raises(ae.ExprError):
        ae.eval_expr(bad, names={"t": 1})


def test_expr_runtime_guards():
    for bad in ("2 ** 1000", "10 ** 400 * 10 ** 400", "1 / 0", "(-8) ** 0.5", "sqrt(-1)", "log(0)", "unknown + 1"):
        with pytest.raises(ae.ExprError):
            ae.eval_expr(bad)
    assert ae.validate_expr("t * 2") is None
    with pytest.raises(ae.ExprError):
        ae.eval_expr("x", names={})
    with pytest.raises(ae.ExprError):
        ae.eval_expr("params.k", params={})
    with pytest.raises(ae.ExprError):
        ae.eval_expr("1e308 * 10")  # 溢出成 inf → ExprError，不把非有限数交出去


def test_expr_never_uses_python_eval(monkeypatch):
    import builtins

    monkeypatch.setattr(builtins, "eval", lambda *a, **k: (_ for _ in ()).throw(AssertionError("eval used")))
    monkeypatch.setattr(builtins, "exec", lambda *a, **k: (_ for _ in ()).throw(AssertionError("exec used")))
    assert ae.eval_expr("1 + 2") == 3


# ── 开关 ─────────────────────────────────────────────────────────────


def test_inactive_is_a_noop_and_does_not_touch_settings():
    s = _settings({"metrics": [_metric(params={"slope_per_year": 1})]})
    s["anatomy_enabled"] = False
    snap = copy.deepcopy(s)
    assert ae.apply_step(s, None, step=1, elapsed_days_raw=10) == ([], [])
    assert s == snap
    assert ae.build_hint(s, [], 1) == ""
    assert not ae.needs_elapsed(s)
    # 开着但只有壳（没有引擎能结算的内容）也不算 active
    s2 = _settings({"meta": {"anatomy_status": "none", "key": True}})
    assert not ae.is_active(s2) and ae.build_hint(s2, [], 1) == "" and not ae.needs_elapsed(s2)
    assert ae.is_active(_settings({"metrics": [_metric(params={"slope_per_year": 1})]}))


# ── 趋势：每种的数值 ──────────────────────────────────────────────────


def _adv_metric(kind, params, value=100, days=YEAR, **kw):
    s = _settings({"metrics": [_metric(value=value, kind=kind, params=params, **kw)]})
    _run(s, days=days)
    return _val(s)


def test_trend_linear():
    assert _adv_metric("linear", {"slope_per_year": 20}) == pytest.approx(120)


def test_trend_exponential_compounds_yearly():
    assert _adv_metric("exponential", {"rate_per_year": 0.1}) == pytest.approx(110)
    assert _adv_metric("exponential", {"rate_per_year": 0.1}, days=2 * YEAR) == pytest.approx(121)
    assert _adv_metric("exponential", {"rate_per_year": -0.5}) == pytest.approx(50)


def test_trend_saturating_and_mean_reversion():
    assert _adv_metric("saturating", {"cap": 200, "rate_per_year": 1}) == pytest.approx(200 - 100 * math.exp(-1))
    assert _adv_metric("mean_reversion", {"mean": 50, "rate_per_year": 1}) == pytest.approx(50 + 50 * math.exp(-1))


def test_trend_logistic_and_degenerate_start():
    v = _adv_metric("logistic", {"cap": 500, "rate_per_year": 0.25}, value=350)
    assert v == pytest.approx(500 / (1 + (150 / 350) * math.exp(-0.25)))
    assert _adv_metric("logistic", {"cap": 500, "rate_per_year": 0.25}, value=600) == 600  # ≥ cap：保持不变
    assert _adv_metric("logistic", {"cap": 500, "rate_per_year": 0.25}, value=0) == 0


def test_trend_is_semigroup_so_step_size_does_not_matter():
    """自治流的增量推进与一次推进等价（步长不应改变结果）。"""
    for kind, params in (("logistic", {"cap": 500, "rate_per_year": 0.3}), ("saturating", {"cap": 300, "rate_per_year": 0.5}),
                         ("exponential", {"rate_per_year": 0.2})):
        one = _adv_metric(kind, params, value=100, days=YEAR)
        s = _settings({"metrics": [_metric(value=100, kind=kind, params=params)]})
        for i in range(4):
            _run(s, step=i + 1, days=YEAR / 4)
        assert _val(s) == pytest.approx(one)


def test_trend_learning_curve_follows_driver():
    s = _settings({"metrics": [
        {"id": "cost", "current": {"value": 100}, "trend": {"kind": "learning_curve", "driver": "metric:q", "params": {"b": {"value": 0.5}}}},
        _metric("q", value=10, kind="linear", params={"slope_per_year": 30}),  # 声明顺序在后：引擎必须先推进驱动量
    ]})
    _run(s, days=YEAR)  # 第一步：dq 尚无记录 → 成本保持，记下驱动量
    assert _val(s, "cost") == 100 and _val(s, "q") == pytest.approx(40)
    _run(s, step=2, days=YEAR)  # q: 40 → 70；cost = 100 * (70/40)^-0.5
    assert _val(s, "cost") == pytest.approx(100 * (70 / 40) ** -0.5)


def test_trend_learning_curve_missing_driver_degrades_with_a10():
    s = _settings({"metrics": [{"id": "c", "current": {"value": 1}, "trend": {"kind": "learning_curve", "params": {"b": {"value": 0.3}}}}]})
    _t, v = _run(s)
    assert _codes(v).count("A10") == 1 and _val(s, "c") == 1
    _t, v2 = _run(s, step=2)
    assert "A10" not in _codes(v2)  # 只提示一次


def test_trend_random_walk_is_reproducible_and_branch_sensitive():
    def go(branch, sim="s"):
        s = _settings({"metrics": [_metric(kind="random_walk_drift", params={"drift_per_year": 10, "vol_per_sqrt_year": 5})]})
        _run(s, days=YEAR, branch=branch, sim_id=sim)
        return _val(s)

    assert go("main") == go("main")
    assert go("main") != go("alt")
    assert go("main") != go("main", sim="other")
    pure = _adv_metric("random_walk_drift", {"drift_per_year": 10})
    assert pure == pytest.approx(110)  # 没有波动率 = 纯漂移


def test_trend_piecewise_table_interpolates_and_holds_ends():
    t = {"table": [[0, 100], [200, 300]]}
    s = _settings({"metrics": [_metric(kind="piecewise_table", trend_extra=t)]})
    _run(s, days=100)
    assert _val(s) == pytest.approx(200)
    _run(s, step=2, days=500)
    assert _val(s) == pytest.approx(300)


def test_trend_custom_expr_uses_t_v0_params_and_other_metrics():
    s = _settings({"metrics": [
        {"id": "m", "current": {"value": 10}, "trend": {"kind": "custom_expr", "expr": "v0 + params.k * t + metric.other",
                                                         "params": {"k": {"value": 0.1}}}},
        {"id": "other", "current": {"value": 5}},
    ]})
    _run(s, days=100)
    assert _val(s) == pytest.approx(10 + 0.1 * 100 + 5)


def test_trend_custom_expr_bad_expression_degrades_not_crashes():
    s = _settings({"metrics": [{"id": "m", "current": {"value": 10}, "trend": {"kind": "custom_expr", "expr": "__import__('os')"}}]})
    _t, v = _run(s)
    assert _codes(v) == ["A10"] and _val(s) == 10


def test_trend_step_events_jump_only_on_hit_events():
    ev = {"events": [{"event": "fab_fire", "factor": 2.0}, {"event": "grant", "delta": 5}]}
    s = _settings({"metrics": [_metric(kind="step_events", trend_extra=ev)]})
    _run(s, sampled_events=[{"id": "other"}])
    assert _val(s) == 100
    t, _v = _run(s, step=2, sampled_events=[{"id": "fab_fire"}, {"id": "grant", "suppressed_by_cap": True}])
    assert _val(s) == 200  # 被上限压掉的事件不算命中
    assert [e["source"] for e in t if e["kind"] == "metric"] == ["event"]


@pytest.mark.parametrize("kind", ["llm_reported", "no_such_trend"])
def test_unknown_or_llm_reported_trend_holds_value_and_logs_a10_once(kind):
    s = _settings({"metrics": [_metric(kind=kind)]})
    _t, v = _run(s)
    assert _codes(v) == ["A10"] and _val(s) == 100
    assert "A10" not in _codes(_run(s, step=2)[1])


def test_metric_without_trend_is_untouched_and_silent():
    s = _settings({"metrics": [{"id": "m", "current": {"value": 5}}], "bottlenecks": [{"id": "b"}]})
    t, v = _run(s)
    assert _val(s) == 5 and not [e for e in t if e["kind"] == "metric"] and v == []


def test_bounds_clamp_with_a4_and_chain_stays_consistent():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 500}, bounds={"min": 0, "max": 400})]})
    t, v = _run(s, days=YEAR)
    assert _val(s) == 400 and "A4" in _codes(v) and "A11" not in _codes(v)
    assert [e["source"] for e in t if e["kind"] == "metric"] == ["model", "clamp"]


def test_component_readiness_trend_is_clamped_to_unit_interval():
    s = _settings({"components": [{"id": "c", "readiness": 0.5, "readiness_trend": {"kind": "linear", "params": {"slope_per_year": {"value": 1.0}}}}]})
    _run(s, days=YEAR)
    assert ae.component_readiness(_item(s, "components", "c")) == 1.0


# ── 重新锚定 / 同一步不重复结算 ───────────────────────────────────────


def test_editing_declared_value_reanchors_with_trace_entry():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 10})]})
    _run(s, days=YEAR)
    assert _val(s) == pytest.approx(110)
    _item(s, "metrics", "m")["current"]["value"] = 500  # 用户/研究更新了现值
    t, v = _run(s, step=2, days=YEAR)
    assert _val(s) == pytest.approx(510) and "A11" not in _codes(v)
    anchors = [e for e in t if e["source"] == "anchor"]
    assert len(anchors) == 1 and anchors[0]["value_before"] == pytest.approx(110) and anchors[0]["value_after"] == 500


def test_same_step_is_settled_only_once():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 10})]})
    _run(s, days=YEAR)
    first = _val(s)
    assert _run(s, days=YEAR) == ([], [])  # 同一步再来一次：什么都不做
    assert _val(s) == first


# ── 瓶颈：路径抽样 / 到期 / 回退 / 耗尽 ───────────────────────────────


def _bn(paths, **extra):
    d = {"id": "b", "name": "b", "status": "open", "resolution_paths": paths}
    d.update(extra)
    return d


def test_certain_path_resolves_exactly_when_due():
    s = _settings({"bottlenecks": [_bn([_path(p=1.0, low=250, mode=250, high=250)])]})
    t, _v = _run(s, days=100)
    assert _item(s, "bottlenecks", "b")["status"] == "open"
    p = _item(s, "bottlenecks", "b")["resolution_paths"][0]
    assert p["status"] == "running" and p["resolve_at_day"] == 250 and p["started_day"] == 0
    _run(s, step=2, days=100)
    assert _item(s, "bottlenecks", "b")["status"] == "open"
    t3, _v = _run(s, step=3, days=100)  # clock 300 ≥ 250
    b = _item(s, "bottlenecks", "b")
    assert b["status"] == "resolved" and b["resolved_day"] == 250 and b["resolved_by"] == "p" and b["resolved_step"] == 3
    assert any(e["kind"] == "bottleneck" and e["value_after"] == "resolved" for e in t3)


def test_failed_path_falls_back_in_declared_fallback_order_then_exhausts():
    paths = [_path("a", p=0.0, low=10, mode=10, high=10), _path("b", p=0.0, low=10, mode=10, high=10), _path("c", p=1.0, low=10, mode=10, high=10)]
    s = _settings({"bottlenecks": [_bn(paths, fallback=["c", "a"])]})
    _run(s, days=15)  # c 先启动（fallback 优先）并成功
    assert _item(s, "bottlenecks", "b")["status"] == "resolved" and _item(s, "bottlenecks", "b")["resolved_by"] == "c"
    s2 = _settings({"bottlenecks": [_bn(paths[:2])]})
    _run(s2, days=100)  # a 失败（第 10 天）→ b 在第 10 天启动 → 第 20 天失败 → 耗尽
    b = _item(s2, "bottlenecks", "b")
    assert b["status"] == "exhausted" and [p["status"] for p in b["resolution_paths"]] == ["failed", "failed"]
    assert b["resolution_paths"][1]["started_day"] == 10


def test_path_requires_gates_start_until_condition_holds():
    p = _path("later", requires={"metric": "m", "op": ">=", "value": 150})
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 100})], "bottlenecks": [_bn([p])]})
    _run(s, days=YEAR / 4)  # m=125：未满足
    assert _item(s, "bottlenecks", "b")["resolution_paths"][0].get("status") is None
    _run(s, step=2, days=YEAR / 2)  # m=175：步末启动（在步末时刻）
    started = _item(s, "bottlenecks", "b")["resolution_paths"][0]
    assert started["status"] == "running" and started["started_day"] == pytest.approx(0.75 * YEAR)


def test_path_without_duration_is_not_schedulable_and_logged():
    s = _settings({"bottlenecks": [_bn([{"id": "p", "p_success": 0.5}])]})
    _t, v = _run(s)
    assert _codes(v) == ["A10"] and _item(s, "bottlenecks", "b").get("status") == "open"


def test_sampling_is_reproducible_and_isolated_by_branch_sim_step_and_common_numbers():
    def outcome(**kw):
        s = _settings({"bottlenecks": [_bn([_path(p=0.5, low=100, mode=400, high=1000)])]}, **kw.pop("settings", {}))
        _run(s, **kw)
        p = _item(s, "bottlenecks", "b")["resolution_paths"][0]
        return p["outcome"], p["resolve_at_day"]

    assert outcome() == outcome()
    results = {outcome(branch=f"b{i}") for i in range(40)}
    assert len(results) > 10  # 分支之间独立
    assert {outcome(sim_id=f"s{i}") for i in range(40)} != {outcome()}
    assert outcome(branch="x", settings={"event_sampling_common_random_numbers": True}) == outcome(branch="y", settings={"event_sampling_common_random_numbers": True})
    salted = lambda salt: {outcome(branch=f"b{i}", settings={"event_sampling_salt": salt}) for i in range(30)}  # noqa: E731
    assert salted("1") != salted("2")


def test_success_probability_is_honoured_statistically():
    wins = 0
    for i in range(400):
        s = _settings({"bottlenecks": [_bn([_path(p=0.3, low=1, mode=1, high=1)])]})
        _run(s, branch=f"b{i}")
        wins += _item(s, "bottlenecks", "b")["status"] == "resolved"
    assert 0.2 < wins / 400 < 0.4


def test_triangular_duration_stays_in_range_and_centres_near_mode():
    samples = []
    for i in range(300):
        s = _settings({"bottlenecks": [_bn([_path(low=100, mode=200, high=900)])]})
        _run(s, branch=f"b{i}", days=1)
        samples.append(_item(s, "bottlenecks", "b")["resolution_paths"][0]["resolve_at_day"])
    assert min(samples) >= 100 and max(samples) <= 900
    assert 100 < sum(samples) / len(samples) < 500


def test_path_start_does_not_reveal_outcome_in_trace():
    s = _settings({"bottlenecks": [_bn([_path(p=0.5)])]})
    t, _v = _run(s, days=1)
    assert "success" not in json.dumps(t, ensure_ascii=False) and "fail" not in json.dumps(t, ensure_ascii=False).replace("failed", "")


# ── LLM 提议的裁决 ────────────────────────────────────────────────────


def test_a2_claiming_resolved_before_due_is_rejected_and_state_unchanged():
    s = _settings({"bottlenecks": [_bn([_path(p=1.0, low=500, mode=500, high=500)])]})
    _t, v = _run(s, {"bottleneck_proposals": [{"element": "el", "bottleneck": "b", "status": "resolved", "reason": "突破了"}]}, days=100)
    assert _codes(v) == ["A2"] and _item(s, "bottlenecks", "b")["status"] == "open"


def test_a2_claim_matching_engine_resolution_is_not_a_violation():
    s = _settings({"bottlenecks": [_bn([_path(p=1.0, low=50, mode=50, high=50)])]})
    _t, v = _run(s, {"bottleneck_proposals": [{"element": "el", "bottleneck": "b", "status": "resolved"}]}, days=100)
    assert "A2" not in _codes(v) and _item(s, "bottlenecks", "b")["status"] == "resolved"


def test_shift_days_needs_reason_and_reanchors_due_day():
    s = _settings({"bottlenecks": [_bn([_path(low=500, mode=500, high=500)])]})
    _run(s, days=10)
    prop = {"bottleneck_proposals": [{"element": "el", "bottleneck": "b", "shift_days": -300, "reason": "出口管制解除", "cause_ref": "ev:export"}]}
    t, v = _run(s, prop, step=2, days=10)
    assert _item(s, "bottlenecks", "b")["resolution_paths"][0]["resolve_at_day"] == 200 and "A2" not in _codes(v)
    shifted = [e for e in t if e["field"] == "resolve_at_day"]
    assert shifted and shifted[0]["cause_ref"] == "ev:export" and shifted[0]["source"] == "deviation"
    _t, v2 = _run(s, {"bottleneck_proposals": [{"element": "el", "bottleneck": "b", "shift_days": 100}]}, step=3, days=10)
    assert _codes(v2) == ["A2"] and _item(s, "bottlenecks", "b")["resolution_paths"][0]["resolve_at_day"] == 200


def test_shift_cannot_pull_due_day_before_now_and_can_resolve_this_step():
    s = _settings({"bottlenecks": [_bn([_path(low=900, mode=900, high=900)])]})
    _run(s, days=100)
    _run(s, {"bottleneck_proposals": [{"element": "el", "bottleneck": "b", "shift_days": -10 ** 6, "reason": "r"}]}, step=2, days=100)
    assert _item(s, "bottlenecks", "b")["status"] == "resolved"
    assert _item(s, "bottlenecks", "b")["resolved_day"] == 100  # 夹到\"当前时钟\"，不会回到过去


def test_a1_big_deviation_without_reason_keeps_engine_value():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 0})]})
    _t, v = _run(s, {"metric_deviations": [{"element": "el", "metric": "m", "value": 300}]})
    assert _codes(v) == ["A1"] and _val(s) == 100


def test_deviation_with_reason_is_accepted_and_rebased_keeping_slope():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 100})]})
    t, v = _run(s, {"metric_deviations": [{"element": "el", "metric": "m", "value": 150, "reason": "新材料", "cause_ref": "ev:x"}]}, days=YEAR)
    assert _val(s) == 150 and v == []
    dev = [e for e in t if e["source"] == "deviation"][0]
    assert dev["value_before"] == pytest.approx(200) and dev["value_after"] == 150 and dev["cause_ref"] == "ev:x"
    _run(s, step=2, days=YEAR)
    assert _val(s) == pytest.approx(250)  # 斜率不变，只是平移了起点


def test_rebase_on_absolute_trend_adds_offset_so_next_step_stays_shifted():
    s = _settings({"metrics": [_metric(kind="piecewise_table", trend_extra={"table": [[0, 100], [200, 200]]})]})
    _run(s, {"metric_deviations": [{"element": "el", "metric": "m", "value": 120, "reason": "r"}]}, days=100)  # 模型 150 → 120
    _run(s, step=2, days=100)
    assert _val(s) == pytest.approx(200 - 30)  # 表值 200，偏移 -30


def test_keep_model_policy_records_but_does_not_adopt():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 0})]}, anatomy_params={"deviation_policy": "keep_model"})
    t, _v = _run(s, {"metric_deviations": [{"element": "el", "metric": "m", "value": 150, "reason": "r"}]})
    assert _val(s) == 100
    rec = [e for e in t if e["source"] == "deviation"][0]
    assert rec["reported_value"] == 150 and rec["value_before"] == rec["value_after"] == 100


def test_small_deviation_without_reason_is_accepted_big_one_is_not():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 0})]})
    _run(s, {"metric_deviations": [{"element": "el", "metric": "m", "value": 110}]})
    assert _val(s) == 110


def test_deviation_on_metric_without_model_is_adopted_as_llm_reported():
    s = _settings({"metrics": [_metric(kind="llm_reported")]})
    _run(s)
    t, v = _run(s, {"metric_deviations": [{"element": "el", "metric": "m", "value": 777, "reason": "新数据"}]}, step=2)
    assert _val(s) == 777 and "A1" not in _codes(v) and [e["source"] for e in t if e["kind"] == "metric"] == ["llm_reported"]


def test_deviation_beyond_bounds_is_clamped_with_a4():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 0}, bounds={"max": 150})]})
    _t, v = _run(s, {"metric_deviations": [{"element": "el", "metric": "m", "value": 999, "reason": "r"}]})
    assert _val(s) == 150 and "A4" in _codes(v)


def test_a5_unknown_references_are_logged_not_blocking():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 10})]})
    _t, v = _run(s, {
        "metric_deviations": [{"element": "ghost", "metric": "m", "value": 1}, {"element": "el", "metric": "nope", "value": 1}],
        "bottleneck_proposals": [{"element": "el", "bottleneck": "nope", "status": "resolved"}],
        "milestone_claims": [{"element": "el", "milestone": "nope"}],
    })
    assert _codes(v) == ["A5"] * 4 and all(x["severity"] == "info" for x in v)
    assert _val(s) == pytest.approx(100 + 10 * 100 / YEAR)  # 推进照常


def test_a5_proposal_without_element_resolves_unique_id_only():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 0})]})
    s["causal_lines"].append({"id": "el2", "label": "二", "kind": "element", "element_type": "technology",
                              "anatomy": {"metrics": [_metric(kind="linear", params={"slope_per_year": 0})]}})
    _t, v = _run(s, {"metric_deviations": [{"metric": "m", "value": 105, "reason": "r"}]})
    assert _codes(v) == ["A5"]  # m 在两个元素里都有 → 不唯一，不猜
    s2 = _settings({"metrics": [_metric("only", kind="linear", params={"slope_per_year": 0})]})
    _run(s2, {"metric_deviations": [{"metric": "only", "value": 105, "reason": "r"}]})
    assert _val(s2, "only") == 105


def test_a6_engine_edits_are_ignored():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 10})]})
    before = copy.deepcopy(_item(s, "metrics", "m")["trend"])
    _t, v = _run(s, {"engine_edits": [{"ref": "metric:m", "field": "trend.params.slope_per_year"}]})
    assert _codes(v) == ["A6"] and _item(s, "metrics", "m")["trend"] == before


# ── 里程碑 / 阶段 / A3 ───────────────────────────────────────────────


def _ms(mid="ms", crit=None, stage=None, **extra):
    d = {"id": mid, "name": mid, "criteria": crit or {"metric": "m", "op": ">=", "value": 150}}
    if stage:
        d["maps_to_stage"] = stage
    d.update(extra)
    return d


def test_milestone_reached_when_criteria_hold_and_never_unreached():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 100})], "milestones": [_ms()]})
    _run(s, days=YEAR / 4)
    assert "reached_step" not in _item(s, "milestones", "ms")
    _run(s, step=2, days=YEAR / 2)
    ms = _item(s, "milestones", "ms")
    assert ms["reached_step"] == 2 and ms["reached_sim_day"] == pytest.approx(0.75 * YEAR)
    t3, _v = _run(s, step=3, days=1)  # 判据依然成立：不重复达成、不改写首次达成的步号
    assert _item(s, "milestones", "ms")["reached_step"] == 2 and not [e for e in t3 if e["kind"] == "milestone"]
    _item(s, "metrics", "m")["engine"]["v"] = 0  # 指标掉回去：里程碑仍然达成（只增不减）
    _run(s, step=4, days=1)
    assert _item(s, "milestones", "ms")["reached_step"] == 2


def test_criteria_all_any_not_and_cross_element_refs():
    s = _settings({"metrics": [_metric(value=10)], "bottlenecks": [{"id": "b", "status": "resolved"}], "milestones": [
        _ms("a", {"all": [{"metric": "m", "op": "<", "value": 20}, {"bottleneck": "b", "status": "resolved"}]}),
        _ms("n", {"not": {"metric": "m", "op": ">", "value": 20}}),
        _ms("o", {"any": [{"metric": "m", "op": ">", "value": 99}, {"component": "x", "min_readiness": 0.5}]}),
        _ms("x", {"metric": "other#t", "op": ">=", "value": 5}),
    ], "components": [{"id": "x", "readiness": 0.4}]})
    s["causal_lines"].append({"id": "other", "label": "o", "kind": "element", "anatomy": {"metrics": [{"id": "t", "current": {"value": 6}}]}})
    _run(s, days=1)
    reached = {m["id"] for m in _a(s)["milestones"] if "reached_step" in m}
    assert reached == {"a", "n", "x"}


def test_milestone_with_dangling_ref_is_false_with_a5():
    s = _settings({"milestones": [_ms(crit={"metric": "ghost", "op": ">=", "value": 1})], "metrics": [{"id": "m", "current": {"value": 1}}]})
    _t, v = _run(s, days=1)
    assert _codes(v) == ["A5"] and "reached_step" not in _item(s, "milestones", "ms")


def test_a3_unsupported_milestone_claim_rejected_but_criteria_less_claim_accepted():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 0})], "milestones": [_ms(), {"id": "free", "desc": "无判据"}]})
    t, v = _run(s, {"milestone_claims": [{"element": "el", "milestone": "ms"}, {"element": "el", "milestone": "free", "reason": "官方宣布"}]})
    assert _codes(v) == ["A3"] and "reached_step" not in _item(s, "milestones", "ms")
    assert _item(s, "milestones", "free")["reached_step"] == 1
    assert [e["source"] for e in t if e["kind"] == "milestone"] == ["llm_reported"]


def _tech_anatomy(**extra):
    a = {"metrics": [_metric(kind="linear", params={"slope_per_year": 100})], "milestones": [
        _ms("pilot", {"metric": "m", "op": ">=", "value": 150}, "expert"),
        _ms("mass", {"metric": "m", "op": ">=", "value": 400}, "developer"),
    ]}
    a.update(extra)
    return a


def test_stage_derived_from_highest_reached_mapped_milestone():
    s = _settings(_tech_anatomy(), tech=True)
    _run(s, days=YEAR / 4)
    life = s["causal_lines"][0]["lifecycle"]
    assert life["stage"] == "lab" and life["progress"] == 0.0  # 判据只有一个叶子且未满足 → 0
    t, _v = _run(s, step=2, days=YEAR)  # m=225+... ≥150
    life = s["causal_lines"][0]["lifecycle"]
    assert life["stage"] == "expert" and life["last_transition_step"] == 2 and life["dwell_days"] == 0
    assert [e["source"] for e in t if e["kind"] == "stage"] == ["milestone"]
    _run(s, step=3, days=3 * YEAR)  # 多个里程碑同步达成：一次升到最高档
    assert s["causal_lines"][0]["lifecycle"]["stage"] == "developer"


def test_derived_progress_is_fraction_of_leaves_of_next_stage_milestone():
    a = _tech_anatomy(milestones=[_ms("pilot", {"all": [{"metric": "m", "op": ">=", "value": 150}, {"metric": "z", "op": ">=", "value": 1}]}, "expert")])
    a["metrics"].append(_metric("z", value=0, kind="linear", params={"slope_per_year": 0}))
    s = _settings(a, tech=True)
    _run(s, days=YEAR)  # m=200 满足，z 不满足 → 1/2
    assert s["causal_lines"][0]["lifecycle"]["progress"] == pytest.approx(0.5)


def test_a3_rejects_llm_stage_claim_that_tech_model_let_through_and_rolls_back():
    s = _settings(_tech_anatomy(), tech=True, tech_params={})
    life = s["causal_lines"][0]["lifecycle"]
    life["progress"] = 1.0  # T2 会放行（进度已满），但没有里程碑支撑
    pre = ae.capture_pre(s)
    life.update({"stage": "developer", "progress": 0.0, "dwell_days": 0.0, "last_transition_step": 1})  # 模拟 tech_model 已放行迁移
    _t, v = ae.apply_step(s, None, step=1, elapsed_days_raw=1, pre=pre, sim_id="s", branch="m")
    after = s["causal_lines"][0]["lifecycle"]
    assert "A3" in _codes(v) and after["stage"] == "lab"
    assert after["progress"] >= 0 and "last_transition_step" not in after and after["dwell_days"] == 0.0


def test_a3_rejects_regression_for_driven_elements():
    s = _settings(_tech_anatomy(), tech=True)
    _run(s, days=YEAR)
    assert s["causal_lines"][0]["lifecycle"]["stage"] == "expert"
    pre = ae.capture_pre(s)
    s["causal_lines"][0]["lifecycle"]["stage"] = "lab"  # tech_model 放行了一次带理由的倒退
    _t, v = ae.apply_step(s, None, step=2, elapsed_days_raw=1, pre=pre, sim_id="s", branch="m")
    assert "A3" in _codes(v) and s["causal_lines"][0]["lifecycle"]["stage"] == "expert"


def test_stage_derivation_off_or_without_mapped_milestones_leaves_tech_logic_alone():
    s = _settings(_tech_anatomy(), tech=True, anatomy_params={"anatomy_drives_stage": False})
    _run(s, days=YEAR)
    assert s["causal_lines"][0]["lifecycle"]["stage"] == "lab" and _item(s, "milestones", "pilot")["reached_step"] == 1
    a = _tech_anatomy()
    for m in a["milestones"]:
        m.pop("maps_to_stage")
    s2 = _settings(a, tech=True)
    s2["causal_lines"][0]["lifecycle"]["progress"] = 0.37
    _run(s2, days=YEAR)
    assert s2["causal_lines"][0]["lifecycle"]["progress"] == 0.37 and s2["causal_lines"][0]["lifecycle"]["stage"] == "lab"
    s3 = _settings(_tech_anatomy())  # 技术模型关闭：生命周期不归我们管
    s3["causal_lines"][0]["lifecycle"] = {"stage": "lab", "progress": 0.1}
    _run(s3, days=YEAR)
    assert s3["causal_lines"][0]["lifecycle"] == {"stage": "lab", "progress": 0.1}


def test_unknown_stage_name_logs_a5_and_does_not_derive():
    a = _tech_anatomy(milestones=[_ms("pilot", {"metric": "m", "op": ">=", "value": 1}, "warp_speed")])
    s = _settings(a, tech=True)
    _t, v = _run(s, days=1)
    assert "A5" in _codes(v) and s["causal_lines"][0]["lifecycle"]["stage"] == "lab"


# ── 门槛 / A7 ────────────────────────────────────────────────────────


def _gate_settings(adoption, before=None, open_value=100, **kw):
    a = {"metrics": [_metric("cost", value=open_value, kind="linear", params={"slope_per_year": 0})],
         "adoption_gates": [{"id": "g", "market": "ev", "criteria": {"metric": "cost", "op": "<=", "value": 120}, "unlocks": {"adoption_cap": 0.1}}]}
    s = _settings(a, tech=True)
    s["causal_lines"][0]["lifecycle"].update({"market": "EV", "adoption": adoption})
    return s


def test_gate_opens_when_criteria_hold_and_caps_new_adoption_growth():
    s = _gate_settings(0.0)
    pre = ae.capture_pre(s)
    s["causal_lines"][0]["lifecycle"]["adoption"] = 0.5  # tech_model 放行了 LLM 的采用率增长
    t, v = ae.apply_step(s, None, step=1, elapsed_days_raw=1, pre=pre, sim_id="s", branch="m")
    assert _item(s, "adoption_gates", "g")["open"] is True and _item(s, "adoption_gates", "g")["opened_step"] == 1
    assert "A7" in _codes(v) and s["causal_lines"][0]["lifecycle"]["adoption"] == pytest.approx(0.1)
    assert any(e["kind"] == "gate" and e["value_after"] is True for e in t)


def test_closed_gate_caps_at_zero_but_never_punishes_pre_existing_adoption():
    s = _gate_settings(0.3, open_value=999)
    pre = ae.capture_pre(s)
    _t, v = ae.apply_step(s, None, step=1, elapsed_days_raw=1, pre=pre, sim_id="s", branch="m")
    assert "A7" not in _codes(v) and s["causal_lines"][0]["lifecycle"]["adoption"] == 0.3  # 原有值不追溯
    s["causal_lines"][0]["lifecycle"]["adoption"] = 0.4
    _t, v = ae.apply_step(s, None, step=2, elapsed_days_raw=1, pre=ae.capture_pre({**s, "causal_lines": [{**s["causal_lines"][0], "lifecycle": {**s["causal_lines"][0]["lifecycle"], "adoption": 0.3}}]}), sim_id="s", branch="m")
    assert "A7" in _codes(v) and s["causal_lines"][0]["lifecycle"]["adoption"] == pytest.approx(0.3)


def test_gate_for_other_market_does_not_apply():
    s = _gate_settings(0.0, open_value=999)
    s["causal_lines"][0]["lifecycle"]["market"] = "grid"
    pre = ae.capture_pre(s)
    s["causal_lines"][0]["lifecycle"]["adoption"] = 0.9
    _t, v = ae.apply_step(s, None, step=1, elapsed_days_raw=1, pre=pre, sim_id="s", branch="m")
    assert "A7" not in _codes(v) and s["causal_lines"][0]["lifecycle"]["adoption"] == 0.9


# ── 审阅开关 ─────────────────────────────────────────────────────────


def test_require_review_to_drive_only_reviewed_items_drive_engine():
    reviewed = _metric("a", kind="linear", params={"slope_per_year": 100}, basis={"state": "user_confirmed"})
    draft = _metric("b", kind="linear", params={"slope_per_year": 100})
    s = _settings({"metrics": [reviewed, draft]}, anatomy_params={"require_review_to_drive": True})
    _run(s, days=YEAR)
    assert _val(s, "a") == pytest.approx(200) and _val(s, "b") == 100


# ── 时间 / A10 / A9 ──────────────────────────────────────────────────


def test_missing_elapsed_falls_back_and_marks_precision_degraded():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 36525})]})
    t, v = _run(s, days=None)
    time = [e for e in t if e["kind"] == "time"][0]
    assert time["source"] == "fallback" and "A10" in _codes(v)
    fb = tm.get_params(s)["fallback_days_per_step"]
    assert time["value_after"] == fb and _val(s) == pytest.approx(100 + 36525 * fb / YEAR)


def test_a9_stale_evidence_is_reported_once_per_anchor_without_blocking():
    m = _metric(kind="linear", params={"slope_per_year": 10})
    m["current"]["basis"] = {"state": "sourced", "evidence_ids": ["ev_1"]}
    s = _settings({"metrics": [m]})
    _t, v = _run(s, stale_evidence_ids={"ev_1"})
    assert _codes(v) == ["A9"] and _val(s) > 100
    assert "A9" not in _codes(_run(s, step=2, stale_evidence_ids={"ev_1"})[1])
    s2 = _settings({"metrics": [copy.deepcopy(m)]})
    assert _codes(_run(s2, stale_evidence_ids={"ev_other"})[1]) == []


def test_stale_evidence_ids_uses_ttl_and_never_raises():
    old = {"ev_id": "ev_1", "retrieved_at": "2000-01-01T00:00:00+00:00"}
    new = {"ev_id": "ev_2", "retrieved_at": "2999-01-01T00:00:00+00:00"}
    assert ae.stale_evidence_ids({}, [old, new, {"x": 1}, "junk"]) == {"ev_1"}
    assert ae.stale_evidence_ids({}, None) == set()
    assert ae.stale_evidence_ids({}, 5) == set()


# ── 流水自洽 / 原子写回 / 兜底 ───────────────────────────────────────


def test_trace_chain_is_consistent_for_a_busy_metric():
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 800}, bounds={"max": 500})]})
    t, v = _run(s, {"metric_deviations": [{"element": "el", "metric": "m", "value": 450, "reason": "r"}]}, days=YEAR)
    rows = [e for e in t if e["kind"] == "metric"]
    assert [(e["source"]) for e in rows] == ["model", "clamp", "deviation"]
    for a, b in zip(rows, rows[1:]):
        assert a["value_after"] == b["value_before"]
    assert "A11" not in _codes(v) and rows[-1]["value_after"] == _val(s)


def test_a11_reports_when_trace_is_tampered_instead_of_silently_fixing(monkeypatch):
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 10})]})
    orig = ae._advance_series

    def sabotage(ctx, el, holder, kind, dt, eng):
        orig(ctx, el, holder, kind, dt, eng)
        eng["v"] += 1  # 状态被偷偷改了，流水没记
    monkeypatch.setattr(ae, "_advance_series", sabotage)
    _t, v = _run(s, days=YEAR)
    assert "A11" in _codes(v)


def test_a11_reports_when_trace_entries_do_not_chain(monkeypatch):
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 800}, bounds={"max": 500})]})
    orig = ae._Ctx.add

    def crooked(self, element, kind, ref, field, before, after, reason, source, **extra):
        if source == "clamp":
            before = before + 7  # 流水里记的起点和上一条的终点对不上
        orig(self, element, kind, ref, field, before, after, reason, source, **extra)
    monkeypatch.setattr(ae._Ctx, "add", crooked)
    _t, v = _run(s, days=YEAR)
    assert any(x["code"] == "A11" and "不衔接" in x["message"] for x in v)


def test_trace_is_capped_with_a11_note(monkeypatch):
    monkeypatch.setattr(ae, "MAX_TRACE", 10)
    s = _settings({"metrics": [_metric(f"m{i}", kind="linear", params={"slope_per_year": 10}) for i in range(24)]
                   + [], "components": [{"id": f"c{i}", "readiness": 0.1, "readiness_trend": {"kind": "linear", "params": {"slope_per_year": {"value": 0.1}}}} for i in range(24)]})
    s["causal_lines"][0]["anatomy"]["bottlenecks"] = [{"id": f"b{i}", "resolution_paths": [_path(low=1, mode=1, high=1)]} for i in range(16)]
    t, v = _run(s, days=YEAR)
    assert len(t) <= ae.MAX_TRACE and any("超过上限" in x["message"] for x in v)


def test_engine_failure_leaves_settings_untouched_and_safe_wrapper_returns_a0(monkeypatch):
    s = _settings({"metrics": [_metric(kind="linear", params={"slope_per_year": 10})], "bottlenecks": [_bn([_path()])]})
    snap = copy.deepcopy(s)

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(ae, "_settle_milestones", boom)  # 在大半计算完成之后才炸
    t, v = ae.safe_apply_step(s, None, step=1, elapsed_days_raw=10, pre={}, sim_id="s", branch="m")
    assert t == [] and _codes(v) == ["A0"] and s == snap
    with pytest.raises(RuntimeError):
        ae.apply_step(s, None, step=1, elapsed_days_raw=10)
    assert s == snap  # 抛异常时也没有半改状态


def test_output_survives_normalization_unchanged():
    s = _settings(_tech_anatomy(bottlenecks=[_bn([_path(p=0.5, low=10, mode=20, high=40), _path("q", p=0.5, low=1, mode=1, high=1)])],
                                components=[{"id": "c", "readiness": 0.2, "readiness_trend": {"kind": "linear", "params": {"slope_per_year": {"value": 0.1}}}}],
                                adoption_gates=[{"id": "g", "criteria": {"metric": "m", "op": ">=", "value": 1}, "unlocks": {"adoption_cap": 0.2}}]), tech=True)
    for i in range(3):
        _run(s, step=i + 1, days=200)
    a = _a(s)
    assert an.normalize_anatomy(a) == a  # 引擎写的每个字段都被 A1 规整层保留（不会被下一次 normalize_element 吞掉）
    assert a["meta"]["clock_day"] == 600 and a["meta"]["last_step"] == 3


def test_engine_state_is_stripped_from_seeds_and_research_drafts():
    dirty = {"metrics": [{"id": "m", "current": {"value": 1}, "engine": {"v": 99}}], "components": [{"id": "c", "engine": {"v": 1}}],
             "bottlenecks": [{"id": "b", "status": "open", "resolved_step": 3, "resolved_day": 9, "resolved_by": "p",
                              "resolution_paths": [{"id": "p", "status": "succeeded", "outcome": "success", "started_day": 1, "started_step": 1, "resolve_at_day": 2}]}],
             "adoption_gates": [{"id": "g", "open": True, "opened_step": 1}], "milestones": [{"id": "ms", "reached_step": 1}],
             "meta": {"clock_day": 5, "last_step": 2}}
    norm = an.normalize_anatomy(dirty)
    assert norm["metrics"][0]["engine"] == {"v": 99}  # 规整层保留（引擎自己要用）
    seed, _d = an.seed_to_anatomy(dirty, "technology")
    flat = json.dumps(seed)
    for key in ("engine", "resolved_step", "outcome", "started_day", "opened_step", "reached_step", "clock_day"):
        assert key not in flat
    stripped = an.strip_engine_state(copy.deepcopy(norm))
    flat = json.dumps(stripped)
    for key in ("engine", "resolved_step", "outcome", "started_day", "opened_step", "reached_step", "clock_day", "last_step"):
        assert key not in flat


# ── 条件语法扩展（事件先验 / 树分支共用求值器）────────────────────────


def test_event_condition_can_reference_anatomy_subitems():
    s = _settings({"metrics": [_metric("density", value=420)], "bottlenecks": [{"id": "b", "status": "resolved"}]})
    ok = lambda c: es.evaluate_condition(c, {}, s)  # noqa: E731
    assert ok({"metric": "el#density", "op": ">=", "value": 400}) == (True, "")
    assert ok({"metric": "density", "op": ">=", "value": 400})[0] is True  # 唯一 id 可省元素前缀
    assert ok({"metric": "el#density", "op": ">=", "value": 500})[0] is False
    assert ok({"bottleneck": "el#b", "status": "resolved"})[0] is True
    assert ok({"all": [{"metric": "density", "op": ">", "value": 1}, {"not": {"bottleneck": "b", "status": "open"}}]})[0] is True
    for bad in ({"metric": "ghost#x", "op": ">=", "value": 1}, {"metric": "nope", "op": ">=", "value": 1}, {"metric": "density", "op": "~", "value": 1}, {"all": []}):
        got, why = ok(bad)
        assert got is False and why
    assert es.evaluate_condition([{"metric": "density", "op": ">=", "value": 400}, {"var": "x", "op": ">", "value": 1}], {"x": 2}, s)[0] is True
    assert es.evaluate_condition({"metric": "density", "op": ">=", "value": 1}, {}, {})[0] is False  # 没有剖面设置：不满足，不抛


def test_event_condition_old_syntax_is_untouched():
    assert es.evaluate_condition({"var": "x", "op": ">", "value": 1}, {"x": 2})[0] is True
    assert es.evaluate_condition({"var": "x", "op": ">", "value": 1}, {"x": 0})[0] is False


def test_tree_trigger_condition_can_use_anatomy_refs():
    from world_simulator import tree_grounding as tg

    s = _settings({"milestones": [_ms("pilot", {"metric": "m", "op": ">=", "value": 1})], "metrics": [_metric(value=5)]})
    line = {"id": "x", "label": "x", "future_tree": {"branches": [
        {"id": "b1", "status": "dormant", "trigger_condition": {"metric": "el#m", "op": ">=", "value": 5}}]}}
    lines, _audit, entries = tg.enforce_step([line], tg.status_index([line]), vars_={}, settings=s, step=1, auto=True)
    assert lines[0]["future_tree"]["branches"][0]["status"] == "active" and entries[0]["code"] == "G4"


# ── 提示词与预览 ─────────────────────────────────────────────────────


def _hint_settings():
    return _settings({
        "metrics": [_metric("density", value=350, kind="linear", params={"slope_per_year": 36.525}, unit="Wh/kg")],
        "bottlenecks": [_bn([_path("coat", p=1.0, low=50, mode=50, high=50)], name="界面稳定性")],
        "milestones": [_ms("pilot", {"bottleneck": "b", "status": "resolved"}, "developer")],
        "meta": {"anatomy_status": "draft", "key": True},
    }, tech=True)


def test_hint_states_facts_predicts_this_step_and_does_not_mutate_settings():
    s = _hint_settings()
    snap = copy.deepcopy(s)
    hint = ae.build_hint(s, [], 1, sim_id="s", branch="m")
    assert snap == s  # 预览在副本上做
    assert "引擎" in hint and "不要自行宣布" in hint and "[el]" in hint and "density 350 Wh/kg" in hint
    assert "**预计本步发生**：" not in hint  # 没有历史时按占位天数估计，小于 50 天：路径还没到期
    assert "elapsed_days" not in hint  # 技术模型已开启，已经索要过了


def test_hint_announces_what_the_engine_will_do_when_basis_days_cover_the_due_date():
    s = _hint_settings()
    hist = [SimState(step=0, summary="x", elapsed_days=100.0)]
    hint = ae.build_hint(s, hist, 1, sim_id="s", branch="m")
    assert "**预计本步发生**：" in hint and "pilot" in hint and "developer" in hint and "已解决" in hint
    assert _item(s, "bottlenecks", "b")["status"] == "open"


def test_preview_matches_real_settlement_when_elapsed_equals_estimate():
    s = _hint_settings()
    hist = [SimState(step=0, summary="x", elapsed_days=100.0)]
    trace, basis, src = ae.preview_step(s, hist, 1, sim_id="s", branch="m")
    assert basis == 100 and src == "history_median"
    real, _v = _run(s, days=100)
    strip = lambda tr: [(e["element"], e["kind"], e["ref"], e["value_after"]) for e in tr]  # noqa: E731
    assert strip(trace) == strip(real)


def test_hint_asks_for_elapsed_only_when_no_other_mechanism_does():
    s = _hint_settings()
    s.pop("tech_model_enabled")
    assert "elapsed_days" in ae.build_hint(s, [], 1, sim_id="s", branch="m")
    s["event_sampling_enabled"] = True
    assert "elapsed_days" not in ae.build_hint(s, [], 1, sim_id="s", branch="m")
    assert "elapsed_days" not in ae.build_hint(_hint_settings(), [], 1, sim_id="s", branch="m", ask_elapsed_override=False)


def test_hint_digest_is_bounded_and_key_elements_first():
    s = _hint_settings()
    s["anatomy_params"] = {"light_digest_chars": 40}
    hint = ae.build_hint(s, [], 1, sim_id="s", branch="m")
    assert "…" in hint
    s2 = _settings({"metrics": [_metric("a", value=1)]})
    for i in range(12):
        s2["causal_lines"].append({"id": f"e{i}", "label": f"e{i}", "kind": "element", "anatomy": {"metrics": [_metric("a", value=i)]}})
    s2["causal_lines"][5]["anatomy"]["meta"] = {"key": True}
    h2 = ae.build_hint(s2, [], 1, sim_id="s", branch="m")
    assert h2.count("\n- [") + h2.startswith("- [") <= ae.MAX_HINT_ELEMENTS and "另有" in h2
    assert h2.index("[e4]") < h2.index("[el]")  # 重点元素排在前面（e4 是下标 5 的线）


def test_safe_build_hint_never_raises(monkeypatch):
    monkeypatch.setattr(ae, "preview_step", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert ae.safe_build_hint(_hint_settings(), [], 1) == ""


def test_needs_elapsed_and_mechanisms_flags():
    from world_simulator.engine import mechanisms as mech

    s = _hint_settings()
    assert mech.needs_elapsed(s) and mech.any_mechanism_enabled(s)
    s["anatomy_enabled"] = False
    assert not mech.needs_elapsed({k: v for k, v in s.items() if k != "tech_model_enabled"})


# ── 展示用 ───────────────────────────────────────────────────────────


def test_trajectory_rebuilds_series_and_rows_from_history():
    h = [SimState(step=0, summary="s"),
         SimState(step=1, summary="s", anatomy_trace=[
             {"element": "el", "kind": "time", "ref": "elapsed", "field": "elapsed_days", "value_before": None, "value_after": 100, "reason": "", "source": "reported"},
             {"element": "el", "kind": "metric", "ref": "metric:m", "field": "value", "value_before": 100.0, "value_after": 110.0, "reason": "趋势", "source": "model"}]),
         SimState(step=2, summary="s", anatomy_trace=[
             {"element": "el", "kind": "metric", "ref": "metric:m", "field": "value", "value_before": 110.0, "value_after": 120.0, "reason": "趋势", "source": "model"},
             {"element": "other", "kind": "metric", "ref": "metric:m", "field": "value", "value_before": 1.0, "value_after": 2.0, "reason": "", "source": "model"}])]
    tr = ae.trajectory(h, "el")
    assert tr["series"] == {"metric:m": [(0, 100.0), (1, 110.0), (2, 120.0)]}
    assert [r["step"] for r in tr["rows"]] == [1, 2] and tr["rows"][0]["change"] == "100 → 110"


# ── 端到端：advance() ─────────────────────────────────────────────────


def _adv_settings(**extra):
    s = _hint_settings()
    s["anatomy_enabled"] = True
    s.update(extra)
    return s


def test_advance_runs_engine_persists_trace_snapshot_and_injects_hint(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, _adv_settings())
    cap: dict = {}
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=60), cap)
    hint = cap["inputs_list"][0]["anatomy_hint"]
    assert "引擎" in hint and "[el]" in hint
    assert state.elapsed_days == 60.0
    assert any(e["kind"] == "time" for e in state.anatomy_trace)
    b = state.dynamic_snapshot["causal_lines"][0]["anatomy"]["bottlenecks"][0]
    assert b["status"] == "resolved" and b["resolved_day"] == 50  # 50 天的路径在 60 天的步里结算
    assert state.dynamic_snapshot["causal_lines"][0]["lifecycle"]["stage"] == "developer"
    persisted = store.load_history("main")[-1]
    assert persisted.anatomy_trace == state.anatomy_trace
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.settings["causal_lines"][0]["anatomy"]["meta"]["last_step"] == 1


def test_advance_llm_cannot_declare_resolution_or_stage_jump(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    s = _adv_settings()
    s["causal_lines"][0]["anatomy"]["bottlenecks"][0]["resolution_paths"][0]["duration_days"] = {"low": 900, "mode": 900, "high": 900}
    s["causal_lines"][0]["lifecycle"]["progress"] = 0.99
    _make_sim(data_dir, s)
    payload = _default_payload(1, elapsed_days=200,
                               tech_updates=[{"id": "el", "stage": "developer"}],
                               anatomy_updates={"bottleneck_proposals": [{"element": "el", "bottleneck": "b", "status": "resolved", "reason": "突破"}]})
    state = _adv(monkeypatch, tmp_path, data_dir, payload)
    codes = [v["code"] for v in state.anatomy_violations]
    assert "A2" in codes and "A3" in codes
    snap = state.dynamic_snapshot["causal_lines"][0]
    assert snap["lifecycle"]["stage"] == "lab" and snap["anatomy"]["bottlenecks"][0]["status"] == "open"


def test_advance_switch_off_is_identical_to_before(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    s = _adv_settings(anatomy_enabled=False)
    store = _make_sim(data_dir, s)
    cap: dict = {}
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=60), cap)
    assert cap["inputs_list"][0]["anatomy_hint"] == ""
    assert state.anatomy_trace == [] and state.anatomy_violations == []
    d = store.load_history("main")[-1].to_dict()
    assert "anatomy_trace" not in d and "anatomy_violations" not in d
    anat = state.dynamic_snapshot["causal_lines"][0]["anatomy"] if state.dynamic_snapshot else _hint_settings()["causal_lines"][0]["anatomy"]
    assert "engine" not in json.dumps(anat) and "clock_day" not in json.dumps(anat)


def test_advance_without_any_anatomy_leaves_old_instances_untouched(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, {"tech_model_enabled": True, "tech_state": {"nodes": [{"id": "ai", "name": "ai", "stage": "lab"}]}})
    cap: dict = {}
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=40), cap)
    assert cap["inputs_list"][0]["anatomy_hint"] == "" and state.anatomy_trace == []
    assert "anatomy_trace" not in store.load_history("main")[-1].to_dict()


def test_advance_engine_failure_does_not_break_the_step(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _adv_settings())
    monkeypatch.setattr(ae, "apply_step", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=60))
    assert state.step == 1 and [v["code"] for v in state.anatomy_violations] == ["A0"]


def test_advance_is_reproducible_across_identical_runs(tmp_path, monkeypatch):
    def run(tag):
        d = tmp_path / tag / "data"
        s = _adv_settings()
        s["causal_lines"][0]["anatomy"]["bottlenecks"][0]["resolution_paths"][0].update({"p_success": 0.5, "duration_days": {"low": 10, "mode": 200, "high": 900}})
        _make_sim(d, s)
        st = _adv(monkeypatch, tmp_path / tag, d, _default_payload(1, elapsed_days=100))
        return json.dumps(st.anatomy_trace, sort_keys=True), json.dumps(st.dynamic_snapshot["causal_lines"][0]["anatomy"], sort_keys=True)

    assert run("a") == run("a2")  # sim_id、分支、步号都相同 → 逐位一致


def test_branch_fork_rolls_back_anatomy_state(tmp_path, monkeypatch):
    from world_simulator import branch_manager as bm

    data_dir = tmp_path / "data"
    s = _adv_settings()
    s["causal_lines"][0]["anatomy"]["bottlenecks"][0]["resolution_paths"][0]["duration_days"] = {"low": 150, "mode": 150, "high": 150}
    _make_sim(data_dir, s)
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=100))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, elapsed_days=100))

    def _anat():
        manifest, _c, _h = get_simulation(data_dir, "sim1")
        return manifest.settings["causal_lines"][0]["anatomy"], manifest.settings["causal_lines"][0]["lifecycle"]

    a, life = _anat()
    assert a["bottlenecks"][0]["status"] == "resolved" and a["meta"]["clock_day"] == 200 and life["stage"] == "developer"
    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1)  # 回到瓶颈解决之前
    a, life = _anat()
    assert a["bottlenecks"][0]["status"] == "open" and a["meta"]["clock_day"] == 100 and life["stage"] == "lab"
    assert a["bottlenecks"][0]["resolution_paths"][0]["status"] == "running"
    bm.switch_branch(data_dir, "sim1", "main")
    a, life = _anat()
    assert a["bottlenecks"][0]["status"] == "resolved" and life["stage"] == "developer"
    assert new_branch != "main"


def test_placeholder_exists_in_both_workflows_and_independent_path_hint():
    root = Path(__file__).resolve().parent.parent / "workflows"
    for name in ("advance_step.yaml", "world_evolve.yaml"):
        assert "{anatomy_hint}" in (root / name).read_text(encoding="utf-8")
    from world_simulator.engine import mechanisms as mech

    s = _hint_settings()
    hint = mech.build_line_hint(s, [], 1, line=s["causal_lines"][0], events=[], owned_ids=set(), sim_id="s", branch="m")
    assert "[el]" in hint and "元素剖面" in hint


# ── 元素档案：引擎推进视图 ────────────────────────────────────────────


def test_profile_rows_show_engine_state_only_after_engine_ran():
    from world_simulator import element_view as ev

    s = _hint_settings()
    before = ev.build_profile(s, "el")
    flat = json.dumps(before, ensure_ascii=False)
    assert "引擎当前值" not in flat and "进行中" not in flat and ev.build_progress(s, [], "el") == {"has_progress": False}
    _run(s, days=20)  # 路径（50 天）启动，指标推进
    prof = json.dumps(ev.build_profile(s, "el"), ensure_ascii=False)
    assert "引擎当前值" in prof and "路径「coat」进行中，预计模拟第 50 天到期" in prof
    _run(s, step=2, days=60)
    prof = json.dumps(ev.build_profile(s, "el"), ensure_ascii=False)
    assert "第 2 步由路径「coat」解决" in prof and "第 2 步达成" in prof


def test_build_progress_collects_series_states_and_rows_and_respects_switch():
    from world_simulator import element_view as ev

    s = _hint_settings()
    trace, _v = _run(s, days=20)
    hist = [SimState(step=0, summary="s"), SimState(step=1, summary="s", anatomy_trace=trace)]
    prog = ev.build_progress(s, hist, "el")
    assert prog["has_progress"] and prog["clock_day"] == 20 and prog["last_step"] == 1
    assert prog["bottlenecks"][0]["running_path"] == "coat" and prog["bottlenecks"][0]["due_day"] == 50
    assert prog["milestones"][0]["reached_step"] is None and prog["milestones"][0]["stage"] == "developer"
    assert any("density" in k for k in prog["series"]) and prog["rows"]
    s["anatomy_enabled"] = False
    assert ev.build_progress(s, hist, "el") == {"has_progress": False}
    assert ev.build_progress(_hint_settings(), hist, "ghost") == {"has_progress": False}
