"""tests/test_element_discovery.py — 第二十三轮 E3：推进阶段的发现 / 去重 / 登记 / 补全。

设计依据：`next_doc/world_simulator_element_causal_lines_plan.md` §4.4、§5.1、§6 E3。
覆盖：运行期参数、别名去重与 id 漂移、关键性门槛与候选池转正、预算、补全与宽限/兜底、
引用即登记（各引用源）、`lifecycle_seed` 经技术裁决、派生因果边、prompt 段落、
`advance()` 端到端（含元素模式关闭时与旧行为等价、分支隔离、异常兜底）。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import branch_manager as bm
from world_simulator import causal_engine, causal_tree, spec_generator
from world_simulator import element_registry as er
from world_simulator import tech_model as tm
from world_simulator.engine import causal_lines as cl_mod
from world_simulator.engine.management import get_simulation
from world_simulator.state_model import SimState

from test_fast_forward import _default_payload
from test_tech_model import _adv, _make_sim


# ── 夹具 ─────────────────────────────────────────────────────────────


def _line(lid, label=None, **extra):
    line = {"id": lid, "label": label or lid, "time_granularity": "", "future_tree": causal_tree.build_default_future_tree(label or lid, 0)}
    line.update(extra)
    return line


def _base(**extra):
    """两个领域 + 两个已完整登记的元素。"""
    settings = {
        "element_modeling_enabled": True,
        "causal_lines": [
            _line("tech", "技术", kind="domain"),
            _line("finance", "金融", kind="domain"),
            _line("gpu_supply", "GPU 供给", kind="element", element_type="technology", parent="tech",
                  aliases=["GPU supply"], origin="seed", born_step=0, profile_status="complete"),
            _line("model_v2", "模型 V2 项目", kind="element", element_type="project", parent="tech",
                  origin="seed", born_step=0, profile_status="complete"),
        ],
    }
    settings.update(extra)
    return settings


def _disc(eid, **kw):
    item = {"id": eid, "label": kw.pop("label", eid)}
    item.update(kw)
    return item


def _rel(with_id, direction="affects", **kw):
    return {"with": with_id, "direction": direction, **kw}


def _run(settings, step=1, **kw):
    return er.process_step(settings, step=step, **kw)


def _ids(settings):
    return [x["id"] for x in settings["causal_lines"]]


def _line_of(settings, lid):
    return next(x for x in settings["causal_lines"] if x["id"] == lid)


def _actions(result):
    return [a["action"] for a in result["audit"]]


# ── 运行期参数 ───────────────────────────────────────────────────────


def test_runtime_params_defaults_unlimited_and_bounds():
    assert er.get_runtime_params(None) == {
        "max_total_elements": None, "candidate_promote_mentions": 2, "enrich_grace_steps": 2,
    }
    p = er.get_runtime_params({"element_params": {"max_total_elements": 5, "candidate_promote_mentions": 3, "enrich_grace_steps": 0}})
    assert p == {"max_total_elements": 5, "candidate_promote_mentions": 3, "enrich_grace_steps": 0}
    # null 只对 max_total_elements 是"不限"；0 不是不限
    assert er.get_runtime_params({"element_params": {"max_total_elements": None}})["max_total_elements"] is None
    assert er.get_runtime_params({"element_params": {"max_total_elements": 0}})["max_total_elements"] == 0


@pytest.mark.parametrize("key,bad", [
    ("max_total_elements", -1), ("max_total_elements", "x"), ("max_total_elements", True),
    ("candidate_promote_mentions", 0), ("candidate_promote_mentions", None), ("candidate_promote_mentions", 1.5),
    ("enrich_grace_steps", -1), ("enrich_grace_steps", None), ("enrich_grace_steps", False),
])
def test_runtime_params_invalid_values_fall_back_and_are_reported(key, bad):
    s = {"element_params": {key: bad}}
    assert er.get_runtime_params(s)[key] == er.RUNTIME_DEFAULT_PARAMS[key]
    assert er.invalid_runtime_param_keys(s) == [key]


def test_create_params_are_untouched_by_runtime_params():
    # E2 的契约：get_params 只含创建期两个键
    assert er.get_params({"element_params": {"max_total_elements": 3}}) == {"create_max_elements": 20, "create_max_per_domain": 6}
    assert er.invalid_runtime_param_keys({"element_params": "x"}) == []


# ── 发现：登记 / 门槛 / 候选池 ───────────────────────────────────────


def test_discovered_with_relation_registers_directly_with_full_fields():
    s = _base()
    r = _run(s, step=4, discovered=[_disc(
        "open_rival_x", label="开源竞品 X", element_type="technology", parent="tech", aliases=["X-OSS", "竞品X"],
        why_key="影响 GPU 供给", relations=[_rel("GPU supply", "affected_by", sign="negative", note="抢卡")],
        future_tree={"branches": [{"id": "wins", "description": "开源阵营胜出", "likelihood": "low"},
                                  {"id": "fades", "description": "热度消退", "likelihood": "medium"}]},
    )])
    line = _line_of(s, "open_rival_x")
    assert line["kind"] == "element" and line["origin"] == "discovered" and line["born_step"] == 4
    assert line["parent"] == "tech" and line["element_type"] == "technology"
    assert line["aliases"] == ["X-OSS", "竞品X"] and line["profile_status"] == "complete"
    assert line["auto_discovered"] is True
    # 关系的 with 被解析成规范 id；种子树被采纳且补上 first_seen_step
    assert line["relations"] == [{"with": "gpu_supply", "direction": "affected_by", "sign": "negative", "note": "抢卡"}]
    assert [b["id"] for b in line["future_tree"]["branches"]] == ["wins", "fades"]
    assert all(b["first_seen_step"] == 4 for b in line["future_tree"]["branches"])
    assert _actions(r) == ["registered"]
    assert r["audit"][0]["relations"] == ["affected_by:gpu_supply"]


def test_registered_without_type_or_parent_is_pending_and_gets_default_tree():
    s = _base()
    _run(s, discovered=[_disc("new_thing", relations=[_rel("model_v2")])])
    line = _line_of(s, "new_thing")
    assert line["profile_status"] == "pending_enrichment"
    assert "parent" not in line and "element_type" not in line
    assert er._is_default_tree(line["future_tree"])


def test_parent_must_be_an_alive_domain_unknown_or_element_parent_is_dropped():
    s = _base()
    _run(s, discovered=[
        _disc("a1", element_type="project", parent="model_v2", relations=[_rel("gpu_supply")]),   # 元素当 parent：不接受
        _disc("a2", element_type="project", parent="不存在的领域", relations=[_rel("gpu_supply")]),
        _disc("a3", element_type="project", parent="金融", relations=[_rel("gpu_supply")]),         # 用领域 label 解析
    ])
    assert "parent" not in _line_of(s, "a1") and "parent" not in _line_of(s, "a2")
    assert _line_of(s, "a3")["parent"] == "finance"
    assert _line_of(s, "a3")["profile_status"] == "complete"
    assert _line_of(s, "a1")["profile_status"] == "pending_enrichment"


def test_no_domains_means_type_alone_completes_the_profile():
    s = {"element_modeling_enabled": True, "causal_lines": [_line("main_line", "主线"), _line("a", "A", kind="element")]}
    _run(s, discovered=[_disc("b", element_type="project", relations=[_rel("a")])])
    assert _line_of(s, "b")["profile_status"] == "complete"


def test_relation_to_unknown_element_does_not_qualify_and_goes_to_candidates():
    s = _base()
    r = _run(s, discovered=[_disc("ghost", why_key="x", relations=[_rel("nobody")])])
    assert "ghost" not in _ids(s)
    assert [c["id"] for c in s["element_candidates"]] == ["ghost"]
    assert _actions(r) == ["candidate"]


def test_no_relation_goes_to_candidate_pool_and_mentions_count_distinct_steps_only():
    s = _base()
    _run(s, step=3, discovered=[_disc("maybe", label="也许", why_key="可能重要")])
    _run(s, step=3, discovered=[_disc("maybe")])                    # 同一步再提：不算第二次
    assert s["element_candidates"][0]["mentions"] == 1
    assert "maybe" not in _ids(s)
    r = _run(s, step=4, discovered=[_disc("maybe")])                 # 不同步骤：第 2 次 + 有 why_key → 转正
    assert "maybe" in _ids(s) and s["element_candidates"] == []
    assert _actions(r) == ["promoted"]
    promoted = _line_of(s, "maybe")
    assert promoted["label"] == "也许" and promoted["born_step"] == 4  # 候选池攒下的信息被带入


def test_without_why_key_repeated_mentions_never_promote():
    s = _base()
    for step in (1, 2, 3, 4):
        _run(s, step=step, discovered=[_disc("noise")])
    assert "noise" not in _ids(s)
    assert s["element_candidates"][0]["mentions"] == 4


def test_candidate_promote_threshold_is_configurable():
    s = _base(element_params={"candidate_promote_mentions": 3})
    _run(s, step=1, discovered=[_disc("m", why_key="w")])
    _run(s, step=2, discovered=[_disc("m")])
    assert "m" not in _ids(s)
    _run(s, step=3, discovered=[_disc("m")])
    assert "m" in _ids(s)


def test_candidate_info_accumulates_across_mentions_and_relation_promotes_immediately():
    s = _base()
    _run(s, step=1, discovered=[_disc("c1", aliases=["老名字"], element_type="policy")])
    _run(s, step=2, discovered=[_disc("c1", relations=[_rel("finance", "affected_by")], parent="finance")])
    line = _line_of(s, "c1")
    assert line["aliases"] == ["老名字"] and line["element_type"] == "policy" and line["parent"] == "finance"
    assert line["relations"][0]["with"] == "finance"
    assert s["element_candidates"] == []


def test_candidate_hit_by_alias_of_pool_entry_is_the_same_candidate():
    s = _base()
    _run(s, step=1, discovered=[_disc("c2", aliases=["别称"], why_key="w")])
    _run(s, step=2, discovered=[_disc("c2_again", aliases=["别称"])])
    assert len(s["element_candidates"]) <= 1
    assert "c2" in _ids(s)  # 别称命中同一候选 → 第 2 次提到 → 转正


def test_sweep_promotes_candidate_when_its_relation_target_gets_registered_later():
    s = _base()
    _run(s, step=1, discovered=[_disc("early", relations=[_rel("later_one")])])
    assert "early" not in _ids(s)
    r = _run(s, step=2, discovered=[_disc("later_one", relations=[_rel("gpu_supply")])])
    assert "later_one" in _ids(s) and "early" in _ids(s) and s["element_candidates"] == []
    assert "promoted" in _actions(r)


def test_candidate_pool_is_capped_dropping_least_mentioned_oldest(monkeypatch):
    monkeypatch.setattr(er, "CANDIDATE_POOL_MAX", 3)
    s = _base()
    _run(s, step=1, discovered=[_disc("k1"), _disc("k2")])
    _run(s, step=2, discovered=[_disc("k2")])  # k2 提了两次
    _run(s, step=3, discovered=[_disc("k3"), _disc("k4")])
    ids = {c["id"] for c in s["element_candidates"]}
    assert len(ids) == 3 and "k2" in ids and "k4" in ids and "k1" not in ids


# ── 去重：别名 / 规范化 / id 漂移 ────────────────────────────────────


def test_id_drift_gpt5_vs_gpt_5_is_the_same_element():
    s = _base()
    _run(s, step=1, discovered=[_disc("gpt5", label="GPT-5", element_type="technology", parent="tech", relations=[_rel("gpu_supply", "affected_by")])])
    n = len(s["causal_lines"])
    r = _run(s, step=2, discovered=[_disc("gpt_5", relations=[_rel("model_v2", "affects")])])
    assert len(s["causal_lines"]) == n and _actions(r) == ["merged_alias"]
    # 合并命中的关系也并入
    assert {x["with"] for x in _line_of(s, "gpt5")["relations"]} == {"gpu_supply", "model_v2"}


def test_label_hit_and_alias_hit_merge_and_record_new_names_as_aliases():
    s = _base()
    r1 = _run(s, discovered=[_disc("gpu_2", label="GPU 供给")])                     # label 命中（规范化后相等）
    r2 = _run(s, discovered=[_disc("totally_new", aliases=["gpu supply"])])          # 别名命中
    assert "gpu_2" not in _ids(s) and "totally_new" not in _ids(s)
    assert _actions(r1) == _actions(r2) == ["merged_alias"]
    aliases = _line_of(s, "gpu_supply")["aliases"]
    assert "gpu_2" in aliases and "totally_new" in aliases and "GPU supply" in aliases
    assert len(aliases) == len({er.norm_key(a) for a in aliases})  # 别名按规范化键去重


def test_fullwidth_case_and_punctuation_are_normalized_when_matching():
    s = _base()
    _run(s, discovered=[_disc("ＧＰＵ－Supply")])   # 全角/连字符
    assert len(s["causal_lines"]) == 4


def test_similar_but_different_names_are_not_merged():
    s = _base()
    _run(s, discovered=[_disc("gpu_supply_cn", label="GPU 供给（国产）", relations=[_rel("tech", "affected_by")])])
    assert "gpu_supply_cn" in _ids(s)   # 只做精确规范化匹配，不模糊合并


def test_rediscovering_a_pending_element_enriches_it_instead_of_only_adding_aliases():
    s = _base()
    _run(s, step=1, discovered=[_disc("stub1", relations=[_rel("gpu_supply")])])
    assert _line_of(s, "stub1")["profile_status"] == "pending_enrichment"
    r = _run(s, step=2, discovered=[_disc("stub1", element_type="project", parent="tech")])
    line = _line_of(s, "stub1")
    assert line["profile_status"] == "complete" and line["parent"] == "tech"
    assert "enriched" in _actions(r)


# ── 预算 ─────────────────────────────────────────────────────────────


def test_budget_blocks_new_elements_but_never_evicts_existing_ones():
    s = _base(element_params={"max_total_elements": 2})   # 已有 2 个元素
    r = _run(s, discovered=[_disc("z", relations=[_rel("gpu_supply")], why_key="w")])
    assert "z" not in _ids(s) and {"gpu_supply", "model_v2"} <= set(_ids(s))
    assert s["element_candidates"][0]["reason"] == "over_budget"
    assert _actions(r) == ["budget_blocked"]


def test_budget_counts_within_a_single_step_and_null_means_unlimited():
    s = _base(element_params={"max_total_elements": 3})
    _run(s, discovered=[_disc("n1", relations=[_rel("gpu_supply")]), _disc("n2", relations=[_rel("gpu_supply")])])
    assert "n1" in _ids(s) and "n2" not in _ids(s)
    s2 = _base(element_params={"max_total_elements": None})
    _run(s2, discovered=[_disc(f"u{i}", relations=[_rel("gpu_supply")]) for i in range(30)])
    assert len([x for x in s2["causal_lines"] if x["id"].startswith("u")]) == 30


def test_domains_and_retired_elements_do_not_count_toward_the_budget():
    s = _base(element_params={"max_total_elements": 2})
    s["causal_lines"][3]["status"] = "retired"   # model_v2 退场 → 只剩 1 个 alive 元素
    _run(s, discovered=[_disc("fits", relations=[_rel("gpu_supply")])])
    assert "fits" in _ids(s)


# ── 补全 / 宽限 / 兜底 ───────────────────────────────────────────────


def _pending_settings(born=5, **extra):
    s = _base(**extra)
    s["causal_lines"].append(_line("p1", "待补全", kind="element", origin="discovered", born_step=born,
                                   profile_status="pending_enrichment", auto_discovered=True))
    return s


def test_enrichment_fills_gaps_and_completes():
    s = _pending_settings()
    r = _run(s, step=6, enrichments=[{
        "id": "p1", "label": "新名字", "element_type": "organization", "parent": "finance", "aliases": ["P-One"],
        "relations": [_rel("model_v2", "affected_by")],
        "future_tree": {"branches": [{"id": "grow", "description": "做大", "likelihood": "medium"}]},
    }])
    line = _line_of(s, "p1")
    assert line["label"] == "新名字" and line["element_type"] == "organization" and line["parent"] == "finance"
    assert line["aliases"] == ["P-One"] and line["relations"][0]["with"] == "model_v2"
    assert [b["id"] for b in line["future_tree"]["branches"]] == ["grow"]
    assert line["profile_status"] == "complete"
    assert _actions(r) == ["enriched"]
    assert set(r["audit"][0]["fields"]) >= {"label", "element_type", "parent", "aliases", "relations", "future_tree", "profile_status"}


def test_enrichment_never_overwrites_existing_type_parent_or_a_tree_that_moved_on():
    s = _pending_settings()
    line = _line_of(s, "p1")
    line.update(element_type="project", parent="tech")
    tree = copy.deepcopy(line["future_tree"])
    tree["branches"][0]["status"] = "active"      # 树已经被推进过：不再是通用兜底模板
    line["future_tree"] = tree
    _run(s, step=6, enrichments=[{"id": "p1", "element_type": "asset", "parent": "finance",
                                  "future_tree": {"branches": [{"id": "x", "description": "x", "likelihood": "low"}]}}])
    after = _line_of(s, "p1")
    assert after["element_type"] == "project" and after["parent"] == "tech"
    assert after["future_tree"]["branches"][0]["status"] == "active"


def test_enrichment_for_complete_or_unknown_elements_is_ignored_with_audit():
    s = _pending_settings()
    before = copy.deepcopy(_line_of(s, "gpu_supply"))
    r = _run(s, step=6, enrichments=[{"id": "gpu_supply", "label": "改名"}, {"id": "no_such", "label": "x"}])
    assert _line_of(s, "gpu_supply") == before
    assert _actions(r) == ["enrichment_ignored", "enrichment_unknown"]


def test_pending_falls_back_after_grace_but_not_before():
    s = _pending_settings(born=5)                  # 默认宽限 2
    _run(s, step=6)
    assert _line_of(s, "p1")["profile_status"] == "pending_enrichment"
    r = _run(s, step=7)
    line = _line_of(s, "p1")
    assert line["profile_status"] == "fallback" and "parent" not in line   # 不猜领域
    assert er._is_default_tree(line["future_tree"])                         # 保留兜底树
    assert _actions(r) == ["fallback"] and r["audit"][0]["missing"] == ["element_type", "parent"]


def test_enrichment_in_the_last_grace_step_beats_fallback():
    s = _pending_settings(born=5)
    _run(s, step=6)
    r = _run(s, step=7, enrichments=[{"id": "p1", "element_type": "project", "parent": "tech"}])
    assert _line_of(s, "p1")["profile_status"] == "complete" and "fallback" not in _actions(r)


def test_late_enrichment_still_completes_a_fallback_element():
    s = _pending_settings(born=5)
    _run(s, step=7)
    assert _line_of(s, "p1")["profile_status"] == "fallback"
    _run(s, step=20, enrichments=[{"id": "p1", "element_type": "project", "parent": "tech"}])
    assert _line_of(s, "p1")["profile_status"] == "complete"


def test_grace_zero_falls_back_immediately_and_is_never_asked():
    s = _pending_settings(born=5, element_params={"enrich_grace_steps": 0})
    _run(s, step=5)
    assert _line_of(s, "p1")["profile_status"] == "fallback"
    s2 = _pending_settings(born=5, element_params={"enrich_grace_steps": 0})
    assert "上一步登记时信息不全" not in er.build_hint(s2, step=6)


# ── 引用即登记 ───────────────────────────────────────────────────────


def test_line_updates_unknown_id_registers_a_pending_stub_and_keeps_the_key():
    s = _base()
    r = _run(s, step=3, line_updates={"brand_new": {"summary": "有进展"}})
    line = _line_of(s, "brand_new")
    assert line["profile_status"] == "pending_enrichment" and line["origin"] == "discovered" and line["born_step"] == 3
    assert line["label"] == "brand_new" and er._is_default_tree(line["future_tree"])
    assert list(r["line_updates"]) == ["brand_new"] and _actions(r) == ["ref_registered"]


def test_line_updates_alias_keys_are_rewritten_to_the_canonical_id_in_order():
    s = _base()
    r = _run(s, line_updates={"model_v2": {"summary": "a"}, "GPU supply": {"summary": "b"}, "技术": {"summary": "c"}})
    assert list(r["line_updates"]) == ["model_v2", "gpu_supply", "tech"]
    assert len(s["causal_lines"]) == 4   # 没有新登记
    assert [a["ref"] for a in r["audit"] if a["action"] == "alias_resolved"] == ["GPU supply", "技术"]


def test_two_keys_resolving_to_one_element_merge_without_losing_fields():
    s = _base()
    r = _run(s, line_updates={"gpu_supply": {"summary": "first", "trend": "steady"}, "GPU supply": {"summary": "second", "extra": 1}})
    assert list(r["line_updates"]) == ["gpu_supply"]
    assert r["line_updates"]["gpu_supply"] == {"summary": "first", "trend": "steady", "extra": 1}   # 先到优先、后到补缺
    assert "alias_collision" in _actions(r)


def test_causal_links_line_id_registers_and_rewrites():
    s = _base()
    links = [{"line_id": "GPU supply", "cause": "c"}, {"line_id": "fresh_line", "cause": "d"}, {"cause": "no line"}]
    r = _run(s, causal_links=links)
    assert [x.get("line_id") for x in r["causal_links"]] == ["gpu_supply", "fresh_line", None]
    assert "fresh_line" in _ids(s)
    assert links[0]["line_id"] == "GPU supply"   # 入参不被改动


def test_tree_updates_line_id_effects_target_and_conditions_are_all_reference_sources():
    s = _base()
    updates = [{
        "line_id": "unseen_line",
        "new_branches": [{
            "description": "d", "likelihood": "low",
            "trigger_condition": [{"tech": "new_tech", "min_stage": "developer"}, {"element": "GPU supply"}, {"var": "a.b", "op": ">=", "value": 1}],
            "effects_if_active": [{"to_line_id": "affected_x", "mechanism": "m", "condition": {"tech": "other_tech"}}, {"to_line_id": "GPU supply"}],
        }],
    }]
    r = _run(s, tree_updates=updates)
    assert {"unseen_line", "new_tech", "affected_x", "other_tech"} <= set(_ids(s))
    assert _line_of(s, "new_tech")["element_type"] == "technology"    # "tech" 键本身就声明了它是技术
    assert "element_type" not in _line_of(s, "affected_x")
    branch = r["tree_updates"][0]["new_branches"][0]
    assert branch["trigger_condition"][1] == {"element": "gpu_supply"}
    assert branch["trigger_condition"][2] == {"var": "a.b", "op": ">=", "value": 1}   # 非元素条件原样
    assert branch["effects_if_active"][1]["to_line_id"] == "gpu_supply"
    assert updates[0]["new_branches"][0]["effects_if_active"][1]["to_line_id"] == "GPU supply"  # 入参不变


def test_tech_updates_ids_are_alias_resolved_but_never_registered_here():
    s = _base()
    r = _run(s, tech_updates=[{"id": "GPU supply", "stage": "expert"}, {"tech_id": "gpu-supply"}, {"id": "brand_new_tech"}, "junk"])
    ids = [p.get("id") or p.get("tech_id") for p in r["tech_updates"] if isinstance(p, dict)]
    assert ids == ["gpu_supply", "gpu_supply", "brand_new_tech"]
    assert "brand_new_tech" not in _ids(s)       # 新技术仍由技术裁决登记（同时建元素线）
    assert r["tech_updates"][3] == "junk"


def test_reference_to_a_candidate_promotes_it():
    s = _base()
    _run(s, step=1, discovered=[_disc("lurker", label="潜伏者", why_key="w", element_type="policy")])
    assert "lurker" not in _ids(s)
    r = _run(s, step=2, line_updates={"lurker": {"summary": "动了"}})
    line = _line_of(s, "lurker")
    assert line["label"] == "潜伏者" and line["element_type"] == "policy" and s["element_candidates"] == []
    assert _actions(r) == ["promoted"] and "referenced:line_updates" in r["audit"][0]["via"]


def test_over_budget_reference_keeps_the_key_and_parks_the_id_in_the_pool():
    s = _base(element_params={"max_total_elements": 2})
    r = _run(s, line_updates={"extra_line": {"summary": "x"}})
    assert "extra_line" not in _ids(s) and list(r["line_updates"]) == ["extra_line"]
    assert s["element_candidates"][0]["id"] == "extra_line" and _actions(r) == ["budget_blocked"]


def test_empty_and_non_dict_inputs_are_ignored_without_error():
    s = _base()
    r = _run(s, line_updates={"": {"summary": "x"}}, causal_links=[None, "x", {"line_id": "  "}],
             tree_updates=["x", {"line_id": ""}], tech_updates="oops", discovered="not a list",
             enrichments={"id": "x"})
    assert len(s["causal_lines"]) == 4 and r["audit"] == []
    assert r["tech_updates"] == "oops"


def test_discovered_items_without_id_use_label_and_without_both_are_dropped():
    s = _base()
    _run(s, discovered=[{"label": "两 个 词", "relations": [_rel("gpu_supply")]}, {"why_key": "x"}, "str", None, {"id": " "}])
    assert "两_个_词" in _ids(s) and len(s["causal_lines"]) == 5


# ── lifecycle_seed ───────────────────────────────────────────────────


def test_lifecycle_seed_becomes_a_synthetic_tech_update_when_the_tech_model_is_on():
    s = _base(tech_model_enabled=True)
    r = _run(s, discovered=[_disc("chip_x", label="芯片 X", element_type="technology", parent="tech",
                                  relations=[_rel("gpu_supply")], lifecycle_seed={"stage": "developer", "preexisting": True})])
    assert r["tech_updates"] == [{"stage": "developer", "preexisting": True, "id": "chip_x", "name": "芯片 X"}]
    assert "lifecycle_seed_queued" in _actions(r)


def test_lifecycle_seed_is_ignored_with_audit_when_the_tech_model_is_off_or_type_is_not_technology():
    s = _base()
    r = _run(s, discovered=[_disc("chip_y", element_type="technology", relations=[_rel("gpu_supply")], lifecycle_seed={"stage": "lab"})])
    assert r["tech_updates"] is None and "lifecycle_seed_ignored" in _actions(r)
    s2 = _base(tech_model_enabled=True)
    r2 = _run(s2, discovered=[_disc("proj_z", element_type="project", relations=[_rel("gpu_supply")], lifecycle_seed={"stage": "lab"})])
    assert r2["tech_updates"] is None


def test_llm_own_tech_proposal_for_the_same_id_wins_over_the_seed():
    s = _base(tech_model_enabled=True)
    r = _run(s, discovered=[_disc("chip_w", element_type="technology", relations=[_rel("gpu_supply")], lifecycle_seed={"stage": "lab"})],
             tech_updates=[{"id": "chip_w", "stage": "expert", "preexisting": True}])
    assert r["tech_updates"] == [{"id": "chip_w", "stage": "expert", "preexisting": True}]


# ── 异常与副作用 ─────────────────────────────────────────────────────


def test_process_step_does_not_mutate_inputs_and_is_atomic_on_error(monkeypatch):
    s = _base()
    snapshot = copy.deepcopy(s)
    lu = {"fresh": {"summary": "x"}}
    lu_copy = copy.deepcopy(lu)

    def boom(ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(er, "_expire_pending", boom)
    with pytest.raises(RuntimeError):
        _run(s, line_updates=lu, discovered=[_disc("d1", relations=[_rel("gpu_supply")])])
    assert s == snapshot and lu == lu_copy      # 中途出错：settings 一个字都没改
    monkeypatch.undo()
    _run(s, line_updates=lu)
    assert lu == lu_copy


def test_element_candidates_key_is_not_created_when_nothing_happens():
    s = _base()
    _run(s)
    assert "element_candidates" not in s


# ── 派生因果边 ───────────────────────────────────────────────────────


def test_normalize_element_cleans_relations():
    out = er.normalize_element({"id": "a", "relations": [
        {"with": "b", "direction": "->", "sign": "+", "note": " n "},
        {"with": "c", "direction": "affected_by", "sign": "weird"},
        {"with": "b", "direction": "affects"},             # 重复
        {"with": "", "direction": "affects"}, {"with": "d", "direction": "sideways"}, "x",
    ]})
    assert out["relations"] == [{"with": "b", "direction": "affects", "sign": "positive", "note": "n"}, {"with": "c", "direction": "affected_by"}]
    assert "relations" not in er.normalize_element({"id": "a", "relations": "nope"})


def _edge_settings():
    s = _base()
    s["causal_lines"].append(_line("x", "X", kind="element", relations=[
        _rel("gpu_supply", "affects", sign="negative", note="抢卡"), _rel("model_v2", "affected_by"), _rel("gone", "affects")]))
    s["causal_lines"].append(_line("old", "老", kind="element", status="retired", relations=[_rel("x", "affects")]))
    return s


def test_derived_edges_direction_endpoints_and_filtering():
    edges = er.derived_edges(_edge_settings())
    pairs = {(e["from_line_id"], e["to_line_id"]) for e in edges}
    assert pairs == {("x", "gpu_supply"), ("model_v2", "x")}   # 目标未登记的、来自退场元素的都不产边
    assert all(e["origin"] == "discovered" for e in edges)
    assert next(e for e in edges if e["to_line_id"] == "gpu_supply")["sign"] == "negative"


def test_derived_edges_are_off_when_element_mode_is_off():
    s = _edge_settings()
    s.pop("element_modeling_enabled")
    assert er.derived_edges(s) == []
    assert causal_engine.get_edges(s)[0] == []


def test_causal_engine_get_edges_merges_derived_edges_with_declared_winning():
    s = _edge_settings()
    s["declared_causal_graph"] = [{"from_line_id": "x", "to_line_id": "gpu_supply", "note": "先验"}]
    edges, problems = causal_engine.get_edges(s)
    by_id = {e["id"]: e for e in edges}
    assert set(by_id) == {"x->gpu_supply", "model_v2->x"} and problems == []
    assert by_id["x->gpu_supply"]["note"] == "先验"          # 声明的先到先得
    assert by_id["model_v2->x"]["from_line_id"] == "model_v2"


def test_prior_graph_hint_lists_derived_edges_without_repeating_declared_pairs():
    s = _edge_settings()
    s["declared_causal_graph"] = [{"from_line_id": "x", "to_line_id": "gpu_supply", "note": "先验"}]
    hint = spec_generator.resolve_causal_graph_hint(s, [])
    assert hint.count("x → gpu_supply") == 1 and "model_v2 → x" in hint


def test_branches_do_not_share_discovered_relations_because_they_live_on_the_line():
    s = _edge_settings()
    other = {"element_modeling_enabled": True, "causal_lines": [x for x in s["causal_lines"] if x["id"] != "x"]}
    assert er.derived_edges(other) == []   # 没有 x 的那条时间线上不会出现 x 的边


# ── prompt 段落 ──────────────────────────────────────────────────────


def test_hint_is_empty_when_element_mode_is_off_and_safe_swallows_errors(monkeypatch):
    s = _base()
    s.pop("element_modeling_enabled")
    assert er.build_hint(s, step=3) == "" and er.safe_build_hint(s, step=3) == ""
    monkeypatch.setattr(er, "build_hint", lambda *a, **k: 1 / 0)
    assert er.safe_build_hint(_base(), step=3) == ""


def test_hint_contains_protocol_domains_index_pending_and_candidates():
    s = _pending_settings(born=5)
    _run(s, step=5, discovered=[_disc("cand1", label="候选一", why_key="很可能重要")])
    hint = er.build_hint(s, step=6)
    assert "discovered_elements" in hint and "element_enrichments" in hint and "relations" in hint
    assert "tech（技术）" in hint and "finance（金融）" in hint
    assert "gpu_supply（GPU 供给；别名：GPU supply）" in hint and "model_v2" in hint
    assert "上一步登记时信息不全" in hint and "p1" in hint and "element_type、parent" in hint
    assert "候选池" in hint and "cand1" in hint and "很可能重要" in hint


def test_hint_pending_window_matches_the_grace_period():
    s = _pending_settings(born=5)           # 宽限 2：第 6、7 步生成时会问
    M = "上一步登记时信息不全"
    assert M not in er.build_hint(s, step=5)
    assert M in er.build_hint(s, step=6) and M in er.build_hint(s, step=7)
    assert M not in er.build_hint(s, step=8)


def test_hint_without_domains_tells_the_model_to_omit_parent():
    s = {"element_modeling_enabled": True, "causal_lines": [_line("main_line", "主线")]}
    assert "没有领域线" in er.build_hint(s, step=1)


def test_hint_index_is_capped():
    s = _base()
    for i in range(er.INDEX_MAX + 15):
        s["causal_lines"].append(_line(f"el{i:03d}", f"元素{i}", kind="element"))
    hint = er.build_hint(s, step=1)
    assert "el000" not in hint and f"el{er.INDEX_MAX + 14:03d}" in hint and "较早登记的元素" in hint


def test_suspected_duplicates_are_hinted_but_never_merged():
    s = _base()
    s["causal_lines"].append(_line("model_v2_proj", "模型 V2", kind="element", origin="discovered", born_step=4, profile_status="complete"))
    hint = er.build_hint(s, step=5)
    assert "model_v2_proj ≈ model_v2" in hint
    assert len(s["causal_lines"]) == 5


def test_suspected_duplicates_ignore_old_unrelated_and_domain_pairs():
    lines = [_line("a", "苹果手机", kind="element"), _line("b", "香蕉船", kind="element"), _line("tech", "技术", kind="domain"),
             _line("tech2", "技术研究", kind="element")]
    assert er.suspected_duplicates(lines, ["a"]) == []
    assert er.suspected_duplicates(lines, ["tech2"]) == []          # 与领域线相近不提示
    assert er.suspected_duplicates(lines, ["zzz"]) == []


# ── SimState ────────────────────────────────────────────────────────


def test_simstate_element_audit_roundtrips_and_is_omitted_when_empty():
    st = SimState(step=1, summary="s", vars={}, options=[], element_audit=[{"action": "registered", "element_id": "x"}])
    assert SimState.from_dict(st.to_dict()).element_audit == [{"action": "registered", "element_id": "x"}]
    assert "element_audit" not in SimState(step=1, summary="s", vars={}, options=[]).to_dict()
    assert SimState.from_dict({"step": 1, "summary": "s", "vars": {}, "element_audit": "bad"}).element_audit == []


# ── advance() 端到端 ─────────────────────────────────────────────────


def _manifest_settings(data_dir):
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    return manifest.settings


def _lines(data_dir):
    return {x["id"]: x for x in _manifest_settings(data_dir)["causal_lines"]}


def _sim_settings(**extra):
    s = _base(**extra)
    return s


def test_advance_registers_discovered_elements_audits_them_and_feeds_the_next_prompt(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, _sim_settings())
    cap: dict = {}
    payload = _default_payload(1, discovered_elements=[_disc(
        "open_rival_x", label="开源竞品 X", element_type="technology", parent="tech",
        why_key="影响 GPU", relations=[_rel("gpu_supply", "affected_by", sign="negative")])],
        line_updates={"open_rival_x": {"summary": "亮相"}, "GPU supply": {"summary": "紧张"}})
    state = _adv(monkeypatch, tmp_path, data_dir, payload, cap)

    assert "discovered_elements" in cap["inputs_list"][0]["element_hint"]       # 第一步 prompt 就带协议
    lines = _lines(data_dir)
    assert lines["open_rival_x"]["origin"] == "discovered" and lines["open_rival_x"]["parent"] == "tech"
    assert set(state.line_updates) == {"open_rival_x", "gpu_supply"}              # 别名 key 被规范
    assert [a["action"] for a in state.element_audit if a["action"] != "alias_resolved"] == ["registered"]
    persisted = store.load_history("main")[-1]
    assert persisted.element_audit == state.element_audit
    assert any(x["id"] == "open_rival_x" for x in persisted.dynamic_snapshot["causal_lines"])  # 随分支快照

    cap2: dict = {}
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2), cap2)
    # E4：已登记元素的索引由分级展示（`causal_lines_hint`）承担，`element_hint` 只留指引、不再重复列 id。
    assert "open_rival_x" in cap2["inputs_list"][0]["causal_lines_hint"]         # 下一步的已登记索引里
    assert "因果线设置" in cap2["inputs_list"][0]["element_hint"]


def test_advance_same_step_tree_updates_can_target_a_just_discovered_element(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _sim_settings())
    payload = _default_payload(1,
        discovered_elements=[_disc("fresh_el", element_type="project", parent="tech", relations=[_rel("model_v2")])],
        tree_updates=[{"line_id": "fresh_el", "new_branches": [{"description": "出现了新走向", "likelihood": "low"}]}])
    state = _adv(monkeypatch, tmp_path, data_dir, payload)
    branches = _lines(data_dir)["fresh_el"]["future_tree"]["branches"]
    assert any(b["description"] == "出现了新走向" for b in branches)
    assert state.tree_updates and state.tree_updates[0]["line_id"] == "fresh_el"


def test_advance_alias_drift_does_not_split_an_element(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _sim_settings())
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, discovered_elements=[
        _disc("gpt5", label="GPT-5", element_type="technology", parent="tech", relations=[_rel("gpu_supply", "affected_by")])]))
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        2, line_updates={"gpt_5": {"summary": "发布"}}, causal_links=[{"line_id": "GPT-5", "cause": "c"}]))
    assert [i for i in _lines(data_dir) if "gpt" in i] == ["gpt5"]
    assert list(state.line_updates) == ["gpt5"] and state.causal_links[0]["line_id"] == "gpt5"


def test_advance_enrichment_flow_across_steps_and_fallback(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _sim_settings())
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, line_updates={"stubby": {"summary": "x"}}))
    assert _lines(data_dir)["stubby"]["profile_status"] == "pending_enrichment"
    cap: dict = {}
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        2, element_enrichments=[{"id": "stubby", "label": "桩", "element_type": "market", "parent": "finance"}]), cap)
    assert "上一步登记时信息不全" in cap["inputs_list"][0]["element_hint"]
    after = _lines(data_dir)["stubby"]
    assert after["profile_status"] == "complete" and after["label"] == "桩"

    _adv(monkeypatch, tmp_path, data_dir, _default_payload(3, line_updates={"stubby2": {"summary": "y"}}))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(4))
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(5))
    assert _lines(data_dir)["stubby2"]["profile_status"] == "fallback"
    assert [a["action"] for a in state.element_audit] == ["fallback"]


def test_legacy_instance_without_the_switch_behaves_exactly_as_before(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    settings = _base()
    settings.pop("element_modeling_enabled")
    _make_sim(data_dir, settings)
    cap: dict = {}
    payload = _default_payload(1, discovered_elements=[_disc("ignored_el", relations=[_rel("gpu_supply")])],
                               element_enrichments=[{"id": "gpu_supply", "label": "改"}],
                               line_updates={"GPU supply": {"summary": "x"}, "legacy_new": {"summary": "y"}})
    state = _adv(monkeypatch, tmp_path, data_dir, payload, cap)
    lines = _lines(data_dir)
    assert "ignored_el" not in lines                                        # 新键被忽略
    assert lines["legacy_new"] == {"id": "legacy_new", "label": "legacy_new", "time_granularity": "",
                                   "auto_discovered": True, "future_tree": lines["legacy_new"]["future_tree"]}  # 旧最小登记，无元素字段
    assert "GPU supply" in lines and "gpu_supply" in lines                  # 旧行为：别名 key 被当作新线登记
    assert state.element_audit == [] and "element_audit" not in state.to_dict()
    assert cap["inputs_list"][0]["element_hint"] == ""
    assert "element_candidates" not in _manifest_settings(data_dir)


def test_advance_lifecycle_seed_goes_through_the_same_tech_adjudication(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _sim_settings(tech_model_enabled=True))
    payload = _default_payload(1, elapsed_days=10, discovered_elements=[
        _disc("claim_dev", label="声称已成熟", element_type="technology", parent="tech", relations=[_rel("gpu_supply")],
              lifecycle_seed={"stage": "developer"}),                              # 没声明 preexisting → T5 夹到起点
        _disc("old_tech", label="老技术", element_type="technology", parent="tech", relations=[_rel("gpu_supply")],
              lifecycle_seed={"stage": "developer", "preexisting": True}),
    ])
    state = _adv(monkeypatch, tmp_path, data_dir, payload)
    nodes = {n["id"]: n for n in tm.get_nodes(_manifest_settings(data_dir))}
    assert nodes["claim_dev"]["stage"] == tm.STAGES[0] and nodes["old_tech"]["stage"] == "developer"
    assert any(v["code"] == "T5" and v["tech_id"] == "claim_dev" for v in state.tech_violations)
    assert not any(v["tech_id"] == "old_tech" for v in state.tech_violations)
    line = _lines(data_dir)["claim_dev"]
    assert line["origin"] == "discovered" and line["element_type"] == "technology" and line["parent"] == "tech"
    assert isinstance(line["lifecycle"], dict)       # 元素线上挂了 lifecycle，没有另建一条重复线
    assert len([i for i in _lines(data_dir) if i == "claim_dev"]) == 1


def test_advance_new_tech_in_tech_updates_is_enriched_through_the_same_flow(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _sim_settings(tech_model_enabled=True))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=10, tech_updates=[{"id": "chip", "name": "芯片", "stage": "lab"}]))
    assert _lines(data_dir)["chip"]["profile_status"] == "pending_enrichment"
    cap: dict = {}
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        2, elapsed_days=10, element_enrichments=[{"id": "chip", "parent": "tech"}]), cap)
    assert "chip" in cap["inputs_list"][0]["element_hint"] and "上一步登记时信息不全" in cap["inputs_list"][0]["element_hint"]
    assert _lines(data_dir)["chip"]["profile_status"] == "complete" and _lines(data_dir)["chip"]["parent"] == "tech"


def test_advance_branches_isolate_discovered_elements_and_candidates(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _sim_settings())
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1))
    branch = bm.fork_branch(data_dir, "sim1", from_step=1)
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, discovered_elements=[
        _disc("only_here", element_type="project", parent="tech", relations=[_rel("model_v2")]),
        _disc("pooled", why_key="w")]))
    assert "only_here" in _lines(data_dir) and _manifest_settings(data_dir)["element_candidates"][0]["id"] == "pooled"

    bm.switch_branch(data_dir, "sim1", "main")
    assert "only_here" not in _lines(data_dir) and not _manifest_settings(data_dir).get("element_candidates")
    bm.switch_branch(data_dir, "sim1", branch)
    assert "only_here" in _lines(data_dir) and _manifest_settings(data_dir)["element_candidates"][0]["id"] == "pooled"
    # 候选池的提次也随分支：主线上再提一次 pooled 是它的第 1 次
    bm.switch_branch(data_dir, "sim1", "main")
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, discovered_elements=[_disc("pooled", why_key="w")]))
    assert "pooled" not in _lines(data_dir) and _manifest_settings(data_dir)["element_candidates"][0]["mentions"] == 1


def test_advance_discovered_relation_feeds_the_causal_engine_edges(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _sim_settings(causal_engine_enabled=True))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=10, discovered_elements=[
        _disc("rival", element_type="project", parent="tech", relations=[_rel("model_v2", "affects", sign="negative", note="分流用户")])]))
    edges, _p = causal_engine.get_edges(_manifest_settings(data_dir))
    assert [(e["from_line_id"], e["to_line_id"], e["sign"]) for e in edges] == [("rival", "model_v2", "negative")]
    # 源头 rival 在下一步有进展 → 边入队
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, elapsed_days=10, line_updates={"rival": {"summary": "动了"}}))
    assert [q["edge_id"] for q in state.causal_queued] == ["rival->model_v2"]


def test_advance_survives_a_crash_in_the_element_pipeline_and_falls_back_to_minimal_registration(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _sim_settings())

    def boom(*a, **k):
        raise RuntimeError("元素管线炸了")

    monkeypatch.setattr(er, "process_step", boom)
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, line_updates={"survivor": {"summary": "x"}}))
    assert state.step == 1 and "survivor" in _lines(data_dir)          # 旧的最小登记兜底
    assert state.element_audit[0]["action"] == "error" and "元素管线炸了" in state.element_audit[0]["message"]


def test_update_element_registry_leaves_manifest_untouched_when_the_pipeline_fails(monkeypatch):
    from types import SimpleNamespace

    manifest = SimpleNamespace(settings=_base())
    before = copy.deepcopy(manifest.settings)
    state = SimpleNamespace(step=1, line_updates={"x": {}}, causal_links=[])
    monkeypatch.setattr(er, "process_step", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    audit = cl_mod._update_element_registry(manifest, state, {})
    assert audit[0]["action"] == "error"
    # 旧最小登记登记了 x，但没有留下元素管线的半截改动（候选池等）
    assert {k: v for k, v in manifest.settings.items() if k != "causal_lines"} == {k: v for k, v in before.items() if k != "causal_lines"}


# ── 变异验证补充用例 ─────────────────────────────────────────────────


def test_relation_to_itself_is_dropped_when_rediscovering_an_existing_element():
    s = _base()
    _run(s, discovered=[_disc("gpu_supply", relations=[_rel("GPU supply", "affects"), _rel("model_v2", "affects")])])
    assert [r["with"] for r in _line_of(s, "gpu_supply")["relations"]] == ["model_v2"]


def test_referencing_a_pooled_candidate_through_a_tech_condition_gives_it_the_technology_type():
    s = _base()
    _run(s, step=1, discovered=[_disc("pooled_t", label="池里的技术", why_key="w")])    # 没有类型
    _run(s, step=2, tree_updates=[{"line_id": "model_v2", "new_branches": [
        {"description": "d", "likelihood": "low", "trigger_condition": {"tech": "pooled_t", "min_stage": "developer"}}]}])
    assert _line_of(s, "pooled_t")["element_type"] == "technology"


def test_short_names_contained_in_longer_ones_are_not_flagged_as_duplicates():
    lines = [_line("ai", "AI", kind="element", origin="discovered", born_step=4),
             _line("chain", "供应链", kind="element")]
    assert er.suspected_duplicates(lines, ["ai"]) == []


def test_get_edges_declared_edge_wins_over_a_derived_edge_with_the_same_id(monkeypatch):
    s = _edge_settings()
    s["declared_causal_graph"] = [{"from_line_id": "x", "to_line_id": "gpu_supply", "note": "先验", "sign": "positive"}]
    edges, _ = causal_engine.get_edges(s)
    assert [e["sign"] for e in edges if e["id"] == "x->gpu_supply"] == ["positive"]
    assert len([e for e in edges if e["id"] == "x->gpu_supply"]) == 1
