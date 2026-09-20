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

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import world_simulator.engine as engine_mod
import world_simulator.spec_generator as spec_mod
from world_simulator.state_model import SimState
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
    causal_lines_hint = captured["inputs"]["causal_lines_hint"]
    assert {k: v for k, v in captured["inputs"].items() if k not in ("causal_lines_hint", "belief_fields_hint")} == {
        "intent": "模拟一个刚毕业的人生",
        "feedback": "",
        "previous_draft_json": "",
        "option_count_hint": (
            "最多不超过 4 个（软上限，用于防止候选列表刷屏，不是目标"
            "数量——具体生成几个由这一步是否出现真正的决策分岔点决定，"
            "见下方“候选选项生成流程”）"
        ),
        "time_granularity_hint": spec_mod.DEFAULT_TIME_GRANULARITY + spec_mod._GRANULARITY_CONTINUITY_NOTE_CREATE,
        "multi_entity_mode_hint": spec_mod._MULTI_ENTITY_MODE_HINT_OFF,
        "background_entities_hint": "未启用（所有主体都按正常流程完整推理）",
        "calibration_notes": "",
        "relevant_knowledge_hint": "（暂无相关的已知因果知识）",
    }
    # 因果线是默认基础机制（不需要用户提前声明），这里只校验语义，不
    # 校验措辞原文，避免和 `test_resolve_hints_causal_lines_hint_variants`
    # 重复维护同一句文案。创建阶段（阶段二十六）要求给出核心因果线+
    # 未来因果树，措辞与推进阶段（`line_updates`）不同，改为校验
    # `future_tree`/`causal_lines`。
    assert "future_tree" in causal_lines_hint and "causal_lines" in causal_lines_hint
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
                {
                    "id": "grad_school",
                    "label": "读研",
                    "description": "继续深造",
                    "risk_level": None,
                    "reversibility": None,
                    "affected_lines": [],
                    "key_uncertainty": "",
                    "action_reason": "",
                    "urgency": None,
                    "time_window": "",
                    "action_type": "single",
                },
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


def test_list_simulations_sorted_by_created_at_desc_not_dir_name(tmp_path):
    """模拟列表页排序应该按创建时间倒序（最新排最前），而不是
    `sim_id` 的目录名字典序——`sim_id` 形如 `{template}_{随机后缀}`，
    不包含时间信息，字典序和创建时间顺序完全无关。这里故意让"目录名
    字典序更靠前"的实例反而是"更晚创建"的，验证排序确实依据
    `created_at` 而不是目录遍历顺序。"""
    data_dir = tmp_path / "data"
    older = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="更早创建的",
        summary="s", vars={}, options=[],
    )
    newer = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="更晚创建的",
        summary="s", vars={}, options=[],
    )
    # 直接改写落盘的 created_at，避免测试依赖真实的墙钟时间间隔
    # （两次调用之间可能在同一毫秒内完成，`now_iso()` 差异不可靠）。
    older_store = SimStore.for_root(data_dir, older.sim_id)
    older_manifest = older_store.load_manifest()
    older_manifest.created_at = "2020-01-01T00:00:00"
    older_store.save_manifest(older_manifest)

    newer_store = SimStore.for_root(data_dir, newer.sim_id)
    newer_manifest = newer_store.load_manifest()
    newer_manifest.created_at = "2026-01-01T00:00:00"
    newer_store.save_manifest(newer_manifest)

    # 目录名字典序会把 sim_id 更小的排在前面；这里不对 sim_id 做任何
    # 假设，只断言按 created_at 的实际先后顺序。
    manifests = engine_mod.list_simulations(data_dir)
    ids_in_order = [m.sim_id for m in manifests]
    assert ids_in_order.index(newer.sim_id) < ids_in_order.index(older.sim_id)


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


