"""tests/test_decision_engine_prompts.py — 阶段三十三第一批
（`next_doc/world_simulator_potential_causal_space_and_decision_engine_
plan.md` 4.1 数量软上限 + 4.5 现实行动语义 + 4.8 文件同步改造）验收测试。

只做静态文案断言：确认软上限措辞、现实行动语义约束、可干预性下沉
说明、`continue_` 前缀约定已经写入两个 workflow yaml 和三个模板
SKILL.md，且旧的"按用户设置来，不要自己拍脑袋"式措辞已被替换。不做
真实 LLM 调用（那需要人工跑真实/接近真实场景核对输出质量，见方案
第 6 节风险说明），这里只保证"改动确实落地到文件里"这一层。
"""

from __future__ import annotations

import sys
from pathlib import Path

WORLD_SIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORLD_SIM_ROOT))

WORKFLOWS_DIR = WORLD_SIM_ROOT / "workflows"
SKILLS_DIR = WORLD_SIM_ROOT / "skills"

ADVANCE_STEP_TEXT = (WORKFLOWS_DIR / "advance_step.yaml").read_text(encoding="utf-8")
GENERATE_SCENARIO_TEXT = (WORKFLOWS_DIR / "generate_scenario.yaml").read_text(encoding="utf-8")

TEMPLATE_TEXTS = {
    "life-sim-template": (SKILLS_DIR / "life-sim-template" / "SKILL.md").read_text(encoding="utf-8"),
    "negotiation-template": (SKILLS_DIR / "negotiation-template" / "SKILL.md").read_text(encoding="utf-8"),
    "group-evolution-template": (
        SKILLS_DIR / "group-evolution-template" / "SKILL.md"
    ).read_text(encoding="utf-8"),
}

_OLD_PHRASE = "按用户设置来，不要自己拍脑袋"
_OLD_PHRASE_NEGOTIATION = "候选选项数量按用户设置来"


def test_old_fixed_count_phrasing_removed_from_all_templates():
    """旧的"按设置来，不要自己拍脑袋"（以及谈判模板自己的简化版本）
    必须被删除——这条指令的方向和"选项应该由情境决定"完全相反。"""
    for name, text in TEMPLATE_TEXTS.items():
        assert _OLD_PHRASE not in text, f"{name} 仍残留旧措辞：{_OLD_PHRASE}"
        assert _OLD_PHRASE_NEGOTIATION not in text, f"{name} 仍残留旧措辞：{_OLD_PHRASE_NEGOTIATION}"


def test_option_count_hint_is_soft_cap_not_target():
    """`spec_generator.resolve_hints()` 生成的 `option_count_hint`
    文案必须是软上限语义，不能再是"N 个左右"这种目标值措辞。"""
    from world_simulator import spec_generator as sg

    hint = sg.resolve_hints({}, stage="create")["option_count_hint"]
    assert "软上限" in hint
    assert "个左右" not in hint
    assert "不是目标数量" in hint


def test_advance_step_prompt_has_decision_flow_and_soft_cap():
    assert "候选分支选项的生成流程" in ADVANCE_STEP_TEXT
    assert "软上限" in ADVANCE_STEP_TEXT
    assert "不是目标值" in ADVANCE_STEP_TEXT
    assert "直接给空数组" in ADVANCE_STEP_TEXT


def test_advance_step_prompt_has_reality_action_semantics_constraint():
    assert "不能是抽象的内部指标调节" in ADVANCE_STEP_TEXT
    assert "模型能力 +0.1" in ADVANCE_STEP_TEXT


def test_advance_step_prompt_has_actionability_sinking_guidance():
    assert "不要把这个" in ADVANCE_STEP_TEXT
    assert "宏观事件本身包装成一个候选选项" in ADVANCE_STEP_TEXT


def test_advance_step_prompt_has_continue_prefix_convention():
    assert "continue_" in ADVANCE_STEP_TEXT
    assert "continue_status_quo" in ADVANCE_STEP_TEXT


def test_generate_scenario_prompt_has_soft_cap_and_reality_action_semantics():
    assert "软上限" in GENERATE_SCENARIO_TEXT
    assert "不能是抽象的内部指标调节" in GENERATE_SCENARIO_TEXT


