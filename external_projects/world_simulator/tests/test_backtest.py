"""tests/test_backtest.py — WP5（第二十二轮）：回测与校准。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md`
§4 WP5。所有引擎/LLM 调用都用桩替换（`create_fn`/`advance_fn`/假 workflow），
这里验证的是：案例校验、隐藏答案、候选抽取、匹配与覆盖、打分的边界、
端到端落盘与隔离、A/B 汇总。**没有在真实 LLM 下跑过。**
"""

from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import backtest as bt
from world_simulator import reality_check
from world_simulator.engine.errors import SimEngineError
from world_simulator.state_model import SimManifest, SimState
from world_simulator.store import SimStore, now_iso

CASES_DIR = Path(__file__).resolve().parent.parent / "backtest_cases"


def _case_dict(**over):
    data = {
        "id": "toy",
        "title": "玩具案例",
        "steps": 6,
        "year_shift": 10,
        "alias_map": {"Alpha": "甲", "Alpha Pro": "甲专业版"},
        "start": {"intent": "1990 年 Alpha 出现，2000 年 Alpha Pro 出现", "start_year": 1990, "years_per_step": 2},
        "milestones": [
            {"id": "m1", "name": "Alpha 发布", "year": 1990, "aliases": ["甲发布"], "stage": "lab"},
            {"id": "m2", "name": "Alpha Pro 发布", "year": 1994, "requires": ["m1"], "stage": "developer"},
            {"id": "m3", "name": "大规模普及", "year": 1998, "requires": ["m2"], "stage": "consumer"},
        ],
    }
    data.update(over)
    return data


def _cap(name, stage=None):
    return {"capability": name, "maturity_stage": stage}


# ── 案例校验 ─────────────────────────────────────────────────────────


def test_parse_case_valid_and_defaults():
    case = bt.parse_case(_case_dict())
    assert case.id == "toy" and len(case.milestones) == 3
    assert case.template == "group_evolution" and case.verified is False
    assert case.milestones[1].requires == ["m1"]


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d.pop("id"), "缺少 id"),
    (lambda d: d["start"].pop("intent"), "intent"),
    (lambda d: d["start"].update(start_year="abc"), "start_year"),
    (lambda d: d["start"].update(years_per_step=0), "> 0"),
    (lambda d: d.update(milestones=[]), "milestones"),
    (lambda d: d["milestones"][1].update(id="m1"), "重复"),
    (lambda d: d["milestones"][1].update(requires=["nope"]), "不存在"),
    (lambda d: d["milestones"][0].update(requires=["m1"]), "不能是自己"),
    (lambda d: d["milestones"][0].update(requires=["m3"]), "成环"),
    (lambda d: d["milestones"][0].update(stage="bogus"), "stage"),
    (lambda d: d["milestones"][0].update(year="x"), "year"),
    (lambda d: d.update(steps=0), "steps"),
])
def test_parse_case_rejects_invalid(mutate, needle):
    data = _case_dict()
    mutate(data)
    with pytest.raises(bt.BacktestError) as exc:
        bt.parse_case(data)
    assert needle in str(exc.value)


def test_load_case_yaml_json_and_missing(tmp_path):
    import yaml

    y = tmp_path / "c.yaml"
    y.write_text(yaml.safe_dump(_case_dict(), allow_unicode=True), encoding="utf-8")
    assert bt.load_case(y).id == "toy"
    j = tmp_path / "c.json"
    j.write_text(json.dumps(_case_dict()), encoding="utf-8")
    assert bt.load_case(j).id == "toy"
    with pytest.raises(bt.BacktestError):
        bt.load_case(tmp_path / "missing.yaml")


def test_shipped_sample_cases_are_valid_and_flagged_unverified():
    for name in ("internet_early", "personal_computer"):
        case = bt.load_case(CASES_DIR / f"{name}.yaml")
        assert case.verified is False  # Claude 起草、未经核对
        assert case.disclaimer and len(case.milestones) >= 4


