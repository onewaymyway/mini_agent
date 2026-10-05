"""tests/test_anatomy_creation.py — 第二十四轮 A2：创建期拆解与向导审阅。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.2.4 / §5.4.4 / §8 A2。
覆盖：重点元素选择（优先级/null/0/领域与旧式线不参与）、`anatomy_seed` 落成草稿（LLM 无权声明出处与引擎状态、
空壳、非重点丢弃、种子键永不落盘）、创建提示（开/关/个数 0）、单次与拆分两条创建路径一致、
向导审阅与重点元素勾选的纯函数、落盘后可存取。无需真实 LLM。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import world_simulator.spec_generator as spec_mod
from world_simulator import anatomy as an
from world_simulator import element_registry as er
from world_simulator.engine.materialize import materialize_simulation
from world_simulator.store import SimStore


def _dom(i):
    return {"id": i, "label": i, "kind": "domain", "future_tree": {"branches": []}}


def _el(i, parent="tech", **kw):
    d = {"id": i, "label": i, "kind": "element", "element_type": "project", "parent": parent}
    d.update(kw)
    return d


SEED = {
    "components": [{"id": "c1", "name": "构件", "readiness": 0.4, "basis": {"state": "sourced", "evidence_ids": ["x"]}}],
    "metrics": [{"id": "m1", "unit": "u", "current": {"value": 3, "as_of": "2026", "basis": {"state": "user_confirmed"}}}],
    "bottlenecks": [{"id": "b1", "status": "resolved", "resolution_paths": [
        {"id": "p1", "p_success": 0.5, "status": "succeeded", "resolve_at_day": 5}]}],
    "milestones": [{"id": "ms", "criteria": {"bottleneck": "b1", "status": "resolved"},
                    "reached_step": 2, "reached_sim_day": 9}],
    "meta": {"anatomy_status": "verified", "key": False, "template": "zzz"},
}


# ── 重点元素选择 ─────────────────────────────────────────────────────


def sel(lines, edges=None, n=5):
    return an.select_key_elements(lines, edges, {"key_element_count": n})


def test_priority_endpoint_then_technology_then_order():
    lines = [_dom("tech"), _el("a"), _el("b", element_type="technology"), _el("c"), _el("d")]
    assert sel(lines, [{"from_line_id": "d", "to_line_id": "tech"}], n=3) == ["d", "b", "a"]
    assert sel(lines, None, n=4) == ["b", "a", "c", "d"]


def test_null_unlimited_zero_none_and_nonelements_excluded():
    lines = [_dom("tech"), _el("a"), {"id": "old", "label": "旧式"}, _el("r", status="retired"), _el("b")]
    assert sel(lines, n=None) == ["a", "b"]  # 领域/旧式独立线/已退场不参与
    assert sel(lines, n=0) == []
    assert sel(lines, n=1) == ["a"]


# ── seed → 草稿 ──────────────────────────────────────────────────────


def test_seed_strips_claims_and_engine_state_and_rewrites_meta():
    a, _ = an.seed_to_anatomy(SEED, "technology")
    assert all("basis" not in x for x in a["components"] + a["metrics"])
    assert "basis" not in a["metrics"][0]["current"]
    p = a["bottlenecks"][0]["resolution_paths"][0]
    assert "status" not in p and "resolve_at_day" not in p and p["p_success"] == 0.5
    assert a["bottlenecks"][0]["status"] == "resolved"  # 起点已解决的瓶颈是合法的声明，保留
    assert "reached_step" not in a["milestones"][0] and "reached_sim_day" not in a["milestones"][0]
    assert a["meta"] == {"anatomy_status": "draft", "key": True, "template": "technology"}
    assert an.basis_stats(a)["llm_prior"] == an.basis_stats(a)["total"]


def lines_with_seeds():
    return [_dom("tech"), _el("a", anatomy_seed=SEED), _el("b"), _el("c", anatomy_seed=SEED)]


def test_apply_seeds_key_only_shells_and_seed_key_never_persists():
    lines = lines_with_seeds()
    notes = an.apply_seeds(lines, None, {"anatomy_params": {"key_element_count": 2}})
    by = {x["id"]: x for x in lines}
    assert an.get_anatomy(by["a"])["components"] and an.is_key(by["a"])
    assert by["b"]["anatomy"] == {"meta": {"anatomy_status": "none", "key": True, "template": "project"}}
    assert "anatomy" not in by["c"]
    assert any("c" in n and "不是重点元素" in n for n in notes)
    assert all("anatomy_seed" not in x for x in lines)


def test_apply_seeds_disabled_only_strips_seed_key():
    lines = lines_with_seeds()
    an.apply_seeds(lines, None, {"anatomy_enabled": False})
    assert all("anatomy" not in x and "anatomy_seed" not in x for x in lines)
    lines = lines_with_seeds()
    an.apply_seeds(lines, None, {"element_modeling_enabled": False})
    assert all("anatomy" not in x for x in lines)


def test_empty_or_garbage_seed_gives_shell():
    lines = [_dom("tech"), _el("a", anatomy_seed={"components": "x"}), _el("b", anatomy_seed="junk")]
    an.apply_seeds(lines, None, {})
    assert all(x["anatomy"]["meta"]["anatomy_status"] == "none" for x in lines[1:])


def test_prepare_created_lines_wires_seeds_and_drops_candidate_seeds():
    lines = [_dom("tech")] + [_el(f"e{i}", anatomy_seed=SEED) for i in range(6)]
    kept, cut = er.prepare_created_lines(lines, None, {"element_params": {"create_max_elements": 3},
                                                       "anatomy_params": {"key_element_count": 2}})
    els = [x for x in kept if x["kind"] == "element"]
    assert len(els) == 3 and len(cut) == 3
    assert sum(1 for x in els if "anatomy" in x) == 2
    assert all("anatomy_seed" not in x for x in kept + cut)


# ── 创建提示 ─────────────────────────────────────────────────────────


def test_create_hint_on_off_and_count():
    h = an.create_hint({})
    assert "anatomy_seed" in h and "大约 5 个" in h and "LLM 先验" in h
    assert "不限个数" in an.create_hint({"anatomy_params": {"key_element_count": None}})
    assert an.create_hint({"anatomy_enabled": False}) == ""
    assert an.create_hint({"anatomy_params": {"key_element_count": 0}}) == ""
    assert an.create_hint({"element_modeling_enabled": False}) == ""


def test_hint_in_creation_prompt_only_when_enabled():
    on = spec_mod._resolve_causal_lines_hint({}, stage="create")
    off = spec_mod._resolve_causal_lines_hint({"anatomy_enabled": False}, stage="create")
    assert "anatomy_seed" in on and "anatomy_seed" not in off
    assert on.startswith(off)  # 关闭时的提示是开启时的逐字节前缀（只在末尾追加）
    adv = spec_mod._resolve_causal_lines_hint({"element_modeling_enabled": True}, stage="advance")
    assert "anatomy_seed" not in adv  # 推进阶段不受影响


# ── 向导审阅 ─────────────────────────────────────────────────────────


def draft_anatomy():
    a, _ = an.seed_to_anatomy(SEED, "project")
    return a


def test_apply_review_confirm_reject_keep_and_status():
    a = draft_anatomy()
    out = an.apply_review(a, {("components", "c1"): "confirm", ("metrics", "m1"): "reject"})
    assert an.basis_of(out["components"][0])["state"] == "user_confirmed"
    assert "metrics" not in out
    assert out["meta"]["anatomy_status"] == "draft"  # 还有未确认条目
    ids = {("components", "c1"): "confirm", ("metrics", "m1"): "confirm", ("bottlenecks", "b1"): "confirm",
           ("milestones", "ms"): "confirm"}
    out = an.apply_review(a, ids)
    assert out["meta"]["anatomy_status"] == "reviewed"
    assert an.basis_of(out["metrics"][0]["current"])["state"] == "user_confirmed"
    assert a == draft_anatomy()  # 入参不被修改


def test_confirm_keeps_existing_evidence_and_all_rejected_goes_none():
    a = an.normalize_anatomy({"components": [{"id": "c", "basis": {"state": "sourced", "evidence_ids": ["e1"]}}],
                              "meta": {"key": True}})
    out = an.apply_review(a, {("components", "c"): "confirm"})
    assert out["components"][0]["basis"] == {"state": "user_confirmed", "evidence_ids": ["e1"]}
    out = an.apply_review(a, {("components", "c"): "reject"})
    assert out == {"meta": {"key": True, "anatomy_status": "none"}}
    assert an.apply_review(None, {}) == {}


def test_apply_key_selection_adds_shell_and_unmarks_without_deleting():
    lines = [_dom("tech"), _el("a", anatomy=draft_anatomy()), _el("b"), {"id": "old"}]
    out = an.apply_key_selection(lines, ["b", "tech", "old"])
    by = {x["id"]: x for x in out}
    assert by["b"]["anatomy"]["meta"]["key"] is True and by["b"]["anatomy"]["meta"]["anatomy_status"] == "none"
    assert by["a"]["anatomy"]["components"] and by["a"]["anatomy"]["meta"]["key"] is False  # 内容保留
    assert "anatomy" not in by["tech"] and "anatomy" not in by["old"]
    assert lines[1]["anatomy"]["meta"]["key"] is True  # 入参不变


def test_apply_creation_review_none_keys_leaves_lines_and_applies_decisions():
    lines = [_dom("tech"), _el("a", anatomy=draft_anatomy())]
    same = an.apply_creation_review(lines, None, {})
    assert same == lines and same is not lines
    out = an.apply_creation_review(lines, None, {("a", "components", "c1"): "confirm", ("a", "metrics", "m1"): "keep"})
    assert an.basis_of(out[1]["anatomy"]["components"][0])["state"] == "user_confirmed"
    assert an.basis_of(out[1]["anatomy"]["metrics"][0])["state"] == "llm_prior"
    out = an.apply_creation_review(lines, ["a"], {("a", "components", "c1"): "reject", ("a", "metrics", "m1"): "reject",
                                                    ("a", "bottlenecks", "b1"): "reject", ("a", "milestones", "ms"): "reject"})
    assert out[1]["anatomy"]["meta"]["anatomy_status"] == "none"


# ── 端到端：两条创建路径 + 落盘 ──────────────────────────────────────


class _Status:
    def __init__(self, v): self.value = v


class _Step:
    def __init__(self, i): self.id, self.skill_name = i, None


class _WF:
    def __init__(self, steps): self.steps = steps


def _stub(monkeypatch, tmp_path, payload):
    class FakeStore:
        def __init__(self, root): pass

        def load(self, name):
            return _WF([_Step({"generate_scenario": "draft", "world_builder": "world_builder",
                               "causal_space_builder": "causal_space_builder"}[name])])

    class FakeRunner:
        def __init__(self, cfg): pass

        def run(self, wf, inputs):
            sid = wf.steps[0].id
            data = payload if sid in ("draft", "causal_space_builder") else {"title": "t", "summary": "s", "vars": {}}
            p = tmp_path / f"{sid}.json"
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return SimpleNamespace(status="done", step_results=[
                SimpleNamespace(step_id=sid, status=_Status("done"), result_file=str(p))])

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)


def _payload():
    return {"title": "t", "summary": "s", "vars": {}, "options": [],
            "causal_lines": [_dom("tech"), _el("e1", element_type="technology", anatomy_seed=SEED),
                             _el("e2", anatomy_seed=SEED), _el("e3")],
            "declared_causal_graph": []}


@pytest.mark.parametrize("split", [False, True])
def test_both_creation_paths_produce_same_drafts(tmp_path, monkeypatch, split):
    _stub(monkeypatch, tmp_path, _payload())
    draft = spec_mod.generate_scenario(
        cfg=object(), workspace_root=tmp_path / "ws", template="life_sim", intent="i",
        settings={"split_creation_calls": split, "anatomy_params": {"key_element_count": 2}},
    )
    by = {x["id"]: x for x in draft.causal_lines}
    assert an.get_anatomy(by["e1"])["meta"]["template"] == "technology" and an.get_anatomy(by["e1"])["components"]
    assert an.is_key(by["e2"])
    assert "anatomy" not in by["e3"]  # 第 3 个不是重点
    assert all("anatomy_seed" not in x for x in draft.causal_lines)


def test_switch_off_creation_has_no_anatomy(tmp_path, monkeypatch):
    _stub(monkeypatch, tmp_path, _payload())
    draft = spec_mod.generate_scenario(
        cfg=object(), workspace_root=tmp_path / "ws", template="life_sim", intent="i",
        settings={"anatomy_enabled": False},
    )
    assert all("anatomy" not in x and "anatomy_seed" not in x for x in draft.causal_lines)


def test_materialized_instance_keeps_anatomy_and_flag(tmp_path):
    lines = [_dom("tech"), _el("a", anatomy=draft_anatomy())]
    m = materialize_simulation(tmp_path / "d", template="life_sim", intent="i", title="t", summary="s",
                               vars={"a": 1}, options=[], settings={"causal_lines": lines})
    mf = SimStore.for_root(tmp_path / "d", m.sim_id).load_manifest()
    assert mf.settings["anatomy_enabled"] is True
    assert an.read_anatomy(mf.settings, "a")["meta"]["key"] is True
