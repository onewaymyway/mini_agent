"""tests/test_element_tech_adapter.py — 第二十三轮 E1：技术节点存取适配器的契约测试。

设计依据：`next_doc/world_simulator_element_causal_lines_plan.md` §4.2 / §6 E1。

核心契约：**同一组输入分别走旧存储（`settings.tech_state`）与元素存储
（`settings.causal_lines[].lifecycle`），`tech_model.apply_step()` 的审计、违规、以及
`get_nodes()` 读出来的节点必须完全一致**——技术裁决规则（R1–R7、T1–T10）一字不改，
只换了"节点存在哪"。另外验证：回滚点（修复调用用）、锚定判断、回测还原、分支回滚/隔离、
条件写法别名、旧实例（开关未写入）零变化。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import backtest
from world_simulator import branch_manager as bm
from world_simulator import dynamic_state, event_sampler
from world_simulator import element_registry as er
from world_simulator import tech_model as tm
from world_simulator.engine.management import get_simulation
from world_simulator.state_model import SimState

from test_fast_forward import _default_payload
from test_tech_model import _adv, _make_sim, _node, _settings


def _legacy(*nodes):
    return _settings(*nodes)


def _element(*nodes, extra_lines=()):
    s = {
        "tech_model_enabled": True,
        "element_modeling_enabled": True,
        "causal_lines": [{"id": "main_line", "label": "主线"}, *extra_lines],
        "tech_state": {"nodes": list(nodes)},
    }
    er.fold_legacy_tech_state(s)
    return s


def _both(*nodes):
    # 种子先规整一遍（设置页保存时就是这样做的）：未规整的旧种子在被读时 `created_step` 会取"当前读的
    # step"，这是旧存储的既有行为，与存取层无关，不在契约比较范围内。
    nodes = [tm.normalize_node(n, step=0) for n in nodes]
    return _legacy(*copy.deepcopy(nodes)), _element(*copy.deepcopy(nodes))


# 覆盖：进度推进、驳回过早迁移、批准迁移、新节点登记、硬前置阻塞、回退、重复/无 id 提议
_SCRIPT = [
    (100, [{"id": "ai", "stage": "expert"}]),                                        # 进度满 → 迁移被批准或驳回
    (30, [{"id": "chip", "name": "芯片", "stage": "lab"}, {"id": "ai", "stage": "developer"}]),
    (10, [{"id": "ai", "stage": "expert", "reason": "x"}, {"name": ""}]),
    (200, [{"id": "chip", "stage": "expert"}, {"id": "ai", "stage": "lab", "reason": "回退"}]),
    (5, [{"id": "new_one", "stage": "developer", "preexisting": True}]),
    (None, []),
]


def _seed_nodes():
    ai = _node("ai", dwell=100)
    chip_req = _node("base", dwell=100)
    return ai, chip_req


@pytest.mark.parametrize("withheld_requires", [False, True])
def test_apply_step_contract_legacy_vs_element(withheld_requires):
    ai, base = _seed_nodes()
    if withheld_requires:
        ai = _node("ai", dwell=100, requires=[{"tech_id": "base", "min_stage": "developer", "mode": "hard"}])
    legacy, element = _both(ai, base)
    assert [n["id"] for n in tm.get_nodes(legacy)] == [n["id"] for n in tm.get_nodes(element)]
    for step, (days, proposals) in enumerate(_SCRIPT, start=1):
        a1, v1 = tm.apply_step(legacy, copy.deepcopy(proposals), step=step, elapsed_days_raw=days)
        a2, v2 = tm.apply_step(element, copy.deepcopy(proposals), step=step, elapsed_days_raw=days)
        assert a1 == a2, f"step {step} audit differs"
        assert v1 == v2, f"step {step} violations differ"
        assert tm.get_nodes(legacy, step=step) == tm.get_nodes(element, step=step), f"step {step} nodes differ"
        assert tm.summarize(legacy) == tm.summarize(element)
        assert tm.build_hint(legacy) == tm.build_hint(element)


def test_element_mode_never_writes_tech_state_and_legacy_mode_never_touches_causal_lines():
    legacy, element = _both(_node("ai"))
    lines_before = copy.deepcopy(legacy.get("causal_lines"))
    tm.apply_step(legacy, [{"id": "chip", "stage": "lab"}], step=1, elapsed_days_raw=10)
    tm.apply_step(element, [{"id": "chip", "stage": "lab"}], step=1, elapsed_days_raw=10)
    assert "tech_state" in legacy and legacy.get("causal_lines") == lines_before
    assert "tech_state" not in element
    ids = [x["id"] for x in element["causal_lines"]]
    assert ids == ["main_line", "ai", "chip"]
    chip = element["causal_lines"][-1]
    assert chip["origin"] == "discovered" and chip["profile_status"] == "pending_enrichment" and chip["born_step"] == 1


def test_legacy_instance_without_switch_is_unchanged():
    s = _legacy(_node("ai"))
    assert not er.is_enabled(s)
    tm.apply_step(s, [{"id": "ai", "stage": "developer"}], step=1, elapsed_days_raw=100)
    assert set(s) == {"tech_model_enabled", "tech_state"}  # 没有冒出 causal_lines / 别的 key


def test_safe_apply_step_error_leaves_element_settings_untouched(monkeypatch):
    s = _element(_node("ai"))
    before = copy.deepcopy(s)

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(tm, "node_status", boom)
    audit, violations = tm.safe_apply_step(s, [{"id": "ai", "stage": "developer"}], step=1, elapsed_days_raw=10)
    assert audit == [] and violations[0]["code"] == "T0"
    assert s == before


# ── 回滚点（修复调用） ───────────────────────────────────────────────


def test_capture_restore_roundtrip_both_modes_and_drops_lines_registered_in_between():
    for settings in _both(_node("ai")):
        mode_element = er.is_enabled(settings)
        captured = tm.capture_storage(settings)
        before_nodes = tm.get_nodes(settings)
        tm.apply_step(settings, [{"id": "chip", "stage": "lab"}, {"id": "ai", "stage": "developer"}],
                      step=1, elapsed_days_raw=100)
        assert len(tm.get_nodes(settings)) == 2
        tm.restore_storage(settings, captured)
        assert tm.get_nodes(settings) == before_nodes
        if mode_element:
            assert [x["id"] for x in settings["causal_lines"]] == ["main_line", "ai"]  # 新建的 chip 线被撤销


def test_restore_keeps_unrelated_lines_added_in_between():
    s = _element(_node("ai"))
    captured = tm.capture_storage(s)
    s["causal_lines"] = s["causal_lines"] + [{"id": "plain_new", "label": "别的线"}]
    tm.restore_storage(s, captured)
    assert "plain_new" in [x["id"] for x in s["causal_lines"]]


def test_restore_accepts_legacy_raw_tech_state_and_none():
    s = _legacy(_node("ai"))
    raw = copy.deepcopy(s["tech_state"])
    tm.apply_step(s, [{"id": "ai", "stage": "developer"}], step=1, elapsed_days_raw=100)
    tm.restore_storage(s, raw)
    assert s["tech_state"] == raw
    tm.restore_storage(s, None)
    assert "tech_state" not in s


def test_settings_with_storage_does_not_mutate_input():
    s = _element(_node("ai"))
    captured = tm.capture_storage(s)
    tm.apply_step(s, [{"id": "chip", "stage": "lab"}], step=1, elapsed_days_raw=10)
    after = copy.deepcopy(s)
    view = tm.settings_with_storage(s, captured)
    assert s == after
    assert [n["id"] for n in tm.get_nodes(view)] == ["ai"]


# ── 锚定判断 / 快照 ──────────────────────────────────────────────────


def test_storage_and_snapshot_presence_checks():
    legacy, element = _both(_node("ai"))
    assert tm.storage_has_nodes(legacy) and tm.storage_has_nodes(element)
    assert not tm.storage_has_nodes({"tech_model_enabled": True})
    assert not tm.storage_has_nodes({"element_modeling_enabled": True, "causal_lines": [{"id": "a"}]})
    # 旧模式：只认 "tech_state" 键（与以前一致）
    assert tm.snapshot_has_nodes({"tech_state": {}}, legacy) and not tm.snapshot_has_nodes({"causal_lines": []}, legacy)
    # 元素模式：旧形态与新形态快照都算
    assert tm.snapshot_has_nodes(dynamic_state.extract(element), element)
    assert tm.snapshot_has_nodes({"tech_state": {"nodes": []}}, element)
    assert not tm.snapshot_has_nodes({"causal_lines": [{"id": "main_line"}]}, element)
    assert not tm.snapshot_has_nodes(None, element)


def test_element_snapshot_contains_lifecycle_and_no_tech_state():
    element = _element(_node("ai"))
    snap = dynamic_state.extract(element)
    assert "tech_state" not in snap
    assert any(isinstance(x.get("lifecycle"), dict) for x in snap["causal_lines"])


def test_apply_to_settings_folds_old_form_snapshot_only_in_element_mode():
    old_snapshot = {"causal_lines": [{"id": "main_line", "label": "主线"}],
                    "tech_state": {"nodes": [_node("ai")]}}
    folded = dynamic_state.apply_to_settings({"element_modeling_enabled": True}, old_snapshot)
    assert "tech_state" not in folded and [n["id"] for n in tm.get_nodes(folded)] == ["ai"]
    assert "tech_state" in old_snapshot  # 快照本身不被改写（分支互不污染）
    plain = dynamic_state.apply_to_settings({}, old_snapshot)
    assert "tech_state" in plain  # 旧实例原样


def test_element_candidates_is_a_dynamic_key():
    assert "element_candidates" in dynamic_state.DYNAMIC_KEYS
    assert dynamic_state.extract({"element_candidates": []}) == {}  # 空值不产生快照


# ── 回测还原 ─────────────────────────────────────────────────────────


def _history_with(snapshots):
    states = []
    for step, snap in snapshots:
        st = SimState(step=step, summary="s", vars={}, options=[])
        st.dynamic_snapshot = snap
        states.append(st)
    return states


def test_backtest_extract_tech_nodes_is_identical_for_both_snapshot_forms():
    def build(mode):
        s = _legacy(_node("ai", dwell=100)) if mode == "legacy" else _element(_node("ai", dwell=100))
        snaps = [(0, dynamic_state.extract(s))]
        for step, (days, props) in enumerate([(100, [{"id": "ai", "stage": "developer"}]),
                                              (50, [{"id": "chip", "stage": "lab"}])], start=1):
            tm.apply_step(s, props, step=step, elapsed_days_raw=days)
            snaps.append((step, dynamic_state.extract(s)))
        return backtest.extract_tech_nodes(_history_with(snaps), start_year=2020.0, years_per_step=1.0)

    legacy_nodes, element_nodes = build("legacy"), build("element")
    assert legacy_nodes and legacy_nodes == element_nodes


def test_backtest_without_any_tech_snapshot_returns_empty():
    hist = _history_with([(0, None), (1, {"causal_lines": [{"id": "main_line"}]})])
    assert backtest.extract_tech_nodes(hist, start_year=2020.0, years_per_step=1.0) == []


# ── 条件写法别名 ─────────────────────────────────────────────────────


def test_condition_element_key_is_alias_of_tech_key_in_both_modes():
    for settings in _both(_node("ai", stage="lab")):
        ok_t, _ = event_sampler.evaluate_condition({"tech": "ai", "min_stage": "lab"}, {}, settings)
        ok_e, _ = event_sampler.evaluate_condition({"element": "ai", "min_stage": "lab"}, {}, settings)
        assert ok_t and ok_e
        ok_hi, why = event_sampler.evaluate_condition({"element": "ai", "min_stage": "developer"}, {}, settings)
        assert not ok_hi and "ai" in why
        ok_miss, why = event_sampler.evaluate_condition({"element": "nope", "min_stage": "lab"}, {}, settings)
        assert not ok_miss and "未登记" in why
    both_keys, _ = event_sampler.evaluate_condition({"tech": "ai", "element": "x", "min_stage": "lab"}, {},
                                                    _element(_node("ai")))
    assert both_keys  # 两者都写时以 tech 为准


def test_element_lifecycle_enabled_is_alias_of_tech_model_enabled():
    assert tm.is_enabled({"element_lifecycle_enabled": True}) is True
    assert tm.is_enabled({"tech_model_enabled": True}) is True
    assert tm.is_enabled({}) is False


# ── replace_nodes（设置页保存） ──────────────────────────────────────


def test_replace_nodes_writes_to_current_storage_in_both_modes():
    raw = [{"id": "ai", "name": "AI", "stage": "developer"}, {"name": "仅有名字"}, {"stage": "lab"}]
    legacy = {"tech_model_enabled": True}
    tm.replace_nodes(legacy, raw)
    assert [n["id"] for n in legacy["tech_state"]["nodes"]] == ["ai", "仅有名字"]
    element = {"element_modeling_enabled": True, "causal_lines": [{"id": "main_line"}]}
    tm.replace_nodes(element, raw)
    assert "tech_state" not in element
    assert [n["id"] for n in tm.get_nodes(element)] == ["ai", "仅有名字"]
    tm.replace_nodes(element, [])
    assert tm.get_nodes(element) == [] and [x["id"] for x in element["causal_lines"]][0] == "main_line"


# ── 端到端：advance + 分支 ───────────────────────────────────────────


def _stage_progress(data_dir, tid="ai"):
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    n = next(n for n in tm.get_nodes(manifest.settings) if n["id"] == tid)
    return n["stage"], round(n["progress"], 3)


@pytest.mark.parametrize("seed_form", ["legacy_seed_with_switch", "prefolded_seed"])
def test_advance_persists_lifecycle_snapshots_and_forks_roll_back(tmp_path, monkeypatch, seed_form):
    data_dir = tmp_path / "data"
    if seed_form == "legacy_seed_with_switch":
        # 旧形态种子（tech_state）+ 开关已开：读路径回退 + 锚定 + 写入时折叠，必须都走得通
        settings = {"tech_model_enabled": True, "element_modeling_enabled": True,
                    "tech_state": {"nodes": [_node("ai", dwell=100)]}}
    else:
        settings = _element(_node("ai", dwell=100))
    store = _make_sim(data_dir, settings)

    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=30))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, elapsed_days=30))
    assert _stage_progress(data_dir) == ("lab", 0.6)

    head = store.load_history("main")[-1]
    assert "tech_state" not in head.dynamic_snapshot  # 新写入的快照是元素形态
    assert tm.snapshot_has_nodes(head.dynamic_snapshot, {"element_modeling_enabled": True})
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert "tech_state" not in manifest.settings

    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1)
    assert _stage_progress(data_dir) == ("lab", 0.3)  # 回滚到分叉那一刻

    _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        2, elapsed_days=70, tech_updates=[{"id": "ai", "stage": "expert"}]))
    assert _stage_progress(data_dir) == ("expert", 0.0)

    bm.switch_branch(data_dir, "sim1", "main")
    assert _stage_progress(data_dir) == ("lab", 0.6)  # 主线没被污染
    bm.switch_branch(data_dir, "sim1", new_branch)
    assert _stage_progress(data_dir) == ("expert", 0.0)


def test_fork_from_before_first_advance_rolls_back_to_seed_in_element_mode(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, _element(_node("ai", dwell=100)))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=50))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(2, elapsed_days=50))
    assert _stage_progress(data_dir) == ("lab", 1.0)
    head0 = store.load_history("main")[0]
    assert head0.dynamic_snapshot and tm.snapshot_has_nodes(head0.dynamic_snapshot, {"element_modeling_enabled": True})

    bm.fork_branch(data_dir, "sim1", from_step=0)
    assert _stage_progress(data_dir) == ("lab", 0.0)
    bm.switch_branch(data_dir, "sim1", "main")
    assert _stage_progress(data_dir) == ("lab", 1.0)


def test_new_tech_registered_during_advance_creates_element_line_and_branches_do_not_share_it(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, _element(_node("ai", dwell=100)))
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(1, elapsed_days=10))
    branch = bm.fork_branch(data_dir, "sim1", from_step=1)
    _adv(monkeypatch, tmp_path, data_dir, _default_payload(
        2, elapsed_days=10, tech_updates=[{"id": "chip", "name": "芯片", "stage": "lab"}]))
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert "chip" in [x["id"] for x in manifest.settings["causal_lines"]]

    bm.switch_branch(data_dir, "sim1", "main")
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert "chip" not in [x["id"] for x in manifest.settings["causal_lines"]]  # 主线看不到分支上登记的元素
    bm.switch_branch(data_dir, "sim1", branch)
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert "chip" in [x["id"] for x in manifest.settings["causal_lines"]]


def test_repair_rollback_path_works_with_element_storage(tmp_path, monkeypatch):
    """修复调用的回滚点在元素存储下可用：直接驱动 `safe_repair_tech_step` 的恢复逻辑。"""
    from world_simulator.engine import tech_repair as tr

    s = _element(_node("ai", dwell=100))
    pre = tm.capture_storage(s)
    tm.apply_step(s, [{"id": "chip", "stage": "lab"}], step=1, elapsed_days_raw=10)
    post = tm.capture_storage(s)
    tr._restore(s, pre)
    assert [n["id"] for n in tm.get_nodes(s)] == ["ai"]
    tr._restore(s, post)
    assert [n["id"] for n in tm.get_nodes(s)] == ["ai", "chip"]


# ── 端到端：修复调用（opt-in）在元素存储下的回滚/重裁 ─────────────────


def _element_t4_settings(form):
    from test_p5b_followups import _t4_settings

    base = _t4_settings()  # 旧形态：tech_state 种子 + tech_repair_enabled
    if form == "legacy_seed":
        return {**base, "element_modeling_enabled": True}
    nodes = base["tech_state"]["nodes"]
    folded = {**base, "element_modeling_enabled": True, "causal_lines": [{"id": "main_line", "label": "主线"}]}
    er.fold_legacy_tech_state(folded)
    assert [n["id"] for n in tm.get_nodes(folded)] == [n["id"] for n in nodes]
    return folded


def _node_now(data_dir, tid="ai"):
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    return next(n for n in tm.get_nodes(manifest.settings) if n["id"] == tid), manifest.settings


@pytest.mark.parametrize("form", ["legacy_seed", "prefolded"])
def test_repair_accepted_in_element_mode(tmp_path, monkeypatch, form):
    from test_p5b_followups import _go, _t4_payload

    reply = {"tech_updates": [{"id": "ai", "stage": "lab", "regression_reason": "融资断裂，项目停摆"}]}
    state, cap, data_dir, store = _go(monkeypatch, tmp_path, _element_t4_settings(form), _t4_payload(), reply)
    assert state.tech_repair["status"] == "accepted" and state.tech_violations == []
    n, settings = _node_now(data_dir)
    assert n["stage"] == "lab" and n["progress"] == 0.5
    assert "tech_state" not in settings
    inputs = cap["repair_inputs"][0]
    assert "阶段 expert" in inputs["tech_state_hint"] and "进度 0%" in inputs["tech_state_hint"]  # 本步开始前的状态
    snap = store.load_history("main")[-1].dynamic_snapshot
    assert "tech_state" not in snap and tm.snapshot_has_nodes(snap, {"element_modeling_enabled": True})


@pytest.mark.parametrize("form", ["legacy_seed", "prefolded"])
def test_repair_rejected_keeps_first_adjudication_in_element_mode(tmp_path, monkeypatch, form):
    from test_p5b_followups import _go, _t4_payload

    same = {"tech_updates": [{"id": "ai", "stage": "lab"}]}
    state, cap, data_dir, _ = _go(monkeypatch, tmp_path, _element_t4_settings(form), _t4_payload(), same)
    assert state.tech_repair["status"] == "rejected"
    n, _s = _node_now(data_dir)
    assert n["stage"] == "expert" and abs(n["progress"] - 0.1) < 1e-9


def test_repair_readjudication_failure_restores_state_in_element_mode(tmp_path, monkeypatch):
    from test_p5b_followups import _go, _t4_payload

    real = tm.apply_step
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real(*a, **k)

    monkeypatch.setattr(tm, "apply_step", flaky)
    reply = {"tech_updates": [{"id": "ai", "stage": "lab", "regression_reason": "有依据"}]}
    state, _cap, data_dir, _ = _go(monkeypatch, tmp_path, _element_t4_settings("prefolded"), _t4_payload(), reply)
    assert state.tech_repair["status"] == "failed"
    n, _s = _node_now(data_dir)
    assert n["stage"] == "expert" and abs(n["progress"] - 0.1) < 1e-9


def test_repair_restoring_a_new_registration_dropped_by_the_repaired_proposal(tmp_path, monkeypatch):
    """第一次裁决登记了新技术 chip（建了元素线）；修复回复里不再提 chip 且违规没有减少 → 被拒绝，
    回滚后重裁没有 chip，最终必须把第一次裁决的结果（含 chip 元素线）原样放回。"""
    from test_p5b_followups import _go

    from test_fast_forward import _default_payload

    settings = _element_t4_settings("prefolded")
    payload = _default_payload(1, elapsed_days=10, tech_updates=[
        {"id": "ai", "stage": "lab"}, {"id": "chip", "name": "芯片", "stage": "lab"}])
    reply = {"tech_updates": [{"id": "ai", "stage": "lab"}]}  # 无改善 → rejected；且不含 chip
    state, _cap, data_dir, _ = _go(monkeypatch, tmp_path, settings, payload, reply)
    assert state.tech_repair["status"] == "rejected"
    _n, final = _node_now(data_dir)
    assert {n["id"] for n in tm.get_nodes(final)} == {"ai", "chip"}
    assert "chip" in [x["id"] for x in final["causal_lines"]]
