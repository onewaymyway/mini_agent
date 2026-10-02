"""world_simulator/tree_effects.py — 树影响世界 P5d（第二十二轮 WP3 的 3d）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §4 WP3 的 3d。
纯 Python、**不调 LLM**、**不改任何变量数值**。

## 为什么需要它

此前未来树只是展示：分支被标 `active` 之后，世界里什么都不会发生（P5c 只管\"能不能 active\"）。
本模块让分支可以**声明**\"我一旦激活，会对世界的哪里施加什么压力\"，并把它转成 P5a 的待兑现
因果队列条目——树第一次真正推动世界，而不只是被展示。

## 做什么

分支可带可选字段 `effects_if_active`（列表，每项形如因果边：`to_line_id`〔必填，目标因果线 id 或技术
节点 id〕、`mechanism`/`sign`/`strength`/`delay_days`/`delay_steps`/`note`/`condition`）。分支在**本步
新变为 `active`**（上一步不是 active）且接地裁决（P5c）之后仍是 `active` 时，每条声明入队成一个待兑现项：

- 延迟到期后，和声明的因果边同一条路径：作为\"到期因果压力\"注入提示词，LLM 用
  `effect_dispositions` 回报 `realized/dampened/postponed/countered + 原因`，引擎校验、结案、统计；
- 待兑现项的 id 形如 `tree:{线id}/{分支id}->{目标}@{步}`，`edge_id` 是 `tree:{线id}/{分支id}->{目标}`；
  同一条（分支, 目标）同一时刻最多一个未结案项，再次激活只累加 `retrigger_count`。

## 开关（`manifest.settings`）

- `tree_effects_enabled`（默认 False）：**必须同时开启 `causal_engine_enabled` 才生效**——待兑现队列、
  处置校验、统计都是因果引擎的；单独开本开关而因果引擎关闭时，什么都不做（界面会提示）。
- 关闭时：`build_hint()` 返回空串、不入队，行为与之前**逐字节一致**。

## 判定口径

- 只看每条线 `future_tree.branches` 的**顶层**分支（与 P5c 一致）。
- \"本步新变为 active\"：推进前的状态索引里该分支不是 active（含本步新增就是 active 的分支）、
  接地之后是 active。被 P5c 降级回 `emerging` 的不入队；分支此后一直保持 active 不会重复入队；
  离开 active 再回来才会再次入队（同一条未结案则累加 `retrigger_count`）。
- 每条声明可带 `condition`（同外生事件的结构化谓词），入队时按**本步结束后**的变量求值；不满足不入队。
- 入队不看\"分支是被 LLM 标的、引擎自动置的、还是被印证的\"——只看结果状态。
- 队列上限与因果引擎共用（`max_pending`），超限记 E6、不入队。

## 违规码（`SimState.causal_violations`，沿用因果引擎的口径）

| 码 | 含义 |
|---|---|
| E5 | 分支的某条 `effects_if_active` 无法使用（缺 `to_line_id`，该条被丢弃），或其中的 `sign`/`strength` 取值不认识（该字段按未声明处理，条目仍入队）；原因写在 message 里 |
| E6 | 待兑现队列已满（与边入队共用） |
| E7 | 目标既不是已登记的因果线也不是技术节点（疑似笔误）——**仍然入队**（不阻断，与技术模型 T9 同一取舍），只留痕 |
| E0 | 本模块内部出错，本步不入队 |

## 已知边界（如实记录）

- 引擎**无法验证**分支声明的影响是否合理，也无法验证 `realized` 是否真的写进了状态（统计是 LLM 自报）。
- 声明由 LLM 在新增分支时可选填，或用户在设置里手填；LLM 填的是**猜测**，不是事实。
- 树边的兑现统计（`edge_stats`）会按 `tree:` 边 id 累计，**不回写 `knowledge_base`**
  （`causal_engine.kb_outcomes` 只认 `declared_causal_graph` 里的边；树声明是实例内的假设）。
- 只看顶层分支；`advance_lines()` 独立推进路径不入队。
- 不追溯：开启前已经 active 的分支不会补入队；分支状态只靠\"本步新变为 active\"触发。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from world_simulator import causal_engine, event_sampler, tech_model, tree_grounding

TREE_EDGE_PREFIX = "tree:"
TRIGGER_REASON = "tree_effect"


def is_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    """子开关 + 因果引擎总开关都开才生效。"""
    s = settings or {}
    return bool(s.get("tree_effects_enabled")) and causal_engine.is_enabled(s)


def is_requested(settings: Optional[Dict[str, Any]]) -> bool:
    """用户勾选了本开关（不管因果引擎是否开启）——界面据此提示\"需要先开因果引擎\"。"""
    return bool((settings or {}).get("tree_effects_enabled"))


def edge_id_for(line_id: str, branch_id: str, target: str) -> str:
    return f"{TREE_EDGE_PREFIX}{line_id}/{branch_id}->{target}"


def is_tree_edge(edge_id: Any) -> bool:
    return str(edge_id or "").startswith(TREE_EDGE_PREFIX)


