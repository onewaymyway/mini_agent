"""tests/test_backtest_p6.py — 第二十二轮 P6：回测改用 `elapsed_days` / `tech_state`。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §10 P6。
验证：时间线（有/无/混合/强制近似）、候选时间、技术节点到达时点的还原、阶段迁移时点偏差
的各种状态、端到端落盘、rescore 兼容旧结果、A/B 的时间基准警告。引擎一律用桩；
**没有在真实 LLM 下跑过。**
"""

from __future__ import annotations

import importlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import backtest as bt
from world_simulator.state_model import SimManifest, SimState
from world_simulator.store import SimStore, now_iso

DAYS = 365.25


def _st(step, **kw):
    return SimState(step=step, summary=f"s{step}", **kw)


def _tech(*nodes):
    return {"tech_state": {"nodes": list(nodes)}}


def _node(tid, stage, *, name=None, preexisting=False):
    return {"id": tid, "name": name or tid, "stage": stage, "preexisting": preexisting}


# ── 时间线 ───────────────────────────────────────────────────────────


def test_timeline_without_elapsed_is_years_per_step():
    tl = bt.build_timeline([_st(0), _st(1), _st(2), _st(3)], years_per_step=2)
    assert tl["basis_used"] == "years_per_step" and tl["reported_steps"] == 0 and tl["total_steps"] == 3
    assert tl["points"] == {0: 0.0, 1: 2.0, 2: 4.0, 3: 6.0} and tl["horizon_years"] == 6.0


def test_timeline_all_elapsed_accumulates_per_step_span():
    hist = [_st(0), _st(1, elapsed_days=2 * DAYS), _st(2, elapsed_days=1 * DAYS), _st(3, elapsed_days=5 * DAYS)]
    tl = bt.build_timeline(hist, years_per_step=99)  # years_per_step 完全不该参与
    assert tl["basis_used"] == "elapsed_days" and tl["reported_steps"] == 3
    assert [round(tl["points"][i], 6) for i in range(4)] == [0.0, 2.0, 3.0, 8.0]
    assert math.isclose(tl["horizon_years"], 8.0)


def test_timeline_mixed_fills_missing_steps_with_years_per_step():
    hist = [_st(0), _st(1, elapsed_days=DAYS), _st(2), _st(3, elapsed_days=DAYS)]
    tl = bt.build_timeline(hist, years_per_step=4)
    assert tl["basis_used"] == "mixed" and tl["reported_steps"] == 2 and tl["total_steps"] == 3
    assert [round(tl["points"][i], 6) for i in range(4)] == [0.0, 1.0, 5.0, 6.0]


def test_timeline_forced_years_per_step_ignores_elapsed():
    hist = [_st(0), _st(1, elapsed_days=10 * DAYS), _st(2, elapsed_days=10 * DAYS)]
    tl = bt.build_timeline(hist, years_per_step=2, basis="years_per_step")
    assert tl["basis_used"] == "years_per_step" and tl["basis_requested"] == "years_per_step"
    assert tl["points"] == {0: 0.0, 1: 2.0, 2: 4.0}


def test_timeline_treats_invalid_elapsed_as_missing():
    bad = SimpleNamespace(step=1, elapsed_days=float("nan"))
    zero = SimpleNamespace(step=2, elapsed_days=0)
    neg = SimpleNamespace(step=3, elapsed_days=-5)
    boolean = SimpleNamespace(step=4, elapsed_days=True)
    tl = bt.build_timeline([_st(0), bad, zero, neg, boolean], years_per_step=1)
    assert tl["basis_used"] == "years_per_step" and tl["horizon_years"] == 4.0


def test_timeline_history_gap_adds_approximation_for_missing_steps():
    tl = bt.build_timeline([_st(0), _st(3, elapsed_days=DAYS)], years_per_step=2)
    assert math.isclose(tl["points"][3], 1.0 + 2 * 2)  # 最后一步用记录值，被跳过的 2 步按近似补


def test_timeline_empty_history():
    tl = bt.build_timeline([], years_per_step=2)
    assert tl["points"] == {} and tl["horizon_years"] == 0.0 and tl["basis_used"] == "years_per_step"


