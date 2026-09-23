"""world_simulator/engine/ledger_correction.py — 字段变化台账
（`field_ledger`）的一次性反馈修正调用（第十五轮，`next_doc/
world_simulator_fifteenth_round_field_ledger_plan.md` 3.4 节）。

只在 `resource_guard._check_field_ledger()` 发现 `ledger_violations`
非空时才会被 `advance.py` 调用一次；修正调用本身失败（workflow 报错/
解析失败等基础设施问题，不是"账还是不平"）时安全降级，保留修正前的
结果，不让一次可选的修正调用失败拖垮整个推进流程——同
`engine/knowledge.py` 里 `_safe_*` 系列包装函数的既有取舍。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from world_simulator.agent_step_result import AgentStepOutputError, extract_agent_json_output
from world_simulator.engine.resource_guard import _check_field_ledger, _get_nested, _set_nested


def _extract_relevant_vars(next_vars: Dict[str, Any], violations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从完整的 `next_vars` 里只摘出这次违规涉及的字段路径，组成一个
    精简的子集喂给修正 prompt（方案 3.4 节"只包含涉及问题字段的
    部分"），避免把整份 `next_vars` 都塞进修正调用的上下文。
    """
    relevant: Dict[str, Any] = {}
    seen_paths = {str(v.get("field") or "").strip() for v in violations if v.get("field")}
    for path in seen_paths:
        if not path:
            continue
        value = _get_nested(next_vars, path)
        _set_nested(relevant, path, value)
    return relevant


def _merge_next_vars_patch(next_vars: Dict[str, Any], patch: Any) -> Dict[str, Any]:
    """把修正调用给出的 `next_vars_patch`（一层嵌套路径写法，同
    `resource_fields`）合并回 `next_vars`，返回一份新字典（不就地
    修改传入的原始字典，调用方决定是否采用）。"""
    merged = dict(next_vars)
    if not isinstance(patch, dict):
        return merged
    for key, value in patch.items():
        if isinstance(value, dict):
            # 支持 `{"resources": {"cash": 1500}}` 这种一层嵌套写法：
            # 逐个子 key 按 `<outer>.<inner>` 路径写入。
            for inner_key, inner_value in value.items():
                _set_nested(merged, f"{key}.{inner_key}", inner_value)
        else:
            _set_nested(merged, key, value)
    return merged


def _merge_field_ledger_patch(
    field_ledger: List[Dict[str, Any]], patch: Any
) -> List[Dict[str, Any]]:
    """把修正调用给出的 `field_ledger_patch`（按字段整体替换的记账
    分组数组）合并回原始 `field_ledger`：补丁里出现的字段整体替换
    掉原来那个字段的所有记账记录，未出现在补丁里的字段保持原样。
    """
    if not isinstance(patch, list) or not patch:
        return list(field_ledger)
    patched_fields = {
        str(item.get("field") or "").strip()
        for item in patch
        if isinstance(item, dict) and str(item.get("field") or "").strip()
    }
    if not patched_fields:
        return list(field_ledger)
    kept = [entry for entry in field_ledger if entry.get("field") not in patched_fields]
    new_entries = [dict(item) for item in patch if isinstance(item, dict)]
    return kept + new_entries


def _safe_correct_field_ledger(
    cfg,
    workspace_root: Path,
    *,
    current_vars: Dict[str, Any],
    next_vars: Dict[str, Any],
    field_ledger: List[Dict[str, Any]],
    ledger_violations: List[Dict[str, Any]],
    narrative_hint: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """发起一次范围有限的记账修正调用，合并补丁并重新校验一次。

    只在 `ledger_violations` 非空时由调用方触发；**只修正一次**——
    这个函数本身不会重试，调用方也不应该在这个函数返回之后再次调用
    （方案 3.4 节第 4 点：修正一次之后剩余的不一致按"只标记、不
    阻断"处理）。

    Returns:
        `(修正后的 next_vars, 修正后的 field_ledger, 修正并重新校验后
        仍剩余的 ledger_violations)`——任何环节失败（workflow 找不到/
        执行失败/回复无法解析成 JSON 等基础设施问题）都安全降级为
        原样返回传入的三个参数，不抛出异常。
    """
    if not ledger_violations:
        return next_vars, field_ledger, ledger_violations

    try:
        from mini_agent.workflow.runner import WorkflowRunner
        from mini_agent.workflow.store import WorkflowStore
        import json

        wf_store = WorkflowStore(Path(workspace_root))
        wf = wf_store.load("ledger_correction")
        if wf is None:
            return next_vars, field_ledger, ledger_violations

        runner = WorkflowRunner(cfg)
        result = runner.run(
            wf,
            {
                "narrative_hint": narrative_hint or "",
                "next_vars_relevant_json": json.dumps(
                    _extract_relevant_vars(next_vars, ledger_violations), ensure_ascii=False
                ),
                "field_ledger_json": json.dumps(field_ledger, ensure_ascii=False),
                "violations_json": json.dumps(ledger_violations, ensure_ascii=False),
            },
        )
        if result.status != "done":
            return next_vars, field_ledger, ledger_violations

        step_result = next(
            (sr for sr in result.step_results if sr.step_id == "ledger_correction"), None
        )
        if step_result is None:
            return next_vars, field_ledger, ledger_violations

        try:
            data = extract_agent_json_output(
                step_result.output, required_keys=["next_vars_patch", "field_ledger_patch"]
            )
        except AgentStepOutputError:
            return next_vars, field_ledger, ledger_violations

        patched_vars = _merge_next_vars_patch(next_vars, data.get("next_vars_patch"))
        patched_ledger = _merge_field_ledger_patch(field_ledger, data.get("field_ledger_patch"))

        _, remaining_violations = _check_field_ledger(current_vars, patched_vars, patched_ledger)
        return patched_vars, patched_ledger, remaining_violations
    except Exception:
        # 修正调用本身是一次可选的旁路增强，任何基础设施异常都不应该
        # 拖垮整个推进流程——保留修正前的结果，剩余违规原样透传给
        # 调用方按"透明标记"处理。
        return next_vars, field_ledger, ledger_violations
