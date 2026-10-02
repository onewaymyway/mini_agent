"""world_simulator/causal_engine.py — 因果结构可执行化 P5a（第二十二轮 WP3 的 3a + 3b）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md`
§4 WP3。纯 Python、**不调 LLM**。

## 范围（P5a 只做这两件事；3c 树接地 / 3d 树影响世界 / 3e 界面留给后续子阶段）

- **3a 边升级**：`settings.declared_causal_graph` 的条目在原有
  `{from_line_id, to_line_id, note}` 之上可选增加 `id`/`mechanism`/`sign`/
  `strength`/`delay_days`/`condition`/`confidence`/`enabled`。旧格式照常可用。
- **3b 待兑现因果队列**：声明的边在**源头出现实质进展**时入队，延迟期到了之后作为
  "到期因果压力"注入提示词；LLM 在 `effect_dispositions` 里回报
  `realized/dampened/postponed/countered + 原因`；引擎累计每条边的**兑现统计**。

## 引擎做什么 / 不做什么

做：持有待兑现队列（按分支存，`dynamic_state.DYNAMIC_KEYS` 里的 `causal_pending`）、
到期判定、校验 LLM 回报的合法性、透明记录违规、统计兑现情况。
不做：判断"这条因果语义上合不合理"、自动改任何变量/分支状态、给边打"精确概率"。
**LLM 仍然决定效果是否体现、怎么体现**——引擎只保证"该兑现的不会被悄悄忘掉"，
以及"没兑现的有交代"。

## 触发源（边的 `from_line_id` 命中以下任一即视为源头有进展）

1. `line_updates[from]` 存在且 `advanced` 不是 False（`line_advanced`）；
2. `tree_updates` 里该线有印证分支（`confirmed_branch`）或有分支被置为 `active`
   （`tree_branch`）；
3. 本步抽中的外生事件（`sampled_events`，未被上限压掉）的 `affects` 含该 id
   （`sampled_event`）；
4. 本步技术模型的 `transition` 审计里 `tech_id == from`（`tech_transition`）——
   所以边的端点既可以是因果线 id，也可以是技术节点 id。

账本显著变化、分支状态变化之外的触发源（计划 §4 WP3 3b 里提到的"账本中显著变化"）
**本阶段没做**：什么算"显著"需要阈值，没有数据前不拍脑袋（见 `docs/causal_engine_guide.md`）。

## 规则

- **每条边同一时刻最多一条未结案的待兑现项**：源头连续多步都有进展时，不会排出一堆
  重复条目，而是累加该条的 `retrigger_count`（避免提示词里出现"5 条同一条边的压力"）。
- **延迟用 `delay_days`（elapsed 天数）**，累计天数取自历史里各步的 `elapsed_days`；
  旧式 `delay_steps`（整数步）仍可用。任一所需步缺 `elapsed_days` 时退化为按步计数
  并标 `precision: "steps"`（"精度降级"，与 WP1/WP2 一致）：声明了 `delay_steps` 就按它，
  否则缺数据的那一步过去之后即视为到期。
  没有声明任何延迟 = 下一步到期。
- **处置校验**（`apply_dispositions`）：`realized` 直接结案；`dampened`/`countered`/
  `postponed` 必须给 `reason`，否则**忽略该条并记违规 E4**（沿用技术模型"没理由就不算数"）；
  `postponed` 不结案，超过 `max_postpone` 次后自动结案为 `expired`；到期项连续
  `max_ignored` 步没有被处置，自动结案为 `unaddressed`。**都透明记录，不静默**。
- 对**还没到期**的项给处置 → 违规 E3，忽略（提前兑现与声明的延迟矛盾，由用户决定要不要
  改延迟，而不是让 LLM 随手绕过）。
- 兑现统计 `edge_stats()` 从**分支历史**推导（`SimState.effect_dispositions`），所以天然
  按分支正确，不需要再加一份动态状态。

## 已知边界（如实记录）

- 引擎**无法验证** `realized` 是否真的体现在了 `next_vars`/叙事里，只能要求 LLM 如实回报；
  统计的是"LLM 说兑现了多少"，不是"世界里真兑现了多少"。
- 入队只看"源头有进展"，不看"进展的方向/幅度"（边的 `sign`/`strength` 只随提示词展示）。
- `delay_steps` 的语义修正只覆盖**本模块新增的边**；`relationship.py` 里关系的
  `delay_steps`（`relationship_pending_effects`）**本阶段没动**，仍按步计。
- 兑现统计没有回写 `knowledge_base` 的 `validated_count/contradicted_count`：那是跨实例副作用，
  与 P1 对 C8 的处理一致，待你确认后再做。
- `advance_lines()` 独立推进路径不入队、不处置。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from world_simulator import event_sampler, tech_model

_STRENGTHS = ("high", "medium", "low")
_CONFIDENCES = ("high", "medium", "low")
_SIGNS = ("positive", "negative", "mixed")
_SIGN_ALIASES = {
    "+": "positive", "positive": "positive", "pos": "positive", "增强": "positive", "正": "positive",
    "-": "negative", "negative": "negative", "neg": "negative", "削弱": "negative", "负": "negative",
    "mixed": "mixed", "both": "mixed", "混合": "mixed", "不确定": "mixed",
}
_SIGN_LABELS = {"positive": "正向（增强）", "negative": "负向（削弱）", "mixed": "方向不定"}
_STRENGTH_LABELS = {"high": "强", "medium": "中", "low": "弱"}

# 处置取值。`CLOSING` 表示结案；`postponed` 保持未结案。
DISPOSITIONS = ("realized", "dampened", "postponed", "countered")
_NEEDS_REASON = ("dampened", "postponed", "countered")
# 进入"兑现率"分母的处置（有明确结论的）。
_CONCLUSIVE = ("realized", "dampened", "countered")

DEFAULT_PARAMS: Dict[str, Any] = {
    # 通用占位值，不是领域事实。
    "max_postpone": 3,
    "max_ignored": 3,
    "max_pending": 40,
}


def is_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    return bool((settings or {}).get("causal_engine_enabled"))


def kb_writeback_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    """兑现结论是否回写跨模拟知识库（`settings.causal_kb_writeback`，**默认 True**）。
    只在因果引擎开启时才有意义；显式设为 False 即关闭回写。"""
    if not is_enabled(settings):
        return False
    return bool((settings or {}).get("causal_kb_writeback", True))


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def get_params(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`DEFAULT_PARAMS` 叠加 `settings.causal_params`（非法值忽略，必须是正整数）。"""
    params = dict(DEFAULT_PARAMS)
    override = (settings or {}).get("causal_params")
    if isinstance(override, dict):
        for key in DEFAULT_PARAMS:
            f = _num(override.get(key))
            if f is not None and f >= 1:
                params[key] = int(f)
    return params


