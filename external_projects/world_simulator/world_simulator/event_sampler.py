"""world_simulator/event_sampler.py — 外生事件采样（第二十二轮 WP2 / 阶段 P4）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md`
§4 WP2。纯 Python、**不调 LLM**。

## 为什么需要它

LLM 倾向于每一步都写出"戏剧性的突发事件"，而真实世界大多数步骤是平静的，
重大外部冲击按某个（大致的）频率发生。本模块让**引擎**按用户声明的先验
概率抽样"本步是否发生某事件"，把抽中的事件作为**既成事实**注入提示词，
LLM 只负责把它写进叙事与状态；没抽中就明确告诉 LLM"本步没有重大外部事件，
鼓励写平静的一步"。

## 数据（`manifest.settings["event_priors"]`，实例级，用户配置）

每条先验见 `normalize_prior()`：`id`/`description`/`rate_per_year`（每年期望
发生次数）/`affects`/`severity`/`cooldown_steps`/`condition`/`source`/
`verified`/`enabled`。**没有任何内置先验**：`rate_per_year` 是什么数由用户
声明，引擎不知道任何领域的真实频率。`source` 默认 `"user"`，`verified`
默认 `False`（用户自己填的数也不等于经过核对）。

## 抽样规则

- 单步概率 `p = 1 - exp(-rate_per_year × basis_days / 365.25)`（泊松到达）。
- **`basis_days` 是估计值，不是这一步实际跨越的天数**：事件必须在调用 LLM
  *之前*抽好（作为事实注入提示词），而 `elapsed_days` 要等 LLM 输出后才知道。
  所以取最近几步 `elapsed_days` 的中位数；没有历史则用
  `event_params.default_days_per_step`（通用占位值 30，**不是领域事实**）。
  每条事件记录里都标了 `basis_days`/`basis_source`。如果 LLM 实际报告的
  跨度和估计差得很远，事件频率就会偏离先验——这是已知误差，不是 bug。
- **可复现种子**：每个事件独立一次均匀抽样，种子 =
  `sha256(sim_id | 分支 | 步序号 | salt | 事件 id)`。同一分支同一步重跑结果
  一致；增删别的先验不影响某个事件自己的抽样。分叉出的分支名不同，所以
  `run_repeated_experiment` 的各条分支天然得到不同的事件序列（真实的分布，
  而不只是 LLM 采样噪声）。
- **公共随机数（`event_sampling_common_random_numbers`，默认关）**：开启后种子
  不含分支名，同一步上所有分支抽到相同事件——用于 `run_comparison_experiment`
  这类"比较不同策略"的实验，避免"策略差异"和"事件差异"混在一起。代价：
  此时重复采样的各分支事件序列相同（除非手动设不同 `salt`）。
- **冷却**：`cooldown_steps` 从**分支历史**里推导（找该事件上次真正发生的步），
  所以天然按分支正确，不需要新增分支作用域状态。
- **条件**（`condition`，可选）：变量比较 `{"var": "a.b", "op": ">=", "value": 5}`
  或技术阶段 `{"tech": "id", "min_stage": "developer"}`；数组 = 全部满足。
  变量不存在/条件写法非法 → **视为不满足**（保守：宁可不发生）。
- **上限**：同一步命中数超过 `max_events_per_step`（默认 3，占位值）时，按抽样值
  从小到大保留，其余记为 `suppressed_by_cap`（不注入、不启动冷却）。

## 已知边界（如实记录）

- 引擎**无法验证 LLM 是否真的把事件写进了叙事**，也无法阻止它另外编造冲击；
  提示词只能要求。
- `elapsed_days` 由 LLM 在输出里给出（提示词里会索要）；只要事件采样或技术模型任一
  开启就会落盘。LLM 不给时基准退回占位天数，精度降低。
- `affects` 只是提示信息，不会自动改任何字段/技术/因果线（那是 WP3 的事）。
- 先验数值是用户/LLM 的猜测，`verified=False` 就是在提醒这一点。
- `basis_days` 与实际 `elapsed_days` 的偏差会让事件频率偏离先验。
- 不做事件之间的相关性（事件 A 触发事件 B），只有各自独立的抽样。
"""

from __future__ import annotations

import hashlib
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

from world_simulator import tech_model

_SEVERITIES = ("low", "medium", "high")
_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

