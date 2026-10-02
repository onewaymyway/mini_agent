"""world_simulator/tree_grounding.py — 因果树接地 P5c（第二十二轮 WP3 的 3c）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md`
§4 WP3 的 3c。纯 Python、**不调 LLM**。

## 为什么需要它

此前未来树是"标签树"：`prerequisites` 只在事后被 C3 读一次（告警，不阻断），
触发条件只是一句自由文本，互斥关系完全靠 LLM 自觉，`likelihood` 从不对账。
本模块让树的结构**开始约束世界**：

| 编号 | 做什么 | 默认 |
|---|---|---|
| (i) 前置强制 | 分支**本步新变为 active**、但同树里可解析到的前置分支还没 `resolved` → 降为 `emerging` 并记录（G1） | 随总开关 |
| (ii) 结构化触发条件 | 分支可带 `trigger_condition`（与外生事件同一套小谓词：变量比较 / 技术阶段）。每步求值，满足则作为 `trigger_met` **建议**喂给 LLM；子开关 `tree_auto_transition` 开启时才由引擎自动置 `active`（G4） | 只建议 |
| (iii) 互斥组 | 分支可带 `exclusive_group`（同一条线内同组互斥）。组内已有 `active/resolved` 时，新变 `active` 的分支降为 `emerging`（G2）；多个同时 `resolved` 只记录（G3）；组内有分支 `resolved` 时，同组 `dormant/emerging` 的是"落败者"，只建议 `invalidated`，`tree_auto_transition` 开启时才自动置（G5） | 随总开关 |
| (iv) 校准账本 | 从分支快照链里记录每个分支**终结那一刻**的 `likelihood` 与结局，汇总成按档位的命中率与"倒挂"检查（high 命中率低于 medium/low）。只读，不改树 | 体检里展示 |

## 开关（`manifest.settings`）

- `tree_grounding_enabled`（默认 False）：总开关。关闭时 `build_hint()` 返回空串、
  `enforce_step()` 不被调用，行为与之前**逐字节一致**。
- `tree_auto_transition`（默认 False，需总开关开启才生效）：自动迁移子开关。

## 引擎做什么 / 不做什么

做：拦截**结构性**不合法（前置未满足就 active、互斥组双占位）、对结构化条件求值、
透明记录每次降级/自动迁移。
不做：判断条件**语义上**是否合理、改任何变量数值、静默篡改（每次动作都写进
`SimState.tree_grounding`，并同步修正本步 `tree_updates` 审计里的实际状态，使
"本步实际生效的修改"这个口径不被引擎降级后的结果打脸）。

## 判定口径

- 只处理每条线 `future_tree.branches` 的**顶层**分支（与 `apply_tree_updates()`/
  `suggest_status_transitions()` 一致；`children`/`sub_branches` 里的分支引擎本来就
  没有写入路径）。
- 前置解析与 C3 一致：同线内按 id 命中 → 否则跨线按 id **唯一**命中 → 都不行（自由
  文本、重名歧义、自指）算"无法核验"，**不阻断**（避免笔误造成死锁，代价是形同
  虚设，与技术模型 T9 同一取舍）。"满足"= 前置分支状态为 `resolved`（严格口径）。
- 条件求值复用 `event_sampler.evaluate_condition()`：写法非法 / 变量不存在 →
  **不满足**（保守）。变量取**本步结束后**的 `next_vars`，技术阶段取本步裁决后的
  `tech_state`。
- 只对**本步新变为 active**（上一步不是 active）的分支强制前置/互斥；**不追溯**
  降级此前已经 active 的分支。

## 已知边界（如实记录）

- 引擎**无法判断** `trigger_condition` 本身写得对不对（谁来写：LLM 在新增分支时可选
  填，或用户在设置里手填；LLM 写的条件是猜测，不是事实）。
- LLM 直接把分支标成 `resolved` 而前置未满足，本模块**不拦截**（只有 C3 对 `active`
  告警）；`resolved` 是对"已发生"的陈述，引擎无法判断真假。
- 降级会造成"叙事说已激活、引擎说仍是 emerging"的错位；只能记录不能消除。
- `advance_lines()` 独立推进路径不跑本模块。
- 校准账本只有**终结事件**数据：样本通常很少，`n<min_n` 的档位不参与倒挂判断；
  `likelihood` 本身是 LLM 的主观档位，不是概率。账本本身只读；P9 起可另经
  `kb_calibration_tiers()` 写入跨实例 `knowledge_base`（默认开，需树接地，样本不足不写，
  条目标注"LLM 自报"，可撤销；见 `docs/knowledge_writeback_guide.md`）。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from world_simulator import causal_tree, event_sampler

_HOLDERS = frozenset({"active", "resolved"})
_OPEN = frozenset({"dormant", "emerging"})
_LIKELIHOODS = ("high", "medium", "low")
DEFAULT_LEDGER_MIN_N = 3

DISCLAIMER = "结构性约束与对账：不判断条件语义是否合理，likelihood 是主观档位而非概率。"


def is_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    return bool((settings or {}).get("tree_grounding_enabled"))


def auto_transition_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    return is_enabled(settings) and bool((settings or {}).get("tree_auto_transition"))


# ── 树遍历 / 索引 ────────────────────────────────────────────────────


def _top_branches(line: Any) -> List[Dict[str, Any]]:
    if not isinstance(line, dict):
        return []
    tree = line.get("future_tree")
    if not isinstance(tree, dict):
        return []
    return [b for b in (tree.get("branches") or []) if isinstance(b, dict)]


def _line_id(line: Any) -> str:
    return str((line or {}).get("id") or "").strip() if isinstance(line, dict) else ""


def _bid(branch: Dict[str, Any]) -> str:
    return str(branch.get("id") or "").strip()


def _status(branch: Dict[str, Any]) -> str:
    return causal_tree.canonical_status(branch.get("status"))


def status_index(causal_lines: Any) -> Dict[Tuple[str, str], str]:
    """`{(line_id, branch_id): 规整后的状态}`（仅顶层分支）。推进前后各取一份用来判断\"本步新变为 X\"。"""
    index: Dict[Tuple[str, str], str] = {}
    for line in causal_lines or []:
        lid = _line_id(line)
        if not lid:
            continue
        for branch in _top_branches(line):
            bid = _bid(branch)
            if bid:
                index[(lid, bid)] = _status(branch)
    return index


