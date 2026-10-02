"""tests/test_event_prior_proposals.py — 第二十二轮 P7：`generate_scenario` 提议外生事件先验。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §10 P7 与 §4 WP2。
LLM 提议的先验默认**未确认、不参与抽样**；用户在向导里逐条采用后才进入 `settings.event_priors`。
验证：提议规整（强制来源/未核对/未确认、非法/重复/越界条件/超限丢弃）、未确认先验不抽样且不影响别的
事件的抽样值、采用（改频率→user_edited、没勾选丢弃）、提示词开关、`ScenarioDraft` 与
`generate_scenario`（桩）端到端、workflow 占位符与界面接线的静态检查。**没有在真实 LLM 下跑过。**
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import event_sampler as es
import world_simulator.spec_generator as spec_mod

ROOT = Path(__file__).resolve().parent.parent
BIG = 1e9


def _raw(pid="quake", rate=0.5, **extra):
    d = {"id": pid, "description": f"{pid} 发生", "rate_per_year": rate}
    d.update(extra)
    return d


# ── 规整：来源/确认语义 ──────────────────────────────────────────────


def test_hand_written_priors_default_to_confirmed_and_old_shape_unchanged():
    p, _ = es.normalize_prior({"id": "a", "rate_per_year": 1})
    assert p["confirmed"] is True and p["source"] == "user" and p["rationale"] == "" and p["rate_range"] is None
    q, _ = es.normalize_prior({"id": "a", "rate_per_year": 1, "source": "农业年鉴"})
    assert q["confirmed"] is True


def test_llm_estimate_source_defaults_to_unconfirmed_unless_explicit():
    p, _ = es.normalize_prior({"id": "a", "rate_per_year": 1, "source": "llm_estimate"})
    assert p["confirmed"] is False
    q, _ = es.normalize_prior({"id": "a", "rate_per_year": 1, "source": "llm_estimate", "confirmed": True})
    assert q["confirmed"] is True
    r, _ = es.normalize_prior({"id": "a", "rate_per_year": 1, "confirmed": False})
    assert r["confirmed"] is False  # 手写也可以显式标未确认
    s, _ = es.normalize_prior({"id": "a", "rate_per_year": 1, "source": "llm_estimate", "confirmed": "yes"})
    assert s["confirmed"] is False  # 非 bool 的 confirmed 不算数


def test_rate_range_validation():
    ok, _ = es.normalize_prior(_raw(rate_range={"low": 0.1, "high": 2}))
    assert ok["rate_range"] == {"low": 0.1, "high": 2.0}
    for bad in ({"low": 3, "high": 1}, {"low": -1, "high": 1}, {"low": "a", "high": 1}, {"low": 1}, "x", None):
        assert es.normalize_prior(_raw(rate_range=bad))[0]["rate_range"] is None


# ── 未确认先验不抽样 ─────────────────────────────────────────────────


def _settings(*priors):
    return {"event_sampling_enabled": True, "event_priors": list(priors)}


def test_unconfirmed_prior_is_never_sampled_even_with_certain_probability():
    s = _settings(_raw("a", BIG, source="llm_estimate"), _raw("b", BIG))
    ev = es.sample_step(s, [], {}, sim_id="s", branch="main", step=1)
    assert [e["id"] for e in ev] == ["b"]
    rows = es.preview(s, [], {}, sim_id="s", branch="main", step=1)
    assert {r["prior"]["id"]: r["status"] for r in rows} == {"a": "unconfirmed", "b": "hit"}
    assert rows[0]["draw"] is None and rows[0]["probability"] == 0.0
    assert es.unconfirmed_ids(s) == ["a"]


def test_confirming_a_prior_does_not_change_other_events_draws():
    a = _raw("a", 0.7, source="llm_estimate")
    b = _raw("b", 0.7)
    d_before = es.preview(_settings(a, b), [], {}, sim_id="s", branch="main", step=3)[1]["draw"]
    d_after = es.preview(_settings({**a, "confirmed": True}, b), [], {}, sim_id="s", branch="main", step=3)[1]["draw"]
    assert d_before == d_after  # 种子只含 id，增删/确认别的先验不扰动


def test_unconfirmed_only_means_calm_step_hint_and_no_events():
    s = _settings(_raw("a", BIG, source="llm_estimate"))
    assert es.sample_step(s, [], {}, sim_id="s", branch="main", step=1) == []
    assert "没有发生已声明的重大外部事件" in es.build_hint(s, [])


# ── 提议规整 ─────────────────────────────────────────────────────────


def test_sanitize_forces_source_unverified_unconfirmed_and_ignores_llm_claims():
    out, problems = es.sanitize_proposals([_raw("a", source="农业年鉴 2020", verified=True, confirmed=True, enabled=False)])
    assert problems == [] and len(out) == 1
    p = out[0]
    assert (p["source"], p["verified"], p["confirmed"], p["enabled"]) == ("llm_estimate", False, False, True)


def test_sanitize_drops_invalid_duplicate_and_reports_reasons():
    raw = [
        "junk", {"description": "无频率"}, _raw("neg", -1), _raw("nan", "abc"), {"rate_per_year": 1},
        _raw("ok"), _raw("ok", 2), _raw("old"),
    ]
    out, problems = es.sanitize_proposals(raw, existing_ids=["old"])
    assert [p["id"] for p in out] == ["ok"]
    assert len(problems) == 7
    assert any("重复" in x and "ok" in x for x in problems) and any("重复" in x and "old" in x for x in problems)


def test_sanitize_drops_condition_referencing_unknown_var_but_keeps_tech_conditions():
    vars_ = {"resources": {"land": 3}}
    good = _raw("g", condition={"var": "resources.land", "op": ">", "value": 0})
    bad = _raw("b", condition={"var": "resources.gold", "op": ">", "value": 0})
    nested_bad = _raw("n", condition=[{"var": "resources.land", "op": ">", "value": 0},
                                      {"var": "nope", "op": "==", "value": 1}])
    tech = _raw("t", condition={"tech": "net", "min_stage": "developer"})
    out, problems = es.sanitize_proposals([good, bad, nested_bad, tech], vars_=vars_)
    assert [p["id"] for p in out] == ["g", "t"]
    assert any("resources.gold" in x for x in problems) and any("nope" in x for x in problems)
    # 没给 vars_ 就不做变量检查
    assert len(es.sanitize_proposals([bad])[0]) == 1


def test_sanitize_limit_and_non_list_and_empty():
    many = [_raw(f"e{i}") for i in range(es.MAX_PROPOSALS + 5)]
    out, problems = es.sanitize_proposals(many)
    assert len(out) == es.MAX_PROPOSALS and any("超过" in x for x in problems)
    assert es.sanitize_proposals("oops")[1] == ["event_priors 不是数组，整体忽略"]
    for empty in (None, "", [], {}):
        assert es.sanitize_proposals(empty) == ([], [])


# ── 采用 ─────────────────────────────────────────────────────────────


def _proposals():
    return es.sanitize_proposals([_raw("a", 0.5), _raw("b", 2), _raw("c", 1)])[0]


def test_adopt_only_checked_ones_and_marks_edited_rate():
    adopted = es.adopt_proposals(_proposals(), {"a": 0.5, "b": 4.0})
    assert [p["id"] for p in adopted] == ["a", "b"]  # c 没勾选 → 丢弃
    by = {p["id"]: p for p in adopted}
    assert by["a"]["source"] == "llm_estimate" and by["a"]["rate_per_year"] == 0.5
    assert by["b"]["source"] == "user_edited" and by["b"]["rate_per_year"] == 4.0
    assert all(p["confirmed"] is True and p["verified"] is False for p in adopted)


def test_adopted_priors_are_sampled_and_still_flagged_unverified():
    adopted = es.adopt_proposals([es.sanitize_proposals([_raw("a", BIG)])[0][0]], {"a": BIG})
    s = {"event_sampling_enabled": True, "event_priors": adopted}
    ev = es.sample_step(s, [], {}, sim_id="s", branch="main", step=1)
    assert [e["id"] for e in ev] == ["a"] and ev[0]["verified"] is False and ev[0]["source"] == "llm_estimate"


def test_adopt_rejects_bad_rates_and_does_not_mutate_input():
    props = _proposals()
    snapshot = json.dumps(props, sort_keys=True)
    assert es.adopt_proposals(props, {"a": -1, "b": "x", "c": True}) == []
    assert es.adopt_proposals(props, {}) == [] and es.adopt_proposals([], {"a": 1}) == []
    es.adopt_proposals(props, {"a": 9})
    assert json.dumps(props, sort_keys=True) == snapshot


def test_adopted_roundtrip_through_get_priors_is_confirmed():
    adopted = es.adopt_proposals(_proposals(), {"a": 1.0})
    priors, problems = es.get_priors({"event_priors": adopted})
    assert problems == [] and priors[0]["confirmed"] is True and priors[0]["rationale"] == ""


# ── 提示词 ───────────────────────────────────────────────────────────


def test_hint_empty_unless_enabled_and_lists_existing_ids():
    assert es.build_proposal_hint({}) == "" and es.build_proposal_hint(None) == ""
    assert es.safe_build_proposal_hint({"event_priors_proposal_enabled": False}) == ""
    h = es.build_proposal_hint({"event_priors_proposal_enabled": True})
    assert "event_priors" in h and "提议" in h and "不要编造" in h and "已经声明" not in h
    h2 = es.build_proposal_hint({"event_priors_proposal_enabled": True, "event_priors": [_raw("quake")]})
    assert "quake" in h2 and "不要重复提议" in h2
    assert "{" not in h and "}" not in h  # 不含会被误判的大括号


# ── ScenarioDraft / generate_scenario（桩）──────────────────────────


def test_scenario_draft_from_dict_sanitizes_against_initial_vars():
    data = {"title": "t", "summary": "s", "vars": {"cash": 10}, "options": [],
            "event_priors": [_raw("ok", condition={"var": "cash", "op": ">", "value": 0}),
                             _raw("bad", condition={"var": "gold", "op": ">", "value": 0})]}
    d = spec_mod.ScenarioDraft.from_dict(data)
    assert [p["id"] for p in d.event_priors] == ["ok"] and len(d.event_prior_problems) == 1
    assert spec_mod.ScenarioDraft.from_dict({"title": "t", "summary": "s", "vars": {}, "options": []}).event_priors == []


class _Status:
    def __init__(self, v):
        self.value = v


def _stub_workflow(monkeypatch, tmp_path, payload, seen_inputs):
    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return SimpleNamespace(steps=[SimpleNamespace(id="draft", skill_name=None)])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            seen_inputs.update(inputs)
            f = tmp_path / "scenario.json"
            f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return SimpleNamespace(status="done", step_results=[
                SimpleNamespace(step_id="draft", status=_Status("done"), result_file=str(f))])

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)


_PAYLOAD = {"title": "t", "summary": "s", "vars": {"cash": 1}, "options": [{"id": "o", "label": "l", "description": "d"}],
            "event_priors": [_raw("quake", 0.2, rationale="凭常识估计", rate_range={"low": 0.1, "high": 0.5})]}


def test_generate_scenario_keeps_proposals_only_when_enabled(tmp_path, monkeypatch):
    seen = {}
    _stub_workflow(monkeypatch, tmp_path, _PAYLOAD, seen)
    on = spec_mod.generate_scenario(cfg=object(), workspace_root=tmp_path, template="life_sim", intent="x",
                                    settings={"event_priors_proposal_enabled": True})
    assert [p["id"] for p in on.event_priors] == ["quake"] and on.event_priors[0]["confirmed"] is False
    assert "event_priors" in seen["event_priors_hint"]
    seen.clear()
    off = spec_mod.generate_scenario(cfg=object(), workspace_root=tmp_path, template="life_sim", intent="x",
                                     settings={})
    assert off.event_priors == [] and off.event_prior_problems == []  # LLM 自作主张输出的也被忽略
    assert seen["event_priors_hint"] == ""


def test_generate_scenario_excludes_ids_already_declared(tmp_path, monkeypatch):
    seen = {}
    _stub_workflow(monkeypatch, tmp_path, _PAYLOAD, seen)
    d = spec_mod.generate_scenario(
        cfg=object(), workspace_root=tmp_path, template="life_sim", intent="x",
        settings={"event_priors_proposal_enabled": True, "event_priors": [_raw("quake")]})
    assert d.event_priors == [] and any("重复" in x for x in d.event_prior_problems)
    assert "quake" in seen["event_priors_hint"]


def test_split_creation_path_also_carries_proposals(tmp_path, monkeypatch):
    seen = {}
    calls = []

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            calls.append(name)
            return SimpleNamespace(steps=[SimpleNamespace(id=name, skill_name=None)])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            seen.update(inputs)
            step_id = wf.steps[0].id
            payload = (
                {"title": "t", "summary": "s", "vars": {"cash": 1}, "event_priors": _PAYLOAD["event_priors"]}
                if step_id == "world_builder" else {"options": [{"id": "o", "label": "l", "description": "d"}]}
            )
            f = tmp_path / f"{step_id}.json"
            f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return SimpleNamespace(status="done", step_results=[
                SimpleNamespace(step_id=step_id, status=_Status("done"), result_file=str(f))])

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)
    d = spec_mod.generate_scenario(cfg=object(), workspace_root=tmp_path, template="life_sim", intent="x",
                                   settings={"split_creation_calls": True, "event_priors_proposal_enabled": True})
    assert calls == ["world_builder", "causal_space_builder"]
    assert [p["id"] for p in d.event_priors] == ["quake"] and d.options[0].id == "o"


# ── 静态检查：workflow 占位符 / 界面接线 ─────────────────────────────


def test_workflows_declare_event_priors_hint_placeholder_and_runner_inputs_provide_it():
    for name in ("generate_scenario.yaml", "world_builder.yaml"):
        text = (ROOT / "workflows" / name).read_text(encoding="utf-8")
        assert text.count("{event_priors_hint}") == 1, name
        # 没有新增含 '.' 的非占位符大括号（见 test_workflow_prompt_placeholders）
        for m in re.finditer(r"\{([^}]+)\}", text):
            if "." in m.group(1):
                assert re.match(r"^[\w\-]+(\.[\w\-:\[\]]+)?$", m.group(1)), (name, m.group(0))


def test_resolve_hints_is_untouched_so_advance_inputs_do_not_change():
    assert "event_priors_hint" not in spec_mod.resolve_hints({}, stage="advance")
    assert "event_priors_hint" not in spec_mod.resolve_hints({}, stage="create")


def test_app_wires_proposal_ui_and_cleanup():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    for needle in ("event_priors_proposal_enabled", "event_mod.adopt_proposals", "ep_adopt_", "ep_rate_",
                   "_clear_event_prior_widget_state()", "create_propose_event_priors", "unconfirmed",
                   'create_settings.pop("event_priors_proposal_enabled"'):
        assert needle in src, needle
    assert src.count("_clear_event_prior_widget_state()") >= 3  # 定义 + 两处换草稿
