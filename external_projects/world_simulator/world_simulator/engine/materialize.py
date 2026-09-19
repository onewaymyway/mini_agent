"""world_simulator/engine/materialize.py — 模拟实例创建 + step 0
落盘。

从原单体 `engine.py` 拆分而来（阶段三十，4.18 节剩余部分），纯粹的
内部重组，行为不变。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from world_simulator import causal_tree
from world_simulator.engine.ids import _new_sim_id
from world_simulator.spec_generator import generate_scenario
from world_simulator.state_model import ChoiceOption, SimManifest, SimState
from world_simulator.store import SimStore, now_iso


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
    field_provenance: Optional[Dict[str, str]] = None,
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
        field_provenance: 初始 `vars` 每个顶层字段的来源标注（阶段
            三十二，4.2 节，见 `state_model.SimState.field_provenance`
            的格式说明），留空表示 skill 没有做这个标注。
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
        field_provenance={str(k): str(v) for k, v in (field_provenance or {}).items()},
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
        field_provenance=draft.field_provenance,
    )
