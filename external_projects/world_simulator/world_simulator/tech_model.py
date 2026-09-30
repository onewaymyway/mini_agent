"""world_simulator/tech_model.py — 技术发展模型（第二十二轮 WP1 / 阶段 P3）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md`
§2.2（技术不是一等公民）/§4 WP1。纯 Python、**不调 LLM**。

## 引擎做什么 / 不做什么（沿用计划 §1 的划分）

- 做：持有权威的技术状态（阶段/进度/前置）；按**提议–审核**裁决 LLM 提出的
  阶段迁移；把违规**透明记录**而不是静默改数；用 `elapsed_days` 让"时间"
  成为推进的依据。
- 不做：判断"语义上合不合理"；内置任何领域数值。下面所有默认参数（典型
  停留时长、投入系数……）都是**通用占位值，不是任何领域的事实**，可在
  `settings.tech_params` / `settings.tech_priors` / 节点自身覆盖；LLM 给出的
  典型时长会标 `dwell_source="llm_estimate"`、`dwell_verified=False`。

## 数据（`manifest.settings["tech_state"]`，按分支存储）

`tech_state` 是 WP0 `dynamic_state.DYNAMIC_KEYS` 的成员：随步写进
`SimState.dynamic_snapshot`，分叉即回滚到那一刻的技术状态。形如
`{"nodes": [节点, ...]}`，节点字段见 `normalize_node()`。阶段序数**沿用**
`capability_discovery` 的 6 档（`STAGES`），不新造枚举。

## 每步流程（`apply_step()`）

1. **登记**：`tech_updates` 里出现未登记的 id → 新节点。除非声明
   `preexisting: true`（记为"LLM 声明的既有技术，未经验证"），新节点阶段
   夹到最低档（T5）——防止"凭空冒出一个已经普及的技术"。
2. **时间推进（引擎做，不听 LLM 报进度）**：对**每个**已登记节点
   `progress += elapsed/典型停留 × 投入系数 × 瓶颈系数 × 软前置系数`，上限 1。
   `elapsed` 取 `elapsed_days`；没有/非法则退化为 `fallback_days_per_step`
   并在审计里标 `elapsed_source="fallback"`（界面提示"精度降级"）。
3. **裁决阶段声明**（LLM 声明"进入下一阶段"）：
   - R1 一步最多升一档：跨两档以上记 T1，最多按一档处理；
   - R4 仅当本步推进后 `progress≥1` 且硬前置满足才生效；提前迁移记 T2/T3，
     **驳回**（保持原阶段，进度只按引擎算的、不"送"进度）；
   - 倒退必须带 `regression_reason`，否则驳回并记 T4；带原因则接受，进度
     置为 `regression_progress`（可配，默认 0.5——占位值）。
   - 同一步里彼此互为前置的迁移，**用本步开始前的阶段**判断（同步不算满足）。
4. **R5 停滞**：阶段内已停留 > `stall_multiplier × 典型时长` 且进度未满 →
   状态"停滞"，写进下一步的提示，要求 LLM 用事件解释。
5. **R6 只约束不模拟**：同一 `market` 内采用率之和 ≤1（超出则把本步更新的
   节点夹到剩余额度，记 T6）；`cost_index` 非增，上升必须带 `cost_shock_reason`
   （否则夹回原值，记 T7）。不内置 S 曲线/学习率公式。
6. **R7** `perceived_stage`（"炒作 vs 现实"）只存储、展示，不做任何约束。

`requires`、`typical_dwell_days` 是**结构性参数**：LLM 只能在登记新节点时给出，
之后 LLM 想改会被忽略并记 T8（想改请在设置里手动编辑）。

## 已知边界（如实记录）

- 只有 `advance()` 路径跑这套规则；`advance_lines()`（独立推进）不跑，
  所以开了 `independent_line_advance` 的实例技术状态不随时间前进。
- 前置引用了**未登记**的技术 id：无法核验，**不阻断**，只记 info（T9）——
  阻断会让笔误造成死锁；代价是这种前置形同虚设。
- 引擎不判断"叙事说已经上市"和"引擎说仍在 developer"是否矛盾，只会在
  下一步提示里把权威状态告诉 LLM；这种错位无法被消除，只能被量化。
- `elapsed_days` 是 LLM 的估计值，只适合做量级判断。
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from world_simulator.capability_discovery import _MATURITY_STAGES as _CD_STAGES

STAGES: Tuple[str, ...] = tuple(_CD_STAGES)
"""6 档阶段序数，沿用 `capability_discovery`，不新造枚举。"""

STAGE_LABELS = {
    "lab": "实验室可行",
    "expert": "专家可用",
    "developer": "开发者可用",
    "consumer": "普通用户可用",
    "cheap_at_scale": "成本足够低/规模化",
    "infrastructure": "基础设施化",
}

_INVESTMENTS = ("low", "normal", "high")
_SEVERITIES = ("low", "medium", "high")

DEFAULT_PARAMS: Dict[str, Any] = {
    # 通用占位值，不是领域事实——见模块 docstring。
    "default_dwell_days": 365.0,
    "min_dwell_days": 1.0,
    "fallback_days_per_step": 30.0,
    "max_elapsed_days": 36500.0,  # 单步上限约 100 年，防止把年份误填成天数之外的离谱值
    "investment_factors": {"low": 0.5, "normal": 1.0, "high": 1.5},
    "bottleneck_factors": {"": 1.0, "low": 0.75, "medium": 0.5, "high": 0.25},
    "soft_penalty": 0.5,
    "stall_multiplier": 3.0,
    "regression_progress": 0.5,
}


# ── 基础工具 ─────────────────────────────────────────────────────────


def is_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    return bool((settings or {}).get("tech_model_enabled"))


def stage_index(stage: Any) -> Optional[int]:
    return STAGES.index(stage) if stage in STAGES else None


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _clamp01(value: Any, default: Optional[float] = None) -> Optional[float]:
    f = _num(value)
    if f is None:
        return default
    return max(0.0, min(1.0, f))


def get_params(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`DEFAULT_PARAMS` 叠加 `settings.tech_params`（非法值忽略）。"""
    params = copy.deepcopy(DEFAULT_PARAMS)
    override = (settings or {}).get("tech_params")
    if not isinstance(override, dict):
        return params
    for key, default in DEFAULT_PARAMS.items():
        if key not in override:
            continue
        if isinstance(default, dict):
            if isinstance(override[key], dict):
                merged = dict(default)
                for k, v in override[key].items():
                    f = _num(v)
                    if f is not None and f >= 0:
                        merged[str(k)] = f
                params[key] = merged
        else:
            f = _num(override[key])
            if f is not None and f > 0:
                params[key] = f
    return params


