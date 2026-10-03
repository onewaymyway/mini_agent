"""world_simulator/engine/causal_lines.py — 因果线自动登记 + 未来树
（future_tree）合并。

从原单体 `engine.py` 拆分而来（阶段三十，4.18 节剩余部分），纯粹的
内部重组，行为不变。
"""

from __future__ import annotations

from typing import Any, Dict, List

from world_simulator import causal_tree, element_registry
from world_simulator.state_model import SimManifest, SimState


def _auto_register_causal_lines(manifest: SimManifest, next_state: SimState) -> None:
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


def _update_element_registry(
    manifest: SimManifest, next_state: SimState, data: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """元素模式下替换 `_auto_register_causal_lines()`（第二十三轮 E3，`next_doc/
    world_simulator_element_causal_lines_plan.md` §4.4）：处理 `element_enrichments`/
    `discovered_elements`，把这一步里所有引用 id 的位置（`line_updates`/`causal_links`/`tree_updates`/
    `tech_updates`）规范成已登记元素的 id，引用了未登记 id 的登记成待补全的桩元素。

    **必须在 `_apply_tree_updates()` 之前调用**（新元素要先存在，同一步的 `tree_updates` 才能引用它），
    也要在技术裁决（`mechanisms.apply_post_llm`）之前——`lifecycle_seed` 转成的合成登记提议和别名
    规范后的 `tech_updates` 都写回 `data["tech_updates"]`，由同一套技术裁决处理。

    就地改 `next_state.line_updates`/`next_state.causal_links`/`data`/`manifest.settings`，返回落到
    `SimState.element_audit` 的审计。**任何异常都不连累本次推进**：回退到旧的最小登记
    （`_auto_register_causal_lines`），审计里留一条 `error` 记录，`manifest.settings` 不留半截状态。
    """
    settings = dict(manifest.settings)
    try:
        result = element_registry.process_step(
            settings,
            step=next_state.step,
            line_updates=next_state.line_updates,
            causal_links=next_state.causal_links,
            tree_updates=data.get("tree_updates"),
            tech_updates=data.get("tech_updates"),
            discovered=data.get("discovered_elements"),
            enrichments=data.get("element_enrichments"),
            ops=data.get("element_ops"),
        )
    except Exception as exc:  # noqa: BLE001 — 旁路功能，绝不让它中断推进
        _auto_register_causal_lines(manifest, next_state)
        return [{"action": "error", "message": f"元素登记本步出错，已退回最小登记：{exc}"}]
    manifest.settings = settings
    next_state.line_updates = result["line_updates"]
    next_state.causal_links = result["causal_links"]
    if "tree_updates" in data or result["tree_updates"]:
        data["tree_updates"] = result["tree_updates"]
    if "tech_updates" in data or result["tech_updates"]:
        data["tech_updates"] = result["tech_updates"]
    return result["audit"]


def _apply_tree_updates(
    manifest: SimManifest, next_state: SimState, data: Dict[str, Any]
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
