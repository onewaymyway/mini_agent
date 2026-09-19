"""world_simulator/engine/structural_change.py — 结构性变化的解析/
采纳（阶段二十三，4.14 节）+ 因果线建议（阶段二十九，4.26 节）。

从原单体 `engine.py` 拆分而来（阶段三十，4.18 节剩余部分），纯粹的
内部重组，行为不变。
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any, Dict, Optional

from world_simulator import causal_tree
from world_simulator.engine.errors import SimEngineError
from world_simulator.state_model import SimManifest
from world_simulator.store import SimStore, now_iso

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
            `structural_change`、或已经采纳过。
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

    # 阶段二十九（`next_doc/world_simulator_universal_simulator_gap_
    # analysis_and_roadmap_v2_plan.md` 4.26 节，开放世界闭环收尾）：
    # 采纳 `new_mechanism`/`regime_shift` 时，顺带生成一条默认因果线
    # 草稿放进 `settings.suggested_causal_lines`，供用户在因果线总览
    # 页面里选择"接受这条建议线"（复用 `causal_lines` 手动覆盖入口，
    # 见 `app.py::_render_suggested_causal_lines()`）。不自动登记进
    # `settings.causal_lines`——是否要给这个新结构专门开一条因果线，
    # 仍然由用户判断，engine 只负责"发现并建议"。`kind == "new_entity"`
    # 场景不生成建议：新实体更多体现在 `vars.entities` 里，不强制每个
    # 新实体都对应一条独立因果线。
    kind = target.structural_change.get("kind", "")
    if kind in ("new_mechanism", "regime_shift"):
        description = target.structural_change.get("description", "")
        suggestion_id = f"suggested_{secrets.token_hex(3)}"
        label = description[:24] if description else suggestion_id
        suggested = list(manifest.settings.get("suggested_causal_lines") or [])
        suggested.append(
            {
                "id": suggestion_id,
                "label": label,
                "time_granularity": "",
                "source_kind": kind,
                "source_description": description,
                "source_step": step,
                "future_tree": causal_tree.build_default_future_tree(label, step),
            }
        )
        manifest.settings = {**manifest.settings, "suggested_causal_lines": suggested}

    store.save_manifest(manifest)
    return manifest


def accept_suggested_causal_line(
    data_dir: Path, sim_id: str, suggestion_id: str
) -> SimManifest:
    """把 `apply_structural_change()` 生成的一条因果线建议正式接入
    `settings.causal_lines`（阶段二十九，4.26 节）。

    这是"建议 → 正式登记"的唯一写入通道——`apply_structural_change()`
    本身只追加建议、不修改 `causal_lines`，只有用户在因果线总览页面
    点击"接受这条建议线"、调用这个函数时，才会真正生效。接受后从
    `suggested_causal_lines` 里移除这条建议（不保留"已接受"状态——
    一旦进了 `causal_lines`，因果线总览本身就会展示它，不需要在建议
    列表里重复标注）。拒绝（不接受）不需要调用任何函数，用户可以在
    UI 上直接忽略这条建议，也提供 `reject_suggested_causal_line()`
    供用户主动清除。

    Raises:
        SimEngineError: 找不到这条建议 id。
    """
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    suggested = list(manifest.settings.get("suggested_causal_lines") or [])
    match = next(
        (s for s in suggested if isinstance(s, dict) and str(s.get("id")) == suggestion_id),
        None,
    )
    if match is None:
        raise SimEngineError(f"找不到 id={suggestion_id!r} 的因果线建议")

    causal_lines = list(manifest.settings.get("causal_lines") or [])
    causal_lines.append(
        {
            "id": match.get("id"),
            "label": match.get("label"),
            "time_granularity": match.get("time_granularity", ""),
            "future_tree": match.get("future_tree"),
        }
    )
    remaining = [s for s in suggested if str(s.get("id")) != suggestion_id]
    manifest.settings = {
        **manifest.settings,
        "causal_lines": causal_lines,
        "suggested_causal_lines": remaining,
    }
    store.save_manifest(manifest)
    return manifest


def reject_suggested_causal_line(
    data_dir: Path, sim_id: str, suggestion_id: str
) -> SimManifest:
    """从 `settings.suggested_causal_lines` 里主动清除一条建议（用户
    判断"不需要为这个新结构专门开一条因果线"），不影响 `causal_lines`。
    找不到该 id 时视为已经清除过，静默返回（幂等，避免重复点击报错）。
    """
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    suggested = list(manifest.settings.get("suggested_causal_lines") or [])
    remaining = [s for s in suggested if str(s.get("id")) != suggestion_id]
    manifest.settings = {**manifest.settings, "suggested_causal_lines": remaining}
    store.save_manifest(manifest)
    return manifest