def test_extract_candidates_uses_timeline_and_defaults_unchanged():
    hist = [_st(0), _st(1, elapsed_days=DAYS, capabilities_gained=[{"capability": "甲", "maturity_stage": "lab"}])]
    tl = bt.build_timeline(hist, years_per_step=5)
    with_tl = bt.extract_candidates(hist, None, start_year=2000, years_per_step=5, timeline=tl)
    without = bt.extract_candidates(hist, None, start_year=2000, years_per_step=5)
    assert math.isclose(with_tl[0]["time"], 2001.0) and without[0]["time"] == 2005


# ── 技术节点还原 ─────────────────────────────────────────────────────


def _tech_history():
    return [
        _st(0, dynamic_snapshot=_tech(_node("seed", "lab", name="种子技术", preexisting=True))),
        _st(1, dynamic_snapshot=_tech(_node("seed", "lab", name="种子技术"))),
        _st(2, dynamic_snapshot=_tech(_node("seed", "lab", name="种子技术"), _node("newtech", "lab", name="新技术"))),
        _st(3),  # 无快照：沿用上一份
        _st(4, dynamic_snapshot=_tech(_node("seed", "expert", name="种子技术"), _node("newtech", "lab", name="新技术"))),
        _st(5, dynamic_snapshot=_tech(_node("seed", "lab", name="种子技术"), _node("newtech", "developer", name="新技术"))),  # seed 倒退
    ]


def test_extract_tech_nodes_kinds_and_times():
    nodes = bt.extract_tech_nodes(_tech_history(), start_year=2000, years_per_step=2)
    by = {n["tech_id"]: n for n in nodes}
    assert [n["id"] for n in nodes] == ["tn1", "tn2"] and nodes[0]["tech_id"] == "seed"
    assert by["seed"]["arrivals"]["lab"] == {"step": 0, "time": 2000.0, "kind": "initial"}
    assert by["seed"]["arrivals"]["expert"] == {"step": 4, "time": 2008.0, "kind": "transition"}
    assert by["newtech"]["arrivals"]["lab"]["kind"] == "registered" and by["newtech"]["arrivals"]["lab"]["step"] == 2
    assert by["newtech"]["arrivals"]["developer"] == {"step": 5, "time": 2010.0, "kind": "transition"}
    assert by["seed"]["maturity_stage"] == "expert"  # 倒退不改已到达的最高阶段


def test_extract_tech_nodes_uses_timeline():
    hist = [_st(0, dynamic_snapshot=_tech(_node("a", "lab"))),
            _st(1, elapsed_days=DAYS, dynamic_snapshot=_tech(_node("a", "expert")))]
    tl = bt.build_timeline(hist, years_per_step=10)
    n = bt.extract_tech_nodes(hist, start_year=2000, years_per_step=10, timeline=tl)[0]
    assert math.isclose(n["arrivals"]["expert"]["time"], 2001.0)


def test_extract_tech_nodes_empty_when_no_tech_state_or_history():
    assert bt.extract_tech_nodes([_st(0), _st(1)], start_year=0, years_per_step=1) == []
    assert bt.extract_tech_nodes([], start_year=0, years_per_step=1) == []


def test_extract_tech_nodes_ignores_unknown_stage_and_bad_snapshot():
    hist = [_st(0, dynamic_snapshot={"tech_state": "oops"}),
            _st(1, dynamic_snapshot=_tech({"id": "x", "name": "x", "stage": "warp"}))]
    # 未知阶段会被 tech_model 规整成最低档（不会崩）；坏快照被跳过
    nodes = bt.extract_tech_nodes(hist, start_year=0, years_per_step=1)
    assert [n["tech_id"] for n in nodes] == ["x"] and nodes[0]["arrivals"]["lab"]["kind"] == "registered"


# ── 阶段迁移时点偏差 ─────────────────────────────────────────────────


def _truth(tid, name, year, stage=None):
    return {"id": tid, "name": name, "aliases": [], "year": year, "requires": [], "stage": stage}


