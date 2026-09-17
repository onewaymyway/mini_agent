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


def test_state_roundtrip_preserves_uncertain_fields():
    """阶段十一（4.3 节）：`uncertain_fields` 应该原样经过
    to_dict/from_dict 往返，旧数据（没有这个字段）也应该正常落回空
    列表，不影响加载。"""
    state = SimState(
        step=2,
        summary="s",
        vars={"startup_success_rate": 0.18},
        uncertain_fields=[
            {"field": "startup_success_rate", "confidence": "low", "note": "主观估计"}
        ],
    )
    restored = SimState.from_dict(state.to_dict())
    assert restored.uncertain_fields == [
        {"field": "startup_success_rate", "confidence": "low", "note": "主观估计"}
    ]

    legacy_restored = SimState.from_dict({"step": 0, "summary": "旧数据"})
    assert legacy_restored.uncertain_fields == []


def test_state_roundtrip_preserves_key_drivers():
    """阶段十三（4.5 节）：`key_drivers` 应该原样经过 to_dict/from_dict
    往返，旧数据（没有这个字段）也应该正常落回空列表。"""
    state = SimState(
        step=3, summary="s",
        key_drivers=["市场需求超预期", "现金储备见底被迫收缩"],
    )
    restored = SimState.from_dict(state.to_dict())
    assert restored.key_drivers == ["市场需求超预期", "现金储备见底被迫收缩"]

    legacy_restored = SimState.from_dict({"step": 0, "summary": "旧数据"})
    assert legacy_restored.key_drivers == []


def test_state_roundtrip_preserves_causal_links():
    """阶段十五（4.7 节）：`causal_links` 应该原样经过 to_dict/from_dict
    往返，旧数据（没有这个字段）也应该正常落回空列表；非字典项应该被
    跳过而不是报错中断。"""
    state = SimState(
        step=3, summary="s",
        key_drivers=["现金储备见底"],
        causal_links=[
            {
                "driver": "现金储备见底",
                "affected_fields": ["cash", "stage"],
                "effect": "被迫从「自由职业」转为「求稳定工作」",
            }
        ],
    )
    restored = SimState.from_dict(state.to_dict())
    assert restored.causal_links == [
        {
            "driver": "现金储备见底",
            "affected_fields": ["cash", "stage"],
            "effect": "被迫从「自由职业」转为「求稳定工作」",
        }
    ]

    legacy_restored = SimState.from_dict({"step": 0, "summary": "旧数据"})
    assert legacy_restored.causal_links == []

    mixed_restored = SimState.from_dict(
        {"step": 0, "summary": "s", "causal_links": ["不是字典", {"driver": "x"}]}
    )
    assert mixed_restored.causal_links == [{"driver": "x"}]


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


def test_pilot_config_defaults_when_no_file(tmp_path):
    store = SimStore.for_root(tmp_path / "data", "sim1")
    assert store.load_pilot_config("main") == {"pilot_mode": "manual", "autopilot": {}}
    assert store.load_pilot_config("br_xyz") == {"pilot_mode": "manual", "autopilot": {}}


def test_pilot_config_save_and_load_roundtrip(tmp_path):
    store = SimStore.for_root(tmp_path / "data", "sim1")
    store.save_pilot_config("main", "autopilot", {"risk_preference": "保守"})
    assert store.load_pilot_config("main") == {
        "pilot_mode": "autopilot",
        "autopilot": {"risk_preference": "保守"},
    }


def test_pilot_config_falls_back_to_manifest_for_legacy_main(tmp_path):
    """旧数据：main 分支没有独立的 pilot_config.json，退回读 manifest 顶层字段。"""
    data_dir = tmp_path / "data"
    store = SimStore.for_root(data_dir, "sim1")
    ts = now_iso()
    store.save_manifest(
        SimManifest(
            sim_id="sim1", template="life_sim", intent="i", title="t",
            created_at=ts, updated_at=ts,
            pilot_mode="autopilot", autopilot={"review_mode": "silent"},
        )
    )
    assert store.load_pilot_config("main") == {
        "pilot_mode": "autopilot",
        "autopilot": {"review_mode": "silent"},
    }


def test_delete_branch_dir_removes_pilot_config_too(tmp_path):
    data_dir = tmp_path / "data"
    store = SimStore.for_root(data_dir, "sim1")
    store.save_pilot_config("br_abc", "autopilot", {})
    assert store.pilot_config_path("br_abc").exists()
    store.delete_branch_dir("br_abc")
    assert not store.pilot_config_path("br_abc").exists()