# ── 隐藏答案 ─────────────────────────────────────────────────────────


def test_anonymize_aliases_longest_first_shifts_years_and_does_not_mutate():
    case = bt.parse_case(_case_dict())
    before = copy.deepcopy(case)
    view = bt.anonymize(case)
    assert view["intent"] == "2000 年 甲 出现，2010 年 甲专业版 出现"  # Alpha Pro 先于 Alpha 替换
    assert "Alpha" not in view["intent"]
    assert view["truths"][1]["name"] == "甲专业版 发布" and view["truths"][1]["year"] == 1994 + 10
    assert view["start_year"] == 2000
    assert case == before


# ── 候选抽取 ─────────────────────────────────────────────────────────


def _st(step, **kw):
    return SimState(step=step, summary=f"s{step}", **kw)


def _tree(status):
    return [{"id": "L1", "future_tree": {"as_of_step": 0, "branches": [
        {"id": "b1", "description": "某分支", "status": status, "likelihood": "medium"}]}}]


def test_extract_candidates_capabilities_and_tree_with_snapshot_step():
    history = [
        _st(0),
        _st(1, capabilities_gained=[_cap("甲发布", "lab")]),
        _st(2, dynamic_snapshot={"causal_lines": _tree("active")}),
        _st(3, dynamic_snapshot={"causal_lines": _tree("resolved")}),
        _st(4, capabilities_gained=[_cap("甲专业版", "developer"), {"capability": ""}, "bad"]),
    ]
    cands = bt.extract_candidates(history, _tree("resolved"), start_year=2000, years_per_step=2)
    assert [(c["id"], c["step"], c["source"]) for c in cands] == [
        ("c1", 1, "capability"), ("c2", 3, "tree_resolved"), ("c3", 4, "capability")]
    assert cands[0]["time"] == 2002 and cands[0]["maturity_stage"] == "lab"
    assert cands[1]["text"] == "某分支"


def test_extract_candidates_tree_without_snapshot_falls_back_to_last_step():
    history = [_st(0), _st(1), _st(5)]
    cands = bt.extract_candidates(history, _tree("resolved"), start_year=0, years_per_step=1)
    assert cands[0]["step"] == 5  # 保守：不编造更早时点
    assert bt.extract_candidates([], None, start_year=0, years_per_step=1) == []
    assert bt.extract_candidates(history, _tree("active"), start_year=0, years_per_step=1) == []


# ── 匹配 / 覆盖 ─────────────────────────────────────────────────────


def _truth(tid, name, year, requires=(), stage=None, aliases=()):
    return {"id": tid, "name": name, "aliases": list(aliases), "year": year, "requires": list(requires), "stage": stage}


def _cand(cid, text, step, stage=None, start=2000, ys=2):
    return {"id": cid, "text": text, "detail": "", "step": step, "time": start + step * ys,
            "source": "capability", "maturity_stage": stage}


def test_rule_matcher_is_one_to_one_earliest_and_ignores_short_keys():
    truths = [_truth("t1", "甲发布", 1), _truth("t2", "甲专业版发布", 2), _truth("t3", "x", 3)]
    cands = [_cand("c1", "甲发布会", 1), _cand("c2", "甲专业版发布", 2), _cand("c3", "甲发布再次", 3), _cand("c4", "x", 4)]
    m = bt.rule_matcher(cands, truths)
    assert m["t1"] == "c1" and m["t2"] == "c2"
    assert m["t3"] is None  # 单字符键太短，不参与匹配