def _tn(nid, tech_id, arrivals):
    return {"id": nid, "tech_id": tech_id, "text": tech_id, "arrivals": arrivals, "step": 0, "time": 0, "detail": ""}


def _arr(step, time, kind="transition"):
    return {"step": step, "time": time, "kind": kind}


def test_timing_perfect_and_slow_engine():
    truths = [_truth("t1", "a", 2000, "lab"), _truth("t2", "b", 2004, "developer"), _truth("t3", "c", 2008, "consumer")]
    fast = [_tn("tn1", "a", {"lab": _arr(1, 2000, "registered")}),
            _tn("tn2", "b", {"developer": _arr(2, 2004)}), _tn("tn3", "c", {"consumer": _arr(4, 2008)})]
    m = {"t1": "tn1", "t2": "tn2", "t3": "tn3"}
    r = bt.score_tech_timing(truths, fast, m, start_year=2000, horizon_years=20)
    assert r["available"] and r["n_timed"] == 3 and r["mean_abs_years"] == 0.0 and r["stage_reached_rate"] == 1.0
    slow = [_tn("tn1", "a", {"lab": _arr(1, 2000, "registered")}),
            _tn("tn2", "b", {"developer": _arr(3, 2006)}), _tn("tn3", "c", {"consumer": _arr(6, 2012)})]
    r = bt.score_tech_timing(truths, slow, m, start_year=2000, horizon_years=20)
    # 相对第一个对齐：t2 慢 2 年，t3 慢 4 年
    assert math.isclose(r["mean_abs_years"], 3.0) and math.isclose(r["mean_signed_years"], 3.0)


def test_timing_signed_negative_when_engine_faster():
    truths = [_truth("t1", "a", 2000, "lab"), _truth("t2", "b", 2010, "developer")]
    nodes = [_tn("tn1", "a", {"lab": _arr(1, 2000, "registered")}), _tn("tn2", "b", {"developer": _arr(2, 2004)})]
    r = bt.score_tech_timing(truths, nodes, {"t1": "tn1", "t2": "tn2"}, start_year=2000, horizon_years=20)
    assert math.isclose(r["mean_signed_years"], -6.0) and math.isclose(r["mean_abs_years"], 6.0)


def test_timing_initial_excluded_not_reached_and_out_of_horizon_counted_separately():
    truths = [_truth("t1", "a", 2000, "lab"), _truth("t2", "b", 2004, "developer"),
              _truth("t3", "c", 2008, "consumer"), _truth("t4", "d", 2090, "expert")]
    nodes = [_tn("tn1", "a", {"lab": _arr(0, 2000, "initial")}),
             _tn("tn2", "b", {"lab": _arr(1, 2002, "registered")}),                # 没到 developer
             _tn("tn3", "c", {"consumer": _arr(3, 2006)}),
             _tn("tn4", "d", {"lab": _arr(1, 2002, "registered")})]                # 没到 expert，但范围外
    r = bt.score_tech_timing(truths, nodes, {"t1": "tn1", "t2": "tn2", "t3": "tn3", "t4": "tn4"},
                             start_year=2000, horizon_years=20)
    assert (r["n_initial"], r["n_not_reached"], r["n_out_of_horizon"], r["n_timed"]) == (1, 1, 1, 1)
    assert r["mean_abs_years"] is None and r["n_intervals"] == 0  # 只有 1 个可计时，没有间隔
    assert math.isclose(r["stage_reached_rate"], 2 / 3)  # (1 timed + 1 initial) / (… + 1 not_reached)
    assert {row["truth_id"]: row["status"] for row in r["per_truth"]} == {
        "t1": "initial", "t2": "not_reached", "t3": "transition", "t4": "out_of_horizon"}


def test_timing_unavailable_without_nodes_is_none_not_zero():
    r = bt.score_tech_timing([_truth("t1", "a", 2000, "lab")], [], {}, start_year=2000, horizon_years=10)
    assert r["available"] is False and "tech_state" in r["reason"] and "mean_abs_years" not in r