# ── 3a：边 ───────────────────────────────────────────────────────────


def edge_id_of(raw: Dict[str, Any]) -> str:
    """边的引用标识：优先显式 `id`，否则 `from->to`。"""
    explicit = str(raw.get("id") or "").strip()
    if explicit:
        return explicit
    return f"{str(raw.get('from_line_id') or '').strip()}->{str(raw.get('to_line_id') or '').strip()}"


def normalize_edge(raw: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """规整一条边，返回 `(边, 问题)`：不可用时边为 None 并给出原因。

    旧格式 `{from_line_id, to_line_id, note}` 原样可用（新字段都是可选的）。
    不认识的 `sign`/`strength`/`confidence` 取值退化为 None（未声明），不猜默认档。
    """
    if not isinstance(raw, dict):
        return None, "不是对象"
    from_id = str(raw.get("from_line_id") or "").strip()
    to_id = str(raw.get("to_line_id") or "").strip()
    if not from_id or not to_id:
        return None, "缺少 from_line_id 或 to_line_id"
    if from_id == to_id:
        return None, f"「{from_id}」指向自己"
    sign = _SIGN_ALIASES.get(str(raw.get("sign") or "").strip().lower())
    strength = str(raw.get("strength") or "").strip().lower()
    confidence = str(raw.get("confidence") or "").strip().lower()
    delay_days = _num(raw.get("delay_days"))
    delay_steps = _num(raw.get("delay_steps"))
    condition = raw.get("condition")
    return {
        "id": edge_id_of(raw),
        "from_line_id": from_id,
        "to_line_id": to_id,
        "note": str(raw.get("note") or "").strip(),
        "mechanism": str(raw.get("mechanism") or "").strip(),
        "sign": sign,
        "strength": strength if strength in _STRENGTHS else None,
        "delay_days": delay_days if delay_days is not None and delay_days >= 0 else None,
        "delay_steps": max(0, int(delay_steps)) if delay_steps is not None else 0,
        "condition": condition if isinstance(condition, (dict, list)) else None,
        "confidence": confidence if confidence in _CONFIDENCES else None,
        "enabled": bool(raw.get("enabled", True)),
    }, None


def get_edges(settings: Optional[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """`settings.declared_causal_graph` → `(可用边, 被忽略的原因)`。重复 id 只留第一条。"""
    edges: List[Dict[str, Any]] = []
    problems: List[str] = []
    seen: set = set()
    raw_list = (settings or {}).get("declared_causal_graph")
    for i, raw in enumerate(raw_list if isinstance(raw_list, list) else []):
        edge, why = normalize_edge(raw)
        if edge is None:
            problems.append(f"第 {i + 1} 条：{why}")
            continue
        if edge["id"] in seen:
            problems.append(f"第 {i + 1} 条：边 id「{edge['id']}」重复，已忽略")
            continue
        seen.add(edge["id"])
        edges.append(edge)
    return edges, problems


# ── 时间 ─────────────────────────────────────────────────────────────


def elapsed_between(history: List[Any], after_step: int, through_step: int) -> Tuple[float, bool]:
    """历史里 `after_step < step <= through_step` 各步 `elapsed_days` 之和，以及是否
    **完整**（每一步都有有效值）。区间为空时返回 `(0.0, True)`。"""
    total = 0.0
    complete = True
    for state in history or []:
        step = int(getattr(state, "step", 0) or 0)
        if not (after_step < step <= through_step):
            continue
        days = _num(getattr(state, "elapsed_days", None))
        if days is None or days <= 0:
            complete = False
        else:
            total += days
    return total, complete


def _is_due(entry: Dict[str, Any], history: List[Any], step: int) -> Tuple[bool, str, Optional[float]]:
    """`(是否到期, 精度 days|steps, 已过天数)`。`step` 是**正在生成**的那一步。"""
    triggered = int(entry.get("triggered_at_step", 0))
    through = step - 1
    steps_since = max(0, through - triggered)
    delay_days = _num(entry.get("delay_days"))
    delay_steps = int(_num(entry.get("delay_steps")) or 0)
    if delay_days is not None:
        days, complete = elapsed_between(history, triggered, through)
        if complete:
            return days >= delay_days, "days", days
        # 精度降级：缺 elapsed_days，退化为按步计数。走到这里说明区间内至少有一步缺数据，
        # 所以 steps_since 必然 >= 1（不需要再额外取 max）；delay_steps 声明了更长的等待时照它算。
        return steps_since >= delay_steps, "steps", None
    return steps_since >= delay_steps, "steps", None


# ── 触发 ─────────────────────────────────────────────────────────────


def _source_reason(edge: Dict[str, Any], next_state: Any) -> Optional[str]:
    src = edge["from_line_id"]
    upd = (getattr(next_state, "line_updates", None) or {}).get(src)
    if isinstance(upd, dict) and upd.get("advanced", True) is not False:
        return "line_advanced"
    for entry in getattr(next_state, "tree_updates", None) or []:
        if not isinstance(entry, dict) or str(entry.get("line_id") or "") != src:
            continue
        if entry.get("confirmed_branch"):
            return "tree_branch"
        for su in entry.get("status_updates") or []:
            if isinstance(su, dict) and su.get("status") == "active":
                return "tree_branch"
    for ev in getattr(next_state, "sampled_events", None) or []:
        if isinstance(ev, dict) and not ev.get("suppressed_by_cap") and src in (ev.get("affects") or []):
            return "sampled_event"
    for upd in getattr(next_state, "tech_updates", None) or []:
        if isinstance(upd, dict) and upd.get("action") == "transition" and str(upd.get("tech_id") or "") == src:
            return "tech_transition"
    return None


def queue_effects(
    settings: Optional[Dict[str, Any]],
    pending: Optional[List[Dict[str, Any]]],
    next_state: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """本步源头有进展的边入队。返回 `(新的 pending, 本步入队/累加记录, 违规)`。

    同一条边已有未结案项时只累加 `retrigger_count`，不新增。边的 `condition`
    （同外生事件的谓词写法）不满足时不入队。超过 `max_pending` 时拒绝并记 E6。
    """
    result = [dict(p) for p in (pending or []) if isinstance(p, dict)]
    queued: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []
    edges, _ = get_edges(settings)
    params = get_params(settings)
    step = int(next_state.step)
    for edge in edges:
        if not edge["enabled"]:
            continue
        reason = _source_reason(edge, next_state)
        if reason is None:
            continue
        ok, _why = event_sampler.evaluate_condition(edge["condition"], next_state.vars, settings)
        if not ok:
            continue
        open_entry = next((p for p in result if p.get("edge_id") == edge["id"]), None)
        if open_entry is not None:
            open_entry["retrigger_count"] = int(open_entry.get("retrigger_count", 0)) + 1
            queued.append({"pending_id": open_entry["pending_id"], "edge_id": edge["id"], "action": "retriggered",
                           "trigger_reason": reason})
            continue
        if len(result) >= params["max_pending"]:
            violations.append(_violation(
                "E6", f"待兑现队列已满（{params['max_pending']}），边「{edge['id']}」本步未入队",
                edge_id=edge["id"]))
            continue
        entry = {
            "pending_id": f"{edge['id']}@{step}",
            "edge_id": edge["id"],
            "from_line_id": edge["from_line_id"],
            "to_line_id": edge["to_line_id"],
            "mechanism": edge["mechanism"],
            "sign": edge["sign"],
            "strength": edge["strength"],
            "note": edge["note"],
            "triggered_at_step": step,
            "trigger_reason": reason,
            "delay_days": edge["delay_days"],
            "delay_steps": edge["delay_steps"],
            "retrigger_count": 0,
            "postponed_count": 0,
            "ignored_count": 0,
        }
        result.append(entry)
        queued.append({"pending_id": entry["pending_id"], "edge_id": edge["id"], "action": "queued",
                       "trigger_reason": reason})
    return result, queued, violations


def safe_queue_effects(*args: Any, **kwargs: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """旁路兜底：出错时原样返回 pending、不入队，并记一条 E0（不拖垮整步推进）。"""
    try:
        return queue_effects(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — 旁路功能
        pending = args[1] if len(args) > 1 else kwargs.get("pending")
        return (list(pending or []), [],
                [_violation("E0", f"因果引擎入队出错，本步未入队：{type(exc).__name__}: {exc}")])


# ── 到期 + 提示词 ────────────────────────────────────────────────────


def due_entries(
    pending: Optional[List[Dict[str, Any]]], history: List[Any], step: int
) -> List[Dict[str, Any]]:
    """`step`（正在生成的这一步）上已到期的未结案项，带 `precision`/`elapsed_days_since`。"""
    out: List[Dict[str, Any]] = []
    for p in pending or []:
        if not isinstance(p, dict):
            continue
        due, precision, days = _is_due(p, history, step)
        if due:
            item = dict(p)
            item["precision"] = precision
            item["elapsed_days_since"] = days
            out.append(item)
    return out


def needs_elapsed(settings: Optional[Dict[str, Any]]) -> bool:
    """开启且至少有一条边（或 P5d 起树分支声明的影响）声明了 `delay_days`：此时每步都需要 LLM 给
    `elapsed_days`，否则延迟只能退化为按步计数。"""
    if not is_enabled(settings):
        return False
    edges, _ = get_edges(settings)
    if any(e["enabled"] and e["delay_days"] is not None for e in edges):
        return True
    # P5d：树分支声明的影响（`effects_if_active`）同样可能带 `delay_days`。延迟 import 避免循环依赖。
    from world_simulator import tree_effects

    return tree_effects.has_day_delays(settings)


_ELAPSED_ASK = (
    "【因果引擎已开启】部分因果边的延迟以天数声明：请在输出里给可选字段 `elapsed_days`"
    "（这一步在模拟世界里跨越的天数，正数，量级估计即可）；不给则这些边按步计数到期，精度降级。"
)


def build_hint(
    settings: Optional[Dict[str, Any]], history: List[Any], step: int
) -> str:
    """喂给 `advance_step`/`world_evolve` 的 `{causal_pending_hint}`。未开启、或既没有到期项
    也不需要索要 `elapsed_days` 时返回空字符串（此时 prompt 与未开启时逐字节等价）。"""
    if not is_enabled(settings):
        return ""
    due = due_entries((settings or {}).get("causal_pending"), history, step)
    # 技术模型/事件采样的提示词已经索要过 elapsed_days，不重复。
    ask_elapsed = (
        needs_elapsed(settings)
        and not (tech_model.is_enabled(settings) or event_sampler.is_enabled(settings))
    )
    if not due:
        return _ELAPSED_ASK if ask_elapsed else ""
    stats = edge_stats(history)
    lines = [
        "【因果引擎已开启】以下声明的因果边，源头已出现进展且延迟期已到——这一步该交代它们的效果了"
        "（引擎只负责提醒，是否体现、怎么体现由你判断，但**必须给出交代**）：",
    ]
    for e in due:
        src = f"{e['from_line_id']}/{e['branch_id']}" if e.get("branch_id") else e["from_line_id"]
        piece = f"- [{e['pending_id']}] {src} → {e['to_line_id']}"
        attrs: List[str] = []
        if e.get("mechanism"):
            attrs.append(f"机制：{e['mechanism']}")
        if e.get("sign"):
            attrs.append(f"方向：{_SIGN_LABELS[e['sign']]}")
        if e.get("strength"):
            attrs.append(f"强度：{_STRENGTH_LABELS[e['strength']]}")
        attrs.append(f"触发于第 {e['triggered_at_step']} 步（{_reason_label(e.get('trigger_reason'))}）")
        if e.get("elapsed_days_since") is not None:
            attrs.append(f"此后已过约 {e['elapsed_days_since']:g} 天")
        elif e.get("precision") == "steps":
            attrs.append("按步计数到期（时间精度降级）")
        if e.get("retrigger_count"):
            attrs.append(f"源头又出现进展 {e['retrigger_count']} 次")
        s = stats.get(e["edge_id"])
        if s and s["concluded"] >= 3 and s["realized_rate"] is not None:
            attrs.append(f"本世界历史：{s['concluded']} 次有结论中兑现 {s['realized']} 次")
        if e.get("ignored_count"):
            attrs.append(f"已有 {e['ignored_count']} 步未被交代")
        if e.get("note"):
            attrs.append(f"备注：{e['note']}")
        lines.append(piece + "（" + "；".join(attrs) + "）")
    lines.append(
        "请在输出里给可选字段 `effect_dispositions`（数组），每个到期项一条："
        "{\"pending_id\": \"上面方括号里的 id\", \"disposition\": \"realized\"|\"dampened\"|"
        "\"postponed\"|\"countered\", \"reason\": \"一句话原因\"}。"
        "realized=效果已在本步的 next_vars/line_updates/narrative 里体现；dampened=只体现了一部分；"
        "postponed=推迟（仍会在之后提醒你）；countered=被别的因素抵消。"
        "dampened/postponed/countered **必须给 reason**，否则引擎会忽略这一条。"
        "不要对上面没列出的 id 给交代。"
    )
    if ask_elapsed:
        lines.append(_ELAPSED_ASK)
    return "\n".join(lines)


def safe_build_hint(*args: Any, **kwargs: Any) -> str:
    try:
        return build_hint(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return ""


def _reason_label(reason: Any) -> str:
    return {
        "line_advanced": "源头线有进展", "tree_branch": "源头线的未来树分支被印证/激活",
        "sampled_event": "外生事件影响源头", "tech_transition": "源头技术阶段迁移",
        "tree_effect": "未来树分支被激活，其声明的影响",
    }.get(str(reason or ""), str(reason or "未知"))


# ── 处置 ─────────────────────────────────────────────────────────────


def _violation(code: str, message: str, **detail: Any) -> Dict[str, Any]:
    return {"code": code, "severity": "warn", "message": message, "detail": detail}


def apply_dispositions(
    settings: Optional[Dict[str, Any]],
    pending: Optional[List[Dict[str, Any]]],
    raw: Any,
    *,
    history: List[Any],
    step: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """应用 LLM 在本步回报的 `effect_dispositions`。返回
    `(新的 pending, 审计记录, 违规)`。

    **必须在本步入队之前调用**：只处理"上一步留下来的"待兑现项，本步新入队的不受影响。
    审计记录每条 `{pending_id, edge_id, from_line_id, to_line_id, disposition, reason,
    closed}`（`closed` 表示是否结案）；自动结案的另带 `auto: true`。
    """
    params = get_params(settings)
    items = [dict(p) for p in (pending or []) if isinstance(p, dict)]
    by_id = {p["pending_id"]: p for p in items if p.get("pending_id")}
    due_ids = {d["pending_id"] for d in due_entries(items, history, step)}
    audit: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []
    addressed: set = set()

    def _row(p: Dict[str, Any], disposition: str, reason: str, closed: bool, **extra: Any) -> Dict[str, Any]:
        row = {
            "pending_id": p["pending_id"], "edge_id": p.get("edge_id"),
            "from_line_id": p.get("from_line_id"), "to_line_id": p.get("to_line_id"),
            "disposition": disposition, "reason": reason, "closed": closed,
            "triggered_at_step": p.get("triggered_at_step"),
        }
        row.update(extra)
        return row

    closed_ids: set = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            violations.append(_violation("E1", "effect_dispositions 里有非对象条目，已忽略"))
            continue
        pid = str(entry.get("pending_id") or "").strip()
        p = by_id.get(pid)
        if p is None and pid:  # 容错：只给了边 id 且该边只有一个未结案项
            cands = [x for x in items if x.get("edge_id") == pid]
            p = cands[0] if len(cands) == 1 else None
        if p is None:
            violations.append(_violation("E1", f"effect_dispositions 引用了不存在/已结案的待兑现项「{pid}」，已忽略",
                                         pending_id=pid))
            continue
        pid = p["pending_id"]
        disp = str(entry.get("disposition") or "").strip().lower()
        if disp not in DISPOSITIONS:
            violations.append(_violation("E2", f"「{pid}」的 disposition「{disp}」不合法"
                                         f"（应为 {'/'.join(DISPOSITIONS)}），已忽略", pending_id=pid))
            continue
        reason = str(entry.get("reason") or "").strip()
        if disp in _NEEDS_REASON and not reason:
            violations.append(_violation("E4", f"「{pid}」声明 {disp} 但没有给 reason，已忽略", pending_id=pid,
                                         disposition=disp))
            continue
        if pid not in due_ids:
            violations.append(_violation("E3", f"「{pid}」延迟期未到，不接受提前处置（{disp}），已忽略",
                                         pending_id=pid, disposition=disp))
            continue
        if pid in addressed:
            continue  # 同一项重复回报，取第一条
        addressed.add(pid)
        if disp == "postponed":
            p["postponed_count"] = int(p.get("postponed_count", 0)) + 1
            p["ignored_count"] = 0
            if p["postponed_count"] > params["max_postpone"]:
                audit.append(_row(p, "expired", f"推迟超过 {params['max_postpone']} 次，自动结案", True, auto=True))
                closed_ids.add(pid)
            else:
                audit.append(_row(p, "postponed", reason, False))
        else:
            audit.append(_row(p, disp, reason, True))
            closed_ids.add(pid)

    # 到期却没被交代的：累计忽略次数，到上限自动结案
    for pid in sorted(due_ids - addressed):
        p = by_id[pid]
        p["ignored_count"] = int(p.get("ignored_count", 0)) + 1
        if p["ignored_count"] >= params["max_ignored"]:
            audit.append(_row(p, "unaddressed", f"到期后连续 {p['ignored_count']} 步未被交代，自动结案", True,
                              auto=True))
            closed_ids.add(pid)
    return [p for p in items if p.get("pending_id") not in closed_ids], audit, violations


def safe_apply_dispositions(*args: Any, **kwargs: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """旁路兜底：出错时 pending 原样返回、不记审计，并记一条 E0。"""
    try:
        return apply_dispositions(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — 旁路功能
        pending = args[1] if len(args) > 1 else kwargs.get("pending")
        return (list(pending or []), [],
                [_violation("E0", f"因果引擎处置出错，本步未处理回报：{type(exc).__name__}: {exc}")])


# ── 统计（从分支历史推导）────────────────────────────────────────────


def edge_stats(history: List[Any]) -> Dict[str, Dict[str, Any]]:
    """每条边的兑现统计，来自历史里的 `causal_queued`/`effect_dispositions`。

    字段：`queued`（入队次数，不含累加）、`realized/dampened/countered/postponed`、
    `expired/unaddressed`（自动结案）、`concluded`（= realized+dampened+countered）、
    `realized_rate`（`realized / concluded`，`concluded == 0` 时为 None）。
    **这是 LLM 自报的兑现，不是世界里真实兑现的度量。**
    """
    stats: Dict[str, Dict[str, Any]] = {}

    def _s(edge_id: str) -> Dict[str, Any]:
        return stats.setdefault(edge_id, {
            "queued": 0, "realized": 0, "dampened": 0, "countered": 0, "postponed": 0,
            "expired": 0, "unaddressed": 0, "concluded": 0, "realized_rate": None,
        })

    for state in history or []:
        for q in getattr(state, "causal_queued", None) or []:
            if isinstance(q, dict) and q.get("action") == "queued" and q.get("edge_id"):
                _s(str(q["edge_id"]))["queued"] += 1
        for d in getattr(state, "effect_dispositions", None) or []:
            if not isinstance(d, dict) or not d.get("edge_id"):
                continue
            key = str(d.get("disposition") or "")
            s = _s(str(d["edge_id"]))
            if key in s and key not in ("queued", "concluded", "realized_rate"):
                s[key] += 1
    for s in stats.values():
        s["concluded"] = sum(s[k] for k in _CONCLUSIVE)
        s["realized_rate"] = round(s["realized"] / s["concluded"], 3) if s["concluded"] else None
    return stats


def _display_name(settings: Optional[Dict[str, Any]], node_id: str) -> str:
    """边端点的可读名：先查因果线 `label`，再查技术节点 `name`，都没有（或 label 就是 id）用 id。"""
    for line in (settings or {}).get("causal_lines") or []:
        if isinstance(line, dict) and str(line.get("id") or "") == node_id:
            label = str(line.get("label") or line.get("name") or "").strip()
            if label:
                return label
    try:
        for node in tech_model.get_nodes(settings):
            if node.get("id") == node_id and str(node.get("name") or "").strip():
                return str(node["name"]).strip()
    except Exception:  # noqa: BLE001 — 只是取名字，取不到就用 id
        pass
    return node_id


def kb_outcomes(
    settings: Optional[Dict[str, Any]], dispositions: Optional[List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """本步的处置审计 → 该回写知识库的结论列表（见 `knowledge_base.record_edge_outcomes`）。

    只取 LLM 回报且引擎接受的 `realized`/`countered`；自动结案（`auto`）、`dampened`、
    `postponed` 不回写。开关关闭或边已不在 `declared_causal_graph` 里时返回 `[]`。
    """
    if not kb_writeback_enabled(settings):
        return []
    edges = {e["id"]: e for e in get_edges(settings)[0]}
    out: List[Dict[str, Any]] = []
    for d in dispositions or []:
        if not isinstance(d, dict) or d.get("auto") or d.get("disposition") not in ("realized", "countered"):
            continue
        edge = edges.get(str(d.get("edge_id") or ""))
        if edge is None:
            continue
        out.append({
            "edge_id": edge["id"],
            "cause": _display_name(settings, edge["from_line_id"]),
            "effect": _display_name(settings, edge["to_line_id"]),
            "mechanism": edge["mechanism"] or edge["note"],
            "outcome": d["disposition"],
        })
    return out


def summarize_open(
    settings: Optional[Dict[str, Any]], history: List[Any], step: int
) -> List[Dict[str, Any]]:
    """给界面/体检：当前所有未结案项，标注是否已到期。未开启返回 `[]`。"""
    if not is_enabled(settings):
        return []
    due_ids = {d["pending_id"] for d in due_entries((settings or {}).get("causal_pending"), history, step)}
    return [{**p, "due": p.get("pending_id") in due_ids}
            for p in ((settings or {}).get("causal_pending") or []) if isinstance(p, dict)]
