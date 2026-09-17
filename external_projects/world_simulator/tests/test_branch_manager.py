"""tests/test_branch_manager.py — branch_manager 单元测试。

不依赖 LLM：直接用 `SimStore` 手工构造好几步历史，测试分叉/切换/对比
是否符合"不销毁原时间线"的核心约定。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from world_simulator import branch_manager as bm
from world_simulator.state_model import ChoiceOption, SimManifest, SimState
from world_simulator.store import SimStore, now_iso


def _make_sim(data_dir: Path, sim_id: str, n_steps: int) -> SimStore:
    store = SimStore.for_root(data_dir, sim_id)
    ts = now_iso()
    store.save_manifest(
        SimManifest(
            sim_id=sim_id, template="life_sim", intent="i", title="t",
            created_at=ts, updated_at=ts,
        )
    )
    for step in range(n_steps + 1):
        store.append_state(
            SimState(
                step=step,
                summary=f"summary-{step}",
                vars={"age": 22 + step},
                options=[ChoiceOption(id=f"opt{step}", label=f"选项{step}")],
            )
        )
    return store


def test_list_branches_main_only(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 2)
    assert bm.list_branches(data_dir, "sim1") == ["main"]


def test_fork_branch_preserves_original_timeline(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 4)  # main: step 0..4

    new_branch = bm.fork_branch(data_dir, "sim1", from_step=2, source_branch="main")

    # 原 main 分支历史原封不动
    main_history = bm.load_branch_timeline(data_dir, "sim1", "main")
    assert [s.step for s in main_history] == [0, 1, 2, 3, 4]

    # 新分支只包含 step <= 2 的部分
    new_history = bm.load_branch_timeline(data_dir, "sim1", new_branch)
    assert [s.step for s in new_history] == [0, 1, 2]

    store = SimStore.for_root(data_dir, "sim1")
    manifest = store.load_manifest()
    assert manifest.branch == new_branch  # 默认 switch=True
    assert manifest.current_step == 2

    assert set(bm.list_branches(data_dir, "sim1")) == {"main", new_branch}


def test_fork_branch_without_switch(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 3)

    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1, switch=False)

    store = SimStore.for_root(data_dir, "sim1")
    manifest = store.load_manifest()
    assert manifest.branch == "main"  # 没有切换
    assert new_branch in bm.list_branches(data_dir, "sim1")


def test_fork_branch_invalid_step_raises(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 1)
    with pytest.raises(bm.BranchError):
        bm.fork_branch(data_dir, "sim1", from_step=-1)


def test_fork_branch_clears_chosen_option_on_cutoff_node(tmp_path):
    """回滚到某一步（包括第 0 步，即最开始）应该让那一步重新变回"还没
    选过"的状态，而不是带着原分支已经做过的选择——否则"回滚重新选"会
    看起来什么都没变，`from_step=0`（回到最开始）尤其明显。"""
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, "sim1", 3)
    # 手工模拟"main 分支上 step 0/1 都已经做过选择"这个既成事实。
    history = store.load_history("main")
    history[0].chosen_option_id = "opt0"
    history[0].chosen_by = "user"
    history[1].chosen_option_id = "opt1"
    history[1].chosen_by = "user"
    from mini_agent.utils.atomic_write import atomic_write_jsonl

    atomic_write_jsonl(store.state_history_path("main"), [s.to_dict() for s in history])

    # 回到最开始（step 0）：新分支的 step 0 不应该还带着"选了 opt0"。
    branch0 = bm.fork_branch(data_dir, "sim1", from_step=0, switch=False)
    new_state0 = store.load_current_state(branch0)
    assert new_state0.step == 0
    assert new_state0.chosen_option_id is None
    assert new_state0.chosen_by is None
    assert len(new_state0.options) == 1  # 候选方向本身原样保留

    # 原 main 分支的历史记录不受影响（没有被就地改坏）。
    main_history = bm.load_branch_timeline(data_dir, "sim1", "main")
    assert main_history[0].chosen_option_id == "opt0"

    # 回到 step 1 也一样：step 1 的选择记录要清空，step 0 的不受影响。
    branch1 = bm.fork_branch(data_dir, "sim1", from_step=1, switch=False)
    new_history1 = bm.load_branch_timeline(data_dir, "sim1", branch1)
    assert new_history1[0].chosen_option_id == "opt0"
    assert new_history1[1].chosen_option_id is None


def test_switch_branch(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 3)
    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1, switch=False)

    manifest = bm.switch_branch(data_dir, "sim1", new_branch)
    assert manifest.branch == new_branch
    assert manifest.current_step == 1

    manifest2 = bm.switch_branch(data_dir, "sim1", "main")
    assert manifest2.branch == "main"
    assert manifest2.current_step == 3


def test_switch_branch_missing_raises(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 1)
    with pytest.raises(bm.BranchError):
        bm.switch_branch(data_dir, "sim1", "does_not_exist")


def test_compare_timelines_cross_sim(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 2)
    _make_sim(data_dir, "sim2", 3)

    result = bm.compare_timelines(data_dir, [("sim1", "main"), ("sim2", "main")])
    assert len(result["lines"]) == 2
    assert result["lines"][0]["sim_id"] == "sim1"
    assert [s.step for s in result["lines"][0]["history"]] == [0, 1, 2]
    assert result["lines"][1]["sim_id"] == "sim2"
    assert [s.step for s in result["lines"][1]["history"]] == [0, 1, 2, 3]


def test_delete_branch_removes_it(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 3)
    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1, switch=False)
    assert new_branch in bm.list_branches(data_dir, "sim1")

    bm.delete_branch(data_dir, "sim1", new_branch)

    assert new_branch not in bm.list_branches(data_dir, "sim1")
    # main 分支不受影响
    assert bm.list_branches(data_dir, "sim1") == ["main"]


def test_delete_branch_rejects_main(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 2)
    with pytest.raises(bm.BranchError):
        bm.delete_branch(data_dir, "sim1", "main")


def test_delete_branch_rejects_active_branch(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 2)
    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1, switch=True)

    with pytest.raises(bm.BranchError):
        bm.delete_branch(data_dir, "sim1", new_branch)

    # 切走之后可以正常删除
    bm.switch_branch(data_dir, "sim1", "main")
    bm.delete_branch(data_dir, "sim1", new_branch)
    assert new_branch not in bm.list_branches(data_dir, "sim1")


def test_delete_branch_missing_raises(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 1)
    with pytest.raises(bm.BranchError):
        bm.delete_branch(data_dir, "sim1", "does_not_exist")


def test_fork_branch_inherits_independent_pilot_config(tmp_path):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, "sim1", 2)
    store.save_pilot_config("main", "autopilot", {"risk_preference": "aggressive"})

    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1, switch=False)

    # 新分支继承了创建时刻 main 的配置……
    new_cfg = store.load_pilot_config(new_branch)
    assert new_cfg["pilot_mode"] == "autopilot"
    assert new_cfg["autopilot"] == {"risk_preference": "aggressive"}

    # ……但落盘成独立一份：改新分支的配置不影响 main。
    store.save_pilot_config(new_branch, "manual", {})
    assert store.load_pilot_config(new_branch) == {"pilot_mode": "manual", "autopilot": {}}
    main_cfg = store.load_pilot_config("main")
    assert main_cfg["pilot_mode"] == "autopilot"
    assert main_cfg["autopilot"] == {"risk_preference": "aggressive"}


def test_fork_branch_switch_mirrors_pilot_config_into_manifest(tmp_path):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, "sim1", 2)
    store.save_pilot_config("main", "autopilot", {"review_mode": "silent"})

    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1, switch=True)

    manifest = store.load_manifest()
    assert manifest.branch == new_branch
    assert manifest.pilot_mode == "autopilot"
    assert manifest.autopilot == {"review_mode": "silent"}


def test_switch_branch_mirrors_target_pilot_config(tmp_path):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, "sim1", 2)
    store.save_pilot_config("main", "manual", {})
    new_branch = bm.fork_branch(data_dir, "sim1", from_step=1, switch=False)
    store.save_pilot_config(new_branch, "autopilot", {"principles": "谨慎"})

    manifest = bm.switch_branch(data_dir, "sim1", new_branch)
    assert manifest.pilot_mode == "autopilot"
    assert manifest.autopilot == {"principles": "谨慎"}

    manifest2 = bm.switch_branch(data_dir, "sim1", "main")
    assert manifest2.pilot_mode == "manual"
    assert manifest2.autopilot == {}
