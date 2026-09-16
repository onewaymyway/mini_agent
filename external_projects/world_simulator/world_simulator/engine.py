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

from world_simulator.spec_generator import ScenarioGenerationError, generate_scenario
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


def materialize_simulation(
    data_dir: Path,
    *,
    template: str,
    intent: str,
    title: str,
    summary: str,
    vars: Dict[str, Any],
    options,
) -> SimManifest:
    """把一份（已生成、可能已被用户编辑过的）提案草稿落盘为一个新实例的
    step 0 初始状态，返回 manifest。

    从 `create_simulation()` 拆出来，供 `app.py` 创建向导使用：向导需要
    先展示 `spec_generator.generate_scenario()` 的草稿、允许用户编辑
    字段，再落盘——如果落盘逻辑仍然嵌在 `create_simulation()` 内部
    （一次调用同时"生成+落盘"），向导就无法在两者之间插入编辑步骤。
    `create_simulation()` 本身改为"生成 + 直接落盘"两步的组合，签名
    对 CLI/entrypoint 调用方保持不变。
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
    )
    state0 = SimState(step=0, summary=summary, narrative="", vars=dict(vars or {}), options=options_list)
    store.save_manifest(manifest)
    store.append_state(state0, branch="main")
    return manifest


def create_simulation(
    cfg,
    workspace_root: Path,
    data_dir: Path,
    *,
    template: str,
    intent: str,
) -> SimManifest:
    """意图 → 提案草稿 → 落盘为 step 0 的初始状态 → 返回 manifest。

    对应方案"一句话意图→生成提案→确认→推进→查看历史"链路里的前两步；
    这是 CLI/entrypoint 场景使用的"一步到位"版本（草稿生成即视为
    确认）。独立看板的创建向导需要在"生成"和"落盘"之间插入用户编辑/
    确认环节，走 `spec_generator.generate_scenario()` +
    `materialize_simulation()` 两步，见 `app.py`。
    """
    draft = generate_scenario(cfg, workspace_root, template=template, intent=intent)
    return materialize_simulation(
        data_dir,
        template=template,
        intent=intent,
        title=draft.title or intent,
        summary=draft.summary,
        vars=draft.vars,
        options=draft.options,
    )


def advance(
    cfg,
    workspace_root: Path,
    data_dir: Path,
    sim_id: str,
    *,
    choice_option_id: Optional[str] = None,
    decision_context: str = "",
    chosen_by: str = "user",
) -> SimState:
    """推进模拟实例一步。

    Args:
        choice_option_id: 用户/代理选中的候选选项 id（当前状态
            `options` 里的一个）；为 None 时表示"不指定，让引擎给出
            默认走向"（对应当前状态 `options` 为空数组的情况，也允许
            非空时仍不指定——由 skill 判断怎么给默认值）。
        decision_context: 阶段四自动挡使用，非空表示这是一次代理代选：
            `choice_option_id` 应该为 None，engine 会把当前候选列表喂给
            skill，由 skill 自己选一个并在结果里回填 `chosen_option_id`/
            `chosen_reason`（engine 校验后落盘，不直接信任 LLM）。
        chosen_by: 当 `choice_option_id` 非 None（手动挡）时，记入当前
            状态 `chosen_by` 字段的值，通常是 `"user"`；自动挡代选场景
            这个值会被引擎内部推导出的 `"autopilot"` 覆盖，调用方不需要
            自己判断。
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
    if choice_option_id is not None:
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
        "chosen_option_json": json.dumps(
            chosen_option.to_dict() if chosen_option else {}, ensure_ascii=False
        ),
        "current_options_json": json.dumps(
            [o.to_dict() for o in current.options], ensure_ascii=False
        ),
        "decision_context": decision_context,
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
        if auto_choice_id:
            chosen_option = next((o for o in current.options if o.id == auto_choice_id), None)
            if chosen_option is None:
                raise SimEngineError(
                    f"自动挡代选返回的 chosen_option_id={auto_choice_id!r} 不在候选列表中"
                    f"（当前候选：{[o.id for o in current.options]}），已中止本次推进"
                )
            effective_chosen_by = "autopilot"
            effective_chosen_reason = data.get("chosen_reason") or None

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

    next_state = SimState(
        step=current.step + 1,
        summary=str(data.get("next_summary", "")),
        narrative=str(data.get("narrative", "")),
        vars=dict(data.get("next_vars") or {}),
        options=[ChoiceOption.from_dict(o) for o in (data.get("options") or [])],
        major_decision=bool(data.get("major_decision", False)),
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
    """更新实例的推进模式（手动挡/自动挡）与自动挡配置。

    对应方案 4.1 节的 `manifest.json.pilot_mode`/`autopilot` 字段，供
    `app.py` 的自动挡配置表单调用；不校验 `autopilot` 字段内部结构
    （`principles`/`risk_preference`/`review_mode`），非法值会在真正
    调用 `advance_step` workflow 时体现为"skill 读不懂这段画像"而不是
    这里报错——阶段四范围内暂不引入额外的 schema 校验。
    """
    if pilot_mode not in ("manual", "autopilot"):
        raise SimEngineError(f"非法推进模式：{pilot_mode}")
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    manifest.pilot_mode = pilot_mode
    manifest.autopilot = dict(autopilot or {})
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