def test_sanitize_matches_enforces_ids_and_one_to_one():
    truths = [_truth("t1", "a", 1), _truth("t2", "b", 2), _truth("t3", "c", 3)]
    cands = [_cand("c1", "a", 1), _cand("c2", "b", 2)]
    raw = [
        {"truth_id": "t1", "candidate_id": "c1"},
        {"truth_id": "t2", "candidate_id": "c1"},      # 重复占用 → 丢弃
        {"truth_id": "t3", "candidate_id": "c99"},     # 不存在 → 丢弃
        {"truth_id": "zz", "candidate_id": "c2"},      # 未知真值 → 忽略
        {"truth_id": "t1", "candidate_id": "c2"},      # 重复真值 → 忽略
        "junk",
    ]
    assert bt.sanitize_matches(raw, cands, truths) == {"t1": "c1", "t2": None, "t3": None}
    assert bt.sanitize_matches("not a list", cands, truths) == {"t1": None, "t2": None, "t3": None}


def test_apply_overrides_reassigns_keeps_one_to_one_and_rejects_unknown():
    truths = [_truth("t1", "a", 1), _truth("t2", "b", 2)]
    cands = [_cand("c1", "a", 1), _cand("c2", "b", 2)]
    new, changed = bt.apply_overrides({"t1": "c1", "t2": None}, {"t2": "c1"}, cands, truths)
    assert new == {"t1": None, "t2": "c1"} and sorted(changed) == ["t1", "t2"]
    new, changed = bt.apply_overrides({"t1": "c1", "t2": "c2"}, {"t1": None}, cands, truths)
    assert new == {"t1": None, "t2": "c2"} and changed == ["t1"]
    with pytest.raises(bt.BacktestError):
        bt.apply_overrides({}, {"nope": "c1"}, cands, truths)
    with pytest.raises(bt.BacktestError):
        bt.apply_overrides({"t1": None}, {"t1": "c99"}, cands, truths)


# ── 打分 ─────────────────────────────────────────────────────────────


def _score(truths, cands, matches, horizon=10.0, start=2000.0):
    return bt.score_run(truths, cands, matches, start_year=start, horizon_years=horizon)


def test_score_perfect_run():
    truths = [_truth("t1", "a", 2000, stage="lab"), _truth("t2", "b", 2004, requires=["t1"], stage="developer"),
              _truth("t3", "c", 2008, requires=["t2"], stage="consumer")]
    cands = [_cand("c1", "a", 0, "lab"), _cand("c2", "b", 2, "developer"), _cand("c3", "c", 4, "consumer")]
    r = _score(truths, cands, {"t1": "c1", "t2": "c2", "t3": "c3"})
    assert r["recall"] == 1.0 and r["precision"] == 1.0 and r["kendall_tau"] == 1.0
    assert r["interval_error"]["mean_abs_years"] == 0.0 and r["interval_error"]["mean_signed_years"] == 0.0
    assert r["prerequisite_violations"] == {"checked": 2, "violations": 0}
    assert r["stage"]["agreement"] == 1.0 and r["stage"]["mean_abs_ordinal_diff"] == 0.0


def test_score_reversed_order_and_prerequisite_violation():
    truths = [_truth("t1", "a", 2000), _truth("t2", "b", 2004, requires=["t1"]), _truth("t3", "c", 2008)]
    cands = [_cand("c1", "c", 0), _cand("c2", "b", 2), _cand("c3", "a", 4)]
    r = _score(truths, cands, {"t1": "c3", "t2": "c2", "t3": "c1"})
    assert r["kendall_tau"] == -1.0
    assert r["prerequisite_violations"] == {"checked": 1, "violations": 1}  # b 涌现(2)早于前置 a(4)


def test_score_same_step_is_not_a_prerequisite_violation():
    truths = [_truth("t1", "a", 2000), _truth("t2", "b", 2004, requires=["t1"])]
    cands = [_cand("c1", "a", 1), _cand("c2", "b", 1)]
    assert _score(truths, cands, {"t1": "c1", "t2": "c2"})["prerequisite_violations"]["violations"] == 0


def test_score_interval_error_is_offset_free_and_signed():
    # 真值间隔 4 年，引擎间隔 6 年（慢 2 年）；整体平移不改变误差
    truths = [_truth("t1", "a", 2000), _truth("t2", "b", 2004)]
    for offset in (0, 3):
        cands = [_cand("c1", "a", 0 + offset), _cand("c2", "b", 3 + offset)]  # step*2 → 间隔 6 年
        r = _score(truths, cands, {"t1": "c1", "t2": "c2"})
        assert r["interval_error"]["mean_abs_years"] == 2.0 and r["interval_error"]["mean_signed_years"] == 2.0


