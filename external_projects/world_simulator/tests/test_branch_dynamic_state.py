"""tests/test_branch_dynamic_state.py — WP0（第二十二轮）：分支作用域动态状态。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md`
§2.4 / §4 WP0。

缺陷（先红后绿的复现）：因果树（`settings.causal_lines`）与待兑现关系
（`settings.relationship_pending_effects`）存在整个实例共享的
`manifest.settings` 里，`fork_branch` 不复制也不回滚它——分叉后在新
分支上推进，会把树改写；切回主线，主线看到的是被"另一条时间线"改过
的树。

修复后的契约（这里逐条断言）：
1. 这两类状态在有变化的那一步以 `SimState.dynamic_snapshot` 落盘，
   按分支各自保存；`manifest.settings` 只是"当前活跃分支的工作副本"。
2. `fork_branch` 后新分支自带分叉点那一刻的树（回滚语义）。
3. `switch_branch` 切走前把工作副本提交到旧分支，切入时刷新工作副本。
4. 旧实例（历史里没有任何快照）行为与今天一致，不做迁移。
5. 没有变化的步不带快照（不膨胀历史）；`dynamic_snapshot` 为 None 时
   `to_dict()` 不输出该 key（关闭态逐字节不变）。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import branch_manager as bm
from world_simulator import causal_tree
from world_simulator.engine.advance import advance
from world_simulator.engine.management import get_simulation
from world_simulator.state_model import SimManifest, SimState
from world_simulator.store import SimStore, now_iso

from test_fast_forward import _default_payload, _patch_sequenced_advance_step_workflow


def _lines():
    return causal_tree.ensure_future_trees(
        [{"id": "L1", "label": "线1", "time_granularity": ""}], as_of_step=0
    )


def _branch_ids():
    return [b["id"] for b in _lines()[0]["future_tree"]["branches"]]


def _make_sim(data_dir: Path, sim_id: str = "sim1", *, settings=None) -> SimStore:
    store = SimStore.for_root(data_dir, sim_id)
    ts = now_iso()
    store.save_manifest(
        SimManifest(
            sim_id=sim_id, template="life_sim", intent="i", title="t",
            created_at=ts, updated_at=ts,
            settings=settings if settings is not None else {"causal_lines": _lines()},
        )
    )
    store.append_state(SimState(step=0, summary="s0", vars={"age": 20}, options=[]))
    return store


def _status(data_dir: Path, sim_id: str, bid: str) -> str:
    manifest, _c, _h = get_simulation(data_dir, sim_id)
    for line in manifest.settings.get("causal_lines") or []:
        if line["id"] == "L1":
            for b in line["future_tree"]["branches"]:
                if b["id"] == bid:
                    return b["status"]
    raise AssertionError(f"branch {bid} not found")


def _confirm_payload(step_no: int, bid: str) -> dict:
    return _default_payload(step_no, tree_updates=[{"line_id": "L1", "confirmed_branch": bid}])


def _advance(monkeypatch, tmp_path, data_dir, payload):
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, [payload], {})
    return advance(object(), tmp_path, data_dir, "sim1")


# ── 缺陷复现（修复前红）──────────────────────────────────────────────


def test_fork_advance_then_switch_back_does_not_pollute_main_tree(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir)
    bid = _branch_ids()[0]
    _advance(monkeypatch, tmp_path, data_dir, _default_payload(1))
    _advance(monkeypatch, tmp_path, data_dir, _default_payload(2))

    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1)
    _advance(monkeypatch, tmp_path, data_dir, _confirm_payload(2, bid))
    assert _status(data_dir, "sim1", bid) == "resolved"  # 新分支上确实印证了

    bm.switch_branch(data_dir, "sim1", "main")
    assert _status(data_dir, "sim1", bid) != "resolved"  # 主线没被另一条时间线改写

    bm.switch_branch(data_dir, "sim1", new_branch)
    assert _status(data_dir, "sim1", bid) == "resolved"  # 切回去，新分支的树还在


# ── 回滚语义 ─────────────────────────────────────────────────────────


def test_fork_rolls_tree_back_to_fork_point(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir)
    bid = _branch_ids()[0]
    _advance(monkeypatch, tmp_path, data_dir, _default_payload(1))
    _advance(monkeypatch, tmp_path, data_dir, _confirm_payload(2, bid))  # 主线第 2 步印证
    assert _status(data_dir, "sim1", bid) == "resolved"

    bm.fork_branch(data_dir, "sim1", from_step=1)  # 回到印证发生之前
    assert _status(data_dir, "sim1", bid) != "resolved"

    bm.switch_branch(data_dir, "sim1", "main")
    assert _status(data_dir, "sim1", bid) == "resolved"


def test_explore_branches_leaves_main_tree_intact(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir)
    bid = _branch_ids()[0]
    _advance(monkeypatch, tmp_path, data_dir, _default_payload(1))
    _patch_sequenced_advance_step_workflow(
        monkeypatch, tmp_path, [_confirm_payload(2, bid)], {}
    )
    results = bm.explore_branches(
        object(), tmp_path, data_dir, "sim1", from_step=1,
        routes=[{"custom_option": {"label": "探索路线"}}],
    )
    assert results["0"].ok
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.branch == "main"
    assert _status(data_dir, "sim1", bid) != "resolved"
    bm.switch_branch(data_dir, "sim1", results["0"].branch)
    assert _status(data_dir, "sim1", bid) == "resolved"


# ── 旧实例兼容（历史里没有任何快照）──────────────────────────────────


def test_legacy_instance_without_snapshots_is_isolated_on_leave(tmp_path, monkeypatch):
    """旧实例：主线历史没有快照。首次切走时把当时的工作副本提交到主线
    头部，之后切回主线仍是当时的树（只对"新写入"隔离，不迁移历史步）。"""
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir)
    bid = _branch_ids()[0]
    assert all(s.dynamic_snapshot is None for s in store.load_history("main"))

    new_branch = bm.fork_branch(data_dir, "sim1", from_step=0)
    _advance(monkeypatch, tmp_path, data_dir, _confirm_payload(1, bid))
    assert _status(data_dir, "sim1", bid) == "resolved"

    bm.switch_branch(data_dir, "sim1", "main")
    assert _status(data_dir, "sim1", bid) != "resolved"
    # 旧历史步本身没有被批量改写：只有头部（step 0）因"离开时提交"多了快照
    main_history = store.load_history("main")
    assert [s.step for s in main_history] == [0]
    bm.switch_branch(data_dir, "sim1", new_branch)
    assert _status(data_dir, "sim1", bid) == "resolved"


def test_legacy_instance_without_any_dynamic_state_is_unchanged(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, settings={})
    _advance(monkeypatch, tmp_path, data_dir, _default_payload(1))
    history = store.load_history("main")
    # 没有任何动态状态：不产生快照，也不影响推进
    assert all(s.dynamic_snapshot is None for s in history)
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.current_step == 1


# ── 快照落盘规则 ─────────────────────────────────────────────────────


def test_snapshot_written_only_when_changed(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir)
    bid = _branch_ids()[0]
    _advance(monkeypatch, tmp_path, data_dir, _default_payload(1))  # 首次：旧实例开始写快照
    _advance(monkeypatch, tmp_path, data_dir, _default_payload(2))  # 无变化：不再写
    _advance(monkeypatch, tmp_path, data_dir, _confirm_payload(3, bid))  # 有变化：写
    history = store.load_history("main")
    assert [s.dynamic_snapshot is not None for s in history] == [False, True, False, True]
    snap = history[3].dynamic_snapshot
    branch = next(
        b for b in snap["causal_lines"][0]["future_tree"]["branches"] if b["id"] == bid
    )
    assert branch["status"] == "resolved"


def test_manual_settings_edit_is_committed_on_leave(tmp_path, monkeypatch):
    """用户在设置面板手改了因果线（只改了工作副本），切走再切回，不丢。"""
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir)
    _advance(monkeypatch, tmp_path, data_dir, _default_payload(1))

    manifest = store.load_manifest()
    lines = copy.deepcopy(manifest.settings["causal_lines"])
    lines[0]["label"] = "手改后的名字"
    manifest.settings = {**manifest.settings, "causal_lines": lines}
    store.save_manifest(manifest)

    other = bm.fork_branch(data_dir, "sim1", from_step=0, switch=False)
    bm.switch_branch(data_dir, "sim1", other)
    bm.switch_branch(data_dir, "sim1", "main")
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.settings["causal_lines"][0]["label"] == "手改后的名字"


def test_relationship_pending_effects_are_branch_scoped(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    settings = {
        "causal_lines": _lines(),
        "relationships": [{"id": "r1", "from": "A", "to": "B", "delay_steps": 2}],
    }
    _make_sim(data_dir, settings=settings)
    _advance(monkeypatch, tmp_path, data_dir, _default_payload(1))
    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1)
    _advance(
        monkeypatch, tmp_path, data_dir,
        _default_payload(2, triggered_relationships=["r1"]),
    )
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert len(manifest.settings.get("relationship_pending_effects") or []) == 1

    bm.switch_branch(data_dir, "sim1", "main")
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert not (manifest.settings.get("relationship_pending_effects") or [])
    bm.switch_branch(data_dir, "sim1", new_branch)
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert len(manifest.settings.get("relationship_pending_effects") or []) == 1


def test_merge_into_active_branch_refreshes_working_copy(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir)
    bid = _branch_ids()[0]
    _advance(monkeypatch, tmp_path, data_dir, _default_payload(1))
    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1, switch=False)
    bm.switch_branch(data_dir, "sim1", new_branch)
    _advance(monkeypatch, tmp_path, data_dir, _confirm_payload(2, bid))
    bm.switch_branch(data_dir, "sim1", "main")
    assert _status(data_dir, "sim1", bid) != "resolved"

    # 把 new_branch 第 0 步之后的历史并入 main（活跃分支）。两条分支
    # step 0 逐字节一致（分叉点是 step 1，step 0 没有被改写），满足
    # merge_branch 的前置条件。合并后 main 的历史来自 new_branch，工作
    # 副本必须跟着刷新成 new_branch 的树。
    bm.merge_branch(data_dir, "sim1", source=new_branch, target="main", from_step=0)
    assert _status(data_dir, "sim1", bid) == "resolved"


# ── 序列化 ───────────────────────────────────────────────────────────


def test_dynamic_snapshot_serialization_roundtrip_and_omission():
    plain = SimState(step=1, summary="s")
    assert "dynamic_snapshot" not in plain.to_dict()  # None 时不输出，旧格式逐字节不变
    assert SimState.from_dict(plain.to_dict()).dynamic_snapshot is None

    snap = {"causal_lines": [{"id": "L1"}]}
    with_snap = SimState(step=2, summary="s", dynamic_snapshot=snap)
    restored = SimState.from_dict(with_snap.to_dict())
    assert restored.dynamic_snapshot == snap
    assert restored.dynamic_snapshot is not snap  # 深拷贝，不共享引用
