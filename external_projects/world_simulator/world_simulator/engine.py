"""world_simulator/engine.py — 核心推演循环

设计依据：`world_simulator_external_project_plan.md` 第 6 节。

`create_simulation()` = `spec_generator.generate_scenario()` + 落盘为
step 0 的初始状态；`advance()` = 一次 `advance_step` workflow 调用，
输出 next_state（结构化）+ 叙事文本 + 候选分支选项。

暂停/恢复只是"要不要继续调用引擎推进"的控制位（`manifest.status`），
状态本身每步都已经落盘，天然可恢复——本模块不需要为"恢复"实现任何
额外逻辑，只需要在 `advance()` 前检查 `status`。
"""

from __future__ import annotations

import json
import secrets
import string
from pathlib import Path
from typing import Any, Dict, Optional

from world_simulator.spec_generator import ScenarioGenerationError, generate_scenario, resolve_hints
from world_simulator.state_model import ChoiceOption, SimManifest, SimState
from world_simulator.store import SimNotFoundError, SimStore, list_sim_ids, now_iso

_ID_ALPHABET = string.ascii_lowercase + string.digits


class SimEngineError(RuntimeError):
    pass


class SimAlreadyEndedError(SimEngineError):
    pass


class SimPausedError(SimEngineError):
    pass


def _new_sim_id(template: str) -> str:
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
    return f"{template}_{suffix}"


def _skill_name_for_template(template: str) -> str:
    return f"{template.replace('_', '-')}-template"


def _normalize_resource_fields(raw: Any) -> list:
    """把 `manifest.settings.resource_fields` 归一化成
    `[{"field": str, "min": float}, ...]` 的形式。

    支持两种输入写法（见 `state_model.SimManifest.settings` 的字段
    说明）：纯字段名字符串（下限默认 0）、或
    `{"field": ..., "min": ...}` 字典（`min` 缺省也是 0）。非法/无法
    解析的项直接跳过，不抛错——这是一个"锦上添花"的校验功能，配置写
    错了不应该让整个推进流程失败。
    """
    fields: list = []
    for item in raw or []:
        if isinstance(item, str):
            name = item.strip()
            if name:
                fields.append({"field": name, "min": 0})
        elif isinstance(item, dict):
            name = str(item.get("field") or "").strip()
            if not name:
                continue
            try:
                min_value = float(item.get("min", 0) or 0)
            except (TypeError, ValueError):
                min_value = 0
            fields.append({"field": name, "min": min_value})
    return fields


def _get_nested(data: Dict[str, Any], path: str) -> Any:
    """按 `.` 分隔的路径读取（最多支持一层嵌套，见 `resource_fields`
    格式说明），路径不存在时返回 `None`。"""
    parts = path.split(".", 1)
    if len(parts) == 1:
        return data.get(parts[0])
    outer = data.get(parts[0])
    if not isinstance(outer, dict):
        return None
    return outer.get(parts[1])


def _set_nested(data: Dict[str, Any], path: str, value: Any) -> None:
    """按 `.` 分隔的路径写入（最多支持一层嵌套），路径中间层不存在时
    静默放弃（说明这个字段这一步 LLM 根本没给出来，没有可以校正的
    数值——不强行造一个结构出来）。"""
    parts = path.split(".", 1)
    if len(parts) == 1:
        data[parts[0]] = value
        return
    outer = data.get(parts[0])
    if isinstance(outer, dict):
        outer[parts[1]] = value


def _apply_resource_guard(vars_dict: Dict[str, Any], resource_fields_raw: Any) -> list:
    """对 `vars_dict` 就地做资源类字段下限校验（阶段九，4.1 节）。

    低于下限的字段被原地夹到下限，返回本次发现并纠正的越界项列表
    （`SimState.resource_violations` 要落盘的内容）；未声明
    `resource_fields`、字段不存在、或字段值不是数字（比如 LLM 把资源
    字段错写成字符串）时都跳过，不报错——这一步只做"数值下限"这一种
    最简单的校验，其它情况留给未来按需扩展。
    """
    violations: list = []
    for spec in _normalize_resource_fields(resource_fields_raw):
        path = spec["field"]
        min_value = spec["min"]
        current = _get_nested(vars_dict, path)
        if not isinstance(current, (int, float)) or isinstance(current, bool):
            continue
        if current < min_value:
            _set_nested(vars_dict, path, min_value)
            violations.append(
                {"field": path, "llm_value": current, "clamped_value": min_value}
            )
    return violations


