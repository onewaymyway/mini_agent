"""world_simulator/engine/mechanisms.py — 两条推进路径共用的"新机制"步骤（第二十二轮 P8）。

`advance()`（主路径）与 `advance_lines()`（独立推进路径）都要按**同样的顺序、同样的开关**
跑第二十二轮 WP1–WP4 的机制。P8 之前这些步骤是 `advance()` 函数体里的内联代码，
`advance_lines()` 一个都不跑；P8 把它们原样抽到这里，两条路径各调一次，而不是在
`advance_lines()` 里再复制一份（复制会让两条路径慢慢漂移）。

**本模块只做搬运和两处很小的扩展，不改任何机制本身的规则**：
- `apply_post_llm()` 是 `advance()` 里"LLM 返回之后、快照之前"那一大段（事件落状态 →
  `elapsed_days` → 技术裁决（+可选修复调用）→ 树接地 → 因果处置/入队 → 树影响入队）逐行搬出，
  新增的唯一参数 `hold_out_pending` 默认 None，None 时行为与搬出前逐行相同；
- `anchor_tech_seed()`、`tree_status_before()`、`snapshot_and_check()` 同理。

独立推进路径专用的辅助（事件投放、每条线的提示词、多条线输出的合并）也放在本文件下半部分，
因为它们和上面的函数共享同样的开关语义。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from world_simulator import (
    anatomy_deepen,
    anatomy_engine,
    causal_engine,
    consistency_guard,
    dynamic_state,
    element_registry,
    event_sampler,
    relationship,
    tech_model,
    tree_effects,
    tree_grounding,
)
from world_simulator.engine.tech_repair import safe_repair_tech_step


def needs_elapsed(settings: Optional[Dict[str, Any]]) -> bool:
    """本步是否需要 `elapsed_days`（技术模型/事件采样/因果引擎延迟/关系延迟任一开启）。"""
    return bool(
        tech_model.is_enabled(settings)
        or event_sampler.is_enabled(settings)
        or causal_engine.needs_elapsed(settings)
        or relationship.needs_elapsed((settings or {}).get("relationships"))
        or anatomy_engine.needs_elapsed(settings)
    )


def any_mechanism_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    """P8 在独立推进路径上接入的“需要走 LLM 之后处理链”的机制是否至少开了一个。
    一致性守卫不算：它默认开启、只读记录，由 `snapshot_and_check()` 无条件处理。"""
    return bool(
        tech_model.is_enabled(settings)
        or event_sampler.is_enabled(settings)
        or causal_engine.is_enabled(settings)
        or tree_grounding.is_enabled(settings)
        or anatomy_engine.is_active(settings)
    )


def load_evidence_if_needed(store: Any, settings: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A9 要用证据时效：只在剖面引擎有事可做时才读证据文件，读失败当作没有。"""
    if not anatomy_engine.is_active(settings):
        return []
    try:
        return store.load_evidence()
    except Exception:  # noqa: BLE001
        return []


def anchor_tech_seed(store: Any, manifest: Any, branch: str, history: List[Any], current: Any) -> Tuple[List[Any], Any]:
    """技术种子锚定（WP1），搬自 `advance()`：返回（可能已刷新的）`(history, current)`。"""
    if tech_model.is_enabled(manifest.settings) and tech_model.storage_has_nodes(manifest.settings):
        _anchor = dynamic_state.latest_snapshot(history)
        if (_anchor is None or not tech_model.snapshot_has_nodes(_anchor, manifest.settings)) and dynamic_state.commit_working_copy(
            store, manifest, branch
        ):
            history = store.load_history(branch)
            current = store.load_current_state(branch) or current
    return history, current


