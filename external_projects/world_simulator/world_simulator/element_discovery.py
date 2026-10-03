"""world_simulator/element_discovery.py — 元素周期扫描（第二十三轮 E5，`next_doc/
world_simulator_element_causal_lines_plan.md` §4.8）。

## 为什么需要它

元素发现（E3）靠 LLM 在推进输出里主动写 `discovered_elements`。万一某类对象 LLM 长期漏掉，没有任何
兜底。本模块提供一个**可选**的兜底：每隔 N 步（或用户手动点一次）单独调一次 LLM，回看最近几步的叙事/
事件和 `vars.entities`，列出"反复出现但未建模"的对象。形态参照 `capability_discovery.py`（同一个
`type: agent` workflow + `extract_agent_json_output` 的套路）。

## 和 `capability_discovery` 的区别

- **建议直接走与 `discovered_elements` 同一套校验与登记**（`element_registry.register_scan_items`：
  别名去重、关键性门槛、预算、桩元素待补全），而不是"只建议、用户确认"——元素登记本身是低风险的
  结构操作（登记错了不污染 `vars`，可以 retire/merge），并且计划明确要求"走同一套校验"。
  扫描只列"反复出现"的对象，所以视为已满足候选池的提次门槛：有 `relations` 或有 `why_key` 就登记，
  两者都没有仍留在候选池。
- **默认关闭**：这是**额外的 LLM 调用**（`settings.element_scan_interval` 缺省 = 0）。吸取
  `problem_discovery` 那次"默认关、没人开"的教训，设置页把它写清楚是什么、要花多少调用，而不是改默认。
- 扫描**不产生 `lifecycle_seed`**：技术裁决只发生在推进那一步，事后没有出口。

## 在引擎里的位置

`engine/advance.py` 在 `mechanisms.snapshot_and_check()` **之前**调用 `safe_scan_in_step()`——这样新登记的
元素进入本步的分支快照（否则分叉到这一步会丢掉扫描的结果）。审计追加到 `next_state.element_audit`
（动作名 `scan_*`/`registered`/`candidate`…，来源 `element_scan`）。任何异常都被吞掉，不影响本次推进。
手动扫描（`scan_now`）作用于当前分支的当前设置，由调用方保存 manifest。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from world_simulator import element_registry as er

WORKFLOW_NAME = "element_discovery"
MAX_SUGGESTIONS_DEFAULT = 5
RECENT_STEPS_DEFAULT = 6
SCAN_SOURCE = "element_scan"


class ElementDiscoveryError(RuntimeError):
    """`suggest_elements()` 底层 workflow 调用失败/无法解析结果时抛出。"""


def get_scan_interval(settings: Optional[Dict[str, Any]]) -> int:
    """`settings.element_scan_interval`：缺省/非法/负数 = 0（关）。"""
    raw = (settings or {}).get("element_scan_interval")
    if raw is None or isinstance(raw, bool):
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _registered_view(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in er.get_lines(settings):
        if not er.is_alive(line):
            continue
        rows.append({
            "id": str(line["id"]).strip(),
            "label": str(line.get("label") or line["id"]).strip(),
            "kind": line.get("kind") or "element",
            "element_type": str(line.get("element_type") or ""),
            "parent": str(line.get("parent") or ""),
            "aliases": [str(a) for a in line.get("aliases") or []],
        })
    return rows


def _candidates_view(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"id": str(c["id"]).strip(), "label": str(c.get("label") or ""), "mentions": int(c.get("mentions", 1) or 1)}
        for c in er.get_candidates(settings)
    ]


def _history_view(history: List[Any], recent_steps: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for s in (history or [])[-max(int(recent_steps), 0):]:
        events = []
        for ev in getattr(s, "sampled_events", None) or []:
            if isinstance(ev, dict) and not ev.get("suppressed_by_cap"):
                name = str(ev.get("label") or ev.get("description") or ev.get("id") or "").strip()
                if name:
                    events.append(name)
        rows.append({
            "step": getattr(s, "step", None),
            "summary": getattr(s, "summary", ""),
            "narrative": getattr(s, "narrative", ""),
            "events": events,
        })
    return rows


def _entities_view(current_vars: Any) -> Dict[str, Any]:
    if isinstance(current_vars, dict) and isinstance(current_vars.get("entities"), (dict, list)):
        return {"entities": current_vars["entities"]}
    return {}


def parse_suggestions(raw: Any, *, max_suggestions: int = MAX_SUGGESTIONS_DEFAULT) -> List[Dict[str, Any]]:
    """把 LLM 回复里的 `suggestions` 规整成 `discovered_elements` 同形的条目列表：
    不是对象/没有 id 也没有 label 的跳过（不中断其它条目）；`lifecycle_seed`/`future_tree` 丢弃
    （扫描只列对象本身，树由登记时的兜底模板或下一步补全给）。"""
    out: List[Dict[str, Any]] = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        norm = er._norm_item(item)
        if norm is None:
            continue
        out.append({
            "id": norm["id"], "label": norm["label"], "element_type": norm["element_type"],
            "parent": norm["parent"], "aliases": norm["aliases"], "why_key": norm["why_key"],
            "relations": norm["relations"],
        })
        if len(out) >= max_suggestions:
            break
    return out


def suggest_elements(
    cfg: Any,
    workspace_root: Path,
    settings: Dict[str, Any],
    current_vars: Dict[str, Any],
    history: List[Any],
    *,
    max_suggestions: int = MAX_SUGGESTIONS_DEFAULT,
    recent_steps: int = RECENT_STEPS_DEFAULT,
) -> List[Dict[str, Any]]:
    """让 LLM 回看一次"有没有反复出现但未建模的关键元素"，只返回规整后的建议，**不落盘**。

    Raises:
        ElementDiscoveryError: 找不到 workflow、执行未成功、或最终回复无法解析出 `suggestions`。
    """
    from mini_agent.workflow.runner import WorkflowRunner
    from mini_agent.workflow.store import WorkflowStore
    from world_simulator.agent_step_result import AgentStepOutputError, extract_agent_json_output

    wf = WorkflowStore(Path(workspace_root)).load(WORKFLOW_NAME)
    if wf is None:
        raise ElementDiscoveryError(
            f"找不到 workflow 定义 '{WORKFLOW_NAME}'（预期路径：{workspace_root}/workflows/{WORKFLOW_NAME}.yaml）"
        )
    result = WorkflowRunner(cfg).run(
        wf,
        {
            "registered_json": json.dumps(_registered_view(settings), ensure_ascii=False),
            "candidates_json": json.dumps(_candidates_view(settings), ensure_ascii=False),
            "entities_json": json.dumps(_entities_view(current_vars), ensure_ascii=False),
            "recent_history_json": json.dumps(_history_view(history, recent_steps), ensure_ascii=False),
            "max_suggestions": max_suggestions,
        },
    )
    if result.status != "done":
        failed = [f"{sr.step_id}({sr.status.value}): {sr.error}" for sr in result.step_results if sr.status.value != "done"]
        raise ElementDiscoveryError(f"{WORKFLOW_NAME} workflow 执行未成功：status={result.status}；" + "；".join(failed))
    step_result = next((sr for sr in result.step_results if sr.step_id == WORKFLOW_NAME), None)
    if step_result is None:
        raise ElementDiscoveryError(f"{WORKFLOW_NAME} 步骤没有产出结果")
    try:
        data = extract_agent_json_output(step_result.output, required_keys=["suggestions"])
    except AgentStepOutputError as exc:
        raise ElementDiscoveryError(f"{WORKFLOW_NAME} 步骤回复无法解析为元素建议：{exc}") from exc
    return parse_suggestions(data.get("suggestions"), max_suggestions=max_suggestions)


def register_suggestions(settings: Dict[str, Any], suggestions: List[Dict[str, Any]], *, step: int) -> List[Dict[str, Any]]:
    """建议 → 与 `discovered_elements` 同一套校验与登记；就地改 `settings`，返回审计（每条带 `source=element_scan`）。"""
    audit = er.register_scan_items(settings, suggestions, step=step)
    return [{**entry, "source": SCAN_SOURCE} for entry in audit]


def safe_scan_in_step(
    cfg: Any,
    workspace_root: Path,
    manifest: Any,
    *,
    history: List[Any],
    next_state: Any,
) -> None:
    """推进中的周期扫描（`element_scan_interval` > 0 且当前 step 是它的整数倍，元素模式开启时才跑）。

    必须在本步分支快照（`mechanisms.snapshot_and_check`）之前调用；就地改 `manifest.settings` 与
    `next_state.element_audit`。任何异常吞掉并往审计里留一条 `scan_error`，不影响本次推进。
    """
    try:
        settings = manifest.settings
        interval = get_scan_interval(settings)
        if interval <= 0 or not er.is_enabled(settings) or int(next_state.step) % interval != 0:
            return
    except Exception:  # noqa: BLE001
        return
    try:
        suggestions = suggest_elements(
            cfg, workspace_root, settings, next_state.vars, [*(history or []), next_state],
        )
        work = dict(settings)
        audit = register_suggestions(work, suggestions, step=int(next_state.step))
        manifest.settings = work
        entry = {"action": "scan", "found": len(suggestions), "source": SCAN_SOURCE}
        next_state.element_audit = [*(next_state.element_audit or []), entry, *audit]
    except Exception as exc:  # noqa: BLE001 — 旁路功能
        next_state.element_audit = [
            *(next_state.element_audit or []),
            {"action": "scan_error", "message": f"周期扫描失败，已跳过：{exc}", "source": SCAN_SOURCE},
        ]


def scan_now(
    cfg: Any,
    workspace_root: Path,
    settings: Dict[str, Any],
    current_vars: Dict[str, Any],
    history: List[Any],
    *,
    step: int,
) -> Dict[str, Any]:
    """手动"扫描遗漏元素"：调一次 LLM → 登记 → 返回 `{"settings": 新设置, "suggestions": [...], "audit": [...]}`。
    **不改入参 `settings`**，由调用方决定是否保存（和设置页其它保存动作一致）。"""
    suggestions = suggest_elements(cfg, workspace_root, settings, current_vars, history)
    work = dict(settings)
    audit = register_suggestions(work, suggestions, step=step)
    return {"settings": work, "suggestions": suggestions, "audit": audit}
