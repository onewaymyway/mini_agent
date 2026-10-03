"""tests/test_element_creation.py — 第二十三轮 E2：创建阶段的"领域→元素"展开。

设计依据：`next_doc/world_simulator_element_causal_lines_plan.md` §4.3 / §5.1 / §6 E2。
覆盖：预算参数（`null`=不限、0≠不限、非法回退）、规整/去重/parent 解析、
`lifecycle_seed`、预算裁剪（优先级、领域不裁、候选不丢）、创建 prompt 的新旧分支、
`generate_scenario` 端到端（单次调用 / 拆分调用）、旧草稿（无 `kind`）仍可落盘。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import world_simulator.spec_generator as spec_mod
from world_simulator import element_registry as er
from world_simulator import tech_model as tm
from world_simulator.engine.materialize import materialize_simulation
from world_simulator.store import SimStore


# ── 预算参数 ─────────────────────────────────────────────────────────


def test_params_defaults_override_unlimited_and_zero():
    assert er.get_params(None) == {"create_max_elements": 20, "create_max_per_domain": 6}
    p = er.get_params({"element_params": {"create_max_elements": None, "create_max_per_domain": 0}})
    assert p == {"create_max_elements": None, "create_max_per_domain": 0}  # None=不限；0 就是 0 个
    assert er.get_params({"element_params": {"create_max_elements": 5.0}})["create_max_elements"] == 5


@pytest.mark.parametrize("bad", [-1, 2.5, "7", True, [], {}])
def test_params_invalid_values_fall_back_to_default_and_are_reported(bad):
    s = {"element_params": {"create_max_elements": bad}}
    assert er.get_params(s)["create_max_elements"] == 20
    assert er.invalid_param_keys(s) == ["create_max_elements"]


def test_params_ignore_non_dict_and_unknown_keys():
    assert er.get_params({"element_params": "x"}) == er.DEFAULT_PARAMS
    assert er.invalid_param_keys({"element_params": {"whatever": -1}}) == []


def test_creation_enabled_defaults_on_and_only_explicit_false_turns_off():
    assert er.creation_enabled(None) and er.creation_enabled({}) and er.creation_enabled({"element_modeling_enabled": True})
    assert not er.creation_enabled({"element_modeling_enabled": False})


# ── 规整 ─────────────────────────────────────────────────────────────


def _tree():
    return {"branches": [{"id": "b1", "description": "快"}, {"id": "b2", "description": "慢"}]}


def _dom(i, **kw):
    return {"id": i, "label": i, "kind": "domain", "future_tree": _tree(), **kw}


def _el(i, parent=None, **kw):
    d = {"id": i, "label": i, "kind": "element", "future_tree": _tree(), **kw}
    if parent:
        d["parent"] = parent
    return d


def test_prepare_normalizes_stamps_and_resolves_parent():
    raw = [
        _dom("tech", label="技术"),
        {"id": "gpu", "label": "GPU", "parent": " TECH ", "element_type": "technology"},  # parent 规范化匹配，无 kind
        _el("lonely", parent="ghost"),                                                  # 未知领域 → 清空 parent
        "junk", {"label": "没有 id"}, {"id": "  "},
    ]
    kept, cut = er.prepare_created_lines(raw, [], {})
    by_id = {x["id"]: x for x in kept}
    assert cut == [] and set(by_id) == {"tech", "gpu", "lonely"}
    assert by_id["gpu"]["parent"] == "tech" and by_id["gpu"]["kind"] == "element"
    assert "parent" not in by_id["lonely"]  # 不猜领域
    for x in kept:
        assert x["origin"] == "seed" and x["born_step"] == 0 and x["profile_status"] == "complete"
    assert "parent" not in by_id["tech"]


def test_prepare_dedupes_by_normalized_id_alias_and_label_first_wins():
    raw = [_el("gpt5", aliases=["GPT-5 Pro"]), _el("gpt_5"), _el("x", aliases=["GPT 5 PRO"]), _el("other")]
    kept, _ = er.prepare_created_lines(raw, [], {})
    assert [x["id"] for x in kept] == ["gpt5", "other"]
    # 只有 label 相同不算重复（宁可漏合并）
    kept2, _ = er.prepare_created_lines([_el("a", label="项目"), _el("b", label="项目")], [], {})
    assert [x["id"] for x in kept2] == ["a", "b"]


def test_prepare_legacy_lines_without_kind_are_untouched():
    legacy = [{"id": "tech", "label": "技术线", "time_granularity": "年", "future_tree": _tree()}]
    kept, cut = er.prepare_created_lines(legacy, [], {})
    assert kept == legacy and cut == []


def test_prepare_domain_never_keeps_parent():
    kept, _ = er.prepare_created_lines([_dom("a"), _dom("b", parent="a")], [], {})
    assert all("parent" not in x for x in kept)


def test_lifecycle_seed_becomes_lifecycle_via_tech_normalization():
    raw = [_dom("tech"), _el("gpu", parent="tech", element_type="technology",
                              lifecycle_seed={"stage": "developer", "preexisting": True}),
           _el("proj", parent="tech", lifecycle_seed={"stage": "lab"}),
           _el("bad", parent="tech", lifecycle_seed="nope")]
    kept, _ = er.prepare_created_lines(raw, [], {})
    by_id = {x["id"]: x for x in kept}
    assert all("lifecycle_seed" not in x for x in kept)
    gpu = by_id["gpu"]
    assert gpu["lifecycle"]["stage"] == "developer" and gpu["lifecycle"]["preexisting"] is True
    assert by_id["proj"]["element_type"] == "technology"  # 给了 seed 就是技术元素
    assert "lifecycle" not in by_id["bad"]
    node = tm.get_nodes({"element_modeling_enabled": True, "causal_lines": kept})
    assert {n["id"] for n in node} == {"gpu", "proj"} and node[0]["name"] == "gpu"


# ── 预算裁剪 ─────────────────────────────────────────────────────────


def _many(n, parent="tech"):
    return [_el(f"e{i}", parent=parent) for i in range(n)]


def test_total_cap_keeps_domains_and_original_order_and_returns_candidates():
    lines = [_dom("tech")] + _many(30)
    kept, cut = er.prepare_created_lines(lines, [], {"element_params": {"create_max_per_domain": None}})
    assert [x["id"] for x in kept][:1] == ["tech"] and len(kept) == 1 + 20 and len(cut) == 10
    assert [x["id"] for x in cut] == [f"e{i}" for i in range(20, 30)]  # 候选不丢，顺序保持
    assert {x["id"] for x in kept} | {x["id"] for x in cut} == {x["id"] for x in lines}


def test_per_domain_cap():
    lines = [_dom("a"), _dom("b")] + _many(8, "a") + _many(2, "b")
    lines = lines[:2] + [dict(x, id=f"{x['id']}_{x['parent']}") for x in lines[2:]]
    kept, cut = er.prepare_created_lines(lines, [], {})
    counts = {}
    for x in kept:
        if x.get("parent"):
            counts[x["parent"]] = counts.get(x["parent"], 0) + 1
    assert counts == {"a": 6, "b": 2} and len(cut) == 2


def test_null_means_unlimited_but_zero_means_none():
    lines = [_dom("tech")] + _many(40)
    kept, cut = er.prepare_created_lines(lines, [], {"element_params": {"create_max_elements": None, "create_max_per_domain": None}})
    assert len(kept) == 41 and cut == []
    kept0, cut0 = er.prepare_created_lines(lines, [], {"element_params": {"create_max_elements": 0}})
    assert [x["id"] for x in kept0] == ["tech"] and len(cut0) == 40


def test_priority_edge_endpoints_then_technology_then_order():
    lines = [_dom("tech"), _el("plain1", "tech"), _el("plain2", "tech"),
             _el("techy", "tech", element_type="technology"), _el("linked", "tech")]
    edges = [{"from_line_id": "linked", "to_line_id": "tech"}]
    kept, cut = er.prepare_created_lines(lines, edges, {"element_params": {"create_max_elements": 2}})
    assert {x["id"] for x in kept if x["kind"] == "element"} == {"linked", "techy"}
    assert [x["id"] for x in cut] == ["plain1", "plain2"]
    # 保留项仍按原顺序
    assert [x["id"] for x in kept] == ["tech", "techy", "linked"]


def test_unparented_elements_count_toward_total_only():
    lines = [_dom("a")] + [_el(f"u{i}") for i in range(5)]
    kept, cut = er.prepare_created_lines(lines, [], {"element_params": {"create_max_elements": 3, "create_max_per_domain": 1}})
    assert len(kept) == 4 and len(cut) == 2


# ── 创建 prompt ──────────────────────────────────────────────────────


def test_create_hint_uses_two_step_text_with_budget_numbers():
    h = spec_mod._resolve_causal_lines_hint({"element_params": {"create_max_elements": 8, "create_max_per_domain": None}}, stage="create")
    assert "领域线" in h and "kind" in h and "element_type" in h and "lifecycle_seed" in h
    assert "8 个以内" in h and "不限 个以内" in h and "future_tree" in h and "declared_causal_graph" in h


def test_create_hint_mentions_previous_lines_when_refining():
    h = spec_mod._resolve_causal_lines_hint({"causal_lines": [{"id": "tech", "label": "技术"}]}, stage="create")
    assert "tech（技术）" in h


def test_create_hint_is_legacy_when_switch_explicitly_off():
    h = spec_mod._resolve_causal_lines_hint({"element_modeling_enabled": False}, stage="create")
    assert "2~4 条" in h and "领域线" not in h


def test_advance_stage_hint_is_unchanged_by_the_switch():
    s = {"causal_lines": [{"id": "tech", "label": "技术", "future_tree": {"branches": []}}]}
    assert spec_mod._resolve_causal_lines_hint(s, stage="advance") == spec_mod._resolve_causal_lines_hint(
        {**s, "element_modeling_enabled": False}, stage="advance")


# ── generate_scenario 端到端 ─────────────────────────────────────────


class _Status:
    def __init__(self, v): self.value = v


class _Step:
    def __init__(self, i): self.id, self.skill_name = i, None


class _WF:
    def __init__(self, steps): self.steps = steps


def _stub(monkeypatch, tmp_path, payload, split):
    captured = {}

    class FakeStore:
        def __init__(self, root): pass

        def load(self, name):
            return _WF([_Step({"generate_scenario": "draft", "world_builder": "world_builder",
                               "causal_space_builder": "causal_space_builder"}[name])])

    class FakeRunner:
        def __init__(self, cfg): pass

        def run(self, wf, inputs):
            sid = wf.steps[0].id
            captured.setdefault("hints", []).append(inputs.get("causal_lines_hint"))
            data = payload if sid in ("draft", "causal_space_builder") else {"title": "t", "summary": "s", "vars": {}}
            p = tmp_path / f"{sid}.json"
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return SimpleNamespace(status="done", step_results=[
                SimpleNamespace(step_id=sid, status=_Status("done"), result_file=str(p))])

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)
    return captured


def _payload():
    return {"title": "t", "summary": "s", "vars": {}, "options": [],
            "causal_lines": [_dom("tech")] + [_el(f"e{i}", "tech") for i in range(9)],
            "declared_causal_graph": [{"from_line_id": "e8", "to_line_id": "tech", "note": "x"}]}


@pytest.mark.parametrize("split", [False, True])
def test_generate_scenario_clips_and_keeps_candidates(tmp_path, monkeypatch, split):
    cap = _stub(monkeypatch, tmp_path, _payload(), split)
    draft = spec_mod.generate_scenario(
        cfg=object(), workspace_root=tmp_path / "ws", template="life_sim", intent="i",
        settings={"split_creation_calls": split, "element_params": {"create_max_per_domain": 4}},
    )
    elements = [x for x in draft.causal_lines if x.get("kind") == "element"]
    assert len(elements) == 4 and len(draft.element_candidates) == 5
    assert "e8" in {x["id"] for x in elements}  # 先验边端点优先保留
    assert any("领域线" in (h or "") for h in cap["hints"])  # 两条路径都拿到了新的创建提示


def test_generate_scenario_switch_off_leaves_lines_untouched(tmp_path, monkeypatch):
    _stub(monkeypatch, tmp_path, _payload(), False)
    draft = spec_mod.generate_scenario(
        cfg=object(), workspace_root=tmp_path / "ws", template="life_sim", intent="i",
        settings={"element_modeling_enabled": False, "element_params": {"create_max_per_domain": 1}},
    )
    assert len(draft.causal_lines) == 10 and draft.element_candidates == []
    assert all("origin" not in x for x in draft.causal_lines)


def test_old_style_draft_without_kind_still_materializes(tmp_path, monkeypatch):
    legacy = {"title": "t", "summary": "s", "vars": {}, "options": [],
              "causal_lines": [{"id": "tech", "label": "技术线"}, {"id": "nego", "label": "谈判线"}]}
    _stub(monkeypatch, tmp_path, legacy, False)
    draft = spec_mod.generate_scenario(cfg=object(), workspace_root=tmp_path / "ws", template="life_sim", intent="i")
    assert [x["id"] for x in draft.causal_lines] == ["tech", "nego"] and draft.element_candidates == []
    manifest = materialize_simulation(
        tmp_path / "data", template="life_sim", intent="i", title="t", summary="s", vars={}, options=[],
        settings={"causal_lines": draft.causal_lines},
    )
    saved = SimStore.for_root(tmp_path / "data", manifest.sim_id).load_manifest()
    assert [x["id"] for x in saved.settings["causal_lines"]] == ["tech", "nego"]
    assert all(x["future_tree"]["branches"] for x in saved.settings["causal_lines"])


def test_domain_element_draft_materializes_with_trees_and_grouping(tmp_path):
    kept, _ = er.prepare_created_lines(
        [{"id": "tech", "label": "技术", "kind": "domain"},
         {"id": "gpu", "label": "GPU", "parent": "tech", "element_type": "technology",
          "lifecycle_seed": {"stage": "developer", "preexisting": True}}], [], {})
    manifest = materialize_simulation(
        tmp_path / "data", template="life_sim", intent="i", title="t", summary="s", vars={}, options=[],
        settings={"causal_lines": kept, "tech_model_enabled": True},
    )
    saved = SimStore.for_root(tmp_path / "data", manifest.sim_id).load_manifest()
    lines = saved.settings["causal_lines"]
    assert all(x["future_tree"]["branches"] for x in lines)  # 领域线和元素线都有树
    groups = er.group_by_domain(lines)
    assert [(g[0]["id"], [m["id"] for m in g[1]]) for g in groups] == [("tech", ["gpu"])]
    assert [n["id"] for n in tm.get_nodes(saved.settings)] == ["gpu"]