def _group_of(branch: Dict[str, Any]) -> str:
    return str(branch.get("exclusive_group") or "").strip()


def _resolve_prerequisite(line_id: str, prereq: str, index: Dict[Tuple[str, str], str]) -> Optional[Tuple[str, str]]:
    if (line_id, prereq) in index:
        return (line_id, prereq)
    matches = [key for key in index if key[1] == prereq]
    return matches[0] if len(matches) == 1 else None


def unmet_prerequisites(
    line_id: str, branch: Dict[str, Any], index: Dict[Tuple[str, str], str]
) -> Tuple[List[str], List[str]]:
    """返回 `(未满足的前置, 无法核验的前置)`。自指算无法核验。"""
    unmet: List[str] = []
    unverifiable: List[str] = []
    me = (line_id, _bid(branch))
    for prereq in branch.get("prerequisites") or []:
        prereq = str(prereq).strip()
        if not prereq:
            continue
        target = _resolve_prerequisite(line_id, prereq, index)
        if target is None or target == me:
            unverifiable.append(prereq)
        elif index[target] != "resolved":
            unmet.append(prereq)
    return unmet, unverifiable


def _entry(
    code: str, action: str, message: str, *, line_id: str, branch_id: str, severity: str = "info", **detail: Any
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "code": code, "action": action, "severity": severity, "message": message,
        "line_id": line_id, "branch_id": branch_id,
    }
    if detail:
        out["detail"] = detail
    return out


# ── 本步裁决 ─────────────────────────────────────────────────────────


def _patch_audit(audit: List[Dict[str, Any]], line_id: str, branch_id: str, from_status: str, to_status: str) -> None:
    """把本步 `tree_updates` 审计里\"声明为 from_status\"的那一项改成引擎实际生效的状态。"""
    for entry in audit:
        if not isinstance(entry, dict) or str(entry.get("line_id") or "") != line_id:
            continue
        for su in entry.get("status_updates") or []:
            if (
                isinstance(su, dict)
                and str(su.get("branch_id") or "") == branch_id
                and su.get("status") == from_status
            ):
                su["status"] = to_status
                su["grounded"] = True


def _auto_audit_entry(line_id: str, branch_id: str, status: str) -> Dict[str, Any]:
    return {
        "line_id": line_id,
        "confirmed_branch": "",
        "pruned_branches": [],
        "new_branch_ids": [],
        "status_updates": [{"branch_id": branch_id, "status": status, "auto": True}],
        "expanded_branch_ids": [],
        "auto": True,
    }