DEFAULT_PARAMS: Dict[str, Any] = {
    # 通用占位值，不是领域事实。
    "default_days_per_step": 30.0,
    "max_events_per_step": 3,
    "basis_window": 3,
}


def is_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    return bool((settings or {}).get("event_sampling_enabled"))


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def get_params(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`DEFAULT_PARAMS` 叠加 `settings.event_params`（非法值忽略）。"""
    params = dict(DEFAULT_PARAMS)
    override = (settings or {}).get("event_params")
    if isinstance(override, dict):
        for key in DEFAULT_PARAMS:
            f = _num(override.get(key))
            if f is not None and f > 0:
                params[key] = int(f) if key != "default_days_per_step" else f
    return params


# ── 先验 ─────────────────────────────────────────────────────────────


def normalize_prior(raw: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """规整一条先验，返回 `(先验, 问题)`：先验不可用时为 None 且给出原因。"""
    if not isinstance(raw, dict):
        return None, "不是对象"
    description = str(raw.get("description") or "").strip()
    pid = str(raw.get("id") or "").strip() or description
    if not pid:
        return None, "缺少 id 和 description"
    rate = _num(raw.get("rate_per_year"))
    if rate is None or rate < 0:
        return None, f"「{pid}」的 rate_per_year 必须是非负数"
    severity = raw.get("severity")
    cooldown = _num(raw.get("cooldown_steps"))
    source = str(raw.get("source") or "").strip() or "user"
    return {
        "id": pid,
        "description": description or pid,
        "rate_per_year": rate,
        "affects": [str(x).strip() for x in (raw.get("affects") or []) if str(x).strip()]
        if isinstance(raw.get("affects"), list) else [],
        "severity": severity if severity in _SEVERITIES else "medium",
        "cooldown_steps": max(0, int(cooldown)) if cooldown is not None else 0,
        "condition": raw.get("condition") if isinstance(raw.get("condition"), (dict, list)) else None,
        "source": source,
        "verified": bool(raw.get("verified", False)),
        "enabled": bool(raw.get("enabled", True)),
    }, None


def get_priors(settings: Optional[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """返回 `(可用先验, 被丢弃的原因)`。重复 id 保留第一个。"""
    priors: List[Dict[str, Any]] = []
    problems: List[str] = []
    seen: set = set()
    raw_list = (settings or {}).get("event_priors")
    for raw in raw_list if isinstance(raw_list, list) else []:
        prior, problem = normalize_prior(raw)
        if prior is None:
            problems.append(str(problem))
            continue
        if prior["id"] in seen:
            problems.append(f"「{prior['id']}」重复，已忽略后一条")
            continue
        seen.add(prior["id"])
        priors.append(prior)
    return priors, problems


# ── 概率/种子 ────────────────────────────────────────────────────────


def step_probability(rate_per_year: float, days: float) -> float:
    """泊松到达：一段时间内至少发生一次的概率。"""
    if rate_per_year <= 0 or days <= 0:
        return 0.0
    return 1.0 - math.exp(-rate_per_year * days / 365.25)


def draw(sim_id: str, branch: str, step: int, salt: str, event_id: str, *, common: bool = False) -> float:
    """可复现的 [0,1) 均匀抽样。`common=True` 时种子不含分支名（公共随机数）。"""
    key = "|".join([sim_id, "" if common else branch, str(step), salt, event_id])
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2 ** 64


def estimate_basis_days(history: List[Any], params: Dict[str, Any]) -> Tuple[float, str]:
    """本步跨度的估计：最近几步 `elapsed_days` 的中位数；没有则用占位值。"""
    window = int(params["basis_window"])
    values: List[float] = []
    for state in reversed(history or []):
        days = getattr(state, "elapsed_days", None)
        if isinstance(days, (int, float)) and not isinstance(days, bool) and days > 0:
            values.append(float(days))
            if len(values) >= window:
                break
    if values:
        return float(statistics.median(values)), "history_median"
    return float(params["default_days_per_step"]), "default"


# ── 条件 ─────────────────────────────────────────────────────────────

_MISSING = object()


def _get_path(data: Any, path: str) -> Any:
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def evaluate_condition(
    condition: Any,
    vars_: Optional[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """返回 `(是否满足, 原因)`。`None`/空 → 满足；写法非法/变量不存在 →
    **不满足**（保守）。数组 = 全部满足。"""
    if condition in (None, [], {}):
        return True, ""
    if isinstance(condition, list):
        for item in condition:
            ok, why = evaluate_condition(item, vars_, settings)
            if not ok:
                return False, why
        return True, ""
    if not isinstance(condition, dict):
        return False, "条件写法非法"
    if "tech" in condition:
        min_stage = condition.get("min_stage")
        need = tech_model.stage_index(min_stage)
        if need is None:
            return False, "条件里的 min_stage 非法"
        node = next((n for n in tech_model.get_nodes(settings) if n["id"] == str(condition["tech"])), None)
        if node is None:
            return False, f"技术 {condition['tech']} 未登记"
        if (tech_model.stage_index(node["stage"]) or 0) < need:
            return False, f"技术 {condition['tech']} 阶段 {node['stage']} 低于 {min_stage}"
        return True, ""
    if "var" in condition:
        op = condition.get("op")
        if op not in _OPS:
            return False, "条件里的 op 非法"
        actual = _get_path(vars_ or {}, str(condition["var"]))
        if actual is _MISSING:
            return False, f"变量 {condition['var']} 不存在"
        expected = condition.get("value")
        try:
            if op in ("==", "!="):
                return (_OPS[op](actual, expected), "" if _OPS[op](actual, expected) else
                        f"{condition['var']} {op} {expected!r} 不成立")
            a, b = _num(actual), _num(expected)
            if a is None or b is None:
                return False, f"变量 {condition['var']} 或比较值不是数字"
            ok = _OPS[op](a, b)
            return ok, "" if ok else f"{condition['var']}={a:g} 不满足 {op} {b:g}"
        except Exception:  # noqa: BLE001
            return False, "条件求值出错"
    return False, "条件需要 var 或 tech"


# ── 冷却 ─────────────────────────────────────────────────────────────


def last_fired_steps(history: List[Any]) -> Dict[str, int]:
    """从分支历史里找每个事件**真正发生**的最近一步（被上限压掉的不算）。"""
    result: Dict[str, int] = {}
    for state in history or []:
        step = int(getattr(state, "step", 0) or 0)
        for ev in getattr(state, "sampled_events", None) or []:
            if isinstance(ev, dict) and ev.get("id") and not ev.get("suppressed_by_cap"):
                result[str(ev["id"])] = max(result.get(str(ev["id"]), -10 ** 9), step)
    return result


# ── 每步抽样 ─────────────────────────────────────────────────────────


def _evaluate(
    settings: Dict[str, Any],
    history: List[Any],
    current_vars: Optional[Dict[str, Any]],
    *,
    sim_id: str,
    branch: str,
    step: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    params = get_params(settings)
    priors, _ = get_priors(settings)
    basis_days, basis_source = estimate_basis_days(history, params)
    salt = str(settings.get("event_sampling_salt") or "")
    common = bool(settings.get("event_sampling_common_random_numbers"))
    fired_at = last_fired_steps(history)
    rows: List[Dict[str, Any]] = []
    for prior in priors:
        row: Dict[str, Any] = {"prior": prior, "probability": 0.0, "draw": None, "status": ""}
        if not prior["enabled"]:
            row["status"] = "disabled"
        else:
            last = fired_at.get(prior["id"])
            if prior["cooldown_steps"] > 0 and last is not None and step - last <= prior["cooldown_steps"]:
                row["status"] = "cooldown"
                row["cooldown_left"] = prior["cooldown_steps"] - (step - last) + 1
            else:
                ok, why = evaluate_condition(prior["condition"], current_vars, settings)
                if not ok:
                    row["status"] = "condition_unmet"
                    row["why"] = why
                else:
                    row["probability"] = step_probability(prior["rate_per_year"], basis_days)
                    row["draw"] = draw(sim_id, branch, step, salt, prior["id"], common=common)
                    row["status"] = "hit" if row["draw"] < row["probability"] else "miss"
        rows.append(row)
    meta = {"basis_days": basis_days, "basis_source": basis_source, "params": params}
    return rows, meta


def sample_step(
    settings: Optional[Dict[str, Any]],
    history: List[Any],
    current_vars: Optional[Dict[str, Any]],
    *,
    sim_id: str,
    branch: str,
    step: int,
) -> List[Dict[str, Any]]:
    """抽样 `step` 这一步的事件，返回要存进 `SimState.sampled_events` 的记录。
    未开启返回 `[]`。记录只含命中的事件（被上限压掉的带 `suppressed_by_cap`）；
    没命中=静默步，什么也不记。"""
    if not is_enabled(settings):
        return []
    rows, meta = _evaluate(settings or {}, history, current_vars, sim_id=sim_id, branch=branch, step=step)
    hits = sorted((r for r in rows if r["status"] == "hit"), key=lambda r: r["draw"])
    cap = int(meta["params"]["max_events_per_step"])
    events: List[Dict[str, Any]] = []
    for i, r in enumerate(hits):
        p = r["prior"]
        entry = {
            "id": p["id"], "description": p["description"], "severity": p["severity"],
            "affects": list(p["affects"]), "probability": round(r["probability"], 6),
            "draw": round(r["draw"], 6), "basis_days": round(meta["basis_days"], 3),
            "basis_source": meta["basis_source"], "source": p["source"], "verified": p["verified"],
        }
        if i >= cap:
            entry["suppressed_by_cap"] = True
        events.append(entry)
    return events


def safe_sample_step(*args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
    """`sample_step()` 的兜底版：它在调用 LLM *之前*执行，出错不能拖垮整步
    推进——出错退化为"本步没有抽到事件"（等同静默步）。"""
    try:
        return sample_step(*args, **kwargs)
    except Exception:  # noqa: BLE001 — 旁路功能
        return []


def build_hint(settings: Optional[Dict[str, Any]], events: List[Dict[str, Any]]) -> str:
    """喂给 `advance_step`/`world_evolve` 的 `{sampled_events_hint}`。未开启返回
    空字符串（此时 prompt 与开启前逐字节等价）。"""
    if not is_enabled(settings):
        return ""
    active = [e for e in events if not e.get("suppressed_by_cap")]
    if not active:
        return (
            "【外生事件采样已开启】引擎对本步做了抽样，结果是：本步没有发生已声明的重大外部事件。\n"
            "允许并鼓励写一个平静的步骤（日常积累、自身行动的后果），不要为了戏剧性凭空引入突发的\n"
            "外部冲击（由角色/组织自身行动引发的后果不受此限）。"
            "\n请同时在输出里给出可选字段 `elapsed_days`（这一步在模拟世界里跨越的天数，正数，量级估计即可）：\n"
            "引擎用它估计下一步的事件概率；不给则下一步按占位天数估计，精度降低。"
        )
    lines = [
        "【外生事件采样已开启】本步**已发生**的外部事件（由引擎按先验概率抽样决定，不是你的选择）：",
    ]
    for e in active:
        piece = f"- [{e['id']}] {e['description']}（严重度 {e['severity']}"
        if e.get("affects"):
            piece += "；可能影响：" + "、".join(e["affects"])
        piece += "）"
        lines.append(piece)
    lines.append(
        "请把它们作为既成事实自然地写入这一步的叙事与状态变化；不要否认、替换它们，也不要再额外\n"
        "凭空引入与已声明先验无关的突发外部冲击（由角色/组织自身行动引发的后果不受此限）。\n"
        "引擎抽样时假设这一步约跨越 "
        f"{active[0]['basis_days']:g} 天（估计值）；如果你判断实际跨度差异很大，请如实给出 elapsed_days"
        "（这一步在模拟世界里跨越的天数，正数，量级估计即可；引擎也用它估计下一步的事件概率）。"
    )
    return "\n".join(lines)


def safe_build_hint(settings: Optional[Dict[str, Any]], events: List[Dict[str, Any]]) -> str:
    try:
        return build_hint(settings, events)
    except Exception:  # noqa: BLE001
        return ""


# ── 展示用 ───────────────────────────────────────────────────────────


def preview(
    settings: Optional[Dict[str, Any]],
    history: List[Any],
    current_vars: Optional[Dict[str, Any]],
    *,
    sim_id: str,
    branch: str,
    step: int,
) -> List[Dict[str, Any]]:
    """给界面：每条先验在 `step` 这一步的概率/状态（不写任何东西）。未开启返回 `[]`。
    `status` ∈ hit/miss/cooldown/condition_unmet/disabled——注意这里的 hit/miss
    就是真会被抽到的结果，同一步重跑一致。"""
    if not is_enabled(settings):
        return []
    rows, meta = _evaluate(settings or {}, history, current_vars, sim_id=sim_id, branch=branch, step=step)
    for r in rows:
        r["basis_days"] = meta["basis_days"]
        r["basis_source"] = meta["basis_source"]
    return rows
