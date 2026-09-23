"""world_simulator/engine/advance.py — 核心推进循环 `advance()`。

从原单体 `engine.py` 拆分而来（阶段三十，`next_doc/
world_simulator_universal_simulator_gap_analysis_and_roadmap_v2_
plan.md` 4.18 节剩余部分）。`advance()` 本身是原 `engine.py` 里
篇幅最长、职责最集中的一个函数（一次 `advance_step` workflow 调用
+ 结果解析 + 资源校验 + 背景角色外推 + 因果线登记/未来树合并 +
知识库沉淀），本次拆分只是把它单独放进一个模块、把已经拆出去的
子职责（资源校验/背景角色外推/因果线/知识库/结构变化解析）改成
从对应子模块导入，函数体本身逐行保持不变，不改变任何行为。
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from world_simulator.engine.background_entities import (
    _apply_background_entity_extrapolation,
    _normalize_background_entities,
)
from world_simulator import causal_tree, relationship
from world_simulator.engine.causal_lines import _apply_tree_updates, _auto_register_causal_lines
from world_simulator.engine.errors import SimAlreadyEndedError, SimEngineError, SimPausedError
from world_simulator.engine.ids import _read_skill_version, _skill_name_for_template
from world_simulator.engine.knowledge import (
    _safe_evaluate_reflexivity,
    _safe_record_causal_links,
    _safe_suggest_knowledge,
)
from world_simulator.decision_validation import compute_option_warnings
from world_simulator.engine.ledger_correction import _safe_correct_field_ledger
from world_simulator.engine.resource_guard import (
    _apply_resource_guard,
    _auto_register_ledger_fields,
    _check_field_ledger,
    _check_resource_relations,
)
from world_simulator.engine.structural_change import (
    _format_confirmed_structural_changes,
    _normalize_structural_change,
)
from world_simulator.problem_discovery import (
    _format_confirmed_problem_suggestions,
    _safe_auto_scan_problems,
)
from world_simulator.spec_generator import (
    resolve_capabilities_hint,
    resolve_causal_graph_hint,
    resolve_hints,
)
from world_simulator.state_model import ChoiceOption, SimState
from world_simulator.store import SimStore


def _normalize_line_update(raw: Dict[str, Any]) -> Dict[str, Any]:
    """原样落盘 skill 给出的单条 `line_updates` 记录，只对可选子字段
    `trend`（第五轮方案 5.3 节）做归一化：合法值透传，非法值/未声明
    时从结果里剔除（不保留一个 `None` 占位，同 `causal_links`/
    `key_uncertainty` 等既有字段"未给出即不出现"的一贯风格）。其余
    字段（`time_label`/`summary`/`advanced` 等）不做任何改动。
    """
    updated = dict(raw)
    trend = causal_tree.normalize_line_trend(updated.get("trend"))
    if trend is not None:
        updated["trend"] = trend
    else:
        updated.pop("trend", None)
    return updated


def _collect_trigger_node_ids(tree_updates_audit: Any) -> List[str]:
    """从 `causal_tree.apply_tree_updates()` 的审计结果里收集这一步
    "变得值得关注"的 KeyNode（`future_tree` 分支）id（4.10 节
    `trigger_node_ids`，阶段三十三第五批）：这一步被印证的分支
    （`confirmed_branch`）、被声明为 `emerging`/`active` 的分支
    （`status_updates`）、以及新长出来的分支（`new_branch_ids`）——
    这三类都是"这一步因果树上出现了值得放进决策背景里的节点"，直接
    从已经算好的审计数据派生，不需要 skill 再额外声明一遍。按出现
    顺序去重，不保证任何排序含义。
    """
    node_ids: List[str] = []
    for entry in tree_updates_audit or []:
        if not isinstance(entry, dict):
            continue
        confirmed = str(entry.get("confirmed_branch") or "").strip()
        if confirmed:
            node_ids.append(confirmed)
        for status_update in entry.get("status_updates") or []:
            if not isinstance(status_update, dict):
                continue
            if status_update.get("status") in ("emerging", "active"):
                branch_id = str(status_update.get("branch_id") or "").strip()
                if branch_id:
                    node_ids.append(branch_id)
        for new_id in entry.get("new_branch_ids") or []:
            new_id = str(new_id).strip()
            if new_id:
                node_ids.append(new_id)
    seen: set = set()
    deduped: List[str] = []
    for node_id in node_ids:
        if node_id not in seen:
            seen.add(node_id)
            deduped.append(node_id)
    return deduped


_URGENCY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_RISK_RANK = {"low": 0, "medium": 1, "high": 2}


def _max_by_rank(values: List[str], rank: Dict[str, int]) -> Optional[str]:
    """在已知取值集合的排名表里取"最高档"，忽略排名表里不认识的
    取值（不应该出现，但防御性地不因为脏数据而报错）；`values` 为
    空返回 `None`。"""
    known = [v for v in values if v in rank]
    if not known:
        return None
    return max(known, key=lambda v: rank[v])


def _build_decision_opportunity(next_state: SimState) -> Optional[Dict[str, Any]]:
    """构造 `SimState.decision_opportunity`（4.10 节，阶段三十三第
    五批，参考文档 Decision Opportunity 的精简版；`max_urgency`/
    `max_risk`/`baseline_option_id` 三个字段是第五轮方案 5.2 节新增
    的批次级聚合，`next_doc/world_simulator_decision_engine_round2_
    gap_analysis_plan.md`）。`options` 为空时返回 `None`——"这一步
    没有形成需要特别说明背景的决策机会"；非空时总是返回一个 dict
    （哪怕各字段都是空/None），`app.py` 展示层只按各字段是否非空
    决定要不要渲染对应小节，不看容器本身是不是 `None`。

    5.2 节的三个聚合字段都是对 `next_state.options` 已有逐选项
    字段的**纯计算聚合**，不发起任何新的 LLM 判断、不新增任何
    LLM 输出字段：
    - `max_urgency`/`max_risk`：取这一批 `options` 里已声明
      `urgency`/`risk_level` 的选项中最高的一档；全部未声明时
      为 `None`。`opportunity_window`（决策机会本身的时间窗口）
      按方案设计不单独存储——它在语义上就是"最紧急那个选项的
      `time_window`"，直接从对应选项上读取即可，避免同一份信息
      在两处维护导致不一致，因此这里只算 `max_urgency` 本身，
      不重复存一份对应的 `time_window`。
    - `baseline_option_id`：这一批 `options` 里第一个 `id` 以
      `continue_` 开头的选项 id（4.6 节"维持现状"约定），没有
      则为 `None`——只是把已有的命名约定显式暴露成一个结构化
      引用，不改变 4.6 节本身的产出逻辑。
    """
    if not next_state.options:
        return None
    urgencies = [opt.urgency for opt in next_state.options if opt.urgency]
    risk_levels = [opt.risk_level for opt in next_state.options if opt.risk_level]
    baseline_option_id = next(
        (opt.id for opt in next_state.options if str(opt.id or "").startswith("continue_")),
        None,
    )
    return {
        "trigger_line_ids": sorted(next_state.line_updates.keys()) if next_state.line_updates else [],
        "trigger_node_ids": _collect_trigger_node_ids(next_state.tree_updates),
        "decision_reason": next_state.decision_reason,
        "context_note": "",
        "max_urgency": _max_by_rank(urgencies, _URGENCY_RANK),
        "max_risk": _max_by_rank(risk_levels, _RISK_RANK),
        "baseline_option_id": baseline_option_id,
    }


def advance(
    cfg,
    workspace_root: Path,
    data_dir: Path,
    sim_id: str,
    *,
    choice_option_id: Optional[str] = None,
    custom_option: Optional[Dict[str, str]] = None,
    decision_context: str = "",
    chosen_by: str = "user",
    allow_custom_options: bool = False,
) -> SimState:
    """推进模拟实例一步。

    Args:
        choice_option_id: 用户/代理选中的候选选项 id（当前状态
            `options` 里的一个）；为 None 时表示"不指定，让引擎给出
            默认走向"（对应当前状态 `options` 为空数组的情况，也允许
            非空时仍不指定——由 skill 判断怎么给默认值）。
        custom_option: 手动挡下用户自己新增的、**不在**当前候选列表里的
            选项，形如 `{"label": ..., "description": ...}`——系统给出的
            候选方向终究只是一种建议，用户经常会想到"更合理的第三个
            选项"，这个参数就是给这种场景用的：与 `choice_option_id`
            互斥，同时给出时以 `custom_option` 为准（调用方不应该同时
            传两个，这里只是不额外报错，取更明确的那个）。engine 会给它
            生成一个 `custom_` 前缀的临时 id，落盘方式与手动挑中候选列表
            里的选项完全一样，不需要预先出现在 `current.options` 里。
        decision_context: 阶段四自动挡使用，非空表示这是一次代理代选：
            `choice_option_id`/`custom_option` 都应该为 None，engine 会把
            当前候选列表喂给 skill，由 skill 自己选一个并在结果里回填
            `chosen_option_id`/`chosen_reason`（engine 校验后落盘，不
            直接信任 LLM）；如果 `decision_context` 里明确允许代理跳出
            候选列表（`autopilot.allow_custom_options`），skill 也可以
            改为回填 `custom_option_label`/`custom_option_description`，
            提出一个候选列表之外的新方向，engine 同样会接收并落盘——
            处理方式与用户手动传 `custom_option` 一致，只是 `chosen_by`
            记为 `"autopilot"`。
        chosen_by: 当 `choice_option_id`/`custom_option` 非 None（手动挡）
            时，记入当前状态 `chosen_by` 字段的值，通常是 `"user"`；
            自动挡代选场景这个值会被引擎内部推导出的 `"autopilot"`
            覆盖，调用方不需要自己判断。
        allow_custom_options: 仅自动挡代选场景使用，对应
            `manifest.autopilot.get("allow_custom_options")`。LLM 并不总是
            严格遵守"跳出候选列表要用 custom_option_label 字段"的约定——
            它经常直接在 `chosen_option_id` 里编一个不存在于候选列表的新
            id。当这个开关为 True 时，如果 `chosen_option_id` 不在候选
            列表里，不再直接报错中止，而是把它当成一个自定义选项收下
            （标签优先取 `custom_option_label`，没有的话退化成用这个
            编造的 id 本身当标签）；为 False 时维持原来的严格校验，
            直接报错中止，避免把脏数据静默写进历史。
    """
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()

    if manifest.status == "ended":
        raise SimAlreadyEndedError(f"模拟实例已结束，无法继续推进：{sim_id}")
    if manifest.status == "paused":
        raise SimPausedError(f"模拟实例处于暂停状态，请先恢复再推进：{sim_id}")

    branch = manifest.branch
    current = store.load_current_state(branch)
    if current is None:
        raise SimEngineError(f"模拟实例缺少当前状态，数据可能已损坏：{sim_id}")

    chosen_option: Optional[ChoiceOption] = None
    if custom_option is not None:
        custom_label = str(custom_option.get("label") or "").strip()
        if not custom_label:
            raise SimEngineError("自定义选项至少需要填写标题（label）")
        chosen_option = ChoiceOption(
            id=f"custom_{secrets.token_hex(4)}",
            label=custom_label,
            description=str(custom_option.get("description") or ""),
        )
    elif choice_option_id is not None:
        chosen_option = next((o for o in current.options if o.id == choice_option_id), None)
        if chosen_option is None:
            raise SimEngineError(
                f"选项 id={choice_option_id!r} 不在当前状态的候选列表中"
                f"（当前候选：{[o.id for o in current.options]}）"
            )

    from mini_agent.workflow.runner import WorkflowRunner
    from mini_agent.workflow.store import WorkflowStore

    wf_store = WorkflowStore(Path(workspace_root))
    skill_name = _skill_name_for_template(manifest.template)
    runner = WorkflowRunner(cfg)

    history_for_prompt = store.load_history(branch)

    shared_inputs = {
        "title": manifest.title,
        "current_summary": current.summary,
        "current_vars_json": json.dumps(current.vars, ensure_ascii=False),
        "current_step": str(current.step),
        "current_time_label": current.time_label or "",
        "current_time_granularity": current.time_granularity or "无（这是第一步推进，还没有\"上一步\"可参考，请按情境自行判断一个合适的起始粒度）",
        "chosen_option_json": json.dumps(
            chosen_option.to_dict() if chosen_option else {}, ensure_ascii=False
        ),
        "current_options_json": json.dumps(
            [o.to_dict() for o in current.options], ensure_ascii=False
        ),
        "decision_context": decision_context,
        "calibration_notes": str(manifest.settings.get("calibration_notes") or ""),
        "relevant_knowledge_hint": _safe_suggest_knowledge(
            data_dir, f"{manifest.intent} {current.summary}", template=manifest.template
        ),
        "confirmed_structural_changes_hint": _format_confirmed_structural_changes(
            manifest.settings.get("confirmed_structural_changes")
        ),
        "confirmed_problem_suggestions_hint": _format_confirmed_problem_suggestions(
            manifest.settings.get("confirmed_problem_suggestions")
        ),
        # 阶段三十三第三批（4.13 节）：把历史 `causal_links` 聚合出的
        # "线到线"邻接关系反过来喂给这一次推进的 prompt——纯只读聚合，
        # 不影响下面 `chosen_option` 落盘那一段对历史的读写。
        "causal_graph_hint": resolve_causal_graph_hint(
            manifest.settings, history_for_prompt
        ),
        # 第十一轮 2.4 节（`next_doc/world_simulator_eleventh_round_
        # remaining_gaps_plan.md`，跳过方案原文建议的人工小范围验证、
        # 直接接入正式 prompt，决定已记录在该文档与 PROJECT.md）：
        # 把历史累计的 `capabilities_gained` 喂给候选选项生成 prompt，
        # 同样是纯只读聚合，不影响任何落盘逻辑。
        "capabilities_hint": resolve_capabilities_hint(history_for_prompt),
        **resolve_hints(manifest.settings, current_step=current.step + 1),
    }


    # 4.12 节第三步（阶段三十三第八批，`next_doc/
    # world_simulator_potential_causal_space_and_decision_engine_plan.md`）：
    # 默认仍然是原来的单次调用（`advance_step.yaml`，向后兼容、不增加
    # 延迟和 token 成本）；`manifest.settings.split_decision_calls` 为
    # True 时改为拆成 `world_evolve` + `decision_generate` 两次独立调用，
    # 更贴合参考文档"World Engine 与 Decision Engine 分工"的设想，代价
    # 是延迟和成本翻倍——是否启用由用户在创建向导/设置面板里自行选择，
    # 不作为新默认行为（见方案原文 4.12 节第三步"取舍说明"里的成本
    # 顾虑）。两条路径最终都产出同一份 `data: Dict[str, Any]`，下面
    # （解析 `chosen_option_id`/构造 `next_state` 等）完全不需要区分
    # 走的是哪条路径。
    if not bool(manifest.settings.get("split_decision_calls")):
        wf = wf_store.load("advance_step")
        if wf is None:
            raise SimEngineError("找不到 workflow 定义 advance_step")
        found = False
        for step in wf.steps:
            if step.id == "step":
                step.skill_name = skill_name
                found = True
                break
        if not found:
            raise SimEngineError("advance_step workflow 中找不到 step id=step")

        result = runner.run(wf, shared_inputs)
        if result.status != "done":
            failed = [
                f"{sr.step_id}({sr.status.value}): {sr.error}"
                for sr in result.step_results
                if sr.status.value != "done"
            ]
            raise SimEngineError(
                f"advance_step workflow 执行未成功（skill={skill_name}）："
                f"status={result.status}；" + "；".join(failed)
            )

        step_result = next((sr for sr in result.step_results if sr.step_id == "step"), None)
        if step_result is None or not step_result.result_file:
            raise SimEngineError("step 步骤未产出 result_file，无法解析推进结果")

        data: Dict[str, Any] = json.loads(Path(step_result.result_file).read_text(encoding="utf-8"))
    else:
        wf_evolve = wf_store.load("world_evolve")
        if wf_evolve is None:
            raise SimEngineError("找不到 workflow 定义 world_evolve（拆分调用模式需要）")
        found = False
        for step in wf_evolve.steps:
            if step.id == "world_evolve":
                step.skill_name = skill_name
                found = True
                break
        if not found:
            raise SimEngineError("world_evolve workflow 中找不到 step id=world_evolve")

        result_evolve = runner.run(wf_evolve, shared_inputs)
        if result_evolve.status != "done":
            failed = [
                f"{sr.step_id}({sr.status.value}): {sr.error}"
                for sr in result_evolve.step_results
                if sr.status.value != "done"
            ]
            raise SimEngineError(
                f"world_evolve workflow 执行未成功（skill={skill_name}）："
                f"status={result_evolve.status}；" + "；".join(failed)
            )

        step_result_evolve = next(
            (sr for sr in result_evolve.step_results if sr.step_id == "world_evolve"), None
        )
        if step_result_evolve is None or not step_result_evolve.result_file:
            raise SimEngineError("world_evolve 步骤未产出 result_file，无法解析推进结果")

        data_evolve: Dict[str, Any] = json.loads(
            Path(step_result_evolve.result_file).read_text(encoding="utf-8")
        )

        wf_decide = wf_store.load("decision_generate")
        if wf_decide is None:
            raise SimEngineError("找不到 workflow 定义 decision_generate（拆分调用模式需要）")
        found = False
        for step in wf_decide.steps:
            if step.id == "decision_generate":
                step.skill_name = skill_name
                found = True
                break
        if not found:
            raise SimEngineError("decision_generate workflow 中找不到 step id=decision_generate")

        # 把 world_evolve 已经决定的"世界怎么变化了"作为既成事实喂给
        # decision_generate——它不应该、也不需要重新生成 next_vars/
        # narrative 等世界状态字段，只专注判断要不要出现候选选项。
        decide_inputs = {
            **shared_inputs,
            "evolved_summary": str(data_evolve.get("next_summary", "")),
            "evolved_narrative": str(data_evolve.get("narrative", "")),
            "evolved_vars_json": json.dumps(data_evolve.get("next_vars") or {}, ensure_ascii=False),
            "evolved_time_label": str(data_evolve.get("time_label", "") or ""),
            "evolved_time_granularity": str(
                data_evolve.get("next_time_granularity", "") or current.time_granularity or ""
            ),
            "evolved_key_drivers_json": json.dumps(
                data_evolve.get("key_drivers") or [], ensure_ascii=False
            ),
            "evolved_tree_updates_json": json.dumps(
                data_evolve.get("tree_updates") or [], ensure_ascii=False
            ),
        }
        result_decide = runner.run(wf_decide, decide_inputs)
        if result_decide.status != "done":
            failed = [
                f"{sr.step_id}({sr.status.value}): {sr.error}"
                for sr in result_decide.step_results
                if sr.status.value != "done"
            ]
            raise SimEngineError(
                f"decision_generate workflow 执行未成功（skill={skill_name}）："
                f"status={result_decide.status}；" + "；".join(failed)
            )

        step_result_decide = next(
            (sr for sr in result_decide.step_results if sr.step_id == "decision_generate"), None
        )
        if step_result_decide is None or not step_result_decide.result_file:
            raise SimEngineError("decision_generate 步骤未产出 result_file，无法解析推进结果")

        data_decide: Dict[str, Any] = json.loads(
            Path(step_result_decide.result_file).read_text(encoding="utf-8")
        )

        # 两份结果按字段合并：`world_evolve` 负责世界状态类字段，
        # `decision_generate` 负责 `options`/`decision_reason` 等决策
        # 类字段，设计上两者的 key 不应重叠；万一 `decision_generate`
        # 意外也输出了某个世界状态字段（比如手滑重复给了 `next_vars`），
        # `data_evolve` 放在后面覆盖，保证世界状态字段始终来自负责它的
        # 那次调用，不会被决策调用意外覆盖。
        data = {**data_decide, **data_evolve}

    # 确定这一步"实际生效的选择"：
    #   1) 调用方显式传了 choice_option_id（手动挡，`chosen_option` 已在
    #      上面校验过） → 直接用它。
    #   2) 调用方没传，但 decision_context 非空（自动挡代选） → skill
    #      应该在 result 里回填 chosen_option_id/chosen_reason（见
    #      advance_step.yaml / life-sim-template SKILL.md 的"选择逻辑"），
    #      这里读回来并校验它确实是候选列表里的合法 id——LLM 偶尔会编造
    #      不存在的 id，不能直接信任，否则会把脏数据写进历史。
    #   3) 都没有 → 这一步没有"选择"这回事（比如当前状态本来就没有候选
    #      分支），不记录。
    effective_chosen_by = chosen_by
    effective_chosen_reason: Optional[str] = None
    if chosen_option is None and decision_context:
        auto_choice_id = data.get("chosen_option_id")
        auto_custom_label = str(data.get("custom_option_label") or "").strip()
        if auto_choice_id:
            chosen_option = next((o for o in current.options if o.id == auto_choice_id), None)
            if chosen_option is None:
                if not allow_custom_options:
                    raise SimEngineError(
                        f"自动挡代选返回的 chosen_option_id={auto_choice_id!r} 不在候选列表中"
                        f"（当前候选：{[o.id for o in current.options]}），已中止本次推进"
                    )
                # allow_custom_options=True 时，不再把"id 编造得不存在"
                # 当成硬错误：LLM 经常并不遵守"跳出列表要改用
                # custom_option_label 字段"的约定，而是直接把编出来的新
                # 方向塞进 chosen_option_id——既然已经明确允许代理跳出候选
                # 列表，就该把这种情况也当作一次合法的自定义选项收下，而
                # 不是中止推进。标签优先用 custom_option_label（如果 LLM
                # 恰好两个字段都填了），否则退化为用这个编造的 id 本身
                # 当标签（好过什么都没有）。
                fallback_label = auto_custom_label or auto_choice_id
                chosen_option = ChoiceOption(
                    id=f"custom_{secrets.token_hex(4)}",
                    label=fallback_label,
                    description=str(data.get("custom_option_description") or ""),
                )
                effective_chosen_by = "autopilot"
                effective_chosen_reason = (
                    data.get("chosen_reason")
                    or f"候选列表之外的自定义选项（原始 chosen_option_id={auto_choice_id!r}）"
                )
            else:
                effective_chosen_by = "autopilot"
                effective_chosen_reason = data.get("chosen_reason") or None
        elif auto_custom_label:
            # 代理判断候选列表里没有足够合理的选项，跳出列表提出了一个
            # 新方向（只有 `decision_context` 明确允许时 skill 才会这么
            # 做，见 `autopilot._build_decision_context`）——落盘方式与
            # 用户手动传 `custom_option` 完全一致，只是标记为 autopilot。
            chosen_option = ChoiceOption(
                id=f"custom_{secrets.token_hex(4)}",
                label=auto_custom_label,
                description=str(data.get("custom_option_description") or ""),
            )
            effective_chosen_by = "autopilot"
            effective_chosen_reason = data.get("chosen_reason") or "候选列表之外的自定义选项"

    # 把这一步的选择记回*当前*状态节点（见 state_model.SimState docstring），
    # 再落盘一份修正后的当前节点——历史里对应 step 的条目需要同步更新，
    # 因此这里重写整份历史里最后一条（阶段二十七之后 `append_state()`
    # 本身已经是真正的追加写，这里属于"修改已落盘的最后一条"这种例外
    # 场景，只能读回整份历史、在内存里改最后一条、整体重写，和
    # `append_state()` 的追加写语义并不冲突——只是这一处需要的是
    # "更新"而不是"追加"）。
    if chosen_option is not None:
        current.chosen_option_id = chosen_option.id
        current.chosen_by = effective_chosen_by
        current.chosen_reason = effective_chosen_reason
        history = store.load_history(branch)
        if history and history[-1].step == current.step:
            history[-1] = current
        else:
            history.append(current)
        from mini_agent.utils.atomic_write import atomic_write_jsonl

        atomic_write_jsonl(
            store.state_history_path(branch),
            [s.to_dict() for s in history],
        )

    # 时间粒度：默认延续"上一步实际用的粒度"（`current.time_granularity`），
    # 只有 skill 明确给出不同的 `next_time_granularity` 时才算"切换"——
    # `granularity_changed` 由 engine 自己比较算出，不直接信任 skill 是否
    # 老实报告，避免"值其实没变但 skill 瞎标了 changed"这种不一致。
    prev_granularity = current.time_granularity or ""
    raw_next_granularity = str(data.get("next_time_granularity", "") or "").strip()
    next_granularity = raw_next_granularity or prev_granularity
    granularity_changed = bool(prev_granularity) and bool(raw_next_granularity) and raw_next_granularity != prev_granularity
    granularity_reason = str(data.get("granularity_reason") or "").strip() or None
    if not granularity_changed:
        granularity_reason = None

    # 资源类字段代码层校验（阶段九，4.1 节）：LLM 给的 next_vars 可能
    # 把某个声明为"资源类"的字段算出负数（花的钱超过账上现金这种最
    # 基础的错误）——不拒绝这次推进，就地把越界字段夹到下限，越界详情
    # 记入 next_state.resource_violations 供时间线展示，保持透明。
    next_vars = dict(data.get("next_vars") or {})
    resource_violations = _apply_resource_guard(
        next_vars, manifest.settings.get("resource_fields")
    )

    # 资源转移关系的一致性检查（阶段十六，4.8 节）：用夹值*之前*的
    # `current.vars` 对比夹值*之后*的 `next_vars`——夹值本身也是这一步
    # 真实落盘的变化量的一部分，检查应该看"最终生效的变化"，而不是
    # LLM 原始给出的、可能已经因为下限校验被修正过的值。
    # 显式资源流水（第九轮批次一，资源转移一致性机制根本性改进）：
    # skill 可选给出这一步实际搬运的量，报了就优先按账核对，不再
    # 只靠"整体快照差分"反推——见 `resource_guard._check_resource_
    # relations()` 的说明。
    resource_transfers = [
        dict(x) for x in (data.get("resource_transfers") or []) if isinstance(x, dict)
    ]
    relation_violations = _check_resource_relations(
        current.vars,
        next_vars,
        manifest.settings.get("resource_relations"),
        resource_transfers,
    )

    # 字段变化台账审计校验（第十五轮，`next_doc/
    # world_simulator_fifteenth_round_field_ledger_plan.md`）：和资源
    # 转移关系一致性检查一样，用夹值*之前*的 `current.vars` 对比夹值
    # *之后*的 `next_vars`——夹值本身也是这一步真实落盘的变化量的一
    # 部分。`tracked_ledger_fields` 决定"未记账变化"检查覆盖哪些字段
    # （不局限于 `resource_fields`，见 `SimManifest.settings.tracked_
    # ledger_fields` 的说明）。
    tracked_ledger_fields = manifest.settings.get("tracked_ledger_fields")
    field_ledger, ledger_violations = _check_field_ledger(
        current.vars, next_vars, data.get("field_ledger"), tracked_ledger_fields
    )
    if ledger_violations:
        # 校验失败先尝试一次范围有限的反馈修正调用（3.4 节），而不是
        # 直接降级为纯标记；修正调用本身是可选旁路，任何基础设施异常
        # 都会安全降级为原样返回，不影响本次推进的主流程。
        next_vars, field_ledger, ledger_violations = _safe_correct_field_ledger(
            cfg,
            workspace_root,
            current_vars=current.vars,
            next_vars=next_vars,
            field_ledger=field_ledger,
            ledger_violations=ledger_violations,
            tracked_ledger_fields=tracked_ledger_fields,
            narrative_hint=str(data.get("narrative", "") or data.get("next_summary", "") or ""),
        )

    # 背景角色的简单趋势外推（Hierarchical Agent，4.10 节设计草案第
    # 一步）：需要"上一步"（当前状态*之前*那一步）的 vars 才能算变化量，
    # 用当前分支的完整历史往前找一条；第一次推进（历史只有 1 条，即
    # current 自己）时没有"上一步"可参考，外推函数内部会安全处理
    # （退化为"原样保留这一步的值"，不是编造一个趋势）。
    background_entities = _normalize_background_entities(
        manifest.settings.get("background_entities")
        if manifest.settings.get("hierarchical_agent_mode") else None
    )
    background_entities_applied: list = []
    if background_entities:
        history_for_bg = store.load_history(branch)
        previous_state = history_for_bg[-2] if len(history_for_bg) >= 2 else None
        previous_vars = previous_state.vars if previous_state is not None else {}
        background_entities_applied = _apply_background_entity_extrapolation(
            current.vars, next_vars, previous_vars, background_entities
        )

    # 4.2 节（阶段三十三第二批）：`action_reason` 缺失时不重试整步
    # （沿用项目一贯"宽松兜底"风格），对非空 `options` 数组里缺失
    # `action_reason` 的项目补一句通用占位文案，避免展示层出现空白。
    parsed_options = [ChoiceOption.from_dict(o) for o in (data.get("options") or [])]
    for opt in parsed_options:
        if not opt.action_reason:
            opt.action_reason = "未说明具体原因，按情境综合判断"

    next_state = SimState(
        step=current.step + 1,
        summary=str(data.get("next_summary", "")),
        narrative=str(data.get("narrative", "")),
        vars=next_vars,
        options=parsed_options,
        major_decision=bool(data.get("major_decision", False)),
        time_label=str(data.get("time_label", "") or ""),
        time_granularity=next_granularity,
        granularity_changed=granularity_changed,
        granularity_reason=granularity_reason,
        resource_violations=resource_violations,
        relation_violations=relation_violations,
        resource_transfers=resource_transfers,
        field_ledger=field_ledger,
        ledger_violations=ledger_violations,
        background_entities_applied=background_entities_applied,
        uncertain_fields=list(data.get("uncertain_fields") or []),
        key_drivers=[str(x) for x in (data.get("key_drivers") or [])],
        beliefs=dict(data.get("beliefs") or {}) if isinstance(data.get("beliefs"), dict) else {},
        causal_links=[
            dict(x) for x in (data.get("causal_links") or []) if isinstance(x, dict)
        ],
        line_updates={
            str(k): _normalize_line_update(v) for k, v in (data.get("line_updates") or {}).items()
            if isinstance(v, dict)
        },
        structural_change=_normalize_structural_change(data.get("structural_change")),
        decision_reason=str(data.get("decision_reason", "") or ""),
        problems=[
            dict(x) for x in (data.get("problems") or []) if isinstance(x, dict)
        ],
        capabilities_gained=[
            dict(x) for x in (data.get("capabilities_gained") or []) if isinstance(x, dict)
        ],
        skill_version=_read_skill_version(workspace_root, manifest.template),
    )

    # 4.3 节（阶段三十三第二批）：存在任意一项 `urgency == "critical"`
    # 的选项，按等同于 `major_decision = True` 的方式处理——`critical`
    # 直接触发暂停等待用户介入，不需要 skill 显式再报一次
    # `major_decision`（两者语义等价，`major_decision` 本身仍然保留，
    # 供不使用 `urgency` 字段的旧场景/模板继续工作）。
    if any(o.urgency == "critical" for o in next_state.options):
        next_state.major_decision = True

    # 4.4/4.5 节辅助校验（阶段三十三第三批）：宏观事件重合/内部指标
    # 调节语言的启发式检测——弱信号、仅展示、不阻断流程，见
    # `option_heuristics.py` docstring。
    next_state.option_warnings = compute_option_warnings(
        next_state.options, next_state.key_drivers
    )

    # 因果线不应该有前置条件（用户要求）：`line_updates`/
    # `causal_links.line_id` 里只要出现了 `manifest.settings.causal_lines`
    # 还没登记过的新 id，就自动把它登记成一条正式的因果线——不要求用户
    # 必须先在创建向导/设置面板里手填 JSON 才能让"因果线总览"视图出现
    # 内容。这是纯粹的"发现并登记"，不像 `structural_change` 那样需要
    # 用户手动"采纳"：因果线本身只是一种展示/组织维度，登记错了也不会
    # 污染 `vars`/推进逻辑，风险和 `structural_change` 不在同一量级，
    # 不需要额外的确认环节。新登记的线同样会带上兜底的默认未来树（阶段
    # 二十六），保证"自发出现的线"不会缺未来展望。
    _auto_register_causal_lines(manifest, next_state)

    # 第十五轮 3.2 节：把这一步 `field_ledger` 里实际出现过的字段自动
    # 并入 `manifest.settings.tracked_ledger_fields`，写法与因果线
    # 自动登记一致——发现即登记，不要求提前声明。只增不减。
    manifest.settings = {
        **manifest.settings,
        "tracked_ledger_fields": _auto_register_ledger_fields(
            manifest.settings.get("tracked_ledger_fields"), next_state.field_ledger
        ),
    }

    # 阶段二十六：合并这一步对因果线"未来树"的修正（印证/排除/新增
    # 分支），必须在 `_auto_register_causal_lines()` 之后调用——新登记
    # 的线需要先存在于 `manifest.settings.causal_lines` 里，
    # `tree_updates` 才有对象可以合并。合并结果同时记回 `next_state.
    # tree_updates`（审计摘要），需要在 `store.append_state()` 之前
    # 完成，否则历史里就存不到这份摘要。
    next_state.tree_updates = _apply_tree_updates(manifest, next_state, data)

    # 4.10 节（阶段三十三第五批）：把这一批 options 共享的决策背景
    # 收纳进一个精简容器（`decision_opportunity`），必须在上面
    # `_apply_tree_updates()` 之后计算——`trigger_node_ids` 需要读
    # 刚生成的 `tree_updates` 审计结果。
    next_state.decision_opportunity = _build_decision_opportunity(next_state)

    store.append_state(next_state, branch=branch)

    # 阶段二十（4.12 节 2.）：把这一步的结构化因果链沉淀进跨模拟知识库。
    # 纯旁路操作，落盘之后才做、失败不影响本次推进（见
    # `_safe_record_causal_links` docstring）。
    _safe_record_causal_links(
        data_dir,
        sim_id=sim_id,
        template=manifest.template,
        causal_links=next_state.causal_links,
        step=next_state.step,
    )

    # 阶段三十六第二批（2.2 节）：把这一步 skill 声明的
    # `triggered_relationships` 记入 `settings.relationship_pending_
    # effects`，供 `_resolve_relationship_hint()` 在到期那一步提醒
    # skill "该体现效果了"。纯粹的排队记账，不做任何自动化的数值
    # 传播——是否真的体现、怎么体现仍完全由后续某一步的 skill 自行
    # 判断。非法/编造的引用会被 `queue_pending_effect()` 静默忽略
    # （delay_steps 退化为 0，等于下一步就提醒），不中断本次推进。
    raw_triggered = data.get("triggered_relationships")
    if isinstance(raw_triggered, list) and raw_triggered:
        pending = manifest.settings.get("relationship_pending_effects")
        for ref in raw_triggered:
            pending = relationship.queue_pending_effect(
                pending,
                relationships=manifest.settings.get("relationships"),
                relationship_ref=str(ref),
                triggered_at_step=next_state.step,
            )
        manifest.settings["relationship_pending_effects"] = pending

    # 第十轮批次一（`next_doc/world_simulator_tenth_round_problem_
    # discovery_automation_plan.md` 3 节）：Problem Discovery 自动
    # 扫描——按 `settings.problem_discovery_auto_scan_interval` 周期
    # 触发，不再要求用户记得去点"扫描潜在问题"按钮；手动挡/自动挡都会
    # 经过这里，`effective_chosen_by == "autopilot"` 是判断"这一步是
    # 不是自动挡代选"的既有信号，自动挡下没有人来点"确认关注"，扫描
    # 结果会额外自动写入 confirmed_problem_suggestions（见
    # `_safe_auto_scan_problems` docstring）。必须放在下面这次统一的
    # `store.save_manifest(manifest)` 之前——它只修改内存里的
    # `manifest.settings`，复用这一次落盘，不单独多一次 IO；任何异常
    # 都被内部吞掉，不影响本次推进已经产生的返回值。
    _safe_auto_scan_problems(
        cfg, workspace_root, store, manifest,
        branch=branch, next_state=next_state,
        auto_confirm=(effective_chosen_by == "autopilot"),
    )

    manifest.current_step = next_state.step
    store.save_manifest(manifest)

    # 阶段三十六第四批（2.4 节反身性最小诠释）：检查这个分支是否有
    # 尚未处理的复盘报告，判断这一步的选择是否让"选择模式与复盘建议
    # 方向一致"这件事有了足够样本可以下结论。纯旁路观察，不影响本次
    # 推进已经产生的返回值/落盘结果（见 `_safe_evaluate_reflexivity`
    # docstring）。
    _safe_evaluate_reflexivity(data_dir, sim_id, branch=branch)

    return next_state


# ─────────────────────────────────────────────────────────────
# fast_forward() — Event-Driven 决策点引擎 + Observer View 完整版
#
# 设计依据：`next_doc/world_simulator_event_driven_engine_and_full_
# architecture_plan.md` 2.1 节（第一批）。不重新设计推进循环本身，
# 只是循环调用已有的单步 `advance()`，复用两个早已存在的信号
# （`SimState.major_decision`/`SimState.options`）判断"这一步要不要
# 停下来给用户看"——跳过的每一步仍然逐一完整落盘（`advance()` 内部
# 的 `store.append_state()` 不变），不会产生历史空洞，下游的
# `attribution.py`/`retrospective.py`/`quality_signals.py` 等统计
# 功能读到的历史和"没有用快进、一步步手动点"完全一致。
# ─────────────────────────────────────────────────────────────


@dataclass
class FastForwardResult:
    """`fast_forward()` 的返回值：一次"快进"调用的完整结果。"""

    sim_id: str
    branch: str
    start_step: int
    """快进开始前的 `step`（即调用前的 `current.step`）。"""
    final_state: SimState
    """快进结束时停在的那一步的完整状态（触发停止的那一步，或者
    达到 `max_steps` 时最后跑完的那一步）。"""
    steps_run: int
    """这次快进实际调用了多少次 `advance()`（不含快进前已有的步数）。"""
    skipped_states: List[SimState] = field(default_factory=list)
    """被"折叠"展示的中间步骤（不含 `final_state` 本身）——这些步骤
    在 `SimStore.load_history()` 里和其它步骤一样完整存在，这里只是
    额外把它们单独列出来，方便调用方渲染"跳过了这些步骤"的摘要，
    默认折叠、可展开查看每一步的原始 `narrative`/`summary`。"""
    stop_reason: str = "max_steps"
    """为什么停在这一步：
    - `"major_decision"`：`final_state.major_decision` 为真（命中
      `stop_on_major_decision`）。
    - `"options"`：`final_state.options` 非空（命中 `stop_on_options`）。
    - `"max_steps"`：跑满 `max_steps` 步都没有命中上面两个信号。
    - `"ended"`/`"paused"`：快进过程中模拟实例被标记为已结束/暂停
      （`advance()` 在下一次循环时会抛出对应异常），已经完成的步骤
      仍然正常返回，不会丢失。
    """
    summary_text: str = ""
    """"跳过了 N 步"的自然语言摘要，复用 `retrospective.py` 一贯的
    "读历史、几句话概括"手法——直接拼接每个被跳过步骤已有的
    `summary` 字段，不额外发起任何新的 LLM 调用（`advance()` 本身
    每一步都已经生成过 `summary`，这里只是复用，不是重新生成）。"""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sim_id": self.sim_id,
            "branch": self.branch,
            "start_step": self.start_step,
            "final_step": self.final_state.step,
            "steps_run": self.steps_run,
            "skipped_steps": [s.step for s in self.skipped_states],
            "stop_reason": self.stop_reason,
            "summary_text": self.summary_text,
        }


def _summarize_skipped_steps(skipped_states: List[SimState]) -> str:
    """把被跳过的若干步 `summary` 拼成一段"跳过了 N 步"的摘要文本。

    刻意不发起新的 LLM 调用——`advance()` 每一步已经生成过
    `summary`，这里只是复用既有文本做拼接，延续 `retrospective.py`
    "只读取已经存在的信息，不在生成摘要时临时发起新的推演"的一贯
    做法（见 `retrospective._collect_materials()` 的 docstring）。
    """
    if not skipped_states:
        return ""
    lines = [f"快进跳过了 {len(skipped_states)} 步，概览："]
    for s in skipped_states:
        label = s.time_label or f"第 {s.step} 步"
        summary = (s.summary or "").strip() or "（无摘要）"
        lines.append(f"- {label}：{summary}")
    return "\n".join(lines)


def fast_forward(
    cfg,
    workspace_root: Path,
    data_dir: Path,
    sim_id: str,
    *,
    max_steps: int = 10,
    stop_on_major_decision: bool = True,
    stop_on_options: bool = True,
    decision_context: str = "",
    chosen_by: str = "user",
    allow_custom_options: bool = False,
) -> FastForwardResult:
    """"快进"：连续调用已有的单步 `advance()`，直到命中"值得停下来
    给用户看"的信号，或者达到 `max_steps` 上限。

    这不是一套新的推进机制——每一步内部仍然是完整的一次
    `advance()` 调用（默认走向，不指定 `choice_option_id`，与详情页
    "按默认走向推进"按钮完全相同的调用方式），只是外层多包了一层
    "遇到信号就停、没遇到就继续调用下一步"的循环，`advance()` 内部
    的 prompt/字段逻辑完全不改动。

    Args:
        max_steps: 最多快进多少步，达到这个数字仍未命中停止信号时
            也会停止（避免无限循环把所有 token 预算耗尽在一次快进
            里）。必须 >= 1。
        stop_on_major_decision: 为 `True`（默认）时，某一步的
            `SimState.major_decision` 为真会立即停止快进，停在这
            一步（这一步不计入 `skipped_states`，作为 `final_state`
            完整展示）。
        stop_on_options: 为 `True`（默认）时，某一步的
            `SimState.options` 非空会立即停止快进，停在这一步。
            自动挡场景（`autopilot.run_batch_autopilot()`）会传
            `False`——自动挡的整个意义就是"不需要为每一批候选选项
            停下来等真人"，是否要暂停完全交给 `stop_on_major_decision`
            + `review_mode` 判断，与手动挡"快进"默认两个信号都要
            拦下来给用户看的语义刻意不同。
        decision_context/chosen_by/allow_custom_options: 透传给内部
            每一次 `advance()` 调用，与 `advance()` 自身同名参数
            含义完全一致。手动挡"快进"不需要传（每一步都是"按默认
            走向推进"，与详情页按钮行为一致）；自动挡场景需要传入
            `autopilot._build_decision_context(manifest)` 等价的
            画像文本，否则快进期间遇到候选选项时会退化成"不做选择、
            由 skill 自行决定默认走向"，不会应用用户设置的风险偏好/
            原则/情境化策略——这是本函数保留这三个透传参数、而不是
            只在内部写死"默认走向"调用方式的原因。

    Returns:
        `FastForwardResult`——即使一步都没能命中停止信号、也没有
        任何异常，跑满 `max_steps` 后依然会返回（`stop_reason`
        为 `"max_steps"`），不会出现"快进了但什么都没返回"的情况。

    Raises:
        ValueError: `max_steps` 小于 1。
        SimEngineError: 第一次调用 `advance()` 之前，模拟实例已经
            不存在或没有可读的当前状态（复用 `advance()` 自身的
            校验，不重复定义新的错误类型）。
    """
    if max_steps < 1:
        raise ValueError(f"max_steps 必须 >= 1，收到：{max_steps}")

    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    branch = manifest.branch
    start_state = store.load_current_state(branch)
    if start_state is None:
        raise SimEngineError(f"模拟实例缺少当前状态，数据可能已损坏：{sim_id}")
    start_step = start_state.step

    skipped_states: List[SimState] = []
    final_state: SimState = start_state
    stop_reason = "max_steps"
    steps_run = 0

    for _ in range(max_steps):
        try:
            next_state = advance(
                cfg, workspace_root, data_dir, sim_id,
                choice_option_id=None,
                decision_context=decision_context,
                chosen_by=chosen_by,
                allow_custom_options=allow_custom_options,
            )
        except SimAlreadyEndedError:
            stop_reason = "ended"
            break
        except SimPausedError:
            stop_reason = "paused"
            break

        steps_run += 1
        final_state = next_state

        hit_major = stop_on_major_decision and bool(next_state.major_decision)
        hit_options = stop_on_options and bool(next_state.options)
        if hit_major or hit_options:
            stop_reason = "major_decision" if hit_major else "options"
            break

        skipped_states.append(next_state)

    summary_text = _summarize_skipped_steps(skipped_states)

    return FastForwardResult(
        sim_id=sim_id,
        branch=branch,
        start_step=start_step,
        final_state=final_state,
        steps_run=steps_run,
        skipped_states=skipped_states,
        stop_reason=stop_reason,
        summary_text=summary_text,
    )
