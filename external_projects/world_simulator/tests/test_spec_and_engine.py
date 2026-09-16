"""tests/test_spec_and_engine.py — spec_generator / engine 单元测试。

阶段一"意图→提案→确认→推进3步→查看历史"这条主链路里依赖 LLM 的部分
（`generate_scenario` / `advance_step` 两个 workflow）用 monkeypatch 打桩：
不真的调用 LLM，只验证"engine.py 传给 workflow 引擎的 skill_name/inputs
是否符合约定" + "workflow 结果如何被解析回 SimState/ScenarioDraft 并
落盘"这条链路本身是对的，避免单测依赖网络/API key。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import world_simulator.engine as engine_mod
import world_simulator.spec_generator as spec_mod
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


def test_generate_scenario_binds_skill_and_parses_draft(tmp_path, monkeypatch):
    draft_step = _FakeStep("draft")
    fake_wf = _FakeWorkflow([draft_step])

    class FakeStore:
        def __init__(self, root):
            self.root = root

        def load(self, name):
            assert name == "generate_scenario"
            return fake_wf

    captured = {}

    class FakeRunner:
        def __init__(self, cfg):
            self.cfg = cfg

        def run(self, wf, inputs):
            captured["wf"] = wf
            captured["inputs"] = inputs
            result_file = _write_result_file(
                tmp_path,
                "scenario.json",
                {
                    "title": "毕业生的选择",
                    "summary": "刚毕业，正在找工作",
                    "vars": {"age": 22},
                    "options": [{"id": "a", "label": "读研", "description": "继续深造"}],
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="draft", status=_FakeStatus("done"), result_file=result_file)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    draft = spec_mod.generate_scenario(
        cfg=object(), workspace_root=tmp_path, template="life_sim", intent="模拟一个刚毕业的人生"
    )

    assert draft_step.skill_name == "life-sim-template"
    assert captured["inputs"] == {"intent": "模拟一个刚毕业的人生"}
    assert draft.title == "毕业生的选择"
    assert draft.vars["age"] == 22
    assert draft.options[0].id == "a"


def test_create_and_advance_simulation_end_to_end(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    # ── 打桩 generate_scenario 使用的 workflow 引擎 ──
    draft_step = _FakeStep("draft")

    class FakeStoreForScenario:
        def __init__(self, root):
            pass

        def load(self, name):
            assert name == "generate_scenario"
            return _FakeWorkflow([draft_step])

    class FakeRunnerForScenario:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            result_file = _write_result_file(
                tmp_path,
                "scenario_result.json",
                {
                    "title": "毕业生的选择",
                    "summary": "刚毕业，正在找工作",
                    "vars": {"age": 22, "stage": "求职"},
                    "options": [
                        {"id": "grad_school", "label": "读研", "description": "继续深造"},
                        {"id": "job", "label": "工作", "description": "直接就业"},
                    ],
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="draft", status=_FakeStatus("done"), result_file=result_file)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForScenario)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForScenario)

    manifest = engine_mod.create_simulation(
        cfg=object(),
        workspace_root=workspace_root,
        data_dir=data_dir,
        template="life_sim",
        intent="模拟一个刚毕业的人生",
    )
    assert manifest.template == "life_sim"
    assert manifest.current_step == 0

    store = SimStore.for_root(data_dir, manifest.sim_id)
    state0 = store.load_current_state()
    assert state0.step == 0
    assert len(state0.options) == 2

    # ── 打桩 advance_step 使用的 workflow 引擎 ──
    step_step = _FakeStep("step")

    class FakeStoreForAdvance:
        def __init__(self, root):
            pass

        def load(self, name):
            assert name == "advance_step"
            return _FakeWorkflow([step_step])

    class FakeRunnerForAdvance:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            assert inputs["chosen_option_json"] == json.dumps(
                {"id": "grad_school", "label": "读研", "description": "继续深造"},
                ensure_ascii=False,
            )
            result_file = _write_result_file(
                tmp_path,
                "advance_result.json",
                {
                    "next_summary": "已经入学读研",
                    "narrative": "顺利通过了复试",
                    "next_vars": {"age": 23, "stage": "研一"},
                    "options": [],
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)

    next_state = engine_mod.advance(
        cfg=object(),
        workspace_root=workspace_root,
        data_dir=data_dir,
        sim_id=manifest.sim_id,
        choice_option_id="grad_school",
    )

    assert step_step.skill_name == "life-sim-template"
    assert next_state.step == 1
    assert next_state.summary == "已经入学读研"
    assert next_state.vars["stage"] == "研一"

    history = store.load_history()
    assert [s.step for s in history] == [0, 1]
    assert history[0].chosen_option_id == "grad_school"
    assert history[0].chosen_by == "user"

    updated_manifest = store.load_manifest()
    assert updated_manifest.current_step == 1


def test_advance_rejects_unknown_option(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    store = SimStore.for_root(data_dir, "life_sim_x")
    from world_simulator.state_model import ChoiceOption, SimManifest, SimState
    from world_simulator.store import now_iso

    ts = now_iso()
    store.save_manifest(
        SimManifest(
            sim_id="life_sim_x", template="life_sim", intent="i", title="t",
            created_at=ts, updated_at=ts,
        )
    )
    store.append_state(
        SimState(step=0, summary="s", options=[ChoiceOption(id="a", label="A")])
    )

    import pytest

    with pytest.raises(engine_mod.SimEngineError):
        engine_mod.advance(
            cfg=object(), workspace_root=tmp_path, data_dir=data_dir,
            sim_id="life_sim_x", choice_option_id="not_exist",
        )