def test_timing_skips_truths_without_stage_or_match():
    truths = [_truth("t1", "a", 2000), _truth("t2", "b", 2004, "lab")]
    nodes = [_tn("tn1", "a", {"lab": _arr(1, 2000, "registered")})]
    r = bt.score_tech_timing(truths, nodes, {"t1": "tn1", "t2": None}, start_year=2000, horizon_years=10)
    assert r["staged_truths"] == 1 and r["matched"] == 0 and r["stage_reached_rate"] is None


def test_timing_stage_arrival_uses_the_truths_own_stage():
    truths = [_truth("t1", "a", 2000, "lab"), _truth("t2", "a2", 2004, "expert")]
    node = _tn("tn1", "a", {"lab": _arr(1, 2000, "registered"), "expert": _arr(3, 2006)})
    r = bt.score_tech_timing(truths, [node], {"t1": "tn1", "t2": None}, start_year=2000, horizon_years=20)
    assert r["per_truth"][0]["engine_time"] == 2000  # t1 的 stage=lab → 用 lab 的到达时点


# ── 端到端（桩引擎）────────────────────────────────────────────────


def _case(**over):
    data = {
        "id": "toy6", "title": "t", "steps": 5, "year_shift": 0,
        "start": {"intent": "起点", "start_year": 2000, "years_per_step": 2},
        "milestones": [
            {"id": "m1", "name": "联盟研究网连通", "year": 2000, "stage": "lab"},
            {"id": "m2", "name": "通用互联协议成为标准", "year": 2004, "stage": "developer", "requires": ["m1"]},
            {"id": "m3", "name": "图形化浏览器", "year": 2008, "stage": "consumer", "requires": ["m2"]},
        ],
    }
    data.update(over)
    return bt.parse_case(data)


def _make_stubs(script):
    """`script[i]` = 第 i+1 步的 dict：elapsed（天）/caps/tech（节点列表，None=不写快照）。"""

    def create_fn(cfg, root, data_dir, *, template, intent, settings):
        store = SimStore.for_root(Path(data_dir), "sim_bt")
        ts = now_iso()
        store.save_manifest(SimManifest(sim_id="sim_bt", template=template, intent=intent, title="t",
                                        created_at=ts, updated_at=ts, settings=dict(settings)))
        store.append_state(SimState(step=0, summary="s0", vars={"x": 0}, options=[]))
        return store.load_manifest()

    def advance_fn(cfg, root, data_dir, sim_id):
        store = SimStore.for_root(Path(data_dir), sim_id)
        manifest = store.load_manifest()
        step = manifest.current_step + 1
        spec = script[step - 1] if step - 1 < len(script) else {}
        state = SimState(step=step, summary=f"s{step}", vars={"x": step}, options=[],
                         capabilities_gained=spec.get("caps", []),
                         elapsed_days=spec.get("elapsed"),
                         dynamic_snapshot=_tech(*spec["tech"]) if spec.get("tech") else None)
        store.append_state(state)
        manifest.current_step = step
        store.save_manifest(manifest)
        return state

    return create_fn, advance_fn


_SCRIPT = [
    {"elapsed": 1 * DAYS, "caps": [{"capability": "联盟研究网连通", "maturity_stage": "lab"}],
     "tech": [_node("net", "lab", name="联盟研究网")]},
    {"elapsed": 1 * DAYS, "tech": [_node("net", "lab", name="联盟研究网")]},
    {"elapsed": 2 * DAYS, "tech": [_node("net", "lab", name="联盟研究网"), _node("proto", "lab", name="通用互联协议")]},
    {"elapsed": 2 * DAYS, "tech": [_node("net", "lab", name="联盟研究网"), _node("proto", "expert", name="通用互联协议")]},
    {"elapsed": 2 * DAYS, "tech": [_node("net", "lab", name="联盟研究网"), _node("proto", "developer", name="通用互联协议")]},
]


