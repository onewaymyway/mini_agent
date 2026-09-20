"""world_simulator/engine/advance_independent.py — 多尺度因果线真正
独立推进（第三批，`next_doc/world_simulator_event_driven_engine_and_
full_architecture_plan.md` 2.3 节）。

**这是一条与 `advance()` 长期共存的独立路径**，不是 `advance()` 内部
的一个分支：`settings.independent_line_advance`（默认 `False`）只是
一个"这个实例打算用哪条路径推进"的标记，由调用方（`app.py`）在推进
按钮那一步据此决定调用 `engine.advance()` 还是本模块的
`advance_lines()`，`advance()` 本身完全不读取这个开关、不受任何影响
——这是方案原文明确要求的风险控制形态："新增开关 + 旧路径完全保留"。

**核心机制**：全局 `step` 仍然是唯一的时钟心跳（`manifest.
current_step`/`next_state.step` 照常 +1），只是这一步不再对所有
因果线发起同一次全局 LLM 调用，而是只对本步到点的线（`local_step %
advance_every_n_steps == 0`，用线自己的 `local_step` 而不是全局
`step` 取模——这样"错峰调用"才有意义，不同起点的线不会永远同步）
各自发起一次**只包含这条线自己 `owned_vars` + 因果链片段**的独立
调用（`workflows/line_evolve.yaml`）。没有到点的线本步不发起任何
调用，对应字段在 `vars` 里原样保留。

**数据一致性边界（刻意的范围克制，方案原文 2.3 节"不做的部分"）**：
- 不做真正的并行/异步调用——本步内到点的多条线仍然按声明顺序
  依次同步执行，不是并发。
- 跨线读取用的是"上一次全局同步点"的快照（`current.vars`，本次
  调用开始前的值），不是同一批次内其它线刚算出来的中间结果——避免
  "先算的线的结果泄漏给后算的线"这种取决于遍历顺序的不确定性。
- 每条线的 `owned_vars` 互不重叠是硬性前提（`_validate_owned_vars_
  no_overlap()` 在推进前校验，重叠直接抛 `OwnedVarsOverlapError`
  拒绝这次推进），不允许"两条线同时写同一个字段"这种情况发生。
- 不产出候选决策分支——独立推进的线目前只负责"世界怎么演化"，
  `options`/决策点判断仍然是主 `advance()` 路径的职责；方案原文
  建议先在 `life_sim` 模板小范围人工验证（3~5 次真实多因果线模拟）
  确认"错峰调用"不会让体验割裂，再考虑要不要推广，这是一条建议给
  真实使用时的节奏，本模块的实现本身不限制具体使用哪个模板。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from world_simulator.engine.errors import (
    OwnedVarsOverlapError,
    SimAlreadyEndedError,
    SimEngineError,
    SimPausedError,
)
from world_simulator.engine.ids import _skill_name_for_template
from world_simulator.state_model import SimState
from world_simulator.store import SimStore


def _validate_owned_vars_no_overlap(causal_lines: List[Dict[str, Any]]) -> None:
    """校验所有声明了 `owned_vars` 的因果线之间字段互不重叠——独立
    推进要求每个字段的归属在推进*之前*就已经清楚，不接受"两条线都
    声称拥有同一个字段"这种模糊状态（后续很难无痛修改，见方案原文
    对这一点的专门强调）。不修改传入的列表，只读校验。
    """
    owner_of: Dict[str, str] = {}
    for line in causal_lines:
        if not isinstance(line, dict):
            continue
        line_id = str(line.get("id") or "").strip()
        if not line_id:
            continue
        owned = line.get("owned_vars")
        if not isinstance(owned, list):
            continue
        for raw_field in owned:
            field_name = str(raw_field).strip()
            if not field_name:
                continue
            existing_owner = owner_of.get(field_name)
            if existing_owner is not None and existing_owner != line_id:
                raise OwnedVarsOverlapError(
                    f"owned_vars 字段 {field_name!r} 同时被因果线 "
                    f"{existing_owner!r} 和 {line_id!r} 声明为独占字段，"
                    f"owned_vars 不允许重叠——请先在\u201c模拟设置\u201d里"
                    f"修正字段归属，再重新尝试独立推进"
                )
            owner_of[field_name] = line_id


def _is_line_due(line: Dict[str, Any]) -> bool:
    """一条线本步是不是"该发起独立调用"的到点线：用这条线自己的
    `local_step`（不是全局 `step`）对 `advance_every_n_steps` 取模，
    默认节奏是每次都到点（`advance_every_n_steps` 未声明或非法时
    按 1 处理，同 `_lines_due_this_step_hint()` 的既有取舍）。
    """
    try:
        n = int(line.get("advance_every_n_steps") or 1)
    except (TypeError, ValueError):
        n = 1
    n = max(1, n)
    try:
        local_step = int(line.get("local_step") or 0)
    except (TypeError, ValueError):
        local_step = 0
    return local_step % n == 0


def advance_lines(cfg, workspace_root: Path, data_dir: Path, sim_id: str) -> SimState:
    """独立推进一步：全局 `step` +1，只对本步到点、且声明了
    `owned_vars` 的因果线分别发起一次独立的 `line_evolve` 调用。

    只有同时满足"声明了 `owned_vars`"和"本步到点
    （`_is_line_due()`）"两个条件的线才会真正发起调用；声明了
    `causal_lines` 但没给 `owned_vars` 的线（尚未参与独立推进）本步
    永远不会被单独调用，字段原样保留——这是"要求显式声明，不允许
    引擎自动推断归属"这一前提的直接体现。一条线没有到点都不发起任何
    调用时，这一步仍然正常落盘（`summary` 会如实说明"本步没有任何
    因果线到点"），全局 `step` 依然 +1，不是空推进就报错。
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

    causal_lines = [
        dict(x) for x in (manifest.settings.get("causal_lines") or []) if isinstance(x, dict)
    ]
    _validate_owned_vars_no_overlap(causal_lines)

    due_lines = [
        line
        for line in causal_lines
        if isinstance(line.get("owned_vars"), list)
        and line.get("owned_vars")
        and _is_line_due(line)
    ]

    # 快照：跨线只读"上一次全局同步点"的状态（本次调用开始前的
    # `current.vars`），不读同一批次内其它线刚算出来的中间结果。
    snapshot_vars = dict(current.vars)
    next_vars = dict(current.vars)

    line_updates: Dict[str, Dict[str, Any]] = {}
    narrative_parts: List[str] = []
    advanced_line_ids: set = set()

    if due_lines:
        from mini_agent.workflow.runner import WorkflowRunner
        from mini_agent.workflow.store import WorkflowStore

        wf_store = WorkflowStore(Path(workspace_root))
        skill_name = _skill_name_for_template(manifest.template)
        runner = WorkflowRunner(cfg)

        for line in due_lines:
            line_id = str(line["id"]).strip()
            label = str(line.get("label") or line_id)
            owned = [str(f).strip() for f in line.get("owned_vars") or [] if str(f).strip()]
            owned_vars_snapshot = {k: snapshot_vars.get(k) for k in owned}

            wf = wf_store.load("line_evolve")
            if wf is None:
                raise SimEngineError("找不到 workflow 定义 line_evolve")
            found = False
            for step in wf.steps:
                if step.id == "line_evolve":
                    step.skill_name = skill_name
                    found = True
                    break
            if not found:
                raise SimEngineError("line_evolve workflow 中找不到 step id=line_evolve")

            try:
                local_step = int(line.get("local_step") or 0)
            except (TypeError, ValueError):
                local_step = 0

            inputs = {
                "title": manifest.title,
                "global_step": str(current.step),
                "current_summary": current.summary,
                "line_id": line_id,
                "line_label": label,
                "line_local_step": str(local_step),
                "line_owned_vars_json": json.dumps(owned_vars_snapshot, ensure_ascii=False),
            }

            result = runner.run(wf, inputs)
            if result.status != "done":
                failed = [
                    f"{sr.step_id}({sr.status.value}): {sr.error}"
                    for sr in result.step_results
                    if sr.status.value != "done"
                ]
                raise SimEngineError(
                    f"line_evolve workflow 执行未成功（line_id={line_id!r}）："
                    f"status={result.status}；" + "；".join(failed)
                )

            step_result = next(
                (sr for sr in result.step_results if sr.step_id == "line_evolve"), None
            )
            if step_result is None or not step_result.result_file:
                raise SimEngineError(
                    f"line_evolve 的 line_id={line_id!r} 未产出 result_file，"
                    f"无法解析这条线的推进结果"
                )

            data: Dict[str, Any] = json.loads(
                Path(step_result.result_file).read_text(encoding="utf-8")
            )

            # 只接受这条线自己声明的 owned_vars 范围内的字段，防止 LLM
            # 手滑（或跨线"帮忙"）写出不属于这条线的字段——独立推进的
            # 一致性边界必须由 engine 自己兜底，不能只靠 prompt 约束。
            line_next_vars = data.get("next_vars") if isinstance(data.get("next_vars"), dict) else {}
            for key in owned:
                if key in line_next_vars:
                    next_vars[key] = line_next_vars[key]

            summary = str(data.get("summary", "") or "")
            narrative = str(data.get("narrative", "") or "")
            if narrative:
                narrative_parts.append(f"[{label}] {narrative}")

            entry: Dict[str, Any] = {"summary": summary, "advanced": True}
            trend = data.get("trend")
            if trend in ("accelerating", "steady", "decelerating", "reversing"):
                entry["trend"] = trend
            line_updates[line_id] = entry
            advanced_line_ids.add(line_id)

    # 到点被真正推进的线 local_step +1；没到点/没声明 owned_vars 的线
    # 原样保留（不消费任何调用，也不该无端前进）。
    new_causal_lines: List[Dict[str, Any]] = []
    for line in causal_lines:
        line = dict(line)
        line_id = str(line.get("id") or "").strip()
        if line_id in advanced_line_ids:
            try:
                local_step = int(line.get("local_step") or 0)
            except (TypeError, ValueError):
                local_step = 0
            line["local_step"] = local_step + 1
        new_causal_lines.append(line)
    manifest.settings = {**manifest.settings, "causal_lines": new_causal_lines}

    if line_updates:
        summary = "；".join(f"{lid}：{lu['summary']}" for lid, lu in line_updates.items() if lu.get("summary"))
        if not summary:
            summary = "、".join(line_updates.keys()) + " 本步有推进"
    else:
        summary = "本步没有任何因果线到点，维持现状"

    next_state = SimState(
        step=current.step + 1,
        summary=summary,
        narrative="\n".join(narrative_parts),
        vars=next_vars,
        options=[],
        time_label=current.time_label or "",
        time_granularity=current.time_granularity,
        line_updates=line_updates,
    )

    store.append_state(next_state, branch=branch)
    manifest.current_step = next_state.step
    store.save_manifest(manifest)
    return next_state