def test_score_horizon_precision_and_missing_data_are_none_not_zero():
    truths = [_truth("t1", "a", 2000), _truth("t2", "b", 2050)]  # t2 超出模拟范围
    cands = [_cand("c1", "a", 0), _cand("c2", "extra", 1)]
    r = _score(truths, cands, {"t1": "c1", "t2": None}, horizon=10)
    assert r["truths_in_horizon"] == 1 and r["recall"] == 1.0 and r["recall_all"] == 0.5
    assert r["precision"] == 0.5
    assert r["kendall_tau"] is None and r["interval_error"]["mean_abs_years"] is None
    assert r["stage"]["agreement"] is None
    empty = _score(truths, [], {"t1": None, "t2": None}, horizon=10)
    assert empty["precision"] is None and empty["recall"] == 0.0
    assert _score([], [], {}, horizon=10)["recall_all"] is None


def test_score_stage_partial_agreement():
    truths = [_truth("t1", "a", 2000, stage="lab"), _truth("t2", "b", 2004, stage="lab")]
    cands = [_cand("c1", "a", 0, "lab"), _cand("c2", "b", 2, "consumer")]
    s = _score(truths, cands, {"t1": "c1", "t2": "c2"})["stage"]
    assert s["agreement"] == 0.5 and s["mean_abs_ordinal_diff"] == 1.5


# ── 端到端（桩引擎）────────────────────────────────────────────────


def _make_stubs(script, *, fail_at=None, end_at=None):
    """`script[i]` = 第 i+1 步的 `capabilities_gained`。返回 (create_fn, advance_fn, calls)。"""
    calls = SimpleNamespace(created_settings=None, intent=None, advanced=0)

    def create_fn(cfg, root, data_dir, *, template, intent, settings):
        calls.created_settings, calls.intent = dict(settings), intent
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
        if fail_at is not None and step == fail_at:
            raise SimEngineError("LLM 输出反复不合法")
        state = SimState(step=step, summary=f"s{step}", vars={"x": step}, options=[],
                         capabilities_gained=script[step - 1] if step - 1 < len(script) else [])
        store.append_state(state)
        manifest.current_step = step
        if end_at is not None and step == end_at:
            manifest.status = "ended"
        store.save_manifest(manifest)
        return state

    return create_fn, advance_fn, calls


_SCRIPT = [
    [_cap("联盟研究网连通", "lab")],   # step 1
    [],
    [_cap("通用互联协议成为标准", "developer")],  # step 3
    [],
    [_cap("无关的新东西", "lab")],     # step 5
]


def _toy_case(**over):
    d = _case_dict(**over)
    d["alias_map"] = {}
    d["milestones"] = [
        {"id": "m1", "name": "联盟研究网连通", "year": 1990, "stage": "lab"},
        {"id": "m2", "name": "通用互联协议成为标准", "year": 1994, "requires": ["m1"], "stage": "developer"},
        {"id": "m3", "name": "浏览器普及", "year": 1996, "requires": ["m2"], "stage": "consumer"},
    ]
    d["start"] = {"intent": "1990 年的世界", "start_year": 1990, "years_per_step": 2}
    return bt.parse_case(d)


def test_run_case_end_to_end_intent_matches_and_horizon(tmp_path):
    create_fn, advance_fn, calls = _make_stubs(_SCRIPT)
    res = bt.run_case(_toy_case(steps=5), cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "run1",
                      create_fn=create_fn, advance_fn=advance_fn)
    assert calls.intent == "2000 年的世界"  # 引擎看到的是平移后的意图
    assert res["steps_run"] == 5 and res["aborted"] is None
    assert res["matches"] == {"m1": "c1", "m2": "c2", "m3": None}
    # start=2000、跑 5 步×2 年 → 范围到 2010；三条真值（2000/2004/2006）都在范围内
    assert res["metrics"]["truths_in_horizon"] == 3


