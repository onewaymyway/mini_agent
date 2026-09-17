"""tests/test_delete_and_achievements.py — 阶段七交付的单元测试。

覆盖两块：
1. `engine.delete_simulation`：删除后实例目录不再存在、`list_sim_ids`
   不再列出它、对不存在的实例删除会报 `SimNotFoundError`。
2. `achievements.compute_achievements`：根据 manifest + history 计算出
   的徽章解锁状态是否符合预期（不依赖 LLM，纯函数）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from world_simulator.achievements import achievement_progress, compute_achievements
from world_simulator.engine import SimEngineError, delete_simulation, rename_simulation
from world_simulator.state_model import ChoiceOption, SimManifest, SimState
from world_simulator.store import SimNotFoundError, SimStore, list_sim_ids, now_iso


def _make_sim(data_dir: Path, sim_id: str, n_steps: int, **manifest_kwargs) -> SimStore:
    store = SimStore.for_root(data_dir, sim_id)
    ts = now_iso()
    manifest = SimManifest(
        sim_id=sim_id, template="life_sim", intent="i", title="t",
        created_at=ts, updated_at=ts, **manifest_kwargs,
    )
    store.save_manifest(manifest)
    for step in range(n_steps + 1):
        store.append_state(
            SimState(step=step, summary=f"summary-{step}", vars={"age": 22 + step})
        )
    return store


# ── delete_simulation ───────────────────────────────────────────────


def test_delete_simulation_removes_dir(tmp_path: Path):
    _make_sim(tmp_path, "sim_a", 2)
    assert "sim_a" in list_sim_ids(tmp_path)

    delete_simulation(tmp_path, "sim_a")

    assert "sim_a" not in list_sim_ids(tmp_path)
    assert not (tmp_path / "sim_a").exists()


def test_delete_simulation_not_found_raises(tmp_path: Path):
    with pytest.raises(SimNotFoundError):
        delete_simulation(tmp_path, "does_not_exist")


def test_delete_simulation_does_not_affect_siblings(tmp_path: Path):
    _make_sim(tmp_path, "sim_a", 1)
    _make_sim(tmp_path, "sim_b", 1)

    delete_simulation(tmp_path, "sim_a")

    remaining = list_sim_ids(tmp_path)
    assert remaining == ["sim_b"]


# ── rename_simulation ────────────────────────────────────────────────


def test_rename_simulation_updates_title(tmp_path: Path):
    _make_sim(tmp_path, "sim_a", 1)

    manifest = rename_simulation(tmp_path, "sim_a", "新标题")

    assert manifest.title == "新标题"
    reloaded = SimStore.for_root(tmp_path, "sim_a").load_manifest()
    assert reloaded.title == "新标题"


def test_rename_simulation_strips_whitespace(tmp_path: Path):
    _make_sim(tmp_path, "sim_a", 1)

    manifest = rename_simulation(tmp_path, "sim_a", "  带空格的标题  ")

    assert manifest.title == "带空格的标题"


def test_rename_simulation_rejects_empty_title(tmp_path: Path):
    _make_sim(tmp_path, "sim_a", 1)

    with pytest.raises(SimEngineError):
        rename_simulation(tmp_path, "sim_a", "   ")


def test_rename_simulation_not_found_raises(tmp_path: Path):
    with pytest.raises(SimNotFoundError):
        rename_simulation(tmp_path, "does_not_exist", "新标题")


# ── achievements ─────────────────────────────────────────────────────


def test_achievements_initial_state_only_nothing_unlocked(tmp_path: Path):
    store = _make_sim(tmp_path, "sim_c", 0)
    manifest = store.load_manifest()
    history = store.load_history()  # step 0 (初始状态) only，尚未推进过

    achievements = compute_achievements(manifest, history)
    unlocked_ids = {a.id for a in achievements if a.unlocked}

    assert unlocked_ids == set()


def test_achievements_one_advance_unlocks_first_step(tmp_path: Path):
    store = _make_sim(tmp_path, "sim_c2", 1)
    manifest = store.load_manifest()
    history = store.load_history()  # step 0 + step 1

    achievements = compute_achievements(manifest, history)
    unlocked_ids = {a.id for a in achievements if a.unlocked}

    assert unlocked_ids == {"first_step"}


def test_achievements_ten_steps_unlocks_progression(tmp_path: Path):
    store = _make_sim(tmp_path, "sim_d", 10)
    manifest = store.load_manifest()
    history = store.load_history()

    achievements = compute_achievements(manifest, history)
    unlocked_ids = {a.id for a in achievements if a.unlocked}

    assert {"first_step", "five_steps", "ten_steps"} <= unlocked_ids
    assert "major_decision" not in unlocked_ids
    assert "autopilot" not in unlocked_ids
    assert "ended" not in unlocked_ids


def test_achievements_major_decision_and_autopilot_and_ended(tmp_path: Path):
    store = SimStore.for_root(tmp_path, "sim_e")
    ts = now_iso()
    store.save_manifest(
        SimManifest(
            sim_id="sim_e", template="life_sim", intent="i", title="t",
            created_at=ts, updated_at=ts, status="ended",
        )
    )
    store.append_state(SimState(step=0, summary="s0"))
    store.append_state(
        SimState(
            step=1, summary="s1", chosen_option_id="opt0", chosen_by="autopilot",
            chosen_reason="风险偏好保守", major_decision=True,
        )
    )
    manifest = store.load_manifest()
    history = store.load_history()

    achievements = compute_achievements(manifest, history)
    unlocked_ids = {a.id for a in achievements if a.unlocked}

    assert {"major_decision", "autopilot", "ended"} <= unlocked_ids

    progress = achievement_progress(achievements)
    assert progress["unlocked_count"] == len(unlocked_ids)
    assert progress["total_count"] == len(achievements)
    assert 0.0 < progress["ratio"] <= 1.0