def test_advance_first_step_background_entity_has_no_trend_to_extrapolate(tmp_path, monkeypatch):
    """阶段十八/4.10 节：Hierarchical Agent 设计草案第一步——第一次
    推进时没有"上一步"可参考，背景角色的数值应该原样保留这一步
    （即初始状态）的值，**强制覆盖** LLM 给出的任何值（哪怕 LLM 给了
    一个明显不同的数）。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="negotiation", intent="i", title="t", summary="s",
        vars={
            "entities": {"甲方": {"budget": 500}, "背景NPC": {"mood_score": 50}},
            "shared_vars": {},
        },
        options=[],
        settings={
            "multi_entity_mode": True,
            "hierarchical_agent_mode": True,
            "background_entities": ["背景NPC"],
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
                    "next_summary": "谈判继续",
                    "narrative": "双方各自盘算",
                    "next_vars": {
                        "entities": {
                            "甲方": {"budget": 480},
                            # LLM 给背景角色编了一个明显不同的值，应该被强制覆盖掉。
                            "背景NPC": {"mood_score": 999},
                        },
                        "shared_vars": {},
                    },
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

    # 关键角色（甲方）不受影响，原样采纳 LLM 的输出。
    assert next_state.vars["entities"]["甲方"]["budget"] == 480
    # 背景角色被强制覆盖：没有"上一步"，外推量为 0，原样保留这一步的值。
    assert next_state.vars["entities"]["背景NPC"]["mood_score"] == 50
    assert next_state.background_entities_applied == ["背景NPC"]


def test_advance_background_entity_extrapolates_linear_trend_from_history(tmp_path, monkeypatch):
    """有真实的"上一步"可参考时，背景角色应该按线性趋势外推，而不是
    简单冻结在某个值上——手动构造一段有真实变化的历史（mood_score
    从 50 变到 60），验证下一步被外推成 70（60 + (60-50)），并且
    仍然强制覆盖 LLM 给出的值。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="negotiation", intent="i", title="t", summary="s",
        vars={"entities": {"背景NPC": {"mood_score": 50}}, "shared_vars": {}},
        options=[],
        settings={
            "multi_entity_mode": True,
            "hierarchical_agent_mode": True,
            "background_entities": ["背景NPC"],
        },
    )

    store = SimStore.for_root(data_dir, manifest.sim_id)
    step1_state = SimState(
        step=1, summary="s1",
        vars={"entities": {"背景NPC": {"mood_score": 60}}, "shared_vars": {}},
    )
    store.append_state(step1_state, branch=manifest.branch)
    manifest.current_step = 1
    store.save_manifest(manifest)

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
                    "next_summary": "谈判继续",
                    "narrative": "双方各自盘算",
                    "next_vars": {
                        "entities": {"背景NPC": {"mood_score": -1}},  # 会被强制覆盖，值本身无意义
                        "shared_vars": {},
                    },
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
    assert next_state.vars["entities"]["背景NPC"]["mood_score"] == 70
    assert next_state.background_entities_applied == ["背景NPC"]


def test_advance_without_hierarchical_agent_mode_never_touches_entities(tmp_path, monkeypatch):
    """`hierarchical_agent_mode` 未声明（默认关闭）时，即使 `settings`
    里意外留了 `background_entities`，也不应该做任何覆盖——完全向后
    兼容，不影响 `negotiation` 模板在阶段十七下的既有行为。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="negotiation", intent="i", title="t", summary="s",
        vars={"entities": {"背景NPC": {"mood_score": 50}}, "shared_vars": {}},
        options=[],
        settings={"multi_entity_mode": True, "background_entities": ["背景NPC"]},
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
                    "next_summary": "谈判继续", "narrative": "双方各自盘算",
                    "next_vars": {"entities": {"背景NPC": {"mood_score": 123}}, "shared_vars": {}},
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
    assert next_state.vars["entities"]["背景NPC"]["mood_score"] == 123  # 原样采纳，未被覆盖
    assert next_state.background_entities_applied == []


def test_advance_passes_calibration_notes_to_prompt_inputs(tmp_path, monkeypatch):
    """阶段十八（4.11 节）：`settings.calibration_notes` 应该原样传入
    `advance_step` 的 prompt 输入，不做任何改写/校验。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s", vars={}, options=[],
        settings={"calibration_notes": "参考：应届硕士平均起薪一万二到一万八"},
    )

    step_step = _FakeStep("step")
    captured = {}

    class FakeStoreForAdvance:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([step_step])

    class FakeRunnerForAdvance:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            captured["inputs"] = inputs
            result_file = _write_result_file(
                tmp_path, "advance_result.json",
                {"next_summary": "s", "narrative": "n", "next_vars": {}, "options": []},
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)

    engine_mod.advance(
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )
    assert captured["inputs"]["calibration_notes"] == "参考：应届硕士平均起薪一万二到一万八"


def test_resolve_hints_background_entities_hint_variants():
    """阶段十八/4.10 节：`background_entities_hint` 应该区分"未启用"/
    "启用但没列名字"/"启用且列出名字"三种情况，且列出的名字要出现在
    提示文本里，方便复核 prompt 内容。"""
    assert "未启用" in spec_mod.resolve_hints({})["background_entities_hint"]
    assert "等同未启用" in spec_mod.resolve_hints(
        {"hierarchical_agent_mode": True}
    )["background_entities_hint"]
    hint = spec_mod.resolve_hints(
        {"hierarchical_agent_mode": True, "background_entities": ["乙方", "背景NPC"]}
    )["background_entities_hint"]
    assert "乙方" in hint and "背景NPC" in hint


def test_resolve_hints_causal_lines_hint_variants():
    """阶段二十二（4.13 节）：因果线是默认基础机制，不需要用户提前
    声明——`causal_lines_hint` 未声明时应该要求 skill 自行判断并给出
    因果线（而不是说"不需要输出 line_updates"），声明了则应该在提示
    文本里列出每条线的 id/label，方便复核 prompt 内容。"""
    default_hint = spec_mod.resolve_hints({})["causal_lines_hint"]
    assert "默认" in default_hint
    assert "不需要" in default_hint and "声明" in default_hint
    assert "line_updates" in default_hint

    hint = spec_mod.resolve_hints(
        {
            "causal_lines": [
                {"id": "tech", "label": "技术线", "time_granularity": "年"},
                {"id": "negotiation", "label": "谈判线"},
            ]
        }
    )["causal_lines_hint"]
    assert "tech" in hint and "技术线" in hint and "年" in hint
    assert "negotiation" in hint and "谈判线" in hint


