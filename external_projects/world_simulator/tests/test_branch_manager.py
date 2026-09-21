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


def test_list_branches_detailed_includes_metadata(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 4)  # main: step 0..4

    new_branch = bm.fork_branch(
        data_dir, "sim1", from_step=2, source_branch="main", switch=False,
    )

    detailed = bm.list_branches_detailed(data_dir, "sim1")
    by_id = {d["branch"]: d for d in detailed}

    assert set(by_id) == {"main", new_branch}

    # main：创建时间取自 manifest，没有来源分支
    assert by_id["main"]["is_current"] is True
    assert by_id["main"]["source_branch"] is None
    assert by_id["main"]["from_step"] is None
    assert by_id["main"]["step_count"] == 5
    assert by_id["main"]["current_step"] == 4

    # 新分支：记录了来源分支/分叉自哪一步，且不是当前活跃分支（switch=False）
    fork_info = by_id[new_branch]
    assert fork_info["is_current"] is False
    assert fork_info["source_branch"] == "main"
    assert fork_info["from_step"] == 2
    assert fork_info["step_count"] == 3
    assert fork_info["current_step"] == 2
    assert fork_info["created_at"]  # 非空

    # 手动挡默认：pilot_mode 为 manual，不应显示自动挡摘要信息
    assert fork_info["pilot_mode"] == "manual"
    assert fork_info["autopilot_enabled"] is False


def test_list_branches_detailed_reflects_autopilot_config(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 1)
    store = SimStore.for_root(data_dir, "sim1")
    store.save_pilot_config(
        "main", "autopilot",
        {
            "enabled": True, "risk_preference": "aggressive",
            "review_mode": "pause_on_major_decision",
            "allow_custom_options": True,
            "principles": ["优先长期收益", "避免过度负债"],
        },
    )

    detailed = bm.list_branches_detailed(data_dir, "sim1")
    main_info = detailed[0]
    assert main_info["pilot_mode"] == "autopilot"
    assert main_info["autopilot_enabled"] is True
    assert main_info["risk_preference"] == "aggressive"
    assert main_info["review_mode"] == "pause_on_major_decision"
    assert main_info["allow_custom_options"] is True
    assert main_info["principles_count"] == 2


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


# ---------------------------------------------------------------------------
# merge_branch()（第八轮批次六，第 7 节）
# ---------------------------------------------------------------------------


def _append_diverging_states(store: SimStore, branch: str, start_step: int, n: int, tag: str) -> None:
    """给某条分支从 `start_step` 开始追加 `n` 个状态，内容带上 `tag`
    区分不同分支各自的后续演化（模拟合并前两条分支已经分岔）。"""
    for i in range(n):
        step = start_step + i
        store.append_state(
            SimState(step=step, summary=f"{tag}-{step}", vars={"age": 22 + step, "tag": tag}),
            branch=branch,
        )


def test_merge_branch_replaces_target_suffix_with_source_suffix(tmp_path):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, "sim1", 2)  # main: step 0..2
    branch_b = bm.fork_branch(data_dir, "sim1", from_step=2, switch=False)

    # main 和 branch_b 各自往后演化出不同的 step 3/4。
    _append_diverging_states(store, "main", 3, 2, "main")
    _append_diverging_states(store, branch_b, 3, 2, "b")

    merged_last_step = bm.merge_branch(
        data_dir, "sim1", source="main", target=branch_b, from_step=2
    )
    assert merged_last_step == 4

    merged_history = bm.load_branch_timeline(data_dir, "sim1", branch_b)
    assert [s.step for s in merged_history] == [0, 1, 2, 3, 4]
    # step 0..2 保持 branch_b 原有（和 main 一致的）内容不变。
    assert merged_history[2].summary == "summary-2"
    # step 3/4 被替换成了 source（main）的内容。
    assert merged_history[3].summary == "main-3"
    assert merged_history[4].summary == "main-4"

    # 原 main 分支历史不受影响（merge 只改 target）。
    main_history = bm.load_branch_timeline(data_dir, "sim1", "main")
    assert [s.summary for s in main_history if s.step >= 3] == ["main-3", "main-4"]


