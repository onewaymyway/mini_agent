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


def test_scenario_draft_from_dict_parses_uncertain_fields():
    """阶段十一（4.3 节）：`ScenarioDraft.from_dict` 应该原样解析可选的
    `uncertain_fields` 字段，缺省时为空列表。"""
    draft = spec_mod.ScenarioDraft.from_dict(
        {
            "title": "t", "summary": "s", "vars": {"rate": 0.2}, "options": [],
            "uncertain_fields": [
                {"field": "rate", "confidence": "low", "note": "主观估计"}
            ],
        }
    )
    assert draft.uncertain_fields == [
        {"field": "rate", "confidence": "low", "note": "主观估计"}
    ]

    draft_without = spec_mod.ScenarioDraft.from_dict(
        {"title": "t", "summary": "s", "vars": {}, "options": []}
    )
    assert draft_without.uncertain_fields == []


def test_scenario_draft_from_dict_parses_objectives():
    """阶段十二（4.4 节）：`ScenarioDraft.from_dict` 应该原样解析可选的
    `objectives` 字段，缺省时为空列表。"""
    draft = spec_mod.ScenarioDraft.from_dict(
        {
            "title": "t", "summary": "s", "vars": {}, "options": [],
            "objectives": ["资产净值", "工作满意度"],
        }
    )
    assert draft.objectives == ["资产净值", "工作满意度"]

    draft_without = spec_mod.ScenarioDraft.from_dict(
        {"title": "t", "summary": "s", "vars": {}, "options": []}
    )
    assert draft_without.objectives == []


def test_scenario_draft_from_dict_parses_structured_objectives():
    """阶段十四（4.6 节）：`objectives` 类型放宽为 `List[Any]`，结构化
    字典项应该原样保留（不被强制转成字符串），纯字符串项行为不变。"""
    draft = spec_mod.ScenarioDraft.from_dict(
        {
            "title": "t", "summary": "s", "vars": {}, "options": [],
            "objectives": [
                "资产净值",
                {"label": "现金", "field": "cash", "direction": "max"},
            ],
        }
    )
    assert draft.objectives[0] == "资产净值"
    assert draft.objectives[1] == {"label": "现金", "field": "cash", "direction": "max"}


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
    assert captured["inputs"] == {
        "intent": "模拟一个刚毕业的人生",
        "feedback": "",
        "previous_draft_json": "",
        "option_count_hint": "4 个左右",
        "time_granularity_hint": spec_mod.DEFAULT_TIME_GRANULARITY + spec_mod._GRANULARITY_CONTINUITY_NOTE_CREATE,
        "multi_entity_mode_hint": spec_mod._MULTI_ENTITY_MODE_HINT_OFF,
    }
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


def test_advance_defaults_to_previous_granularity_when_omitted(tmp_path, monkeypatch):
    """skill 没给 next_time_granularity 时，应该原样延续上一步的粒度，
    且不应被标记为"变化了"。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={"age": 22}, options=[], time_granularity="1 年",
    )
    store = SimStore.for_root(data_dir, manifest.sim_id)
    assert store.load_current_state().time_granularity == "1 年"

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
            assert inputs["current_time_granularity"] == "1 年"
            result_file = _write_result_file(
                tmp_path, "advance_result.json",
                {"next_summary": "s2", "narrative": "n", "next_vars": {"age": 23}, "options": []},
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
    assert next_state.time_granularity == "1 年"
    assert next_state.granularity_changed is False
    assert next_state.granularity_reason is None


def test_advance_records_granularity_switch_and_reason(tmp_path, monkeypatch):
    """skill 给出不同粒度时，应该记录 changed=True 和切换理由。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[], time_granularity="1 年",
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
                    "next_summary": "进入谈判", "narrative": "n", "next_vars": {},
                    "options": [], "next_time_granularity": "一轮",
                    "granularity_reason": "进入关键谈判，改为按轮次推进",
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
    assert next_state.time_granularity == "一轮"
    assert next_state.granularity_changed is True
    assert next_state.granularity_reason == "进入关键谈判，改为按轮次推进"


