"""tests/test_anatomy.py — 第二十四轮 A1：元素剖面的数据模型、存取层、兼容。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.2 / §7 / §8 A1。
覆盖：规整（非法丢弃/不补缺省/幂等/体积上限）、basis 规则、趋势不设白名单、条件树、模板、参数回退、
存取（领域线/退场拒绝、清除）、`normalize_element` 契约（无剖面逐字节不变）、快照往返、
新实例开关、合并保护、只读视图与体检。无需真实 LLM。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import anatomy as an
from world_simulator import anatomy_templates as at
from world_simulator import dynamic_state
from world_simulator import element_registry as er
from world_simulator import element_view as ev
from world_simulator.engine.materialize import materialize_simulation
from world_simulator.store import SimStore


def full():
    return {
        "components": [
            {"id": "electrolyte", "name": "固态电解质", "readiness": 0.5},
            {"id": "mfg", "name": "制造", "readiness": 0.2, "requires": ["component:electrolyte"]},
        ],
        "metrics": [
            {"id": "density", "name": "能量密度", "unit": "Wh/kg",
             "current": {"value": 350, "as_of": "2026-06", "basis": {"state": "sourced", "evidence_ids": ["ev_1"]}},
             "target": {"op": ">=", "value": 400},
             "trend": {"kind": "logistic", "params": {"cap": {"value": 500, "low": 450, "high": 600},
                                                      "rate": {"value": 0.25, "low": 0.1, "high": 0.4}}}},
        ],
        "bottlenecks": [
            {"id": "iface", "type": "physical", "blocks": ["component:mfg"], "severity": "high", "status": "open",
             "resolution_paths": [
                 {"id": "coat", "p_success": 0.5, "duration_days": {"low": 540, "mode": 900, "high": 1500}},
                 {"id": "new", "p_success": 0.25},
             ], "fallback": ["new", "ghost"]},
        ],
        "milestones": [
            {"id": "pilot", "desc": "中试",
             "criteria": {"all": [{"bottleneck": "iface", "status": "resolved"},
                                   {"component": "mfg", "min_readiness": 0.5}]},
             "maps_to_stage": "developer"},
        ],
        "adoption_gates": [
            {"id": "oem", "market": "ev",
             "criteria": {"all": [{"metric": "battery#cost", "op": "<=", "value": 120}]},
             "unlocks": {"adoption_cap": 0.1, "other": 1}},
        ],
        "assumptions": [
            {"id": "a1", "statement": "界面问题可工程化", "attached_to": ["bottleneck:iface"], "prior_p_true": 0.5,
             "if_false": {"overrides": [{"ref": "bottleneck:iface", "set": {"status": "open"}}]}},
        ],
        "signals": [{"id": "s1", "watch": "中试线公告", "means": "走快路径", "refs": ["bottleneck:iface"]}],
        "meta": {"anatomy_status": "draft", "key": True, "template": "technology"},
    }


# ── 规整 ─────────────────────────────────────────────────────────────


def test_full_roundtrip_and_idempotent():
    a = an.normalize_anatomy(full())
    assert an.normalize_anatomy(a) == a
    assert an.normalize_anatomy(json.loads(json.dumps(a))) == a
    assert a["adoption_gates"][0]["unlocks"] == {"adoption_cap": 0.1}  # 未知解锁项丢弃
    assert a["bottlenecks"][0]["fallback"] == ["new"]  # 指向不存在路径的 fallback 丢弃


def test_garbage_inputs_give_empty():
    for raw in (None, 1, "x", [], {}, {"components": "no"}, {"zzz": [1]}, {"metrics": [1, "a", None]}):
        assert an.normalize_anatomy(raw) == {}


def test_no_defaults_added():
    a = an.normalize_anatomy({"components": [{"id": "c"}]})
    assert a == {"components": [{"id": "c"}]}


def test_id_rules_and_dedup_and_name_derivation():
    a, dropped = an.normalize_anatomy_report({"components": [
        {"id": "a b"}, {"id": "a_b"}, {"id": "x#y"}, {"id": "k:v"}, {"name": "电解质"}, {"id": ""},
    ]})
    assert [c["id"] for c in a["components"]] == ["a_b", "电解质"]
    assert len(dropped) == 4


def test_numbers_must_be_finite_and_ranges_enforced():
    a = an.normalize_anatomy({"components": [
        {"id": "a", "readiness": 1.5}, {"id": "b", "readiness": float("nan")},
        {"id": "c", "readiness": True}, {"id": "d", "readiness": 1.0},
    ]})
    assert [("readiness" in c) for c in a["components"]] == [False, False, False, True]
    p = an.normalize_param({"value": 1, "low": 5, "high": 2, "dist": "weird"})
    assert p == {"value": 1}  # 区间颠倒丢两端，未知分布丢字段
    assert an.normalize_param({"value": float("inf")}) is None
    assert an.normalize_param(3) == {"value": 3}
    assert an.normalize_param("3") is None


def test_basis_rules():
    assert an.normalize_basis(None) == {"state": "llm_prior"}
    assert an.normalize_basis({"state": "sourced"}) == {"state": "llm_prior"}  # 无证据降级
    b = an.normalize_basis({"state": "sourced", "evidence_ids": ["e1", "e1", "bad:id"], "confidence": "high"})
    assert b == {"state": "sourced", "evidence_ids": ["e1"], "confidence": "high"}
    assert an.normalize_basis({"state": "nope"}) == {"state": "llm_prior"}
    # 默认 basis 不落盘
    a = an.normalize_anatomy({"components": [{"id": "c", "basis": {"state": "llm_prior"}}]})
    assert "basis" not in a["components"][0]
    assert an.basis_of(a["components"][0])["state"] == "llm_prior"


def test_trend_has_no_whitelist():
    for kind in ("logistic", "custom_expr", "llm_reported", "my_weird_curve"):
        a = an.normalize_anatomy({"metrics": [{"id": "m", "trend": {"kind": kind, "expr": "t*2"}}]})
        assert a["metrics"][0]["trend"]["kind"] == kind
    a = an.normalize_anatomy({"metrics": [{"id": "m", "trend": {"kind": ""}}]})
    assert "trend" not in a["metrics"][0]


def test_metric_target_bounds_and_current():
    a = an.normalize_anatomy({"metrics": [
        {"id": "m", "target": {"op": "~", "value": 1}, "bounds": {"min": 5, "max": 1}, "current": {"value": "x"}},
    ]})
    assert a["metrics"][0] == {"id": "m"}


def test_bottleneck_paths_and_duration_order():
    a = an.normalize_anatomy({"bottlenecks": [{"id": "b", "severity": "huge", "resolution_paths": [
        {"id": "p1", "p_success": 2, "duration_days": {"low": 10, "mode": 5}},
        {"id": "p1"}, {"id": "p2", "status": "failed", "resolve_at_day": 12},
    ]}]})
    b = a["bottlenecks"][0]
    assert "severity" not in b
    assert b["resolution_paths"][0] == {"id": "p1"}
    assert b["resolution_paths"][1] == {"id": "p2", "status": "failed", "resolve_at_day": 12}


def test_criteria_shapes():
    n = an.normalize_criteria
    assert n({"metric": "x#m", "op": ">=", "value": 1}) == {"metric": "x#m", "op": ">=", "value": 1}
    assert n({"metric": "m", "op": "bad", "value": 1}) is None
    assert n({"component": "c", "min_readiness": 2}) is None
    assert n({"bottleneck": "b", "status": "zzz"}) is None
    assert n({"var": "x", "op": ">", "value": 3}) == {"var": "x", "op": ">", "value": 3}
    assert n({"all": [{"bottleneck": "b", "status": "bad"}]}) is None
    assert n({"not": {"bottleneck": "b", "status": "open"}}) == {"not": {"bottleneck": "b", "status": "open"}}
    deep = {"bottleneck": "b", "status": "open"}
    for _ in range(10):
        deep = {"all": [deep]}
    assert n(deep) is None  # 超深度
    wide = {"all": [{"bottleneck": f"b{i}", "status": "open"} for i in range(40)]}
    assert len(n(wide)["all"]) == an.MAX_CRITERIA_NODES - 1


def test_assumption_needs_statement_and_signal_needs_watch():
    a, dropped = an.normalize_anatomy_report({
        "assumptions": [{"id": "x"}, {"id": "y", "statement": "s"}],
        "signals": [{"id": "x"}, {"id": "y", "watch": "w"}],
    })
    assert [i["id"] for i in a["assumptions"]] == ["y"] and [i["id"] for i in a["signals"]] == ["y"]
    assert len(dropped) == 2


def test_size_caps():
    a, dropped = an.normalize_anatomy_report({"components": [{"id": f"c{i}"} for i in range(100)]})
    assert len(a["components"]) == an.MAX_ITEMS["components"]
    assert dropped
    long = an.normalize_anatomy({"components": [{"id": "c", "name": "x" * 500, "desc": "y" * 900}]})
    assert len(long["components"][0]["name"]) == an.MAX_NAME_LEN
    assert len(long["components"][0]["desc"]) == an.MAX_TEXT_LEN


def test_meta_only_counts_as_non_empty():
    assert an.normalize_anatomy({"meta": {"key": True, "anatomy_status": "bad"}}) == {"meta": {"key": True}}
    assert an.normalize_anatomy({"meta": {"anatomy_status": "bad"}}) == {}


# ── 模板 ─────────────────────────────────────────────────────────────


def test_templates():
    assert at.template_name(" Technology ") == "technology"
    assert at.template_name("galaxy") == "default" and at.template_name(None) == "default"
    t = at.get_template("project")
    t["metrics"].append("x")
    assert "x" not in at.get_template("project")["metrics"]  # 返回拷贝
    assert set(at.known_templates()) >= {"technology", "policy", "default"}
    assert "技术类元素建议关注" in at.slot_hint("technology")
    assert not any(ch.isdigit() for k in at.known_templates() for ch in str(at._TEMPLATES[k]))  # 不带数值


# ── 参数 ─────────────────────────────────────────────────────────────


def test_params_defaults_and_fallback():
    p = an.get_params(None)
    assert p["key_element_count"] == 5 and p["deep_enabled"] is True and p["deep_allow_search"] is False
    assert p["deep_max_calls_total"] is None and p["mc_runs"] == 1000
    s = {"anatomy_params": {
        "key_element_count": None, "deep_max_calls_per_step": 0, "mc_runs": 0, "deviation_policy": "zzz",
        "deep_enabled": "yes", "research_ttl_days": True, "mc_seed": 7, "trend_recommended": [],
    }}
    p = an.get_params(s)
    assert p["key_element_count"] is None  # null = 不限
    assert p["deep_max_calls_per_step"] == 0  # 0 就是 0
    assert p["mc_runs"] == 1000 and p["deviation_policy"] == "rebase" and p["deep_enabled"] is True
    assert p["research_ttl_days"] == 180 and p["mc_seed"] == 7
    assert set(an.invalid_param_keys(s)) == {"mc_runs", "deviation_policy", "deep_enabled", "research_ttl_days", "trend_recommended"}
    # 默认值字典不被污染
    an.get_params(None)["trend_recommended"].append("x")
    assert "x" not in an.get_params(None)["trend_recommended"]
    assert an.invalid_param_keys({"anatomy_params": "bad"}) == []


def test_is_enabled_requires_both_switches():
    assert not an.is_enabled(None)
    assert not an.is_enabled({"anatomy_enabled": True})
    assert not an.is_enabled({"element_modeling_enabled": True})
    assert an.is_enabled({"element_modeling_enabled": True, "anatomy_enabled": True})


# ── 存取 ─────────────────────────────────────────────────────────────


def settings_with_lines():
    return {"element_modeling_enabled": True, "anatomy_enabled": True, "causal_lines": [
        {"id": "dom", "label": "领域", "kind": "domain"},
        {"id": "battery", "label": "固态电池", "kind": "element", "parent": "dom", "element_type": "technology"},
        {"id": "old", "label": "旧", "kind": "element", "status": "retired"},
        {"id": "legacy", "label": "旧式线"},
    ]}


def test_set_and_read_anatomy():
    s = settings_with_lines()
    ok, err, dropped = an.set_anatomy(s, "固态电池", full())
    assert ok and not err
    assert an.read_anatomy(s, "battery") == s["causal_lines"][1]["anatomy"]
    assert an.key_elements(s) == ["battery"]
    assert [x["id"] for x in an.lines_with_anatomy(s)] == ["battery"] and an.has_anatomy(s)
    ok, _, _ = an.set_anatomy(s, "battery", {})
    assert ok and "anatomy" not in s["causal_lines"][1] and not an.has_anatomy(s)


def test_set_anatomy_refusals():
    s = settings_with_lines()
    before = copy.deepcopy(s)
    for ref in ("dom", "old", "nope"):
        ok, err, _ = an.set_anatomy(s, ref, full())
        assert not ok and err
    assert s == before


def test_set_anatomy_on_legacy_line_works():
    s = settings_with_lines()
    assert an.set_anatomy(s, "legacy", {"components": [{"id": "c"}]})[0]


# ── normalize_element 契约 ───────────────────────────────────────────


def test_normalize_element_no_anatomy_is_byte_identical():
    for raw in ({"id": "a"}, {"id": "a", "kind": "element", "element_type": "technology", "x": 1}):
        assert json.dumps(er.normalize_element(raw), sort_keys=True) == json.dumps(raw, sort_keys=True)


def test_normalize_element_normalizes_or_drops_anatomy():
    line = er.normalize_element({"id": "a", "anatomy": {"components": [{"id": "c", "readiness": 9}]}})
    assert line["anatomy"] == {"components": [{"id": "c"}]}
    assert "anatomy" not in er.normalize_element({"id": "a", "anatomy": "junk"})
    assert "anatomy" not in er.normalize_element({"id": "a", "anatomy": {}})


# ── 快照 / 分支 ──────────────────────────────────────────────────────


def test_anatomy_rides_with_causal_lines_snapshot():
    s = settings_with_lines()
    an.set_anatomy(s, "battery", full())
    snap = dynamic_state.extract(s)
    assert snap["causal_lines"][1]["anatomy"] == s["causal_lines"][1]["anatomy"]
    later = copy.deepcopy(s)
    an.set_anatomy(later, "battery", {"components": [{"id": "z"}]})
    later = dynamic_state.apply_to_settings(later, snap)
    assert later["causal_lines"][1]["anatomy"] == s["causal_lines"][1]["anatomy"]  # 回滚到快照里的剖面


# ── 新实例开关 / 合并保护 ────────────────────────────────────────────


def _mat(tmp_path, settings):
    m = materialize_simulation(tmp_path / "d", template="life_sim", intent="i", title="t", summary="s",
                               vars={"a": 1}, options=[], settings=settings)
    return SimStore.for_root(tmp_path / "d", m.sim_id).load_manifest()


def test_new_instance_defaults(tmp_path):
    assert _mat(tmp_path, {}).settings["anatomy_enabled"] is True


def test_explicit_and_element_off_respected(tmp_path):
    assert _mat(tmp_path, {"anatomy_enabled": False}).settings["anatomy_enabled"] is False
    assert "anatomy_enabled" not in _mat(tmp_path / "x", {"element_modeling_enabled": False}).settings


def test_merge_rejects_source_with_anatomy_but_allows_target():
    def line(lid, **extra):
        d = {"id": lid, "label": lid, "kind": "element", "element_type": "project", "origin": "seed",
             "born_step": 0, "profile_status": "complete"}
        d.update(extra)
        return d

    def base():
        return {"element_modeling_enabled": True, "causal_lines": [line("a"), line("b")]}

    s = base()
    s["causal_lines"][0]["anatomy"] = {"components": [{"id": "c"}]}
    r = er.process_step(s, step=3, ops=[{"op": "merge", "from": "a", "into": "b"}])
    rej = [x for x in r["audit"] if x["action"] == "op_rejected"]
    assert rej and "anatomy" in rej[0]["reason"]
    assert s["causal_lines"][0].get("status") is None and "anatomy" in s["causal_lines"][0]
    # 保留方带剖面没问题
    s = base()
    s["causal_lines"][1]["anatomy"] = {"components": [{"id": "c"}]}
    r = er.process_step(s, step=3, ops=[{"op": "merge", "from": "a", "into": "b"}])
    assert [x["action"] for x in r["audit"]] == ["op_merge"]


# ── 统计 / 体检 / 视图 ───────────────────────────────────────────────


def test_basis_stats_and_counts():
    a = an.normalize_anatomy(full())
    st = an.basis_stats(a)
    # 条目：2 组件 + 1 指标 + 1 瓶颈 + 1 里程碑 + 1 门槛 + 1 假设 + 1 信号 = 8，外加指标现值 = 9
    assert st["total"] == 9 and st["sourced"] == 1 and st["llm_prior"] == 8
    assert abs(st["llm_prior_ratio"] - 8 / 9) < 1e-9
    assert an.basis_stats(None)["llm_prior_ratio"] is None
    assert an.counts(a)["components"] == 2 and "approaches" not in an.counts(a)


def test_validate_reports_but_never_changes():
    a = an.normalize_anatomy({
        "components": [{"id": "c", "requires": ["component:ghost", "other#component:x", "component:c"]}],
        "metrics": [{"id": "m", "current": {"value": 10}, "bounds": {"max": 5},
                     "trend": {"kind": "linear", "params": {"r": {"value": 9, "low": 1, "high": 2}}}}],
        "milestones": [{"id": "ms"}],
    })
    before = copy.deepcopy(a)
    kinds = sorted(p["kind"] for p in an.validate_anatomy(a))
    assert kinds == ["current_out_of_bounds", "dangling_ref", "no_criteria", "param_out_of_range"]
    assert a == before
    full_a = an.normalize_anatomy(full())
    assert not [p for p in an.validate_anatomy(full_a) if p["kind"] == "dangling_ref"]
    ev_problems = an.validate_anatomy(full_a, known_evidence_ids=[])
    assert [p["kind"] for p in ev_problems if p["kind"] == "dangling_evidence"]
    assert not [p for p in an.validate_anatomy(full_a, known_evidence_ids=["ev_1"]) if p["kind"] == "dangling_evidence"]


def test_parse_ref():
    assert an.parse_ref("component:x") == (None, "component", "x")
    assert an.parse_ref("batt#bottleneck:y") == ("batt", "bottleneck", "y")
    assert an.parse_ref("batt#cost") == ("batt", None, "cost")


def test_clock_days_reuses_elapsed():
    class S:
        def __init__(self, step, d): self.step, self.elapsed_days = step, d
    assert an.clock_days([S(0, None), S(1, 10), S(2, 5)], 2) == (15.0, True)
    assert an.clock_days([S(1, 10), S(2, None)], 2) == (10.0, False)


def test_build_profile_gating_and_content():
    s = settings_with_lines()
    an.set_anatomy(s, "battery", full())
    assert ev.build_profile({k: v for k, v in s.items() if k != "anatomy_enabled"}, "battery") == {"enabled": False}
    assert ev.build_profile(s, "legacy") == {"enabled": True, "has_anatomy": False}
    assert ev.build_profile(s, "nope") == {"enabled": True, "has_anatomy": False}
    p = ev.build_profile(s, "battery")
    assert p["has_anatomy"] and p["template"] == "technology" and p["key"] is True and p["status_label"] == "未审阅"
    assert [x["part"] for x in p["sections"]] == [
        "components", "metrics", "bottlenecks", "adoption_gates", "milestones", "assumptions", "signals"]
    metric = p["sections"][1]["rows"][0]
    assert metric["basis"] == "LLM 先验" and "350" in metric["detail"] and "logistic" in metric["detail"]
    ms = p["sections"][4]["rows"][0]["detail"]
    assert "iface" in ms and "且" in ms
    assert p["stats"]["sourced"] == 1


def test_disabled_instance_unaffected_by_helpers():
    s = {"causal_lines": [{"id": "x"}]}
    assert not an.has_anatomy(s) and an.key_elements(s) == [] and ev.build_profile(s, "x") == {"enabled": False}
