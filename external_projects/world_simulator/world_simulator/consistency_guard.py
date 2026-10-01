"""world_simulator/consistency_guard.py — 一致性守卫 + 真实性体检（第二十二轮 WP4）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md`
§2.5 / §4 WP4。

## 这个模块是什么、不是什么

纯 Python、不调用 LLM、**不改任何数值/状态、不阻断推进、不触发修复**。
它只做两件事：

1. **逐步守卫**（`safe_check_step()`）：`advance()` 落盘前，对这一步做
   结构性检查，结果写进 `SimState.consistency_warnings`（展示型）。
2. **实例级体检**（`analyze_history()`）：读一条分支的完整历史，重新
   计算全部检查并给出统计，用来给**已有实例**出基线数字。体检不依赖
   逐步守卫是否开过——C1/C5/C6/C7/C8 直接从历史算，C2/C3/C4 只能
   从 WP0 之后写下的 `dynamic_snapshot` 链里算（旧步没有快照，如实
   标注"覆盖不到"，不猜）。

它度量的是**结构上有没有明显作弊**（跳级、复活、前置违规、波动异常……），
**不是语义上合不合理**。界面与文档必须这样标注，不得当作"真实性评分"。

## 八项检查

| 码 | 含义 | 逐步告警 | 体检统计 |
|---|---|---|---|
| C1 | 同一能力两次出现之间，`maturity_stage` 序数差 >1（跳级）或 <0（倒退；带 `regression_reason` 不告警） | ✓ | ✓ |
| C2 | 已终结（resolved/expired/invalidated）的分支再次 active/emerging | ✓ | ✓* |
| C3 | 分支变为 active 时，`prerequisites` 里能解析到分支 id 的前置未 resolved | ✓ | ✓* |
| C4 | 不在允许迁移表内的其它状态跳变 | ✓ | ✓* |
| C5 | 事件密度：major_decision/structural_change/新能力 的步占比与最长连续段 | 只统计 | ✓ |
| C6 | 数值叶子字段本步变化量相对其历史变化量标准差的 z 值超阈 | ✓ | ✓ |
| C7 | 跨线因果链里，未在 `declared_causal_graph` 声明的边的占比 | 只统计 | ✓ |
| C8 | 已终结分支按创建时 `likelihood` 分组的命中率 | — | ✓ |

`*`：依赖 `dynamic_snapshot` 链（WP0），只覆盖 WP0 之后写入的步。

## 阈值说明（都是可覆盖的默认值，不是"经数据校准的真理"）

- C6 `z_threshold=4.0`、`min_deltas=5`：历史变化量样本不足 5 个时不判断。
  历史变化量标准差为 0（如"年龄每步 +1"）时，用 `0.1×|均值|` 做下限，
  避免除零，也让"匀速计数器突然跳变"仍能被发现。**已知误报来源**：
  时间粒度切换（本模块跳过 `granularity_changed` 那一步）与步长可变。
  WP1 起，带合法 `elapsed_days` 的步改按"每日变化率"比较（口径见
  `VolatilityTracker`），步长可变不再是误报来源；没有 `elapsed_days` 的
  旧步/未开技术模型的实例仍是原口径，误报来源不变。
- C5 `run_threshold=3`：只用于统计"连续 ≥3 步都戏剧化"出现了几段，
  不据此告警——阈值应由基线数据决定，先看数据再定（计划 §4 WP4 C5）。

## C3 的判定边界

`prerequisites` 在 `causal_tree.py` 里的约定是"前置节点 id 列表（同线
内部或跨线）"。判定顺序：同一条线内按 id 命中 → 否则跨线按 id **唯一**
命中 → 都不行（自由文本、或跨线 id 重名有歧义）算"无法核验"，**不告警**、
只计入 `unverifiable_prerequisites`。默认兜底树的分支没有前置，所以这项
在不写 `prerequisites` 的实例上恒为 0，属于预期，不代表"没问题"。
"满足"= 前置分支状态为 `resolved`（严格口径）；这比"in progress 也算"
更容易告警，但告警本身不阻断。

## 不做的事（如实记录）

- 不把 C8 校准率写进跨模拟 `knowledge_base`：那是跨实例共享数据，写入有
  副作用，本阶段只读展示；计划里"也可写入"是可选项，留到确认后再做。
- 不做修复调用（`consistency_repair_enabled` 属于后续阶段）。
- `advance_lines()`（独立推进）路径本阶段不做逐步守卫；体检从历史重算，
  该路径的步同样会被覆盖到。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# 与 `capability_discovery._MATURITY_STAGES` 保持一致（序数即采用阶段）。
STAGES: Tuple[str, ...] = (
    "lab", "expert", "developer", "consumer", "cheap_at_scale", "infrastructure",
)
_STAGE_ORD = {name: i for i, name in enumerate(STAGES)}

TERMINAL_STATUSES = frozenset({"resolved", "expired", "invalidated"})
_REVIVE_TARGETS = frozenset({"active", "emerging"})

# 非终结状态之间的合法迁移；终结状态没有出边（任何变化都会告警）。
_ALLOWED_NON_TERMINAL: Dict[str, frozenset] = {
    "dormant": frozenset({"emerging", "active", "resolved", "expired", "invalidated"}),
    "emerging": frozenset({"dormant", "active", "resolved", "expired", "invalidated"}),
    "active": frozenset({"emerging", "resolved", "expired", "invalidated"}),
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "z_threshold": 4.0,
    "min_deltas": 5,
    "run_threshold": 3,
}

ENABLED_SETTING = "consistency_guard_enabled"


def is_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    """逐步守卫开关：默认开（只记录、不阻断、不改数值），显式设为 False 关闭。"""
    value = (settings or {}).get(ENABLED_SETTING, True)
    return bool(value)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _warn(code: str, step: int, message: str, severity: str = "warn", **detail: Any) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"code": code, "severity": severity, "step": int(step), "message": message}
    if detail:
        entry["detail"] = detail
    return entry


# ── C1：技术/能力成熟度跳级、倒退 ────────────────────────────────────


def _capability_key(item: Any) -> str:
    return str(_get(item, "capability", "") or "").strip().lower()


class StageTracker:
    """按能力名跟踪最近一次出现时的 `maturity_stage`。`feed(state)` 逐步喂入
    历史，返回这一步的 C1 告警（`capabilities_gained` 只记录"这一步新增"，
    所以比较的是同一能力两次**出现**之间的阶段差，不要求相邻步）。"""

    def __init__(self) -> None:
        self._last: Dict[str, Tuple[int, int]] = {}
        self.first_seen_stage: Dict[str, str] = {}

    def feed(self, state: Any) -> List[Dict[str, Any]]:
        step = int(_get(state, "step", 0) or 0)
        warnings: List[Dict[str, Any]] = []
        for item in _get(state, "capabilities_gained") or []:
            key = _capability_key(item)
            stage = _get(item, "maturity_stage")
            if not key or stage not in _STAGE_ORD:
                continue
            ordinal = _STAGE_ORD[stage]
            prev = self._last.get(key)
            if prev is None:
                self.first_seen_stage[key] = stage
            else:
                prev_ord, prev_step = prev
                diff = ordinal - prev_ord
                name = str(_get(item, "capability", key))
                if diff > 1:
                    warnings.append(_warn(
                        "C1", step,
                        f"能力「{name}」的成熟度从 {STAGES[prev_ord]} 直接跳到 {stage}"
                        f"（跳过 {diff - 1} 档，上次出现在第 {prev_step} 步）",
                        capability=name, kind="jump", from_stage=STAGES[prev_ord],
                        to_stage=stage, from_step=prev_step,
                    ))
                elif diff < 0:
                    reason = str(_get(item, "regression_reason", "") or "").strip()
                    if not reason:
                        warnings.append(_warn(
                            "C1", step,
                            f"能力「{name}」的成熟度从 {STAGES[prev_ord]} 倒退到 {stage}，"
                            f"且没有给出 regression_reason（上次出现在第 {prev_step} 步）",
                            capability=name, kind="regression", from_stage=STAGES[prev_ord],
                            to_stage=stage, from_step=prev_step,
                        ))
            self._last[key] = (ordinal, step)
        return warnings


# ── C2/C3/C4：因果树状态迁移 ─────────────────────────────────────────


def _iter_branches(branches: Any) -> Iterable[Dict[str, Any]]:
    if not isinstance(branches, list):
        return
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        yield branch
        yield from _iter_branches(branch.get("children"))
        yield from _iter_branches(branch.get("sub_branches"))


def tree_index(causal_lines: Any) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """`{(line_id, branch_id): {status, likelihood, prerequisites}}`，用于
    比较两个时点的树。状态/likelihood 经 `causal_tree` 的规整口径读取。"""
    from world_simulator import causal_tree

    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for line in causal_lines or []:
        if not isinstance(line, dict):
            continue
        line_id = str(line.get("id") or "").strip()
        tree = line.get("future_tree") or {}
        for branch in _iter_branches(tree.get("branches") if isinstance(tree, dict) else None):
            branch_id = str(branch.get("id") or "").strip()
            if not line_id or not branch_id:
                continue
            index[(line_id, branch_id)] = {
                "status": causal_tree.canonical_status(branch.get("status")),
                "likelihood": str(branch.get("likelihood") or "medium").strip().lower(),
                "prerequisites": [
                    str(x).strip() for x in (branch.get("prerequisites") or []) if str(x).strip()
                ],
            }
    return index


def _resolve_prerequisite(
    line_id: str, prereq: str, index: Dict[Tuple[str, str], Dict[str, Any]]
) -> Optional[Tuple[str, str]]:
    if (line_id, prereq) in index:
        return (line_id, prereq)
    matches = [key for key in index if key[1] == prereq]
    return matches[0] if len(matches) == 1 else None


def check_tree_transitions(
    before: Dict[Tuple[str, str], Dict[str, Any]],
    after: Dict[Tuple[str, str], Dict[str, Any]],
    step: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """比较两个时点的树索引，返回 `(告警, 无法核验的前置数)`。只看两边都
    存在的分支的状态变化，以及"变为 active"时的前置；新出现的分支不算迁移。"""
    warnings: List[Dict[str, Any]] = []
    unverifiable = 0
    for key, new in after.items():
        old = before.get(key)
        if old is None:
            continue
        old_status, new_status = old["status"], new["status"]
        if old_status == new_status:
            continue
        line_id, branch_id = key
        if old_status in TERMINAL_STATUSES:
            if new_status in _REVIVE_TARGETS:
                warnings.append(_warn(
                    "C2", step,
                    f"分支「{branch_id}」（线 {line_id}）已是 {old_status}，又变回 {new_status}（复活）",
                    line_id=line_id, branch_id=branch_id, from_status=old_status, to_status=new_status,
                ))
            else:
                warnings.append(_warn(
                    "C4", step,
                    f"分支「{branch_id}」（线 {line_id}）在终结状态之间改判：{old_status} → {new_status}",
                    line_id=line_id, branch_id=branch_id, from_status=old_status, to_status=new_status,
                ))
        elif new_status not in _ALLOWED_NON_TERMINAL.get(old_status, frozenset()):
            warnings.append(_warn(
                "C4", step,
                f"分支「{branch_id}」（线 {line_id}）出现非常规状态迁移：{old_status} → {new_status}",
                line_id=line_id, branch_id=branch_id, from_status=old_status, to_status=new_status,
            ))
        if new_status == "active":
            unmet: List[str] = []
            for prereq in new["prerequisites"]:
                target = _resolve_prerequisite(line_id, prereq, after)
                if target is None or target == key:
                    unverifiable += 1
                    continue
                if after[target]["status"] != "resolved":
                    unmet.append(prereq)
            if unmet:
                warnings.append(_warn(
                    "C3", step,
                    f"分支「{branch_id}」（线 {line_id}）变为 active，但前置 {', '.join(unmet)} 尚未 resolved",
                    line_id=line_id, branch_id=branch_id, unmet_prerequisites=unmet,
                ))
    return warnings, unverifiable


# ── C6：数值叶子字段波动异常 ─────────────────────────────────────────


def _numeric_leaves(vars_dict: Any) -> Dict[str, float]:
    from world_simulator.engine.resource_guard import _flatten_numeric_leaves  # 延迟导入，避免包循环

    return dict(_flatten_numeric_leaves(vars_dict if isinstance(vars_dict, dict) else {}))


class VolatilityTracker:
    """逐步喂入历史，累计每个数值叶子字段的"变化量"序列；对新一步给出
    z 值超阈的 C6 告警。

    两种口径互不混用（序列按 `(字段, 口径)` 分开存）：
    - `rate`：这一步带合法 `elapsed_days`（WP1）→ 用"每天变化率"
      `delta / elapsed_days` 比较，步长可变也可比，此时**不**因
      `granularity_changed` 跳过。
    - `raw`：没有 `elapsed_days`（旧数据/技术模型未开）→ 沿用原口径，
      `granularity_changed` 的那一步不参与判断也不进入基线。
    """

    def __init__(self, z_threshold: float, min_deltas: int) -> None:
        self.z_threshold = z_threshold
        self.min_deltas = min_deltas
        self._prev: Optional[Dict[str, float]] = None
        self._deltas: Dict[Any, List[float]] = {}

    def feed(self, state: Any) -> List[Dict[str, Any]]:
        step = int(_get(state, "step", 0) or 0)
        leaves = _numeric_leaves(_get(state, "vars"))
        warnings: List[Dict[str, Any]] = []
        elapsed = _get(state, "elapsed_days")
        rate_mode = isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and elapsed > 0
        skip = bool(_get(state, "granularity_changed", False)) and not rate_mode
        mode = "rate" if rate_mode else "raw"
        if self._prev is not None and not skip:
            for name, value in leaves.items():
                if name not in self._prev:
                    continue
                delta = float(value) - float(self._prev[name])
                used = delta / float(elapsed) if rate_mode else delta
                series = self._deltas.setdefault((name, mode), [])
                if len(series) >= self.min_deltas:
                    mean = sum(series) / len(series)
                    var = sum((d - mean) ** 2 for d in series) / len(series)
                    std = max(var ** 0.5, 0.1 * abs(mean), 1e-9)
                    z = (used - mean) / std
                    if abs(z) >= self.z_threshold:
                        if rate_mode:
                            msg = (
                                f"字段 {name} 本步变化 {delta:+g}（约 {used:+g}/天，按 elapsed_days 归一），"
                                f"与它此前的每日变化率（均值 {mean:+g}/天）相比偏离 {abs(z):.1f} 个标准差"
                            )
                        else:
                            msg = (
                                f"字段 {name} 本步变化 {delta:+g}，与它此前的变化量"
                                f"（均值 {mean:+g}）相比偏离 {abs(z):.1f} 个标准差"
                            )
                        warnings.append(_warn(
                            "C6", step, msg,
                            field=name, delta=delta, mean_delta=mean, z=round(z, 2), mode=mode,
                        ))
                series.append(used)
        self._prev = leaves
        return warnings


# ── 逐步守卫入口 ─────────────────────────────────────────────────────


def check_step(
    history_before: Sequence[Any],
    new_state: Any,
    *,
    tree_before: Any = None,
    tree_after: Any = None,
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """对刚生成、尚未落盘的 `new_state` 做 C1/C2/C3/C4/C6 检查。
    `history_before` 是推进前该分支的历史（含当前步）。"""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    stage_tracker = StageTracker()
    vol_tracker = VolatilityTracker(cfg["z_threshold"], cfg["min_deltas"])
    for state in history_before:
        stage_tracker.feed(state)
        vol_tracker.feed(state)
    warnings = stage_tracker.feed(new_state) + vol_tracker.feed(new_state)
    step = int(_get(new_state, "step", 0) or 0)
    tree_warnings, _unv = check_tree_transitions(tree_index(tree_before), tree_index(tree_after), step)
    return tree_warnings + warnings


def safe_check_step(
    history_before: Sequence[Any],
    new_state: Any,
    settings: Optional[Dict[str, Any]],
    *,
    tree_before: Any = None,
    tree_after: Any = None,
) -> List[Dict[str, Any]]:
    """`check_step()` 的宽松包装：开关关闭返回 []；任何异常也返回 []——
    守卫是旁路观察，绝不能因为它自己出错而让一步推进失败（同项目
    "辅助信息算不出来就留空"的一贯风格）。"""
    if not is_enabled(settings):
        return []
    try:
        config = {k: settings[k] for k in DEFAULT_CONFIG if isinstance(settings, dict) and k in settings}
        return check_step(
            history_before, new_state, tree_before=tree_before, tree_after=tree_after, config=config
        )
    except Exception:  # noqa: BLE001 — 见 docstring
        return []


# ── 实例级体检 ───────────────────────────────────────────────────────


def _is_dramatic(state: Any) -> bool:
    return bool(
        _get(state, "major_decision", False)
        or _get(state, "structural_change")
        or (_get(state, "capabilities_gained") or [])
    )


def _event_density(history: Sequence[Any], run_threshold: int) -> Dict[str, Any]:
    steps = [s for s in history if int(_get(s, "step", 0) or 0) > 0]  # 初始状态不算推进
    flags = [_is_dramatic(s) for s in steps]
    longest = current = long_runs = 0
    for flag in flags:
        if flag:
            current += 1
            longest = max(longest, current)
            if current == run_threshold:
                long_runs += 1
        else:
            current = 0
    total = len(flags)
    dramatic = sum(flags)
    return {
        "advanced_steps": total,
        "dramatic_steps": dramatic,
        "ratio": (dramatic / total) if total else None,
        "longest_run": longest,
        "run_threshold": run_threshold,
        "runs_at_or_above_threshold": long_runs,
    }


def _declared_edges(declared: Any) -> set:
    edges = set()
    for item in declared or []:
        if not isinstance(item, dict):
            continue
        src = str(item.get("from_line_id") or "").strip()
        dst = str(item.get("to_line_id") or "").strip()
        if src and dst and src != dst:
            edges.add((src, dst))
    return edges


def _edge_coverage(history: Sequence[Any], declared: Any) -> Dict[str, Any]:
    edges = _declared_edges(declared)
    cross = undeclared = 0
    for state in history:
        for link in _get(state, "causal_links") or []:
            src = str(_get(link, "source_line_id", "") or "").strip()
            dst = str(_get(link, "line_id", "") or "").strip()
            if not src or not dst or src == dst:
                continue
            cross += 1
            if (src, dst) not in edges:
                undeclared += 1
    return {
        "declared_edges": len(edges),
        "cross_line_links": cross,
        "undeclared": undeclared,
        "ratio": (undeclared / cross) if (cross and edges) else None,
    }


def tree_calibration(causal_lines: Any) -> Dict[str, Any]:
    """C8：已终结分支按 `likelihood` 分组的命中率（resolved 记为命中，
    expired/invalidated 记为未命中）。样本量务必一起看——小样本的命中率
    没有统计意义。`likelihood` 是分支**当前**的值（不追溯创建时的取值）。"""
    groups = {name: {"resolved": 0, "expired": 0, "invalidated": 0, "pending": 0}
              for name in ("high", "medium", "low")}
    for info in tree_index(causal_lines).values():
        likelihood = info["likelihood"] if info["likelihood"] in groups else "medium"
        status = info["status"]
        bucket = status if status in TERMINAL_STATUSES else "pending"
        groups[likelihood][bucket] += 1
    for stats in groups.values():
        terminal = stats["resolved"] + stats["expired"] + stats["invalidated"]
        stats["terminal"] = terminal
        stats["hit_rate"] = (stats["resolved"] / terminal) if terminal else None
    return groups


def _likelihood_ledger(history: Sequence[Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """C8 的对账版（P5c / 3c-iv）：终结那一刻的 likelihood 与结局，见 `tree_grounding`。
    延迟导入避免包循环；任何异常都退化为空账本，不影响体检其它项。"""
    try:
        from world_simulator import tree_grounding

        return tree_grounding.summarize_ledger(
            tree_grounding.build_likelihood_ledger(history),
            nominal=cfg.get("likelihood_nominal"),
            min_n=int(cfg.get("ledger_min_n") or tree_grounding.DEFAULT_LEDGER_MIN_N),
        )
    except Exception:  # noqa: BLE001
        return {"total_terminal": 0, "by_likelihood": {}, "inversions": [], "entries": [], "note": "账本计算出错"}


def analyze_history(
    history: Sequence[Any],
    causal_lines: Any = None,
    declared_causal_graph: Any = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """实例级"真实性体检"：重新计算全部检查并汇总。返回值全部是 JSON 兼容
    结构；`disclaimer` 字段必须随结果一起展示。

    C2/C3/C4 只能从 `dynamic_snapshot` 链计算（旧步没有快照）；
    `coverage` 如实报告能覆盖到多少。
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    stage_tracker = StageTracker()
    vol_tracker = VolatilityTracker(cfg["z_threshold"], cfg["min_deltas"])
    warnings: List[Dict[str, Any]] = []
    prev_tree: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None
    snapshot_steps = transition_pairs = unverifiable = 0

    for state in history:
        warnings.extend(stage_tracker.feed(state))
        warnings.extend(vol_tracker.feed(state))
        snap = _get(state, "dynamic_snapshot")
        if isinstance(snap, dict):
            snapshot_steps += 1
            current = tree_index(snap.get("causal_lines"))
            if prev_tree is not None:
                transition_pairs += 1
                found, unv = check_tree_transitions(
                    prev_tree, current, int(_get(state, "step", 0) or 0)
                )
                warnings.extend(found)
                unverifiable += unv
            prev_tree = current

    warnings.sort(key=lambda w: (w["step"], w["code"]))
    counts: Dict[str, int] = {}
    for w in warnings:
        counts[w["code"]] = counts.get(w["code"], 0) + 1

    born_mature = sorted(
        name for name, stage in stage_tracker.first_seen_stage.items()
        if _STAGE_ORD[stage] >= _STAGE_ORD["consumer"]
    )
    return {
        "total_steps": len(history),
        "warnings": warnings,
        "warning_counts": counts,
        "c1_capability_stage": {
            "tracked_capabilities": len(stage_tracker.first_seen_stage),
            "first_seen_at_consumer_or_later": born_mature,
        },
        "c5_event_density": _event_density(history, cfg["run_threshold"]),
        "c7_edge_coverage": _edge_coverage(history, declared_causal_graph),
        "c8_tree_calibration": tree_calibration(causal_lines),
        "c8_likelihood_ledger": _likelihood_ledger(history, cfg),
        "coverage": {
            "snapshot_steps": snapshot_steps,
            "tree_transition_pairs_checked": transition_pairs,
            "unverifiable_prerequisites": unverifiable,
            "tree_checks_note": (
                "C2/C3/C4 只覆盖 WP0 之后写入快照的步；"
                "旧步没有快照，无法核验，不代表没有问题。"
            ),
        },
        "config": copy.deepcopy(cfg),
        "disclaimer": "结构性代理指标：只说明有没有明显的结构性作弊，不是语义真实性评分。",
    }