def test_each_template_has_its_own_reality_action_semantics_examples():
    """三个模板不能抄同一组"职业线"例子——各自要有贴合自己场景的
    正反例（方案 4.8 节要求）。"""
    life_sim = TEMPLATE_TEXTS["life-sim-template"]
    negotiation = TEMPLATE_TEXTS["negotiation-template"]
    group_evo = TEMPLATE_TEXTS["group-evolution-template"]

    assert "转向 AI 相关岗位" in life_sim
    assert "提升职业竞争力 20%" in life_sim

    assert "延长交付周期换取价格让步" in negotiation
    assert "要价提高 10%" in negotiation

    assert "发起集体决议改组治理结构" in group_evo
    assert "群体凝聚力 +10%" in group_evo

    # 三个模板的现实行动语义反例不应该完全相同（避免复制粘贴同一组例子）
    assert life_sim != negotiation
    assert negotiation != group_evo


def test_each_template_has_soft_cap_and_continue_prefix_guidance():
    for name, text in TEMPLATE_TEXTS.items():
        assert "软上限" in text, f"{name} 缺少软上限措辞"
        assert "continue_" in text, f"{name} 缺少 continue_ 前缀约定说明"


# ── 阶段三十三第二批（4.2 Decision Reason/Action Reason + 4.3 紧急度 +
#    4.6 continue_ 前缀落地到数据结构层）─────────────────────────────


def test_advance_step_prompt_has_decision_reason_and_action_reason():
    assert "decision_reason" in ADVANCE_STEP_TEXT
    assert "action_reason" in ADVANCE_STEP_TEXT
    # 两者的区分说明必须出现，避免退化成笼统的单一 trigger_reason
    assert "为什么现在" in ADVANCE_STEP_TEXT
    assert "为什么这个具体行动" in ADVANCE_STEP_TEXT


def test_advance_step_prompt_has_urgency_and_time_window():
    assert "urgency" in ADVANCE_STEP_TEXT
    assert "time_window" in ADVANCE_STEP_TEXT
    assert "critical" in ADVANCE_STEP_TEXT
    assert "声明这一档会使模拟自动暂停" in ADVANCE_STEP_TEXT


def test_choice_option_supports_action_reason_urgency_time_window():
    from world_simulator.state_model import ChoiceOption

    opt = ChoiceOption.from_dict({
        "id": "a", "label": "x", "action_reason": "r",
        "urgency": "high", "time_window": "数周内",
    })
    assert opt.action_reason == "r"
    assert opt.urgency == "high"
    assert opt.time_window == "数周内"


def test_sim_state_supports_decision_reason():
    from world_simulator.state_model import SimState

    state = SimState.from_dict({"step": 1, "summary": "s", "decision_reason": "职业转型机会正在形成"})
    assert state.decision_reason == "职业转型机会正在形成"


def test_engine_advance_module_backfills_missing_action_reason():
    """`engine/advance.py` 应该对非空 options 里缺失 action_reason 的
    项补一句通用占位文案，避免展示层出现空白（4.2 节兜底规则）。"""
    source = (WORLD_SIM_ROOT / "world_simulator" / "engine" / "advance.py").read_text(
        encoding="utf-8"
    )
    assert "未说明具体原因，按情境综合判断" in source


def test_engine_advance_module_treats_critical_urgency_as_major_decision():
    source = (WORLD_SIM_ROOT / "world_simulator" / "engine" / "advance.py").read_text(
        encoding="utf-8"
    )
    assert 'o.urgency == "critical"' in source
    assert "major_decision = True" in source


def test_each_template_mentions_action_reason_and_decision_reason():
    for name, text in TEMPLATE_TEXTS.items():
        assert "action_reason" in text, f"{name} 缺少 action_reason 说明"
        assert "decision_reason" in text, f"{name} 缺少 decision_reason 说明"


# ── 阶段三十三第三批（4.4 可干预性下沉 + 4.13 跨线级联的辅助校验/
#    主动计算落地）─────────────────────────────────────────────────


def test_advance_step_prompt_has_causal_graph_hint_placeholder():
    assert "causal_graph_hint" in ADVANCE_STEP_TEXT
    assert "跨因果线级联提示" in ADVANCE_STEP_TEXT