def enforce_step(
    causal_lines: Any,
    status_before: Dict[Tuple[str, str], str],
    *,
    vars_: Optional[Dict[str, Any]],
    settings: Optional[Dict[str, Any]],
    step: int,
    tree_updates: Optional[List[Dict[str, Any]]] = None,
    auto: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """对\"本步树更新之后\"的树做接地裁决。

    返回 `(新的 causal_lines, 修正后的 tree_updates 审计, 本步 tree_grounding 条目)`。
    入参不被修改（深拷贝）。顺序：先处理本步新 `resolved`（占住互斥组），再处理新
    `active`（前置 / 互斥），再（仅 `auto`）互斥落败者与触发条件自动迁移。
    """
    lines = copy.deepcopy([x for x in (causal_lines or []) if isinstance(x, dict)])
    audit = copy.deepcopy([x for x in (tree_updates or []) if isinstance(x, dict)])
    entries: List[Dict[str, Any]] = []

    # 本步"新变为"某状态的分支（含本步新增的分支）。
    new_active: List[Tuple[str, Dict[str, Any]]] = []
    new_resolved: List[Tuple[str, Dict[str, Any]]] = []
    for line in lines:
        lid = _line_id(line)
        for branch in _top_branches(line):
            key = (lid, _bid(branch))
            now = _status(branch)
            if now == "active" and status_before.get(key) != "active":
                new_active.append((lid, branch))
            elif now == "resolved" and status_before.get(key) != "resolved":
                new_resolved.append((lid, branch))
    newly = {(lid, _bid(b)) for lid, b in new_active + new_resolved}

    # 互斥组占位：本步之前就是 active/resolved 且仍是的分支。
    holders: Dict[Tuple[str, str], List[str]] = {}
    for line in lines:
        lid = _line_id(line)
        for branch in _top_branches(line):
            group = _group_of(branch)
            if group and _status(branch) in _HOLDERS and (lid, _bid(branch)) not in newly:
                holders.setdefault((lid, group), []).append(_bid(branch))

    # 先处理新 resolved：它是对"已发生"的陈述，不改，只记录冲突。
    for lid, branch in new_resolved:
        group = _group_of(branch)
        if not group:
            continue
        held = holders.setdefault((lid, group), [])
        if held:
            entries.append(_entry(
                "G3", "flagged",
                f"互斥组「{group}」（线 {lid}）内已有 {', '.join(held)}，分支「{_bid(branch)}」又变为 resolved",
                line_id=lid, branch_id=_bid(branch), severity="warn", exclusive_group=group, held_by=list(held),
            ))
        held.append(_bid(branch))

    # 再处理新 active：前置 → 互斥。
    status_now = status_index(lines)
    for lid, branch in new_active:
        bid = _bid(branch)
        unmet, _unv = unmet_prerequisites(lid, branch, status_now)
        group = _group_of(branch)
        reason_code = reason = None
        detail: Dict[str, Any] = {}
        if unmet:
            reason_code = "G1"
            reason = f"前置 {', '.join(unmet)} 尚未 resolved，不能直接 active，已降为 emerging"
            detail = {"unmet_prerequisites": unmet}
        elif group and holders.get((lid, group)):
            reason_code = "G2"
            held = holders[(lid, group)]
            reason = f"互斥组「{group}」内已有 {', '.join(held)}（active/resolved），已降为 emerging"
            detail = {"exclusive_group": group, "held_by": list(held)}
        if reason_code:
            branch["status"] = "emerging"
            status_now[(lid, bid)] = "emerging"
            _patch_audit(audit, lid, bid, "active", "emerging")
            entries.append(_entry(
                reason_code, "downgraded", f"分支「{bid}」（线 {lid}）：{reason}",
                line_id=lid, branch_id=bid, severity="warn", from_status="active", to_status="emerging", **detail,
            ))
        elif group:
            holders.setdefault((lid, group), []).append(bid)

    if auto:
        # G5：互斥组里本步新 resolved 的分支 → 同组 dormant/emerging 的落败者自动 invalidated。
        for lid, winner in new_resolved:
            group = _group_of(winner)
            if not group:
                continue
            for line in lines:
                if _line_id(line) != lid:
                    continue
                for other in _top_branches(line):
                    if other is winner or _group_of(other) != group or _status(other) not in _OPEN:
                        continue
                    other_id = _bid(other)
                    other["status"] = "invalidated"
                    status_now[(lid, other_id)] = "invalidated"
                    audit.append(_auto_audit_entry(lid, other_id, "invalidated"))
                    entries.append(_entry(
                        "G5", "auto_invalidated",
                        f"互斥组「{group}」内「{_bid(winner)}」已 resolved，同组分支「{other_id}」（线 {lid}）自动置为 invalidated",
                        line_id=lid, branch_id=other_id, from_status="dormant/emerging", to_status="invalidated",
                        exclusive_group=group, winner=_bid(winner),
                    ))
        # G4：触发条件已满足的 dormant/emerging 分支自动置 active（前置/互斥仍要满足）。
        for line in lines:
            lid = _line_id(line)
            for branch in _top_branches(line):
                bid = _bid(branch)
                if _status(branch) not in _OPEN or not branch.get("trigger_condition"):
                    continue
                ok, _why = event_sampler.evaluate_condition(branch.get("trigger_condition"), vars_, settings)
                if not ok:
                    continue
                unmet, _unv = unmet_prerequisites(lid, branch, status_now)
                group = _group_of(branch)
                if unmet or (group and holders.get((lid, group))):
                    continue
                prev = _status(branch)
                branch["status"] = "active"
                status_now[(lid, bid)] = "active"
                if group:
                    holders.setdefault((lid, group), []).append(bid)
                audit.append(_auto_audit_entry(lid, bid, "active"))
                entries.append(_entry(
                    "G4", "auto_activated",
                    f"分支「{bid}」（线 {lid}）的触发条件已满足，自动由 {prev} 置为 active",
                    line_id=lid, branch_id=bid, from_status=prev, to_status="active",
                    trigger_condition=copy.deepcopy(branch.get("trigger_condition")),
                ))

    if entries:
        for line in lines:
            tree = line.get("future_tree")
            if isinstance(tree, dict):
                tree["as_of_step"] = step
    return lines, audit, entries


def safe_enforce_step(*args: Any, **kwargs: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """出错时不改树、不改审计，只返回一条 G0 记录——接地失败不能中断本次推进。"""
    try:
        return enforce_step(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        lines = copy.deepcopy([x for x in (args[0] or []) if isinstance(x, dict)]) if args else []
        audit = copy.deepcopy([x for x in (kwargs.get("tree_updates") or []) if isinstance(x, dict)])
        return lines, audit, [_entry(
            "G0", "error", f"树接地内部出错，本步已跳过：{type(exc).__name__}: {exc}",
            line_id="", branch_id="", severity="warn",
        )]


# ── 建议（提示词 + 界面共用）────────────────────────────────────────


def pending_suggestions(
    causal_lines: Any, vars_: Optional[Dict[str, Any]], settings: Optional[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """按**当前**状态计算的建议/约束，纯读不写。`kind`：

    - `trigger_met`：触发条件已满足且前置/互斥都放行 → 建议 active；
    - `trigger_blocked`：触发条件已满足，但前置未满足或互斥组被占 → 不能 active（附原因）；
    - `exclusive_loser`：同组有分支已 resolved，本分支（dormant/emerging）是落败者 → 建议 invalidated；
    - `prereq_unmet`：dormant/emerging 且有已可核验的前置未满足 → 提醒现在不能 active。
    """
    lines = [x for x in (causal_lines or []) if isinstance(x, dict)]
    index = status_index(lines)
    holders: Dict[Tuple[str, str], List[str]] = {}
    for line in lines:
        lid = _line_id(line)
        for branch in _top_branches(line):
            group = _group_of(branch)
            if group and _status(branch) in _HOLDERS:
                holders.setdefault((lid, group), []).append(_bid(branch))

    out: List[Dict[str, Any]] = []
    for line in lines:
        lid = _line_id(line)
        for branch in _top_branches(line):
            if _status(branch) not in _OPEN:
                continue
            bid = _bid(branch)
            group = _group_of(branch)
            unmet, _unv = unmet_prerequisites(lid, branch, index)
            held = holders.get((lid, group), []) if group else []
            resolved_holders = [h for h in held if index.get((lid, h)) == "resolved"]
            base = {"line_id": lid, "branch_id": bid}
            if resolved_holders:
                out.append({**base, "kind": "exclusive_loser", "suggested_status": "invalidated",
                            "exclusive_group": group, "winner": resolved_holders[0]})
                continue
            cond = branch.get("trigger_condition")
            if cond:
                ok, why = event_sampler.evaluate_condition(cond, vars_, settings)
                if ok and not unmet and not held:
                    out.append({**base, "kind": "trigger_met", "suggested_status": "active", "condition": cond})
                    continue
                if ok:
                    reasons = []
                    if unmet:
                        reasons.append(f"前置 {', '.join(unmet)} 未 resolved")
                    if held:
                        reasons.append(f"互斥组「{group}」已被 {', '.join(held)} 占用")
                    out.append({**base, "kind": "trigger_blocked", "reasons": reasons, "condition": cond})
                    continue
            if unmet:
                out.append({**base, "kind": "prereq_unmet", "unmet_prerequisites": unmet})
    return out


def _describe(sug: Dict[str, Any]) -> str:
    where = f"{sug['line_id']}/{sug['branch_id']}"
    kind = sug["kind"]
    if kind == "trigger_met":
        return f"{where}（触发条件已满足，前置与互斥放行）"
    if kind == "trigger_blocked":
        return f"{where}（触发条件已满足，但{'；'.join(sug.get('reasons') or [])}）"
    if kind == "exclusive_loser":
        return f"{where}（互斥组「{sug.get('exclusive_group')}」内「{sug.get('winner')}」已 resolved）"
    return f"{where}（前置 {', '.join(sug.get('unmet_prerequisites') or [])} 未 resolved）"


def build_hint(
    settings: Optional[Dict[str, Any]],
    causal_lines: Any = None,
    vars_: Optional[Dict[str, Any]] = None,
) -> str:
    """喂给 `advance_step`/`world_evolve` 的 `{tree_grounding_hint}`。未开启返回空串。

    协议说明每步都带（开启后的固定开销，与技术模型提示同一做法）；当前建议/约束只在
    非空时追加。条件按**当前**状态求值——引擎自动迁移则按**本步结束后**的状态再求一次。
    """
    if not is_enabled(settings):
        return ""
    auto = auto_transition_enabled(settings)
    text = (
        "因果树接地已开启：\n"
        "- 分支要变成 active，同树里能找到的 `prerequisites` 前置分支必须已经 resolved，"
        "否则引擎会把它降回 emerging；\n"
        "- 同一条线内 `exclusive_group` 相同的分支互斥：组内已有 active/resolved 时，其它分支不能再 active；\n"
        "- 新增分支时可选填 `trigger_condition`（结构化触发条件）："
        '`{"var": "a.b", "op": ">=", "value": 5}` 或 `{"tech": "技术id", "min_stage": "developer"}`，'
        "数组表示全部满足。这是你对\"什么情况下它会发生\"的可核验假设，没把握就不要填；\n"
    )
    text += (
        "- 触发条件满足、且前置/互斥放行的分支，引擎会在本步结束后**自动**置为 active；"
        "同组分支 resolved 后，同组 dormant/emerging 的落败者会被自动置为 invalidated。"
        if auto else
        "- 触发条件满足时引擎只给**建议**，是否采纳由你按情境判断（在 `tree_updates.status_updates` 里声明）。"
    )
    groups = {
        "trigger_met": "触发条件已满足（建议考虑置为 active）",
        "trigger_blocked": "触发条件已满足但被拦住（现在不能 active）",
        "exclusive_loser": "互斥落败者（建议考虑置为 invalidated）",
        "prereq_unmet": "前置未满足（现在不能 active）",
    }
    sugs = pending_suggestions(causal_lines, vars_, settings)
    for kind, title in groups.items():
        items = [_describe(s) for s in sugs if s["kind"] == kind]
        if items:
            text += f"\n当前{title}：" + "；".join(items) + "。"
    return text


def safe_build_hint(*args: Any, **kwargs: Any) -> str:
    try:
        return build_hint(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return ""


# ── (iv) likelihood 校准账本 ─────────────────────────────────────────


def build_likelihood_ledger(history: Any) -> List[Dict[str, Any]]:
    """沿分支快照链找出\"某分支在相邻两份快照之间变为终结状态\"的事件，记下终结那一刻的
    `likelihood`（不是当前值）与结局。只覆盖 WP0 之后写入快照的步；新出现就已终结的
    分支（没有\"之前\"）不记。`step` 是终结被记录的那一步（快照落在的步）。"""
    from world_simulator import consistency_guard as cg

    ledger: List[Dict[str, Any]] = []
    prev: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None
    for state in history or []:
        snap = cg._get(state, "dynamic_snapshot")
        if not isinstance(snap, dict) or "causal_lines" not in snap:
            continue
        cur = cg.tree_index(snap.get("causal_lines"))
        if prev is not None:
            for key, new in cur.items():
                old = prev.get(key)
                if old is None:
                    continue
                if new["status"] in cg.TERMINAL_STATUSES and old["status"] not in cg.TERMINAL_STATUSES:
                    likelihood = new["likelihood"] if new["likelihood"] in _LIKELIHOODS else "medium"
                    ledger.append({
                        "line_id": key[0], "branch_id": key[1], "likelihood": likelihood,
                        "outcome": new["status"], "step": int(cg._get(state, "step", 0) or 0),
                    })
        prev = cur
    return ledger


def kb_calibration_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    """校准账本是否写入跨实例知识库（P9）：必须开启树接地，且 `settings.kb_calibration_writeback`
    不为 False（**默认 True**）。只对用了树接地的实例生效——和 P5b 的"因果引擎开启才回写"同一思路，
    避免升级后所有旧实例悄悄开始往共享库写东西。"""
    return is_enabled(settings) and bool((settings or {}).get("kb_calibration_writeback", True))


def kb_calibration_tiers(history: Any, *, own_after: Optional[int] = None) -> Dict[str, Dict[str, int]]:
    """给 `knowledge_base.record_likelihood_calibration()` 的输入：各档位 resolved/expired/invalidated 数。

    `own_after`：只统计 `step > own_after` 的终结事件——分叉出来的分支，分叉点及之前的历史是从
    源分支**拷贝**来的，那些终结事件属于源分支；不剔除会让同一个事件被两条分支各写一遍，
    在共享库里重复计数。`None`（主线）统计全部。
    """
    ledger = [
        e for e in build_likelihood_ledger(history)
        if own_after is None or int(e.get("step", 0) or 0) > own_after
    ]
    by = summarize_ledger(ledger)["by_likelihood"]
    return {
        tier: {k: int(by[tier].get(k, 0)) for k in ("resolved", "expired", "invalidated")}
        for tier in _LIKELIHOODS
    }


def summarize_ledger(
    ledger: List[Dict[str, Any]],
    *,
    nominal: Optional[Dict[str, Any]] = None,
    min_n: int = DEFAULT_LEDGER_MIN_N,
) -> Dict[str, Any]:
    """按 likelihood 档位汇总命中率（resolved 记命中，expired/invalidated 记未命中）。

    - `inversions`：两档样本都 ≥ `min_n` 且高档命中率**低于**低档（high<medium、medium<low、
      high<low）——这是不需要任何先验数值就能发现的\"档位没有区分度\"信号；
    - `nominal`（可选，用户给的 `{high: 0.8, medium: 0.5, low: 0.2}`）：给了才算
      `gap = observed - expected`。引擎不内置任何档位对应的概率。
    """
    by: Dict[str, Dict[str, Any]] = {
        name: {"n": 0, "resolved": 0, "expired": 0, "invalidated": 0, "hit_rate": None} for name in _LIKELIHOODS
    }
    for item in ledger or []:
        grp = by.get(item.get("likelihood"))
        if grp is None:
            continue
        grp["n"] += 1
        if item.get("outcome") in ("resolved", "expired", "invalidated"):
            grp[item["outcome"]] += 1
    for name, grp in by.items():
        grp["hit_rate"] = (grp["resolved"] / grp["n"]) if grp["n"] else None
        grp["enough_samples"] = grp["n"] >= min_n
        exp = (nominal or {}).get(name)
        if isinstance(exp, (int, float)) and not isinstance(exp, bool) and 0 <= float(exp) <= 1:
            grp["expected"] = float(exp)
            if grp["hit_rate"] is not None:
                grp["gap"] = grp["hit_rate"] - float(exp)
    inversions: List[Dict[str, Any]] = []
    for hi, lo in (("high", "medium"), ("medium", "low"), ("high", "low")):
        a, b = by[hi], by[lo]
        if a["n"] >= min_n and b["n"] >= min_n and a["hit_rate"] < b["hit_rate"]:
            inversions.append({
                "higher": hi, "lower": lo, "higher_hit_rate": a["hit_rate"], "lower_hit_rate": b["hit_rate"],
                "message": f"{hi} 档命中率（{a['hit_rate']:.0%}，n={a['n']}）低于 {lo} 档（{b['hit_rate']:.0%}，n={b['n']}）",
            })
    return {
        "total_terminal": len(ledger or []),
        "by_likelihood": by,
        "inversions": inversions,
        "min_n": min_n,
        "note": f"样本量通常很小，n<{min_n} 的档位不参与倒挂判断；likelihood 是主观档位，不是概率。",
        "entries": list(ledger or []),
    }
