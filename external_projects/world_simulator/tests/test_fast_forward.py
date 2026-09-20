"""tests/test_fast_forward.py — engine.fast_forward() 单元测试。

设计依据：`next_doc/world_simulator_event_driven_engine_and_full_
architecture_plan.md` 2.1 节（第一批，Event-Driven 决策点引擎 +
Observer View 完整版）。

同 `test_autopilot.py` 的既有做法：不真的调用 LLM，用 monkeypatch 打桩
`advance_step` workflow，让打桩的结果按调用次序返回一串不同的 payload
（`_patch_sequenced_advance_step_workflow()`），驱动 `fast_forward()`
在"命中信号就停、没命中就继续"这条外层循环上的行为。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from world_simulator import autopilot as ap_mod
from world_simulator.engine.advance import FastForwardResult, fast_forward
from world_simulator.state_model import ChoiceOption, SimManifest, SimState
from world_simulator.store import SimStore, now_iso


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


def _make_sim(data_dir: Path, sim_id: str, *, pilot_mode="manual", autopilot=None, settings=None):
    store = SimStore.for_root(data_dir, sim_id)
    ts = now_iso()
    store.save_manifest(
        SimManifest(
            sim_id=sim_id, template="life_sim", intent="i", title="t",
            created_at=ts, updated_at=ts, pilot_mode=pilot_mode, autopilot=autopilot or {},
            settings=settings or {},
        )
    )
    store.append_state(SimState(step=0, summary="s0", vars={"age": 20}, options=[]))
    return store


def _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, payloads: list, capture: dict):
    """让每一次 `advance()` 调用依次返回 `payloads` 里的下一项；如果
    调用次数超过 `payloads` 长度，重复最后一项（避免测试因为多调用
    一次就直接报错，同时又能在断言里精确核实调用了几次）。
    """
    step_step = _FakeStep("step")
    capture["calls"] = 0
    capture["inputs_list"] = []

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            assert name == "advance_step"
            return _FakeWorkflow([step_step])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            idx = min(capture["calls"], len(payloads) - 1)
            payload = payloads[idx]
            capture["calls"] += 1
            capture["inputs_list"].append(inputs)
            result_file = _write_result_file(
                tmp_path, f"advance_result_{capture['calls']}.json", payload
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)


def _default_payload(step_no: int, **overrides) -> dict:
    payload = {
        "next_summary": f"第 {step_no} 步的摘要",
        "narrative": f"第 {step_no} 步发生的事",
        "next_vars": {"age": 20 + step_no},
        "options": [],
    }
    payload.update(overrides)
    return payload


# ── 命中 major_decision 立即停止 ─────────────────────────────────────


def test_fast_forward_stops_on_major_decision(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1")
    capture: dict = {}
    payloads = [
        _default_payload(1),
        _default_payload(2),
        _default_payload(3, major_decision=True),
        _default_payload(4),  # 不应该被调用到
    ]
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, payloads, capture)

    result = fast_forward(object(), tmp_path, data_dir, "sim1", max_steps=10)

    assert isinstance(result, FastForwardResult)
    assert result.stop_reason == "major_decision"
    assert result.steps_run == 3
    assert capture["calls"] == 3  # 命中之后不应该再多调用一次
    assert result.final_state.step == 3
    assert result.final_state.major_decision is True
    assert [s.step for s in result.skipped_states] == [1, 2]
    assert "跳过了 2 步" in result.summary_text
    assert "第 1 步的摘要" in result.summary_text
    assert "第 2 步的摘要" in result.summary_text


# ── 命中非空 options 停止 ────────────────────────────────────────────


def test_fast_forward_stops_on_options(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1")
    capture: dict = {}
    payloads = [
        _default_payload(1),
        _default_payload(
            2,
            options=[{"id": "a", "label": "选项A"}, {"id": "b", "label": "选项B"}],
        ),
        _default_payload(3),  # 不应该被调用到
    ]
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, payloads, capture)

    result = fast_forward(object(), tmp_path, data_dir, "sim1", max_steps=10)

    assert result.stop_reason == "options"
    assert result.steps_run == 2
    assert capture["calls"] == 2
    assert result.final_state.step == 2
    assert len(result.final_state.options) == 2
    assert [s.step for s in result.skipped_states] == [1]


# ── 达到 max_steps 都没有命中时停止并生成摘要 ────────────────────────


def test_fast_forward_stops_on_max_steps(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1")
    capture: dict = {}
    payloads = [_default_payload(i) for i in range(1, 6)]
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, payloads, capture)

    result = fast_forward(object(), tmp_path, data_dir, "sim1", max_steps=5)

    assert result.stop_reason == "max_steps"
    assert result.steps_run == 5
    assert capture["calls"] == 5
    assert result.final_state.step == 5
    # max_steps 情况下，最后一步本身也被计入"跳过"列表展示
    # （它没有触发任何停止信号，和其它步骤一样被折叠）。
    assert [s.step for s in result.skipped_states] == [1, 2, 3, 4, 5]
    assert "跳过了 5 步" in result.summary_text


def test_fast_forward_rejects_invalid_max_steps(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1")
    with pytest.raises(ValueError):
        fast_forward(object(), tmp_path, data_dir, "sim1", max_steps=0)


# ── 跳过的每一步都能在历史里完整读到（没有历史空洞） ─────────────────


def test_fast_forward_leaves_no_history_gap(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(data_dir, "sim1")
    capture: dict = {}
    payloads = [_default_payload(1), _default_payload(2), _default_payload(3, major_decision=True)]
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, payloads, capture)

    fast_forward(object(), tmp_path, data_dir, "sim1", max_steps=10)

    store = SimStore.for_root(data_dir, "sim1")
    history = store.load_history()
    # step 0（初始状态）+ 三次快进调用 = 4 条完整历史记录。
    assert [s.step for s in history] == [0, 1, 2, 3]
    assert history[1].summary == "第 1 步的摘要"
    assert history[2].summary == "第 2 步的摘要"
    assert history[3].summary == "第 3 步的摘要"


# ── 未开启 autopilot_fast_forward（默认）时，run_batch_autopilot()
#    行为与之前完全一致（逐步调用 run_autopilot_step，不走 fast_forward）


def test_autopilot_fast_forward_default_off_keeps_existing_behavior(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim(
        data_dir, "sim1", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent"},
        settings={},  # 未声明 autopilot_fast_forward，默认 False
    )
    capture: dict = {}
    payloads = [
        _default_payload(1, chosen_option_id=None),
        _default_payload(2, chosen_option_id=None),
    ]
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, payloads, capture)

    results = ap_mod.run_batch_autopilot(object(), tmp_path, data_dir, steps=2)

    assert len(results) == 2
    assert all(r.ok for r in results)
    assert results[-1].next_step == 2
    # 默认路径每一步都是一次独立调用（不是一次 fast_forward 调用）。
    assert capture["calls"] == 2


# ── 开启 autopilot_fast_forward 后，run_batch_autopilot() 改为调用
#    一次 fast_forward()，命中 major_decision 时按 review_mode 暂停


def test_autopilot_fast_forward_enabled_uses_fast_forward_and_pauses_on_major_decision(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    _make_sim(
        data_dir, "sim1", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "pause_on_major_decision"},
        settings={"autopilot_fast_forward": True},
    )
    capture: dict = {}
    payloads = [
        _default_payload(1),
        _default_payload(2, major_decision=True),
        _default_payload(3),  # 不应该被调用到
    ]
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, payloads, capture)

    results = ap_mod.run_batch_autopilot(object(), tmp_path, data_dir, steps=5)

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].next_step == 2
    assert results[0].paused_for_review is True
    # 一次 fast_forward 内部循环，不是外层 for 循环逐步调用。
    assert capture["calls"] == 2

    manifest = SimStore.for_root(data_dir, "sim1").load_manifest()
    assert manifest.status == "paused"


def test_autopilot_fast_forward_enabled_does_not_stop_on_options(tmp_path, monkeypatch):
    """自动挡快进不应该因为出现候选 options 就停下——`stop_on_options`
    固定传 `False`，自动挡的意义就是不需要为候选选项停下来等真人，
    是否暂停完全交给 `stop_on_major_decision` + `review_mode` 判断。
    """
    data_dir = tmp_path / "data"
    _make_sim(
        data_dir, "sim1", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent"},
        settings={"autopilot_fast_forward": True},
    )
    capture: dict = {}
    payloads = [
        # 第 1 步：当前状态（初始 step0）还没有任何候选选项，这一步
        # 只是产出下一批 options，不涉及"选择"。
        _default_payload(1, options=[{"id": "a", "label": "选项A"}]),
        # 第 2 步：当前状态（第 1 步产出）有候选选项，autopilot 的
        # decision_context 应该驱动 LLM 从里面选一个，而不是让
        # fast_forward 因为出现了 options 就停下来。
        _default_payload(2, chosen_option_id="a", chosen_reason="r"),
    ]
    _patch_sequenced_advance_step_workflow(monkeypatch, tmp_path, payloads, capture)

    results = ap_mod.run_batch_autopilot(object(), tmp_path, data_dir, steps=2)

    assert results[0].ok is True
    assert results[0].next_step == 2
    assert capture["calls"] == 2

    manifest = SimStore.for_root(data_dir, "sim1").load_manifest()
    assert manifest.status == "active"  # 没有因为 options 出现而暂停