def test_run_case_recall_counts_unmatched_in_horizon_and_records_verdicts(tmp_path):
    create_fn, advance_fn, _ = _make_stubs(_SCRIPT)
    out = tmp_path / "run1"
    res = bt.run_case(_toy_case(steps=5), cfg=None, workspace_root=tmp_path, out_dir=out,
                      create_fn=create_fn, advance_fn=advance_fn)
    assert abs(res["metrics"]["recall"] - 2 / 3) < 1e-9
    assert res["metrics"]["kendall_tau"] == 1.0
    saved = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert saved["run_id"] == res["run_id"] and saved["case_verified"] is False
    assert saved["caveats"] and "structural_health" in saved
    # 数据落在 out_dir/data，不碰调用方的其它目录
    assert (out / "data" / "sim_bt").exists()
    checks = reality_check.load_all(out / "data", "sim_bt")
    verdicts = sorted((c.actual_outcome, c.verdict) for c in checks)
    assert verdicts == [("浏览器普及", "diverged"), ("联盟研究网连通", "matched"), ("通用互联协议成为标准", "matched")]
    assert res["reality_checks_written"] == 3


def test_run_case_skips_out_of_horizon_truths_in_reality_checks(tmp_path):
    create_fn, advance_fn, _ = _make_stubs(_SCRIPT)
    res = bt.run_case(_toy_case(steps=2), cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "r",
                      create_fn=create_fn, advance_fn=advance_fn)  # 只跑 2 步 → 范围到 2004
    names = {c.actual_outcome for c in reality_check.load_all(tmp_path / "r" / "data", "sim_bt")}
    assert "浏览器普及" not in names  # 超出范围：不是"预测错误"，不记
    assert res["metrics"]["truths_in_horizon"] == 2  # 2000、2004 在范围内；2006 不在


def test_run_case_aborts_gracefully_and_scores_partial_history(tmp_path):
    create_fn, advance_fn, _ = _make_stubs(_SCRIPT, fail_at=3)
    res = bt.run_case(_toy_case(steps=5), cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "r",
                      create_fn=create_fn, advance_fn=advance_fn)
    assert res["steps_run"] == 2 and "SimEngineError" in res["aborted"]
    assert res["matches"]["m1"] == "c1"  # 前两步的数据没丢


def test_run_case_stops_when_simulation_ends_and_passes_settings_override(tmp_path):
    create_fn, advance_fn, calls = _make_stubs(_SCRIPT, end_at=2)
    res = bt.run_case(_toy_case(steps=5, ), cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "r",
                      settings_override={"tech_model_enabled": True}, create_fn=create_fn, advance_fn=advance_fn)
    assert res["steps_run"] == 2
    assert calls.created_settings["tech_model_enabled"] is True
    assert res["settings_override"] == {"tech_model_enabled": True}


def test_run_case_uses_injected_matcher(tmp_path):
    create_fn, advance_fn, _ = _make_stubs(_SCRIPT)

    def everything_to_m3(cands, truths):
        return {t["id"]: (cands[0]["id"] if t["id"] == "m3" else None) for t in truths}

    res = bt.run_case(_toy_case(steps=5), cfg=None, workspace_root=tmp_path, out_dir=tmp_path / "r",
                      matcher=everything_to_m3, create_fn=create_fn, advance_fn=advance_fn)
    assert res["matches"] == {"m1": None, "m2": None, "m3": "c1"} and res["matcher"] == "everything_to_m3"


# ── 覆盖 / rescore ──────────────────────────────────────────────────


