"""tests/test_element_linkage.py — 第二十三轮 E6：元素模型的联动与收尾。

设计依据：`next_doc/world_simulator_element_causal_lines_plan.md` §4.7 / §6 E6。覆盖：
- 因果引擎：领域端点（`domain_child` 触发、目标领域展示、事件 `affects` 领域展开）；
- 树影响：目标经注册表解析（别名/合并目标）；跨线前置的 `线/分支` 限定写法；
- 体检 C9 只读提示（元素规模接近上限、长期待补全/兜底）；
- 独立推进 `advance_lines()`：事件按领域投放、`discovered_elements` 合并与登记、提示词里的元素段；
- 展示层：`element_view` 分组/过滤/徽标、手动编辑校验、预算参数、被合并元素历史重定向；
- 静态 HTML 导出的分组与徽标。
旧实例（未开启元素模式）的行为不变也在这里逐项断言。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import causal_engine as ce
from world_simulator import consistency_guard as cg
from world_simulator import element_registry as er
from world_simulator import element_view as ev
from world_simulator import event_sampler as es
from world_simulator import html_export
from world_simulator import tree_effects as te
from world_simulator import tree_grounding as tg
from world_simulator.engine import mechanisms
from world_simulator.state_model import SimManifest, SimState

from test_advance_lines_mechanisms import _event_settings, _install_runner, _lines_step, _make_sim, _sure_event
from test_element_tiers import _el, _line, _settings, _st
from test_tree_effects import _eff, _queue as _tree_queue
from test_tree_grounding import _b


# ── 夹具 ─────────────────────────────────────────────────────────────


def _dom(lid, label=None, **extra):
    return _line(lid, label or lid, kind="domain", **extra)


def _world(**extra) -> Dict[str, Any]:
    """两个领域：tech（gpu、model）与 biz（shop）；一条从领域 tech 到 target 的边。"""
    s = {
        "element_modeling_enabled": True,
        "causal_engine_enabled": True,
        "causal_lines": [
            _dom("tech", "技术"), _dom("biz", "商业"),
            _el("gpu", "GPU", element_type="technology", parent="tech"),
            _el("model", "模型", element_type="project", parent="tech"),
            _el("shop", "商店", element_type="organization", parent="biz"),
            _el("target", "目标", element_type="market", parent="biz"),
        ],
    }
    s.update(extra)
    return s


def _ns(step=3, **kw):
    base = dict(step=step, line_updates={}, tree_updates=[], sampled_events=[], tech_updates=[], vars={})
    base.update(kw)
    return SimpleNamespace(**base)


def _queued(settings, state):
    pending, queued, viol = ce.queue_effects(settings, [], state)
    return pending, queued, viol


# ═════════════════════════════════════════════════════════════════════
# 因果引擎：领域端点
# ═════════════════════════════════════════════════════════════════════


def test_domain_source_edge_fires_when_any_alive_child_progresses():
    s = _world(declared_causal_graph=[{"from_line_id": "tech", "to_line_id": "target"}])
    pending, queued, _ = _queued(s, _ns(line_updates={"gpu": {"summary": "x"}}))
    assert [p["trigger_reason"] for p in pending] == ["domain_child"]
    assert pending[0]["from_line_id"] == "tech" and queued[0]["action"] == "queued"


def test_domain_source_ignores_other_domains_retired_children_and_advanced_false():
    s = _world(declared_causal_graph=[{"from_line_id": "tech", "to_line_id": "target"}])
    # 别的领域下的元素有进展：不触发
    assert _queued(s, _ns(line_updates={"shop": {"summary": "x"}}))[0] == []
    # advanced=False 不算进展
    assert _queued(s, _ns(line_updates={"gpu": {"advanced": False}}))[0] == []
    # 子元素已退场：不算
    for line in s["causal_lines"]:
        if line["id"] == "gpu":
            line["status"] = "retired"
    assert _queued(s, _ns(line_updates={"gpu": {"summary": "x"}}))[0] == []
    # 另一个存活子元素有进展照常触发
    assert [p["trigger_reason"] for p in _queued(s, _ns(line_updates={"model": {"summary": "y"}}))[0]] == ["domain_child"]


def test_domain_source_direct_progress_keeps_its_original_reason():
    s = _world(declared_causal_graph=[{"from_line_id": "tech", "to_line_id": "target"}])
    pending, _, _ = _queued(s, _ns(line_updates={"tech": {"summary": "领域自己动了"}, "gpu": {"summary": "x"}}))
    assert [p["trigger_reason"] for p in pending] == ["line_advanced"]  # 领域自身直接有进展优先，不记为 domain_child


def test_domain_child_rule_is_off_without_element_mode():
    s = _world(declared_causal_graph=[{"from_line_id": "tech", "to_line_id": "target"}])
    s.pop("element_modeling_enabled")  # 旧实例：领域 id 只是一个普通 id
    assert _queued(s, _ns(line_updates={"gpu": {"summary": "x"}}))[0] == []


def test_domain_child_progress_via_tree_update_and_tech_transition():
    s = _world(declared_causal_graph=[{"from_line_id": "tech", "to_line_id": "target"}])
    via_tree = _ns(tree_updates=[{"line_id": "model", "confirmed_branch": "b1"}])
    assert _queued(s, via_tree)[0][0]["trigger_reason"] == "domain_child"
    via_tech = _ns(tech_updates=[{"action": "transition", "tech_id": "gpu"}])
    assert _queued(s, via_tech)[0][0]["trigger_reason"] == "domain_child"


def test_event_affecting_a_domain_triggers_edges_from_its_elements():
    edge = {"from_line_id": "gpu", "to_line_id": "target"}
    s = _world(declared_causal_graph=[edge])
    ev_hit = {"id": "e1", "affects": ["tech"], "severity": "high"}
    pending, _, _ = _queued(s, _ns(sampled_events=[ev_hit]))
    assert [p["trigger_reason"] for p in pending] == ["sampled_event"] and pending[0]["from_line_id"] == "gpu"
    # 领域展开只含其下元素：affects 写另一个领域则不触发
    assert _queued(s, _ns(sampled_events=[{"id": "e2", "affects": ["biz"]}]))[0] == []
    # 被上限压掉的事件不触发
    assert _queued(s, _ns(sampled_events=[{**ev_hit, "suppressed_by_cap": True}]))[0] == []
    # 旧实例：领域 id 不展开
    s.pop("element_modeling_enabled")
    assert _queued(s, _ns(sampled_events=[ev_hit]))[0] == []


def test_event_affects_alias_resolves_to_the_element():
    s = _world(declared_causal_graph=[{"from_line_id": "gpu", "to_line_id": "target"}])
    s["causal_lines"][2]["aliases"] = ["显卡"]
    pending, _, _ = _queued(s, _ns(sampled_events=[{"id": "e", "affects": ["显卡"]}]))
    assert len(pending) == 1 and pending[0]["trigger_reason"] == "sampled_event"


def test_expand_affects_rules():
    s = _world()
    s["causal_lines"][3]["status"] = "retired"  # model 已退场
    assert es.expand_affects(s, ["tech"]) == ["tech", "gpu"]  # 保序、不含已退场
    assert es.expand_affects(s, ["gpu", "tech", "gpu"]) == ["gpu", "tech"]  # 去重
    assert es.expand_affects(s, ["no_such", " "]) == ["no_such"]  # 解析不到的原样保留
    assert es.expand_affects(s, "not a list") == [] and es.expand_affects(s, None) == []
    legacy = {k: v for k, v in s.items() if k != "element_modeling_enabled"}
    assert es.expand_affects(legacy, ["tech"]) == ["tech"]  # 旧实例原样
    # 事件记录本身不被改写（历史不可变）
    rec = {"affects": ["tech"]}
    es.expand_affects(s, rec["affects"])
    assert rec["affects"] == ["tech"]


def test_pending_hint_lists_active_children_for_a_domain_target_without_fanout():
    s = _world(declared_causal_graph=[{"from_line_id": "shop", "to_line_id": "tech"}],
               causal_pending=[{"pending_id": "shop->tech@1", "edge_id": "shop->tech", "from_line_id": "shop",
                                "to_line_id": "tech", "triggered_at_step": 1, "delay_days": None, "delay_steps": 0,
                                "retrigger_count": 0, "postponed_count": 0, "ignored_count": 0}],
               element_params={"active_window_steps": 1, "watch_window_steps": 1})
    for line in s["causal_lines"]:
        line["born_step"] = 0
    history = [_st(5, line_updates={"gpu": {"summary": "动了"}})]  # 只有 gpu 近期有动静
    hint = ce.build_hint(s, history, 6)
    assert "目标是领域，其下当前 active 的元素：gpu（GPU）" in hint
    assert "model" not in hint.split("目标是领域")[1].split("；效果落在")[0]  # 没动静的 model 不列
    assert "不会逐个扇出" in hint
    # 没有 active 子元素时给出明确说法
    hint_quiet = ce.build_hint(s, [], 30)
    assert "其下暂无 active 元素" in hint_quiet
    # 目标不是领域：没有这段；旧实例：没有这段
    s["causal_pending"][0]["to_line_id"] = "target"
    assert "目标是领域" not in ce.build_hint(s, history, 6)
    s["causal_pending"][0]["to_line_id"] = "tech"
    legacy = {k: v for k, v in s.items() if k != "element_modeling_enabled"}
    assert "目标是领域" not in ce.build_hint(legacy, history, 6)


def test_display_name_follows_a_merge():
    s = _world()
    s["causal_lines"][2].update({"status": "merged", "merged_into": "model"})
    assert ce._display_name(s, "gpu") == "模型"
    legacy = {k: v for k, v in s.items() if k != "element_modeling_enabled"}
    assert ce._display_name(legacy, "gpu") == "GPU"  # 旧实例不重定向


# ═════════════════════════════════════════════════════════════════════
# 树影响 / 树接地
# ═════════════════════════════════════════════════════════════════════


def _tree_world(effect_target: str, **extra):
    lines = [
        _dom("tech", "技术"),
        _el("gpu", "GPU", element_type="technology", parent="tech",
            future_tree={"as_of_step": 0, "branches": [_b("b1", status="dormant", effects_if_active=[_eff(effect_target)])]}),
        _el("market", "市场", element_type="market", aliases=["商场"]),
    ]
    before = [dict(x) for x in lines]
    import copy

    after = copy.deepcopy(lines)
    after[1]["future_tree"]["branches"][0]["status"] = "active"
    s = {"element_modeling_enabled": True, "causal_engine_enabled": True, "tree_effects_enabled": True,
         "causal_lines": after}
    s.update(extra)
    return s, before, after


def test_tree_effect_target_alias_is_canonicalised_in_element_mode():
    s, before, after = _tree_world("商场")  # 写的是别名
    pending, queued, viol = _tree_queue(after, before, settings=s)
    assert [p["to_line_id"] for p in pending] == ["market"]
    assert pending[0]["edge_id"].endswith("->market") and "E7" not in [v["code"] for v in viol]


def test_tree_effect_target_follows_merge_and_skips_retired():
    s, before, after = _tree_world("old_market")
    s["causal_lines"].append(_el("old_market", "旧市场", status="merged", merged_into="market"))
    pending, _, viol = _tree_queue(after, before, settings=s)
    assert [p["to_line_id"] for p in pending] == ["market"]
    # 目标（合并后的存活元素）已退场 → 不再入队
    for line in s["causal_lines"]:
        if line["id"] == "market":
            line["status"] = "retired"
    assert _tree_queue(after, before, settings=s)[0] == []


def test_tree_effect_unknown_target_is_still_queued_with_e7():
    s, before, after = _tree_world("totally_unknown")
    pending, _, viol = _tree_queue(after, before, settings=s)
    assert [p["to_line_id"] for p in pending] == ["totally_unknown"] and "E7" in [v["code"] for v in viol]


def test_tree_effect_alias_is_not_resolved_for_legacy_instances():
    s, before, after = _tree_world("商场")
    s.pop("element_modeling_enabled")
    pending, _, viol = _tree_queue(after, before, settings=s)
    assert [p["to_line_id"] for p in pending] == ["商场"] and "E7" in [v["code"] for v in viol]


def test_qualified_prerequisite_resolves_across_lines_in_both_checkers():
    lines = [
        {"id": "a", "label": "a", "future_tree": {"as_of_step": 0, "branches": [_b("x", status="resolved")]}},
        {"id": "b", "label": "b", "future_tree": {"as_of_step": 0, "branches": [_b("x", status="dormant")]}},
        {"id": "c", "label": "c", "future_tree": {"as_of_step": 0, "branches": [_b("go", prerequisites=["a/x"])]}},
    ]
    idx = tg.status_index(lines)
    branch = _b("go", prerequisites=["a/x", "b/x", "x", "nope/x", "a/zzz"])
    unmet, unv = tg.unmet_prerequisites("c", branch, idx)
    assert unmet == ["b/x"]                      # a/x 已 resolved；b/x 未满足
    assert set(unv) == {"x", "nope/x", "a/zzz"}  # 裸 x 在两条线上重名有歧义；其余解析不到 → 无法核验，不阻断
    gidx = cg.tree_index(lines)
    assert cg._resolve_prerequisite("c", "a/x", gidx) == ("a", "x")
    assert cg._resolve_prerequisite("c", "x", gidx) is None  # 重名歧义行为不变
    assert cg._resolve_prerequisite("c", "nope/x", gidx) is None


# ═════════════════════════════════════════════════════════════════════
# 体检 C9
# ═════════════════════════════════════════════════════════════════════


def test_element_health_none_when_element_mode_off():
    assert cg.element_health({}, []) is None and cg.element_health(None, []) is None
    s = _world()
    s.pop("element_modeling_enabled")
    assert cg.element_health(s, []) is None


def test_element_health_flags_near_budget_stale_pending_and_fallback():
    s = _world(element_params={"max_total_elements": 5, "enrich_grace_steps": 2})
    for line in s["causal_lines"]:
        if line["id"] == "gpu":
            line.update(profile_status="pending_enrichment", born_step=1)
        if line["id"] == "model":
            line.update(profile_status="fallback", born_step=1)
    h = cg.element_health(s, [_st(10)])
    assert h["alive_elements"] == 4 and h["near_budget"] is True  # 4 ≥ 5×0.8
    assert h["stale_pending_enrichment"] == ["gpu"] and h["fallback"] == ["model"]
    assert len(h["notes"]) == 3 and "只读" in h["note"]
    # 宽限期内的待补全不算“长期”
    assert cg.element_health(s, [_st(2)])["stale_pending_enrichment"] == []
    # 不限（null）→ 不提示规模
    s["element_params"]["max_total_elements"] = None
    assert cg.element_health(s, [_st(10)])["near_budget"] is False


def test_analyze_history_includes_c9_only_when_settings_given_and_element_mode_on():
    s = _world()
    base = cg.analyze_history([_st(1)])
    assert "c9_element_health" not in base
    assert "c9_element_health" not in cg.analyze_history([_st(1)], settings=None)
    assert "c9_element_health" in cg.analyze_history([_st(1)], settings=s)
    legacy = {k: v for k, v in s.items() if k != "element_modeling_enabled"}
    assert "c9_element_health" not in cg.analyze_history([_st(1)], settings=legacy)


# ═════════════════════════════════════════════════════════════════════
# 独立推进 advance_lines
# ═════════════════════════════════════════════════════════════════════

_LINES_E = [
    _dom("tech", "技术"),
    _el("gpu", "GPU", element_type="technology", parent="tech", owned_vars=["a"], advance_every_n_steps=1, local_step=0),
    _el("shop", "商店", element_type="organization", owned_vars=["b"], advance_every_n_steps=1, local_step=0),
]


def _disc(i, label=None, **extra):
    d = {"id": i, "label": label or i, "element_type": "project", "parent": "tech",
         "relations": [{"with": "gpu", "direction": "affected_by", "sign": "positive", "note": "依赖"}],
         "why_key": "关键"}
    d.update(extra)
    return d


def test_event_delivery_by_domain_parent():
    lines = [{"id": "gpu", "parent": "tech", "owned_vars": ["a"]}, {"id": "shop", "owned_vars": ["b"]}]
    f = mechanisms.event_delivered_to
    assert f({"affects": ["tech"]}, lines) == ["gpu"]          # 领域 id → 其下到点线
    assert f({"affects": ["biz"]}, lines) == []
    assert f({"affects": ["gpu"]}, lines) == ["gpu"] and f({"affects": []}, lines) == ["gpu", "shop"]  # 旧规则不变
    assert f({"affects": ["tech"], "suppressed_by_cap": True}, lines) == []


def test_merge_line_outputs_concatenates_discovered_elements_only_when_present():
    merged, _, _ = mechanisms.merge_line_outputs([("A", {"discovered_elements": [{"id": "x"}]}),
                                                  ("B", {"discovered_elements": [{"id": "y"}, {"id": "x"}]}),
                                                  ("C", {"discovered_elements": "not a list"})])
    assert [d["id"] for d in merged["discovered_elements"]] == ["x", "y", "x"]
    none_merged, _, _ = mechanisms.merge_line_outputs([("A", {})])
    assert "discovered_elements" not in none_merged  # 没有就不加键：与 E6 之前逐字节一致


def test_line_hint_has_element_section_only_in_element_mode():
    s = _world()
    hint = mechanisms.build_line_hint(s, [], 2, line=s["causal_lines"][2], events=[], owned_ids=set())
    assert "discovered_elements" in hint and "gpu（GPU）" in hint and "tech（技术）" in hint
    assert "element_ops" not in hint and "element_enrichments" not in hint  # 独立推进不处理运维/补全
    legacy = {k: v for k, v in s.items() if k != "element_modeling_enabled"}
    assert mechanisms.build_line_hint(legacy, [], 2, line={}, events=[], owned_ids=set()) == ""
    assert er.build_line_hint({}, step=1) == "" and er.safe_build_line_hint(None, step=1) == ""


def test_advance_lines_registers_discovered_elements_from_several_lines(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, settings={"element_modeling_enabled": True}, lines=_LINES_E)
    seen: List[Dict[str, Any]] = []
    # 两条线都发现了 proj（后到的并入，不重复登记）；shop 线另发现 vendor
    _install_runner(monkeypatch, tmp_path, {
        "gpu": {"discovered_elements": [_disc("proj", "新项目")]},
        "shop": {"discovered_elements": [_disc("proj", "新项目"), _disc("vendor", "供应商", parent="tech")]},
    }, seen)
    state = _lines_step(tmp_path, data_dir)
    assert all("discovered_elements" in i["line_mechanism_hint"] for i in seen)  # 机制全关，元素段仍给
    manifest = SimManifestLoader(data_dir)
    ids = [x["id"] for x in manifest.settings["causal_lines"]]
    assert ids.count("proj") == 1 and "vendor" in ids
    registered = [a for a in state.element_audit if a.get("action") == "registered"]
    assert {a.get("element_id") for a in registered} == {"proj", "vendor"}
    # 新元素没有 owned_vars：不会被单独调用
    assert next(x for x in manifest.settings["causal_lines"] if x["id"] == "proj").get("owned_vars") in (None, [])


def SimManifestLoader(data_dir):
    from world_simulator.store import SimStore

    return SimStore.for_root(data_dir, "sim1").load_manifest()


def test_advance_lines_without_element_mode_ignores_discovered_elements(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    plain = [{"id": "A", "label": "A", "owned_vars": ["a"], "advance_every_n_steps": 1, "local_step": 0}]
    _make_sim(data_dir, settings={}, lines=plain)
    seen: List[Dict[str, Any]] = []
    _install_runner(monkeypatch, tmp_path, {"A": {"discovered_elements": [_disc("proj")]}}, seen)
    state = _lines_step(tmp_path, data_dir)
    assert seen[0]["line_mechanism_hint"] == ""
    assert [x["id"] for x in SimManifestLoader(data_dir).settings["causal_lines"]] == ["A"]
    assert not state.element_audit


def test_advance_lines_domain_event_is_delivered_to_child_line(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    settings = {"element_modeling_enabled": True, **_event_settings([_sure_event("ev", ["tech"])])}
    _make_sim(data_dir, settings=settings, lines=_LINES_E)
    seen: List[Dict[str, Any]] = []
    _install_runner(monkeypatch, tmp_path, {}, seen)
    state = _lines_step(tmp_path, data_dir)
    gpu_hint = next(i for i in seen if i["line_id"] == "gpu")["line_mechanism_hint"]
    shop_hint = next(i for i in seen if i["line_id"] == "shop")["line_mechanism_hint"]
    assert "事件ev" in gpu_hint and "事件ev" not in shop_hint
    assert state.sampled_events[0]["delivered_to"] == ["gpu"]


# ═════════════════════════════════════════════════════════════════════
# element_view：分组 / 过滤 / 编辑 / 预算
# ═════════════════════════════════════════════════════════════════════


def test_overview_disabled_for_legacy_instances():
    s = _world()
    s.pop("element_modeling_enabled")
    o = ev.build_overview(s, [])
    assert o == {"enabled": False, "groups": [], "types": [], "counts": {}}
    assert ev.filter_overview(o, ["technology"]) is o and ev.ordered_ids(o) == []
    assert ev.merged_sources(s) == {} and ev.merged_sources(None) == {}


def test_overview_groups_by_domain_with_badges_and_ungrouped_last():
    s = _world()
    s["causal_lines"].append(_line("loose", "游离线"))                         # 旧式独立线 → 未归类
    s["causal_lines"].append(_el("lost", "无归属", element_type="person", parent="nope"))  # parent 不存在 → 未归类
    s["causal_lines"][2]["profile_status"] = "pending_enrichment"
    s["causal_lines"][3]["tier_pin"] = "active"
    s["causal_lines"][4]["status"] = "retired"
    o = ev.build_overview(s, [_st(4, line_updates={"gpu": {"summary": "x"}})])
    labels = [g["domain_label"] for g in o["groups"]]
    assert labels == ["技术", "商业", "未归类"]
    gpu = next(r for g in o["groups"] for r in g["rows"] if r["id"] == "gpu")
    assert "技术" in gpu["badges"] and "待补全" in gpu["badges"] and any("活跃" in b for b in gpu["badges"])
    model = next(r for g in o["groups"] for r in g["rows"] if r["id"] == "model")
    assert "📌 固定活跃" in model["badges"]
    shop = next(r for g in o["groups"] for r in g["rows"] if r["id"] == "shop")
    assert "已退场" in shop["badges"] and not any(b in ev.TIER_LABELS.values() for b in shop["badges"])
    assert o["counts"]["pending_enrichment"] == 1 and o["counts"]["retired"] == 1 and o["counts"]["domains"] == 2
    assert ev.ordered_ids(o)[:3] == ["tech", "gpu", "model"]  # 领域线在其子元素之前
    assert {t["value"] for t in o["types"]} >= {"technology", "project", "organization", "market", "person", ""}


def test_overview_merged_elements_fold_into_target_and_extra_ids_go_ungrouped():
    s = _world()
    s["causal_lines"][3].update({"status": "merged", "merged_into": "gpu"})  # model 并入 gpu
    o = ev.build_overview(s, [], extra_ids=["only_in_history", "gpu"])
    ids = ev.ordered_ids(o)
    assert "model" not in ids and "only_in_history" in ids and ids.count("gpu") == 1
    gpu = next(r for g in o["groups"] for r in g["rows"] if r["id"] == "gpu")
    assert "并入：model" in gpu["badges"]
    assert o["counts"]["merged"] == 1 and o["counts"]["elements"] == 3
    assert ev.merged_sources(s) == {"gpu": ["model"]}
    # 链式合并：a→b→c，来源都记在最终目标上
    chain = _world()
    chain["causal_lines"][2].update({"status": "merged", "merged_into": "model"})
    chain["causal_lines"][3].update({"status": "merged", "merged_into": "shop"})
    assert sorted(ev.merged_sources(chain)["shop"]) == ["gpu", "model"]


def test_overview_filter_by_type_and_hide_inactive():
    s = _world()
    s["causal_lines"][4]["status"] = "retired"
    o = ev.build_overview(s, [])
    f = ev.filter_overview(o, ["technology"])
    assert [r["id"] for g in f["groups"] for r in g["rows"]] == ["gpu"]
    assert [g["domain_label"] for g in f["groups"]] == ["技术"]  # 没有匹配子元素的领域组整组隐藏
    h = ev.filter_overview(o, [], hide_inactive=True)
    assert "shop" not in [r["id"] for g in h["groups"] for r in g["rows"]]
    assert ev.filter_overview(o, [], hide_inactive=False) is o and ev.filter_overview(o, None) is o
    assert o["groups"][0]["rows"]  # 入参未被修改


def test_update_for_line_and_link_ids_redirect_merged_history():
    updates = {"model": {"summary": "旧 id 的记录"}}
    assert ev.update_for_line(updates, "gpu", ["model"]) == {"summary": "旧 id 的记录"}
    assert ev.update_for_line({"gpu": {"summary": "自己"}, "model": {"summary": "旧"}}, "gpu", ["model"]) == {"summary": "自己"}
    assert ev.update_for_line(updates, "gpu") is None and ev.update_for_line(None, "gpu", ["model"]) is None
    assert ev.link_ids("gpu", ["model"]) == {"gpu", "model"} and ev.link_ids("gpu") == {"gpu"}


def test_apply_element_edit_happy_path_and_no_input_mutation():
    s = _world()
    lines = s["causal_lines"]
    snapshot = [dict(x) for x in lines]
    new, errs = ev.apply_element_edit(lines, "gpu", parent="biz", aliases="显卡，图形卡, 显卡", tier_pin="active", status="retired", step=7)
    assert errs == [] and lines == snapshot
    gpu = next(x for x in new if x["id"] == "gpu")
    assert gpu["parent"] == "biz" and gpu["aliases"] == ["显卡", "图形卡"] and gpu["tier_pin"] == "active"
    assert gpu["status"] == "retired" and gpu["retired_step"] == 7 and gpu["retire_reason"] == "手动"
    back, errs = ev.apply_element_edit(new, "gpu", parent="", tier_pin="", status="alive")
    gpu2 = next(x for x in back if x["id"] == "gpu")
    assert errs == [] and gpu2["parent"] == "" and "tier_pin" not in gpu2
    assert gpu2["status"] == "alive" and "retired_step" not in gpu2 and "retire_reason" not in gpu2
    # 没传的字段不动
    only, _ = ev.apply_element_edit(lines, "gpu", aliases=["x"])
    assert next(x for x in only if x["id"] == "gpu")["parent"] == "tech"


@pytest.mark.parametrize("kwargs, needle", [
    ({"parent": "nope"}, "不是已登记且存活的领域线"),
    ({"parent": "gpu"}, "不是已登记且存活的领域线"),        # 元素不能当领域
    ({"tier_pin": "watch"}, "tier_pin"),
    ({"status": "merged"}, "status 只能是"),
    ({"status": "dead"}, "status 只能是"),
    ({"aliases": ["商店"]}, "冲突"),                         # 与 shop 的名称冲突
    ({"aliases": ["model"]}, "冲突"),                        # 与 model 的 id 冲突
])
def test_apply_element_edit_validation_rejects_atomically(kwargs, needle):
    lines = _world()["causal_lines"]
    new, errs = ev.apply_element_edit(lines, "gpu", **kwargs)
    assert errs and needle in "；".join(errs)
    assert new == lines  # 出错时整次作废，返回原样


def test_apply_element_edit_partial_failure_discards_whole_edit():
    lines = _world()["causal_lines"]
    new, errs = ev.apply_element_edit(lines, "gpu", parent="biz", tier_pin="bogus")
    assert errs and next(x for x in new if x["id"] == "gpu")["parent"] == "tech"  # parent 的合法修改也没生效


def test_apply_element_edit_domain_merged_and_missing_lines():
    lines = _world()["causal_lines"]
    for kw in ({"parent": "biz"}, {"tier_pin": "active"}, {"status": "retired"}):
        assert ev.apply_element_edit(lines, "tech", **kw)[1], kw   # 领域线不能设这些
    assert not ev.apply_element_edit(lines, "tech", aliases=["科技"])[1]  # 领域线可以有别名
    assert ev.apply_element_edit(lines, "ghost", parent="biz")[1] == ["找不到元素「ghost」"]
    merged = [dict(x) for x in lines]
    merged[2].update({"status": "merged", "merged_into": "model"})
    assert "已合并" in ev.apply_element_edit(merged, "gpu", status="alive")[1][0]


def test_alias_equal_to_own_id_or_label_is_dropped_silently():
    new, errs = ev.apply_element_edit(_world()["causal_lines"], "gpu", aliases=["gpu", "GPU", "显卡"])
    assert errs == [] and next(x for x in new if x["id"] == "gpu")["aliases"] == ["显卡"]


def test_param_specs_and_current_params_cover_all_nine_keys():
    keys = [sp["key"] for sp in ev.param_specs()]
    assert len(keys) == 9 and len(set(keys)) == 9
    cur = ev.current_params(None)
    assert set(cur) == set(keys)
    assert cur["max_active_in_prompt"] == 12 and cur["create_max_elements"] == 20 and cur["max_total_elements"] is None
    assert cur["active_window_steps"] == 3 and cur["watch_window_steps"] == 10 and cur["enrich_grace_steps"] == 2
    assert ev.current_params({"element_params": {"max_active_in_prompt": None, "dormant_index_max": 0}})[
        "max_active_in_prompt"] is None


def test_build_params_unlimited_is_null_and_zero_is_zero_and_unknown_keys_survive():
    existing = {"max_total_elements": 80, "custom_future_key": 1}
    out, errs = ev.build_params(existing, {
        "max_total_elements": {"unlimited": True, "value": 5},
        "max_active_in_prompt": {"unlimited": False, "value": 0},        # 0 是“0 个”，不是不限
        "active_window_steps": {"value": 4},
    })
    assert errs == []
    assert out["max_total_elements"] is None and out["max_active_in_prompt"] == 0 and out["active_window_steps"] == 4
    assert out["custom_future_key"] == 1 and existing["max_total_elements"] == 80  # 入参未改
    # 写进去的值引擎读回来的就是同一个意思
    assert er.get_runtime_params({"element_params": out})["max_total_elements"] is None
    assert ev.current_params({"element_params": out})["max_active_in_prompt"] == 0


@pytest.mark.parametrize("form", [
    {"active_window_steps": {"value": 0}},        # 下限 1
    {"enrich_grace_steps": {"value": -1}},
    {"max_total_elements": {"value": 2.5}},
    {"max_total_elements": {"value": True}},
    {"max_total_elements": {"value": "12"}},
    {"no_such_param": {"value": 1}},
    {"active_window_steps": {"unlimited": True, "value": 1}, "watch_window_steps": {"value": 0}},
])
def test_build_params_rejects_invalid_and_keeps_existing(form):
    existing = {"max_total_elements": 80}
    out, errs = ev.build_params(existing, form)
    assert errs and out == existing


def test_unlimited_flag_is_ignored_for_non_limit_params():
    out, errs = ev.build_params({}, {"active_window_steps": {"unlimited": True, "value": 5}})
    assert errs == [] and out == {"active_window_steps": 5}


# ═════════════════════════════════════════════════════════════════════
# 静态 HTML 导出
# ═════════════════════════════════════════════════════════════════════


def _manifest(settings):
    return SimManifest(sim_id="s", template="life_sim", intent="i", title="t", created_at="2026-01-01T00:00:00",
                       updated_at="2026-01-01T00:00:00", settings=settings)


def _hist():
    return [SimState(step=1, summary="s", vars={}, line_updates={"gpu": {"summary": "GPU 起步"}}),
            SimState(step=2, summary="s", vars={}, line_updates={"model": {"summary": "模型旧记录"}},
                     causal_links=[{"line_id": "model", "driver": "d", "effect": "e"}])]


def test_export_groups_by_domain_with_badges_in_element_mode():
    html = html_export._render_causal_lines_breakdown(_manifest(_world()), _hist())
    assert html.index("技术（2）") < html.index("GPU") < html.index("商业（2）")
    assert "技术</span>" in html and "项目</span>" in html and html.count("ws-uncertain-badge") >= 5


def test_export_is_unchanged_for_legacy_instances():
    legacy = _world()
    legacy.pop("element_modeling_enabled")
    html = html_export._render_causal_lines_breakdown(_manifest(legacy), _hist())
    assert "<h4" not in html and "并入：" not in html and "待补全" not in html


def test_export_folds_merged_history_into_target_row():
    s = _world()
    s["causal_lines"][3].update({"status": "merged", "merged_into": "gpu"})  # model 并入 gpu
    html = html_export._render_causal_lines_breakdown(_manifest(s), _hist())
    assert "模型旧记录" in html                 # 旧 id 的记录出现在了目标 gpu 的时间轴上
    assert html.count("ws-causal-line-row-id") == html.count("ws-causal-line-row-title")
    assert 'ws-causal-line-row-id">model<' not in html  # 被合并的行不再单独占行
    assert "并入：model" in html
    assert "第 2 步 · d" in html or "d → e" in html  # 旧 id 的因果链条目也并了过来


def test_realism_health_export_shows_c9_notes():
    from world_simulator import html_export_mechanisms as hm

    s = _world(element_params={"max_total_elements": 4})
    s["causal_lines"][2]["profile_status"] = "fallback"
    h = [SimState(step=1, summary="s", vars={}, consistency_warnings=[{"code": "C1", "step": 1, "severity": "warn",
                                                                       "message": "m", "detail": {}}])]
    out = hm.health_html(_manifest(s), h, s)
    assert "元素（C9" in out and "兜底" in out
    legacy = {k: v for k, v in s.items() if k != "element_modeling_enabled"}
    assert "元素（C9" not in hm.health_html(_manifest(legacy), h, legacy)


# ── 没有任何领域线时：按元素类型做展示层虚拟分组（不再全堆进“未归类”）──────────────────────


def _no_domain_world():
    s = _world()
    s["causal_lines"] = [x for x in s["causal_lines"] if x.get("kind") != "domain"]
    for x in s["causal_lines"]:
        x.pop("parent", None)
    return s


def test_overview_without_domains_groups_by_element_type():
    s = _no_domain_world()
    s["causal_lines"].append(_line("loose", "游离线"))  # 无类型 → 仍是“未归类”
    o = ev.build_overview(s, [])
    labels = [g["domain_label"] for g in o["groups"]]
    assert labels[-1] == "未归类" and all(l.endswith("（按类型）") for l in labels[:-1])
    assert all(g.get("virtual") for g in o["groups"][:-1]) and not o["groups"][-1].get("virtual")
    assert o["counts"]["domains"] == 0 and o["counts"]["virtual_groups"] == len(labels) - 1
    assert all(g["domain_row"] is None for g in o["groups"])
    # 每个元素恰好出现一次，登记顺序保持
    ids = [r["id"] for g in o["groups"] for r in g["rows"]]
    assert sorted(ids) == sorted(str(x["id"]) for x in s["causal_lines"]) and len(ids) == len(set(ids))


def test_overview_without_domains_extras_go_to_ungrouped_not_type_group():
    s = _no_domain_world()
    o = ev.build_overview(s, [], extra_ids=["ghost"])
    last = o["groups"][-1]
    assert last["domain_label"] == "未归类" and not last.get("virtual")
    assert [r["id"] for r in last["rows"]] == ["ghost"]
    assert all("ghost" not in [r["id"] for r in g["rows"]] for g in o["groups"][:-1])


def test_overview_with_real_domains_keeps_ungrouped_semantics():
    o = ev.build_overview(_world(), [])
    assert o["counts"]["virtual_groups"] == 0
    assert not any(g.get("virtual") for g in o["groups"])


def test_filter_and_ordered_ids_work_with_virtual_groups():
    s = _no_domain_world()
    o = ev.build_overview(s, [])
    f = ev.filter_overview(o, ["technology"])
    assert f["groups"] and all(r["element_type"] == "technology" for g in f["groups"] for r in g["rows"])
    assert ev.ordered_ids(o) == [r["id"] for g in o["groups"] for r in g["rows"]]
