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
from typing import Any, Dict, List, Optional

from world_simulator import causal_tree, knowledge_base
from world_simulator.spec_generator import ScenarioGenerationError, generate_scenario, resolve_hints
from world_simulator.state_model import ChoiceOption, SimManifest, SimState
from world_simulator.store import SimNotFoundError, SimStore, list_sim_ids, now_iso

_ID_ALPHABET = string.ascii_lowercase + string.digits


class SimEngineError(RuntimeError):
    pass


def _safe_suggest_knowledge(data_dir: Path, query_text: str, *, template: str) -> str:
    """`knowledge_base.suggest_for_prompt()` 的安全包装（阶段二十，4.12
    节 3.）：检索是"锦上添花"的旁路信息，任何异常（比如知识库文件被
    手工改坏）都不应该让 `generate_scenario`/`advance()` 的核心链路
    失败，退化为"没有可参考的知识"即可，不向上抛出。
    """
    try:
        return knowledge_base.suggest_for_prompt(data_dir, query_text, template=template)
    except Exception:
        return "（暂无相关的已知因果知识）"


def _safe_record_causal_links(
    data_dir: Path, *, sim_id: str, template: str, causal_links: list
) -> None:
    """`knowledge_base.record_causal_links()` 的安全包装（阶段二十，
    4.12 节 2.）：写入知识库是这一步推进落盘*之后*的旁路操作，失败
    不应该让本次推进本身失败（`advance()` 的返回值/落盘结果已经产生），
    这里吞掉异常，只保留"尽力而为"的语义。
    """
    try:
        knowledge_base.record_causal_links(
            data_dir, sim_id=sim_id, template=template, causal_links=causal_links
        )
    except Exception:
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


def _normalize_background_entities(raw: Any) -> list:
    """把 `manifest.settings.background_entities` 归一化成字符串
    列表（Hierarchical Agent，4.10 节设计草案第一步）。非法/空项直接
    跳过，不抛错。"""
    names: list = []
    for item in raw or []:
        name = str(item or "").strip()
        if name:
            names.append(name)
    return names


def _apply_background_entity_extrapolation(
    current_vars: Dict[str, Any],
    next_vars: Dict[str, Any],
    previous_vars: Dict[str, Any],
    background_entities: list,
) -> list:
    """对声明为"背景角色"的主体，用简单线性趋势外推**强制覆盖** LLM
    这一步给出的值（Hierarchical Agent，4.10 节设计草案第一步：先做
    "分层"本身，不做"调度框架"）。

    直接原地修改 `next_vars`（调用方传入的是本次推进即将落盘的那份
    `next_vars`，修改它就是修改最终落盘的结果），返回实际生效的主体
    名字列表，供落盘进 `SimState.background_entities_applied` 留痕。

    外推规则：对每个声明为背景角色的主体，取它"上一步"（`previous_
    vars`，可能不存在——第一次推进时没有"上一步"）到"这一步"
    （`current_vars`，即这次推进*开始前*的状态）之间每个数值字段的
    变化量，按相同变化量再推一步得到覆盖值；没有上一步可参考、或字段
    不是数值类型时，原样保留这一步（`current_vars`）的值（外推量为
    0，不是随便编一个数）。**只处理 `vars.entities` 结构**（要求
    `multi_entity_mode` 同时启用），`current_vars`/`next_vars` 没有
    `entities` 字典时是空操作，不报错——`hierarchical_agent_mode`
    误开在不支持的模板上不应该导致推进失败。
    """
    if not background_entities:
        return []
    entities_current = current_vars.get("entities") if isinstance(current_vars, dict) else None
    entities_next = next_vars.get("entities") if isinstance(next_vars, dict) else None
    if not isinstance(entities_current, dict) or not isinstance(entities_next, dict):
        return []
    entities_previous = previous_vars.get("entities") if isinstance(previous_vars, dict) else None
    applied: list = []
    for name in background_entities:
        cur_entity = entities_current.get(name)
        if not isinstance(cur_entity, dict):
            continue
        prev_entity = entities_previous.get(name) if isinstance(entities_previous, dict) else None
        extrapolated: Dict[str, Any] = {}
        for key, cur_value in cur_entity.items():
            is_numeric = isinstance(cur_value, (int, float)) and not isinstance(cur_value, bool)
            if not is_numeric:
                extrapolated[key] = cur_value
                continue
            prev_value = prev_entity.get(key) if isinstance(prev_entity, dict) else None
            prev_is_numeric = isinstance(prev_value, (int, float)) and not isinstance(prev_value, bool)
            delta = (cur_value - prev_value) if prev_is_numeric else 0
            extrapolated[key] = cur_value + delta
        entities_next[name] = extrapolated
        applied.append(name)
    next_vars["entities"] = entities_next
    return applied


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
    # 阶段二十六（`next_doc/world_simulator_causal_line_future_tree_
    # plan.md`）：创建模拟就必须有核心因果线、且每条线自带一棵初始
    # 未来因果树——不管调用方（独立看板向导 / CLI 一步到位创建）有没有
    # 手填、skill 有没有认真输出，这里统一兜底，保证"没推进也能看到
    # 因果线和它的未来分支"。就地改写刚构造好的 manifest.settings，
    # 不影响调用方传入的原始字典。
    manifest.settings = {
        **manifest.settings,
        "causal_lines": causal_tree.ensure_future_trees(
            manifest.settings.get("causal_lines"), as_of_step=0
        ),
    }
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
    draft = generate_scenario(
        cfg, workspace_root, template=template, intent=intent, settings=settings, data_dir=data_dir,
    )
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


