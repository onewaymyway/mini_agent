"""tests/test_autopilot.py — autopilot 单元测试。

同样不真的调用 LLM：用 monkeypatch 打桩 workflow 引擎，让打桩的
`advance_step` 结果里带上 `chosen_option_id`/`chosen_reason`/
`major_decision`，验证 engine.advance() 的自动挡分支解析是否正确，
以及 autopilot.py 的批量推进/review_mode 暂停逻辑是否符合预期。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from world_simulator import autopilot as ap_mod
from world_simulator import engine as engine_mod
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


def _make_sim_with_options(data_dir: Path, sim_id: str, *, pilot_mode="manual", autopilot=None):
    store = SimStore.for_root(data_dir, sim_id)
    ts = now_iso()
    store.save_manifest(
        SimManifest(
            sim_id=sim_id, template="life_sim", intent="i", title="t",
            created_at=ts, updated_at=ts, pilot_mode=pilot_mode, autopilot=autopilot or {},
        )
    )
    store.append_state(
        SimState(
            step=0, summary="s0",
            vars={"age": 20},
            options=[
                ChoiceOption(id="safe", label="稳妥选项"),
                ChoiceOption(id="risky", label="激进选项"),
            ],
        )
    )
    return store


def _patch_advance_step_workflow(monkeypatch, tmp_path, response_payload, capture: dict):
    step_step = _FakeStep("step")

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
            capture["inputs"] = inputs
            result_file = _write_result_file(tmp_path, "advance_result.json", response_payload)
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)
    return step_step


def test_autopilot_records_llm_chosen_option(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim_with_options(
        data_dir, "sim1", pilot_mode="autopilot",
        autopilot={"enabled": True, "principles": ["优先稳定"], "risk_preference": "conservative",
                   "review_mode": "silent"},
    )
    capture: dict = {}
    _patch_advance_step_workflow(
        monkeypatch, tmp_path,
        {
            "next_summary": "选择了稳妥方向",
            "narrative": "代理选择了稳妥选项",
            "next_vars": {"age": 21},
            "options": [],
            "chosen_option_id": "safe",
            "chosen_reason": "符合保守风险偏好",
        },
        capture,
    )

    next_state = ap_mod.run_autopilot_step(object(), tmp_path, data_dir, "sim1")

    assert next_state.step == 1
    assert "决策者画像" in capture["inputs"]["decision_context"] or "代理" in capture["inputs"]["decision_context"]
    assert json.loads(capture["inputs"]["current_options_json"])[0]["id"] == "safe"

    store = SimStore.for_root(data_dir, "sim1")
    history = store.load_history()
    assert history[0].chosen_option_id == "safe"
    assert history[0].chosen_by == "autopilot"
    assert history[0].chosen_reason == "符合保守风险偏好"


def test_autopilot_rejects_hallucinated_option_id(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim_with_options(
        data_dir, "sim1", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent"},
    )
    capture: dict = {}
    _patch_advance_step_workflow(
        monkeypatch, tmp_path,
        {
            "next_summary": "x", "narrative": "x", "next_vars": {}, "options": [],
            "chosen_option_id": "not_a_real_option",
        },
        capture,
    )

    with pytest.raises(engine_mod.SimEngineError):
        ap_mod.run_autopilot_step(object(), tmp_path, data_dir, "sim1")


def test_autopilot_disabled_raises(tmp_path):
    data_dir = tmp_path / "data"
    _make_sim_with_options(data_dir, "sim1", pilot_mode="manual")
    with pytest.raises(ap_mod.AutopilotDisabledError):
        ap_mod.run_autopilot_step(object(), tmp_path, data_dir, "sim1")


def test_pause_on_major_decision(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim_with_options(
        data_dir, "sim1", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "pause_on_major_decision"},
    )
    capture: dict = {}
    _patch_advance_step_workflow(
        monkeypatch, tmp_path,
        {
            "next_summary": "重大转折", "narrative": "x", "next_vars": {}, "options": [],
            "chosen_option_id": "risky", "chosen_reason": "r", "major_decision": True,
        },
        capture,
    )

    ap_mod.run_autopilot_step(object(), tmp_path, data_dir, "sim1")

    store = SimStore.for_root(data_dir, "sim1")
    manifest = store.load_manifest()
    assert manifest.status == "paused"


def test_batch_autopilot_skips_manual_instances(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim_with_options(data_dir, "manual1", pilot_mode="manual")
    _make_sim_with_options(
        data_dir, "auto1", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent"},
    )
    capture: dict = {}
    _patch_advance_step_workflow(
        monkeypatch, tmp_path,
        {
            "next_summary": "s1", "narrative": "n", "next_vars": {"age": 21}, "options": [],
            "chosen_option_id": "safe", "chosen_reason": "r",
        },
        capture,
    )

    results = ap_mod.run_batch_autopilot(object(), tmp_path, data_dir, steps=1)

    assert [r.sim_id for r in results] == ["auto1"]
    assert results[0].ok is True
    assert results[0].next_step == 1


def test_batch_autopilot_continues_after_single_failure(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _make_sim_with_options(
        data_dir, "auto_bad", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent"},
    )
    _make_sim_with_options(
        data_dir, "auto_good", pilot_mode="autopilot",
        autopilot={"enabled": True, "review_mode": "silent"},
    )

    step_step = _FakeStep("step")

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([step_step])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            if "auto_bad" in json.dumps(inputs, ensure_ascii=False) or inputs["title"] == "t" and False:
                pass
            # 用 inputs 里的 current_step/summary 无法区分是哪个 sim，改用一个
            # 模块级计数器：第一次调用（按 list_simulations 的顺序，"auto_bad"
            # 先于 "auto_good"）制造失败，第二次成功。
            call_count = getattr(FakeRunner, "_calls", 0)
            FakeRunner._calls = call_count + 1
            if call_count == 0:
                return SimpleNamespace(status="failed", step_results=[
                    SimpleNamespace(step_id="step", status=_FakeStatus("failed"), result_file=None, error="boom")
                ])
            result_file = _write_result_file(
                tmp_path, f"advance_result_{call_count}.json",
                {
                    "next_summary": "ok", "narrative": "ok", "next_vars": {}, "options": [],
                    "chosen_option_id": "safe", "chosen_reason": "r",
                },
            )
            return SimpleNamespace(status="done", step_results=[
                SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)
            ])

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    results = ap_mod.run_batch_autopilot(object(), tmp_path, data_dir, steps=1)

    by_id = {r.sim_id: r for r in results}
    assert by_id["auto_bad"].ok is False
    assert by_id["auto_good"].ok is True
