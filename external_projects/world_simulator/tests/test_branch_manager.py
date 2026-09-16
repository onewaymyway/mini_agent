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
