"""tests/test_multi_template.py — 阶段六：验证"新增模拟类型=新增一个
skill，不改引擎代码"这条设计假设。

本测试文件不对 `engine.py`/`spec_generator.py` 做任何改动，只是新增
一个 `template="group_evolution"` 的调用路径，复用已有的
`_skill_name_for_template()` 约定（模板名下划线转连字符 + `-template`
后缀），断言：

1. `spec_generator.generate_scenario(template="group_evolution", ...)`
   会把 `generate_scenario` workflow 的 `draft` 步骤 `skill_name` 绑定
   到 `group-evolution-template`（对应新增的 `skills/
   group-evolution-template/SKILL.md`），而不是硬编码的
   `life-sim-template`。
2. `engine.advance(template 隐含在 manifest 里, ...)` 同理把
   `advance_step` workflow 的 `step` 步骤绑定到
   `group-evolution-template`。
3. 一个实例内 `template` 字段全程保持为 `group_evolution`，不会被引擎
   悄悄改写/归一化成 `life_sim`。
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


def test_group_evolution_template_binds_correct_skill_for_scenario(tmp_path, monkeypatch):
    draft_step = _FakeStep("draft")

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([draft_step])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            result_file = _write_result_file(
                tmp_path, "scenario.json",
                {
                    "title": "一个初创社群的兴衰",
                    "summary": "刚成立的线上社群，成员 50 人",
                    "vars": {"group_type": "线上社群", "size": 50, "cohesion": 70},
                    "options": [
                        {"id": "expand", "label": "快速扩张", "description": "大量拉新"},
                        {"id": "consolidate", "label": "稳健巩固", "description": "先做好留存"},
                    ],
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
        cfg=object(), workspace_root=tmp_path, template="group_evolution",
        intent="模拟一个刚成立的线上社群的发展",
    )

    # 核心断言：skill_name 是按 template 动态推导出来的，
    # 不是 spec_generator.py 里硬编码的 life-sim-template。
    assert draft_step.skill_name == "group-evolution-template"
    assert draft.title == "一个初创社群的兴衰"
    assert draft.vars["group_type"] == "线上社群"


def test_group_evolution_template_end_to_end_create_and_advance(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    draft_step = _FakeStep("draft")

    class FakeStoreForScenario:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([draft_step])

    class FakeRunnerForScenario:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            result_file = _write_result_file(
                tmp_path, "scenario_result.json",
                {
                    "title": "初创公司的第一年",
                    "summary": "5 人团队，种子轮刚到账",
                    "vars": {"group_type": "创业公司", "size": 5, "resources": "6 个月 runway"},
                    "options": [
                        {"id": "hire_fast", "label": "快速招人", "description": "扩大团队抢时间"},
                        {"id": "stay_lean", "label": "保持精简", "description": "先把产品打磨好"},
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
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir,
        template="group_evolution", intent="模拟一家刚拿到种子轮的创业公司",
    )
    assert manifest.template == "group_evolution"

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
                    "next_summary": "团队扩张到 12 人",
                    "narrative": "快速招人后团队规模翻倍",
                    "next_vars": {"group_type": "创业公司", "size": 12, "resources": "4 个月 runway"},
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
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir,
        sim_id=manifest.sim_id, choice_option_id="hire_fast",
    )

    # 核心断言：advance_step 的 skill_name 也按 manifest.template
    # （group_evolution）动态推导，engine.py 本身没有为这个新模板改过
    # 任何一行代码。
    assert step_step.skill_name == "group-evolution-template"
    assert next_state.vars["size"] == 12

    store = SimStore.for_root(data_dir, manifest.sim_id)
    reloaded_manifest = store.load_manifest()
    assert reloaded_manifest.template == "group_evolution"