def test_rescore_applies_override_recomputes_and_appends_tagged_reality_checks(tmp_path):
    create_fn, advance_fn, _ = _make_stubs(_SCRIPT)
    out = tmp_path / "r"
    res = bt.run_case(_toy_case(steps=5), cfg=None, workspace_root=tmp_path, out_dir=out,
                      create_fn=create_fn, advance_fn=advance_fn)
    before = len(reality_check.load_all(out / "data", "sim_bt"))
    # 人工裁定：候选 c3（"无关的新东西"）其实就是浏览器普及
    updated = bt.rescore(out / "result.json", {"m3": "c3"})
    assert updated["matches"]["m3"] == "c3" and updated["metrics"]["recall"] == 1.0
    assert updated["overrides"] == {"m3": "c3"} and updated["matcher"].endswith("+user_override")
    after = reality_check.load_all(out / "data", "sim_bt")
    assert len(after) == before + 1  # 只追加变化的那一条，不删旧记录
    tagged = [c for c in after if c.model_version == "backtest-override"]
    assert len(tagged) == 1 and tagged[0].verdict == "matched" and tagged[0].actual_outcome == "浏览器普及"
    # 覆盖累积
    again = bt.rescore(out / "result.json", {"m1": None})
    assert again["overrides"] == {"m3": "c3", "m1": None} and again["matches"]["m1"] is None
    with pytest.raises(bt.BacktestError):
        bt.rescore(out / "result.json", {"nope": "c1"})


# ── A/B ──────────────────────────────────────────────────────────────


def test_summarize_metric_handles_none_and_sample_size():
    s = bt.summarize_metric([1.0, None, 3.0])
    assert (s["n"], s["n_missing"], s["mean"], s["min"], s["max"]) == (2, 1, 2.0, 1.0, 3.0)
    assert s["enough_samples"] is False and s["stdev"] is not None
    assert bt.summarize_metric([None])["mean"] is None
    assert bt.summarize_metric([1, 2, 3, 4, 5])["enough_samples"] is True
    assert bt.summarize_metric([2.0])["stdev"] is None


def test_run_ab_two_arms_distributions_difference_and_sample_warning(tmp_path):
    script_a = [[_cap("联盟研究网连通", "lab")], [], [], [], []]
    script_b = _SCRIPT
    holder = {"script": script_a}

    def create_fn(cfg, root, data_dir, *, template, intent, settings):
        # 臂 B 带开关 → 用更好的脚本；证明 settings_override 真的传到了创建处
        holder["script"] = script_b if settings.get("flag") else script_a
        return _make_stubs(holder["script"])[0](cfg, root, data_dir, template=template, intent=intent, settings=settings)

    def advance_fn(cfg, root, data_dir, sim_id):
        return _make_stubs(holder["script"])[1](cfg, root, data_dir, sim_id)

    report = bt.run_ab(_toy_case(steps=5), cfg=None, workspace_root=tmp_path, out_root=tmp_path / "ab",
                       arms={"A": {}, "B": {"flag": True}}, repeats=2,
                       create_fn=create_fn, advance_fn=advance_fn)
    a, b = report["arms"]["A"], report["arms"]["B"]
    assert a["_runs"] == 2 and b["_runs"] == 2
    assert a["recall"]["values"] == [1 / 3, 1 / 3] and abs(b["recall"]["mean"] - 2 / 3) < 1e-9
    assert abs(report["mean_difference"]["by_metric"]["recall"] - 1 / 3) < 1e-9
    assert "不足以下结论" in report["verdict_note"] and report["insufficient_samples"]
    saved = json.loads((tmp_path / "ab" / "ab_report.json").read_text(encoding="utf-8"))
    assert saved["repeats"] == 2 and saved["arms_settings"]["B"] == {"flag": True}
    assert len(saved["run_dirs"]["A"]) == 2 and (tmp_path / "ab" / "A_1" / "result.json").exists()


def test_run_ab_validates_arguments(tmp_path):
    case = _toy_case()
    with pytest.raises(bt.BacktestError):
        bt.run_ab(case, cfg=None, workspace_root=tmp_path, out_root=tmp_path, arms={"A": {}}, repeats=0)
    with pytest.raises(bt.BacktestError):
        bt.run_ab(case, cfg=None, workspace_root=tmp_path, out_root=tmp_path, arms={}, repeats=1)


