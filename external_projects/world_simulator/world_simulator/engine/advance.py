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
from pathlib import Path
from typing import Any, Dict, Optional

from world_simulator.engine.background_entities import (
    _apply_background_entity_extrapolation,
    _normalize_background_entities,
)
from world_simulator.engine.causal_lines import _apply_tree_updates, _auto_register_causal_lines
from world_simulator.engine.errors import SimAlreadyEndedError, SimEngineError, SimPausedError
from world_simulator.engine.ids import _skill_name_for_template
from world_simulator.engine.knowledge import _safe_record_causal_links, _safe_suggest_knowledge
from world_simulator.engine.resource_guard import _apply_resource_guard, _check_resource_relations
from world_simulator.engine.structural_change import (
    _format_confirmed_structural_changes,
    _normalize_structural_change,
)
from world_simulator.spec_generator import resolve_hints
from world_simulator.state_model import ChoiceOption, SimState
from world_simulator.store import SimStore


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
    wf = wf_store.load("advance_step")
    if wf is None:
        raise SimEngineError("找不到 workflow 定义 advance_step")
    skill_name = _skill_name_for_template(manifest.template)
    found = False
    for step in wf.steps:
        if step.id == "step":
            step.skill_name = skill_name
            found = True
            break
    if not found:
        raise SimEngineError("advance_step workflow 中找不到 step id=step")

    inputs = {
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
        **resolve_hints(manifest.settings),
    }

    runner = WorkflowRunner(cfg)
    result = runner.run(wf, inputs)

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
    relation_violations = _check_resource_relations(
        current.vars, next_vars, manifest.settings.get("resource_relations")
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

    next_state = SimState(
        step=current.step + 1,
        summary=str(data.get("next_summary", "")),
        narrative=str(data.get("narrative", "")),
        vars=next_vars,
        options=[ChoiceOption.from_dict(o) for o in (data.get("options") or [])],
        major_decision=bool(data.get("major_decision", False)),
        time_label=str(data.get("time_label", "") or ""),
        time_granularity=next_granularity,
        granularity_changed=granularity_changed,
        granularity_reason=granularity_reason,
        resource_violations=resource_violations,
        relation_violations=relation_violations,
        background_entities_applied=background_entities_applied,
        uncertain_fields=list(data.get("uncertain_fields") or []),
        key_drivers=[str(x) for x in (data.get("key_drivers") or [])],
        causal_links=[
            dict(x) for x in (data.get("causal_links") or []) if isinstance(x, dict)
        ],
        line_updates={
            str(k): dict(v) for k, v in (data.get("line_updates") or {}).items()
            if isinstance(v, dict)
        },
        structural_change=_normalize_structural_change(data.get("structural_change")),
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

    # 阶段二十六：合并这一步对因果线"未来树"的修正（印证/排除/新增
    # 分支），必须在 `_auto_register_causal_lines()` 之后调用——新登记
    # 的线需要先存在于 `manifest.settings.causal_lines` 里，
    # `tree_updates` 才有对象可以合并。合并结果同时记回 `next_state.
    # tree_updates`（审计摘要），需要在 `store.append_state()` 之前
    # 完成，否则历史里就存不到这份摘要。
    next_state.tree_updates = _apply_tree_updates(manifest, next_state, data)

    store.append_state(next_state, branch=branch)

    # 阶段二十（4.12 节 2.）：把这一步的结构化因果链沉淀进跨模拟知识库。
    # 纯旁路操作，落盘之后才做、失败不影响本次推进（见
    # `_safe_record_causal_links` docstring）。
    _safe_record_causal_links(
        data_dir,
        sim_id=sim_id,
        template=manifest.template,
        causal_links=next_state.causal_links,
    )

    manifest.current_step = next_state.step
    store.save_manifest(manifest)

    return next_state
