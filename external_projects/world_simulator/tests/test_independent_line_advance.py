"""tests/test_independent_line_advance.py — 多尺度因果线真正独立推进
（阶段三十六第三批，`next_doc/world_simulator_event_driven_engine_and_
full_architecture_plan.md` 2.3 节）单元测试。

按方案原文的验收点：
1. 两条 `advance_every_n_steps` 不同的线，只有到点的线真正发起调用。
2. `owned_vars` 重叠时报错阻止推进。
3. 某一步没有任何线到点时的空推进（全局 `step` 依然 +1，但没有任何
   LLM 调用）。
4. 跨线读取用的是快照（本次调用开始前的 `vars`），不是本步中间态。

同 `test_spec_and_engine.py` 一贯的打桩方式：monkeypatch
`mini_agent.workflow.{store,runner}`，不真的调用 LLM，只验证
"engine 传给 workflow 引擎的 inputs 是否符合约定" + "workflow 结果
如何被解析回 SimState/manifest.settings 并落盘"这条链路本身是对的。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import world_simulator.engine as engine_mod
from world_simulator.engine.errors import OwnedVarsOverlapError
from world_simulator.store import SimStore


class _FakeStatus:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeStep:
    def __init__(self, step_id: str) -> None:
        self.id = step_id
        self.skill_name = None


class _FakeWorkflow:
    def __init__(self, steps):
        self.steps = steps


def _write_result_file(tmp_path: Path, name: str, payload: dict) -> str:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _make_manifest(tmp_path, data_dir, *, causal_lines, vars_):
    return engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars=vars_, options=[],
        settings={"causal_lines": causal_lines, "independent_line_advance": True},
    )


def test_only_due_lines_are_called_when_cadence_differs(tmp_path, monkeypatch):
    """两条 `advance_every_n_steps` 不同的线（都从 `local_step=0`
    起步），第一次 `advance_lines()` 调用时两条线的 `local_step % n`
    都是 0，理应都到点；把其中一条的 `local_step` 手动设成 1（模拟
    "已经推进过一次，还没到下一个节点"）后再调用一次，应该只有
    `local_step % n == 0` 的那条线真正发起调用。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = _make_manifest(
        tmp_path, data_dir,
        causal_lines=[
            {"id": "fast_line", "label": "快线", "owned_vars": ["a"], "advance_every_n_steps": 1, "local_step": 0},
            {"id": "slow_line", "label": "慢线", "owned_vars": ["b"], "advance_every_n_steps": 3, "local_step": 1},
        ],
        vars_={"a": 1, "b": 2},
    )

    called_line_ids = []

    class FakeStoreForAdvance:
        def __init__(self, root):
            pass

        def load(self, name):
            assert name == "line_evolve"
            return _FakeWorkflow([_FakeStep("line_evolve")])

    class FakeRunnerForAdvance:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            called_line_ids.append(inputs["line_id"])
            result_file = _write_result_file(
                tmp_path, f"line_result_{inputs['line_id']}.json",
                {"summary": f"{inputs['line_id']} 推进了", "narrative": "n", "next_vars": {}},
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="line_evolve", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)

    next_state = engine_mod.advance_lines(
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )

    assert next_state.step == 1
    # slow_line 的 local_step=1，advance_every_n_steps=3，1 % 3 != 0，
    # 不到点；fast_line 的 local_step=0，0 % 1 == 0，到点。
    assert called_line_ids == ["fast_line"]

    store = SimStore.for_root(data_dir, manifest.sim_id)
    saved = store.load_manifest()
    lines_by_id = {line["id"]: line for line in saved.settings["causal_lines"]}
    assert lines_by_id["fast_line"]["local_step"] == 1
    assert lines_by_id["slow_line"]["local_step"] == 1  # 未被推进，原样保留