def test_resolve_hints_causal_lines_hint_includes_user_feedback():
    """用户在详情页对某条因果线留的修改意见（`user_feedback`），应该
    出现在 `causal_lines_hint` 里，供 `advance_step` 认真纳入考虑。"""
    hint = spec_mod.resolve_hints(
        {
            "causal_lines": [
                {"id": "tech", "label": "技术线", "user_feedback": "希望技术线放慢一点"},
            ]
        }
    )["causal_lines_hint"]
    assert "希望技术线放慢一点" in hint


def test_resolve_hints_causal_lines_hint_ignores_entries_without_id():
    """没有 `id` 的因果线声明不构成一条可引用的线，应该被忽略，退化为
    "未声明"（默认自主判断）分支。"""
    hint = spec_mod.resolve_hints({"causal_lines": [{"label": "没有 id"}]})["causal_lines_hint"]
    assert "默认" in hint


def test_resolve_hints_lines_due_hint_omitted_when_no_cadence_declared():
    """阶段三十一，4.20 节：`advance_every_n_steps` 未声明（或 <= 1）
    时不应该出现节奏提示，避免给"每步都可能动"的线增加噪音。"""
    hint = spec_mod.resolve_hints(
        {"causal_lines": [{"id": "tech", "label": "技术线"}]},
        current_step=5,
    )["causal_lines_hint"]
    assert "预期这一步" not in hint


def test_resolve_hints_lines_due_hint_marks_due_and_quiet_lines():
    """`advance_every_n_steps=5` 的线，在 `current_step` 是 5 的倍数时
    应该出现在"预期有动静"里，否则出现在"预期维持不变"里；这只是提示，
    不应该出现"强制"/"必须"这类约束性措辞。"""
    settings = {
        "causal_lines": [
            {"id": "tech", "label": "技术线", "advance_every_n_steps": 5},
            {"id": "daily", "label": "日常线"},
        ]
    }
    due_hint = spec_mod.resolve_hints(settings, current_step=10)["causal_lines_hint"]
    assert "预期这一步有动静" in due_hint and "tech" in due_hint
    assert "daily" not in due_hint.split("预期这一步有动静")[1].split("；")[0]

    quiet_hint = spec_mod.resolve_hints(settings, current_step=7)["causal_lines_hint"]
    assert "预期这一步大概率维持不变" in quiet_hint and "tech" in quiet_hint


def test_resolve_hints_lines_due_hint_tolerates_invalid_cadence_value():
    """`advance_every_n_steps` 是非法值（字符串/负数）时应该安全退化为
    1（不出现在提示里），不应该报错。"""
    settings = {"causal_lines": [{"id": "tech", "label": "技术线", "advance_every_n_steps": "abc"}]}
    hint = spec_mod.resolve_hints(settings, current_step=3)["causal_lines_hint"]
    assert "预期这一步" not in hint


def test_scenario_draft_from_dict_parses_causal_lines():
    """阶段二十二（4.13 节）：`ScenarioDraft.causal_lines` 应该原样
    解析出 skill 给出的建议值列表；非字典项应该被跳过。"""
    draft = spec_mod.ScenarioDraft.from_dict(
        {
            "title": "t", "summary": "s", "vars": {}, "options": [],
            "causal_lines": [
                {"id": "tech", "label": "技术线", "time_granularity": "年"}, "不是字典",
            ],
        }
    )
    assert draft.causal_lines == [{"id": "tech", "label": "技术线", "time_granularity": "年"}]

    draft_empty = spec_mod.ScenarioDraft.from_dict(
        {"title": "t", "summary": "s", "vars": {}, "options": []}
    )
    assert draft_empty.causal_lines == []


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