# ── 规整 ────────────────────────────────────────────────────────────


def normalize_effect(raw: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """语义规整一条 `effects_if_active` 声明：`(声明, 被忽略/降级的说明)`。

    `causal_tree` 已做过形状清理；这里再把 `sign` 别名归一、`strength` 限定取值，认不出的取值
    退化为 None（未声明，不猜默认档），与 `causal_engine.normalize_edge` 同口径。
    """
    if not isinstance(raw, dict):
        return None, "不是对象"
    target = str(raw.get("to_line_id") or "").strip()
    if not target:
        return None, "缺少 to_line_id"
    notes: List[str] = []
    sign_raw = str(raw.get("sign") or "").strip().lower()
    sign = causal_engine._SIGN_ALIASES.get(sign_raw)
    if sign_raw and sign is None:
        notes.append(f"sign「{sign_raw}」不认识，已当作未声明")
    strength = str(raw.get("strength") or "").strip().lower()
    if strength and strength not in causal_engine._STRENGTHS:
        notes.append(f"strength「{strength}」不认识，已当作未声明")
        strength = ""
    delay_days = causal_engine._num(raw.get("delay_days"))
    delay_steps = causal_engine._num(raw.get("delay_steps"))
    condition = raw.get("condition")
    return {
        "to_line_id": target,
        "mechanism": str(raw.get("mechanism") or "").strip(),
        "sign": sign,
        "strength": strength or None,
        "note": str(raw.get("note") or "").strip(),
        "delay_days": delay_days if delay_days is not None and delay_days >= 0 else None,
        "delay_steps": max(0, int(delay_steps)) if delay_steps is not None else 0,
        "condition": condition if isinstance(condition, (dict, list)) else None,
    }, ("；".join(notes) if notes else None)


def declared_effects(causal_lines: Any) -> List[Dict[str, Any]]:
    """所有顶层分支声明的影响（不论当前状态）：
    `[{line_id, branch_id, status, effect(规整后)}]`。界面/体检用。"""
    out: List[Dict[str, Any]] = []
    for line in causal_lines or []:
        lid = tree_grounding._line_id(line)
        if not lid:
            continue
        for branch in tree_grounding._top_branches(line):
            for raw in branch.get("effects_if_active") or []:
                eff, _note = normalize_effect(raw)
                if eff is not None:
                    out.append({"line_id": lid, "branch_id": tree_grounding._bid(branch),
                                "status": tree_grounding._status(branch), "effect": eff})
    return out


def has_day_delays(settings: Optional[Dict[str, Any]]) -> bool:
    """开启且至少有一条分支声明了 `delay_days`：此时每步都需要 `elapsed_days`。"""
    if not is_enabled(settings):
        return False
    return any(d["effect"]["delay_days"] is not None for d in declared_effects((settings or {}).get("causal_lines")))


# ── 入队 ────────────────────────────────────────────────────────────


def _violation(code: str, message: str, **detail: Any) -> Dict[str, Any]:
    return {"code": code, "severity": "warn", "message": message, "detail": detail}


def _known_targets(settings: Optional[Dict[str, Any]]) -> set:
    known = {tree_grounding._line_id(x) for x in (settings or {}).get("causal_lines") or []}
    try:
        known |= {str(n.get("id") or "") for n in tech_model.get_nodes(settings)}
    except Exception:  # noqa: BLE001 — 只是核对名字，取不到就不报 E7
        return set()
    known.discard("")
    return known


def newly_active(
    causal_lines: Any, status_before: Dict[Tuple[str, str], str]
) -> List[Tuple[str, Dict[str, Any]]]:
    """接地之后仍是 active、且推进前不是 active 的顶层分支（含本步新增就是 active 的）。"""
    out: List[Tuple[str, Dict[str, Any]]] = []
    for line in causal_lines or []:
        lid = tree_grounding._line_id(line)
        if not lid:
            continue
        for branch in tree_grounding._top_branches(line):
            key = (lid, tree_grounding._bid(branch))
            if tree_grounding._status(branch) == "active" and status_before.get(key) != "active":
                out.append((lid, branch))
    return out


def queue_tree_effects(
    settings: Optional[Dict[str, Any]],
    pending: Optional[List[Dict[str, Any]]],
    next_state: Any,
    status_before: Optional[Dict[Tuple[str, str], str]],
    causal_lines: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """本步新激活分支声明的影响入队。返回 `(新的 pending, 入队/累加记录, 违规)`。

    必须在 P5c 接地之后、`causal_engine.queue_effects` 之后调用（队列上限共用），
    `causal_lines` 是接地后的树。未开启 / `status_before` 缺失时原样返回。
    """
    result = [dict(p) for p in (pending or []) if isinstance(p, dict)]
    queued: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []
    if not is_enabled(settings) or status_before is None:
        return result, queued, violations
    params = causal_engine.get_params(settings)
    step = int(next_state.step)
    known = _known_targets(settings)
    for lid, branch in newly_active(causal_lines, status_before):
        bid = tree_grounding._bid(branch)
        for raw in branch.get("effects_if_active") or []:
            eff, note = normalize_effect(raw)
            if eff is None:
                violations.append(_violation(
                    "E5", f"分支「{bid}」（线 {lid}）的一条 effects_if_active 无法使用：{note}，已忽略",
                    line_id=lid, branch_id=bid))
                continue
            if note:
                violations.append(_violation(
                    "E5", f"分支「{bid}」（线 {lid}）→「{eff['to_line_id']}」：{note}",
                    line_id=lid, branch_id=bid, target=eff["to_line_id"]))
            ok, _why = event_sampler.evaluate_condition(eff["condition"], next_state.vars, settings)
            if not ok:
                continue
            edge_id = edge_id_for(lid, bid, eff["to_line_id"])
            if known and eff["to_line_id"] not in known:
                violations.append(_violation(
                    "E7", f"分支「{bid}」（线 {lid}）声明的目标「{eff['to_line_id']}」既不是已登记的因果线"
                          "也不是技术节点（疑似笔误），仍入队",
                    line_id=lid, branch_id=bid, target=eff["to_line_id"]))
            open_entry = next((p for p in result if p.get("edge_id") == edge_id), None)
            if open_entry is not None:
                open_entry["retrigger_count"] = int(open_entry.get("retrigger_count", 0)) + 1
                queued.append({"pending_id": open_entry["pending_id"], "edge_id": edge_id,
                               "action": "retriggered", "trigger_reason": TRIGGER_REASON})
                continue
            if len(result) >= params["max_pending"]:
                violations.append(_violation(
                    "E6", f"待兑现队列已满（{params['max_pending']}），分支「{bid}」→「{eff['to_line_id']}」本步未入队",
                    edge_id=edge_id))
                continue
            entry = {
                "pending_id": f"{edge_id}@{step}",
                "edge_id": edge_id,
                "source": "tree",
                "branch_id": bid,
                "from_line_id": lid,
                "to_line_id": eff["to_line_id"],
                "mechanism": eff["mechanism"],
                "sign": eff["sign"],
                "strength": eff["strength"],
                "note": eff["note"],
                "triggered_at_step": step,
                "trigger_reason": TRIGGER_REASON,
                "delay_days": eff["delay_days"],
                "delay_steps": eff["delay_steps"],
                "retrigger_count": 0,
                "postponed_count": 0,
                "ignored_count": 0,
            }
            result.append(entry)
            queued.append({"pending_id": entry["pending_id"], "edge_id": edge_id, "action": "queued",
                           "trigger_reason": TRIGGER_REASON})
    return result, queued, violations


def safe_queue_tree_effects(*args: Any, **kwargs: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """旁路兜底：出错时 pending 原样返回、不入队，并记一条 E0（不拖垮整步推进）。"""
    try:
        return queue_tree_effects(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — 旁路功能
        pending = args[1] if len(args) > 1 else kwargs.get("pending")
        return (list(pending or []), [],
                [_violation("E0", f"树影响世界入队出错，本步未入队：{type(exc).__name__}: {exc}")])


# ── 提示词 ───────────────────────────────────────────────────────────


def build_hint(settings: Optional[Dict[str, Any]]) -> str:
    """喂给 `advance_step`/`world_evolve` 的 `{tree_effects_hint}`。未开启（含因果引擎没开）返回空串。

    只说明协议，不重复列待兑现项——到期项已经由因果引擎的 `{causal_pending_hint}` 列出。
    """
    if not is_enabled(settings):
        return ""
    return (
        "树影响世界已开启：新增分支时可选填 `effects_if_active`（数组），声明\"这个分支一旦变为 active，"
        "会对世界的哪里施加什么压力\"，每项形如 "
        '`{"to_line_id": "目标因果线或技术节点 id", "mechanism": "一句话机制", "sign": "positive|negative|mixed", '
        '"strength": "high|medium|low", "delay_days": 90}`'
        "（`delay_days` 是模拟世界里的天数，可选；`condition` 可选，写法同 `trigger_condition`）。"
        "分支在某一步**新变为 active** 之后，引擎会把这些声明排进待兑现队列，延迟期到了会在"
        "「因果引擎」那一项里列出来，要你用 `effect_dispositions` 交代效果。"
        "这是你对\"该分支发生后会怎样\"的可核验假设：没把握就不要填，也不要为了凑数编目标 id。"
    )


def safe_build_hint(*args: Any, **kwargs: Any) -> str:
    try:
        return build_hint(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return ""


# ── 汇总（界面/体检）────────────────────────────────────────────────


def summarize_open(settings: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """当前未结案项里来自树的那部分。未开启返回 `[]`。"""
    if not is_enabled(settings):
        return []
    return [p for p in ((settings or {}).get("causal_pending") or [])
            if isinstance(p, dict) and (p.get("source") == "tree" or is_tree_edge(p.get("edge_id")))]