_STRUCTURAL_CHANGE_KINDS = ("new_entity", "new_mechanism", "regime_shift")


def _normalize_structural_change(raw: Any) -> Optional[Dict[str, Any]]:
    """校验/规整 skill 给出的 `structural_change` 原始输出（阶段二十三，
    4.14 节）。

    只做最基础的形状校验——`kind` 必须是约定的三选一，`description`
    必须非空，否则视为"没给"（返回 `None`），不落一条内容不完整的
    提示进历史，避免展示层遇到空标题/未知 kind 的脏数据。不校验
    `proposed_fields` 的内部结构（自由 JSON，语义由 `kind` 决定，
    `engine.py` 不解析）。落盘时强制补上 `accepted: False`/
    `accepted_at: None`——是否采纳由用户在详情页手动确认（见
    `apply_structural_change()`），skill 的原始输出不应该带着
    `accepted` 字段影响这个判断。
    """
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip()
    description = str(raw.get("description") or "").strip()
    if kind not in _STRUCTURAL_CHANGE_KINDS or not description:
        return None
    proposed_fields = raw.get("proposed_fields")
    return {
        "detected": True,
        "kind": kind,
        "description": description,
        "proposed_fields": dict(proposed_fields) if isinstance(proposed_fields, dict) else {},
        "accepted": False,
        "accepted_at": None,
    }


def _format_confirmed_structural_changes(raw: Any) -> str:
    """把 `manifest.settings.confirmed_structural_changes`（阶段二十三，
    见 `apply_structural_change()`）拼成一段人类可读的提示文本，喂给
    `advance_step` 的 prompt，让 skill 知道"哪些新结构已经被用户正式
    确认，之后的推进应该把它们当成既有事实"（对应演进计划 4.14 节
    验收标准："下一步推进时 prompt 输入里能看到这个新实体已经作为
    已知实体存在"）。留空（未声明/尚无已确认项）返回空字符串，不
    影响任何已有行为。
    """
    items = raw if isinstance(raw, list) else []
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        description = str(item.get("description") or "")
        if not description:
            continue
        lines.append(f"- [{kind}] {description}")
    return "\n".join(lines)


def _auto_register_causal_lines(manifest: "SimManifest", next_state: "SimState") -> None:
    """把这一步 `line_updates`/`causal_links.line_id` 里出现的、还没在
    `manifest.settings.causal_lines` 里登记过的线 id，自动补一条最小
    的声明条目（`label` 先用 id 本身占位，用户可以之后在设置面板里
    改成更友好的中文名）。就地修改 `manifest.settings`，不落盘——调用方
    紧接着会统一 `store.save_manifest(manifest)`。

    这是"因果线是默认基础机制，不需要前置声明"这一要求的落地点：
    `spec_generator._resolve_causal_lines_hint()` 允许 skill 在没有任何
    声明的情况下自行起 id 输出 `line_updates`，这里负责把这些自发出现
    的 id 正式记下来，下一步推进时 prompt 里就能看到它们，也能出现在
    "因果线总览"视图里。
    """
    line_update_ids = [str(lid) for lid in (next_state.line_updates or {}).keys()]
    causal_link_line_ids = [
        str(link.get("line_id") or "")
        for link in (next_state.causal_links or [])
        if isinstance(link, dict) and str(link.get("line_id") or "").strip()
    ]
    manifest.settings = {
        **manifest.settings,
        "causal_lines": causal_tree.auto_register_lines(
            manifest.settings.get("causal_lines"),
            line_update_ids=line_update_ids,
            causal_link_line_ids=causal_link_line_ids,
            as_of_step=next_state.step,
        ),
    }


