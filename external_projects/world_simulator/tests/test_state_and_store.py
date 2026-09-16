"""tests/test_state_and_store.py — state_model / store 单元测试。

阶段一核心闭环里"不依赖 LLM"的那部分：数据结构的序列化/反序列化，
以及基于 `atomic_write` 的落盘/读回是否一致。engine.py 涉及 LLM 调用
的部分见 `test_engine.py`（用 monkeypatch 打桩 workflow 引擎）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator.state_model import ChoiceOption, SimManifest, SimState
from world_simulator.store import SimNotFoundError, SimStore, list_sim_ids, now_iso


def test_state_roundtrip():
    state = SimState(
        step=1,
        summary="摘要",
        narrative="发生了一些事",
        vars={"age": 21, "stage": "大学"},
        options=[ChoiceOption(id="a", label="选项A", description="去实习")],
        chosen_option_id="a",
        chosen_by="user",
    )
    d = state.to_dict()
    restored = SimState.from_dict(d)
    assert restored.step == 1
    assert restored.vars["age"] == 21
    assert restored.options[0].id == "a"
    assert restored.chosen_option_id == "a"


def test_manifest_roundtrip():
    ts = now_iso()
    m = SimManifest(
        sim_id="life_sim_abc123",
        template="life_sim",
        intent="模拟一个刚毕业的人生",
        title="毕业生的选择",
        created_at=ts,
        updated_at=ts,
    )
    d = m.to_dict()
    restored = SimManifest.from_dict(d)
    assert restored.sim_id == "life_sim_abc123"
    assert restored.status == "active"
    assert restored.pilot_mode == "manual"


def test_store_save_load_and_history(tmp_path):
    data_dir = tmp_path / "data"
    store = SimStore.for_root(data_dir, "life_sim_test01")

    assert not store.exists()

    ts = now_iso()
    manifest = SimManifest(
        sim_id="life_sim_test01",
        template="life_sim",
        intent="test intent",
        title="test title",
        created_at=ts,
        updated_at=ts,
    )
    store.save_manifest(manifest)
    assert store.exists()

    loaded = store.load_manifest()
    assert loaded.sim_id == "life_sim_test01"

    state0 = SimState(step=0, summary="初始状态", vars={"age": 22})
    store.append_state(state0)
    state1 = SimState(step=1, summary="推进一步后", vars={"age": 23})
    store.append_state(state1)

    current = store.load_current_state()
    assert current is not None
    assert current.step == 1
    assert current.summary == "推进一步后"

    history = store.load_history()
    assert [s.step for s in history] == [0, 1]

    assert list_sim_ids(data_dir) == ["life_sim_test01"]


def test_load_manifest_missing_raises(tmp_path):
    store = SimStore.for_root(tmp_path / "data", "does_not_exist")
    import pytest

    with pytest.raises(SimNotFoundError):
        store.load_manifest()