def test_run_case_uses_elapsed_time_and_reports_basis_and_tech_timing(tmp_path):
    create_fn, advance_fn = _make_stubs(_SCRIPT)
    res = bt.run_case(_case(), cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "r",
                      create_fn=create_fn, advance_fn=advance_fn)
    assert res["time_basis"]["basis_used"] == "elapsed_days" and res["time_basis"]["reported_steps"] == 5
    assert math.isclose(res["horizon_years"], 8.0)  # 1+1+2+2+2 年，而不是 5×2=10
    assert math.isclose(res["candidates"][0]["time"], 2001.0)  # step1 = 1 年（旧口径会是 2002）
    assert "elapsed_days" in "".join(res["caveats"]) and "years_per_step 近似" not in "".join(res["caveats"])
    tt = res["metrics"]["tech_timing"]
    assert tt["available"] and tt["nodes"] == 2 and tt["n_timed"] == 2
    # net 在 step1 登记（2001），proto 在 step5 到 developer（2008）：引擎间隔 7 年，真值间隔 4 年 → 慢 3 年
    assert math.isclose(tt["mean_signed_years"], 3.0)
    assert res["tech_matches"] == {"m1": "tn1", "m2": "tn2", "m3": None}
    saved = json.loads((tmp_path / "r" / "result.json").read_text(encoding="utf-8"))
    assert saved["timeline"]["5"] == pytest.approx(8.0) and saved["tech_nodes"][0]["arrivals"]


def test_run_case_forced_years_per_step_basis(tmp_path):
    create_fn, advance_fn = _make_stubs(_SCRIPT)
    res = bt.run_case(_case(), cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "r",
                      create_fn=create_fn, advance_fn=advance_fn, time_basis="years_per_step")
    assert res["time_basis"]["basis_used"] == "years_per_step" and res["horizon_years"] == 10.0
    assert res["candidates"][0]["time"] == 2002 and "years_per_step 近似" in "".join(res["caveats"])


def test_run_case_horizon_follows_elapsed_so_recall_denominator_changes(tmp_path):
    # 真值 2000/2004/2008；按 elapsed 只跑到 +8 年 → 全在范围内；把案例改成 2010 的真值：
    case = _case(milestones=[
        {"id": "m1", "name": "联盟研究网连通", "year": 2000},
        {"id": "m9", "name": "遥远的事", "year": 2009},
    ])
    create_fn, advance_fn = _make_stubs(_SCRIPT)
    elapsed = bt.run_case(case, cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "a",
                          create_fn=create_fn, advance_fn=advance_fn)
    approx = bt.run_case(case, cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "b",
                         create_fn=create_fn, advance_fn=advance_fn, time_basis="years_per_step")
    assert elapsed["metrics"]["truths_in_horizon"] == 1   # 8 年 < 9 年
    assert approx["metrics"]["truths_in_horizon"] == 2    # 10 年 ≥ 9 年


def test_run_case_without_tech_state_has_unavailable_timing_and_old_basis(tmp_path):
    script = [{"caps": [{"capability": "联盟研究网连通", "maturity_stage": "lab"}]}, {}, {}]
    create_fn, advance_fn = _make_stubs(script)
    res = bt.run_case(_case(steps=3), cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "r",
                      create_fn=create_fn, advance_fn=advance_fn)
    assert res["time_basis"]["basis_used"] == "years_per_step" and res["metrics"]["tech_timing"]["available"] is False
    assert res["tech_nodes"] == [] and res["tech_matches"] == {}


def test_matcher_not_called_for_tech_when_no_nodes(tmp_path):
    calls = []

    def spy(cands, truths):
        calls.append(len(cands))
        return {t["id"]: None for t in truths}

    create_fn, advance_fn = _make_stubs([{}, {}])
    bt.run_case(_case(steps=2), cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "r", matcher=spy,
                create_fn=create_fn, advance_fn=advance_fn)
    assert len(calls) == 1  # 只有里程碑候选那一次；没有技术节点就不多花一次匹配（LLM 匹配器省钱）


def test_case_time_basis_validation_and_default():
    assert _case().time_basis == "auto"
    with pytest.raises(bt.BacktestError):
        _case(time_basis="nonsense")
    with pytest.raises(bt.BacktestError):
        # 运行时参数同样校验
        bt.run_case(_case(), cfg=None, workspace_root=Path("."), out_dir=Path("."), time_basis="x",
                    create_fn=_make_stubs([])[0], advance_fn=_make_stubs([])[1])


