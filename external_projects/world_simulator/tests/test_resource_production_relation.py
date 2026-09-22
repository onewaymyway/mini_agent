"""tests/test_resource_production_relation.py — 第八轮批次二：
`resource_relations` 新增 `production`（持续产出）关系类型的测试。

对应 `next_doc/world_simulator_c_category_precision_upgrade_
improvement_plan.md` 第 3 节。前半部分直接单测
`engine/resource_guard.py` 的归一化 + 检查函数；后半部分通过
`engine.advance()` 做一次端到端集成验证，手法和
`test_spec_and_engine.py` 里已有的 `transfer` 关系测试完全一致。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import world_simulator.engine as engine_mod
from world_simulator.engine.resource_guard import (
    _check_resource_relations,
    _normalize_resource_relations,
)
from world_simulator.store import SimStore

from test_spec_and_engine import _FakeStatus, _FakeStep, _FakeWorkflow, _write_result_file


# ---------------------------------------------------------------------------
# _normalize_resource_relations()
# ---------------------------------------------------------------------------


def test_normalize_production_relation_with_full_fields():
    raw = [
        {
            "type": "production",
            "field": "resources.wood",
            "amount_per_step": 5,
            "source_line_id": "line_forestry",
            "tolerance": 0.2,
        }
    ]
    assert _normalize_resource_relations(raw) == [
        {
            "type": "production",
            "field": "resources.wood",
            "amount_per_step": 5.0,
            "source_line_id": "line_forestry",
            "tolerance": 0.2,
        }
    ]


def test_normalize_production_relation_defaults_when_optional_fields_missing():
    """未声明 `amount_per_step`/`source_line_id` 时应安全默认
    （`None`/空字符串），不抛错，`tolerance` 用默认 0.1。"""
    raw = [{"type": "production", "field": "resources.wood"}]
    assert _normalize_resource_relations(raw) == [
        {
            "type": "production",
            "field": "resources.wood",
            "amount_per_step": None,
            "source_line_id": "",
            "tolerance": 0.1,
        }
    ]


def test_normalize_production_relation_invalid_amount_per_step_falls_back_to_none():
    raw = [{"type": "production", "field": "resources.wood", "amount_per_step": "not-a-number"}]
    result = _normalize_resource_relations(raw)
    assert result[0]["amount_per_step"] is None


def test_normalize_production_relation_missing_field_is_skipped():
    raw = [{"type": "production", "amount_per_step": 5}]
    assert _normalize_resource_relations(raw) == []


def test_normalize_transfer_relation_still_works_unchanged():
    """回归：`transfer` 关系的归一化行为不受影响（现在内部多了一个
    `"type": "transfer"` key，但既有调用方只读 `from`/`to`/`tolerance`，
    不受影响）。"""
    raw = [{"type": "transfer", "from": "cash", "to": "inventory.value"}]
    result = _normalize_resource_relations(raw)
    assert result == [
        {"type": "transfer", "from": "cash", "to": "inventory.value", "tolerance": 0.1}
    ]


def test_normalize_unknown_relation_type_is_skipped():
    raw = [{"type": "teleport", "field": "x", "amount_per_step": 1}]
    assert _normalize_resource_relations(raw) == []


# ---------------------------------------------------------------------------
# _check_resource_relations()
# ---------------------------------------------------------------------------


def test_check_production_relation_flags_deviation_from_declared_rate():
    raw = [{"type": "production", "field": "resources.wood", "amount_per_step": 10}]
    current = {"resources": {"wood": 100}}
    # 实际只增长了 2，明显偏离声明的每步 10。
    nxt = {"resources": {"wood": 102}}
    violations = _check_resource_relations(current, nxt, raw)
    assert violations == [
        {
            "kind": "production",
            "field": "resources.wood",
            "amount_per_step": 10.0,
            "actual_delta": 2,
        }
    ]


def test_check_production_relation_within_tolerance_produces_no_violation():
    raw = [{"type": "production", "field": "resources.wood", "amount_per_step": 10}]
    current = {"resources": {"wood": 100}}
    # 实际增长 9.5，在默认 10% 容差内。
    nxt = {"resources": {"wood": 109.5}}
    assert _check_resource_relations(current, nxt, raw) == []


def test_check_production_relation_without_amount_per_step_never_checked():
    """未声明速率的 production 关系不参与任何数值核对，哪怕实际变化量
    看起来很离谱。"""
    raw = [{"type": "production", "field": "resources.wood"}]
    current = {"resources": {"wood": 100}}
    nxt = {"resources": {"wood": 100000}}
    assert _check_resource_relations(current, nxt, raw) == []


def test_check_production_relation_non_numeric_field_is_skipped():
    raw = [{"type": "production", "field": "resources.wood", "amount_per_step": 10}]
    current = {"resources": {"wood": "abundant"}}
    nxt = {"resources": {"wood": "scarce"}}
    assert _check_resource_relations(current, nxt, raw) == []


def test_check_transfer_relation_unchanged_by_production_addition():
    """回归：混合声明 transfer + production 两条关系时，transfer 的
    检查结果形状（无 `kind`/`type` key）保持阶段十六上线时完全一致。"""
    raw = [
        {"type": "transfer", "from": "cash", "to": "inventory.value"},
        {"type": "production", "field": "resources.wood", "amount_per_step": 10},
    ]
    current = {"cash": 1000, "inventory": {"value": 0}, "resources": {"wood": 100}}
    nxt = {"cash": 900, "inventory": {"value": 20}, "resources": {"wood": 110}}
    violations = _check_resource_relations(current, nxt, raw)
    assert violations == [
        {
            "from": "cash",
            "to": "inventory.value",
            "delta_from": -100,
            "delta_to": 20,
            "checked_by": "diff",
        }
    ]


# ---------------------------------------------------------------------------
# engine.advance() 端到端集成测试
# ---------------------------------------------------------------------------


def test_advance_records_production_violation_when_rate_not_matched(tmp_path, monkeypatch):
    """production 关系声明了 amount_per_step，这一步实际产出明显偏离时，
    应该记入 `next_state.relation_violations`，且不修改任何数值、不
    拒绝推进。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={"resources": {"wood": 100}}, options=[],
        settings={
            "resource_relations": [
                {
                    "type": "production",
                    "field": "resources.wood",
                    "amount_per_step": 10,
                    "source_line_id": "line_forestry",
                }
            ]
        },
    )

    step_step = _FakeStep("step")

    class FakeStoreForAdvance:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([step_step])

    class FakeRunnerForAdvance:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            # 声明每步产出 10，实际只产出了 1——明显偏离。
            result_file = _write_result_file(
                tmp_path, "advance_result.json",
                {
                    "next_summary": "伐木进度缓慢",
                    "narrative": "今天雨天，伐木效率很低",
                    "next_vars": {"resources": {"wood": 101}},
                    "options": [],
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)

    next_state = engine_mod.advance(
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )

    # 不修改任何数值：next_vars 原样落盘。
    assert next_state.vars["resources"]["wood"] == 101
    assert next_state.relation_violations == [
        {
            "kind": "production",
            "field": "resources.wood",
            "amount_per_step": 10.0,
            "actual_delta": 1,
        }
    ]

    store = SimStore.for_root(data_dir, manifest.sim_id)
    history = store.load_history()
    assert history[-1].relation_violations == next_state.relation_violations


def test_advance_ignores_production_without_declared_rate(tmp_path, monkeypatch):
    """未声明 amount_per_step 的 production 关系不应该产生任何
    relation_violations 记录，哪怕这一步产出很大。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={"resources": {"wood": 100}}, options=[],
        settings={
            "resource_relations": [
                {"type": "production", "field": "resources.wood"}
            ]
        },
    )

    step_step = _FakeStep("step")

    class FakeStoreForAdvance:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([step_step])

    class FakeRunnerForAdvance:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            result_file = _write_result_file(
                tmp_path, "advance_result.json",
                {
                    "next_summary": "大丰收",
                    "narrative": "今天效率极高",
                    "next_vars": {"resources": {"wood": 999}},
                    "options": [],
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)

    next_state = engine_mod.advance(
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )

    assert next_state.vars["resources"]["wood"] == 999
    assert next_state.relation_violations == []