def test_owned_vars_overlap_raises_before_any_call(tmp_path, monkeypatch):
    """两条线声明了重叠的 `owned_vars` 字段时，`advance_lines()`
    应该在发起任何调用之前直接报错阻止，不应该有任何一条线被推进。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = _make_manifest(
        tmp_path, data_dir,
        causal_lines=[
            {"id": "line_x", "owned_vars": ["shared_field"]},
            {"id": "line_y", "owned_vars": ["shared_field", "other_field"]},
        ],
        vars_={"shared_field": 1, "other_field": 2},
    )

    called = []

    class FakeStoreForAdvance:
        def __init__(self, root):
            pass

        def load(self, name):
            called.append(name)
            return _FakeWorkflow([_FakeStep("line_evolve")])

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)

    with pytest.raises(OwnedVarsOverlapError):
        engine_mod.advance_lines(
            cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
        )
    assert called == []  # 校验失败在任何 workflow 加载之前就该短路

    # 全局 step 不应该因为校验失败而前进。
    store = SimStore.for_root(data_dir, manifest.sim_id)
    assert store.load_manifest().current_step == 0


def test_empty_step_when_no_line_is_due(tmp_path, monkeypatch):
    """所有线本步都不到点（或压根没声明 `owned_vars`）时，
    `advance_lines()` 仍然正常落盘、全局 step +1，但不应该发起任何
    workflow 调用。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = _make_manifest(
        tmp_path, data_dir,
        causal_lines=[
            {"id": "no_owned_vars_line", "label": "未参与独立推进的线"},
            {"id": "slow_line", "owned_vars": ["c"], "advance_every_n_steps": 5, "local_step": 2},
        ],
        vars_={"c": 1},
    )

    called = []

    class FakeStoreForAdvance:
        def __init__(self, root):
            called.append("store_loaded")
            return None

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)

    next_state = engine_mod.advance_lines(
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )

    assert next_state.step == 1
    assert next_state.line_updates == {}
    assert "维持现状" in next_state.summary
    assert called == []  # WorkflowStore 完全没被实例化，没有发起任何调用

    store = SimStore.for_root(data_dir, manifest.sim_id)
    saved = store.load_manifest()
    lines_by_id = {line["id"]: line for line in saved.settings["causal_lines"]}
    assert lines_by_id["slow_line"]["local_step"] == 2  # 未到点，原样保留


def test_cross_line_reads_snapshot_not_mid_batch_intermediate_state(tmp_path, monkeypatch):
    """同一批次内，两条线都到点时，后处理的线看到的应该是"上一次全局
    同步点"的快照（调用前的 `vars`），不是先处理的那条线刚算出来的
    中间结果——用 `line_owned_vars_json` 之外、prompt 里给出的
    `current_summary` 验证：两条线拿到的 `current_summary` 应该完全
    一致，且等于调用前的 `current.summary`，不会因为处理顺序不同而
    变化。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = _make_manifest(
        tmp_path, data_dir,
        causal_lines=[
            {"id": "line_a", "owned_vars": ["a"]},
            {"id": "line_b", "owned_vars": ["b"]},
        ],
        vars_={"a": "a0", "b": "b0"},
    )

    seen_inputs = []

    class FakeStoreForAdvance:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([_FakeStep("line_evolve")])

    class FakeRunnerForAdvance:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            seen_inputs.append(dict(inputs))
            line_id = inputs["line_id"]
            # line_a 被推进时把 a 改成 "a1"；如果 line_b 读到的是
            # "本批次中间态"而不是快照，它的 line_owned_vars_json
            # 就不会包含被污染的字段（b 本身不受影响），但更关键的是
            # current_summary 不应该因为 line_a 先跑完而变化。
            result_file = _write_result_file(
                tmp_path, f"line_result_{line_id}.json",
                {"summary": f"{line_id} 推进了", "narrative": "", "next_vars": {line_id[-1]: f"{line_id[-1]}1"}},
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="line_evolve", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)

    next_state = engine_mod.advance_lines(
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )

    assert len(seen_inputs) == 2
    summaries = {inp["current_summary"] for inp in seen_inputs}
    assert summaries == {"s"}  # 两条线看到的都是调用前的 summary，没有互相污染

    owned_snapshots = {inp["line_id"]: json.loads(inp["line_owned_vars_json"]) for inp in seen_inputs}
    assert owned_snapshots["line_a"] == {"a": "a0"}
    assert owned_snapshots["line_b"] == {"b": "b0"}

    assert next_state.vars == {"a": "a1", "b": "b1"}
