"""tests/test_element_ops.py — 第二十三轮 E5：元素运维（merge/split/retire/reparent）与周期扫描。

设计依据：`next_doc/world_simulator_element_causal_lines_plan.md` §4.5、§4.8、§6 E5。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import branch_manager as bm
from world_simulator import causal_engine, causal_tree, tree_effects
from world_simulator import element_discovery as ed
from world_simulator import element_ops as eo
from world_simulator import element_registry as er
from world_simulator import element_tiers as et
from world_simulator.engine.management import get_simulation
from world_simulator.state_model import SimState

from test_fast_forward import _default_payload
from test_tech_model import _adv, _make_sim


def _line(lid, label=None, **extra):
    line = {"id": lid, "label": label or lid, "time_granularity": "",
            "future_tree": causal_tree.build_default_future_tree(label or lid, 0)}
    line.update(extra)
    return line


def _el(lid, label=None, **extra):
    extra.setdefault("kind", "element")
    extra.setdefault("element_type", "project")
    extra.setdefault("parent", "tech")
    extra.setdefault("origin", "seed")
    extra.setdefault("born_step", 0)
    extra.setdefault("profile_status", "complete")
    return _line(lid, label, **extra)


def _base(**extra):
    s = {"element_modeling_enabled": True, "causal_lines": [
        _line("tech", "技术", kind="domain"), _line("finance", "金融", kind="domain"),
        _el("a", "项目甲", aliases=["甲"]), _el("b", "项目乙"), _el("c", "项目丙", parent="finance"),
    ]}
    s.update(extra)
    return s


def _ops(settings, *ops, step=3, **kw):
    return er.process_step(settings, step=step, ops=list(ops), **kw)


def _L(settings, lid):
    return next(x for x in settings["causal_lines"] if x["id"] == lid)


def _acts(result):
    return [a["action"] for a in result["audit"]]


def _rej(result):
    return [a for a in result["audit"] if a["action"] == "op_rejected"]


# ── normalize_op ─────────────────────────────────────────────────────

def test_normalize_op_aliases_and_garbage():
    assert eo.normalize_op("x") is None and eo.normalize_op({}) is None
    assert eo.normalize_op({"type": "合并", "source": "a", "target": "b"})["op"] == "merge"
    assert eo.normalize_op({"op": "move", "id": "a", "to": "tech"}) == {"op": "reparent", "note": "", "id": "a", "parent": "tech"}
    assert eo.normalize_op({"op": "bogus"})["op"] == "bogus"
    assert eo.normalize_op({"op": "split", "from": "a", "into": "bad"})["into"] == []


def test_non_list_ops_and_unknown_op_do_not_break():
    s = _base()
    r = er.process_step(s, step=1, ops="nope")
    assert r["audit"] == []
    r = _ops(s, {"op": "explode"})
    assert "不认识" in _rej(r)[0]["reason"]


# ── merge ────────────────────────────────────────────────────────────

def test_merge_aliases_status_relations_and_tree():
    s = _base()
    _L(s, "a")["relations"] = [{"with": "c", "direction": "affects"}]
    _L(s, "a")["future_tree"] = {"as_of_step": 1, "branches": [
        {"id": "win", "description": "赢", "likelihood": "high", "status": "active", "prerequisites": ["lose"]},
        {"id": "lose", "description": "输", "likelihood": "low", "status": "dormant", "exclusive_group": "g"}]}
    _L(s, "c")["relations"] = [{"with": "a", "direction": "affected_by"}]
    r = _ops(s, {"op": "merge", "from": "a", "into": "b", "note": "同一个"})
    a, b = _L(s, "a"), _L(s, "b")
    assert a["status"] == "merged" and a["merged_into"] == "b"
    assert "a" in b["aliases"] and "项目甲" in b["aliases"] and "甲" in b["aliases"]
    assert b["merged_from"] == ["a"]
    ids = [x["id"] for x in b["future_tree"]["branches"]]
    assert "a__win" in ids and "a__lose" in ids
    moved = {x["id"]: x for x in b["future_tree"]["branches"]}
    assert moved["a__win"]["status"] == "active" and moved["a__win"]["prerequisites"] == ["a__lose"]
    assert moved["a__lose"]["exclusive_group"] == "a__g"
    assert {"with": "c", "direction": "affects"} in b["relations"]
    assert _L(s, "c")["relations"] == [{"with": "b", "direction": "affected_by"}]
    assert _acts(r) == ["op_merge"] and r["audit"][0]["branches_moved"] == 2


def test_merge_resolve_follows_and_chain():
    s = _base()
    _ops(s, {"op": "merge", "from": "a", "into": "b"})
    assert er.resolve(s, "a")["id"] == "b" and er.resolve(s, "甲")["id"] == "b"
    assert er.resolve(s, "a", follow_merged=False)["id"] == "a"
    _ops(s, {"op": "merge", "from": "b", "into": "c"})
    assert er.resolve(s, "a")["id"] == "c"
    # 保留方写旧 id：并到最终归宿
    s2 = _base()
    _ops(s2, {"op": "merge", "from": "a", "into": "b"})
    r = _ops(s2, {"op": "merge", "from": "c", "into": "a"})
    assert _L(s2, "c")["merged_into"] == "b" and _acts(r) == ["op_merge"]


def test_redirect_merged_is_exact_id_only_and_cycle_safe():
    s = _base()
    _ops(s, {"op": "merge", "from": "a", "into": "b"})
    assert er.redirect_merged(s, "a") == "b" and er.redirect_merged(s, "b") == "b"
    assert er.redirect_merged(s, "甲") == "甲" and er.redirect_merged(s, "zzz") == "zzz" and er.redirect_merged(s, "") == ""
    cyc = {"causal_lines": [_el("x", status="merged", merged_into="y"), _el("y", status="merged", merged_into="x")]}
    assert er.redirect_merged(cyc, "x") in ("x", "y")  # 不死循环


@pytest.mark.parametrize("op,why", [
    ({"op": "merge", "from": "a", "into": "a"}, "自己"),
    ({"op": "merge", "from": "a", "into": "nope"}, "未登记"),
    ({"op": "merge", "from": "", "into": "b"}, "缺 from"),
    ({"op": "merge", "from": "tech", "into": "b"}, "领域线"),
    ({"op": "merge", "from": "a", "into": "tech"}, "领域线"),
])
def test_merge_validation(op, why):
    s = _base()
    before = [dict(x) for x in s["causal_lines"]]
    r = _ops(s, op)
    assert why in _rej(r)[0]["reason"] and s["causal_lines"] == before


def test_merge_rejects_retired_merged_and_lifecycle_source():
    s = _base()
    _L(s, "b")["status"] = "retired"
    assert "退场" in _rej(_ops(s, {"op": "merge", "from": "a", "into": "b"}))[0]["reason"]
    s = _base()
    _ops(s, {"op": "merge", "from": "a", "into": "b"})
    assert "已经被并入" in _rej(_ops(s, {"op": "merge", "from": "a", "into": "c"}))[0]["reason"]
    s = _base()
    _L(s, "a")["lifecycle"] = {"stage": "lab"}
    r = _ops(s, {"op": "merge", "from": "a", "into": "b"})
    assert "lifecycle" in _rej(r)[0]["reason"] and _L(s, "a").get("status") is None
    # 保留方带 lifecycle 没问题
    s = _base()
    _L(s, "b")["lifecycle"] = {"stage": "lab"}
    assert _acts(_ops(s, {"op": "merge", "from": "a", "into": "b"})) == ["op_merge"]


def test_merge_does_not_move_default_tree_and_rewrites_effects():
    s = _base()
    r = _ops(s, {"op": "merge", "from": "a", "into": "b"})
    assert r["audit"][0]["branches_moved"] == 0 and len(_L(s, "b")["future_tree"]["branches"]) == len(
        causal_tree.build_default_future_tree("b", 0)["branches"])
    s = _base()
    _L(s, "c")["future_tree"] = {"as_of_step": 1, "branches": [
        {"id": "x", "description": "d", "likelihood": "low", "status": "dormant",
         "effects_if_active": [{"to_line_id": "a", "mechanism": "m"}]}]}
    _L(s, "a")["future_tree"] = {"as_of_step": 1, "branches": [
        {"id": "y", "description": "d", "likelihood": "low", "status": "dormant",
         "effects_if_active": [{"to_line_id": "b", "mechanism": "self"}, {"to_line_id": "c", "mechanism": "keep"}]}]}
    r = _ops(s, {"op": "merge", "from": "a", "into": "b"})
    assert _L(s, "c")["future_tree"]["branches"][0]["effects_if_active"][0]["to_line_id"] == "b"
    moved = next(x for x in _L(s, "b")["future_tree"]["branches"] if x["id"] == "a__y")
    assert [e["to_line_id"] for e in moved["effects_if_active"]] == ["c"]  # 自指的去掉
    assert r["audit"][0]["effects_retargeted"] == 1


def test_discovery_and_enrichment_hitting_a_merged_name_land_on_target():
    s = _base()
    _ops(s, {"op": "merge", "from": "a", "into": "b"})
    n = len(s["causal_lines"])
    r = er.process_step(s, step=4, discovered=[{"id": "项目甲", "label": "项目甲", "aliases": ["新别名"]}])
    assert len(s["causal_lines"]) == n and "新别名" in _L(s, "b")["aliases"] and "新别名" not in _L(s, "a").get("aliases", [])
    r = er.process_step(s, step=5, line_updates={"a": {"summary": "x"}})
    assert list(r["line_updates"]) == ["b"]


def test_enrichment_addressed_to_a_merged_id_lands_on_target():
    s = _base()
    for lid in ("a", "b"):
        _L(s, lid).pop("parent")
        _L(s, lid)["profile_status"] = "pending_enrichment"
    _ops(s, {"op": "merge", "from": "a", "into": "b"})
    r = er.process_step(s, step=5, enrichments=[{"id": "a", "parent": "finance", "element_type": "project"}])
    assert _L(s, "b")["parent"] == "finance"
    assert [(x["action"], x["element_id"]) for x in r["audit"] if x["action"] == "enriched"] == [("enriched", "b")]
    assert "parent" not in _L(s, "a")                                              # 被合并的线本身不被补全


def test_merged_history_ids_count_for_the_target_tier():
    # 元素都登记得很早（born_step=0），step=12 时只有"最近一步更新过旧 id a"这一个动静
    hist = [SimpleNamespace(step=11, line_updates={"a": {"summary": "x"}}, tree_updates=[], tech_updates=[])]
    control = _base()
    ctl = et.derive_tiers(hist, control, step=12)
    assert ctl["tiers"]["a"] == "active" and ctl["tiers"]["b"] == "dormant"      # 对照：没合并，只有 a 活跃
    s = _base()
    _ops(s, {"op": "merge", "from": "a", "into": "b"})
    res = et.derive_tiers(hist, s, step=12)
    assert res["tiers"]["b"] == "active" and res["reasons"]["b"] == ["近期有进展"]   # 旧 id 的动静算到合并目标头上
    assert "a" not in res["tiers"]                                               # 被合并的线不再单独分级
    for ref in ("项目甲", "甲"):                                                     # 历史里写名称/别名，同样落到合并目标
        h2 = [SimpleNamespace(step=11, line_updates={ref: {"summary": "x"}}, tree_updates=[], tech_updates=[])]
        assert et.derive_tiers(h2, s, step=12)["tiers"]["b"] == "active", ref


# ── retire ───────────────────────────────────────────────────────────

def test_retire_marks_keeps_tree_and_is_idempotent():
    s = _base()
    s["causal_lines"][2]["tier_pin"] = "active"
    r = _ops(s, {"op": "retire", "id": "a", "reason": "被淘汰"}, step=7)
    a = _L(s, "a")
    assert a["status"] == "retired" and a["retired_step"] == 7 and a["retire_reason"] == "被淘汰" and "tier_pin" not in a
    assert a["future_tree"]
    assert _acts(_ops(s, {"op": "retire", "id": "a"})) == ["op_noop"]
    assert "领域线" in _rej(_ops(s, {"op": "retire", "id": "tech"}))[0]["reason"]
    assert _L(s, "tech").get("status") is None                                      # 领域线真的没被退场
    assert "未登记" in _rej(_ops(s, {"op": "retire", "id": "zz"}))[0]["reason"]


def test_retired_element_is_not_resurrected_by_rediscovery_and_skips_budget():
    s = _base()
    _ops(s, {"op": "retire", "id": "a"})
    n = len(s["causal_lines"])
    er.process_step(s, step=5, discovered=[{"id": "a", "label": "项目甲"}])
    assert len(s["causal_lines"]) == n and _L(s, "a")["status"] == "retired"


def _edge_settings():
    s = _base(causal_engine_enabled=True, declared_causal_graph=[
        {"from_line_id": "a", "to_line_id": "b", "note": "n"},
        {"from_line_id": "c", "to_line_id": "a", "note": "n2"}])
    return s


def _ns(step=3, line_updates=None):
    return SimpleNamespace(step=step, line_updates=line_updates or {}, tree_updates=[], sampled_events=[],
                           tech_updates=[], vars={})


def test_retired_endpoint_stops_queueing_but_open_entries_stay():
    s = _edge_settings()
    pending, queued, _ = causal_engine.queue_effects(s, [], _ns(line_updates={"a": {"summary": "x"}}))
    assert [q["action"] for q in queued] == ["queued"]
    _ops(s, {"op": "retire", "id": "b"})
    p2, q2, _ = causal_engine.queue_effects(s, pending, _ns(step=4, line_updates={"a": {"summary": "x"}}))
    assert q2 == [] and p2 == pending  # 不再入队/累加，已入队的不撤销
    _ops(s, {"op": "retire", "id": "c"})
    _p3, q3, _ = causal_engine.queue_effects(s, [], _ns(step=5, line_updates={"c": {"summary": "x"}}))
    assert q3 == []


def test_retire_has_no_effect_on_legacy_instances():
    s = {"causal_lines": [_line("a", status="retired"), _line("b")],
         "declared_causal_graph": [{"from_line_id": "a", "to_line_id": "b"}], "causal_engine_enabled": True}
    assert er.retired_ids(s) == set()
    _p, q, _ = causal_engine.queue_effects(s, [], _ns(line_updates={"a": {"summary": "x"}}))
    assert [x["action"] for x in q] == ["queued"]


def _tfx_settings():
    s = _base(tree_effects_enabled=True, causal_engine_enabled=True)
    _L(s, "a")["future_tree"] = {"as_of_step": 1, "branches": [
        {"id": "x", "description": "d", "likelihood": "low", "status": "active",
         "effects_if_active": [{"to_line_id": "b", "mechanism": "m"}]}]}
    return s


def _tfx_queue(s):
    return tree_effects.queue_tree_effects(s, [], _ns(), {("a", "x"): "dormant"}, s["causal_lines"])[1]


def test_retired_source_or_target_stops_tree_effects_but_control_queues():
    assert [q["action"] for q in _tfx_queue(_tfx_settings())] == ["queued"]        # 正向对照：没退场时会入队
    s = _tfx_settings()
    _ops(s, {"op": "retire", "id": "a"})
    assert _tfx_queue(s) == []                                                      # 源元素退场
    s = _tfx_settings()
    _ops(s, {"op": "retire", "id": "b"})
    assert _tfx_queue(s) == []                                                      # 目标元素退场


def test_merge_redirects_declared_edge_endpoints_on_read():
    s = _edge_settings()
    _ops(s, {"op": "merge", "from": "b", "into": "c"})
    edges, problems = causal_engine.get_edges(s)
    by = {e["id"]: e for e in edges}
    assert by["a->b"]["to_line_id"] == "c" and by["c->a"]["from_line_id"] == "c"
    # 合并后自指的边被忽略并给出原因
    s2 = _base(declared_causal_graph=[{"from_line_id": "a", "to_line_id": "b"}])
    _ops(s2, {"op": "merge", "from": "a", "into": "b"})
    e2, p2 = causal_engine.get_edges(s2)
    assert e2 == [] and any("指向同一元素" in x for x in p2)
    # 关闭元素模式的旧实例不改写
    s3 = {"causal_lines": [_line("a", status="merged", merged_into="b"), _line("b")],
          "declared_causal_graph": [{"from_line_id": "a", "to_line_id": "b"}]}
    e3, _p3 = causal_engine.get_edges(s3)                      # 没开元素模式：端点原样，不做改写
    assert [(e["from_line_id"], e["to_line_id"]) for e in e3] == [("a", "b")]


# ── reparent ─────────────────────────────────────────────────────────

def test_reparent_changes_parent_and_completes_profile():
    s = _base()
    _L(s, "b").pop("parent")
    _L(s, "b")["profile_status"] = "fallback"
    r = _ops(s, {"op": "reparent", "id": "b", "parent": "finance"})
    assert _L(s, "b")["parent"] == "finance" and _L(s, "b")["profile_status"] == "complete" and _acts(r) == ["op_reparent"]
    assert _acts(_ops(s, {"op": "reparent", "id": "b", "parent": "finance"})) == ["op_noop"]


@pytest.mark.parametrize("op,why", [
    ({"op": "reparent", "id": "a", "parent": "a"}, "不是已登记"),
    ({"op": "reparent", "id": "a", "parent": "nope"}, "不是已登记"),
    ({"op": "reparent", "id": "a"}, "缺少"),
    ({"op": "reparent", "id": "tech", "parent": "finance"}, "领域线"),
    ({"op": "reparent", "id": "zz", "parent": "tech"}, "未登记"),
])
def test_reparent_validation(op, why):
    s = _base()
    assert why in _rej(_ops(s, op))[0]["reason"]


def test_reparent_rejects_retired_element():
    s = _base()
    _L(s, "a")["status"] = "retired"
    assert "退场" in _rej(_ops(s, {"op": "reparent", "id": "a", "parent": "finance"}))[0]["reason"]


# ── split ────────────────────────────────────────────────────────────

def test_split_inherits_parent_links_and_dedups():
    s = _base()
    r = _ops(s, {"op": "split", "from": "a", "into": [
        {"id": "a1", "label": "甲一期", "element_type": "project"},
        {"id": "a2", "label": "甲二期", "parent": "finance"},
        {"id": "b", "label": "撞名"}]})
    a1, a2 = _L(s, "a1"), _L(s, "a2")
    assert a1["origin"] == "split" and a1["split_from"] == "a" and a1["parent"] == "tech"
    assert a2["parent"] == "finance"
    assert _L(s, "a")["split_into"] == ["a1", "a2"] and _L(s, "a").get("status") is None
    assert "op_split_skipped" in _acts(r) and "op_split" in _acts(r)


def test_split_validation_and_budget():
    s = _base()
    assert "没有可用" in _rej(_ops(s, {"op": "split", "from": "a", "into": []}))[0]["reason"]
    assert "未登记" in _rej(_ops(s, {"op": "split", "from": "zz", "into": [{"id": "q"}]}))[0]["reason"]
    assert "领域线" in _rej(_ops(s, {"op": "split", "from": "tech", "into": [{"id": "q"}]}))[0]["reason"]
    s = _base(element_params={"max_total_elements": 3})
    r = _ops(s, {"op": "split", "from": "a", "into": [{"id": "z1"}]})
    assert "budget_blocked" in _acts(r) and "没有新元素" in _rej(r)[0]["reason"] and "z1" not in [x["id"] for x in s["causal_lines"]]


def test_split_technology_seed_goes_through_tech_proposals():
    s = _base(tech_model_enabled=True)
    r = _ops(s, {"op": "split", "from": "a", "into": [
        {"id": "t1", "label": "T1", "element_type": "technology", "lifecycle_seed": {"stage": "developer"}}]})
    props = [p for p in r["tech_updates"] if p.get("id") == "t1"]
    assert props and props[0]["stage"] == "developer"


# ── 限额与回滚 ───────────────────────────────────────────────────────

def test_ops_per_step_cap_and_per_op_rollback(monkeypatch):
    s = _base()
    ops = [{"op": "reparent", "id": "a", "parent": "finance" if i % 2 == 0 else "tech"} for i in range(eo.OPS_PER_STEP_MAX + 2)]
    r = _ops(s, *ops)
    assert sum("超过上限" in x["reason"] for x in _rej(r)) == 2
    s = _base()
    def half_done(ctx, op):                      # 先改了一半，再炸
        ctx.lines[ctx.index_of("a")] = {**ctx.lines[ctx.index_of("a")], "status": "retired"}
        ctx.log("op_retire", "a")
        raise RuntimeError("boom")

    monkeypatch.setitem(eo._HANDLERS, "retire", half_done)
    r = _ops(s, {"op": "reparent", "id": "b", "parent": "finance"}, {"op": "retire", "id": "a"})
    assert _acts(r) == ["op_reparent", "op_error"]                                  # 半截审计被撤掉
    assert _L(s, "a").get("status") is None                                         # 半截状态被回滚
    assert _L(s, "b")["parent"] == "finance"                                        # 前一条成功的不受影响


# ── 提示词 ───────────────────────────────────────────────────────────

def test_protocol_hint_mentions_element_ops_when_enabled_only():
    hint = er.build_hint(_base(), step=2)
    assert "element_ops" in hint
    for name in eo.OP_NAMES:
        assert f'"op": "{name}"' in hint.replace('\\"', '"'), name
    assert er.build_hint({"causal_lines": []}, step=2) == ""


# ── 周期扫描 ─────────────────────────────────────────────────────────

def test_scan_interval_parsing():
    assert ed.get_scan_interval(None) == 0 and ed.get_scan_interval({}) == 0
    assert ed.get_scan_interval({"element_scan_interval": 3}) == 3
    for bad in (-2, "x", True, None, 0):
        assert ed.get_scan_interval({"element_scan_interval": bad}) == 0


def test_parse_suggestions_drops_junk_caps_and_strips_seed():
    out = ed.parse_suggestions([1, {}, {"id": "x", "lifecycle_seed": {"stage": "lab"}, "future_tree": {"branches": []}},
                                {"label": "标签"}, {"id": "y"}], max_suggestions=2)
    assert [o["id"] for o in out] == ["x", "标签"] and "lifecycle_seed" not in out[0] and "future_tree" not in out[0]
    assert ed.parse_suggestions("bad") == []


def _fake_runner(monkeypatch, payload, captured=None, status="done", raw=None):
    class _St:
        def __init__(self, v): self.value = v

    class FakeStore:
        def __init__(self, root): pass
        def load(self, name):
            assert name == "element_discovery"
            return SimpleNamespace(steps=[SimpleNamespace(id="element_discovery")])

    class FakeRunner:
        def __init__(self, cfg): pass
        def run(self, wf, inputs):
            if captured is not None:
                captured.update(inputs)
            out = raw if raw is not None else json.dumps(payload, ensure_ascii=False)
            return SimpleNamespace(status=status, step_results=[
                SimpleNamespace(step_id="element_discovery", status=_St(status), output=out, error="e")])

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)


def test_suggest_elements_assembles_inputs(tmp_path, monkeypatch):
    cap: dict = {}
    _fake_runner(monkeypatch, {"suggestions": [{"id": "n1", "label": "新对象", "why_key": "w",
                                                 "relations": [{"with": "a", "direction": "affects"}]}]}, cap)
    s = _base(element_candidates=[{"id": "pool", "label": "池", "mentions": 1}])
    s["causal_lines"][3]["status"] = "retired"
    hist = [SimpleNamespace(step=i, summary=f"s{i}", narrative=f"n{i}",
                            sampled_events=[{"label": "事件X"}, {"label": "压", "suppressed_by_cap": True}]) for i in range(1, 9)]
    out = ed.suggest_elements(object(), tmp_path, s, {"entities": {"co": 1}}, hist)
    reg = json.loads(cap["registered_json"])
    assert [r["id"] for r in reg] == ["tech", "finance", "a", "c"]  # 退场的不列
    assert json.loads(cap["candidates_json"]) == [{"id": "pool", "label": "池", "mentions": 1}]
    assert json.loads(cap["entities_json"]) == {"entities": {"co": 1}}
    h = json.loads(cap["recent_history_json"])
    assert len(h) == ed.RECENT_STEPS_DEFAULT and h[-1]["step"] == 8 and h[-1]["events"] == ["事件X"]
    assert out[0]["id"] == "n1"


def test_suggest_elements_errors(tmp_path, monkeypatch):
    _fake_runner(monkeypatch, None, status="failed")
    with pytest.raises(ed.ElementDiscoveryError, match="未成功"):
        ed.suggest_elements(object(), tmp_path, _base(), {}, [])
    _fake_runner(monkeypatch, None, raw="not json at all")
    with pytest.raises(ed.ElementDiscoveryError, match="无法解析"):
        ed.suggest_elements(object(), tmp_path, _base(), {}, [])

    class NoWf:
        def __init__(self, root): pass
        def load(self, name): return None
    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", NoWf)
    with pytest.raises(ed.ElementDiscoveryError, match="找不到"):
        ed.suggest_elements(object(), tmp_path, _base(), {}, [])


def test_scan_registration_uses_same_validation():
    s = _base()
    audit = ed.register_suggestions(s, [
        {"id": "gpu", "label": "GPU", "element_type": "technology", "parent": "tech", "why_key": "反复出现"},   # 无关系但有 why_key → 登记（扫描=已满足提次）
        {"id": "甲", "label": "项目甲"},                                                                      # 别名命中 → 合并不新建
        {"id": "vague", "label": "模糊"},                                                                     # 既无关系也无 why_key → 候选池
        {"id": "rel", "label": "有关系", "relations": [{"with": "a", "direction": "affects"}]},
    ], step=9)
    ids = [x["id"] for x in s["causal_lines"]]
    assert "gpu" in ids and "rel" in ids and "vague" not in ids and ids.count("a") == 1
    assert [c["id"] for c in s["element_candidates"]] == ["vague"]
    assert all(x["source"] == "element_scan" for x in audit)
    assert _L(s, "gpu")["origin"] == "discovered" and _L(s, "gpu")["born_step"] == 9


def test_scan_never_queues_lifecycle_seed():
    s = _base(tech_model_enabled=True)
    audit = er.register_scan_items(s, [{"id": "t9", "label": "T9", "element_type": "technology", "parent": "tech",
                                         "why_key": "w", "lifecycle_seed": {"stage": "developer"}}], step=3)
    assert "t9" in [x["id"] for x in s["causal_lines"]]
    assert not [a for a in audit if a["action"].startswith("lifecycle_seed")]


def test_scan_respects_budget_and_is_noop_when_element_mode_off():
    s = _base(element_params={"max_total_elements": 3})
    ed.register_suggestions(s, [{"id": "n1", "why_key": "w"}], step=2)
    assert "n1" not in [x["id"] for x in s["causal_lines"]] and s["element_candidates"][0]["id"] == "n1"
    off = {"causal_lines": [_line("x")]}
    assert ed.register_suggestions(off, [{"id": "n1", "why_key": "w"}], step=2) == [] and [x["id"] for x in off["causal_lines"]] == ["x"]


def test_scan_now_does_not_mutate_input_and_returns_new_settings(tmp_path, monkeypatch):
    _fake_runner(monkeypatch, {"suggestions": [{"id": "n1", "label": "N", "why_key": "w"}]})
    s = _base()
    res = ed.scan_now(object(), tmp_path, s, {}, [], step=4)
    assert "n1" not in [x["id"] for x in s["causal_lines"]]
    assert "n1" in [x["id"] for x in res["settings"]["causal_lines"]] and res["suggestions"][0]["id"] == "n1"


def test_safe_scan_in_step_gating_and_error_swallowing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ed, "suggest_elements", lambda *a, **k: (calls.append(1), [{"id": "n1", "label": "N", "why_key": "w"}])[1])
    m = SimpleNamespace(settings=_base())
    st = SimpleNamespace(step=4, vars={}, element_audit=[])
    ed.safe_scan_in_step(object(), tmp_path, m, history=[], next_state=st)        # 间隔 0：不跑
    assert calls == [] and st.element_audit == []
    m.settings["element_scan_interval"] = 3
    ed.safe_scan_in_step(object(), tmp_path, m, history=[], next_state=st)        # 4 不是 3 的倍数
    assert calls == []
    st.step = 6
    ed.safe_scan_in_step(object(), tmp_path, m, history=[], next_state=st)
    assert len(calls) == 1 and "n1" in [x["id"] for x in m.settings["causal_lines"]]
    assert st.element_audit[0]["action"] == "scan" and st.element_audit[0]["found"] == 1
    off = SimpleNamespace(settings={"element_scan_interval": 1, "causal_lines": []})
    ed.safe_scan_in_step(object(), tmp_path, off, history=[], next_state=SimpleNamespace(step=1, vars={}, element_audit=[]))
    assert len(calls) == 1                                                        # 元素模式关：不跑
    monkeypatch.setattr(ed, "suggest_elements", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("llm down")))
    m2 = SimpleNamespace(settings=_base(element_scan_interval=1))
    st2 = SimpleNamespace(step=2, vars={}, element_audit=[])
    ed.safe_scan_in_step(object(), tmp_path, m2, history=[], next_state=st2)
    assert st2.element_audit[0]["action"] == "scan_error" and [x["id"] for x in m2.settings["causal_lines"]] == [x["id"] for x in _base()["causal_lines"]]


# ── advance() 端到端 ─────────────────────────────────────────────────

def _lines(data_dir):
    m, _c, _h = get_simulation(data_dir, "sim1")
    return {x["id"]: x for x in m.settings["causal_lines"]}


def test_advance_applies_element_ops_audits_and_persists(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, _base())
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        1, element_ops=[{"op": "merge", "from": "a", "into": "b"}, {"op": "retire", "id": "c", "reason": "终止"}],
        line_updates={"a": {"summary": "旧 id 写法"}}))
    ls = _lines(data_dir)
    assert ls["a"]["status"] == "merged" and ls["c"]["status"] == "retired"
    assert list(state.line_updates) == ["b"]                    # 同一步里写旧 id 的更新落到合并目标
    assert [a["action"] for a in state.element_audit if a["action"].startswith("op_")] == ["op_merge", "op_retire"]
    snap = {x["id"]: x for x in store.load_history("main")[-1].dynamic_snapshot["causal_lines"]}
    assert snap["a"]["merged_into"] == "b"                     # 随分支快照


def test_advance_ops_are_branch_scoped(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _base())
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1))
    branch = bm.fork_branch(data_dir, "sim1", from_step=1)
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, element_ops=[{"op": "merge", "from": "a", "into": "b"}]))
    assert _lines(data_dir)["a"]["status"] == "merged"
    bm.switch_branch(data_dir, "sim1", "main")
    assert _lines(data_dir)["a"].get("status") is None
    bm.switch_branch(data_dir, "sim1", branch)
    assert _lines(data_dir)["a"]["status"] == "merged"


def test_advance_ignores_element_ops_when_element_mode_off(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    s = _base()
    s["element_modeling_enabled"] = False
    _make_sim(data_dir, s)
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, element_ops=[{"op": "retire", "id": "a"}]))
    assert _lines(data_dir)["a"].get("status") is None and not state.element_audit


def test_advance_bad_ops_never_break_the_step(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _base())
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, element_ops="garbage"))
    assert state.step == 1
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, element_ops=[None, 3, {"op": "merge"}]))
    assert state.step == 2 and [a["action"] for a in state.element_audit] == ["op_rejected"]


def test_advance_periodic_scan_registers_before_snapshot(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, _base(element_scan_interval=1))
    monkeypatch.setattr(ed, "suggest_elements", lambda *a, **k: [{"id": "sc1", "label": "扫描所得", "parent": "tech", "element_type": "project",
                                                                  "relations": [{"with": "a", "direction": "affects"}]}])
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(1))
    assert "sc1" in _lines(data_dir)
    assert any(a["action"] == "scan" for a in state.element_audit)
    snap_ids = [x["id"] for x in store.load_history("main")[-1].dynamic_snapshot["causal_lines"]]
    assert "sc1" in snap_ids                                    # 扫描结果进了本步分支快照