def _normalize_resource_relations(raw: Any) -> list:
    """把 `manifest.settings.resource_relations` 归一化成
    `[{"from": str, "to": str, "tolerance": float}, ...]` 的形式
    （阶段十六，4.8 节）。

    只识别 `type == "transfer"`（或没写 `type`，默认按 `transfer`
    处理）的项，`production` 等其它类型本版本不支持，直接跳过；
    非法/无法解析的项也直接跳过，不抛错——同 `_normalize_resource_
    fields()`，配置写错了不应该让整个推进流程失败。
    """
    relations: list = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        rel_type = str(item.get("type") or "transfer").strip() or "transfer"
        if rel_type != "transfer":
            continue
        from_field = str(item.get("from") or "").strip()
        to_field = str(item.get("to") or "").strip()
        if not from_field or not to_field:
            continue
        try:
            tolerance = float(item.get("tolerance", 0.1) or 0.1)
        except (TypeError, ValueError):
            tolerance = 0.1
        relations.append({"from": from_field, "to": to_field, "tolerance": tolerance})
    return relations


def _check_resource_relations(
    current_vars: Dict[str, Any], next_vars: Dict[str, Any], resource_relations_raw: Any
) -> list:
    """对声明的 `transfer` 关系做一次事后一致性检查（阶段十六，4.8 节）。

    对每条关系计算 `delta_from = next_vars[from] - current_vars[from]`、
    `delta_to = next_vars[to] - current_vars[to]`，如果两者之和的绝对值
    超出容差（按两者绝对值的较大者衡量），记为一条不一致。**不修改任何
    数值、不拒绝推进**——这里没有"应该是多少"的唯一正确答案，只做
    留痕。任一字段缺失/非数字/变化量都为 0（没有实际发生转移）时跳过，
    不产生误报。
    """
    violations: list = []
    for spec in _normalize_resource_relations(resource_relations_raw):
        from_path = spec["from"]
        to_path = spec["to"]
        tolerance = spec["tolerance"]
        cur_from = _get_nested(current_vars, from_path)
        cur_to = _get_nested(current_vars, to_path)
        next_from = _get_nested(next_vars, from_path)
        next_to = _get_nested(next_vars, to_path)
        values = (cur_from, cur_to, next_from, next_to)
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in values):
            continue
        delta_from = next_from - cur_from
        delta_to = next_to - cur_to
        if delta_from == 0 and delta_to == 0:
            continue
        allowed = tolerance * max(abs(delta_from), abs(delta_to))
        if abs(delta_from + delta_to) > allowed:
            violations.append(
                {
                    "from": from_path,
                    "to": to_path,
                    "delta_from": delta_from,
                    "delta_to": delta_to,
                }
            )
    return violations