def _norm_requires(raw: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in raw or []:
        if isinstance(item, str):
            item = {"tech_id": item}
        if not isinstance(item, dict):
            continue
        tech_id = str(item.get("tech_id") or item.get("id") or "").strip()
        if not tech_id:
            continue
        min_stage = item.get("min_stage")
        for_stage = item.get("for_stage")
        result.append({
            "tech_id": tech_id,
            "min_stage": min_stage if min_stage in STAGES else STAGES[1],
            "mode": "soft" if item.get("mode") == "soft" else "hard",
            "for_stage": for_stage if for_stage in STAGES else STAGES[1],
        })
    return result


def _norm_dwell(raw: Any) -> Dict[str, float]:
    result: Dict[str, float] = {}
    if isinstance(raw, dict):
        for stage, days in raw.items():
            f = _num(days)
            if stage in STAGES and f is not None and f > 0:
                result[str(stage)] = f
    return result


def normalize_node(raw: Dict[str, Any], *, step: int = 0) -> Optional[Dict[str, Any]]:
    """规整一个技术节点（用户在设置里手写的种子节点、以及 LLM 新登记的节点
    共用）。缺 id 且缺 name → 返回 None。所有字段都有安全默认值。"""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    node_id = str(raw.get("id") or "").strip() or name
    if not node_id:
        return None
    stage = raw.get("stage") if raw.get("stage") in STAGES else STAGES[0]
    severity = raw.get("bottleneck_severity")
    cost = _num(raw.get("cost_index"))
    dwell_source = str(raw.get("dwell_source") or "").strip()
    if dwell_source not in ("user", "llm_estimate", "default"):
        dwell_source = "user" if raw.get("typical_dwell_days") else "default"
    return {
        "id": node_id,
        "name": name or node_id,
        "kind": str(raw.get("kind") or "").strip(),
        "stage": stage,
        "progress": _clamp01(raw.get("progress"), 0.0),
        "requires": _norm_requires(raw.get("requires")),
        "bottleneck": str(raw.get("bottleneck") or "").strip(),
        "bottleneck_severity": severity if severity in _SEVERITIES else "",
        "adoption": _clamp01(raw.get("adoption")),
        "cost_index": cost if cost is not None and cost > 0 else None,
        "market": str(raw.get("market") or "").strip(),
        "substitutes": [str(x).strip() for x in (raw.get("substitutes") or []) if str(x).strip()],
        "perceived_stage": raw.get("perceived_stage") if raw.get("perceived_stage") in STAGES else None,
        "typical_dwell_days": _norm_dwell(raw.get("typical_dwell_days")),
        "dwell_source": dwell_source,
        "dwell_verified": bool(raw.get("dwell_verified", dwell_source == "user")),
        "investment": raw.get("investment") if raw.get("investment") in _INVESTMENTS else "normal",
        "dwell_days": max(0.0, _num(raw.get("dwell_days")) or 0.0),
        "stalled_steps": max(0, int(_num(raw.get("stalled_steps")) or 0)),
        "last_transition_step": (
            int(_num(raw.get("last_transition_step")))
            if _num(raw.get("last_transition_step")) is not None else None
        ),
        "created_step": int(_num(raw.get("created_step")) if _num(raw.get("created_step")) is not None else step),
        "preexisting": bool(raw.get("preexisting", False)),
    }


def get_nodes(settings: Optional[Dict[str, Any]], *, step: int = 0) -> List[Dict[str, Any]]:
    """读出 `settings.tech_state.nodes` 并规整（重复 id 保留第一个）。"""
    state = (settings or {}).get("tech_state")
    raw_nodes = state.get("nodes") if isinstance(state, dict) else None
    seen: set = set()
    nodes: List[Dict[str, Any]] = []
    for raw in raw_nodes or []:
        node = normalize_node(raw, step=step)
        if node is None or node["id"] in seen:
            continue
        seen.add(node["id"])
        nodes.append(node)
    return nodes


def _write_nodes(settings: Dict[str, Any], nodes: List[Dict[str, Any]]) -> None:
    if nodes:
        settings["tech_state"] = {"nodes": nodes}
    else:
        settings.pop("tech_state", None)


# ── 时间 ─────────────────────────────────────────────────────────────


def resolve_elapsed(raw: Any, params: Dict[str, Any]) -> Tuple[float, str, Optional[str]]:
    """返回 `(days, source, note)`。`source` 为 `"reported"`（LLM 给了合法
    值）或 `"fallback"`（缺失/非法 → 按 `fallback_days_per_step`）。超过
    `max_elapsed_days` 时夹到上限并给 note。"""
    f = _num(raw)
    if f is None or f <= 0:
        note = None if raw in (None, "") else f"elapsed_days={raw!r} 不是正数，已退化为按步计数"
        return float(params["fallback_days_per_step"]), "fallback", note
    cap = float(params["max_elapsed_days"])
    if f > cap:
        return cap, "reported", f"elapsed_days={f:g} 超过上限，已夹到 {cap:g}"
    return f, "reported", None


def normalize_reported_elapsed(raw: Any) -> Optional[float]:
    """`SimState.elapsed_days` 落盘用：合法正数原样返回，否则 None。"""
    f = _num(raw)
    return f if f is not None and f > 0 else None


# ── 状态判定 ─────────────────────────────────────────────────────────


def _dwell_for(node: Dict[str, Any], settings: Optional[Dict[str, Any]], params: Dict[str, Any]) -> float:
    stage = node["stage"]
    if stage in node["typical_dwell_days"]:
        days = node["typical_dwell_days"][stage]
    else:
        priors = (settings or {}).get("tech_priors")
        days = None
        if isinstance(priors, dict):
            for key in (node["kind"], "default"):
                table = priors.get(key) if key else None
                if isinstance(table, dict) and _num(table.get(stage)) and _num(table.get(stage)) > 0:
                    days = _num(table.get(stage))
                    break
        if days is None:
            days = params["default_dwell_days"]
    return max(float(days), float(params["min_dwell_days"]))


def _unmet_requires(
    node: Dict[str, Any],
    stages_by_id: Dict[str, str],
    *,
    target_stage: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """返回 `(未满足的硬前置, 未满足的软前置, 无法核验的前置 id)`。
    `target_stage` 给定时，只看 `for_stage <= target_stage` 的前置。"""
    hard: List[Dict[str, Any]] = []
    soft: List[Dict[str, Any]] = []
    unresolved: List[str] = []
    target_idx = stage_index(target_stage) if target_stage else None
    for req in node["requires"]:
        if target_idx is not None and (stage_index(req["for_stage"]) or 0) > target_idx:
            continue
        have = stages_by_id.get(req["tech_id"])
        if have is None:
            unresolved.append(req["tech_id"])
            continue
        if (stage_index(have) or 0) < (stage_index(req["min_stage"]) or 0):
            item = {**req, "current_stage": have}
            (soft if req["mode"] == "soft" else hard).append(item)
    return hard, soft, unresolved


def node_status(
    node: Dict[str, Any],
    nodes: List[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """节点当前判定：`status` ∈ ready/blocked/stalled/progressing/mature，
    以及原因。界面与提示词共用。"""
    params = get_params(settings)
    stages = {n["id"]: n["stage"] for n in nodes}
    idx = stage_index(node["stage"]) or 0
    at_top = idx >= len(STAGES) - 1
    target = None if at_top else STAGES[idx + 1]
    hard, soft, unresolved = _unmet_requires(node, stages, target_stage=target)
    typical = _dwell_for(node, settings, params)
    if at_top:
        status = "mature" if node["progress"] >= 1 else "progressing"
    elif node["progress"] >= 1:
        status = "blocked" if hard else "ready"
    elif node["dwell_days"] > params["stall_multiplier"] * typical:
        status = "stalled"
    else:
        status = "progressing"
    return {
        "status": status,
        "target_stage": target,
        "unmet_hard": hard,
        "unmet_soft": soft,
        "unresolved_requires": unresolved,
        "typical_dwell_days": typical,
    }


_STATUS_TEXT = {
    "ready": "已达到迁移条件（可在叙事中体现为进入下一阶段）",
    "blocked": "进度已满但硬前置未满足，不能进入下一阶段",
    "stalled": "停滞（停留过久）——请用具体事件解释（资金/监管/技术瓶颈等）",
    "progressing": "推进中",
    "mature": "已处于最高阶段",
}


def _fmt_req(req: Dict[str, Any]) -> str:
    return (
        f"{req['tech_id']} 需达 {STAGE_LABELS.get(req['min_stage'], req['min_stage'])}"
        f"（现为 {STAGE_LABELS.get(req['current_stage'], req['current_stage'])}）"
    )


# ── 提示词 ───────────────────────────────────────────────────────────

_PROTOCOL = (
    "【技术发展模型已开启】引擎持有下列技术的权威状态；你负责写故事，引擎负责裁决\n"
    "阶段迁移。请在输出里：\n"
    "1) 给出 `elapsed_days`：这一步在模拟世界里大致跨越了多少天（正数，量级估计即可；\n"
    "   不给会退化为按步计数，精度降低）。\n"
    "2) 有技术相关的变化时给出 `tech_updates`（数组，没有就不输出）。每项可含：\n"
    "   `id`（必填，已登记的沿用同一 id）、`name`/`kind`（新技术登记时给）、\n"
    "   `stage`（你认为这一步结束时所处阶段，取值 lab/expert/developer/consumer/\n"
    "   cheap_at_scale/infrastructure）、`investment`（low/normal/high，附 `investment_reason`；\n"
    "   没有理由按 normal）、`bottleneck` 与 `bottleneck_severity`（low/medium/high；清除\n"
    "   瓶颈须给 `bottleneck_resolved_reason`）、`requires`（仅新登记时：前置技术数组，\n"
    "   每项含 `tech_id`/`min_stage`/`mode` 为 hard 或 soft）、`typical_dwell_days`（仅新登记时：\n"
    "   各阶段典型停留天数的估计，会被标为\"未验证的 LLM 估计\"）、`adoption`（0 到 1）、\n"
    "   `market`、`cost_index`（成本上升须给 `cost_shock_reason`）、`perceived_stage`（公众/\n"
    "   市场以为的阶段，炒作与现实的落差）、`regression_reason`（倒退必填）、\n"
    "   `preexisting`（仅当这是世界里本来就存在的技术时才为 true）。\n"
    "3) 规则（违反会被引擎驳回并记录在时间线上，不会静默修改）：一步最多升一档；\n"
    "   进入下一阶段需要该技术在引擎里的进度已满且硬前置已满足——**进度由引擎按时间推算，\n"
    "   你不能自己宣布**；新登记的技术只能从 lab 开始（除非 preexisting）；倒退必须说明原因。"
)


def build_hint(settings: Optional[Dict[str, Any]]) -> str:
    """喂给 `advance_step`/`world_evolve` 的 `{tech_state_hint}`。未开启返回
    空字符串（此时 prompt 与开启前逐字节等价）。"""
    if not is_enabled(settings):
        return ""
    nodes = get_nodes(settings)
    lines = [_PROTOCOL]
    if not nodes:
        lines.append(
            "当前还没有登记任何技术节点：如果这一步出现了值得追踪的技术，请用 `tech_updates` 登记。"
        )
        return "\n".join(lines)
    lines.append("当前已登记的技术（引擎权威状态）：")
    for node in nodes:
        st = node_status(node, nodes, settings)
        piece = (
            f"- [{node['id']}] {node['name']}：阶段 {node['stage']}"
            f"（{STAGE_LABELS.get(node['stage'], node['stage'])}），阶段内进度 {node['progress']:.0%}，"
            f"已停留约 {node['dwell_days']:.0f} 天（典型约 {st['typical_dwell_days']:.0f} 天）；"
            f"状态：{_STATUS_TEXT[st['status']]}"
        )
        if st["unmet_hard"]:
            piece += "；未满足的硬前置：" + "、".join(_fmt_req(r) for r in st["unmet_hard"])
        if st["unmet_soft"]:
            piece += "；未满足的软前置（拖慢推进）：" + "、".join(_fmt_req(r) for r in st["unmet_soft"])
        if node["bottleneck"]:
            piece += f"；瓶颈：{node['bottleneck']}"
        if node["perceived_stage"] and node["perceived_stage"] != node["stage"]:
            piece += f"；公众以为处于 {node['perceived_stage']}（与实际不一致）"
        lines.append(piece)
    return "\n".join(lines)


def safe_build_hint(settings: Optional[Dict[str, Any]]) -> str:
    """`build_hint()` 的兜底版：它在调用 LLM *之前*执行，出错不能拖垮整步
    推进——出错时退化为空字符串（等同没开启，本步 LLM 不会被告知技术状态，
    但引擎侧状态不受影响）。"""
    try:
        return build_hint(settings)
    except Exception:  # noqa: BLE001 — 旁路功能
        return ""


# ── 每步裁决 ─────────────────────────────────────────────────────────


def _violation(code: str, tech_id: str, message: str, severity: str = "warn", **detail: Any) -> Dict[str, Any]:
    return {"code": code, "severity": severity, "tech_id": tech_id, "message": message, "detail": detail}


def apply_step(
    settings: Dict[str, Any],
    proposals: Any,
    *,
    step: int,
    elapsed_days_raw: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """执行一步技术裁决，就地更新 `settings["tech_state"]`，返回
    `(审计条目, 违规记录)`。未开启时什么都不做、返回两个空列表。

    审计条目 `action`：`time`（本步时间来源，恒有一条）/`registered`/
    `transition`/`regression`/`held`（提出了迁移但被驳回）。
    """
    if not is_enabled(settings):
        return [], []
    params = get_params(settings)
    nodes = get_nodes(settings, step=step)
    by_id = {n["id"]: n for n in nodes}
    audit: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []

    days, source, time_note = resolve_elapsed(elapsed_days_raw, params)
    time_entry: Dict[str, Any] = {"action": "time", "elapsed_days": days, "elapsed_source": source}
    if time_note:
        time_entry["note"] = time_note
    audit.append(time_entry)

    # 提议按 id 归并（同一步同 id 多条：后者覆盖前者的同名字段）
    prop_by_id: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for raw in proposals if isinstance(proposals, list) else []:
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("id") or raw.get("tech_id") or "").strip() or str(raw.get("name") or "").strip()
        if not tid:
            violations.append(_violation("T9", "", "tech_updates 里有一项既没有 id 也没有 name，已忽略", "info"))
            continue
        if tid not in prop_by_id:
            order.append(tid)
            prop_by_id[tid] = {}
        prop_by_id[tid].update(raw)

    stages_before = {n["id"]: n["stage"] for n in nodes}
    new_ids: set = set()

    # 1) 登记新节点
    for tid in order:
        if tid in by_id:
            continue
        raw = prop_by_id[tid]
        preexisting = bool(raw.get("preexisting"))
        claimed = raw.get("stage") if raw.get("stage") in STAGES else STAGES[0]
        seed = dict(raw)
        seed["id"] = tid
        seed["progress"] = 0.0
        seed["dwell_days"] = 0.0
        seed["preexisting"] = preexisting
        if "typical_dwell_days" in raw and _norm_dwell(raw.get("typical_dwell_days")):
            seed["dwell_source"] = "llm_estimate"
            seed["dwell_verified"] = False
        else:
            seed.pop("typical_dwell_days", None)
            seed["dwell_source"] = "default"
        if not preexisting and claimed != STAGES[0]:
            seed["stage"] = STAGES[0]
            violations.append(_violation(
                "T5", tid,
                f"新登记的技术「{raw.get('name') or tid}」声明阶段为 {claimed}，但新技术只能从 "
                f"{STAGES[0]} 开始（除非声明 preexisting），已按 {STAGES[0]} 登记",
                claimed_stage=claimed,
            ))
        node = normalize_node(seed, step=step)
        if node is None:
            continue
        node["created_step"] = step
        node["last_transition_step"] = step
        # 登记者声明的 investment 不在登记当步生效（没有时间可推进）
        nodes.append(node)
        by_id[node["id"]] = node
        new_ids.add(node["id"])
        entry = {
            "action": "registered", "tech_id": node["id"], "name": node["name"],
            "stage_after": node["stage"],
        }
        if preexisting:
            entry["note"] = "LLM 声明为世界里本来就存在的技术，未经验证"
        audit.append(entry)
        if node["dwell_source"] == "llm_estimate":
            audit[-1]["dwell_source"] = "llm_estimate（未验证）"
        stages_before.setdefault(node["id"], node["stage"])
        prop_by_id[tid] = {}  # 登记步不再当作"已有节点的更新"处理

    # 2) 已有节点：结构参数忽略 + 元数据 + 时间推进
    for node in nodes:
        if node["id"] in new_ids:
            continue  # 本步刚登记：没有时间可推进
        raw = prop_by_id.get(node["id"], {})
        tid = node["id"]

        for immutable in ("requires", "typical_dwell_days"):
            if immutable in raw and raw[immutable]:
                violations.append(_violation(
                    "T8", tid, f"「{node['name']}」的 {immutable} 只能在登记时给出，本步的修改已忽略"
                    "（需要改请在设置里手动编辑）", "info", field=immutable,
                ))

        # 投入是"本步"申报：没申报则本步按 normal，不沿用上一步的高投入
        inv_now = "normal"
        if raw.get("investment") in _INVESTMENTS:
            inv = raw["investment"]
            if inv != "normal" and not str(raw.get("investment_reason") or "").strip():
                violations.append(_violation(
                    "T10", tid, f"「{node['name']}」申报投入 {inv} 但没有给出理由，按 normal 处理", "info",
                    declared=inv,
                ))
                inv = "normal"
            node["investment"] = inv
            inv_now = inv

        if "bottleneck" in raw or "bottleneck_severity" in raw:
            new_text = str(raw.get("bottleneck") if "bottleneck" in raw else node["bottleneck"]).strip()
            new_sev = raw.get("bottleneck_severity") if raw.get("bottleneck_severity") in _SEVERITIES else node["bottleneck_severity"]
            if node["bottleneck"] and not new_text:
                if str(raw.get("bottleneck_resolved_reason") or "").strip():
                    node["bottleneck"], node["bottleneck_severity"] = "", ""
                else:
                    violations.append(_violation(
                        "T11", tid, f"「{node['name']}」的瓶颈「{node['bottleneck']}」被清除但没有给出 "
                        "bottleneck_resolved_reason，已保留", "info",
                    ))
            else:
                node["bottleneck"] = new_text
                node["bottleneck_severity"] = new_sev if new_text else ""
                if new_text and not node["bottleneck_severity"]:
                    node["bottleneck_severity"] = "medium"

        if raw.get("perceived_stage") in STAGES:
            node["perceived_stage"] = raw["perceived_stage"]

        idx_now = stage_index(node["stage"]) or 0
        next_stage = STAGES[idx_now + 1] if idx_now < len(STAGES) - 1 else None
        hard, soft, unresolved = _unmet_requires(node, stages_before, target_stage=next_stage)
        for uid in unresolved:
            violations.append(_violation(
                "T9", tid, f"「{node['name']}」的前置 {uid} 没有登记为技术节点，无法核验（不阻断）", "info",
                requires=uid,
            ))
        factor_soft = params["soft_penalty"] ** len(soft)
        factor_inv = params["investment_factors"].get(inv_now, 1.0)
        factor_bn = params["bottleneck_factors"].get(node["bottleneck_severity"] if node["bottleneck"] else "", 1.0)
        typical = _dwell_for(node, settings, params)
        delta = days / typical * factor_inv * factor_bn * factor_soft
        node["progress"] = min(1.0, node["progress"] + delta)
        node["dwell_days"] += days

    # 3) 裁决阶段声明（用本步开始前的阶段判断前置）
    for tid in order:
        node = by_id.get(tid)
        raw = prop_by_id.get(tid) or {}
        if node is None or raw.get("stage") not in STAGES:
            continue
        claimed = raw["stage"]
        cur = stage_index(node["stage"]) or 0
        want = stage_index(claimed) or 0
        diff = want - cur
        if diff == 0:
            continue
        before_stage, before_progress = node["stage"], node["progress"]
        if diff < 0:
            if str(raw.get("regression_reason") or "").strip():
                node["stage"] = claimed
                node["progress"] = float(params["regression_progress"])
                node["dwell_days"] = 0.0
                node["stalled_steps"] = 0
                node["last_transition_step"] = step
                audit.append({
                    "action": "regression", "tech_id": tid, "name": node["name"],
                    "stage_before": before_stage, "stage_after": claimed,
                    "note": str(raw["regression_reason"]).strip(),
                })
            else:
                violations.append(_violation(
                    "T4", tid, f"「{node['name']}」声明从 {before_stage} 倒退到 {claimed} 但没有 "
                    "regression_reason，已驳回", from_stage=before_stage, claimed=claimed,
                ))
                audit.append({"action": "held", "tech_id": tid, "name": node["name"],
                              "stage_before": before_stage, "stage_after": before_stage,
                              "note": "倒退缺少原因，驳回"})
            continue
        # diff > 0：想升级
        if diff > 1:
            violations.append(_violation(
                "T1", tid, f"「{node['name']}」声明从 {before_stage} 直接跳到 {claimed}（一步最多升一档）",
                from_stage=before_stage, claimed=claimed,
            ))
        target = STAGES[cur + 1]
        hard, _soft, _un = _unmet_requires(node, stages_before, target_stage=target)
        reasons: List[str] = []
        if node["progress"] < 1.0:
            reasons.append("progress")
            violations.append(_violation(
                "T2", tid, f"「{node['name']}」声明进入 {target}，但引擎推算的阶段内进度只有 "
                f"{node['progress']:.0%}（未满），已驳回", progress=round(node["progress"], 3), target=target,
            ))
        if hard:
            reasons.append("requires")
            violations.append(_violation(
                "T3", tid, f"「{node['name']}」声明进入 {target}，但硬前置未满足："
                + "、".join(_fmt_req(r) for r in hard) + "，已驳回",
                unmet=[r["tech_id"] for r in hard], target=target,
            ))
        if reasons:
            audit.append({
                "action": "held", "tech_id": tid, "name": node["name"],
                "stage_before": before_stage, "stage_after": before_stage,
                "progress_after": round(node["progress"], 3), "note": "迁移被驳回：" + "、".join(reasons),
            })
            continue
        node["stage"] = target
        node["progress"] = 0.0
        node["dwell_days"] = 0.0
        node["stalled_steps"] = 0
        node["last_transition_step"] = step
        audit.append({
            "action": "transition", "tech_id": tid, "name": node["name"],
            "stage_before": before_stage, "stage_after": target,
            "progress_before": round(before_progress, 3),
        })

    # 4) R6：采用率/成本只约束
    updated_ids = set(order)
    for tid in order:
        node = by_id.get(tid)
        raw = prop_by_id.get(tid) or {}
        if node is None:
            continue
        if "cost_index" in raw:
            new_cost = _num(raw.get("cost_index"))
            if new_cost is not None and new_cost > 0:
                if node["cost_index"] is not None and new_cost > node["cost_index"] and not str(raw.get("cost_shock_reason") or "").strip():
                    violations.append(_violation(
                        "T7", tid, f"「{node['name']}」的成本指数从 {node['cost_index']:g} 升到 {new_cost:g}，"
                        "但没有 cost_shock_reason，已保留原值", old=node["cost_index"], claimed=new_cost,
                    ))
                else:
                    node["cost_index"] = new_cost
        if "adoption" in raw:
            new_adopt = _clamp01(raw.get("adoption"))
            if new_adopt is not None:
                node["adoption"] = new_adopt
    for market in sorted({n["market"] for n in nodes if n["market"]}):
        members = [n for n in nodes if n["market"] == market and n["adoption"] is not None]
        total = sum(n["adoption"] for n in members)
        if total <= 1.0 + 1e-9:
            continue
        fixed = sum(n["adoption"] for n in members if n["id"] not in updated_ids)
        room = max(0.0, 1.0 - fixed)
        changing = [n for n in members if n["id"] in updated_ids]
        changing_total = sum(n["adoption"] for n in changing)
        if not changing or changing_total <= 0:
            continue
        scale = min(1.0, room / changing_total)
        for n in changing:
            old = n["adoption"]
            n["adoption"] = round(old * scale, 6)
            violations.append(_violation(
                "T6", n["id"], f"市场「{market}」内采用率之和 {total:.2f} 超过 1，"
                f"「{n['name']}」的采用率由 {old:.2f} 夹到 {n['adoption']:.2f}",
                market=market, old=old, new=n["adoption"],
            ))

    # 5) 停滞计数
    for node in nodes:
        st = node_status(node, nodes, settings)
        if st["status"] in ("stalled", "blocked"):
            node["stalled_steps"] += 1

    _write_nodes(settings, nodes)
    return audit, violations


def safe_apply_step(
    settings: Dict[str, Any],
    proposals: Any,
    *,
    step: int,
    elapsed_days_raw: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """`apply_step()` 的兜底版，给 `advance()` 用：任何异常都不连累本次推进，
    返回空审计 + 一条 info 级 `T0` 记录说明技术模型本步被跳过。`apply_step()`
    在最后一行才写回 `settings`，所以异常时 `settings["tech_state"]` 保持原样。"""
    try:
        return apply_step(settings, proposals, step=step, elapsed_days_raw=elapsed_days_raw)
    except Exception as exc:  # noqa: BLE001 — 旁路功能，绝不让它中断推进
        return [], [_violation("T0", "", f"技术模型本步出错，已跳过（状态未改动）：{exc}", "info")]


# ── 展示用 ───────────────────────────────────────────────────────────


def summarize(settings: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """给界面：每个节点 + 当前判定。未开启或没有节点返回 []。"""
    nodes = get_nodes(settings)
    return [{"node": n, **node_status(n, nodes, settings)} for n in nodes]