def test_advance_lines_due_hint_reflects_next_step_not_current_step(tmp_path, monkeypatch):
    """阶段三十一，4.20 节：`engine.advance()` 喂给
    `resolve_hints()` 的 `current_step` 应该是即将产生的*下一个*状态的
    step（`current.step + 1`），不是当前状态的 step——用一条
    `advance_every_n_steps=2` 的线验证：`state0.step == 0`，下一步是
    `step 1`（奇数，不是 2 的倍数），因此这条线应该出现在"预期维持
    不变"里，而不是"预期有动静"里。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
        settings={"causal_lines": [{"id": "tech", "label": "技术线", "advance_every_n_steps": 2}]},
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
            assert "预期这一步大概率维持不变" in inputs["causal_lines_hint"]
            assert "tech" in inputs["causal_lines_hint"]
            result_file = _write_result_file(
                tmp_path, "advance_result.json",
                {"next_summary": "s2", "narrative": "n", "next_vars": {}, "options": []},
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)

    engine_mod.advance(
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )



    """阶段二十二（4.13 节）：`advance_step` 输出里的可选 `line_updates`
    应该原样解析进 `next_state.line_updates`；未给出时应为空字典；
    非字典 value 应该被跳过。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
        settings={"causal_lines": [{"id": "tech", "label": "技术线"}]},
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
            assert "tech" in inputs["causal_lines_hint"]
            result_file = _write_result_file(
                tmp_path, "advance_result.json",
                {
                    "next_summary": "s2", "narrative": "n", "next_vars": {}, "options": [],
                    "line_updates": {
                        "tech": {"time_label": "第 3 年", "summary": "AI 成本持续下降"},
                        "bad": "不是字典，应该被跳过",
                    },
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
    assert next_state.line_updates == {
        "tech": {"time_label": "第 3 年", "summary": "AI 成本持续下降"},
    }


def test_advance_auto_registers_undeclared_causal_line_ids(tmp_path, monkeypatch):
    """用户要求：因果线是模拟的默认基础机制，不应该有前置条件——即使
    `manifest.settings.causal_lines` 完全没有声明过，只要 `advance_step`
    在 `line_updates`/`causal_links.line_id` 里自发用了一个新 id，
    `advance()` 也应该自动把它登记进 `manifest.settings.causal_lines`，
    而不是要求用户先手填 JSON 才能让这条线出现。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
    )
    # 阶段二十六：创建模拟就必须有核心因果线——完全没有声明时兜底生成
    # 一条"主线"（`main_line`），不再是空列表。
    initial_ids = {
        line["id"] for line in manifest.settings.get("causal_lines") or [] if isinstance(line, dict)
    }
    assert initial_ids == {"main_line"}

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
            # 因果线已经在创建时兜底生成了 main_line，推进阶段的提示语
            # 应该列出已识别的线（而不是"完全没有声明"的默认邀请文案）。
            assert "main_line" in inputs["causal_lines_hint"]
            result_file = _write_result_file(
                tmp_path, "advance_result.json",
                {
                    "next_summary": "s2", "narrative": "n", "next_vars": {}, "options": [],
                    "line_updates": {
                        "career": {"time_label": "第 1 年", "summary": "刚入职"},
                    },
                    "causal_links": [
                        {"driver": "薪资到位", "affected_fields": ["cash"], "effect": "开始攒钱",
                         "line_id": "finance"},
                    ],
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)

    engine_mod.advance(
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )

    reloaded = SimStore.for_root(data_dir, manifest.sim_id).load_manifest()
    ids = {
        line["id"] for line in reloaded.settings.get("causal_lines") or [] if isinstance(line, dict)
    }
    # 兜底生成的 main_line 仍然保留，新自发出现的 career/finance 追加登记。
    assert ids == {"main_line", "career", "finance"}
    # 自动登记的线标注了 `auto_discovered`，UI/后续逻辑可以据此区分
    # "用户手填的"和"系统发现的"，不强制要求，但不应该丢失这个信息。
    auto_flags = {
        line["id"]: line.get("auto_discovered")
        for line in reloaded.settings.get("causal_lines") or [] if isinstance(line, dict)
    }
    assert auto_flags.get("career") is True
    assert auto_flags.get("finance") is True


def test_advance_builds_decision_opportunity_when_options_present(tmp_path, monkeypatch):
    """阶段三十三第五批（4.10 节）：`options` 非空时，`advance()` 应该
    把 `line_updates` 的 key、`tree_updates` 审计里的分支 id、
    `decision_reason` 一起收纳进 `next_state.decision_opportunity`。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
        settings={
            "causal_lines": [
                {
                    "id": "tech",
                    "label": "技术线",
                    "future_tree": {"branches": [{"id": "fast", "description": "快速发展"}]},
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
            result_file = _write_result_file(
                tmp_path, "advance_result.json",
                {
                    "next_summary": "s2", "narrative": "n", "next_vars": {},
                    "options": [{"id": "a", "label": "选项 A", "description": "d"}],
                    "line_updates": {"tech": {"time_label": "第 1 年", "summary": "加速"}},
                    "tree_updates": [{"line_id": "tech", "confirmed_branch": "fast"}],
                    "decision_reason": "技术线出现关键窗口",
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
    assert next_state.decision_opportunity == {
        "trigger_line_ids": ["tech"],
        "trigger_node_ids": ["fast"],
        "decision_reason": "技术线出现关键窗口",
        "context_note": "",
    }
    # 顶层 decision_reason 字段与容器里的值保持镜像同步。
    assert next_state.decision_reason == "技术线出现关键窗口"


def test_advance_decision_opportunity_is_none_when_no_options(tmp_path, monkeypatch):
    """`options` 为空数组时，`decision_opportunity` 应该是 `None`——
    "这一步没有形成需要特别说明背景的决策机会"。"""
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
                {"next_summary": "s2", "narrative": "n", "next_vars": {}, "options": []},
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
    assert next_state.decision_opportunity is None


def test_advance_does_not_duplicate_already_declared_causal_lines(tmp_path, monkeypatch):
    """已经声明过的因果线 id 再次出现在 `line_updates`/`causal_links`
    里时，不应该被重复登记一遍。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
        settings={"causal_lines": [{"id": "tech", "label": "技术线", "time_granularity": "年"}]},
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
                    "next_summary": "s2", "narrative": "n", "next_vars": {}, "options": [],
                    "line_updates": {"tech": {"time_label": "第 2 年", "summary": "继续下降"}},
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)

    engine_mod.advance(
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )
    reloaded = SimStore.for_root(data_dir, manifest.sim_id).load_manifest()
    lines = reloaded.settings.get("causal_lines") or []
    # 不应该重复登记同一个 id——列表里只有一条 "tech"（其余字段会因为
    # `ensure_future_trees()` 兜底补上 `future_tree`，不再逐字段比对）。
    assert [line["id"] for line in lines] == ["tech"]
    tech_line = lines[0]
    assert tech_line["label"] == "技术线"
    assert tech_line["time_granularity"] == "年"
    assert tech_line["future_tree"]["branches"]


def test_advance_records_causal_links_into_knowledge_base(tmp_path, monkeypatch):
    """阶段二十（4.12 节 2.）：`advance()` 落盘 `next_state` 之后，应该
    把非空的 `causal_links` 旁路写入跨模拟知识库。"""
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
                    "causal_links": [
                        {
                            "driver": "现金储备见底",
                            "affected_fields": ["cash"],
                            "effect": "被迫收缩开支",
                        }
                    ],
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)

    engine_mod.advance(
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )

    import world_simulator.knowledge_base as kb_mod

    items = kb_mod.load_all(data_dir)
    assert len(items) == 1
    assert items[0].cause == "现金储备见底"
    assert items[0].source_sim_id == manifest.sim_id
    assert items[0].source_template == "life_sim"


def test_advance_survives_broken_knowledge_base_file(tmp_path, monkeypatch):
    """知识库写入是旁路操作，即使知识库文件本身已经损坏（比如被手工
    改坏），也不应该让本次推进失败（4.12 节 2. 的"失败也不抛出"约束）。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
    )

    import world_simulator.knowledge_base as kb_mod

    broken_path = kb_mod.knowledge_path(data_dir)
    broken_path.parent.mkdir(parents=True, exist_ok=True)
    broken_path.write_text("这不是合法的 jsonl 内容 {{{", encoding="utf-8")

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
                    "next_summary": "s2", "narrative": "n", "next_vars": {}, "options": [],
                    "causal_links": [{"driver": "d", "effect": "e"}],
                },
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)

    # 损坏的知识库文件里每一行都会被 load_all 静默跳过（不是合法 JSON），
    # 所以这里实际验证的是"advance 本身不会因为知识库旁路操作而抛出"。
    next_state = engine_mod.advance(
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )
    assert next_state.step == 1


def test_generate_scenario_passes_knowledge_hint_from_data_dir(tmp_path, monkeypatch):
    """阶段二十（4.12 节 3.）：给了 `data_dir` 时，`generate_scenario()`
    应该检索知识库并把结果拼进 `relevant_knowledge_hint` 输入。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    import world_simulator.knowledge_base as kb_mod

    kb_mod.record_causal_links(
        data_dir, sim_id="sim_prev", template="life_sim",
        causal_links=[{"driver": "现金储备见底", "effect": "被迫收缩开支"}],
    )

    draft_step = _FakeStep("draft")
    captured = {}

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([draft_step])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            captured["inputs"] = inputs
            result_file = _write_result_file(
                tmp_path, "scenario_result.json",
                {"title": "t", "summary": "s", "vars": {}, "options": []},
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="draft", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    spec_mod.generate_scenario(
        cfg=object(), workspace_root=workspace_root, template="life_sim",
        intent="现金储备见底了怎么办", data_dir=data_dir,
    )

    assert "现金储备见底" in captured["inputs"]["relevant_knowledge_hint"]


def test_generate_scenario_without_data_dir_uses_placeholder_hint(tmp_path, monkeypatch):
    """`data_dir` 为 None（比如某些不关心历史积累的调用场景）时，
    应该退化为占位文案，不抛出、也不影响其余生成逻辑。"""
    workspace_root = tmp_path / "ws"
    draft_step = _FakeStep("draft")
    captured = {}

    class FakeStore:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([draft_step])

    class FakeRunner:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            captured["inputs"] = inputs
            result_file = _write_result_file(
                tmp_path, "scenario_result.json",
                {"title": "t", "summary": "s", "vars": {}, "options": []},
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="draft", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStore)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunner)

    spec_mod.generate_scenario(
        cfg=object(), workspace_root=workspace_root, template="life_sim", intent="随便什么意图",
    )
    assert captured["inputs"]["relevant_knowledge_hint"] == "（暂无相关的已知因果知识）"


def test_advance_parses_structural_change_from_llm_output(tmp_path, monkeypatch):
    """阶段二十三（4.14 节）：`advance_step` 输出里的可选 `structural_change`
    应该原样解析进 `next_state.structural_change`，并强制补上
    `accepted: False`/`accepted_at: None`（不管 skill 有没有给这两个
    字段，都不应该信任它自己声称"已采纳"）；未给出时应为 `None`。"""
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
                    "next_summary": "s2", "narrative": "n", "next_vars": {}, "options": [],
                    "structural_change": {
                        "kind": "new_entity",
                        "description": "谈判各方之间形成了稳定的联盟结构",
                        "proposed_fields": {"alliance": {"members": ["甲", "乙"]}},
                        "accepted": True,  # skill 自己声称已采纳，engine 不应该信任
                    },
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
    assert next_state.structural_change == {
        "detected": True,
        "kind": "new_entity",
        "description": "谈判各方之间形成了稳定的联盟结构",
        "proposed_fields": {"alliance": {"members": ["甲", "乙"]}},
        "accepted": False,
        "accepted_at": None,
    }


def test_advance_ignores_structural_change_with_unknown_kind_or_empty_description(tmp_path, monkeypatch):
    """`kind` 不是约定三选一之一、或 `description` 为空时，
    `_normalize_structural_change` 应该视为"没给"，落盘为 `None`，
    不留一条内容不完整的提示进历史。"""
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
                    "next_summary": "s2", "narrative": "n", "next_vars": {}, "options": [],
                    "structural_change": {"kind": "not_a_real_kind", "description": "x"},
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
    assert next_state.structural_change is None


def test_apply_structural_change_marks_accepted_and_updates_settings(tmp_path):
    """阶段二十三：`apply_structural_change()` 应该把目标 step 的
    `structural_change.accepted` 置为 True、记录 `accepted_at`，并把
    这条变化追加进 `manifest.settings.confirmed_structural_changes`
    （不修改任何其它 settings 字段/vars 结构）。"""
    data_dir = tmp_path / "data"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
    )
    store = SimStore.for_root(data_dir, manifest.sim_id)
    current = store.load_current_state(manifest.branch)
    current.structural_change = {
        "detected": True,
        "kind": "new_mechanism",
        "description": "形成了新的定期复盘机制",
        "proposed_fields": {"cadence": "每季度"},
        "accepted": False,
        "accepted_at": None,
    }
    history = store.load_history(manifest.branch)
    history[-1] = current
    from mini_agent.utils.atomic_write import atomic_write_jsonl
    atomic_write_jsonl(store.state_history_path(manifest.branch), [s.to_dict() for s in history])

    updated_manifest = engine_mod.apply_structural_change(
        data_dir, manifest.sim_id, step=current.step
    )

    reloaded = store.load_history(manifest.branch)
    reloaded_state = next(s for s in reloaded if s.step == current.step)
    assert reloaded_state.structural_change["accepted"] is True
    assert reloaded_state.structural_change["accepted_at"]

    confirmed = updated_manifest.settings["confirmed_structural_changes"]
    assert len(confirmed) == 1
    assert confirmed[0]["kind"] == "new_mechanism"
    assert confirmed[0]["description"] == "形成了新的定期复盘机制"
    assert confirmed[0]["step"] == current.step


def test_apply_structural_change_rejects_missing_or_already_accepted(tmp_path):
    """没有 `structural_change` 的 step、或已经采纳过的 step，
    再次调用 `apply_structural_change()` 应该报错而不是静默覆盖。"""
    data_dir = tmp_path / "data"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
    )
    with pytest.raises(engine_mod.SimEngineError):
        engine_mod.apply_structural_change(data_dir, manifest.sim_id, step=0)

    store = SimStore.for_root(data_dir, manifest.sim_id)
    current = store.load_current_state(manifest.branch)
    current.structural_change = {
        "detected": True, "kind": "regime_shift", "description": "x",
        "proposed_fields": {}, "accepted": False, "accepted_at": None,
    }
    history = store.load_history(manifest.branch)
    history[-1] = current
    from mini_agent.utils.atomic_write import atomic_write_jsonl
    atomic_write_jsonl(store.state_history_path(manifest.branch), [s.to_dict() for s in history])

    engine_mod.apply_structural_change(data_dir, manifest.sim_id, step=0)
    with pytest.raises(engine_mod.SimEngineError):
        engine_mod.apply_structural_change(data_dir, manifest.sim_id, step=0)


def test_apply_structural_change_new_mechanism_generates_suggested_causal_line(tmp_path):
    """阶段二十九（4.26 节，开放世界闭环收尾）：采纳 `new_mechanism`
    结构性变化后，`settings.suggested_causal_lines` 应该多出一条带
    默认未来树的建议，`settings.causal_lines` 本身不受影响（建议不
    自动登记）。"""
    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
    )
    store = SimStore.for_root(data_dir, manifest.sim_id)
    current = store.load_current_state(manifest.branch)
    current.structural_change = {
        "detected": True,
        "kind": "new_mechanism",
        "description": "形成了新的定期复盘机制",
        "proposed_fields": {},
        "accepted": False,
        "accepted_at": None,
    }
    history = store.load_history(manifest.branch)
    history[-1] = current
    from mini_agent.utils.atomic_write import atomic_write_jsonl
    atomic_write_jsonl(store.state_history_path(manifest.branch), [s.to_dict() for s in history])

    before_lines = manifest.settings.get("causal_lines") or []
    updated_manifest = engine_mod.apply_structural_change(
        data_dir, manifest.sim_id, step=current.step
    )

    suggested = updated_manifest.settings.get("suggested_causal_lines") or []
    assert len(suggested) == 1
    assert suggested[0]["source_kind"] == "new_mechanism"
    assert suggested[0]["source_description"] == "形成了新的定期复盘机制"
    assert suggested[0]["source_step"] == current.step
    assert suggested[0]["future_tree"]["branches"]
    assert (updated_manifest.settings.get("causal_lines") or []) == before_lines


def test_apply_structural_change_new_entity_does_not_generate_suggestion(tmp_path):
    """`kind == \"new_entity\"` 不生成因果线建议——新实体更多体现在
    `vars.entities` 里，不强制每个新实体都对应一条独立因果线。"""
    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
    )
    store = SimStore.for_root(data_dir, manifest.sim_id)
    current = store.load_current_state(manifest.branch)
    current.structural_change = {
        "detected": True, "kind": "new_entity", "description": "出现了新的合作伙伴",
        "proposed_fields": {}, "accepted": False, "accepted_at": None,
    }
    history = store.load_history(manifest.branch)
    history[-1] = current
    from mini_agent.utils.atomic_write import atomic_write_jsonl
    atomic_write_jsonl(store.state_history_path(manifest.branch), [s.to_dict() for s in history])

    updated_manifest = engine_mod.apply_structural_change(
        data_dir, manifest.sim_id, step=current.step
    )
    assert not (updated_manifest.settings.get("suggested_causal_lines") or [])


def test_accept_suggested_causal_line_moves_it_into_causal_lines(tmp_path):
    """`accept_suggested_causal_line()` 应该把建议追加进
    `settings.causal_lines`，并从 `suggested_causal_lines` 里移除。"""
    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
    )
    store = SimStore.for_root(data_dir, manifest.sim_id)
    current = store.load_current_state(manifest.branch)
    current.structural_change = {
        "detected": True, "kind": "regime_shift", "description": "行业规则发生了根本转变",
        "proposed_fields": {}, "accepted": False, "accepted_at": None,
    }
    history = store.load_history(manifest.branch)
    history[-1] = current
    from mini_agent.utils.atomic_write import atomic_write_jsonl
    atomic_write_jsonl(store.state_history_path(manifest.branch), [s.to_dict() for s in history])

    updated_manifest = engine_mod.apply_structural_change(
        data_dir, manifest.sim_id, step=current.step
    )
    suggestion_id = updated_manifest.settings["suggested_causal_lines"][0]["id"]
    before_count = len(updated_manifest.settings.get("causal_lines") or [])

    final_manifest = engine_mod.accept_suggested_causal_line(
        data_dir, manifest.sim_id, suggestion_id
    )
    causal_lines = final_manifest.settings.get("causal_lines") or []
    assert len(causal_lines) == before_count + 1
    assert causal_lines[-1]["id"] == suggestion_id
    assert causal_lines[-1]["future_tree"]["branches"]
    assert final_manifest.settings.get("suggested_causal_lines") == []


def test_accept_suggested_causal_line_raises_for_unknown_id(tmp_path):
    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
    )
    with pytest.raises(engine_mod.SimEngineError):
        engine_mod.accept_suggested_causal_line(data_dir, manifest.sim_id, "does_not_exist")


def test_reject_suggested_causal_line_removes_it_without_touching_causal_lines(tmp_path):
    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
    )
    store = SimStore.for_root(data_dir, manifest.sim_id)
    current = store.load_current_state(manifest.branch)
    current.structural_change = {
        "detected": True, "kind": "new_mechanism", "description": "新的分红机制",
        "proposed_fields": {}, "accepted": False, "accepted_at": None,
    }
    history = store.load_history(manifest.branch)
    history[-1] = current
    from mini_agent.utils.atomic_write import atomic_write_jsonl
    atomic_write_jsonl(store.state_history_path(manifest.branch), [s.to_dict() for s in history])

    updated_manifest = engine_mod.apply_structural_change(
        data_dir, manifest.sim_id, step=current.step
    )
    suggestion_id = updated_manifest.settings["suggested_causal_lines"][0]["id"]
    before_causal_lines = updated_manifest.settings.get("causal_lines") or []

    final_manifest = engine_mod.reject_suggested_causal_line(
        data_dir, manifest.sim_id, suggestion_id
    )
    assert final_manifest.settings.get("suggested_causal_lines") == []
    assert (final_manifest.settings.get("causal_lines") or []) == before_causal_lines

    # 幂等：再次拒绝同一个（已经不存在的）id 不应该报错。
    engine_mod.reject_suggested_causal_line(data_dir, manifest.sim_id, suggestion_id)


def test_advance_formats_confirmed_structural_changes_hint_for_prompt(tmp_path, monkeypatch):
    """阶段二十三：`manifest.settings.confirmed_structural_changes` 里
    已确认的项，应该被拼进 `advance_step` 的
    `confirmed_structural_changes_hint` 输入，喂给下一步推进的 prompt。"""
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "ws"

    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
        settings={
            "confirmed_structural_changes": [
                {"step": 1, "kind": "new_entity", "description": "联盟：甲乙同盟",
                 "proposed_fields": {}, "accepted_at": "2026-01-01T00:00:00"},
            ]
        },
    )

    step_step = _FakeStep("step")
    captured = {}

    class FakeStoreForAdvance:
        def __init__(self, root):
            pass

        def load(self, name):
            return _FakeWorkflow([step_step])

    class FakeRunnerForAdvance:
        def __init__(self, cfg):
            pass

        def run(self, wf, inputs):
            captured["inputs"] = inputs
            result_file = _write_result_file(
                tmp_path, "advance_result.json",
                {"next_summary": "s2", "narrative": "n", "next_vars": {}, "options": []},
            )
            return SimpleNamespace(
                status="done",
                step_results=[SimpleNamespace(step_id="step", status=_FakeStatus("done"), result_file=result_file)],
            )

    monkeypatch.setattr("mini_agent.workflow.store.WorkflowStore", FakeStoreForAdvance)
    monkeypatch.setattr("mini_agent.workflow.runner.WorkflowRunner", FakeRunnerForAdvance)

    engine_mod.advance(
        cfg=object(), workspace_root=workspace_root, data_dir=data_dir, sim_id=manifest.sim_id,
    )
    assert "联盟：甲乙同盟" in captured["inputs"]["confirmed_structural_changes_hint"]
    assert "[new_entity]" in captured["inputs"]["confirmed_structural_changes_hint"]


# ── 4.3 节（State/Belief 分离，第一批）：beliefs 字段 ─────────────────


def test_sim_state_beliefs_round_trip():
    from world_simulator.state_model import SimState

    s = SimState(
        step=1, summary="", narrative="",
        vars={"market_demand": 100},
        beliefs={"market_demand": {"low": 70, "high": 130, "point": 90}},
    )
    d = s.to_dict()
    assert d["beliefs"] == {"market_demand": {"low": 70, "high": 130, "point": 90}}
    reloaded = SimState.from_dict(d)
    assert reloaded.beliefs == s.beliefs


def test_sim_state_beliefs_defaults_to_empty_dict():
    from world_simulator.state_model import SimState

    s = SimState.from_dict({"step": 0, "summary": "", "narrative": "", "vars": {}})
    assert s.beliefs == {}


def test_resolve_belief_fields_hint_empty_when_not_declared():
    assert spec_mod._resolve_belief_fields_hint(None) == ""
    assert spec_mod._resolve_belief_fields_hint({}) == ""
    assert spec_mod._resolve_belief_fields_hint({"belief_fields": []}) == ""


def test_resolve_belief_fields_hint_joins_declared_fields():
    hint = spec_mod._resolve_belief_fields_hint(
        {"belief_fields": ["market_demand", "competitor_strength"]}
    )
    assert hint == "market_demand、competitor_strength"


# ── 4.3 节（State/Belief 分离，第二批，阶段三十四）：ScenarioDraft
# 的 belief_fields/beliefs + materialize_simulation 落盘 ────────────────


def test_scenario_draft_from_dict_parses_belief_fields_and_beliefs():
    draft = spec_mod.ScenarioDraft.from_dict(
        {
            "title": "t", "summary": "s", "vars": {"market_demand": 100}, "options": [],
            "belief_fields": ["market_demand", " competitor_strength ", "", "  "],
            "beliefs": {"market_demand": {"low": 70, "high": 130, "point": 90}},
        }
    )
    assert draft.belief_fields == ["market_demand", "competitor_strength"]
    assert draft.beliefs == {"market_demand": {"low": 70, "high": 130, "point": 90}}

    draft_without = spec_mod.ScenarioDraft.from_dict(
        {"title": "t", "summary": "s", "vars": {}, "options": []}
    )
    assert draft_without.belief_fields == []
    assert draft_without.beliefs == {}


def test_materialize_simulation_stores_initial_beliefs_on_state0(tmp_path):
    """阶段三十四（4.3 节第二批）：`generate_scenario` 阶段给出的初始
    `beliefs`（角色一开始就存在的认知偏差）应该原样落到
    `state0.beliefs`，`vars` 里的真实值不受影响。"""
    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={"market_demand": 100}, options=[],
        settings={"belief_fields": ["market_demand"]},
        beliefs={"market_demand": {"low": 70, "high": 130, "point": 90}},
    )
    store = SimStore.for_root(data_dir, manifest.sim_id)
    state0 = store.load_current_state("main")
    assert state0.vars["market_demand"] == 100
    assert state0.beliefs == {"market_demand": {"low": 70, "high": 130, "point": 90}}


def test_materialize_simulation_defaults_beliefs_to_empty_dict(tmp_path):
    """未传 `beliefs`（多数模拟场景）时 `state0.beliefs` 应为空字典，
    不影响任何已有行为（向后兼容）。"""
    data_dir = tmp_path / "data"
    manifest = engine_mod.materialize_simulation(
        data_dir, template="life_sim", intent="i", title="t", summary="s",
        vars={}, options=[],
    )
    store = SimStore.for_root(data_dir, manifest.sim_id)
    state0 = store.load_current_state("main")
    assert state0.beliefs == {}


def test_create_simulation_passes_draft_beliefs_through(tmp_path, monkeypatch):
    """`create_simulation()`（CLI/entrypoint 一步到位路径）应该把
    `generate_scenario` 给出的 `draft.beliefs` 原样透传给
    `materialize_simulation()`，同 `field_provenance`/`uncertain_fields`
    的既有透传方式。"""
    data_dir = tmp_path / "data"

    def fake_generate_scenario(cfg, workspace_root, *, template, intent, settings, data_dir):
        return spec_mod.ScenarioDraft.from_dict(
            {
                "title": "t", "summary": "s", "vars": {"market_demand": 100}, "options": [],
                "belief_fields": ["market_demand"],
                "beliefs": {"market_demand": 70},
            }
        )

    monkeypatch.setattr(
        "world_simulator.engine.materialize.generate_scenario", fake_generate_scenario
    )
    manifest = engine_mod.create_simulation(
        cfg=object(), workspace_root=tmp_path / "ws", data_dir=data_dir,
        template="life_sim", intent="i",
    )
    store = SimStore.for_root(data_dir, manifest.sim_id)
    state0 = store.load_current_state("main")
    assert state0.beliefs == {"market_demand": 70}