def test_compare_arms_single_arm_has_no_difference():
    fake = {"metrics": {"recall": 0.5, "precision": None, "kendall_tau": None,
                        "interval_error": {"mean_abs_years": None}, "prerequisite_violations": {"violations": 0},
                        "stage": {"agreement": None}}}
    out = bt.compare_arms({"only": [fake]})
    assert "mean_difference" not in out and out["arms"]["only"]["recall"]["mean"] == 0.5


# ── LLM 匹配器（假 workflow）────────────────────────────────────────


def test_llm_matcher_parses_and_sanitizes(monkeypatch, tmp_path):
    import mini_agent.workflow.runner as runner_mod
    import mini_agent.workflow.store as store_mod

    seen = {}

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            seen["name"] = name
            return object() if name == "backtest_match" else None

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            seen["inputs"] = inputs
            out = json.dumps({"matches": [
                {"truth_id": "t1", "candidate_id": "c1", "confidence": "high"},
                {"truth_id": "t2", "candidate_id": "c1"},        # 一对一：被丢弃
                {"truth_id": "t2", "candidate_id": "c404"},      # 不存在：被丢弃
            ]}, ensure_ascii=False)
            step = SimpleNamespace(step_id="match", output="```json\n" + out + "\n```", status=SimpleNamespace(value="done"))
            return SimpleNamespace(status="done", step_results=[step])

    monkeypatch.setattr(store_mod, "WorkflowStore", FakeStore)
    monkeypatch.setattr(runner_mod, "WorkflowRunner", FakeRunner)
    matcher = bt.make_llm_matcher(cfg=None, workspace_root=tmp_path)
    truths = [_truth("t1", "a", 1), _truth("t2", "b", 2)]
    cands = [_cand("c1", "a", 1)]
    assert matcher(cands, truths) == {"t1": "c1", "t2": None}
    assert seen["name"] == "backtest_match"
    assert json.loads(seen["inputs"]["truths_json"])[0]["id"] == "t1"


def test_llm_matcher_reports_missing_workflow(monkeypatch, tmp_path):
    import mini_agent.workflow.store as store_mod

    monkeypatch.setattr(store_mod, "WorkflowStore", lambda root: SimpleNamespace(load=lambda name: None))
    with pytest.raises(bt.BacktestError):
        bt.make_llm_matcher(cfg=None, workspace_root=tmp_path)([], [])


def test_backtest_match_workflow_file_is_valid_yaml_with_match_step():
    import yaml

    wf = yaml.safe_load((CASES_DIR.parent / "workflows" / "backtest_match.yaml").read_text(encoding="utf-8"))
    assert wf["name"] == "backtest_match" and wf["steps"][0]["id"] == "match"
    assert "{candidates_json}" in wf["steps"][0]["prompt"] and "{truths_json}" in wf["steps"][0]["prompt"]


# ── CLI（只测无副作用的 check / rescore 解析）────────────────────────


def _cli():
    sys.path.insert(0, str(CASES_DIR.parent / "entrypoints"))
    return importlib.import_module("backtest")


def test_cli_check_prints_anonymized_view_without_side_effects(monkeypatch, capsys):
    cli = _cli()
    monkeypatch.setattr(sys, "argv", ["backtest.py", "check", str(CASES_DIR / "internet_early.yaml")])
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "未经核对" in out and "联盟研究网" in out and "ARPANET" not in out.split("引擎将看到的意图：")[1]


def test_cli_parse_kv_json_and_string():
    cli = _cli()
    assert cli._parse_kv(["a=true", "b=3", "c=hello", 'd="x"']) == {"a": True, "b": 3, "c": "hello", "d": "x"}
    with pytest.raises(SystemExit):
        cli._parse_kv(["novalue"])
