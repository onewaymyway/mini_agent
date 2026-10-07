"""tests/test_forecast.py — 第二十四轮 A6：蒙特卡洛 / 敏感性 / 假设条件化 / what-if / 监测清单。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.6 / §8 A6。无需真实 LLM。
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import anatomy as an
from world_simulator import anatomy_engine as ae
from world_simulator import event_sampler as es
from world_simulator import forecast as fc
from world_simulator import reality_check as rc

from test_anatomy_engine import _metric, _path, _settings


# ── 构造 ─────────────────────────────────────────────────────────────


def _bn(paths, bid="b", **extra):
    return {"id": bid, "name": bid, "status": "open", "resolution_paths": paths, **extra}


def _ms_resolved(mid="ms", bid="b"):
    return {"id": mid, "name": mid, "criteria": {"bottleneck": bid, "status": "resolved"}}


def _bn_settings(p_success=1.0, low=100, mode=200, high=300, **extra):
    a = {"bottlenecks": [_bn([_path("p1", p_success, low, mode, high)])], "milestones": [_ms_resolved()]}
    return _settings(a, **extra)


def _fc(s, **kw):
    kw.setdefault("runs", 300)
    kw.setdefault("seed", 11)
    kw.setdefault("horizon_days", 1000)
    kw.setdefault("steps", 40)
    return fc.run_forecast(s, **kw)


def _ms(res, mid="ms"):
    return next(m for m in res["milestones"] if m["id"] == mid)


# ── 引擎侧：精简模式与 apply_step 逐位一致 ─────────────────────────────


def test_simulate_step_equals_apply_step():
    a = {
        "metrics": [_metric("m", 100, "logistic", {"cap": 500, "rate_per_year": 0.4})],
        "components": [{"id": "c", "name": "c", "readiness": 0.1, "readiness_trend": {"kind": "saturating", "params": {"cap": {"value": 1}, "rate_per_year": {"value": 0.5}}}}],
        "bottlenecks": [_bn([_path("p1", 0.7, 100, 250, 400), _path("p2", 0.5, 50, 100, 150)], fallback=["p1", "p2"])],
        "milestones": [{"id": "ms", "name": "ms", "criteria": {"all": [{"bottleneck": "b", "status": "resolved"}, {"metric": "m", "op": ">=", "value": 150}]}}],
    }
    full, lean = _settings(a), copy.deepcopy(_settings(a))
    for step in range(1, 30):
        ae.apply_step(full, None, step=step, elapsed_days_raw=40, pre=ae.capture_pre(full), sim_id="s", branch="b1")
        ae.simulate_step(lean, step=step, days=40, sim_id="s", branch="b1")
    fa, la = an.get_anatomy(full["causal_lines"][0]), an.get_anatomy(lean["causal_lines"][0])
    assert ae.metric_value(fa["metrics"][0]) == pytest.approx(ae.metric_value(la["metrics"][0]))
    assert fa["bottlenecks"][0]["status"] == la["bottlenecks"][0]["status"]
    assert fa["milestones"][0].get("reached_sim_day") == la["milestones"][0].get("reached_sim_day")
    assert fa["bottlenecks"][0].get("resolved_day") == la["bottlenecks"][0].get("resolved_day")


def test_simulate_step_inactive_and_default_apply_step_still_atomic():
    assert ae.simulate_step({"anatomy_enabled": False}, step=1, days=10) == []
    s = _bn_settings()
    before = copy.deepcopy(s)
    trace, _v = ae.apply_step(s, None, step=1, elapsed_days_raw=10, pre=ae.capture_pre(s), sim_id="s", branch="b")
    assert trace and s != before  # 默认路径照常写回并记流水


def test_set_series_value_clamps_and_shifts_absolute_trend():
    h = {"id": "m", "current": {"value": 10}, "bounds": {"min": 0, "max": 50}, "trend": {"kind": "custom_expr", "expr": "v0 + t"}}
    assert ae.set_series_value(h, "metric", 999) == (10.0, 50.0)
    assert h["engine"]["off"] == 40.0
    assert ae.set_series_value({"id": "x"}, "metric", 1) is None


# ── 工具函数 ─────────────────────────────────────────────────────────


def test_quantile_with_inf():
    v = sorted([1.0, 2.0, 3.0, math.inf, math.inf])
    assert fc._quantile(v, 0.0) == 1.0
    assert fc._quantile(v, 0.5) == 3.0
    assert fc._quantile(v, 0.9) is None
    assert fc._quantile([], 0.5) is None
    assert fc._quantile([5.0], 0.9) == 5.0


@pytest.mark.parametrize("dist", ["triangular", "uniform", "lognormal"])
def test_sample_param_stays_in_bounds(dist):
    spec = {"low": 1.0, "high": 9.0, "value": 3.0, "dist": dist}
    vals = [fc._sample_param(spec, (i % 97 + 0.5) / 98, (i % 89 + 0.5) / 90) for i in range(500)]
    assert all(1.0 <= v <= 9.0 for v in vals)
    assert len(set(round(v, 6) for v in vals)) > 50
    assert fc._sample_param({"low": 2.0, "high": 2.0, "value": 2.0}, 0.3, 0.3) == 2.0


# ── 蒙特卡洛：统计正确性 ──────────────────────────────────────────────


def test_certain_path_distribution_matches_triangular():
    res = _fc(_bn_settings(1.0, 100, 200, 300), runs=600)
    m = _ms(res)
    assert m["p_reached"] == 1.0
    assert m["p50"] == pytest.approx(200, abs=40)           # 一步 25 天的量化 + 抽样噪声
    assert 120 <= m["p10"] < m["p50"] < m["p90"] <= 300


def test_success_probability_drives_reach_probability():
    m = _ms(_fc(_bn_settings(0.5, 100, 100, 100), runs=600))
    assert m["p_reached"] == pytest.approx(0.5, abs=0.08)


def test_failed_paths_exhaust_and_never_reach():
    res = _fc(_bn_settings(0.0, 100, 100, 100))
    assert _ms(res)["p_reached"] == 0.0 and _ms(res)["p50"] is None
    assert res["bottlenecks"][0]["exhausted"] == 1.0


def test_deterministic_same_seed_and_seed_changes_result():
    s = _bn_settings(0.6, 100, 300, 600)
    a, b = _fc(s, runs=120), _fc(s, runs=120)
    a["meta"].pop("elapsed_sec"), b["meta"].pop("elapsed_sec")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    c = _fc(s, runs=120, seed=12)
    assert c["milestones"] != a["milestones"]


def test_does_not_mutate_input_settings():
    s = _bn_settings(0.6, 100, 300, 600)
    snap = copy.deepcopy(s)
    _fc(s, runs=40)
    fc.sensitivity(s, runs=20, horizon_days=1000, steps=20)
    assert s == snap


def test_time_budget_truncation_is_a_prefix_of_the_full_run():
    s = _bn_settings(0.6, 100, 300, 600)
    ticks = iter(range(10 ** 6))
    clock = lambda: next(ticks) * 1.0  # noqa: E731 — 每次调用前进 1 秒
    res = _fc(s, runs=500, time_budget_sec=100, _clock=clock)
    assert res["meta"]["truncated"] and res["meta"]["runs_done"] < 500
    assert res["meta"]["runs_done"] >= fc.MIN_RUNS_BEFORE_TRUNCATE
    same = _fc(s, runs=res["meta"]["runs_done"])
    assert same["milestones"] == res["milestones"]            # 前 N 次与不截断时完全一致


def test_unlimited_budget_runs_everything():
    res = _fc(_bn_settings(), runs=60, time_budget_sec=0)
    assert res["meta"]["runs_done"] == 60 and not res["meta"]["truncated"]


def test_not_active_returns_reason():
    assert fc.run_forecast({"anatomy_enabled": False})["ok"] is False
    assert fc.run_forecast(None)["ok"] is False
    s = _settings({"signals": [{"id": "s", "watch": "x"}]})
    assert "没有带可结算内容" in fc.run_forecast(s)["reason"]
    assert fc.run_forecast(_bn_settings(), element="nope")["ok"] is False


def test_already_reached_milestone_and_resolved_bottleneck_are_reported_not_simulated():
    s = _bn_settings()
    a = an.get_anatomy(s["causal_lines"][0])
    a["meta"] = {"clock_day": 100.0}
    a["bottlenecks"][0]["status"] = "resolved"
    a["milestones"][0].update(reached_step=2, reached_sim_day=60.0)
    res = _fc(s, runs=20)
    assert _ms(res)["status"] == "reached" and _ms(res)["reached_day"] == -40.0
    assert res["bottlenecks"][0]["status"] == "resolved"


def test_running_path_is_redrawn_conditioned_on_elapsed_and_does_not_leak():
    s = _bn_settings(1.0, 100, 200, 300)
    a = an.get_anatomy(s["causal_lines"][0])
    a["meta"] = {"clock_day": 150.0}
    p = a["bottlenecks"][0]["resolution_paths"][0]
    p.update(status="running", outcome="fail", started_day=0.0, resolve_at_day=160.0, started_step=1)
    res = _fc(s, runs=300)
    m = _ms(res)
    assert m["p_reached"] == 1.0                    # 真实运行里藏着的 outcome=fail 不能泄漏（重抽后 p_success=1 必成功）
    assert m["p10"] >= 0                            # 已经过了 150 天，剩余耗时 > 0
    assert m["p50"] == pytest.approx(75, abs=45)    # 三角(100,200,300) 条件于 >150 的剩余时间中位数 ≈ 70–80


# ── 参数不确定性 ──────────────────────────────────────────────────────


def _metric_settings(ranged=True, **extra):
    p = {"rate_per_year": {"value": 0.3, "low": 0.1, "high": 0.5}} if ranged else {"rate_per_year": {"value": 0.3}}
    m = {"id": "m", "name": "m", "unit": "u", "current": {"value": 100}, "trend": {"kind": "exponential", "params": p}}
    a = {"metrics": [m], "milestones": [{"id": "ms", "name": "ms", "criteria": {"metric": "m", "op": ">=", "value": 130}}]}
    return _settings(a, **extra)


def test_ranged_parameter_widens_band_point_parameter_does_not():
    wide = fc.run_forecast(_metric_settings(True), runs=300, seed=3, horizon_days=1000, steps=20)["metrics"][0]
    flat = fc.run_forecast(_metric_settings(False), runs=300, seed=3, horizon_days=1000, steps=20)["metrics"][0]
    assert wide["p90"][-1] > wide["p10"][-1] * 1.05
    assert flat["p90"][-1] == pytest.approx(flat["p10"][-1])
    assert wide["start"] == 100.0 and wide["days"][0] == 0.0


def test_point_estimates_and_llm_prior_share_reported():
    res = fc.run_forecast(_metric_settings(False), runs=60, horizon_days=500, steps=10)
    assert res["meta"]["params"]["ranged"] == 0 and res["meta"]["params"]["point"] == 1
    assert any("rate_per_year" in x for x in res["meta"]["point_estimates"])
    assert res["meta"]["params"]["llm_prior_share"] == 1.0
    assert res["meta"]["honesty"] == fc.HONEST_NOTE


def test_point_mode_ignores_parameter_ranges():
    res = fc.run_forecast(_metric_settings(True), runs=100, seed=3, horizon_days=1000, steps=20, point=True)
    m = res["metrics"][0]
    assert m["p90"][-1] == pytest.approx(m["p10"][-1]) and res["meta"]["mode"] == "point"


def test_reaching_probability_monotone_in_growth_rate_range():
    lo = _metric_settings(True)
    hi = _metric_settings(True)
    for s, (l, h) in ((lo, (0.01, 0.05)), (hi, (0.6, 0.9))):
        p = an.get_anatomy(s["causal_lines"][0])["metrics"][0]["trend"]["params"]["rate_per_year"]
        p.update(value=(l + h) / 2, low=l, high=h)
    a = _ms(fc.run_forecast(lo, runs=100, horizon_days=1000, steps=20))["p_reached"]
    b = _ms(fc.run_forecast(hi, runs=100, horizon_days=1000, steps=20))["p_reached"]
    assert a < b


# ── 假设条件化 ────────────────────────────────────────────────────────


def _asm_settings(p_true=0.5, overrides=None):
    s = _bn_settings(1.0, 100, 200, 300)
    a = an.get_anatomy(s["causal_lines"][0])
    a["assumptions"] = [{"id": "a1", "statement": "可以解决", "prior_p_true": p_true,
                         "if_false": {"overrides": overrides if overrides is not None else [{"ref": "bottleneck:b", "set": {"duration_scale": 3.0}}]}}]
    return s


def test_assumption_truth_frequency_and_conditional_effect():
    res = _fc(_asm_settings(0.3), runs=600)
    row = res["conditional"][0]
    assert row["sufficient"] and row["n_true"] / 600 == pytest.approx(0.3, abs=0.07)
    e = row["effects"][0]
    assert e["p50_false"] > e["p50_true"] * 2          # 不成立：耗时 ×3
    assert e["delta_p50"] == pytest.approx(e["p50_false"] - e["p50_true"], abs=0.2)


def test_assumption_insufficient_samples_and_no_overrides():
    row = _fc(_asm_settings(0.01), runs=200)["conditional"][0]
    assert not row["sufficient"] and row["effects"] == []
    row2 = _fc(_asm_settings(0.5, overrides=[]), runs=200)["conditional"][0]
    assert not row2["has_overrides"]
    assert row2["effects"][0]["p50_true"] == pytest.approx(row2["effects"][0]["p50_false"], abs=60)


def test_unsupported_override_key_warns():
    s = _asm_settings(0.5, [{"ref": "bottleneck:b", "set": {"paths": "slow_only"}}, {"ref": "metric:nope", "set": {"value": 1}}])
    w = _fc(s, runs=60)["meta"]["warnings"]
    assert any("不认识键" in x for x in w) and any("不存在" in x for x in w)


def test_assumption_without_prior_is_listed_unsampled():
    s = _asm_settings(None)
    res = _fc(s, runs=40)
    assert res["conditional"] == [] and res["meta"]["assumptions_unsampled"]


def test_metric_override_changes_trend_parameter():
    s = _metric_settings(False)
    a = an.get_anatomy(s["causal_lines"][0])
    a["assumptions"] = [{"id": "a1", "statement": "增长放缓", "prior_p_true": 0.0,
                         "if_false": {"overrides": [{"ref": "metric:m", "set": {"rate_per_year": 0.0}}]}}]
    res = fc.run_forecast(s, runs=60, horizon_days=1000, steps=20)
    assert res["metrics"][0]["p50"][-1] == pytest.approx(100.0)   # 假设必不成立 → 增长率被覆盖为 0


# ── 事件 effects ──────────────────────────────────────────────────────


def _event_settings(effects, **pri):
    s = _metric_settings(False)
    an.get_anatomy(s["causal_lines"][0])["metrics"][0]["trend"] = {"kind": "linear", "params": {"slope_per_year": {"value": 0.0}}}
    s["event_sampling_enabled"] = True
    s["event_priors"] = [{"id": "boom", "rate_per_year": 1000.0, "effects": effects, **pri}]
    return s


def test_event_effects_apply_to_metric():
    res = fc.run_forecast(_event_settings([{"ref": "metric:m", "op": "add", "value": 5}], cooldown_steps=0), runs=30, horizon_days=100, steps=10)
    assert res["metrics"][0]["p50"][-1] > 100 + 5 * 3
    assert res["meta"]["events"] == {"sampled": 1, "with_effects": 1}


def test_event_cooldown_limits_hits():
    free = fc.run_forecast(_event_settings([{"ref": "metric:m", "op": "add", "value": 1}]), runs=20, horizon_days=100, steps=10)
    cool = fc.run_forecast(_event_settings([{"ref": "metric:m", "op": "add", "value": 1}], cooldown_steps=4), runs=20, horizon_days=100, steps=10)
    assert free["metrics"][0]["p50"][-1] > cool["metrics"][0]["p50"][-1]


def test_event_with_var_condition_never_fires_and_warns():
    res = fc.run_forecast(_event_settings([{"ref": "metric:m", "op": "add", "value": 5}], condition={"var": "x", "op": ">", "value": 0}), runs=20, horizon_days=100, steps=10)
    assert res["metrics"][0]["p50"][-1] == pytest.approx(100.0)
    assert any("vars" in w for w in res["meta"]["warnings"])


def test_event_unresolved_effect_ref_warns_and_unconfirmed_prior_ignored():
    res = fc.run_forecast(_event_settings([{"ref": "metric:ghost", "op": "add", "value": 5}]), runs=10, horizon_days=100, steps=10)
    assert any("解析不到" in w for w in res["meta"]["warnings"])
    res2 = fc.run_forecast(_event_settings([{"ref": "metric:m", "op": "add", "value": 5}], source="llm_estimate"), runs=10, horizon_days=100, steps=10)
    assert res2["meta"]["events"]["sampled"] == 0


def test_event_effect_shifts_running_path_and_mul_set_ops():
    s = _event_settings([{"ref": "metric:m", "op": "mul", "value": 2}], cooldown_steps=100)
    r = fc.run_forecast(s, runs=10, horizon_days=100, steps=10)
    assert r["metrics"][0]["p50"][-1] == pytest.approx(200.0)
    s = _event_settings([{"ref": "metric:m", "op": "set", "value": 7}], cooldown_steps=100)
    assert fc.run_forecast(s, runs=10, horizon_days=100, steps=10)["metrics"][0]["p50"][-1] == pytest.approx(7.0)


def test_event_rate_range_is_sampled_around_point():
    s = _event_settings([{"ref": "metric:m", "op": "add", "value": 1}], rate_per_year=20.0, rate_range={"low": 0.0, "high": 60.0}, cooldown_steps=0)
    res = fc.run_forecast(s, runs=200, horizon_days=100, steps=10)
    m = res["metrics"][0]
    assert m["p90"][-1] > m["p10"][-1]


def test_normalize_prior_effects_byte_identical_without_effects():
    p, _ = es.normalize_prior({"id": "a", "rate_per_year": 1})
    assert "effects" not in p
    p2, _ = es.normalize_prior({"id": "a", "rate_per_year": 1, "effects": [{"ref": "metric:x", "op": "mul", "value": 2}, {"ref": "b", "shift_days": -5}, {"ref": "", "op": "add", "value": 1}, {"ref": "m", "op": "zzz", "value": 1}, "x"]})
    assert p2["effects"] == [{"ref": "metric:x", "op": "mul", "value": 2.0}, {"ref": "b", "shift_days": -5.0}]
    many = [{"ref": f"metric:m{i}", "op": "add", "value": 1} for i in range(20)]
    assert len(es.normalize_prior({"id": "a", "rate_per_year": 1, "effects": many})[0]["effects"]) == es.MAX_EFFECTS


# ── 树分支 / 阶段 / 门槛 / 瓶颈顺序 ───────────────────────────────────


def test_tree_branch_activation_frequency_and_var_condition_unevaluable():
    s = _metric_settings(True)
    line = s["causal_lines"][0]
    line["future_tree"] = {"branches": [
        {"id": "b1", "label": "达到 130", "status": "dormant", "trigger_condition": {"metric": "m", "op": ">=", "value": 130}},
        {"id": "b2", "label": "用 var", "status": "dormant", "trigger_condition": {"var": "x", "op": ">", "value": 0}},
        {"id": "b3", "label": "无条件", "status": "dormant"},
    ]}
    res = fc.run_forecast(s, runs=100, seed=1, horizon_days=1500, steps=30)
    rows = {r["branch_id"]: r for r in res["branches"]}
    assert set(rows) == {"b1", "b2"}
    assert rows["b1"]["evaluable"] and 0 < rows["b1"]["p_active"] <= 1
    assert rows["b1"]["p_active"] == pytest.approx(_ms(res)["p_reached"], abs=0.12)   # 同一判据
    assert rows["b2"]["evaluable"] is False and "vars" in rows["b2"]["reason"]


def test_gate_opening_distribution():
    s = _bn_settings(1.0, 100, 100, 100)
    a = an.get_anatomy(s["causal_lines"][0])
    a["adoption_gates"] = [{"id": "g", "name": "g", "market": "ev", "criteria": {"bottleneck": "b", "status": "resolved"}, "unlocks": {"adoption_cap": 0.1}}]
    g = _fc(s, runs=50)["gates"][0]
    assert g["status"] == "closed" and g["p_ever_open"] == 1.0 and g["p50"] == pytest.approx(100, abs=30)


def test_resolution_order_frequencies():
    a = {"bottlenecks": [_bn([_path("p", 1.0, 100, 100, 100)], "fast"), _bn([_path("p", 1.0, 500, 500, 500)], "slow")], "milestones": []}
    res = _fc(_settings(a), runs=40)
    o = res["resolution_order"]
    assert o["orders"][0]["freq"] == 1.0 and o["orders"][0]["order"][0].endswith("fast")
    assert list(o["first_resolved"].values()) == [1.0] and o["none_resolved"] == 0.0


def test_stage_distribution_for_tech_element():
    a = {"bottlenecks": [_bn([_path("p", 1.0, 100, 100, 100)])],
         "milestones": [{"id": "ms", "name": "ms", "maps_to_stage": "developer", "criteria": {"bottleneck": "b", "status": "resolved"}}]}
    res = _fc(_settings(a, tech=True), runs=30)
    assert res["stages"][0]["final"] == {"developer": 1.0}


# ── 依赖闭包 ─────────────────────────────────────────────────────────


def _two_elements():
    s = _bn_settings(0.6, 100, 300, 600)
    other = {"id": "other", "label": "另一个", "kind": "element", "element_type": "technology", "anatomy": {
        "bottlenecks": [_bn([_path("p", 0.5, 100, 100, 100)])], "milestones": [_ms_resolved("om")]}}
    dep = {"id": "dep", "label": "依赖者", "kind": "element", "element_type": "technology", "anatomy": {
        "milestones": [{"id": "dm", "name": "dm", "criteria": {"bottleneck": "el#b", "status": "resolved"}}]}}
    s["causal_lines"] += [other, dep]
    return s


def test_closure_follows_explicit_refs_only():
    s = _two_elements()
    assert fc._closure(s, "dep") == ["el", "dep"] or set(fc._closure(s, "dep")) == {"el", "dep"}
    assert fc._closure(s, "other") == ["other"]
    assert fc._closure(s, "ghost") is None


def test_restricted_run_matches_full_run_for_closed_elements():
    s = _two_elements()
    full = _fc(s, runs=100)
    only = fc.run_forecast(s, runs=100, seed=11, horizon_days=1000, steps=40, element="other")
    assert only["meta"]["elements"] == ["other"]
    pick = lambda r: next(m for m in r["milestones"] if m["id"] == "om")  # noqa: E731
    assert pick(full) == pick(only)          # 随机数按身份取：限定闭包不改变闭包内元素的结果


# ── 敏感性 ───────────────────────────────────────────────────────────


def _sens_settings():
    a = {
        "metrics": [
            {"id": "big", "name": "big", "current": {"value": 100}, "trend": {"kind": "exponential", "params": {"rate_per_year": {"value": 0.3, "low": 0.05, "high": 0.8}}}},
            {"id": "tiny", "name": "tiny", "current": {"value": 100}, "trend": {"kind": "exponential", "params": {"rate_per_year": {"value": 0.3, "low": 0.29, "high": 0.31}}}},
        ],
        "milestones": [{"id": "ms", "name": "ms", "criteria": {"metric": "big", "op": ">=", "value": 150}}],
        "assumptions": [{"id": "a", "statement": "A", "prior_p_true": 0.9, "if_false": {"overrides": [{"ref": "metric:big", "set": {"rate_per_year": 0.0}}]}}],
    }
    return _settings(a)


def test_sensitivity_ranks_big_effect_first_with_crn():
    res = fc.sensitivity(_sens_settings(), runs=40, seed=5, horizon_days=1500, steps=30)
    assert res["ok"]
    rows = res["targets"][0]["rows"]
    labels = [r["label"] for r in rows]
    assert "big" in labels[0] or "假设" in labels[0]
    tiny = next(r for r in rows if "tiny" in r["label"])
    big = next(r for r in rows if "big" in r["label"] and r["kind"] == "param")
    assert big["score"] > tiny["score"] * 3
    assert [r["score"] for r in rows] == sorted((r["score"] for r in rows), reverse=True)
    asm = next(r for r in rows if r["kind"] == "assumption")
    assert asm["low"]["value"] == "不成立" and asm["high"]["p_reached"] == res["targets"][0]["baseline"]["p_reached"]  # 假设基线侧直接取基线
    assert res["meta"]["runs_per_scenario"] == 40 and not res["meta"]["truncated"]


def test_sensitivity_is_deterministic_and_target_validation():
    a = fc.sensitivity(_sens_settings(), runs=30, horizon_days=1500, steps=20)
    b = fc.sensitivity(_sens_settings(), runs=30, horizon_days=1500, steps=20)
    a["meta"].pop("elapsed_sec"), b["meta"].pop("elapsed_sec")
    assert a == b
    assert fc.sensitivity(_sens_settings(), target="el#ms", runs=20, horizon_days=1500, steps=20)["ok"]
    assert fc.sensitivity(_sens_settings(), target="ghost", runs=20)["ok"] is False
    assert fc.sensitivity({"anatomy_enabled": False})["ok"] is False


def test_sensitivity_budget_skips_unfinished_scenarios():
    def tick():
        t = iter(range(10 ** 6))
        return lambda: float(next(t))                  # 每次调用前进 1 秒

    # 基线 40 次约用掉 41 秒，预算 60 秒 → 基线完成，后面的情景作废并列入 skipped
    res = fc.sensitivity(_sens_settings(), runs=40, horizon_days=1500, steps=20, time_budget_sec=60, _clock=tick())
    assert res["ok"] and res["meta"]["truncated"] and res["meta"]["skipped"]
    assert res["meta"]["scenarios_done"] < res["meta"]["scenarios_total"]
    # 预算连基线都不够 → 明确失败，不给半截数据
    assert fc.sensitivity(_sens_settings(), runs=40, horizon_days=1500, steps=20, time_budget_sec=5, _clock=tick())["ok"] is False


# ── what-if ──────────────────────────────────────────────────────────


def test_apply_edits_all_kinds_without_mutation():
    s = _sens_settings()
    a = an.get_anatomy(s["causal_lines"][0])
    a["bottlenecks"] = [_bn([_path("p", 0.5, 10, 20, 30)])]
    s["event_sampling_enabled"] = True
    s["event_priors"] = [{"id": "ev", "rate_per_year": 1.0}]
    snap = copy.deepcopy(s)
    edits = [
        {"kind": "param", "element": "el", "holder": "metric:big", "name": "rate_per_year", "field": "low", "value": 0.01},
        {"kind": "current", "element": "el", "holder": "metric:big", "value": 123},
        {"kind": "duration", "element": "el", "bottleneck": "b", "path": "p", "field": "all", "value": 99},
        {"kind": "p_success", "element": "el", "bottleneck": "b", "path": "p", "value": 7},
        {"kind": "assumption", "element": "el", "id": "a", "p_true": 0.2},
        {"kind": "event_rate", "id": "ev", "rate": 3},
    ]
    new, applied, errors = fc.apply_edits(s, edits)
    assert s == snap and len(applied) == 6 and errors == []
    na = an.get_anatomy(new["causal_lines"][0])
    assert na["metrics"][0]["trend"]["params"]["rate_per_year"]["low"] == 0.01
    assert na["metrics"][0]["current"]["value"] == 123
    assert na["bottlenecks"][0]["resolution_paths"][0]["duration_days"] == {"low": 99, "mode": 99, "high": 99}
    assert na["bottlenecks"][0]["resolution_paths"][0]["p_success"] == 1.0     # 夹到 [0,1]
    assert na["assumptions"][0]["prior_p_true"] == 0.2 and new["event_priors"][0]["rate_per_year"] == 3


def test_apply_edits_bad_input_never_raises():
    s = _sens_settings()
    bad = [{"kind": "param", "element": "ghost"}, {"kind": "zzz"}, "x", {"kind": "param", "element": "el", "holder": "metric:big", "name": "nope", "value": 1},
           {"kind": "event_rate", "id": "none", "rate": 1}, {"kind": "assumption", "element": "el", "id": "a", "p_true": "x"}]
    new, applied, errors = fc.apply_edits(s, bad)
    assert applied == [] and len(errors) == 6 and new["causal_lines"] == s["causal_lines"]
    assert fc.apply_edits(s, None)[1:] == ([], [])


def test_what_if_equals_direct_recompute_and_shows_diff():
    s = _asm_settings(0.5)
    base = _fc(s, runs=200)
    edits = [{"kind": "assumption", "element": "el", "id": "a1", "p_true": 0.0}]
    wi = fc.what_if(s, None, edits, base=base)
    assert wi["ok"] and wi["comparable"]
    edited, _a, _e = fc.apply_edits(s, edits)
    direct = fc.run_forecast(edited, runs=200, seed=11, horizon_days=1000, steps=40)
    wi["result"]["meta"].pop("elapsed_sec"), direct["meta"].pop("elapsed_sec")
    assert wi["result"] == direct
    d = wi["diff"]["milestones"][0]
    assert d["delta_p50"] > 0 and d["p50_after"] > d["p50_before"]


def test_what_if_noop_edit_has_empty_diff_and_invalid_edit_fails():
    s = _asm_settings(0.5)
    base = _fc(s, runs=100)
    same = fc.what_if(s, None, [{"kind": "assumption", "element": "el", "id": "a1", "p_true": 0.5}], base=base)
    assert same["ok"] and not any(same["diff"].values())
    assert fc.what_if(s, None, [{"kind": "zzz"}], base=base)["ok"] is False
    assert fc.what_if(None, None, [])["ok"] is False
    no_base = fc.what_if(s, None, [{"kind": "assumption", "element": "el", "id": "a1", "p_true": 0.0}], runs=60, seed=2, horizon_days=1000, steps=20)
    assert no_base["ok"] and no_base["base_meta"]["seed"] == 2


# ── 监测清单 / 现实回填 ───────────────────────────────────────────────


def _watch_settings():
    a = {
        "bottlenecks": [_bn([_path("p1", 0.6, 100, 200, 300), _path("p2", 0.5, 100, 200, 300)], fallback=["p1", "p2"])],
        "milestones": [_ms_resolved()],
        "assumptions": [{"id": "a1", "statement": "界面可行", "prior_p_true": 0.5, "if_false": {"overrides": [{"ref": "bottleneck:b", "set": {"duration_scale": 4.0}}]}}],
        "signals": [{"id": "sig1", "watch": "看论文", "means": "走向 A"}],
    }
    return _settings(a)


def test_watchlist_combines_anatomy_signals_and_forecast_derived_items():
    s = _watch_settings()
    res = _fc(s, runs=300, horizon_days=800)
    items = fc.build_watchlist(s, res)
    keys = [i["key"] for i in items]
    assert "el#sig1" in keys and items[0]["source"] == "anatomy"
    kinds = {k.split(":")[1] for k in keys if k.startswith("fc:")}
    assert {"bottleneck", "assumption"} <= kinds
    bn = next(i for i in items if i["key"].startswith("fc:bottleneck"))
    assert "p1" in bn["means"] and "p2" in bn["means"]
    assert len(set(keys)) == len(keys) and len([i for i in items if i["source"] == "forecast"]) <= fc.MAX_WATCH_FORECAST
    assert [i["key"] for i in fc.build_watchlist(s, res)] == keys                    # 稳定、确定
    assert [i["key"] for i in fc.build_watchlist(s)] == ["el#sig1"]                  # 没有预测只剩已有信号


def test_watchlist_includes_sensitivity_items():
    s = _sens_settings()
    res = _fc(s, runs=100, horizon_days=1500)
    sens = fc.sensitivity(s, runs=30, horizon_days=1500, steps=20)
    items = fc.build_watchlist(s, res, sens)
    assert any(i["key"].startswith("fc:sens:") for i in items)


def test_signal_observations_and_reality_check_roundtrip(tmp_path):
    item = rc.record_reality_check(tmp_path, "sim", branch="main", step=3, predicted_summary="p", actual_outcome="o", verdict="matched",
                                   signals_observed=["el#sig1", "el#sig1", " ", 5, "fc:bottleneck:el#b"])
    assert item.signals_observed == ["el#sig1", "fc:bottleneck:el#b"]
    rc.record_reality_check(tmp_path, "sim", branch="main", step=7, predicted_summary="p", actual_outcome="o2", verdict="diverged", signals_observed=["el#sig1"])
    plain = rc.record_reality_check(tmp_path, "sim", branch="main", step=8, predicted_summary="p", actual_outcome="o3", verdict="matched")
    assert "signals_observed" not in plain.to_dict()                                   # 旧记录逐字节不变
    loaded = rc.load_all(tmp_path, "sim")
    obs = fc.signal_observations(loaded)
    assert obs["el#sig1"] == {"count": 2, "last_step": 7, "verdicts": {"matched": 1, "diverged": 1}}
    assert set(fc.signal_observations(loaded, ["fc:bottleneck:el#b"])) == {"fc:bottleneck:el#b"}
    assert rc.RealityCheck.from_dict({"signals_observed": "bad"}).signals_observed == []


# ── 视图 ─────────────────────────────────────────────────────────────


def test_forecast_view_filters_by_element_and_adds_banners():
    from world_simulator import element_view as ev

    s = _two_elements()
    res = _fc(s, runs=60)
    view = ev.build_forecast_view(res, "other", None, fc.build_watchlist(s, res))
    assert view["has_forecast"] and all(m["element"] == "other" for m in view["milestones"])
    assert any("LLM 先验" in b for b in view["banners"]) and view["honesty"] == fc.HONEST_NOTE
    assert ev.build_forecast_view(None, "x")["has_forecast"] is False
    assert ev.build_forecast_view({"ok": False, "reason": "r"}, "x")["reason"] == "r"


def test_time_precision_degraded_flag_from_history():
    st = SimpleNamespace(step=3, anatomy_trace=[{"kind": "time", "source": "fallback"}])
    ok = SimpleNamespace(step=4, anatomy_trace=[{"kind": "time", "source": "reported"}])
    res = fc.run_forecast(_bn_settings(), [ok, st], runs=20, horizon_days=500, steps=10)
    assert res["meta"]["time_precision_degraded"] is True and "降级" in res["meta"]["time_precision_note"]
    assert fc.run_forecast(_bn_settings(), [ok], runs=20, horizon_days=500, steps=10)["meta"]["time_precision_degraded"] is False


def test_result_is_json_serialisable_and_has_no_nan():
    res = _fc(_watch_settings(), runs=60)
    text = json.dumps(res, allow_nan=False)
    assert "Infinity" not in text


def test_auto_horizon_extends_for_long_paths_and_grid_clamps():
    s = _bn_settings(1.0, 3000, 6000, 8000)
    res = fc.run_forecast(s, runs=10, steps=10)
    assert res["meta"]["horizon_auto"] and res["meta"]["horizon_days"] >= 10000
    res2 = fc.run_forecast(_bn_settings(), runs=10, horizon_days=1, steps=10 ** 6)
    assert res2["meta"]["horizon_days"] == 30.0 and res2["meta"]["steps"] == fc.MAX_STEPS


# ── 变异检查补出的断言缺口 ────────────────────────────────────────────


def test_quantile_between_finite_and_inf_is_inf():
    v = sorted([1.0, 2.0, 3.0, math.inf, math.inf])
    assert fc._quantile(v, 0.7) is None            # 落在 3.0 与 inf 之间 → 视野内未达成，不能返回 3.0
    assert fc._quantile(v, 0.5) == 3.0


def test_point_mode_fixes_assumptions_to_the_likelier_side():
    likely = fc.run_forecast(_asm_settings(0.9), runs=60, seed=1, horizon_days=1000, steps=20, point=True)["conditional"][0]
    unlikely = fc.run_forecast(_asm_settings(0.2), runs=60, seed=1, horizon_days=1000, steps=20, point=True)["conditional"][0]
    assert (likely["n_true"], likely["n_false"]) == (60, 0)
    assert (unlikely["n_true"], unlikely["n_false"]) == (0, 60)


def test_random_numbers_are_keyed_by_identity_not_prefix():
    assert fc._u("b", "param/el/metric:x.rate/1") == fc._u("b", "param/el/metric:x.rate/1")
    assert fc._u("b", "param/el/metric:x.rate/1") != fc._u("b", "param/el/metric:y.rate/1")
    assert fc._u("b", "redraw/el/b/p1/ok") != fc._u("b", "redraw/el/b/p2/ok")
    assert fc._u("b1", "t") != fc._u("b2", "t")


def test_truncation_never_drops_below_the_minimum_run_count():
    t = iter(range(10 ** 6))
    res = _fc(_bn_settings(0.6, 100, 300, 600), runs=500, time_budget_sec=1, _clock=lambda: float(next(t)) * 1000)
    assert res["meta"]["runs_done"] == fc.MIN_RUNS_BEFORE_TRUNCATE and res["meta"]["truncated"]
    small = _fc(_bn_settings(), runs=10, time_budget_sec=1, _clock=lambda: float(next(t)) * 1000)
    assert small["meta"]["runs_done"] == 10 and not small["meta"]["truncated"]
