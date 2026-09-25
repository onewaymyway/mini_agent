"""tests/test_explore_branches.py — branch_manager.explore_branches()
单元测试（第十二轮方案第 1 节 Exploration Mode）。

不真的调用 LLM：复用 `test_fast_forward.py` 的既有打桩方式
（`_patch_sequenced_advance_step_workflow()`）替换掉
`WorkflowStore`/`WorkflowRunner`，只关注 `explore_branches()` 自己
的编排逻辑（多条路线各自 fork+advance、失败路线清理、结果映射、
探索结束后切回原分支）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from world_simulator import branch_manager as bm
from world_simulator.engine.management import get_simulation
from world_simulator.state_model import ChoiceOption, SimManifest, SimState
from world_simulator.store import SimStore, now_iso

from test_fast_forward import _default_payload, _patch_sequenced_advance_step_workflow


def _make_sim(data_dir: Path, sim_id: str) -> SimStore:
    store = SimStore.for_root(data_dir, sim_id)
    ts = now_iso()
    store.save_manifest(
        SimManifest(
            sim_id=sim_id, template="life_sim", intent="i", title="t",
            created_at=ts, updated_at=ts,
        )
    )
    store.append_state(
        SimState(
            step=0,
            summary="s0",
            vars={"age": 20},
            options=[
                ChoiceOption(id="optA", label="路线A"),
                ChoiceOption(id="optB", label="路线B"),
            ],
        )
    )
    return store


def test_explore_branches_all_succeed(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1")
    capture: dict = {}
    payloads = [_default_payload(1), _default_payload(1)]
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, payloads, capture)

    routes = [{"choice_option_id": "optA"}, {"choice_option_id": "optB"}]
    results = bm.explore_branches(
        object(), tmp_path, data_dir, "sim1", from_step=0, routes=routes,
    )

    assert set(results.keys()) == {"0", "1"}
    assert results["0"].ok is True
    assert results["1"].ok is True
    assert results["0"].route_label == "路线A"
    assert results["1"].route_label == "路线B"
    assert results["0"].branch != results["1"].branch

    # 两条分支各自独立推进到了 step 1，各自的 chosen_option_id 与来源
    # 路线一一对应。
    branch_a_history = bm.load_branch_timeline(data_dir, "sim1", results["0"].branch)
    branch_b_history = bm.load_branch_timeline(data_dir, "sim1", results["1"].branch)
    assert [s.step for s in branch_a_history] == [0, 1]
    assert [s.step for s in branch_b_history] == [0, 1]
    assert branch_a_history[0].chosen_option_id == "optA"
    assert branch_b_history[0].chosen_option_id == "optB"

    # 探索结束后切回探索开始前的活跃分支（main）。
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.branch == "main"

    # main 分支本身没有被动过。
    main_history = bm.load_branch_timeline(data_dir, "sim1", "main")
    assert [s.step for s in main_history] == [0]


def test_explore_branches_partial_failure_no_leftover_branch(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1")
    capture: dict = {}
    # optA 合法，advance 会成功；"optX" 不在候选列表里，advance() 在
    # 调用 workflow 之前就会直接抛 SimEngineError（见
    # engine/advance.py 里 choice_option_id 的校验），workflow 打桩
    # 只会被成功的那一条路线用到，这里给 2 份 payload 保险。
    payloads = [_default_payload(1), _default_payload(1)]
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, payloads, capture)

    routes = [{"choice_option_id": "optA"}, {"choice_option_id": "optX"}]
    results = bm.explore_branches(
        object(), tmp_path, data_dir, "sim1", from_step=0, routes=routes,
    )

    assert results["0"].ok is True
    assert results["0"].branch is not None
    assert results["1"].ok is False
    assert results["1"].branch is None
    assert results["1"].error  # 报告了失败原因

    # 失败路线不留下半成品分支：实例上应该只多出成功那一条分支。
    branches = bm.list_branches(data_dir, "sim1")
    assert branches == ["main", results["0"].branch]

    # 探索结束后切回 main，不受任何一条路线成败影响。
    manifest, _c, _h = get_simulation(data_dir, "sim1")
    assert manifest.branch == "main"


def test_explore_branches_empty_routes_raises(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1")

    with pytest.raises(bm.BranchError):
        bm.explore_branches(object(), tmp_path, data_dir, "sim1", from_step=0, routes=[])


def test_explore_branches_custom_option_route(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1")
    capture: dict = {}
    payloads = [_default_payload(1)]
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, payloads, capture)

    routes = [{"custom_option": {"label": "自定义路线", "description": "d"}}]
    results = bm.explore_branches(
        object(), tmp_path, data_dir, "sim1", from_step=0, routes=routes,
    )

    assert results["0"].ok is True
    assert results["0"].route_label == "自定义路线"
    branch_history = bm.load_branch_timeline(data_dir, "sim1", results["0"].branch)
    assert branch_history[0].chosen_option_id.startswith("custom_")
