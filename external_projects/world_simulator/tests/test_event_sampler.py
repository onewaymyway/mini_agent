"""tests/test_event_sampler.py — WP2（第二十二轮 / 阶段 P4）：外生事件采样。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md`
§4 WP2。引擎按用户声明的先验概率，在调用 LLM *之前*抽样本步事件，作为既成
事实注入提示词；种子可复现；冷却从分支历史推导；不调 LLM。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import branch_manager as bm
from world_simulator import event_sampler as es
from world_simulator.state_model import SimState

from test_fast_forward import _default_payload
from test_tech_model import _adv, _make_sim, _node

BIG = 1e9  # 每年 10 亿次 → 概率恒为 1（保证命中）


def _prior(pid="flood", rate=BIG, **extra):
    p = {"id": pid, "description": f"{pid} 发生", "rate_per_year": rate}
    p.update(extra)
    return p


def _settings(*priors, **extra):
    s = {"event_sampling_enabled": True, "event_priors": list(priors)}
    s.update(extra)
    return s


def _sample(settings, history=None, vars_=None, *, step=1, branch="main", sim_id="s"):
    return es.sample_step(settings, history or [], vars_ or {}, sim_id=sim_id, branch=branch, step=step)


def _hist(*events_by_step, elapsed=None):
    """events_by_step: [(step, [event dict...]), ...]"""
    out = []
    for step, events in events_by_step:
        out.append(SimState(step=step, summary="x", sampled_events=list(events), elapsed_days=elapsed))
    return out


# ── 开关/规整 ────────────────────────────────────────────────────────


def test_disabled_is_a_noop():
    s = {"event_priors": [_prior()]}
    assert _sample(s) == []
    assert es.build_hint(s, []) == ""
    assert es.preview(s, [], {}, sim_id="s", branch="main", step=1) == []
    assert es.safe_build_hint(None, []) == ""


def test_normalize_prior_defaults_and_rejections():
    p, why = es.normalize_prior({"description": "地震", "rate_per_year": 2})
    assert why is None and p["id"] == "地震" and p["severity"] == "medium"
    assert (p["source"], p["verified"], p["enabled"], p["cooldown_steps"]) == ("user", False, True, 0)
    assert es.normalize_prior("x")[0] is None
    assert es.normalize_prior({"rate_per_year": 1})[0] is None  # 没 id 没 description
    assert es.normalize_prior({"id": "a"})[0] is None  # 缺 rate
    assert es.normalize_prior({"id": "a", "rate_per_year": -1})[0] is None
    assert es.normalize_prior({"id": "a", "rate_per_year": True})[0] is None
    q, _ = es.normalize_prior({"id": "a", "rate_per_year": 1, "severity": "bogus", "cooldown_steps": -4,
                               "affects": "not-a-list", "verified": True, "source": "paper"})
    assert q["severity"] == "medium" and q["cooldown_steps"] == 0 and q["affects"] == []
    assert q["verified"] is True and q["source"] == "paper"


def test_get_priors_reports_problems_and_dedupes():
    priors, problems = es.get_priors(_settings(_prior("a"), {"id": "b"}, _prior("a", rate=2), "junk"))
    assert [p["id"] for p in priors] == ["a"] and priors[0]["rate_per_year"] == BIG
    assert len(problems) == 3


def test_params_override_and_invalid_ignored():
    p = es.get_params({"event_params": {"default_days_per_step": 7, "max_events_per_step": 5, "basis_window": -1}})
    assert p["default_days_per_step"] == 7 and p["max_events_per_step"] == 5 and p["basis_window"] == 3


# ── 概率/种子 ────────────────────────────────────────────────────────


def test_step_probability_poisson_formula_and_edges():
    import math
    assert abs(es.step_probability(1.0, 365.25) - (1 - math.exp(-1))) < 1e-12
    assert es.step_probability(0, 100) == 0.0 and es.step_probability(1, 0) == 0.0
    assert es.step_probability(BIG, 30) == 1.0
    assert es.step_probability(1, 10) < es.step_probability(1, 20) < es.step_probability(2, 20)


def test_draw_is_reproducible_and_sensitive_to_each_key_part():
    base = es.draw("s", "main", 3, "", "e")
    assert base == es.draw("s", "main", 3, "", "e") and 0 <= base < 1
    assert len({base, es.draw("s2", "main", 3, "", "e"), es.draw("s", "b2", 3, "", "e"),
                es.draw("s", "main", 4, "", "e"), es.draw("s", "main", 3, "x", "e"),
                es.draw("s", "main", 3, "", "f")}) == 6


def test_draw_is_uniform_enough_to_honour_the_probability():
    hits = sum(es.draw("s", "main", i, "", "e") < 0.2 for i in range(4000))
    assert 0.17 < hits / 4000 < 0.23


def test_common_random_numbers_ignore_branch_only_when_enabled():
    a = es.draw("s", "b1", 5, "", "e", common=True)
    assert a == es.draw("s", "b2", 5, "", "e", common=True)
    assert es.draw("s", "b1", 5, "", "e") != es.draw("s", "b2", 5, "", "e")
    # 公共随机数下 salt 仍然生效（重复采样靠它区分）
    assert a != es.draw("s", "b1", 5, "other", "e", common=True)


def test_each_event_draw_is_independent_of_other_priors():
    solo = _sample(_settings(_prior("a", rate=2.0)), step=7)
    both = es.preview(_settings(_prior("z", rate=2.0), _prior("a", rate=2.0)), [], {}, sim_id="s", branch="main", step=7)
    a_row = next(r for r in both if r["prior"]["id"] == "a")
    solo_row = es.preview(_settings(_prior("a", rate=2.0)), [], {}, sim_id="s", branch="main", step=7)[0]
    assert a_row["draw"] == solo_row["draw"]
    assert solo == _sample(_settings(_prior("a", rate=2.0)), step=7)  # 重跑一致


def test_certain_and_impossible_events():
    assert [e["id"] for e in _sample(_settings(_prior("a", BIG)))] == ["a"]
    assert _sample(_settings(_prior("a", 0.0))) == []


def test_sampled_entry_records_audit_fields():
    e = _sample(_settings(_prior("a", BIG, severity="high", affects=["cash"], source="paper", verified=True)))[0]
    assert e["severity"] == "high" and e["affects"] == ["cash"] and e["probability"] == 1.0
    assert e["basis_source"] == "default" and e["basis_days"] == 30.0
    assert e["source"] == "paper" and e["verified"] is True and 0 <= e["draw"] < 1


# ── 时间基准 ─────────────────────────────────────────────────────────


def test_basis_days_uses_history_median_else_placeholder():
    params = es.get_params({})
    assert es.estimate_basis_days([], params) == (30.0, "default")
    hist = [SimState(step=i, summary="s", elapsed_days=d) for i, d in enumerate([10, 1000, 20, 40, None])]
    # 最近 3 个有效值：40, 20, 1000 → 中位数 40（不被离群的 1000 拖走）
    assert es.estimate_basis_days(hist, params) == (40.0, "history_median")
    assert es.estimate_basis_days([SimState(step=1, summary="s", elapsed_days=-5)], params)[1] == "default"


def test_probability_scales_with_basis_days():
    s = _settings(_prior("a", rate=1.0))
    short = es.preview(s, [], {}, sim_id="s", branch="main", step=1)[0]["probability"]
    long_hist = [SimState(step=1, summary="s", elapsed_days=3650)]
    long_ = es.preview(s, long_hist, {}, sim_id="s", branch="main", step=2)[0]["probability"]
    assert short < 0.1 < 0.99 < long_


# ── 冷却 ─────────────────────────────────────────────────────────────


def test_cooldown_is_derived_from_branch_history():
    s = _settings(_prior("a", BIG, cooldown_steps=2))
    fired = _hist((3, [{"id": "a"}]))
    assert _sample(s, fired, step=4) == [] and _sample(s, fired, step=5) == []
    assert [e["id"] for e in _sample(s, fired, step=6)] == ["a"]
    # 没有历史记录 → 没有冷却
    assert [e["id"] for e in _sample(s, [], step=4)] == ["a"]
    # cooldown_steps=0 → 连续每步都可以
    s0 = _settings(_prior("a", BIG))
    assert [e["id"] for e in _sample(s0, fired, step=4)] == ["a"]


def test_suppressed_events_do_not_start_a_cooldown():
    s = _settings(_prior("a", BIG, cooldown_steps=5))
    hist = _hist((3, [{"id": "a", "suppressed_by_cap": True}]))
    assert es.last_fired_steps(hist) == {}
    assert [e["id"] for e in _sample(s, hist, step=4)] == ["a"]


def test_preview_reports_cooldown_left():
    s = _settings(_prior("a", BIG, cooldown_steps=3))
    row = es.preview(s, _hist((2, [{"id": "a"}])), {}, sim_id="s", branch="main", step=4)[0]
    assert row["status"] == "cooldown" and row["cooldown_left"] == 2


# ── 条件 ─────────────────────────────────────────────────────────────


def test_condition_variable_comparisons():
    v = {"res": {"cash": 100}, "stage": "growth"}
    ev = es.evaluate_condition
    assert ev(None, v)[0] and ev([], v)[0] and ev({}, v)[0]
    assert ev({"var": "res.cash", "op": ">=", "value": 100}, v)[0]
    assert not ev({"var": "res.cash", "op": ">", "value": 100}, v)[0]
    assert ev({"var": "stage", "op": "==", "value": "growth"}, v)[0]
    assert ev({"var": "stage", "op": "!=", "value": "x"}, v)[0]
    ok, why = ev({"var": "res.missing", "op": ">", "value": 1}, v)
    assert not ok and "不存在" in why  # 变量不存在 → 保守视为不满足
    assert not ev({"var": "res.cash", "op": "~", "value": 1}, v)[0]
    assert not ev({"var": "stage", "op": ">", "value": 1}, v)[0]  # 非数字做大小比较
    assert not ev("junk", v)[0] and not ev({"foo": 1}, v)[0]


def test_condition_list_is_and():
    v = {"a": 5}
    ok = {"var": "a", "op": ">", "value": 1}
    bad = {"var": "a", "op": "<", "value": 1}
    assert es.evaluate_condition([ok, ok], v)[0]
    assert not es.evaluate_condition([ok, bad], v)[0]


def test_condition_tech_stage():
    s = {"tech_state": {"nodes": [_node("ai", stage="developer")]}}
    ev = es.evaluate_condition
    assert ev({"tech": "ai", "min_stage": "expert"}, {}, s)[0]
    assert not ev({"tech": "ai", "min_stage": "consumer"}, {}, s)[0]
    assert "未登记" in ev({"tech": "ghost", "min_stage": "lab"}, {}, s)[1]
    assert not ev({"tech": "ai", "min_stage": "bogus"}, {}, s)[0]


def test_unmet_condition_blocks_sampling_even_when_certain():
    s = _settings(_prior("a", BIG, condition={"var": "cash", "op": ">", "value": 10}))
    assert _sample(s, vars_={"cash": 5}) == []
    assert [e["id"] for e in _sample(s, vars_={"cash": 50})] == ["a"]
    row = es.preview(s, [], {"cash": 5}, sim_id="s", branch="main", step=1)[0]
    assert row["status"] == "condition_unmet" and row["probability"] == 0.0


def test_disabled_prior_is_never_sampled():
    assert _sample(_settings(_prior("a", BIG, enabled=False))) == []


# ── 上限 ─────────────────────────────────────────────────────────────


def test_cap_keeps_smallest_draws_and_marks_the_rest_suppressed():
    priors = [_prior(f"e{i}") for i in range(5)]
    s = _settings(*priors, event_params={"max_events_per_step": 2})
    events = _sample(s)
    assert len(events) == 5
    kept = [e for e in events if not e.get("suppressed_by_cap")]
    assert len(kept) == 2 and all(e["draw"] <= min(x["draw"] for x in events if x.get("suppressed_by_cap")) for e in kept)
    assert _sample(s) == events  # 决定性
    hint = es.build_hint(s, events)
    assert all(f"[{e['id']}]" in hint for e in kept)
    assert all(f"[{e['id']}]" not in hint for e in events if e.get("suppressed_by_cap"))


# ── 提示词 ───────────────────────────────────────────────────────────


def test_hint_for_silent_step_encourages_calm():
    h = es.build_hint(_settings(_prior("a", 0.0)), [])
    assert "没有发生已声明的重大外部事件" in h and "平静" in h


def test_hint_lists_events_as_facts():
    s = _settings(_prior("flood", BIG, severity="high", affects=["收成"]))
    h = es.build_hint(s, _sample(s))
    assert "[flood]" in h and "严重度 high" in h and "收成" in h and "既成事实" in h
    assert "30 天" in h and "elapsed_days" in h


def test_hint_when_only_suppressed_events_counts_as_silent():
    s = _settings(_prior("a"))
    assert "没有发生" in es.build_hint(s, [{"id": "a", "suppressed_by_cap": True}])


# ── 容错/序列化 ──────────────────────────────────────────────────────


def test_safe_sample_and_hint_never_raise(monkeypatch):
    monkeypatch.setattr(es, "_evaluate", lambda *a, **k: 1 / 0)
    assert es.safe_sample_step(_settings(_prior()), [], {}, sim_id="s", branch="main", step=1) == []
    monkeypatch.undo()
    assert es.safe_build_hint(_settings(_prior()), [{"bad": 1}]) == ""  # 缺字段 → 兜底为空


def test_simstate_roundtrip_and_omitted_when_empty():
    assert "sampled_events" not in SimState(step=1, summary="s").to_dict()
    st = SimState(step=1, summary="s", sampled_events=[{"id": "a", "probability": 0.3}])
    back = SimState.from_dict(json.loads(json.dumps(st.to_dict())))
    assert back.sampled_events == st.sampled_events
    assert SimState.from_dict({"step": 1, "sampled_events": ["junk", {"id": "b"}]}).sampled_events == [{"id": "b"}]


# ── advance() 端到端 ─────────────────────────────────────────────────


def test_advance_injects_hint_persists_events_and_respects_cooldown(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, _settings(_prior("flood", BIG, cooldown_steps=1)))
    cap: dict = {}
    s1 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1), cap)
    assert "[flood]" in cap["inputs_list"][0]["sampled_events_hint"]
    assert [e["id"] for e in s1.sampled_events] == ["flood"]
    assert [e["id"] for e in store.load_history("main")[-1].sampled_events] == ["flood"]

    cap2: dict = {}
    s2 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(2), cap2)  # 冷却 1 步
    assert s2.sampled_events == [] and "没有发生" in cap2["inputs_list"][0]["sampled_events_hint"]
    s3 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(3))
    assert [e["id"] for e in s3.sampled_events] == ["flood"]


def test_advance_switch_off_is_identical_to_before(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, {"event_priors": [_prior()]})  # 有先验但开关未开
    cap: dict = {}
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1), cap)
    assert cap["inputs_list"][0]["sampled_events_hint"] == ""
    assert state.sampled_events == []
    assert "sampled_events" not in store.load_history("main")[-1].to_dict()


def test_advance_sampling_failure_degrades_to_silent_step(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _settings(_prior()))
    monkeypatch.setattr(es, "_evaluate", lambda *a, **k: 1 / 0)
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1))
    assert state.step == 1 and state.sampled_events == []


def test_advance_uses_previous_elapsed_days_as_basis(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _settings(_prior("a", rate=BIG)))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=400))
    s2 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(2))
    assert s2.sampled_events[0]["basis_days"] == 400.0 and s2.sampled_events[0]["basis_source"] == "history_median"


def test_cooldown_does_not_leak_across_branches(tmp_path, monkeypatch):
    """冷却从本分支历史推导：主线触发后，从触发前分叉出的分支不受主线冷却影响。"""
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _settings(_prior("flood", BIG, cooldown_steps=10)))
    m1 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1))
    assert [e["id"] for e in m1.sampled_events] == ["flood"]
    m2 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(2))
    assert m2.sampled_events == []  # 主线处于冷却

    bm.fork_branch(data_dir, "sim1", from_step=0)
    b1 = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1))
    assert [e["id"] for e in b1.sampled_events] == ["flood"]  # 新分支没有触发过，不在冷却


def test_repeated_branches_get_different_draws_but_same_branch_is_reproducible(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _settings(_prior("a", rate=2.0)))
    b1 = bm.fork_branch(data_dir, "sim1", from_step=0)
    b2 = bm.fork_branch(data_dir, "sim1", from_step=0)
    assert b1 != b2
    draws = {es.draw("sim1", b, 1, "", "a") for b in (b1, b2, "main")}
    assert len(draws) == 3
    common = {es.draw("sim1", b, 1, "", "a", common=True) for b in (b1, b2, "main")}
    assert len(common) == 1


def test_hint_placeholder_exists_in_both_workflows():
    root = Path(__file__).resolve().parent.parent / "workflows"
    for name in ("advance_step.yaml", "world_evolve.yaml"):
        assert "{sampled_events_hint}" in (root / name).read_text(encoding="utf-8"), name


def test_split_mode_feeds_hint_to_world_evolve(tmp_path, monkeypatch):
    import world_simulator.engine as engine_mod
    from world_simulator.engine.management import update_settings

    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s", vars={"age": 22}, options=[],
    )
    update_settings(data_dir, manifest.sim_id, split_decision_calls=True, event_sampling_enabled=True,
                    event_priors=[_prior("flood")])
    seen = {}

    class _Step:
        def __init__(self, sid):
            self.id, self.skill_name = sid, None

    class _WF:
        def __init__(self, steps):
            self.steps = steps

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return _WF([_Step(name)])

    def _res(name, payload):
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return SimpleNamespace(status="done", step_results=[
            SimpleNamespace(step_id=name, status=SimpleNamespace(value="done"), result_file=str(p))])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            sid = wf.steps[0].id
            seen[sid] = dict(inputs)
            if sid == "world_evolve":
                return _res(sid, {"next_summary": "s1", "narrative": "n", "next_vars": {"age": 23}, "key_drivers": []})
            return _res(sid, {"options": [{"id": "a", "label": "A", "description": "d"}], "decision_reason": "r"})

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)
    state = engine_mod.advance(cfg=object(), workspace_root=tmp_path / "ws", data_dir=data_dir,
                               sim_id=manifest.sim_id)
    assert "[flood]" in seen["world_evolve"]["sampled_events_hint"]
    assert [e["id"] for e in state.sampled_events] == ["flood"]


def test_both_hint_variants_ask_for_elapsed_days():
    s = _settings(_prior("a", BIG))
    assert "elapsed_days" in es.build_hint(s, _sample(s))  # 有事件
    assert "elapsed_days" in es.build_hint(s, [])  # 静默步


def test_sampling_only_records_elapsed_but_does_not_run_tech_model(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _settings(_prior("a", 0.0), tech_state={"nodes": [_node("ai")]}))
    cap: dict = {}
    state = _adv(monkeypatch, tmp_path, data_dir,
                 _default_payload(1, elapsed_days=12, tech_updates=[{"id": "ai", "stage": "consumer"}]), cap)
    assert state.elapsed_days == 12.0
    assert state.tech_updates == [] and state.tech_violations == []
    assert cap["inputs_list"][0]["tech_state_hint"] == ""


# ── 界面（app.py）────────────────────────────────────────────────────


def test_ui_sampled_events_html_escapes_and_flags_unverified_and_suppressed():
    import app

    st_ = SimpleNamespace(sampled_events=[
        {"id": "a", "description": "<b>洪水</b>", "severity": "high", "probability": 0.25, "verified": False},
        {"id": "b", "description": "地震", "severity": "low", "probability": 0.1, "verified": True,
         "suppressed_by_cap": True},
        "junk",
    ])
    out = app._sampled_events_html(st_)
    assert "&lt;b&gt;洪水&lt;/b&gt;" in out and "<b>洪水" not in out
    assert "25%" in out and "先验未核对" in out and "未注入" in out
    assert app._sampled_events_html(SimpleNamespace(sampled_events=[])) == ""
    assert app._sampled_events_html(SimpleNamespace()) == ""  # 旧 SimState 不报错


def test_advance_draws_with_the_real_sim_branch_step_and_salt(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _settings(_prior("a", rate=BIG), event_sampling_salt="S1"))
    new_branch = bm.fork_branch(data_dir, "sim1", from_step=0)
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1))
    assert state.sampled_events[0]["draw"] == round(es.draw("sim1", new_branch, 1, "S1", "a"), 6)
    assert state.sampled_events[0]["draw"] != round(es.draw("sim1", "main", 1, "S1", "a"), 6)