def _apply_tree_updates(
    manifest: "SimManifest", next_state: "SimState", data: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """把 `advance_step` 可选输出的 `tree_updates` 合并进对应因果线的
    `future_tree`（阶段二十六，`next_doc/
    world_simulator_causal_line_future_tree_plan.md`）。就地改写
    `manifest.settings`，返回落到 `SimState.tree_updates` 的审计摘要。

    纯粹的合并操作，不做任何判断——"哪个分支该确认/排除/新增"完全
    由 skill 输出决定，与 `_auto_register_causal_lines()`"发现即登记，
    不需要用户额外确认"是同一量级的风险（只是调整树的展示状态，不
    污染 `vars`/推进逻辑），因此不像 `structural_change` 那样需要
    "先展示，用户手动采纳"。
    """
    raw_updates = data.get("tree_updates")
    if not isinstance(raw_updates, list) or not raw_updates:
        return []
    updated_lines, audit = causal_tree.apply_tree_updates(
        manifest.settings.get("causal_lines"), raw_updates, next_state.step
    )
    if not audit:
        return []
    manifest.settings = {**manifest.settings, "causal_lines": updated_lines}
    return audit


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
    # 内容。这是纯粹的\"发现并登记\"，不像 `structural_change` 那样需要
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


def apply_structural_change(
    data_dir: Path, sim_id: str, *, step: int, branch: Optional[str] = None
) -> SimManifest:
    """采纳某一步 `advance_step` 报告的结构性变化（阶段二十三，4.14
    节，Model Regime Detection / Emergence）。

    这是 `SimState.structural_change`（"发现并展示"）与
    `manifest.settings`（真正影响后续推进的模拟配置）之间**唯一**的
    写入通道——`advance()` 本身只落盘、绝不自动修改 `settings`，
    只有用户在详情页对某一步的提示点击"采纳"、调用这个函数时，才会
    发生实际的结构固化。固化方式是把这条变化追加进
    `settings.confirmed_structural_changes`（列表，供
    `_format_confirmed_structural_changes()` 拼进后续 prompt 输入，见
    `advance()`），**不**尝试自动改写 `vars`/`multi_entity_mode` 的
    具体实体结构——engine 不猜"新实体具体应该长成什么 JSON 形状塞进
    `vars.entities`"，那仍然是下一次 `advance_step` 由 LLM 结合"已知
    这个新实体存在"这条提示自行决定如何在叙事/`vars` 里体现，延续
    "LLM 负责推理内容，engine 负责编排与留痕"的既有分工，也避免一次
    判断失误的 LLM 输出被直接、不可逆地写进 `vars` schema。

    Args:
        step: 要采纳的状态节点的 `step` 序号（该节点必须带有非空、
            尚未采纳的 `structural_change`）。
        branch: 目标分支，默认当前活跃分支（`manifest.branch`）。

    Raises:
        SimEngineError: 找不到该 step、该 step 没有
            `structural_change`、或已经被采纳过。
    """
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    target_branch = branch or manifest.branch
    history = store.load_history(target_branch)
    target = next((s for s in history if s.step == step), None)
    if target is None:
        raise SimEngineError(f"分支 {target_branch!r} 中找不到 step={step} 的状态节点")
    if not target.structural_change:
        raise SimEngineError(f"step={step} 没有待采纳的结构性变化")
    if target.structural_change.get("accepted"):
        raise SimEngineError(f"step={step} 的结构性变化已经采纳过，不能重复采纳")

    accepted_at = now_iso()
    target.structural_change = {
        **target.structural_change,
        "accepted": True,
        "accepted_at": accepted_at,
    }

    from mini_agent.utils.atomic_write import atomic_write_jsonl

    atomic_write_jsonl(
        store.state_history_path(target_branch),
        [s.to_dict() for s in history],
    )

    confirmed = list(manifest.settings.get("confirmed_structural_changes") or [])
    confirmed.append(
        {
            "step": step,
            "branch": target_branch,
            "kind": target.structural_change.get("kind", ""),
            "description": target.structural_change.get("description", ""),
            "proposed_fields": target.structural_change.get("proposed_fields", {}),
            "accepted_at": accepted_at,
        }
    )
    manifest.settings = {**manifest.settings, "confirmed_structural_changes": confirmed}
    store.save_manifest(manifest)
    return manifest


def rename_simulation(data_dir: Path, sim_id: str, new_title: str) -> SimManifest:
    """修改一个模拟实例的标题（`manifest.title`）。

    纯展示层的重命名，不涉及历史/分支数据，也不影响 `intent`（创建时的
    原始一句话意图，作为"这个实例最初想模拟什么"的留档，重命名不应该
    连带改掉）——只改 `title` 这一个字段，同 `update_settings()` 的
    "只合并/只改传入字段"取舍一致。
    """
    title = new_title.strip()
    if not title:
        raise SimEngineError("标题不能为空")
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    manifest.title = title
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
    """返回所有实例的 manifest 列表，按 `created_at` 倒序（最新的排在
    最前面）。

    `sim_id` 本身是 `{template}_{随机后缀}`（见 `_new_sim_id()`），不
    包含时间信息，目录名字典序并不等价于创建时间顺序，因此这里显式
    按 `created_at`（`now_iso()` 生成的 ISO 8601 字符串，可直接按字符
    串倒序比较）排序，而不是依赖 `list_sim_ids()` 的目录遍历顺序。
    `created_at` 缺失或解析异常的历史脏数据（理论上不应该出现，
    `materialize_simulation()` 落盘时总会写入）统一排到最后，不让
    异常数据影响其它正常实例的排序，也不让整个列表页因此报错。
    """
    manifests = []
    for sim_id in list_sim_ids(Path(data_dir)):
        store = SimStore.for_root(data_dir, sim_id)
        try:
            manifests.append(store.load_manifest())
        except SimNotFoundError:
            continue
    manifests.sort(key=lambda m: m.created_at or "", reverse=True)
    return manifests