def test_advance_ignores_reason_when_granularity_unchanged(tmp_path, monkeypatch):
    """即使 skill 多嘴给了 granularity_reason，只要值和上一步一样，
    engine 也不应该把它当成"变化"落盘（避免脏理由污染时间线展示）。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[], time_granularity="1 年",
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
                    "next_summary": "s2", "narrative": "n", "next_vars": {},
                    "options": [], "next_time_granularity": "1 年",
                    "granularity_reason": "其实没变但多嘴写了理由",
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
    assert next_state.granularity_changed is False
    assert next_state.granularity_reason is None


def test_materialize_simulation_stores_initial_time_granularity(tmp_path):
    manifest = engine_mod.materialize_simulation(
        tmp_path / "data", template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[], time_granularity="1 个季度",
    )
    store = SimStore.for_root(tmp_path / "data", manifest.sim_id)
    assert store.load_current_state().time_granularity == "1 个季度"


def test_resolve_hints_fixed_mode_uses_configured_value():
    hints = spec_mod.resolve_hints({"time_granularity_mode": "fixed", "time_granularity": "1 个月"})
    assert "固定模式" in hints["time_granularity_hint"]
    assert "1 个月" in hints["time_granularity_hint"]


def test_resolve_hints_guided_mode_includes_user_guide_text():
    hints = spec_mod.resolve_hints(
        {"time_granularity_mode": "guided", "time_granularity_guide": "日常按季度，谈判时按轮次"},
        stage="advance",
    )
    assert "引导模式" in hints["time_granularity_hint"]
    assert "日常按季度，谈判时按轮次" in hints["time_granularity_hint"]
    assert "{current_time_granularity}" in hints["time_granularity_hint"]  # advance 场景保留延续性说明


def test_resolve_hints_create_stage_has_no_dangling_placeholder():
    hints = spec_mod.resolve_hints({}, stage="create")
    assert "{current_time_granularity}" not in hints["time_granularity_hint"]


def test_resolve_hints_multi_entity_mode_off_by_default():
    """阶段十七（4.9 节）：`multi_entity_mode` 未声明时默认关闭，提示词
    要明确写清楚"未启用"，不能是含糊的空字符串。"""
    hints = spec_mod.resolve_hints({})
    assert "未启用" in hints["multi_entity_mode_hint"]


def test_resolve_hints_multi_entity_mode_on_mentions_entities_and_shared_vars():
    """`multi_entity_mode` 为 True 时，提示词要点出 `entities`/
    `shared_vars` 这两个约定字段名，skill 才知道具体怎么组织 `vars`。"""
    hints = spec_mod.resolve_hints({"multi_entity_mode": True})
    assert "entities" in hints["multi_entity_mode_hint"]
    assert "shared_vars" in hints["multi_entity_mode_hint"]


def test_set_pilot_config_updates_manifest(tmp_path):
    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s", vars={}, options=[],
    )
    assert manifest.pilot_mode == "manual"

    updated = engine_mod.set_pilot_config(
        data_dir, manifest.sim_id,
        pilot_mode="autopilot",
        autopilot={"enabled": True, "principles": ["稳"], "risk_preference": "conservative",
                   "review_mode": "silent"},
    )
    assert updated.pilot_mode == "autopilot"
    assert updated.autopilot["enabled"] is True

    store = SimStore.for_root(data_dir, manifest.sim_id)
    reloaded = store.load_manifest()
    assert reloaded.pilot_mode == "autopilot"
    assert reloaded.autopilot["risk_preference"] == "conservative"


def test_materialize_simulation_skips_llm_call(tmp_path):
    """`app.py` 创建向导用的路径：草稿已经在内存里（可能被用户编辑过），
    直接落盘，不应该再触发任何 workflow/LLM 调用。"""
    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir,
        template="life_sim",
        intent="用户编辑后的意图",
        title="编辑后的标题",
        summary="编辑后的摘要",
        vars={"age": 30},
        options=[{"id": "x", "label": "选项X", "description": "d"}],
    )
    assert manifest.title == "编辑后的标题"

    store = SimStore.for_root(data_dir, manifest.sim_id)
    state0 = store.load_current_state()
    assert state0.summary == "编辑后的摘要"
    assert state0.options[0].id == "x"


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


def test_advance_clamps_negative_resource_field_and_records_violation(tmp_path, monkeypatch):
    """阶段九（4.1 节）：`resource_fields` 声明的字段被 LLM 算成负数时，
    应该被夹到下限（默认 0），且推进本身不被拒绝，越界详情记入
    `next_state.resource_violations`。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={"cash": 1000}, options=[],
        settings={"resource_fields": ["cash"]},
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
                    "next_summary": "破产了",
                    "narrative": "花超了",
                    "next_vars": {"cash": -500},
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

    assert next_state.vars["cash"] == 0
    assert next_state.resource_violations == [
        {"field": "cash", "llm_value": -500, "clamped_value": 0}
    ]

    # 落盘的历史里也应该带上这份记录（不是只存在于返回值里）。
    store = SimStore.for_root(data_dir, manifest.sim_id)
    history = store.load_history()
    assert history[-1].resource_violations == next_state.resource_violations


