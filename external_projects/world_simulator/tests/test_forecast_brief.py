"""tests/test_forecast_brief.py — 第二十四轮 A7：预测简报与置信等级。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.7 / §5.8 / §8 A7。无需真实 LLM。
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import anatomy as an
from world_simulator import evidence as evm
from world_simulator import forecast as fc
from world_simulator import forecast_brief as fb

SRC = {"state": "sourced", "evidence_ids": ["ev_0001"]}
CONF = {"state": "user_confirmed"}


def _metric(mid="cost", basis=None, cur_basis=None, low=-0.25, high=-0.05):
    m = {
        "id": mid, "name": mid, "unit": "u", "current": {"value": 200, "as_of": "2026-06"},
        "target": {"op": "<=", "value": 120},
        "trend": {"kind": "exponential", "params": {"rate_per_year": {"value": -0.15, "low": low, "high": high}}},
    }
    if basis:
        m["basis"] = basis
    if cur_basis:
        m["current"]["basis"] = cur_basis
    return m


def _bn(basis=None, paths=None, bid="iface"):
    b = {
        "id": bid, "name": bid, "status": "open", "fallback": ["coat", "newel"],
        "resolution_paths": paths or [
            {"id": "coat", "p_success": 0.5, "duration_days": {"low": 540, "mode": 900, "high": 1500}},
            {"id": "newel", "p_success": 0.25, "duration_days": {"low": 900, "mode": 1500, "high": 2400}},
        ],
    }
    if basis:
        b["basis"] = basis
    return b


def _asm(basis=None, p=0.5, with_override=True):
    a = {"id": "a1", "statement": "界面可工程化", "prior_p_true": p}
    if with_override:
        a["if_false"] = {"overrides": [{"ref": "bottleneck:iface", "set": {"status": "open"}}]}
    if basis:
        a["basis"] = basis
    return a


def _anat(metrics=None, bns=None, asms=None, status="draft", key=True, milestones=None, signals=None, extra=None):
    raw = {
        "metrics": metrics if metrics is not None else [_metric()],
        "bottlenecks": bns if bns is not None else [_bn()],
        "milestones": milestones if milestones is not None else [
            {"id": "pilot", "name": "中试线跑通", "criteria": {"bottleneck": "iface", "status": "resolved"}},
            {"id": "cheap", "name": "成本达标", "criteria": {"all": [{"metric": "cost", "op": "<=", "value": 120}, {"bottleneck": "iface", "status": "resolved"}]}},
        ],
        "assumptions": asms if asms is not None else [_asm()],
        "meta": {"key": key, "anatomy_status": status},
    }
    if signals:
        raw["signals"] = signals
    raw.update(extra or {})
    return an.normalize_anatomy(raw)


def _line(lid="el", label="固态电池", anatomy=None, **kw):
    return {"id": lid, "label": label, "kind": "element", "element_type": "technology", "anatomy": anatomy if anatomy is not None else _anat(), **kw}


def _settings(*lines, **extra):
    s = {"element_modeling_enabled": True, "anatomy_enabled": True, "causal_lines": list(lines) or [_line()]}
    s.update(extra)
    return s


def _brief(s, **kw):
    kw.setdefault("runs", 150)
    kw.setdefault("sens_runs", 50)
    kw.setdefault("seed", 7)
    kw.setdefault("horizon_days", 3650)
    kw.setdefault("time_budget_sec", 0)
    kw.setdefault("sens_budget_sec", 0)
    return fb.run_brief(s, [], **kw)


def _deds(conf):
    return {d["rule"]: d for d in conf["deductions"]}


# ── 小工具 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("days, text", [
    (None, "视野内未达成"), (0, "0 天"), (45, "45 天"), (59.4, "59 天"), (60, "约 2 个月"), (365, "约 12 个月"),
    (729, "约 24 个月"), (730, "约 2.0 年"), (3650, "约 10.0 年"), ("x", "—"), (float("nan"), "—"),
])
def test_fmt_days(days, text):
    assert fb.fmt_days(days) == text


# ── 参数（conf_*） ───────────────────────────────────────────────────


def test_conf_params_defaults_and_invalid_fallback():
    p = an.get_params(None)
    assert (p["conf_high_min"], p["conf_medium_min"], p["conf_grounded_ok"], p["conf_grounded_low"], p["conf_wide_ratio"]) == (75, 45, 0.6, 0.25, 3.0)
    bad = {"anatomy_params": {"conf_high_min": 101, "conf_medium_min": True, "conf_grounded_ok": 1.5, "conf_grounded_low": -0.1, "conf_wide_ratio": 1.0}}
    assert set(an.invalid_param_keys(bad)) == {"conf_high_min", "conf_medium_min", "conf_grounded_ok", "conf_grounded_low", "conf_wide_ratio"}
    assert an.get_params(bad)["conf_high_min"] == 75
    good = {"anatomy_params": {"conf_high_min": 90, "conf_grounded_ok": 0.8, "conf_wide_ratio": 5}}
    assert an.invalid_param_keys(good) == [] and an.get_params(good)["conf_high_min"] == 90 and an.get_params(good)["conf_wide_ratio"] == 5.0


def test_thresholds_clamp_reversed_config():
    s = _settings(anatomy_params={"conf_high_min": 40, "conf_medium_min": 60, "conf_grounded_ok": 0.3, "conf_grounded_low": 0.5})
    th = fb._thresholds(s)
    assert th["medium_min"] == 40 and th["grounded_low"] == 0.3


# ── 置信等级：逐条规则 ───────────────────────────────────────────────


def test_all_llm_prior_is_capped_low_with_reason():
    c = fb.confidence(_settings(), "el")
    d = _deds(c)
    assert d["grounded_share"]["points"] == 35 and "0/" in d["grounded_share"]["detail"]
    assert c["level"] == "low" and {x["rule"] for x in c["caps"]} == {"grounded_share", "unreviewed"}


def test_grounded_middle_band_deducts_without_low_cap():
    # 指标(条目+现值各算一个)、瓶颈、假设共 4 个单位：给 2 个有依据 = 50%，落在 [25%, 60%)
    a = _anat(metrics=[_metric(basis=SRC, cur_basis=CONF)], status="reviewed")
    c = fb.confidence(_settings(_line(anatomy=a)), "el")
    d = _deds(c)
    assert d["grounded_share"]["points"] == 20 and not c["caps"]
    assert c["inputs"]["grounded"]["grounded"] == 2 and c["inputs"]["grounded"]["total"] == 4


def test_grounded_share_at_or_above_ok_has_no_deduction():
    a = _anat(metrics=[_metric(basis=SRC, cur_basis=CONF)], bns=[_bn(basis=SRC)], asms=[_asm()], status="reviewed")  # 3/4 = 75% ≥ 60%
    c = fb.confidence(_settings(_line(anatomy=a)), "el")
    assert "grounded_share" not in _deds(c)


def test_user_edited_counts_as_grounded():
    a = _anat(metrics=[_metric(basis={"state": "user_edited"}, cur_basis={"state": "user_edited"})], bns=[_bn(basis={"state": "user_edited"})], asms=[_asm(basis={"state": "user_edited"})], status="reviewed")
    assert fb.confidence(_settings(_line(anatomy=a)), "el")["inputs"]["grounded"]["share"] == 1.0


def test_element_without_key_items_is_low():
    a = _anat(metrics=[], bns=[], asms=[], status="reviewed", milestones=[{"id": "m", "name": "m", "criteria": {"component": "c", "min_readiness": 0.5}}], extra={"components": [{"id": "c", "name": "c", "readiness": 0.1}]})
    c = fb.confidence(_settings(_line(anatomy=a)), "el")
    assert c["level"] == "low" and "没有指标" in _deds(c)["grounded_share"]["detail"]


def test_unreviewed_deducts_and_caps_medium_reviewed_does_not():
    good = dict(metrics=[_metric(basis=SRC, cur_basis=SRC)], bns=[_bn(basis=SRC)], asms=[_asm(basis=SRC)])
    draft = fb.confidence(_settings(_line(anatomy=_anat(**good, status="draft"))), "el", backtest={"cases": 1})
    assert _deds(draft)["unreviewed"]["points"] == 10 and draft["level"] == "medium"  # 分数是高，但被封顶
    assert draft["score"] >= 75
    rev = fb.confidence(_settings(_line(anatomy=_anat(**good, status="reviewed"))), "el", backtest={"cases": 1})
    assert "unreviewed" not in _deds(rev) and rev["level"] == "high"


def test_high_is_attainable_only_with_everything_in_place():
    a = _anat(metrics=[_metric(basis=SRC, cur_basis=SRC, low=-0.2, high=-0.1)], bns=[_bn(basis=SRC)], asms=[_asm(basis=SRC)], status="reviewed")
    s = _settings(_line(anatomy=a))
    assert fb.confidence(s, "el", backtest={"cases": 3})["level"] == "high"
    c = fb.confidence(s, "el")  # 没有回测：-10，仍可到高（90 分）
    assert _deds(c)["no_backtest"]["points"] == 10 and c["score"] == 90 and c["level"] == "high"


def test_backtest_rule_needs_at_least_one_case():
    s = _settings()
    assert "no_backtest" in _deds(fb.confidence(s, "el", backtest={"cases": 0}))
    assert "no_backtest" in _deds(fb.confidence(s, "el", backtest="x"))
    assert "no_backtest" not in _deds(fb.confidence(s, "el", backtest={"cases": 2}))


def _rec(eid="el", ev_id="ev_0001", days_ago=0, **kw):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return evm.normalize_record({"ev_id": ev_id, "element_id": eid, "claim": "c", "source_url": "https://example.org/x", "retrieved_at": ts, **kw})


def test_stale_evidence_only_counts_when_used_and_when_evidence_given():
    a = _anat(metrics=[_metric(basis=SRC, cur_basis=SRC)], status="reviewed")
    s = _settings(_line(anatomy=a))
    assert "stale_evidence" not in _deds(fb.confidence(s, "el"))  # 没传证据库：不查，不猜
    assert fb.confidence(s, "el")["inputs"]["evidence_checked"] is False
    fresh = fb.confidence(s, "el", evidence=[_rec(days_ago=1)])
    assert "stale_evidence" not in _deds(fresh) and fresh["inputs"]["evidence_checked"] is True
    stale = fb.confidence(s, "el", evidence=[_rec(days_ago=400)])
    assert _deds(stale)["stale_evidence"]["points"] == 10 and stale["inputs"]["stale_evidence"] == 1
    unused = fb.confidence(s, "el", evidence=[_rec(ev_id="ev_0009", days_ago=400)])
    assert "stale_evidence" not in _deds(unused)  # 过期但没被任何字段引用


def test_stale_evidence_respects_ttl_param():
    a = _anat(metrics=[_metric(basis=SRC, cur_basis=SRC)], status="reviewed")
    s = _settings(_line(anatomy=a), anatomy_params={"research_ttl_days": 500})
    assert "stale_evidence" not in _deds(fb.confidence(s, "el", evidence=[_rec(days_ago=400)]))


def test_wide_intervals_rule_uses_median_ratio_and_threshold():
    wide = _anat(metrics=[_metric(low=0.01, high=0.5)], bns=[_bn(paths=[{"id": "p", "p_success": 0.5, "duration_days": {"low": 10, "mode": 100, "high": 500}}])])
    c = fb.confidence(_settings(_line(anatomy=wide)), "el")
    assert _deds(c)["wide_intervals"]["points"] == 10 and c["inputs"]["widths"]["median_ratio"] >= 3
    s = _settings(_line(anatomy=wide), anatomy_params={"conf_wide_ratio": 1000})
    assert "wide_intervals" not in _deds(fb.confidence(s, "el"))
    narrow = _anat(metrics=[_metric(low=1.0, high=1.5)], bns=[_bn(paths=[{"id": "p", "p_success": 0.5, "duration_days": {"low": 90, "mode": 100, "high": 120}}])])
    assert "wide_intervals" not in _deds(fb.confidence(_settings(_line(anatomy=narrow)), "el"))


def test_wide_intervals_skips_non_positive_low():
    a = _anat(metrics=[_metric(low=-0.25, high=-0.05)], bns=[_bn(paths=[{"id": "p", "p_success": 0.5, "duration_days": {"low": 0, "mode": 100, "high": 500}}])])
    w = fb._widths(a)
    assert w["median_ratio"] is None  # low <= 0 的区间不能取 high/low


def test_point_estimate_rule():
    pts = _anat(metrics=[{"id": "m", "name": "m", "current": {"value": 1}, "trend": {"kind": "exponential", "params": {"rate_per_year": {"value": 0.1}}}}],
                bns=[_bn(paths=[{"id": "p", "p_success": 0.5, "duration_days": {"mode": 100}}])])
    c = fb.confidence(_settings(_line(anatomy=pts)), "el")
    assert _deds(c)["point_estimates"]["points"] == 10 and c["inputs"]["widths"]["point_share"] == 1.0
    ranged = _anat()
    assert "point_estimates" not in _deds(fb.confidence(_settings(_line(anatomy=ranged)), "el"))


def test_forecast_meta_rules_time_precision_and_truncation():
    s = _settings()
    c = fb.confidence(s, "el", forecast_meta={"time_precision_degraded": True, "time_precision_note": "N 步缺 elapsed_days", "truncated": True, "runs_done": 50, "runs_requested": 1000})
    d = _deds(c)
    assert d["time_precision"]["points"] == 10 and "N 步缺" in d["time_precision"]["detail"]
    assert d["truncated"]["points"] == 5 and "50/1000" in d["truncated"]["detail"]
    clean = fb.confidence(s, "el", forecast_meta={"time_precision_degraded": False, "truncated": False})
    assert "time_precision" not in _deds(clean) and "truncated" not in _deds(clean)


def test_level_thresholds_are_configurable_and_score_is_floor_zero():
    a = _anat(metrics=[_metric(basis=SRC, cur_basis=SRC)], bns=[_bn(basis=SRC)], asms=[_asm(basis=SRC)], status="reviewed")
    base = _settings(_line(anatomy=a))
    assert fb.confidence(base, "el")["level"] == "high"  # 90
    strict = _settings(_line(anatomy=a), anatomy_params={"conf_high_min": 95, "conf_medium_min": 91})
    assert fb.confidence(strict, "el")["level"] == "low"
    lenient = _settings(_line(), anatomy_params={"conf_medium_min": 1, "conf_grounded_low": 0.0, "conf_grounded_ok": 0.0})
    c = fb.confidence(lenient, "el")
    assert c["score"] >= 0 and c["level"] == "medium"  # 未审阅仍封顶中
    assert 0 <= fb.confidence(_settings(), "el", forecast_meta={"time_precision_degraded": True, "truncated": True})["score"] <= 100


def test_confidence_does_not_mutate_inputs_and_is_json_safe():
    s = _settings()
    before = copy.deepcopy(s)
    ev = [_rec(days_ago=400)]
    ev_before = copy.deepcopy(ev)
    c = fb.confidence(s, "el", evidence=ev)
    assert s == before and ev == ev_before
    json.dumps(c, ensure_ascii=False)


def test_overall_confidence_takes_the_worst_element():
    strong = _anat(metrics=[_metric(basis=SRC, cur_basis=SRC)], bns=[_bn(basis=SRC)], asms=[_asm(basis=SRC)], status="reviewed")
    s = _settings(_line("a", "强", strong), _line("b", "弱", _anat()))
    ca, cb = fb.confidence(s, "a"), fb.confidence(s, "b")
    assert ca["level"] == "high" and cb["level"] == "low"
    o = fb.overall_confidence([("a", "强", ca), ("b", "弱", cb)])
    assert o["level"] == "low" and o["worst"] == {"id": "b", "label": "弱"} and o["deductions"] == cb["deductions"]
    assert [p["level"] for p in o["per_element"]] == ["high", "low"]
    empty = fb.overall_confidence([])
    assert empty["level"] == "low" and "没有可预测" in empty["rule"]


# ── 选元素 ───────────────────────────────────────────────────────────


def test_select_elements_prefers_key_then_falls_back_to_all_and_honors_explicit():
    s = _settings(_line("a", "甲", _anat(key=True)), _line("b", "乙", _anat(key=False)))
    assert fb.select_elements(s) == (["a"], False)
    assert fb.select_elements(s, ["b", "b", "nope"]) == (["b"], False)
    s2 = _settings(_line("a", "甲", _anat(key=False)), _line("b", "乙", _anat(key=False)))
    assert fb.select_elements(s2) == (["a", "b"], True)
    assert fb.select_elements({"element_modeling_enabled": True, "anatomy_enabled": True, "causal_lines": []}) == ([], False)


def test_select_elements_skips_unsettleable_key_elements():
    empty = _line("e", "空壳", an.normalize_anatomy({"meta": {"key": True}, "signals": [{"id": "s", "watch": "w", "means": "m"}]}))
    s = _settings(empty, _line("a", "甲", _anat(key=False)))
    assert fb.select_elements(s) == (["a"], True)


# ── 简报：整体行为 ───────────────────────────────────────────────────


def test_disabled_or_empty_returns_reason_not_exception():
    assert fb.run_brief(None)["ok"] is False
    assert fb.run_brief({"anatomy_enabled": False, "causal_lines": []})["ok"] is False
    s = {"element_modeling_enabled": True, "anatomy_enabled": True, "causal_lines": []}
    r = fb.run_brief(s)
    assert r["ok"] is False and "没有带可结算内容" in r["reason"] and r["honesty"] == fb.HONEST_NOTE
    assert fb.build_brief(None, {})["ok"] is False


def test_brief_answers_the_five_questions():
    a = _anat(signals=[{"id": "sg", "watch": "关注中试线公告", "means": "路径 coat 走通"}])
    r = _brief(_settings(_line(anatomy=a)))
    assert r["ok"] and r["honesty"] == fc.HONEST_NOTE and r["confidence_note"] == fb.CONFIDENCE_NOTE
    e = r["elements"][0]
    ans = e["answers"]
    assert ans["success"]["state"] == "pending" and ans["success"]["milestone"]["id"] == "cheap"  # 最后一个有判据未达成的里程碑
    assert 0.0 < ans["success"]["p_reached"] < 1.0 and "推演运行" in ans["success"]["text"]
    assert "P50" in ans["when"]["text"] or "没有出现" in ans["when"]["text"] or "不足一半" in ans["when"]["text"]
    assert ans["blockers"] and ans["blockers"][0]["id"] == "iface" and "路径" in ans["blockers"][0]["text"]
    assert ans["assumptions"] and ans["assumptions"][0]["statement"] == "界面可工程化"
    assert [x["watch"] for x in ans["signals"]][0] == "关注中试线公告"  # 剖面里已有的信号排在预测生成的前面
    assert len(e["uncertainties"]) == 3 and all(u["source"] in ("sensitivity", "forecast") for u in e["uncertainties"])
    assert e["headline"].startswith("「固态电池」") and r["summary"] == [e["headline"]]
    assert r["overall"]["level"] == e["confidence"]["level"] and r["overall"]["worst"]["id"] == "el"


def test_brief_uncertainties_prefer_sensitivity_then_fill_from_forecast():
    s = _settings()
    with_sens = _brief(s)["elements"][0]["uncertainties"]
    assert with_sens[0]["source"] == "sensitivity" and "P50" in with_sens[0]["detail"]
    without = _brief(s, with_sensitivity=False)
    assert without["meta"]["sensitivity"] is False
    ws = without["elements"][0]["uncertainties"]
    assert ws and all(u["source"] == "forecast" for u in ws)
    assert [u["score"] for u in ws] == sorted([u["score"] for u in ws], reverse=True)


def test_brief_is_deterministic_for_a_fixed_seed():
    s = _settings()
    a, b = _brief(s), _brief(s)
    for x in (a, b):
        for el in x["elements"]:
            el.pop("evidence", None)
    ja = json.dumps(a, ensure_ascii=False, sort_keys=True)
    assert ja == json.dumps(b, ensure_ascii=False, sort_keys=True)
    assert json.dumps(_brief(s, seed=8), ensure_ascii=False, sort_keys=True) != ja


def test_run_brief_does_not_mutate_settings_and_is_json_safe():
    s = _settings()
    before = copy.deepcopy(s)
    r = _brief(s)
    assert s == before
    json.dumps(r, ensure_ascii=False)


def test_each_element_section_only_contains_its_own_rows():
    other = _anat(
        metrics=[_metric("price")], bns=[_bn(bid="supply")],
        milestones=[{"id": "launch", "name": "上市", "criteria": {"bottleneck": "supply", "status": "resolved"}}],
        asms=[_asm(with_override=False)],
    )
    s = _settings(_line("a", "甲"), _line("b", "乙", other))
    r = _brief(s)
    assert [e["id"] for e in r["elements"]] == ["a", "b"]
    for e in r["elements"]:
        assert all(m["element"] == e["id"] for m in e["milestones"])
        assert all(m["element"] == e["id"] for m in e["metrics"])
        assert all(b["id"] == ("iface" if e["id"] == "a" else "supply") for b in e["answers"]["blockers"])
    assert r["elements"][1]["answers"]["success"]["milestone"]["id"] == "launch"
    assert [p["id"] for p in r["overall"]["per_element"]] == ["a", "b"]


def test_target_milestone_states():
    done = _anat(milestones=[{"id": "m1", "name": "m1", "criteria": {"bottleneck": "iface", "status": "resolved"}}])
    done["milestones"][0]["reached_step"] = 2
    done["milestones"][0]["reached_sim_day"] = 10
    r = _brief(_settings(_line(anatomy=done)))
    s1 = r["elements"][0]["answers"]["success"]
    assert s1["state"] == "all_reached" and "都已经达成" in s1["text"] and r["elements"][0]["answers"]["when"]["text"] == ""
    nocrit = _anat(milestones=[{"id": "m1", "name": "m1"}])
    s2 = _brief(_settings(_line(anatomy=nocrit)))["elements"][0]["answers"]["success"]
    assert s2["state"] == "no_criteria" and "无法预测" in s2["text"]
    none = _anat(milestones=[])
    s3 = _brief(_settings(_line(anatomy=none)))["elements"][0]["answers"]["success"]
    assert s3["state"] == "none" and "没有声明里程碑" in s3["text"]


def test_target_milestone_is_last_pending_with_criteria():
    rows = [{"id": "a", "status": "reached"}, {"id": "b", "status": "pending"}, {"id": "c", "status": "pending"}, {"id": "d", "status": "no_criteria"}]
    assert fb._target_milestone(rows)[0]["id"] == "c"
    assert fb._target_milestone([{"id": "a", "status": "reached"}])[1] == "all_reached"
    assert fb._target_milestone([])[1] == "none"


@pytest.mark.parametrize("p, word", [(1.0, "多数"), (0.8, "多数"), (0.79, "过半"), (0.5, "过半"), (0.3, "少数"), (0.05, "极少"), (0.0, "没有任何")])
def test_bucket_wording_talks_about_runs_not_reality(p, word):
    assert word in fb._bucket(p) and "推演运行" in fb._bucket(p)
    assert fb._bucket(None) == "无法判定"


def test_blockers_sorted_hardest_first_and_capped():
    rows = [
        {"id": f"b{i}", "name": f"b{i}", "status": "open", "p_resolved": p, "p50": 100, "exhausted": 0.1, "by_path_share": {"x": 1.0}}
        for i, p in enumerate([0.9, 0.2, 0.5, 0.7, 0.1, 0.3, 0.6])
    ] + [{"id": "done", "name": "done", "status": "resolved"}]
    out = fb._blockers(rows)
    assert [b["p_resolved"] for b in out] == [0.1, 0.2, 0.3, 0.5, 0.6] and len(out) == fb.MAX_BLOCKERS
    assert all(b["id"] != "done" for b in out)


def test_assumptions_without_overrides_or_samples_are_explained_honestly():
    s = _settings(_line(anatomy=_anat(asms=[_asm(with_override=False)])))
    a = _brief(s, runs=400)["elements"][0]["answers"]["assumptions"][0]
    assert a["effect"] is None and "没有声明 if_false" in a["text"]
    tiny = _brief(_settings(), runs=50)["elements"][0]["answers"]["assumptions"]
    assert tiny  # 50 次时每侧样本可能不足，但条目仍在并解释原因
    assert all(("样本不足" in x["text"]) or x["effect"] or ("没有声明" in x["text"]) for x in tiny)


def test_signals_anatomy_first_then_forecast_and_capped():
    watch = [{"key": f"fc:{i}", "watch": f"w{i}", "means": "m", "source": "forecast", "score": i / 10, "basis": "derived"} for i in range(9)]
    watch.append({"key": "el#s", "watch": "已有", "means": "m", "source": "anatomy", "score": None, "basis": "llm_prior"})
    out = fb._signals(watch)
    assert out[0]["watch"] == "已有" and len(out) == fb.MAX_SIGNALS
    assert [o["watch"] for o in out[1:]] == ["w8", "w7", "w6", "w5", "w4"]


def test_truncated_forecast_is_flagged_and_lowers_confidence():
    s = _settings()
    r = fb.run_brief(s, [], runs=2000, seed=3, horizon_days=3650, time_budget_sec=1e-9, with_sensitivity=False)
    assert r["meta"]["truncated"] is True
    e = r["elements"][0]
    assert any("时间预算用尽" in b for b in e["banners"])
    assert "truncated" in _deds(e["confidence"])


def test_banners_report_prior_share_and_time_precision():
    class H:  # 最近的步没有 elapsed_days → 时间精度降级
        def __init__(self, step):
            self.step = step
            self.anatomy_trace = []
            self.elapsed_days = None
            self.elapsed_source = "fallback"

    s = _settings()
    r = fb.run_brief(s, [H(1), H(2), H(3)], runs=100, seed=1, horizon_days=1000, time_budget_sec=0, with_sensitivity=False)
    e = r["elements"][0]
    assert any("LLM 先验" in b for b in e["banners"])
    if r["meta"]["time_precision_degraded"]:
        assert "time_precision" in _deds(e["confidence"])


def test_failed_element_forecast_still_gets_a_section_and_confidence():
    s = _settings()
    r = fb.build_brief(s, {"elements": ["el"], "forecast": {"el": {"ok": False, "reason": "测试失败原因"}}, "sens": {}})
    e = r["elements"][0]
    assert r["ok"] and e["has_forecast"] is False and "测试失败原因" in e["headline"] and e["answers"] is None
    assert e["confidence"]["level"] in fb.LEVELS


def test_fallback_all_banner_when_no_key_elements():
    s = _settings(_line("a", "甲", _anat(key=False)))
    r = _brief(s)
    assert r["meta"]["fallback_all"] is True and any("没有被标为" in b for b in r["banners"])
    assert _brief(_settings())["banners"] == []


def test_evidence_summary_only_when_evidence_given():
    s = _settings(_line(anatomy=_anat(metrics=[_metric(basis=SRC, cur_basis=SRC)])))
    assert _brief(s)["elements"][0]["evidence"] is None
    e = _brief(s, evidence=[_rec(days_ago=400)])["elements"][0]["evidence"]
    assert e["active"] == 1 and e["stale"] == 1
    assert "stale_evidence" in _deds(_brief(s, evidence=[_rec(days_ago=400)])["elements"][0]["confidence"])


def test_time_budget_is_split_evenly_without_a_floor():
    assert fb._split(60, 3, 99) == 20.0
    assert fb._split(6, 3, 99) == 2.0          # 不设下限：各元素预算之和不会超过总预算
    assert fb._split(0, 3, 99) == 0.0 and fb._split(-1, 3, 99) == 0.0
    assert fb._split(None, 2, 40) == 20.0
