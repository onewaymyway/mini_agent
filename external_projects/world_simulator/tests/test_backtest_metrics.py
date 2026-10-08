"""tests/test_backtest_metrics.py — 第二十四轮 A8：指标级回测（拟合 / 防泄漏 / 评分 / 汇总 / 置信衔接）。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §8 A8。无需 LLM、无需联网。
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
from world_simulator import backtest_fit as bf
from world_simulator import backtest_metrics as bm
from world_simulator import forecast_brief as fb

from test_forecast_brief import _anat, _bn, _deds, _line, _metric, _settings

FAST = dict(runs=60, seed=7)


def _case(**over):
    data = {
        "id": "syn", "title": "合成指数", "family": "exponential", "kind": "series", "verified": False,
        "series": [{"id": "v", "name": "v", "unit": "x", "scored": True, "scale": "log",
                    "points": [[2000 + i, 100 * 1.3 ** i] for i in range(15)]}],
        "model": {"metrics": [{"series": "v", "trend": "exponential"}]},
        "cutoffs": [2007],
    }
    data.update(over)
    return bm.parse_case(data)


# ── 拟合 ─────────────────────────────────────────────────────────────


def test_fit_exponential_recovers_rate_and_degenerates_to_point():
    r = bf.fit_exponential([(t, 5 * 1.25 ** t) for t in range(8)])
    p = r["rate_per_year"]
    assert p["value"] == pytest.approx(0.25) and p["high"] - p["low"] < 1e-9


def test_fit_exponential_noise_gives_interval_and_needs_points():
    pts = [(0, 10), (1, 14), (2, 17), (3, 30), (4, 36), (5, 70)]
    p = bf.fit_exponential(pts)["rate_per_year"]
    assert p["low"] < p["value"] < p["high"]
    with pytest.raises(bf.FitError):
        bf.fit_exponential(pts[:2])
    with pytest.raises(bf.FitError):
        bf.fit_exponential([(0, 1), (1, -1), (2, 3)])


def test_fit_logistic_recovers_cap_and_respects_bounds():
    cap, r = 80.0, 0.6
    pts = [(t, cap / (1 + 30 * math.exp(-r * t))) for t in range(0, 14)]
    f = bf.fit_logistic(pts)
    assert f["cap"]["low"] <= cap * 1.02 and f["cap"]["high"] >= cap * 0.98
    assert f["cap"]["value"] == pytest.approx(cap, rel=0.05) and f["rate_per_year"]["value"] == pytest.approx(r, rel=0.05)
    capped = bf.fit_logistic([(0, 10), (1, 15), (2, 22), (3, 30)], cap_max=60)
    assert capped["cap"]["high"] <= 60 + 1e-9
    with pytest.raises(bf.FitError):
        bf.fit_logistic([(0, 10), (1, 20), (2, 30), (3, 40)], cap_max=40)  # 上限不高于 1.02×最大值


def test_fit_learning_curve_pairs_by_t():
    q = {t: 2.0 ** t for t in range(10)}
    cost = [(t, 100 * q[t] ** -0.3) for t in range(10)]
    f = bf.fit_learning_curve(cost, list(q.items()))
    assert f["b"]["value"] == pytest.approx(0.3)
    with pytest.raises(bf.FitError):
        bf.fit_learning_curve(cost[:3], list(q.items()))


def test_fit_schedule_overrun_rule():
    f = bf.fit_schedule_overrun(baseline_announced=2000, baseline_completion=2004, latest_completion=2010, cutoff=2006)
    assert f["overrun_factor"] == pytest.approx(10 / 4)
    assert f["low"] == pytest.approx(4 * 365.25) and f["mode"] == pytest.approx(4 * 365.25 * 2.5) and f["high"] == pytest.approx(4 * 365.25 * 6.25)
    flat = bf.fit_schedule_overrun(baseline_announced=2000, baseline_completion=2004, latest_completion=2004, cutoff=2002)
    assert flat["overrun_factor"] == bf.OVERRUN_FLOOR  # 没超期过也留下限
    with pytest.raises(bf.FitError):
        bf.fit_schedule_overrun(baseline_announced=2000, baseline_completion=2004, latest_completion=2005, cutoff=2006)


def test_t80_and_dates():
    assert bf.t80(1) > bf.t80(30) > bf.t80(500) == pytest.approx(1.2816)
    assert bf.to_decimal_year("2018-07-01") == pytest.approx(2018.495, abs=0.01)
    assert bf.to_decimal_year(2020) == 2020.0
    with pytest.raises(bf.FitError):
        bf.to_decimal_year("2018-13-01")


# ── 案例解析 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("over,needle", [
    ({"family": "nope"}, "family"),
    ({"kind": "schedule"}, "schedule"),
    ({"cutoffs": []}, "cutoffs"),
    ({"cutoffs": [2050]}, "切成了空"),
    ({"series": [{"id": "v", "scored": True, "scale": "log", "points": [[2000, -1], [2001, 2], [2002, 3]]}]}, "为正"),
    ({"model": {"metrics": [{"series": "zzz", "trend": "exponential"}]}}, "不存在"),
    ({"model": {"metrics": [{"series": "v", "trend": "magic"}]}}, "不支持的趋势"),
    ({"family": "logistic"}, "不一致"),
])
def test_parse_case_rejects_bad_input(over, needle):
    with pytest.raises(bm.BacktestMetricError) as e:
        _case(**over)
    assert needle in str(e.value)


def test_shipped_cases_all_parse_and_are_unverified_and_cover_four_families():
    cases = bm.load_cases()
    assert len(cases) >= 4 and {c.family for c in cases} >= {"exponential", "logistic", "learning_curve", "project_schedule"}
    assert all(not c.verified and c.provenance for c in cases)  # 如实：数值没有人工逐点核对


def test_schedule_case_requires_cutoff_inside_project():
    data = {"id": "p", "title": "p", "family": "project_schedule", "kind": "schedule", "cutoffs": ["2030-01-01"],
            "project": {"name": "p", "baseline": {"announced": "2000-01-01", "completion": "2004-01-01"}, "announcements": [], "actual": "2010-01-01"}}
    with pytest.raises(bm.BacktestMetricError):
        bm.parse_case(data)


# ── 防泄漏 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", bm.load_cases(), ids=lambda c: c.id)
def test_leak_canary_passes_for_every_shipped_case(case):
    for cut in case.cutoffs:
        truth = bm.extract_truth(case, cut)
        bm.leak_check(case, cut, bm.future_horizon_days(case, cut, truth))


def test_pack_ignores_the_future_and_only_uses_history():
    c = _case()
    a = bm.build_pack(c, 2007, future_horizon_days=2000)
    assert a.history_counts == {"v": 8}
    c2 = copy.deepcopy(c)
    c2.series[0].points = [(t, v if t <= 2007 else v * 99) for t, v in c2.series[0].points]
    assert bm.build_pack(c2, 2007, future_horizon_days=2000).digest() == a.digest()


def test_canary_catches_a_leaky_pack_builder(monkeypatch):
    c = _case()
    real = bm._history
    monkeypatch.setattr(bm, "_history", lambda spec, ct: real(spec, ct + 3))  # 故意多拿未来 3 年
    with pytest.raises(bm.BacktestLeak):
        bm.leak_check(c, 2007, 2000)


def test_schedule_pack_drops_announcements_after_cutoff():
    c = next(x for x in bm.load_cases() if x.id == "project_vogtle3")
    early = bm.build_pack(c, "2018-02-28", future_horizon_days=5000)
    late = bm.build_pack(c, "2021-10-31", future_horizon_days=5000)
    assert early.history_counts["announcements"] == 1 and late.history_counts["announcements"] == 2
    assert early.fits["schedule"]["remaining_days"] > late.fits["schedule"]["remaining_days"]


# ── 评分 ─────────────────────────────────────────────────────────────


def _row(days, p10, p50, p90):
    return {"days": days, "p10": p10, "p50": p50, "p90": p90}


def test_band_at_interpolates_log_exactly_for_exponential_and_none_outside():
    days = [0.0, 100.0, 200.0]
    vals = [10.0, 100.0, 1000.0]
    row = _row(days, vals, vals, vals)
    assert bm.band_at(row, 50)["p50"] == pytest.approx(10 ** 1.5)
    assert bm.band_at(row, 250) is None and bm.band_at(row, -1) is None
    neg = _row(days, [-1.0, 0.0, 1.0], [-1.0, 0.0, 1.0], [-1.0, 0.0, 1.0])
    assert bm.band_at(neg, 50)["p10"] == pytest.approx(-0.5)  # 非正值退回线性
    gap = _row(days, [1.0, None, 3.0], [1.0, None, 3.0], [1.0, None, 3.0])
    assert bm.band_at(gap, 50) is None


def test_pinball_is_asymmetric():
    assert bm.pinball(10, 8, 0.9) == pytest.approx(1.8) and bm.pinball(10, 8, 0.1) == pytest.approx(0.2)
    assert bm.pinball(8, 10, 0.9) == pytest.approx(0.2)


def test_exact_exponential_series_is_fully_covered_and_deterministic():
    c = _case()
    a, b = bm.run_case(c, 2007, **FAST), bm.run_case(c, 2007, **FAST)
    assert a["ok"] and a["run_coverage"] == 1.0
    assert a["series"][0]["mean_abs_log_error"] < 1e-3
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_wrong_regime_is_reported_as_not_covered():
    pts = [[2000 + i, 100 * 1.3 ** i] for i in range(8)] + [[2008 + i, 100 * 1.3 ** 7 * 1.02 ** (i + 1)] for i in range(8)]
    r = bm.run_case(_case(series=[{"id": "v", "name": "v", "unit": "x", "scored": True, "scale": "log", "points": pts}]), 2007, **FAST)
    s = r["series"][0]
    assert r["run_coverage"] == 0.0 and s["below"] == s["n_scored"] and s["mean_abs_log_error"] > 0.3


def test_series_scoring_uses_p10_p90_bounds_and_pinball():
    spec = bm.SeriesSpec("v", "v", "x", True, "linear", [])
    row = {"days": [0.0, 365.25 * 10], "p10": [8.0, 8.0], "p50": [10.0, 10.0], "p90": [12.0, 12.0]}
    truth = [(1, 9.0), (2, 7.0), (3, 13.0), (4, 11.9), (5, 8.0)]
    sc = bm._score_series(spec, row, truth, 0.0, 10.0)
    assert sc["covered"] == 3 and sc["below"] == 1 and sc["above"] == 1 and sc["coverage"] == pytest.approx(0.6)
    assert sc["pinball"] > 0 and sc["width_ratio"] == pytest.approx(0.4)
    far = bm._score_series(spec, row, [(50, 9.0)], 0.0, 10.0)
    assert far["n_scored"] == 0 and far["n_out_of_horizon"] == 1 and far["coverage"] is None


def test_milestone_positions():
    row = {"status": "pending", "p_reached": 1.0, "p10": 100.0, "p50": 200.0, "p90": 300.0}
    pos = lambda d: bm._score_milestone(row, d, "m")["position"]  # noqa: E731
    assert (pos(50), pos(150), pos(250), pos(400)) == ("below_p10", "p10_p50", "p50_p90", "above_p90")
    m = bm._score_milestone(row, 150, "m")
    assert m["inside"] is True and m["p50_error_days"] == 50
    assert bm._score_milestone(None, 1, "m")["position"] == "unscored"


def test_real_case_smoke_series_and_schedule():
    cases = {c.id: c for c in bm.load_cases()}
    r = bm.run_case(cases["cpu_transistors"], 1982, **FAST)
    assert r["ok"] and r["series"][0]["n_scored"] >= 3 and r["pack_digest"]
    v = bm.run_case(cases["project_vogtle3"], "2021-10-31", **FAST)
    assert v["ok"] and v["milestones"][0]["truth_day"] > 0 and v["run_coverage"] in (0.0, 1.0)


# ── 汇总与判定 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("cov,n,v", [
    (0.3, 6, "narrow_severe"), (0.5, 6, "narrow"), (0.69, 6, "narrow"), (0.7, 6, "ok"), (0.95, 6, "ok"),
    (0.96, 6, "wide"), (0.1, 2, "insufficient"), (None, 9, "insufficient"),
])
def test_verdict_thresholds(cov, n, v):
    assert bm.verdict_for(cov, n) == v


def test_wilson_interval_sane():
    lo, hi = bm.wilson(8, 10)
    assert 0 < lo < 0.8 < hi < 1 and bm.wilson(0, 0) is None


def _fake(cid, fam, cov):
    return {"ok": True, "case_id": cid, "family": fam, "cutoff": 1, "verified": False, "run_coverage": cov,
            "series": [{"points": [{"truth": 1, "p10": 0, "p90": 2, "covered": cov >= 0.5}], "pinball": 0.1, "mape_p50": 0.2, "width_ratio": 1.5}], "milestones": []}


def _summary(covs):
    res = [_fake(f"c{i}", f, c) for i, (f, c) in enumerate(covs)]
    return bm.summarize(res, now="2026-01-01T00:00:00Z")


def test_summarize_groups_by_family_and_counts_failures():
    res = [_fake("a", "exponential", 0.0), _fake("b", "exponential", 1.0), _fake("c", "logistic", 0.5), {"ok": False, "case_id": "x", "cutoff": 1, "error": "boom"}]
    s = bm.summarize(res, now="t")
    assert s["cases"] == 3 and s["overall"]["coverage"] == pytest.approx(0.5) and s["by_family"]["exponential"]["coverage"] == 0.5
    assert s["failed"][0]["error"] == "boom" and s["unverified_cases"] == ["a", "b", "c"] and s["caveats"]


def test_assess_scopes_family_overall_thin_and_legacy():
    anat = _anat()  # 该夹具元素的趋势族 = exponential + project_schedule
    assert bm.families_for_anatomy(anat) == ["exponential", "project_schedule"]
    s_family = _summary([("project_schedule", 0.0)] * 3 + [("exponential", 1.0)] * 2)
    a = bm.assess_for_element(s_family, anat)
    assert a["scope"] == "family" and a["verdict"] == "narrow_severe" and a["cases"] == 5 and a["coverage"] == pytest.approx(0.4)
    s_overall = _summary([("logistic", 0.2)] * 2 + [("learning_curve", 0.2)] * 2)
    a = bm.assess_for_element(s_overall, anat)
    assert a["scope"] == "overall" and a["verdict"] == "narrow_severe"
    thin = bm.assess_for_element(_summary([("logistic", 0.2)] * 2), anat)
    assert thin["scope"] == "thin" and thin["verdict"] == "insufficient"
    assert bm.assess_for_element({"cases": 2}, anat)["legacy"] is True
    assert bm.assess_for_element(None, anat)["has_data"] is False and bm.assess_for_element({"cases": 0}, anat)["has_data"] is False


def test_families_for_anatomy_maps_trend_kinds():
    raw = {"metrics": [{"id": "a", "current": {"value": 1}, "trend": {"kind": "logistic", "params": {"cap": {"value": 5}, "rate_per_year": {"value": 1}}}},
                       {"id": "b", "current": {"value": 1}, "trend": {"kind": "exponential", "params": {"rate_per_year": {"value": 0.1}}}}]}
    assert bm.families_for_anatomy(an.normalize_anatomy(raw)) == ["logistic", "exponential"]
    assert bm.families_for_anatomy(None) == []


# ── 与置信等级衔接 ───────────────────────────────────────────────────


def _conf(backtest):
    s = _settings(_line(anatomy=_anat(status="reviewed")))
    return fb.confidence(s, "el", backtest=backtest)


def test_confidence_unchanged_without_or_with_legacy_backtest():
    none = _conf(None)
    assert _deds(none)["no_backtest"]["points"] == 10
    assert "backtest_narrow" not in _deds(_conf({"cases": 2})) and "no_backtest" not in _deds(_conf({"cases": 2}))


def test_confidence_deducts_and_caps_when_same_family_is_severely_narrow():
    c = _conf(_summary([("project_schedule", 0.0)] * 3))
    d = _deds(c)["backtest_narrow"]
    assert d["points"] == 15 and "机械拟合" in d["detail"]
    assert c["level"] != "high" and any(x["rule"] == "backtest_narrow" for x in c["caps"])
    assert c["inputs"]["backtest"]["scope"] == "family"


def test_confidence_overall_scope_halves_points_and_does_not_cap():
    c = _conf(_summary([("logistic", 0.0)] * 3))
    assert _deds(c)["backtest_narrow"]["points"] == 8 and not any(x["rule"] == "backtest_narrow" for x in c["caps"])


def test_confidence_ok_and_thin_cases():
    assert "backtest_narrow" not in _deds(_conf(_summary([("project_schedule", 0.8)] * 3)))
    assert _deds(_conf(_summary([("project_schedule", 0.0)] * 2)))["backtest_thin"]["points"] == 5


# ── 开关与读取 ───────────────────────────────────────────────────────


def test_feedback_is_opt_in_and_reads_user_summary(tmp_path):
    s = {"element_modeling_enabled": True, "anatomy_enabled": True}
    assert an.get_params(s)["backtest_feedback"] is False
    assert bm.feedback_summary(s, reports_dir=tmp_path) is None
    on = {**s, "anatomy_params": {"backtest_feedback": True}}
    assert bm.feedback_summary(on, reports_dir=tmp_path) is None  # 开了但还没跑过回测 → 当作没有
    bm.write_json(tmp_path / "backtest_metric" / bm.SUMMARY_NAME, _summary([("exponential", 0.5)] * 3))
    assert bm.feedback_summary(on, reports_dir=tmp_path)["cases"] == 3
    assert bm.feedback_summary(s, reports_dir=tmp_path) is None  # 文件在、开关关 → 仍不使用
    assert an.invalid_param_keys({"anatomy_params": {"backtest_feedback": "yes"}}) == ["backtest_feedback"]


def test_load_summary_tolerates_garbage(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json", encoding="utf-8")
    assert bm.load_summary(p) is None
    p.write_text(json.dumps({"cases": 3}), encoding="utf-8")
    assert bm.load_summary(p) is None


def test_shipped_baseline_is_loadable_and_consistent():
    b = bm.load_summary(bm.CASE_DIR / "baseline_summary.json")
    assert b and b["cases"] == len(bm.load_cases()) and b["overall"]["runs"] == sum(len(c.cutoffs) for c in bm.load_cases())
    assert b["verdict"] in bm.VERDICT_LABELS and b["caveats"]


def test_forecast_brief_run_uses_summary_when_passed():
    s = _settings(_line(anatomy=_anat(status="reviewed")))
    brief = fb.run_brief(s, None, runs=60, time_budget_sec=0, with_sensitivity=False, backtest=_summary([("project_schedule", 0.0)] * 3))
    assert brief["ok"]
    assert "backtest_narrow" in _deds(brief["elements"][0]["confidence"])


def test_cli_metric_check_runs(capsys, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "entrypoints"))
    import backtest as ep

    monkeypatch.setattr(sys, "argv", ["backtest.py", "metric-check", str(bm.CASE_DIR / "cpu_transistors.yaml")])
    assert ep.main() == 0
    assert "金丝雀通过" in capsys.readouterr().out
