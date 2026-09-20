"""tests/test_split_creation_calls.py — 第五轮方案 5.5 节（阶段三十四
第六批，`next_doc/world_simulator_decision_engine_round2_gap_analysis_
plan.md`）：`settings.split_creation_calls == True` 时，
`spec_generator.generate_scenario()` 应该调用 `world_builder` +
`causal_space_builder` 两个 workflow 而不是单一的 `generate_scenario`，
并把两次调用的结果正确合并成同一份 `ScenarioDraft`；默认（未声明该
设置）仍然走原来的单次调用路径，不受影响（该默认路径已经被
`test_spec_and_engine.py` 覆盖，这里不重复）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import world_simulator.spec_generator as spec_mod


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


def test_generate_scenario_split_mode_calls_world_builder_then_causal_space_builder(
    tmp_path, monkeypatch
):
    workspace_root = tmp_path / "ws"

    world_step = _FakeStep("world_builder")
    causal_step = _FakeStep("causal_space_builder")

    calls: list[str] = []

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            calls.append(f"load:{name}")
            if name == "world_builder":
                return _FakeWorkflow([world_step])
            if name == "causal_space_builder":
                return _FakeWorkflow([causal_step])
            raise AssertionError(f"拆分模式不应该加载 {name!r}")

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            step_id = wf.steps[0].id
            calls.append(f"run:{step_id}")
            if step_id == "world_builder":
                assert inputs["intent"] == "开一家咖啡馆"
                result_file = _write_result_file(
                    tmp_path,
                    "world_builder_result.json",
                    {
                        "title": "咖啡馆创业",
                        "summary": "刚辞职，准备开一家社区咖啡馆",
                        "vars": {"cash": 200000, "stage": "筹备中"},
                        "time_label": "起点",
                    },
                )
                return SimpleNamespace(
                    status="done",
                    step_results=[
                        SimpleNamespace(
                            step_id="world_builder", status=_FakeStatus("done"), result_file=result_file
                        )
                    ],
                )
            elif step_id == "causal_space_builder":
                # causal_space_builder 应该能看到 world_builder 的输出被
                # 喂进了 built_* 系列输入，且不应该重新收到"生成 title/
                # summary"之类的指令输入之外的额外内容。
                assert inputs["built_title"] == "咖啡馆创业"
                assert json.loads(inputs["built_vars_json"]) == {
                    "cash": 200000, "stage": "筹备中",
                }
                result_file = _write_result_file(
                    tmp_path,
                    "causal_space_builder_result.json",
                    {
                        "options": [
                            {"id": "rent_shop", "label": "租下临街店面", "description": "人流量大但租金高"}
                        ],
                        "causal_lines": [{"id": "market", "label": "市场线"}],
                    },
                )
                return SimpleNamespace(
                    status="done",
                    step_results=[
                        SimpleNamespace(
                            step_id="causal_space_builder", status=_FakeStatus("done"), result_file=result_file
                        )
                    ],
                )
            raise AssertionError(f"不认识的 step_id={step_id!r}")

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    draft = spec_mod.generate_scenario(
        cfg=object(),
        workspace_root=workspace_root,
        template="life_sim",
        intent="开一家咖啡馆",
        settings={"split_creation_calls": True},
    )

    assert calls == [
        "load:world_builder", "run:world_builder",
        "load:causal_space_builder", "run:causal_space_builder",
    ]
    # 世界状态字段来自 world_builder。
    assert draft.title == "咖啡馆创业"
    assert draft.summary == "刚辞职，准备开一家社区咖啡馆"
    assert draft.vars == {"cash": 200000, "stage": "筹备中"}
    # 因果/决策字段来自 causal_space_builder。
    assert [o.id for o in draft.options] == ["rent_shop"]
    assert draft.causal_lines == [{"id": "market", "label": "市场线"}]


def test_generate_scenario_default_mode_does_not_call_split_workflows(tmp_path, monkeypatch):
    """未声明 `split_creation_calls`（或显式为 False）时，仍然只应该
    加载单一的 `generate_scenario` workflow，不触碰 `world_builder`/
    `causal_space_builder`。"""
    workspace_root = tmp_path / "ws"
    draft_step = _FakeStep("draft")

    calls: list[str] = []

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            calls.append(f"load:{name}")
            if name == "generate_scenario":
                return _FakeWorkflow([draft_step])
            raise AssertionError(f"默认模式不应该加载 {name!r}")

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            calls.append("run:draft")
            result_file = _write_result_file(
                tmp_path,
                "draft_result.json",
                {"title": "t", "summary": "s", "vars": {}, "options": []},
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
        cfg=object(), workspace_root=workspace_root, template="life_sim", intent="随便试试",
    )

    assert calls == ["load:generate_scenario", "run:draft"]
    assert draft.title == "t"
