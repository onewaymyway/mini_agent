"""tests/test_element_tiers.py — 第二十三轮 E4：元素的派生分级与 prompt 预算。

设计依据：`next_doc/world_simulator_element_causal_lines_plan.md` §4.6、§5.1、§6 E4。
覆盖：分级参数（含 `null`=不限）、分级规则逐条、预算与排序、钉住不受预算裁剪、分支正确（两个分支
派生出不同分级）、prompt 渲染（active 完整/watch 一行/dormant 索引及上限）、**prompt 规模**
（元素从 10 增到 100，active 固定 12，增量只来自 watch 摘要与受上限约束的休眠索引）、
元素模式关闭时提示词逐字节不变、不拦截（更新休眠元素照常接受，下一步升为 active）。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import causal_tree, spec_generator
from world_simulator import element_registry as er
from world_simulator import element_tiers as et
from world_simulator.engine.management import get_simulation

from test_fast_forward import _default_payload
from test_tech_model import _adv, _make_sim


# ── 夹具 ─────────────────────────────────────────────────────────────


def _line(lid, label=None, **extra):
    line = {"id": lid, "label": label or lid, "time_granularity": "",
            "future_tree": causal_tree.build_default_future_tree(label or lid, 0)}
    line.update(extra)
    return line


def _el(lid, label=None, **extra):
    extra.setdefault("kind", "element")
    extra.setdefault("born_step", 0)
    return _line(lid, label, **extra)


def _settings(*lines, **extra):
    s = {"element_modeling_enabled": True,
         "causal_lines": [_line("tech", "技术", kind="domain"), *lines]}
    s.update(extra)
    return s


def _st(step, line_updates=None, tree_updates=None, tech_updates=None):
    return SimpleNamespace(step=step, line_updates=line_updates or {}, tree_updates=tree_updates or [],
                           tech_updates=tech_updates or [])


def _tiers(settings, history=(), step=20, **kw):
    return et.derive_tiers(list(history), settings, step=step, **kw)


def _quiet_branches(line):
    """把一条线的所有分支都标成 dormant（默认树里不一定是 dormant，避免"有活跃分支"干扰规则测试）。"""
    for b in line["future_tree"]["branches"]:
        b["status"] = "dormant"
    return line


# ── 参数 ─────────────────────────────────────────────────────────────


def test_tier_params_defaults_and_overrides():
    assert et.get_tier_params(None) == {
        "max_active_in_prompt": 12, "active_window_steps": 3, "watch_window_steps": 10, "dormant_index_max": 60,
    }
    p = et.get_tier_params({"element_params": {
        "max_active_in_prompt": 5, "active_window_steps": 2, "watch_window_steps": 7, "dormant_index_max": 0}})
    assert p == {"max_active_in_prompt": 5, "active_window_steps": 2, "watch_window_steps": 7, "dormant_index_max": 0}


def test_tier_params_null_means_unlimited_only_for_max_active_and_zero_is_not_unlimited():
    assert et.get_tier_params({"element_params": {"max_active_in_prompt": None}})["max_active_in_prompt"] is None
    assert et.get_tier_params({"element_params": {"max_active_in_prompt": 0}})["max_active_in_prompt"] == 0
    for key in ("active_window_steps", "watch_window_steps", "dormant_index_max"):
        s = {"element_params": {key: None}}
        assert et.get_tier_params(s)[key] == et.TIER_DEFAULT_PARAMS[key]
        assert et.invalid_tier_param_keys(s) == [key]


@pytest.mark.parametrize("key,bad", [
    ("max_active_in_prompt", -1), ("max_active_in_prompt", "x"), ("max_active_in_prompt", True),
    ("active_window_steps", 0), ("active_window_steps", 1.5), ("watch_window_steps", -3),
    ("dormant_index_max", -1), ("dormant_index_max", False),
])
def test_tier_params_invalid_values_fall_back_and_are_reported(key, bad):
    s = {"element_params": {key: bad}}
    assert et.get_tier_params(s)[key] == et.TIER_DEFAULT_PARAMS[key]
    assert et.invalid_tier_param_keys(s) == [key]
    assert et.invalid_tier_param_keys({"element_params": "x"}) == [] and et.invalid_tier_param_keys(None) == []


def test_tier_params_share_the_element_params_dict_with_earlier_stages():
    s = {"element_params": {"create_max_elements": 3, "max_total_elements": 9, "max_active_in_prompt": 4}}
    assert er.get_params(s)["create_max_elements"] == 3
    assert er.get_runtime_params(s)["max_total_elements"] == 9
    assert et.get_tier_params(s)["max_active_in_prompt"] == 4
    assert er.invalid_runtime_param_keys(s) == [] and et.invalid_tier_param_keys(s) == []


def test_watch_window_never_smaller_than_active_window():
    s = _settings(_quiet_branches(_el("a")), element_params={"active_window_steps": 6, "watch_window_steps": 2})
    # watch 窗口被抬到 6：不会出现"active 窗口比 watch 窗口还大"的倒挂
    assert _tiers(s, [_st(15, {"a": {}})], step=20)["tiers"]["a"] == et.ACTIVE    # 年龄 5 ≤ 6
    assert _tiers(s, [_st(14, {"a": {}})], step=20)["tiers"]["a"] == et.ACTIVE    # 年龄 6 ≤ 6（边界）
    assert _tiers(s, [_st(13, {"a": {}})], step=20)["tiers"]["a"] == et.DORMANT   # 年龄 7：直接休眠，没有 watch 档


# ── 分级规则 ─────────────────────────────────────────────────────────


def test_domain_lines_and_non_alive_elements_are_not_tiered():
    s = _settings(_el("a"), _el("old", status="retired"), _el("gone", status="merged", merged_into="a"))
    out = _tiers(s)
    assert set(out["tiers"]) == {"a"}


def test_recent_registration_counts_as_activity():
    s = _settings(_quiet_branches(_el("new", born_step=18)), _quiet_branches(_el("old", born_step=2)))
    out = _tiers(s, step=20)
    assert out["tiers"] == {"new": et.ACTIVE, "old": et.DORMANT}
    assert out["reasons"]["new"] == ["刚登记"]


def test_line_without_born_step_is_treated_as_born_at_zero():
    line = _quiet_branches(_line("legacy"))   # 旧式独立线：没有 kind/born_step
    assert _tiers(_settings(line), step=2)["tiers"]["legacy"] == et.ACTIVE
    assert _tiers(_settings(line), step=30)["tiers"]["legacy"] == et.DORMANT


def test_recent_line_updates_make_active_then_watch_then_dormant():
    s = _settings(_quiet_branches(_el("a")))
    h = [_st(10, {"a": {"summary": "x"}})]
    assert _tiers(s, h, step=12)["tiers"]["a"] == et.ACTIVE      # 年龄 2 ≤ 3
    assert _tiers(s, h, step=13)["tiers"]["a"] == et.ACTIVE      # 年龄 3 ≤ 3（含边界）
    assert _tiers(s, h, step=14)["tiers"]["a"] == et.WATCH       # 年龄 4
    assert _tiers(s, h, step=20)["tiers"]["a"] == et.WATCH       # 年龄 10 ≤ 10（含边界）
    assert _tiers(s, h, step=21)["tiers"]["a"] == et.DORMANT     # 年龄 11


def test_tree_updates_and_tech_transitions_count_as_progress_but_other_tech_actions_do_not():
    s = _settings(*[_quiet_branches(_el(i)) for i in ("t", "x", "reg", "held")])
    h = [_st(18, tree_updates=[{"line_id": "t"}],
             tech_updates=[{"action": "transition", "tech_id": "x"}, {"action": "registered", "tech_id": "reg"},
                           {"action": "held", "tech_id": "held"}, {"action": "time"}])]
    tiers = _tiers(s, h, step=20)["tiers"]
    assert tiers["t"] == et.ACTIVE and tiers["x"] == et.ACTIVE
    assert tiers["reg"] == et.DORMANT and tiers["held"] == et.DORMANT   # 登记/驳回不算"进展"
    h2 = [_st(18, tech_updates=[{"action": "regression", "tech_id": "x"}])]
    assert _tiers(s, h2, step=20)["tiers"]["x"] == et.ACTIVE


def test_history_references_resolve_through_aliases_and_old_ids():
    s = _settings(_quiet_branches(_el("gpt_5", "GPT-5", aliases=["gpt5", "GPT 5"])))
    h = [_st(19, {"GPT5": {}})]                       # 历史里用的是别名（规范化后命中）
    assert _tiers(s, h, step=20)["tiers"]["gpt_5"] == et.ACTIVE
    assert _tiers(s, [_st(19, {"nobody": {}})], step=20)["tiers"]["gpt_5"] == et.DORMANT   # 未知 id 忽略、不报错


def test_live_branch_keeps_a_quiet_element_active():
    quiet = _quiet_branches(_el("quiet"))
    live = _quiet_branches(_el("live"))
    live["future_tree"]["branches"][0]["status"] = "emerging"
    out = _tiers(_settings(quiet, live), step=40)
    assert out["tiers"] == {"quiet": et.DORMANT, "live": et.ACTIVE}
    assert out["reasons"]["live"] == ["有活跃分支"]
    live["future_tree"]["branches"][0]["status"] = "resolved"      # 终态不算
    assert _tiers(_settings(quiet, live), step=40)["tiers"]["live"] == et.DORMANT


def test_tier_pin_and_user_feedback_force_active():
    s = _settings(_quiet_branches(_el("pinned", tier_pin="active")),
                  _quiet_branches(_el("fb", user_feedback="请更悲观一点")),
                  _quiet_branches(_el("none")))
    out = _tiers(s, step=50)
    assert out["tiers"] == {"pinned": et.ACTIVE, "fb": et.ACTIVE, "none": et.DORMANT}
    assert "用户固定" in out["reasons"]["pinned"] and "用户留有修改意见" in out["reasons"]["fb"]


def test_due_pending_effect_pointing_at_an_element_makes_it_active():
    pending = [{"pending_id": "e1@1", "edge_id": "e1", "from_line_id": "src", "to_line_id": "a",
                "triggered_at_step": 1, "delay_steps": 2, "delay_days": None}]
    s = _settings(_quiet_branches(_el("a")), _quiet_branches(_el("b")),
                  causal_engine_enabled=True, causal_pending=pending)
    out = _tiers(s, step=30)
    assert out["tiers"] == {"a": et.ACTIVE, "b": et.DORMANT}
    assert out["reasons"]["a"] == ["有到期的待兑现因果"]
    # 因果引擎没开：待兑现项不参与分级
    assert _tiers({**s, "causal_engine_enabled": False}, step=30)["tiers"]["a"] == et.DORMANT
    # 还没到期
    early = [{**pending[0], "delay_steps": 100}]
    assert _tiers({**s, "causal_pending": early}, step=30)["tiers"]["a"] == et.DORMANT


def test_event_hits_activate_elements_and_domains_expand_to_alive_children():
    s = _settings(_quiet_branches(_el("a", parent="tech")), _quiet_branches(_el("b", parent="tech")),
                  _quiet_branches(_el("c", parent="other")), _quiet_branches(_el("r", parent="tech", status="retired")))
    out = _tiers(s, step=40, hit_refs=["a"])
    assert out["tiers"] == {"a": et.ACTIVE, "b": et.DORMANT, "c": et.DORMANT}
    out = _tiers(s, step=40, hit_refs=["tech"])                  # 领域 id → 其下 alive 元素
    assert out["tiers"]["a"] == et.ACTIVE and out["tiers"]["b"] == et.ACTIVE and out["tiers"]["c"] == et.DORMANT
    assert "r" not in out["tiers"]
    assert out["reasons"]["a"] == ["被本步事件命中"]
    assert _tiers(s, step=40, hit_refs=["nope", None, ""])["tiers"]["a"] == et.DORMANT


def test_active_element_triggers_its_edge_targets_exactly_one_hop():
    edges = [{"id": "e1", "from_line_id": "a", "to_line_id": "b"}, {"id": "e2", "from_line_id": "b", "to_line_id": "c"}]
    s = _settings(_quiet_branches(_el("a", born_step=39)), _quiet_branches(_el("b")), _quiet_branches(_el("c")),
                  _quiet_branches(_el("d")), declared_causal_graph=edges)
    out = _tiers(s, step=40)
    assert out["tiers"] == {"a": et.ACTIVE, "b": et.ACTIVE, "c": et.DORMANT, "d": et.DORMANT}   # c 是两跳，不算
    assert out["reasons"]["b"] == ["被活跃元素经因果边触发"]
    off = [{**edges[0], "enabled": False}, edges[1]]
    assert _tiers({**s, "declared_causal_graph": off}, step=40)["tiers"]["b"] == et.DORMANT


def test_edges_derived_from_element_relations_also_propagate():
    a = _quiet_branches(_el("a", born_step=39, relations=[{"with": "b", "direction": "affects", "sign": "positive"}]))
    s = _settings(a, _quiet_branches(_el("b")))
    assert _tiers(s, step=40)["tiers"]["b"] == et.ACTIVE


def test_windows_scale_with_the_lines_own_pace():
    slow = _quiet_branches(_el("slow", advance_every_n_steps=5))
    fast = _quiet_branches(_el("fast"))
    h = [_st(10, {"slow": {}, "fast": {}})]
    tiers = _tiers(_settings(slow, fast), h, step=20)["tiers"]            # 两者年龄都是 10
    assert tiers["fast"] == et.WATCH                                       # 窗口 3/10：过了 active、没过 watch
    assert tiers["slow"] == et.ACTIVE                                      # 窗口 ×5：active 窗口 15
    assert _tiers(_settings(slow, fast), h, step=30)["tiers"]["slow"] == et.WATCH     # 年龄 20：过了 15、没过 50
    assert _tiers(_settings(slow, fast), h, step=70)["tiers"]["slow"] == et.DORMANT   # 年龄 60 > 50


def test_history_scan_is_bounded_by_the_watch_window():
    s = _settings(_quiet_branches(_el("a")))
    # 很久以前的进展（超出扫描范围）不影响结果，也不会因为历史很长而报错
    h = [_st(i, {"a": {}}) for i in range(0, 5)] + [_st(i) for i in range(5, 60)]
    assert _tiers(s, h, step=60)["tiers"]["a"] == et.DORMANT


# ── 预算 ─────────────────────────────────────────────────────────────


def _crowd(n, **extra):
    lines = [_quiet_branches(_el(f"e{i:02d}", born_step=39, **extra)) for i in range(n)]
    return _settings(*lines, element_params=extra.pop("params", {}))


def test_budget_demotes_overflow_to_watch_not_dormant():
    s = _settings(*[_quiet_branches(_el(f"e{i:02d}", born_step=39)) for i in range(15)])
    out = _tiers(s, step=40)
    assert et.counts(out) == {et.ACTIVE: 12, et.WATCH: 3, et.DORMANT: 0}
    assert len(out["demoted"]) == 3 and all(out["tiers"][i] == et.WATCH for i in out["demoted"])
    assert "超出 active 预算，降为 watch" in out["reasons"][out["demoted"][0]]


def test_budget_null_means_everything_active_and_number_is_respected():
    lines = [_quiet_branches(_el(f"e{i:02d}", born_step=39)) for i in range(15)]
    unlimited = _settings(*lines, element_params={"max_active_in_prompt": None})
    assert et.counts(_tiers(unlimited, step=40)) == {et.ACTIVE: 15, et.WATCH: 0, et.DORMANT: 0}
    three = _settings(*lines, element_params={"max_active_in_prompt": 3})
    assert et.counts(_tiers(three, step=40))[et.ACTIVE] == 3
    zero = _settings(*lines, element_params={"max_active_in_prompt": 0})
    assert et.counts(_tiers(zero, step=40)) == {et.ACTIVE: 0, et.WATCH: 15, et.DORMANT: 0}


def test_budget_ranking_pressure_then_live_branch_then_feedback_then_recency_then_indegree():
    pending = [{"pending_id": "p@1", "edge_id": "p", "from_line_id": "x", "to_line_id": "due",
                "triggered_at_step": 1, "delay_steps": 1, "delay_days": None}]
    live = _quiet_branches(_el("live", born_step=0))
    live["future_tree"]["branches"][0]["status"] = "active"
    lines = [
        _quiet_branches(_el("recent", born_step=39)),                         # 年龄 1
        _quiet_branches(_el("older", born_step=38)),                          # 年龄 2
        _quiet_branches(_el("fb", born_step=0, user_feedback="注意")),
        live,
        _quiet_branches(_el("due", born_step=0)),
    ]
    s = _settings(*lines, causal_engine_enabled=True, causal_pending=pending,
                  element_params={"max_active_in_prompt": 5})
    assert _tiers(s, step=40)["rank"] == ["due", "live", "fb", "recent", "older"]
    s2 = {**s, "element_params": {"max_active_in_prompt": 2}}
    out = _tiers(s2, step=40)
    assert out["rank"] == ["due", "live"] and set(out["demoted"]) == {"fb", "recent", "older"}


def test_budget_ties_are_broken_by_edge_in_degree():
    edges = [{"id": "e1", "from_line_id": "hub_src", "to_line_id": "popular"}]
    s = _settings(_quiet_branches(_el("lonely", born_step=39)), _quiet_branches(_el("popular", born_step=39)),
                  _quiet_branches(_el("hub_src", born_step=0)), declared_causal_graph=edges,
                  element_params={"max_active_in_prompt": 1})
    assert _tiers(s, step=40)["rank"] == ["popular"]


def test_pinned_elements_are_exempt_from_the_budget_and_use_up_its_room():
    lines = [_quiet_branches(_el(f"e{i:02d}", born_step=39)) for i in range(6)]
    for line in lines[:3]:
        line["tier_pin"] = "active"
    s = _settings(*lines, element_params={"max_active_in_prompt": 2})
    out = _tiers(s, step=40)
    assert out["rank"] == ["e00", "e01", "e02"]               # 钉住的 3 个全留（超出预算也不裁），其余名额为 0
    assert set(out["demoted"]) == {"e03", "e04", "e05"}


def test_derive_tiers_is_pure_and_deterministic():
    s = _settings(*[_quiet_branches(_el(f"e{i}", born_step=39)) for i in range(5)])
    before = copy.deepcopy(s)
    h = [_st(39, {"e1": {}})]
    assert _tiers(s, h, step=40) == _tiers(s, h, step=40)
    assert s == before


# ── 分支正确 ─────────────────────────────────────────────────────────


def test_two_branches_with_different_history_derive_different_tiers():
    s = _settings(_quiet_branches(_el("a")), _quiet_branches(_el("b")))
    main_hist = [_st(18, {"a": {}})]
    fork_hist = [_st(18, {"b": {}})]
    assert _tiers(s, main_hist, step=20)["tiers"] == {"a": et.ACTIVE, "b": et.DORMANT}
    assert _tiers(s, fork_hist, step=20)["tiers"] == {"a": et.DORMANT, "b": et.ACTIVE}


# ── prompt 渲染 ──────────────────────────────────────────────────────


def _hint(settings, history=(), step=20, current_vars=None, hit_refs=None):
    return spec_generator._resolve_causal_lines_hint(
        settings, stage="advance", current_step=step, history=list(history),
        current_vars=current_vars, hit_refs=hit_refs)


def test_active_element_renders_type_domain_lifecycle_var_refs_and_only_live_branches():
    line = _el("gpu", "GPU 供给", element_type="technology", parent="tech", time_granularity="按季度",
               var_refs=["vars.cash", "econ.rate", "missing.path", "vars.items.1"],
               lifecycle={"stage": "developer", "progress": 0.4})
    line["future_tree"]["branches"] = [
        {"id": "b1", "description": "继续扩产", "likelihood": "high", "status": "emerging"},
        {"id": "b2", "description": "停滞", "likelihood": "low", "status": "dormant"},
        {"id": "b3", "description": "已兑现", "likelihood": "low", "status": "resolved"},
        {"id": "b4", "description": "已失效", "likelihood": "low", "status": "invalidated"},
    ]
    out = _hint(_settings(line), step=2, current_vars={"cash": 120, "econ": {"rate": "5%"}, "items": ["x", "y"]})
    assert "gpu（GPU 供给，类型：technology，领域：tech，发展阶段：developer（进度 0.4），节奏参考：按季度" in out
    assert "vars.cash=120" in out and "econ.rate=5%" in out and "vars.items.1=y" in out and "missing.path" not in out
    assert "b1[emerging]：继续扩产" in out and "b2[dormant]：停滞" in out
    assert "b3" not in out and "b4" not in out and "另有 2 个已终态分支" in out


def test_var_refs_text_handles_non_dict_vars_long_values_and_limit():
    line = {"var_refs": ["a", "b", "c", "d"]}
    assert et.var_refs_text(line, None) == "" and et.var_refs_text(line, "x") == ""
    long = et.var_refs_text({"var_refs": ["a"]}, {"a": "x" * 100})
    assert long.endswith("…") and len(long) < 60
    assert et.var_refs_text(line, {"a": 1, "b": 2, "c": 3, "d": 4}).count("=") == 3   # 默认最多 3 条


def test_domain_lines_are_always_rendered_in_full():
    out = _hint(_settings(_quiet_branches(_el("a"))), step=50)
    assert "tech（技术，领域线）" in out and "tech 当前未来分支" in out


def test_watch_and_dormant_sections_and_non_blocking_note():
    s = _settings(_quiet_branches(_el("act", element_type="project", born_step=19)),
                  _quiet_branches(_el("wat", "观察对象", element_type="market", parent="tech")),
                  _quiet_branches(_el("dor", "休眠对象")))
    h = [_st(10, {"wat": {}}), _st(2, {"dor": {}})]
    out = _hint(s, h, step=20)
    assert "近期少动的元素（一行摘要）：wat（观察对象；market，领域 tech，10 步前有动静）" in out
    assert "休眠元素索引" in out and "dor（休眠对象）" in out
    assert "它们同样可以更新" in out
    # active 元素的分支只在 active 里展开
    assert "act 当前未来分支" in out and "wat 当前未来分支" not in out and "dor 当前未来分支" not in out


def test_no_tier_sections_when_everything_is_active():
    out = _hint(_settings(_el("a", born_step=19)), step=20)
    assert "近期少动" not in out and "休眠元素索引" not in out and "它们同样可以更新" not in out


def test_dormant_index_is_capped_most_recent_first_with_hidden_count():
    lines = [_quiet_branches(_el(f"d{i:03d}")) for i in range(30)]
    s = _settings(*lines, element_params={"dormant_index_max": 5})
    h = [_st(6, {"d007": {}}), _st(5, {"d003": {}})]        # 这两个最近一次有动静（但都已休眠）
    out = _hint(s, h, step=60)
    shown = out.split("休眠元素索引")[1]
    assert "d007（d007）、d003（d003）" in shown
    assert shown.count("（d0") == 5 and "另有 25 个更久没有动静的休眠元素未列出" in shown
    zero = _hint(_settings(*lines, element_params={"dormant_index_max": 0}), h, step=60)
    assert "未列出" in zero and "另有 30 个" in zero


def test_retired_and_merged_elements_never_appear_in_the_prompt():
    s = _settings(_el("live", born_step=19), _el("old", status="retired"), _el("gone", status="merged", merged_into="live"))
    out = _hint(s, step=20)
    assert "old" not in out and "gone" not in out and "live" in out


def test_hit_refs_expand_a_dormant_element_in_the_rendered_prompt():
    s = _settings(_quiet_branches(_el("target", "被命中")), _quiet_branches(_el("other")))
    assert "target 当前未来分支" not in _hint(s, step=60)
    assert "target 当前未来分支" in _hint(s, step=60, hit_refs=["target"])


def test_due_and_stale_hints_follow_the_tiers():
    quiet = _quiet_branches(_el("quiet_slow", advance_every_n_steps=5))
    s = _settings(quiet, _quiet_branches(_el("act", advance_every_n_steps=5, born_step=99)))
    out = _hint(s, step=100)                                   # 100 % 5 == 0：到点；quiet_slow 年龄 100 > 50 → 休眠
    assert "act（act，约每 5 步一动）" in out
    assert "quiet_slow（quiet_slow，约每 5 步一动）" not in out   # 休眠元素不进"预期有动静"清单
    # 陈旧分支建议也只针对领域线与 active 元素
    assert "tech/" in out and "quiet_slow/" not in out
    watch = _settings(_quiet_branches(_el("w", advance_every_n_steps=5)))
    assert "w（w，约每 5 步一动）" in _hint(watch, step=40)    # 年龄 40 ≤ 50：watch，仍在清单里


def test_all_elements_retired_falls_back_to_the_no_lines_message():
    s = _settings(_el("old", status="retired"))
    s["causal_lines"] = [x for x in s["causal_lines"] if x.get("kind") != "domain"]
    assert "不需要用户提前声明" in _hint(s, step=20)


# ── 元素模式关闭：逐字节不变 ─────────────────────────────────────────


def test_legacy_instances_render_exactly_as_before_even_with_the_new_arguments():
    legacy = {"causal_lines": [_line("main_line", "主线"), _line("side", "支线", advance_every_n_steps=2)]}
    base = spec_generator._resolve_causal_lines_hint(legacy, stage="advance", current_step=6)
    withargs = _hint(legacy, history=[_st(5, {"main_line": {}})], step=6,
                     current_vars={"a": 1}, hit_refs=["side"])
    assert base == withargs
    assert "main_line 当前未来分支" in base and "side 当前未来分支" in base
    assert "近期少动" not in base and "休眠元素索引" not in base


def test_legacy_lines_in_a_non_element_instance_ignore_element_fields():
    legacy = {"causal_lines": [_line("x", "X", kind="element", status="retired")]}
    assert "x 当前未来分支" in _hint(legacy, step=20)          # 没开元素模式：不过滤、不分级


def test_create_stage_is_untouched_by_tiering():
    s = _settings(_el("a"))
    out = spec_generator._resolve_causal_lines_hint(s, stage="create")
    assert "近期少动" not in out and "休眠元素索引" not in out


# ── element_hint 与分级展示的分工 ────────────────────────────────────


def test_build_hint_without_history_keeps_the_e3_index_and_with_history_points_to_the_tiered_listing():
    s = _settings(_el("gpu_supply", "GPU 供给", origin="seed", profile_status="complete"))
    legacy_style = er.build_hint(s, step=5)
    assert "已登记元素（复用这些 id，不要重复发现）：gpu_supply" in legacy_style
    tiered_style = er.build_hint(s, step=5, history=[])
    assert "gpu_supply" not in tiered_style.split("可用的领域线")[1]
    assert "已登记元素的 id 都列在上面的\"因果线设置\"里" in tiered_style
    assert er.safe_build_hint(s, step=5, history=[]) == tiered_style
    assert er.build_hint(_settings(), step=5, history=[]) == er.build_hint(_settings(), step=5)   # 没有元素：无差别


def test_element_hint_still_lists_pending_enrichment_and_candidates_when_history_is_passed():
    pend = _el("stub", "桩", origin="discovered", profile_status="pending_enrichment", born_step=4)
    s = _settings(pend)
    assert "待补全元素" in er.build_hint(s, step=5, history=[])


# ── prompt 规模 ──────────────────────────────────────────────────────


def _scale_settings(n, hot=12):
    lines = []
    for i in range(n):
        lines.append(_quiet_branches(_el(f"el{i:03d}", f"元素{i}", element_type="project", parent="tech")))
    return _settings(*lines), [f"el{i:03d}" for i in range(hot)]


def _scale_hint(n, hot=12):
    s, hot_ids = _scale_settings(n, hot)
    h = [_st(48, {k: {} for k in hot_ids})]                  # 这 hot 个刚有动静 → 全是 active 候选
    return _hint(s, h, step=50), s


def test_prompt_growth_from_10_to_100_elements_only_comes_from_watch_summaries_and_the_capped_index():
    small, _ = _scale_hint(10)
    big, _ = _scale_hint(100)
    # active 完整展开的数量固定：10 个元素时 10 个全展开，100 个时只展开 12 个（再加 1 条领域线）
    assert small.count(" 当前未来分支") == 1 + 10
    assert big.count(" 当前未来分支") == 1 + 12
    # 10 个元素没有休眠索引；100 个：12 个 active、88 个休眠，索引上限 60
    assert "休眠元素索引" not in small and "休眠元素索引" in big
    index = big.split("休眠元素索引")[1]
    assert index.count("（元素") == 60 and "另有 28 个更久没有动静的休眠元素未列出" in index
    # 索引封顶之后，再增加元素 prompt 基本不再增长（只多"另有 N 个"里的位数）
    sizes = {n: len(_scale_hint(n)[0]) for n in (80, 160, 320, 1000)}
    assert sizes[1000] - sizes[80] < 40
    # 而 100 个元素的 prompt 比"全部展开"的朴素做法小得多
    naive = len(_hint(_settings(*[_quiet_branches(_el(f"el{i:03d}", f"元素{i}", parent="tech")) for i in range(100)],
                                element_params={"max_active_in_prompt": None, "active_window_steps": 100,
                                                "watch_window_steps": 100}), [], step=50))
    assert len(big) < naive / 3


def test_with_watch_elements_growth_is_one_line_per_watch_element_and_active_stays_fixed():
    # 把"近期动过"的元素放大到 40 个：active 固定 12，其余 28 个降为 watch（一行摘要）
    s, _ = _scale_settings(40, hot=40)
    h = [_st(48, {f"el{i:03d}": {} for i in range(40)})]
    out = _hint(s, h, step=50)
    assert out.count(" 当前未来分支") == 13                         # 领域 + 12 个 active
    watch_part = out.split("近期少动的元素（一行摘要）：")[1].split("\n")[0]
    assert watch_part.count("2 步前有动静") == 28
    assert "休眠元素索引" not in out


def test_active_budget_null_expands_everything():
    s, _ = _scale_settings(30, hot=30)
    s["element_params"] = {"max_active_in_prompt": None}
    h = [_st(48, {f"el{i:03d}": {} for i in range(30)})]
    out = _hint(s, h, step=50)
    assert out.count(" 当前未来分支") == 31 and "近期少动" not in out


# ── 引擎端到端 ───────────────────────────────────────────────────────


def _sim_settings(**extra):
    s = _settings(
        _quiet_branches(_el("gpu_supply", "GPU 供给", element_type="technology", parent="tech",
                            origin="seed", profile_status="complete")),
        _quiet_branches(_el("model_v2", "模型 V2", element_type="project", parent="tech",
                            origin="seed", profile_status="complete")),
        element_params={"active_window_steps": 1, "watch_window_steps": 1},
    )
    s.update(extra)
    return s


def _lines(data_dir):
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    return {x["id"]: x for x in manifest.settings["causal_lines"]}


def test_advance_prompt_is_tiered_and_updating_a_dormant_element_is_not_blocked(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, _sim_settings())
    # 前两步都只更新 model_v2；gpu_supply 一直没动静
    for i in (1, 2):
        cap: dict = {}
        _adv(monkeypatch, tmp_path, data_dir, _default_payload(i, line_updates={"model_v2": {"summary": "推进"}}), cap)
    cap = {}
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(3, line_updates={"model_v2": {"summary": "推进"}}), cap)
    hint = cap["inputs_list"][0]["causal_lines_hint"]
    assert "model_v2 当前未来分支" in hint and "gpu_supply 当前未来分支" not in hint   # 休眠元素没有展开
    assert "gpu_supply（GPU 供给）" in hint and "休眠元素索引" in hint                  # 但索引里有
    assert "gpu_supply" not in cap["inputs_list"][0]["element_hint"].split("可用的领域线")[1]

    # LLM 仍然可以更新休眠元素——引擎照常接受
    cap = {}
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(4, line_updates={"gpu_supply": {"summary": "突发进展"}}), cap)
    assert "gpu_supply" in state.line_updates
    assert store.load_history("main")[-1].line_updates["gpu_supply"]["summary"] == "突发进展"

    # 下一步它被重新展开
    cap = {}
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(5), cap)
    assert "gpu_supply 当前未来分支" in cap["inputs_list"][0]["causal_lines_hint"]


def test_advance_passes_current_vars_into_the_prompt(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    settings = _sim_settings()
    for line in settings["causal_lines"]:
        if line["id"] == "model_v2":
            line["var_refs"] = ["vars.age"]
    _make_sim(data_dir, settings)
    cap: dict = {}
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1), cap)
    assert "对应状态：vars.age=20" in cap["inputs_list"][0]["causal_lines_hint"]    # 当前 vars 是 {"age": 20}


def test_advance_sampled_event_hits_expand_the_affected_dormant_element(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, _sim_settings())
    for i in (1, 2, 3):   # 只更新 model_v2，gpu_supply 休眠
        _adv(monkeypatch, tmp_path, data_dir, _default_payload(i, line_updates={"model_v2": {"summary": "推进"}}), {})
    cap: dict = {}
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(4, line_updates={"model_v2": {"summary": "推进"}}), cap)
    assert "gpu_supply 当前未来分支" not in cap["inputs_list"][0]["causal_lines_hint"]      # 没有事件：仍休眠

    manifest = store.load_manifest()
    manifest.settings["event_sampling_enabled"] = True
    manifest.settings["event_priors"] = [{"id": "export_ban", "description": "出口管制", "rate_per_year": 1e9,
                                          "affects": ["gpu_supply"]}]
    store.save_manifest(manifest)
    cap = {}
    state = _adv(monkeypatch, tmp_path, data_dir, _default_payload(5, line_updates={"model_v2": {"summary": "推进"}}), cap)
    assert [e["id"] for e in state.sampled_events] == ["export_ban"]
    assert "gpu_supply 当前未来分支" in cap["inputs_list"][0]["causal_lines_hint"]          # 被事件命中：本步展开


def test_advance_legacy_instance_prompt_has_no_tier_text(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, {"causal_lines": [_line("main_line", "主线")]})
    cap: dict = {}
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1), cap)
    hint = cap["inputs_list"][0]["causal_lines_hint"]
    assert "main_line 当前未来分支" in hint and "近期少动" not in hint and "休眠元素索引" not in hint
    assert cap["inputs_list"][0]["element_hint"] == ""


def test_forked_branch_gets_its_own_tiers(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _sim_settings())
    for i in (1, 2, 3):
        _adv(monkeypatch, tmp_path, data_dir, _default_payload(i, line_updates={"model_v2": {"summary": "推进"}}), {})
    manifest, current, history = get_simulation(data_dir, "sim1")
    # 主分支历史：model_v2 一直动 → active；gpu_supply 休眠
    main = et.derive_tiers(history, manifest.settings, step=current.step + 1)
    assert main["tiers"]["model_v2"] == et.ACTIVE and main["tiers"]["gpu_supply"] == et.DORMANT
    # 构造另一条历史（gpu_supply 一直动）：同一份设置得到相反的分级
    other = [_st(s.step, {"gpu_supply": {}}) for s in history]
    alt = et.derive_tiers(other, manifest.settings, step=current.step + 1)
    assert alt["tiers"]["gpu_supply"] == et.ACTIVE and alt["tiers"]["model_v2"] == et.DORMANT


def test_tier_derivation_failure_cannot_break_due_and_edge_helpers(monkeypatch):
    # causal_engine 读取出错 → 按"没有到期项/没有边"处理，分级仍可用
    from world_simulator import causal_engine

    monkeypatch.setattr(causal_engine, "get_edges", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(causal_engine, "due_entries", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    s = _settings(_quiet_branches(_el("a", born_step=39)), causal_engine_enabled=True,
                  causal_pending=[{"pending_id": "p", "to_line_id": "a", "triggered_at_step": 0}])
    assert _tiers(s, step=40)["tiers"]["a"] == et.ACTIVE