# ── rescore / A-B ────────────────────────────────────────────────────


def test_rescore_keeps_tech_timing_and_uses_stored_horizon(tmp_path):
    create_fn, advance_fn = _make_stubs(_SCRIPT)
    res = bt.run_case(_case(), cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "r",
                      create_fn=create_fn, advance_fn=advance_fn)
    before = res["metrics"]["tech_timing"]
    out = bt.rescore(tmp_path / "r" / "result.json", {"m3": None})
    assert out["metrics"]["tech_timing"] == before
    assert out["metrics"]["truths_in_horizon"] == res["metrics"]["truths_in_horizon"]


def test_rescore_old_result_without_p6_fields_still_works(tmp_path):
    create_fn, advance_fn = _make_stubs(_SCRIPT)
    bt.run_case(_case(), cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "r",
                create_fn=create_fn, advance_fn=advance_fn)
    p = tmp_path / "r" / "result.json"
    old = json.loads(p.read_text(encoding="utf-8"))
    for k in ("horizon_years", "time_basis", "timeline", "tech_nodes", "tech_matches"):
        old.pop(k)
    old["metrics"].pop("tech_timing")
    p.write_text(json.dumps(old), encoding="utf-8")
    out = bt.rescore(p, {"m3": None})
    assert "tech_timing" not in out["metrics"]
    assert out["metrics"]["truths_in_horizon"] == 3  # 旧口径 5 步×2 年 = 10 年


def _run(basis_used, tt_mean=None):
    return {"metrics": {"recall": 1.0, "precision": 1.0, "kendall_tau": 1.0,
                        "interval_error": {"mean_abs_years": 0.0}, "prerequisite_violations": {"violations": 0},
                        "stage": {"agreement": 1.0},
                        "tech_timing": {"available": tt_mean is not None, "mean_abs_years": tt_mean,
                                        "stage_reached_rate": 1.0 if tt_mean is not None else None}},
            "time_basis": {"basis_used": basis_used}, "aborted": None}


def test_compare_arms_warns_when_time_bases_differ_and_includes_tech_metrics():
    rep = bt.compare_arms({"A": [_run("years_per_step")], "B": [_run("elapsed_days", 1.5)]})
    assert "time_basis_warning" in rep and "years_per_step" in rep["time_basis_warning"]
    assert rep["arms"]["B"]["tech_timing_mean_abs_years"]["mean"] == 1.5
    assert rep["arms"]["A"]["tech_timing_mean_abs_years"]["n"] == 0  # 无数据不参与统计
    assert rep["arms"]["A"]["_time_bases"] == ["years_per_step"]
    assert rep["mean_difference"]["by_metric"]["tech_timing_mean_abs_years"] is None


def test_compare_arms_no_warning_when_same_basis_and_old_results_default_to_years_per_step():
    same = bt.compare_arms({"A": [_run("elapsed_days", 1.0)], "B": [_run("elapsed_days", 2.0)]})
    assert "time_basis_warning" not in same
    assert same["mean_difference"]["by_metric"]["tech_timing_mean_abs_years"] == 1.0
    old = _run("x")
    old.pop("time_basis")
    assert "time_basis_warning" not in bt.compare_arms({"A": [old], "B": [_run("years_per_step")]})


# ── CLI ──────────────────────────────────────────────────────────────


def _cli():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "entrypoints"))
    return importlib.import_module("backtest")


def test_cli_prints_time_basis_and_tech_timing(tmp_path, capsys):
    create_fn, advance_fn = _make_stubs(_SCRIPT)
    res = bt.run_case(_case(), cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "r",
                      create_fn=create_fn, advance_fn=advance_fn)
    _cli()._print_metrics(res)
    out = capsys.readouterr().out
    assert "时间基准：elapsed_days" in out and "阶段迁移时点偏差 3.00 年" in out
    res["metrics"]["tech_timing"] = {"available": False, "reason": "没有快照"}
    _cli()._print_metrics(res)
    assert "阶段迁移时点偏差：无数据（没有快照）" in capsys.readouterr().out
