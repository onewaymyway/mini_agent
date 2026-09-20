"""tests/test_split_decision_calls.py — 4.12 节第三步（阶段三十三
第八批，`next_doc/
world_simulator_potential_causal_space_and_decision_engine_plan.md`）：
`manifest.settings.split_decision_calls == True` 时，`advance()` 应该
调用 `world_evolve` + `decision_generate` 两个 workflow 而不是单一的
`advance_step`，并把两次调用的结果正确合并成同一份 `next_state`；
默认（未声明该设置）仍然走原来的单次调用路径，不受影响（该默认路径
已经被 `test_spec_and_engine.py` 覆盖，这里不重复）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import world_simulator.engine as engine_mod
from world_simulator.engine.management import update_settings
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


def test_advance_split_mode_calls_world_evolve_then_decision_generate(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={"age": 22, "stage": "求职"}, options=[],
    )
    update_settings(data_dir, manifest.sim_id, split_decision_calls=True)

    evolve_step = _FakeStep("world_evolve")
    decide_step = _FakeStep("decision_generate")

    calls: list[str] = []

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            calls.append(f"load:{name}")
            if name == "world_evolve":
                return _FakeWorkflow([evolve_step])
            if name == "decision_generate":
                return _FakeWorkflow([decide_step])
            raise AssertionError(f"拆分模式不应该加载 {name!r}")

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            step_id = wf.steps[0].id
            calls.append(f"run:{step_id}")
            if step_id == "world_evolve":
                # 拆分模式下，world_evolve 这一次调用不应该被要求/看到
                # 任何 options 相关的软上限提示之外的"生成选项"指令——
                # 这里只做最基本的契约校验：能拿到 current_vars_json。
                assert json.loads(inputs["current_vars_json"]) == {"age": 22, "stage": "求职"}
                result_file = _write_result_file(
                    tmp_path,
                    "world_evolve_result.json",
                    {
                        "next_summary": "步入职场",
                        "narrative": "拿到了第一份 offer",
                        "next_vars": {"age": 23, "stage": "在职"},
                        "key_drivers": ["拿到心仪 offer"],
                        "tree_updates": [
                            {"line_id": "career", "confirmed_branch": "fast_track"}
                        ],
                    },
                )
                return SimpleNamespace(
                    status="done",
                    step_results=[
                        SimpleNamespace(
                            step_id="world_evolve", status=_FakeStatus("done"), result_file=result_file
                        )
                    ],
                )
            elif step_id == "decision_generate":
                # decision_generate 应该能看到 world_evolve 的输出被喂
                # 进了 evolved_* 系列输入。
                assert json.loads(inputs["evolved_vars_json"]) == {"age": 23, "stage": "在职"}
                assert inputs["evolved_summary"] == "步入职场"
                assert json.loads(inputs["evolved_tree_updates_json"]) == [
                    {"line_id": "career", "confirmed_branch": "fast_track"}
                ]
                result_file = _write_result_file(
                    tmp_path,
                    "decision_generate_result.json",
                    {
                        "options": [
                            {"id": "stay", "label": "留在现岗位", "description": "继续积累经验"}
                        ],
                        "decision_reason": "职业转型窗口正在打开",
                    },
                )
                return SimpleNamespace(
                    status="done",
                    step_results=[
                        SimpleNamespace(
                            step_id="decision_generate", status=_FakeStatus("done"), result_file=result_file
                        )
                    ],
                )
            raise AssertionError(f"不认识的 step_id={step_id!r}")

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    next_state = engine_mod.advance(
        cfg=object(),
        workspace_root=workspace_root,
        data_dir=data_dir,
        sim_id=manifest.sim_id,
    )

    # 两个 workflow 都被加载和调用过，且顺序是先 world_evolve 再
    # decision_generate（decision_generate 依赖 world_evolve 的输出）。
    assert calls == [
        "load:world_evolve", "run:world_evolve",
        "load:decision_generate", "run:decision_generate",
    ]
    assert evolve_step.skill_name == "life-sim-template"
    assert decide_step.skill_name == "life-sim-template"

    # 世界状态类字段来自 world_evolve。
    assert next_state.summary == "步入职场"
    assert next_state.narrative == "拿到了第一份 offer"
    assert next_state.vars == {"age": 23, "stage": "在职"}
    assert next_state.key_drivers == ["拿到心仪 offer"]

    # 决策类字段来自 decision_generate。
    assert len(next_state.options) == 1
    assert next_state.options[0].label == "留在现岗位"
    assert next_state.decision_reason == "职业转型窗口正在打开"

    # 4.10 节 DecisionOpportunity 容器应该照常基于合并后的 next_state 构造。
    assert next_state.decision_opportunity is not None
    assert next_state.decision_opportunity["decision_reason"] == "职业转型窗口正在打开"

    store = SimStore.for_root(data_dir, manifest.sim_id)
    history = store.load_history()
    assert [s.step for s in history] == [0, 1]


def test_advance_default_mode_does_not_touch_split_workflows(tmp_path, monkeypatch):
    """未声明 `split_decision_calls`（或显式为 False）时，应该只加载/
    调用 `advance_step`，完全不接触 `world_evolve`/`decision_generate`
    ——确认默认行为向后兼容，不会因为新增了拆分路径而误触发。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={"age": 22}, options=[],
    )

    step_step = _FakeStep("step")
    calls: list[str] = []

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            calls.append(name)
            assert name == "advance_step"
            return _FakeWorkflow([step_step])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            result_file = _write_result_file(
                tmp_path,
                "advance_result.json",
                {"next_summary": "s2", "narrative": "n2", "next_vars": {"age": 23}, "options": []},
            )
            return SimpleNamespace(
                status="done",
                step_results=[
                    SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)
                ],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    engine_mod.advance(
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )

    assert calls == ["advance_step"]