def materialize_simulation(
    data_dir: Path,
    *,
    template: str,
    intent: str,
    title: str,
    summary: str,
    vars: Dict[str, Any],
    options,
    settings: Optional[Dict[str, Any]] = None,
    time_label: str = "",
    time_granularity: str = "",
    uncertain_fields: Optional[list] = None,
) -> SimManifest:
    """把一份（已生成、可能已被用户编辑过的）提案草稿落盘为一个新实例的
    step 0 初始状态，返回 manifest。

    从 `create_simulation()` 拆出来，供 `app.py` 创建向导使用：向导需要
    先展示 `spec_generator.generate_scenario()` 的草稿、允许用户编辑
    字段，再落盘——如果落盘逻辑仍然嵌在 `create_simulation()` 内部
    (一次调用同时"生成+落盘"），向导就无法在两者之间插入编辑步骤。
    `create_simulation()` 本身改为"生成 + 直接落盘"两步的组合，签名
    对 CLI/entrypoint 调用方保持不变。

    Args:
        settings: 用户在创建向导里设置的 `options_count`/
            `time_granularity_mode` 等（见 `SimManifest.settings`
            docstring），原样存进 manifest，之后每一步 `advance()`
            都会读它。
        time_label: 初始状态对应的"模拟内时间"人类可读描述（比如
            `起点`），留空时默认为 `起点`。
        time_granularity: 这次模拟的起始基准粒度（`auto`/`guided`
            模式下由 skill 给出，`fixed` 模式下留空即可——展示层/
            后续推进都会 fallback 到 `settings.time_granularity`）。
        uncertain_fields: 初始状态里 skill 主动标注的"本质是主观估计、
            置信度不高"的字段（阶段十一，见
            `state_model.SimState.uncertain_fields` 的格式说明），
            留空表示没有需要标注的字段。
    """
    options_list = [
        o if isinstance(o, ChoiceOption) else ChoiceOption.from_dict(o) for o in (options or [])
    ]
    sim_id = _new_sim_id(template)
    store = SimStore.for_root(data_dir, sim_id)
    ts = now_iso()
    manifest = SimManifest(
        sim_id=sim_id,
        template=template,
        intent=intent,
        title=title,
        created_at=ts,
        updated_at=ts,
        status="active",
        pilot_mode="manual",
        autopilot={},
        current_step=0,
        branch="main",
        settings=dict(settings or {}),
    )
    state0 = SimState(
        step=0, summary=summary, narrative="", vars=dict(vars or {}), options=options_list,
        time_label=time_label or "起点",
        time_granularity=time_granularity,
        uncertain_fields=list(uncertain_fields or []),
    )
    store.save_manifest(manifest)
    store.append_state(state0, branch="main")
    store.save_pilot_config("main", manifest.pilot_mode, manifest.autopilot)
    return manifest


def create_simulation(
    cfg,
    workspace_root: Path,
    data_dir: Path,
    *,
    template: str,
    intent: str,
    settings: Optional[Dict[str, Any]] = None,
) -> SimManifest:
    """意图 → 提案草稿 → 落盘为 step 0 的初始状态 → 返回 manifest。

    对应方案"一句话意图→生成提案→确认→推进→查看历史"链路里的前两步；
    这是 CLI/entrypoint 场景使用的"一步到位"版本（草稿生成即视为
    确认）。独立看板的创建向导需要在"生成"和"落盘"之间插入用户编辑/
    确认环节，走 `spec_generator.generate_scenario()` +
    `materialize_simulation()` 两步，见 `app.py`。
    """
    draft = generate_scenario(cfg, workspace_root, template=template, intent=intent, settings=settings)
    return materialize_simulation(
        data_dir,
        template=template,
        intent=intent,
        title=draft.title or intent,
        summary=draft.summary,
        vars=draft.vars,
        options=draft.options,
        settings=settings,
        time_label=draft.time_label,
        time_granularity=draft.time_granularity,
        uncertain_fields=draft.uncertain_fields,
    )


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
                raise SimEngineError(
                    f"自动挡代选返回的 chosen_option_id={auto_choice_id!r} 不在候选列表中"
                    f"（当前候选：{[o.id for o in current.options]}），已中止本次推进"
                )
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
    # 因此这里重写整份历史里最后一条（append_state 每次整体重写 jsonl，
    # 直接在内存里改最后一条再整体落盘即可，不需要额外的"更新历史"方法）。
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
        uncertain_fields=list(data.get("uncertain_fields") or []),
        key_drivers=[str(x) for x in (data.get("key_drivers") or [])],
        causal_links=[
            dict(x) for x in (data.get("causal_links") or []) if isinstance(x, dict)
        ],
    )
    store.append_state(next_state, branch=branch)

    manifest.current_step = next_state.step
    store.save_manifest(manifest)

    return next_state


def set_status(data_dir: Path, sim_id: str, status: str) -> SimManifest:
    """设置实例状态：`active` | `paused` | `ended`。"""
    if status not in ("active", "paused", "ended"):
        raise SimEngineError(f"非法状态：{status}")
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    manifest.status = status
    store.save_manifest(manifest)
    return manifest