def test_resolve_hints_does_not_include_causal_graph_hint():
    """`causal_graph_hint` 依赖历史数据，`resolve_hints()` 只吃
    `settings`（`generate_scenario` 创建阶段没有历史）——这个提示是
    单独在 `engine/advance.py` 里算好、直接塞进 inputs 字典的，不属于
    `resolve_hints()` 的返回值，这里确认两者没有被误合并。"""
    from world_simulator import spec_generator as sg

    hints = sg.resolve_hints({}, stage="advance")
    assert "causal_graph_hint" not in hints


def test_engine_advance_module_wires_causal_graph_hint_and_option_warnings():
    source = (WORLD_SIM_ROOT / "world_simulator" / "engine" / "advance.py").read_text(
        encoding="utf-8"
    )
    assert "resolve_causal_graph_hint" in source
    assert "causal_graph_hint" in source
    assert "compute_option_warnings" in source
    assert "next_state.option_warnings" in source


def test_option_heuristics_module_exposes_expected_functions():
    from world_simulator.engine import option_heuristics as oh

    assert callable(oh.detect_macro_overlap_warnings)
    assert callable(oh.detect_metric_adjustment_warnings)
    assert callable(oh.compute_option_warnings)


def test_sim_state_supports_option_warnings():
    from world_simulator.state_model import SimState

    state = SimState.from_dict(
        {
            "step": 1,
            "summary": "s",
            "option_warnings": [
                {"option_id": "a", "kind": "metric_adjustment_pattern", "note": "疑似指标调节"}
            ],
        }
    )
    assert state.option_warnings == [
        {"option_id": "a", "kind": "metric_adjustment_pattern", "note": "疑似指标调节"}
    ]


# ── 阶段三十三第六批（4.7 节：组合/条件行动的最小结构化支持）──────────


def test_advance_step_prompt_mentions_action_type():
    assert "action_type" in ADVANCE_STEP_TEXT
    assert "combo" in ADVANCE_STEP_TEXT
    assert "conditional" in ADVANCE_STEP_TEXT


def test_advance_step_prompt_explains_no_forced_multi_step_execution():
    """4.7 节明确"不做步骤序列强制执行"这条简化取舍，prompt 里应该
    说清楚选中 combo/conditional 选项后引擎不会做任何特殊记账。"""
    assert "只推进一次" in ADVANCE_STEP_TEXT or "只推进一步" in ADVANCE_STEP_TEXT
    assert "不会做任何" in ADVANCE_STEP_TEXT or "不做任何特殊记账" in ADVANCE_STEP_TEXT


def test_each_template_mentions_action_type():
    for name, text in TEMPLATE_TEXTS.items():
        assert "action_type" in text, f"{name} 缺少 action_type 说明"


def test_choice_option_action_type_field_exists_with_expected_default():
    from world_simulator.state_model import ChoiceOption

    assert ChoiceOption(id="a", label="x").action_type == "single"



def test_stale_branch_suggestions_hint_empty_when_no_stale_branches():
    from world_simulator.spec_generator import _stale_branch_suggestions_hint

    lines = [
        {
            "id": "tech",
            "advance_every_n_steps": 2,
            "future_tree": {
                "branches": [{"id": "a", "description": "d", "status": "dormant", "first_seen_step": 5}]
            },
        }
    ]
    assert _stale_branch_suggestions_hint(lines, current_step=5) == ""


def test_stale_branch_suggestions_hint_includes_suggestion_when_stale():
    from world_simulator.spec_generator import _stale_branch_suggestions_hint

    lines = [
        {
            "id": "tech",
            "advance_every_n_steps": 1,
            "future_tree": {
                "branches": [{"id": "a", "description": "d", "status": "dormant", "first_seen_step": 0}]
            },
        }
    ]
    hint = _stale_branch_suggestions_hint(lines, current_step=3)
    assert "tech/a" in hint
    assert "仅供参考" in hint


def test_resolve_causal_lines_hint_advance_stage_includes_stale_suggestion():
    from world_simulator.spec_generator import _resolve_causal_lines_hint

    settings = {
        "causal_lines": [
            {
                "id": "tech",
                "label": "技术线",
                "advance_every_n_steps": 1,
                "future_tree": {
                    "branches": [
                        {"id": "a", "description": "d", "status": "dormant", "first_seen_step": 0}
                    ]
                },
            }
        ]
    }
    hint = _resolve_causal_lines_hint(settings, stage="advance", current_step=3)
    assert "tech/a" in hint