def test_merge_branch_raises_when_history_diverges_before_from_step(tmp_path):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, "sim1", 2)
    branch_b = bm.fork_branch(data_dir, "sim1", from_step=2, switch=False)
    # 人为篡改 branch_b 的 step 1，制造"from_step 之前就已经分歧"。
    history_b = store.load_history(branch_b)
    history_b[1] = SimState(step=1, summary="被人为改过的 step 1", vars={"age": 999})
    from mini_agent.utils.atomic_write import atomic_write_jsonl
    atomic_write_jsonl(store.state_history_path(branch_b), [s.to_dict() for s in history_b])

    with pytest.raises(bm.BranchError):
        bm.merge_branch(data_dir, "sim1", source="main", target=branch_b, from_step=2)

    # 拒绝执行时不应该改动 target 的历史。
    unchanged = bm.load_branch_timeline(data_dir, "sim1", branch_b)
    assert unchanged[1].summary == "被人为改过的 step 1"


def test_merge_branch_raises_when_source_equals_target(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 2)
    with pytest.raises(bm.BranchError):
        bm.merge_branch(data_dir, "sim1", source="main", target="main", from_step=1)


def test_merge_branch_raises_when_branch_missing(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1", 2)
    with pytest.raises(bm.BranchError):
        bm.merge_branch(data_dir, "sim1", source="main", target="not_exist", from_step=1)
    with pytest.raises(bm.BranchError):
        bm.merge_branch(data_dir, "sim1", source="not_exist", target="main", from_step=1)


def test_merge_branch_raises_when_from_step_negative(tmp_path):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, "sim1", 2)
    branch_b = bm.fork_branch(data_dir, "sim1", from_step=2, switch=False)
    with pytest.raises(bm.BranchError):
        bm.merge_branch(data_dir, "sim1", source="main", target=branch_b, from_step=-1)


def test_merge_branch_raises_when_branch_missing_prefix_history(tmp_path):
    """target 分支的历史根本没有覆盖到 `from_step`（比如只推进到了
    step 1，但 `from_step=5`）时应该拒绝，而不是静默产出一份"缺了中间
    一段"的历史。"""
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, "sim1", 1)  # main: step 0..1
    branch_b = bm.fork_branch(data_dir, "sim1", from_step=1, switch=False)
    _append_diverging_states(store, "main", 2, 5, "main")  # main: step 0..6

    with pytest.raises(bm.BranchError):
        bm.merge_branch(data_dir, "sim1", source="main", target=branch_b, from_step=5)


def test_merge_branch_updates_manifest_current_step_when_target_is_active(tmp_path):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, "sim1", 2)
    # branch_b 是当前活跃分支（switch=True）。
    branch_b = bm.fork_branch(data_dir, "sim1", from_step=2, switch=True)
    _append_diverging_states(store, "main", 3, 3, "main")
    _append_diverging_states(store, branch_b, 3, 1, "b")

    bm.merge_branch(data_dir, "sim1", source="main", target=branch_b, from_step=2)

    manifest = store.load_manifest()
    assert manifest.branch == branch_b
    assert manifest.current_step == 5  # main 演化到了 step 5


def test_merge_branch_does_not_touch_manifest_when_target_is_inactive(tmp_path):
    data_dir = tmp_path / "data"
    store = _make_sim(data_dir, "sim1", 2)
    branch_b = bm.fork_branch(data_dir, "sim1", from_step=2, switch=False)  # main 仍是活跃分支
    _append_diverging_states(store, "main", 3, 2, "main")
    _append_diverging_states(store, branch_b, 3, 1, "b")

    bm.merge_branch(data_dir, "sim1", source="main", target=branch_b, from_step=2)

    manifest = store.load_manifest()
    assert manifest.branch == "main"
    assert manifest.current_step != 4  # 不应该被 branch_b 的合并结果影响