def set_pilot_config(
    data_dir: Path, sim_id: str, *, pilot_mode: str, autopilot: Optional[Dict[str, Any]] = None
) -> SimManifest:
    """更新**当前活跃分支**的推进模式（手动挡/自动挡）与自动挡配置。

    每条分支有自己独立的一份自动挡配置（`SimStore.pilot_config_path`），
    互不影响：在分支 A 上调整"风险偏好"不会波及分支 B。这里只更新
    `manifest.branch` 指向的这一条分支的配置文件；`manifest.pilot_mode`/
    `manifest.autopilot` 这两个顶层字段仍然同步写一份作为"当前活跃分支
    配置"的镜像——`autopilot.py`、看板列表等既有代码读的就是这两个
    顶层字段，镜像它们可以在不改动那些读取逻辑的前提下，让"配置已经
    是按分支存储"这件事对它们透明。分支切换/分叉时（`branch_manager`）
    也会同步刷新这份镜像，保证它始终等于"当前活跃分支自己的配置"。

    不校验 `autopilot` 字段内部结构（`principles`/`risk_preference`/
    `review_mode`），非法值会在真正调用 `advance_step` workflow 时体现
    为"skill 读不懂这段画像"而不是这里报错——阶段四范围内暂不引入
    额外的 schema 校验。
    """
    if pilot_mode not in ("manual", "autopilot"):
        raise SimEngineError(f"非法推进模式：{pilot_mode}")
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    autopilot_cfg = dict(autopilot or {})
    store.save_pilot_config(manifest.branch, pilot_mode, autopilot_cfg)
    manifest.pilot_mode = pilot_mode
    manifest.autopilot = autopilot_cfg
    store.save_manifest(manifest)
    return manifest


def update_settings(data_dir: Path, sim_id: str, **updates: Any) -> SimManifest:
    """更新实例的 `settings`（`options_count`/`time_granularity`），
    只合并传入的字段，不清空其它已有设置。

    对应"创建时选的候选方向数量/时间粒度，之后模拟过程中还想改"的场景
    （比如模拟推进到后期想从"1 年一步"切到"1 个月一步"看得更细）；
    改了之后从下一次 `advance()` 调用开始生效——`advance()` 每次都
    重新从磁盘读 `manifest.settings`，不需要额外的"生效"逻辑。
    """
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    manifest.settings = {**manifest.settings, **updates}
    store.save_manifest(manifest)
    return manifest


def delete_simulation(data_dir: Path, sim_id: str) -> None:
    """彻底删除一个模拟实例（`data/<sim_id>/` 整个目录，含所有分支）。

    对应方案第 5 节页面 5「存档管理」的"删除实例"操作。这是一个不可逆
    操作——不做软删除/回收站，理由：模拟实例本身就是"存档"语义（分叉/
    回滚已经覆盖了"不想要这条时间线了"的场景，见 `branch_manager.py`），
    真正点了"删除"通常是想彻底清掉一个不再需要的实例，加一层回收站会
    让"删除"这个操作本身语义变得含糊；调用方（`app.py`）在 UI 层要求
    二次确认来弥补"不可逆"的风险。
    """
    store = SimStore.for_root(data_dir, sim_id)
    if not store.exists():
        raise SimNotFoundError(f"模拟实例不存在：{sim_id}")
    import shutil

    shutil.rmtree(store.sim_dir)


def get_simulation(data_dir: Path, sim_id: str) -> tuple[SimManifest, SimState, list]:
    """返回 (manifest, current_state, history)，供 CLI/看板展示。"""
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    current = store.load_current_state(manifest.branch)
    history = store.load_history(manifest.branch)
    if current is None:
        raise SimEngineError(f"模拟实例缺少当前状态：{sim_id}")
    return manifest, current, history


def list_simulations(data_dir: Path) -> list:
    """返回所有实例的 manifest 列表（按创建时间顺序，目录名字典序）。"""
    manifests = []
    for sim_id in list_sim_ids(Path(data_dir)):
        store = SimStore.for_root(data_dir, sim_id)
        try:
            manifests.append(store.load_manifest())
        except SimNotFoundError:
            continue
    return manifests