def tree_status_before(settings: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """树接地/树影响需要的"推进前状态索引"（只在开启时才算），搬自 `advance()`。"""
    if tree_grounding.is_enabled(settings) or tree_effects.is_enabled(settings):
        return tree_grounding.status_index((settings or {}).get("causal_lines"))
    return None


def snapshot_and_check(
    manifest: Any, next_state: Any, history: List[Any], *, tree_before: Any
) -> None:
    """分支作用域动态状态快照（WP0）+ 一致性守卫（WP4），搬自 `advance()`，两者必须连着做。"""
    next_state.dynamic_snapshot = dynamic_state.snapshot_if_changed(manifest.settings, history)
    next_state.consistency_warnings = consistency_guard.safe_check_step(
        history,
        next_state,
        manifest.settings,
        tree_before=tree_before,
        tree_after=manifest.settings.get("causal_lines"),
    )


def apply_post_llm(
    cfg: Any,
    workspace_root: Path,
    manifest: Any,
    next_state: Any,
    data: Dict[str, Any],
    *,
    history: List[Any],
    tg_before: Optional[Dict[str, Any]],
    sampled_events: List[Dict[str, Any]],
    hold_out_pending: Optional[Callable[[Dict[str, Any]], bool]] = None,
    sim_id: str = "",
    branch: str = "",
    evidence_records: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """LLM 返回之后的机制链（就地修改 `manifest.settings` 与 `next_state`）。

    顺序（与 P8 之前 `advance()` 内联时完全一致）：事件落状态 → `elapsed_days` → 技术裁决
    （+可选修复）→ 树接地 → 因果处置 → 因果入队 → 树影响入队。`data` 只读这三个键：
    `elapsed_days`、`tech_updates`、`effect_dispositions`（修复调用另读 `narrative`/`next_summary`）。
    """
    _tg_before = tg_before
    history_for_prompt = history
    # 第二十二轮 WP2：把本步抽中的事件记进状态（审计 + 下一步冷却的依据）。
    next_state.sampled_events = sampled_events

    # 第二十二轮 WP1：技术模型裁决（提议–审核）。必须在下面
    # `dynamic_state.snapshot_if_changed()` 之前——`tech_state` 是分支作用域
    # 动态状态，快照要包含这一步的推进/迁移结果。未开启时整段是空操作，
    # 不产生任何新字段（`SimState.to_dict()` 对空值不输出）。
    # `elapsed_days` 也服务于外生事件采样（WP2：下一步的事件概率用最近几步的跨度
    # 估计），所以两个功能任一开启都记录它；技术裁决本身仍只在技术模型开启时跑。
    if (
        tech_model.is_enabled(manifest.settings)
        or event_sampler.is_enabled(manifest.settings)
        or causal_engine.needs_elapsed(manifest.settings)
        or relationship.needs_elapsed(manifest.settings.get("relationships"))
        or anatomy_engine.needs_elapsed(manifest.settings)
    ):
        next_state.elapsed_days = tech_model.normalize_reported_elapsed(data.get("elapsed_days"))
    # 第二十四轮 A4：技术裁决之前留一份生命周期快照（剖面引擎的 A3 回滚 / A7 增量判断用；未开启时为空，零开销）。
    _anatomy_pre = anatomy_engine.capture_pre(manifest.settings) if anatomy_engine.is_active(manifest.settings) else None
    if tech_model.is_enabled(manifest.settings):
        # 修复调用（P5b，独立 opt-in `tech_repair_enabled`）需要"本步开始前"的技术状态用来回滚重裁，
        # 所以只在开启时才拷贝，关闭时零开销、不走任何新分支。
        _pre_tech_state = (
            tech_model.capture_storage(manifest.settings) if tech_model.repair_enabled(manifest.settings) else None
        )
        next_state.tech_updates, next_state.tech_violations = tech_model.safe_apply_step(
            manifest.settings,
            data.get("tech_updates"),
            step=next_state.step,
            elapsed_days_raw=data.get("elapsed_days"),
        )
        if tech_model.repair_enabled(manifest.settings) and tech_model.repairable_violations(
            next_state.tech_violations
        ):
            (
                next_state.tech_updates,
                next_state.tech_violations,
                next_state.tech_repair,
            ) = safe_repair_tech_step(
                cfg,
                workspace_root,
                manifest.settings,
                pre_tech_state=_pre_tech_state,
                proposals=data.get("tech_updates"),
                elapsed_days_raw=data.get("elapsed_days"),
                step=next_state.step,
                audit=next_state.tech_updates,
                violations=next_state.tech_violations,
                narrative_hint=str(data.get("narrative", "") or data.get("next_summary", "") or ""),
            )

    # 第二十四轮 A4：元素剖面引擎。必须在技术裁决之后（要裁决 T 码放行的阶段声明）、树接地之前（条件可能读阶段/剖面子项）、
    # 快照之前（`causal_lines` 是分支作用域动态状态）。未开启或没有可结算的元素时是空操作，不产生任何新字段。
    if _anatomy_pre is not None:
        _stale = anatomy_engine.stale_evidence_ids(manifest.settings, evidence_records)
        next_state.anatomy_trace, next_state.anatomy_violations = anatomy_engine.safe_apply_step(
            manifest.settings, data.get("anatomy_updates"),
            step=next_state.step, elapsed_days_raw=data.get("elapsed_days"), pre=_anatomy_pre,
            sim_id=sim_id, branch=branch, vars_=next_state.vars, sampled_events=sampled_events,
            stale_evidence_ids=_stale,
        )

    # 第二十四轮 A5：深度模式。紧跟在轻量结算之后（深度提议走同一套裁决，要在其结果之上叠加）、树接地之前。
    # 默认关闭（`deep_enabled=false`）时是空操作；任何失败只降级为 A8，不影响本步推进。
    anatomy_deepen.safe_run_in_step(
        cfg, workspace_root, manifest, next_state, history=history,
        sampled_events=sampled_events, sim_id=sim_id, branch=branch,
    )

    # 第二十二轮 WP3 / P5c：因果树接地（前置强制 / 互斥组 / 结构化触发条件）。必须在技术裁决之后
    # （条件可能读 `tech_state`）、因果引擎入队之前（自动迁移要能被\"树分支 active\"触发源看到）、
    # 快照之前。降级/自动迁移的结果写回 `manifest.settings`，并同步修正本步 `tree_updates` 审计
    # 里的实际状态；未开启时整段是空操作。
    if _tg_before is not None and tree_grounding.is_enabled(manifest.settings):
        _tg_lines, _tg_audit, next_state.tree_grounding = tree_grounding.safe_enforce_step(
            manifest.settings.get("causal_lines"),
            _tg_before,
            vars_=next_state.vars,
            settings=manifest.settings,
            step=next_state.step,
            tree_updates=next_state.tree_updates,
            auto=tree_grounding.auto_transition_enabled(manifest.settings),
        )
        if next_state.tree_grounding:
            manifest.settings = {**manifest.settings, "causal_lines": _tg_lines}
            next_state.tree_updates = _tg_audit

    # 第二十二轮 WP3 / P5a：因果引擎。先处置 LLM 对*上一步遗留*待兑现项的回报，再把本步
    # 源头有进展的边入队（顺序不能反：本步新入队的项不该被本步回报处置）。必须在下面
    # `dynamic_state.snapshot_if_changed()` 之前——`causal_pending` 是分支作用域动态状态。
    # 未开启时整段是空操作，不产生任何新字段、不碰 `settings`。
    if causal_engine.is_enabled(manifest.settings):
        _cpending = manifest.settings.get("causal_pending")
        # P8：独立推进路径里，目标线本步没到点的待兑现项"无人可交代"，不能让它们被记成"到期未交代"
        # 而自动结案——先挑出来不参与处置，处置完再按原顺序放回（入队上限也要把它们算进去）。
        _held_ids: set = set()
        _orig_pending = list(_cpending or [])
        if hold_out_pending is not None:
            _kept = []
            for _p in _orig_pending:
                if isinstance(_p, dict) and hold_out_pending(_p):
                    _held_ids.add(id(_p))
                else:
                    _kept.append(_p)
            _cpending = _kept
        _cpending, next_state.effect_dispositions, _disp_violations = causal_engine.safe_apply_dispositions(
            manifest.settings, _cpending, data.get("effect_dispositions"),
            history=history_for_prompt, step=next_state.step,
        )
        if _held_ids:
            _new_by_id = {p.get("pending_id"): p for p in _cpending if isinstance(p, dict)}
            _restored = []
            for _p in _orig_pending:
                if id(_p) in _held_ids:
                    _restored.append(_p)
                elif isinstance(_p, dict) and _p.get("pending_id") in _new_by_id:
                    _restored.append(_new_by_id[_p.get("pending_id")])
            _cpending = _restored
        _cpending, next_state.causal_queued, _queue_violations = causal_engine.safe_queue_effects(
            manifest.settings, _cpending, next_state,
        )
        # 第二十二轮 P5d：本步新激活的树分支声明的影响（`effects_if_active`）入队，与边入队共用队列上限；
        # 在边入队之后、接地之后（看到的是降级后的树）。需要 `tree_effects_enabled`，否则空操作。
        _cpending, _tree_queued, _tree_violations = tree_effects.safe_queue_tree_effects(
            manifest.settings, _cpending, next_state, _tg_before, manifest.settings.get("causal_lines"),
        )
        next_state.causal_queued = next_state.causal_queued + _tree_queued
        next_state.causal_violations = _disp_violations + _queue_violations + _tree_violations
        manifest.settings = {**manifest.settings, "causal_pending": _cpending}


# ═════════════════════════════════════════════════════════════════════
# 以下仅供独立推进路径 `advance_lines()` 使用（P8）
#
# 主路径只有一次 LLM 调用，LLM 一次性看到全部机制提示并一次性输出全部字段；独立推进路径每步
# 对**每条到点的线**各调一次，而技术状态、待兑现因果、外生事件都是**全局**的。所以这里要回答三个
# 问题：谁看到什么提示（投放）、多条线的输出怎么并成一份（合并）、哪些待兑现项本步无人可交代（挂起）。
# ═════════════════════════════════════════════════════════════════════

TECH_DUPLICATE_CODE = "T10"
"""多条线对同一技术都给了提议：只采纳声明顺序靠前的那条，其余记 T10（info，不静默丢弃）。"""


def _owned(line: Dict[str, Any]) -> List[str]:
    raw = line.get("owned_vars")
    if not isinstance(raw, list):
        return []
    return [str(f).strip() for f in raw if str(f).strip()]


def owned_line_ids(causal_lines: List[Dict[str, Any]]) -> set:
    """声明了 `owned_vars` 的线 id——只有它们能被独立推进路径单独调用。"""
    return {
        str(line.get("id") or "").strip()
        for line in causal_lines
        if isinstance(line, dict) and str(line.get("id") or "").strip() and _owned(line)
    }


def event_delivered_to(event: Dict[str, Any], due_lines: List[Dict[str, Any]]) -> List[str]:
    """一个已抽中事件投给哪些到点线（用户确认的规则）：`affects` 为空 → 所有到点线；
    否则 `affects` 与线 id 或该线 `owned_vars` 有交集的线。被上限压掉的事件不投放。

    第二十三轮 E6：`affects` 里写的是**领域 id** 时，也投给 `parent` 为该领域的到点线（元素线）。
    旧式独立线没有 `parent` 字段，这条规则对它们不起作用（行为不变）。"""
    if event.get("suppressed_by_cap"):
        return []
    affects = {str(a).strip() for a in (event.get("affects") or []) if str(a).strip()}
    out: List[str] = []
    for line in due_lines:
        lid = str(line.get("id") or "").strip()
        parent = str(line.get("parent") or "").strip()
        if not affects or lid in affects or affects & set(_owned(line)) or (parent and parent in affects):
            out.append(lid)
    return out


def annotate_event_delivery(events: List[Dict[str, Any]], due_lines: List[Dict[str, Any]]) -> None:
    """给每个已抽中的事件记 `delivered_to`（投给了哪些线）。空列表 = 事件已发生并计入冷却，
    但本步没有任何到点线能写它（`affects` 指向没到点的线/没被任何线拥有的字段）——可审计，不静默。"""
    for ev in events:
        if isinstance(ev, dict) and not ev.get("suppressed_by_cap"):
            ev["delivered_to"] = event_delivered_to(ev, due_lines)


def pending_routed_to(pending: Dict[str, Any], line_id: str, owned_ids: set) -> bool:
    """待兑现项由谁交代：目标线是可独立推进的线 → 只有目标线；目标线不在可推进的线里 → 所有到点线。"""
    target = str(pending.get("to_line_id") or "").strip()
    return target == line_id or target not in owned_ids


def hold_out_predicate(owned_ids: set, due_ids: set) -> Callable[[Dict[str, Any]], bool]:
    """本步无人可交代的待兑现项：目标线可独立推进、但本步没到点。它们不参与处置（不记“到期未交代”）。"""

    def _hold(pending: Dict[str, Any]) -> bool:
        target = str(pending.get("to_line_id") or "").strip()
        return target in owned_ids and target not in due_ids

    return _hold


_LINE_ELAPSED_ASK = (
    "【时间跨度】请在输出里给可选字段 `elapsed_days`：**这条线这一次推进**（它自己节奏下的这一步）在模拟世界里\n"
    "跨越的天数（正数，量级估计即可）。引擎取本步所有到点线申报值里最大的一个作为这一步的全局时长，用于外生\n"
    "事件概率、技术进度和因果延迟；不给则按占位天数估计，精度降低。"
)

_LINE_TECH_NOTE = (
    "（本步有多条线同时被推进，技术状态是全局的：只提议与**这条线**直接相关的技术变化；同一项技术若多条线都给了\n"
    "提议，引擎只采纳声明顺序靠前的那条，其余会被记录并忽略。）"
)


def build_line_hint(
    settings: Dict[str, Any],
    history: List[Any],
    step: int,
    *,
    line: Dict[str, Any],
    events: List[Dict[str, Any]],
    owned_ids: set,
    sim_id: str = "",
    branch: str = "",
) -> str:
    """给**一条**到点线的机制提示词（`line_evolve.yaml` 的 `{line_mechanism_hint}`）。
    机制全关时返回空字符串。各段都有旁路兜底：出错只丢那一段，不拖垮推进。"""
    parts: List[str] = []
    line_id = str(line.get("id") or "").strip()

    if event_sampler.is_enabled(settings):
        mine = [
            e for e in events
            if isinstance(e, dict) and line_id in (e.get("delivered_to") or [])
        ]
        if mine:
            rows = ["【外生事件采样已开启】本步**已发生**、与这条线相关的外部事件（引擎按先验概率抽样，不是你的选择）："]
            for e in mine:
                piece = f"- [{e.get('id')}] {e.get('description')}（严重度 {e.get('severity')}"
                if e.get("affects"):
                    piece += "；可能影响：" + "、".join(str(a) for a in e["affects"])
                rows.append(piece + "）")
            rows.append(
                "请把它们作为既成事实自然地写入这条线这一步的推进；不要否认、替换它们，也不要再凭空引入与已声明先验\n"
                "无关的突发外部冲击（由这条线自身逻辑引发的后果不受此限）。"
            )
            parts.append("\n".join(rows))
        else:
            parts.append(
                "【外生事件采样已开启】引擎对本步做了抽样：没有与这条线相关的已抽中外部事件。允许并鼓励写平静的\n"
                "推进，不要凭空引入突发的外部冲击（由这条线自身逻辑引发的后果不受此限）。"
            )

    if tech_model.is_enabled(settings):
        hint = tech_model.safe_build_hint(settings)
        if hint:
            parts.append(hint + "\n" + _LINE_TECH_NOTE)

    if anatomy_engine.is_active(settings):
        hint = anatomy_engine.safe_build_hint(
            settings, history, step, sim_id=sim_id, branch=branch,
            sampled_events=events, ask_elapsed_override=False,
        )
        if hint:
            parts.append(hint)

    if causal_engine.is_enabled(settings):
        pending = [
            p for p in (settings.get("causal_pending") or [])
            if isinstance(p, dict) and pending_routed_to(p, line_id, owned_ids)
        ]
        hint = causal_engine.safe_build_hint(
            {**settings, "causal_pending": pending}, history, step, ask_elapsed_override=False
        )
        if hint:
            parts.append(hint)

    if needs_elapsed(settings):
        parts.append(_LINE_ELAPSED_ASK)
    # 第二十三轮 E6：元素模式下，这条线的输出也可以带 `discovered_elements`。放在最后，元素模式关闭时为空，
    # 其余各段与 E6 之前逐字节一致。
    elem = element_registry.safe_build_line_hint(settings, step=step)
    if elem:
        parts.append(elem)
    return "\n\n".join(parts)


def merge_line_outputs(
    outputs: List[Tuple[str, Dict[str, Any]]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, float]]:
    """把多条线各自的输出并成 `apply_post_llm()` 要的一份 `data`。

    返回 `(data, 合并说明, 各线申报的有效跨度)`：
    - `elapsed_days`：各线申报的合法正数取**最大值**（用户确认的口径），一个都没有则 None；
    - `tech_updates`：按线声明顺序拼接，同一技术只采纳先到的那条，其余记 T10（info）；
    - `effect_dispositions`：直接拼接（`apply_dispositions` 自己对同一项取第一条）；
    - `narrative`：各线叙事拼接，仅供技术修复调用做上下文。
    """
    reported: Dict[str, float] = {}
    tech: List[Any] = []
    seen_tech: Dict[str, str] = {}
    notes: List[Dict[str, Any]] = []
    dispositions: List[Any] = []
    narratives: List[str] = []
    discovered: List[Any] = []
    for line_id, data in outputs:
        days = tech_model.normalize_reported_elapsed(data.get("elapsed_days"))
        if days is not None:
            reported[line_id] = days
        raw_tech = data.get("tech_updates")
        for prop in raw_tech if isinstance(raw_tech, list) else []:
            pid = tech_model._proposal_id(prop)
            if pid and pid in seen_tech:
                notes.append(tech_model._violation(
                    TECH_DUPLICATE_CODE, pid,
                    f"技术「{pid}」被多条线提议，只采纳线「{seen_tech[pid]}」的那条，已忽略线「{line_id}」的",
                    "info", lines=[seen_tech[pid], line_id],
                ))
                continue
            if pid:
                seen_tech[pid] = line_id
            tech.append(prop)
        raw_disp = data.get("effect_dispositions")
        if isinstance(raw_disp, list):
            dispositions.extend(raw_disp)
        # 第二十三轮 E6：各线发现的新元素按线声明顺序拼接；重复的（同 id/名称/别名）由元素注册表按“先到先采纳、
        # 后到的并入”去重（`element_registry.process_step` → `_discover_one`），这里不重复实现。
        raw_disc = data.get("discovered_elements")
        if isinstance(raw_disc, list):
            discovered.extend(raw_disc)
        text = str(data.get("narrative", "") or data.get("summary", "") or "")
        if text:
            narratives.append(text)
    merged: Dict[str, Any] = {
        "elapsed_days": max(reported.values()) if reported else None,
        "tech_updates": tech,
        "effect_dispositions": dispositions,
        "narrative": "\n".join(narratives),
    }
    if discovered:  # 只有真的有才加键：没有时 `merged` 与 E6 之前逐字节一致
        merged["discovered_elements"] = discovered
    return merged, notes, reported


_AU_KEYS = {
    # 键 → 取"同一件事"身份的字段（先到先采纳，后到的重复项记 A12）
    "metric_deviations": ("element", "metric"),
    "bottleneck_proposals": ("element", "bottleneck"),
    "new_subitems": ("element", "part", "_item_id"),
    "signals": ("element", "watch"),
}


def merge_anatomy_updates(
    outputs: List[Tuple[str, Dict[str, Any]]],
) -> Tuple[Dict[str, List[Any]], List[Dict[str, Any]]]:
    """第二十四轮 A5：把各线输出里的 `anatomy_updates` 并成一份（**先到先采纳**，按线声明顺序）。

    同一件事（同元素同指标/瓶颈/新子项/信号）被多条线提议时，只采纳先到的，后到的记 `A12`（info）。
    返回 `(合并后的 anatomy_updates（没有任何提议则为空 dict）, 合并说明)`。不改 `merge_line_outputs` 的返回签名。"""
    merged: Dict[str, List[Any]] = {}
    notes: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[Tuple[str, ...], str]] = {k: {} for k in _AU_KEYS}
    for line_id, data in outputs:
        au = data.get("anatomy_updates") if isinstance(data, dict) else None
        if not isinstance(au, dict):
            continue
        for key, fields in _AU_KEYS.items():
            block = au.get(key)
            for prop in block if isinstance(block, list) else []:
                if not isinstance(prop, dict):
                    continue
                item = prop.get("item") if isinstance(prop.get("item"), dict) else {}
                ident = tuple(
                    str(item.get("id") or item.get("name") or "") if f == "_item_id" else str(prop.get(f) or "")
                    for f in fields
                )
                if all(ident):
                    first = seen[key].get(ident)
                    if first is not None:
                        notes.append({
                            "code": "A12", "severity": "info",
                            "message": f"{key} 中「{'/'.join(ident)}」被多条线提议，只采纳线「{first}」的那条，已忽略线「{line_id}」的",
                            "detail": {"lines": [first, line_id], "key": key},
                        })
                        continue
                    seen[key][ident] = line_id
                merged.setdefault(key, []).append(prop)
    return merged, notes
