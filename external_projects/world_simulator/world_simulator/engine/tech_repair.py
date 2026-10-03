"""world_simulator/engine/tech_repair.py — 技术违规的一次性修复调用（第二十二轮 P5b）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §8 第 3 问
（用户确认：做修复调用，独立 opt-in）。开关 `settings.tech_repair_enabled`，默认 False；
关闭时 `advance()` 不会走到这里，行为与之前逐字节一致。

只在 `tech_model.repairable_violations()` 非空时由 `advance()` 调用**一次**（口径同
`ledger_correction.py`：修一次，修完剩下的按"只记录"处理，不循环）。流程：

1. 把本步开始前的权威技术状态、LLM 原来的 `tech_updates`、可修复的违规喂给 `tech_repair`
   workflow，让它重新给出 `tech_updates`；
2. `tech_model.constrain_repair()` 约束结果（不得新增 id、只能改白名单字段、阶段不得升高）；
3. 技术状态**回滚到本步开始前**，用约束后的提议重新 `apply_step()` 一遍；
4. 仅当可修复违规数量**严格减少**才采纳；否则恢复第一次裁决的结果；
5. 任何环节失败（workflow 缺失/执行失败/回复无法解析/重新裁决出错）都安全降级为第一次裁决的结果，
   不拖垮推进——与 `ledger_correction` 一致。

**不改写叙事/摘要**：修复的只是 `tech_updates` 这份提议。叙事与引擎状态的错位不会被消除。

返回的修复记录会落到 `SimState.tech_repair`：`{status, codes_before, codes_after,
violations_before, notes}`，`status` ∈ `accepted`/`rejected`/`failed`。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from world_simulator import tech_model
from world_simulator.agent_step_result import AgentStepOutputError, extract_agent_json_output


def _slim(violations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {"code": v.get("code"), "tech_id": v.get("tech_id"), "message": v.get("message")}
        for v in violations
    ]


def _restore(settings: Dict[str, Any], tech_state: Any) -> None:
    """回滚技术状态。`tech_state` 是 `tech_model.capture_storage()` 的返回值（也兼容旧式的
    裸 `tech_state` 值 / `None`）；存取细节（`tech_state` 还是元素 `lifecycle`）由
    `tech_model.restore_storage()` 处理。"""
    tech_model.restore_storage(settings, tech_state)


def _record(status: str, before: List[Dict[str, Any]], after: List[Dict[str, Any]], notes: List[str]) -> Dict[str, Any]:
    return {
        "status": status,
        "codes_before": [v.get("code") for v in before],
        "codes_after": [v.get("code") for v in after],
        "violations_before": _slim(before),
        "notes": list(notes),
    }


def _call_repair_workflow(
    cfg: Any,
    workspace_root: Path,
    *,
    settings_before: Dict[str, Any],
    proposals: Any,
    violations: List[Dict[str, Any]],
    narrative_hint: str,
) -> Any:
    """发起 `tech_repair` workflow，返回解析出的 `tech_updates`（列表）。失败抛异常，由调用方降级。"""
    from mini_agent.workflow.runner import WorkflowRunner
    from mini_agent.workflow.store import WorkflowStore

    wf = WorkflowStore(Path(workspace_root)).load("tech_repair")
    if wf is None:
        raise RuntimeError("找不到 workflow tech_repair")
    result = WorkflowRunner(cfg).run(
        wf,
        {
            "narrative_hint": narrative_hint or "",
            "tech_state_hint": tech_model.build_hint(settings_before),
            "proposals_json": json.dumps(proposals if isinstance(proposals, list) else [], ensure_ascii=False),
            "violations_json": json.dumps(_slim(violations), ensure_ascii=False),
        },
    )
    if result.status != "done":
        raise RuntimeError(f"tech_repair 未成功：status={result.status}")
    step_result = next((sr for sr in result.step_results if sr.step_id == "tech_repair"), None)
    if step_result is None:
        raise RuntimeError("tech_repair 没有产出结果")
    try:
        data = extract_agent_json_output(step_result.output, required_keys=["tech_updates"])
    except AgentStepOutputError as exc:
        raise RuntimeError(f"tech_repair 回复无法解析：{exc}") from exc
    return data.get("tech_updates")


def safe_repair_tech_step(
    cfg: Any,
    workspace_root: Path,
    settings: Dict[str, Any],
    *,
    pre_tech_state: Optional[Dict[str, Any]],
    proposals: Any,
    elapsed_days_raw: Any,
    step: int,
    audit: List[Dict[str, Any]],
    violations: List[Dict[str, Any]],
    narrative_hint: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """对已经裁决过一次的结果尝试一次修复。就地维护 `settings["tech_state"]`（采纳修复时为重新
    裁决后的状态，否则恢复第一次裁决后的状态）。

    Args:
        pre_tech_state: **第一次裁决之前**的技术状态存储（`tech_model.capture_storage()` 的返回值；
            兼容旧式的裸 `settings["tech_state"]` 深拷贝，`None` 表示当时没有）。
        audit/violations: 第一次裁决的结果。

    Returns:
        `(审计, 违规, 修复记录)`。没有可修复违规时原样返回且记录为 `None`；任何失败都返回第一次
        裁决的 `(审计, 违规)` 加一条 `status="failed"` 的记录。
    """
    before = tech_model.repairable_violations(violations)
    if not before:
        return audit, violations, None
    post_first = tech_model.capture_storage(settings)
    try:
        repaired_raw = _call_repair_workflow(
            cfg, workspace_root,
            settings_before=tech_model.settings_with_storage(settings, pre_tech_state),
            proposals=proposals, violations=before, narrative_hint=narrative_hint,
        )
        constrained, notes = tech_model.constrain_repair(proposals, repaired_raw)
        _restore(settings, pre_tech_state)
        new_audit, new_violations = tech_model.apply_step(
            settings, constrained, step=step, elapsed_days_raw=elapsed_days_raw,
        )
    except Exception as exc:  # noqa: BLE001 — 修复是可选旁路，任何异常都降级为第一次裁决的结果
        _restore(settings, post_first)
        return audit, violations, _record("failed", before, before, [f"{type(exc).__name__}: {exc}"])

    after = tech_model.repairable_violations(new_violations)
    if len(after) < len(before):
        return new_audit, new_violations, _record("accepted", before, after, notes)
    _restore(settings, post_first)
    return audit, violations, _record(
        "rejected", before, after, notes + ["修复后可修复违规没有减少，保留第一次裁决"],
    )