def test_advance_ignores_resource_fields_when_value_within_bounds(tmp_path, monkeypatch):
    """字段值没有越界时，不应该产生任何 `resource_violations` 记录。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={"cash": 1000}, options=[],
        settings={"resource_fields": ["cash"]},
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
                {"next_summary": "s2", "narrative": "n", "next_vars": {"cash": 800}, "options": []},
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
    assert next_state.vars["cash"] == 800
    assert next_state.resource_violations == []


def test_advance_records_relation_violation_when_transfer_not_conserved(tmp_path, monkeypatch):
    """阶段十六（4.8 节）：声明了 `resource_relations` 的一对字段，如果
    这一步的变化量明显不守恒（超出默认 10% 容差），应该记入
    `next_state.relation_violations`，且不修改任何数值、不拒绝推进。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={"cash": 1000, "inventory": {"value": 0}}, options=[],
        settings={
            "resource_relations": [
                {"type": "transfer", "from": "cash", "to": "inventory.value"}
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
            # 花了 100 现金，库存只增加了 20——明显超出 10% 容差。
            result_file = _write_result_file(
                tmp_path, "advance_result.json",
                {
                    "next_summary": "买了点库存",
                    "narrative": "花钱买货",
                    "next_vars": {"cash": 900, "inventory": {"value": 20}},
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
    assert next_state.vars["cash"] == 900
    assert next_state.vars["inventory"]["value"] == 20
    assert next_state.relation_violations == [
        {"from": "cash", "to": "inventory.value", "delta_from": -100, "delta_to": 20}
    ]

    store = SimStore.for_root(data_dir, manifest.sim_id)
    history = store.load_history()
    assert history[-1].relation_violations == next_state.relation_violations


def test_advance_ignores_transfer_within_tolerance(tmp_path, monkeypatch):
    """变化量在容差范围内（默认 10%）时，不应该产生任何
    `relation_violations` 记录。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={"cash": 1000, "inventory": {"value": 0}}, options=[],
        settings={
            "resource_relations": [
                {"type": "transfer", "from": "cash", "to": "inventory.value"}
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
            # 花了 100 现金，库存增加了 95——5% 偏差，在默认 10% 容差内。
            result_file = _write_result_file(
                tmp_path, "advance_result.json",
                {
                    "next_summary": "买了点库存",
                    "narrative": "花钱买货，有点交易成本",
                    "next_vars": {"cash": 900, "inventory": {"value": 95}},
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
    assert next_state.relation_violations == []


def test_scenario_draft_from_dict_parses_resource_relations():
    """阶段十六（4.8 节）：`ScenarioDraft.resource_relations` 应该原样
    解析出 skill 给出的建议值列表。"""
    draft = spec_mod.ScenarioDraft.from_dict(
        {
            "title": "t", "summary": "s", "vars": {}, "options": [],
            "resource_relations": [
                {"type": "transfer", "from": "cash", "to": "inventory.value", "tolerance": 0.1}
            ],
        }
    )
    assert draft.resource_relations == [
        {"type": "transfer", "from": "cash", "to": "inventory.value", "tolerance": 0.1}
    ]

    empty_draft = spec_mod.ScenarioDraft.from_dict({"title": "t", "summary": "s", "vars": {}, "options": []})
    assert empty_draft.resource_relations == []


def test_materialize_simulation_stores_uncertain_fields_on_state0(tmp_path):
    """阶段十一（4.3 节）：`generate_scenario` 阶段给出的
    `uncertain_fields` 建议应该原样落到 `state0.uncertain_fields`。"""
    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={"startup_success_rate": 0.18}, options=[],
        uncertain_fields=[
            {"field": "startup_success_rate", "confidence": "low", "note": "主观估计"}
        ],
    )
    store = SimStore.for_root(data_dir, manifest.sim_id)
    state0 = store.load_current_state("main")
    assert state0.uncertain_fields == [
        {"field": "startup_success_rate", "confidence": "low", "note": "主观估计"}
    ]


def test_advance_parses_uncertain_fields_from_llm_output(tmp_path, monkeypatch):
    """阶段十一（4.3 节）：`advance_step` 输出里的可选 `uncertain_fields`
    应该原样解析进 `next_state.uncertain_fields`；未给出时应为空列表。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
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
                    "next_summary": "s2",
                    "narrative": "n",
                    "next_vars": {"startup_success_rate": 0.32},
                    "options": [],
                    "uncertain_fields": [
                        {"field": "startup_success_rate", "confidence": "medium", "note": "行业均值外推"}
                    ],
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
    assert next_state.uncertain_fields == [
        {"field": "startup_success_rate", "confidence": "medium", "note": "行业均值外推"}
    ]


def test_materialize_simulation_stores_objectives_in_settings(tmp_path):
    """阶段十二（4.4 节，Problem Compiler 雏形）：创建向导确认的
    `settings.objectives` 应该原样落到 `manifest.settings`，纯记录用途，
    不影响任何推进/校验逻辑。"""
    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
        settings={"objectives": ["资产净值", "工作满意度"]},
    )
    store = SimStore.for_root(data_dir, manifest.sim_id)
    reloaded = store.load_manifest()
    assert reloaded.settings.get("objectives") == ["资产净值", "工作满意度"]


def test_advance_parses_key_drivers_from_llm_output(tmp_path, monkeypatch):
    """阶段十三（4.5 节）：`advance_step` 输出里的可选 `key_drivers`
    应该原样解析进 `next_state.key_drivers`；未给出时应为空列表。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
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
                    "next_summary": "s2",
                    "narrative": "n",
                    "next_vars": {},
                    "options": [],
                    "key_drivers": ["市场需求超预期", "现金储备见底被迫收缩"],
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
    assert next_state.key_drivers == ["市场需求超预期", "现金储备见底被迫收缩"]


def test_advance_parses_causal_links_from_llm_output(tmp_path, monkeypatch):
    """阶段十五（4.7 节）：`advance_step` 输出里的可选 `causal_links`
    应该原样解析进 `next_state.causal_links`；未给出时应为空列表；
    非字典项应该被跳过。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
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
                    "next_summary": "s2",
                    "narrative": "n",
                    "next_vars": {},
                    "options": [],
                    "key_drivers": ["现金储备见底"],
                    "causal_links": [
                        {
                            "driver": "现金储备见底",
                            "affected_fields": ["cash", "stage"],
                            "effect": "被迫从「自由职业」转为「求稳定工作」",
                        },
                        "不是字典，应该被跳过",
                    ],
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
    assert next_state.causal_links == [
        {
            "driver": "现金储备见底",
            "affected_fields": ["cash", "stage"],
            "effect": "被迫从「自由职业」转为「求稳定工作」",
        }
    ]
