"""world_simulator/anatomy.py — 元素剖面（anatomy）的数据模型、规整、存取与参数（第二十四轮 A1）。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.2（数据模型）、
§7（开关与参数）、§8 A1。

## 这个模块做什么（A1 范围）

剖面是元素线（`settings.causal_lines[i]`）上的**新增可选字段 `anatomy`**：把一个元素拆成
构成 / 指标 / 瓶颈 / 竞争路线 / 采用门槛 / 里程碑 / 假设 / 先行信号，每个叶子字段带来源状态 `basis`。

A1 只做四件事，**不改任何 LLM 行为、不加 prompt 段落、不加输出协议键、不推进任何数值**：

1. **规整** `normalize_anatomy`：只做结构与取值校验，丢弃非法项（可选地报告丢了什么），**不补缺省、不猜值**；
2. **存取** `get_anatomy` / `read_anatomy` / `set_anatomy` / `lines_with_anatomy` / `key_elements`：
   剖面跟着 `causal_lines` 走，所以分支快照、分叉回滚（`dynamic_state.DYNAMIC_KEYS` 已含它）零额外接线；
3. **参数** `settings.anatomy_params`（`get_params` / `invalid_param_keys`），约定同 `element_params`；
4. **只读体检** `validate_anatomy`（悬空引用 / 证据 id / 参数越界，只报告不改数据）与统计 `basis_stats` / `counts`。

## 与计划的两处细化（如实记录）

- **`sourced` 必须带证据 id**：声称"有出处"却没有 `evidence_ids` 的字段，规整时降为 `llm_prior`。
  否则"有出处"徽标会变成无法核对的空话（计划 §3.2 第 5 条"所有数字都带来源状态"的反面）。
  证据库本身（A3）还不存在，所以 A1 不核对 id 是否真实存在——`validate_anatomy(known_evidence_ids=...)` 留给 A3 接入。
- **体积上限**：每部分条目数、每个字符串长度、条件树深度/节点数都有上限（见 `MAX_*`），
  对应计划 §9 风险 10（剖面随 `causal_lines` 每步快照）。超出的条目/字符被丢弃/截断，写入 `report`。

## 刻意不做（留给后续阶段）

趋势推算、瓶颈抽样、里程碑结算、`A` 码裁决（A4）；创建期拆解（A2）；联网研究与证据库（A3）；
`anatomy_updates` 协议与深度调用（A5）；蒙特卡洛（A6）。因此 A1 里**没有任何代码会自己写入 `anatomy`**，
只有 `set_anatomy`（给后续阶段与手动编辑用）和 `element_registry.normalize_element`（只规整已存在的字段）。

## 累计模拟日（`anatomy_clock`）核对结论

计划要求"核对现有状态里是否已有可用的累计量"：**有**——每步 `SimState.elapsed_days`，`causal_engine.elapsed_between`
（`relationship.delay_days` 也用它）可累加。所以**不新增存储字段**，`clock_days()` 只是薄封装。

纯 Python：不调 LLM、不读写磁盘。依赖 `element_registry`（精确规范化匹配与参数校验）；
`element_registry.normalize_element` 对本模块只做函数内延迟导入（避免循环导入）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from world_simulator import anatomy_templates
from world_simulator import element_registry as er

# ── 常量 ─────────────────────────────────────────────────────────────

BASIS_STATES = ("sourced", "llm_prior", "user_confirmed", "user_edited")
DEFAULT_BASIS_STATE = "llm_prior"
CONFIDENCES = ("high", "medium", "low")
ANATOMY_STATUSES = ("none", "draft", "reviewed", "verified")
SEVERITIES = ("low", "medium", "high")
BOTTLENECK_STATUSES = ("open", "resolved", "exhausted")
PATH_STATUSES = ("pending", "running", "succeeded", "failed")
DISTS = ("triangular", "uniform", "lognormal")
OPS = (">=", ">", "<=", "<", "==", "!=")
REF_KINDS = ("component", "metric", "bottleneck", "milestone", "assumption", "approach", "signal")

# 剖面各部分（顺序即默认展示顺序，与模板的 `SLOTS` 一致）。
PARTS: Tuple[str, ...] = anatomy_templates.SLOTS

# 体积上限（风险 10）。
MAX_ITEMS: Dict[str, int] = {
    "components": 24, "metrics": 24, "bottlenecks": 16, "approaches": 12,
    "adoption_gates": 12, "milestones": 24, "assumptions": 24, "signals": 24,
}
MAX_PATHS = 8             # 单个瓶颈的解决路径数
MAX_REFS = 12             # 单个条目的引用数
MAX_EVIDENCE_IDS = 8      # 单个 basis 的证据 id 数
MAX_TREND_PARAMS = 12     # 单个趋势的参数数
MAX_OVERRIDES = 8         # 单个假设 `if_false.overrides` 条数
MAX_OVERRIDE_KEYS = 8
MAX_CRITERIA_DEPTH = 4
MAX_CRITERIA_NODES = 16
MAX_ID_LEN = 64
MAX_NAME_LEN = 80
MAX_TEXT_LEN = 300        # desc / statement / watch / means / expr 等

CRITERIA_LEAF_KEYS = ("metric", "component", "bottleneck", "var", "tech", "element")

# 推荐趋势库（计划 §5.3.1）。**仅影响下拉与提示词偏好，不限制可用类型**：规整时任何非空 `kind` 都保留。
RECOMMENDED_TRENDS: Tuple[str, ...] = (
    "linear", "exponential", "saturating", "logistic", "learning_curve",
    "mean_reversion", "random_walk_drift", "piecewise_table", "step_events",
)


# ── 开关 ─────────────────────────────────────────────────────────────


def is_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    """剖面总开关。**未写入 = 关闭**（旧实例逐字节保持原行为）；还要求元素模式已开启
    （剖面挂在元素线上，没有元素模式就没有地方放）。"""
    s = settings or {}
    return bool(s.get("anatomy_enabled")) and er.is_enabled(s)


# ── 参数（`settings.anatomy_params`） ────────────────────────────────

DEFAULT_PARAMS: Dict[str, Any] = {
    "key_element_count": 5,            # 重点元素个数；None = 不限
    "research_on_create": True,
    "research_on_register": True,
    "research_max_searches": 6,
    "research_ttl_days": 180,          # 现实天数
    "require_review_to_drive": False,
    "anatomy_drives_stage": True,
    "deviation_policy": "rebase",
    "light_digest_chars": 600,
    "deep_enabled": True,
    "deep_max_calls_per_step": 3,
    "deep_max_calls_total": None,      # None = 不限
    "deep_cadence_steps": 3,
    "deep_allow_search": False,
    "mc_runs": 1000,
    "mc_seed": 20260101,
    "trend_recommended": list(RECOMMENDED_TRENDS),
}
# 计划 §7 还列了 `deep_time_budget_sec`（"待 A5 定"）——A5 才有深度调用，默认值到那时再定，A1 不预设。

DEVIATION_POLICIES = ("rebase", "keep_model")


def _is_bool(value: Any) -> Tuple[bool, Any]:
    return (True, value) if isinstance(value, bool) else (False, None)


def _is_int(value: Any) -> Tuple[bool, Optional[int]]:
    if isinstance(value, bool):
        return False, None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return (True, value) if isinstance(value, int) else (False, None)


def _is_policy(value: Any) -> Tuple[bool, Any]:
    return (True, value) if value in DEVIATION_POLICIES else (False, None)


def _is_str_list(value: Any) -> Tuple[bool, Any]:
    if not isinstance(value, (list, tuple)):
        return False, None
    items = er._str_list(value)
    return (True, items) if items else (False, None)


_VALIDATORS = {
    "key_element_count": er._valid_limit,
    "research_on_create": _is_bool,
    "research_on_register": _is_bool,
    "research_max_searches": lambda v: er._valid_int_at_least(v, 1),
    "research_ttl_days": lambda v: er._valid_int_at_least(v, 1),
    "require_review_to_drive": _is_bool,
    "anatomy_drives_stage": _is_bool,
    "deviation_policy": _is_policy,
    "light_digest_chars": lambda v: er._valid_int_at_least(v, 1),
    "deep_enabled": _is_bool,
    "deep_max_calls_per_step": lambda v: er._valid_int_at_least(v, 0),
    "deep_max_calls_total": er._valid_limit,
    "deep_cadence_steps": lambda v: er._valid_int_at_least(v, 1),
    "deep_allow_search": _is_bool,
    "mc_runs": lambda v: er._valid_int_at_least(v, 1),
    "mc_seed": _is_int,
    "trend_recommended": _is_str_list,
}


def get_params(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`DEFAULT_PARAMS` 叠加 `settings.anatomy_params`；非法值回退默认。
    约定同 `element_params`：`null` 只对 `key_element_count` / `deep_max_calls_total` 合法（= 不限），`0` 就是 0。"""
    params = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_PARAMS.items()}
    override = (settings or {}).get("anatomy_params")
    if isinstance(override, dict):
        for key, check in _VALIDATORS.items():
            if key in override:
                ok, val = check(override[key])
                if ok:
                    params[key] = val
    return params


def invalid_param_keys(settings: Optional[Dict[str, Any]]) -> List[str]:
    """`anatomy_params` 里取值非法（已回退默认）的 key，给设置页提示。"""
    override = (settings or {}).get("anatomy_params")
    if not isinstance(override, dict):
        return []
    return [k for k, check in _VALIDATORS.items() if k in override and not check(override[k])[0]]


# ── 规整：基础类型 ───────────────────────────────────────────────────


class _Report:
    """规整过程中"丢了什么"的收集器（`normalize_anatomy_report` 用；不关心报告时开销可忽略）。"""

    def __init__(self) -> None:
        self.dropped: List[str] = []

    def drop(self, where: str, why: str) -> None:
        if len(self.dropped) < 200:
            self.dropped.append(f"{where}：{why}")


def _num(value: Any) -> Optional[float]:
    """有限数（不含布尔、NaN、inf）；其余 None。整数值保持 int，便于落盘稳定。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return int(value)
    return value


def _text(value: Any, limit: int) -> str:
    text = str(value).strip() if value is not None and not isinstance(value, (dict, list)) else ""
    return text[:limit]


def _unit(value: Any) -> Optional[float]:
    n = _num(value)
    return n if n is not None and 0 <= n <= 1 else None


def _nonneg(value: Any) -> Optional[float]:
    n = _num(value)
    return n if n is not None and n >= 0 else None


def _clean_id(value: Any) -> str:
    """条目 id：空白折成下划线；含 `#` / `:`（它们是引用语法的分隔符）或过长则无效（返回空串）。"""
    text = "_".join(str(value).split()) if value is not None and not isinstance(value, (dict, list)) else ""
    if not text or len(text) > MAX_ID_LEN or "#" in text or ":" in text:
        return ""
    return text


def normalize_basis(raw: Any) -> Dict[str, Any]:
    """规整来源状态，**总返回完整的 basis**（缺省/非法 = `llm_prior`）。
    `sourced` 必须带至少一个 `evidence_id`，否则降为 `llm_prior`（见模块文档）。"""
    src = raw if isinstance(raw, dict) else {}
    state = src.get("state") if src.get("state") in BASIS_STATES else DEFAULT_BASIS_STATE
    evidence = [_clean_id(e) for e in er._str_list(src.get("evidence_ids"))]
    evidence = [e for e in evidence if e][:MAX_EVIDENCE_IDS]
    if state == "sourced" and not evidence:
        state = DEFAULT_BASIS_STATE
    out: Dict[str, Any] = {"state": state}
    if evidence:
        out["evidence_ids"] = evidence
    if src.get("confidence") in CONFIDENCES:
        out["confidence"] = src["confidence"]
    return out


def _put_basis(out: Dict[str, Any], raw: Any) -> None:
    """只在**非默认**时写 `basis`（快照小；缺省 = `llm_prior`，由 `basis_of` 读出）。"""
    if not isinstance(raw, dict):
        return
    basis = normalize_basis(raw)
    if basis != {"state": DEFAULT_BASIS_STATE}:
        out["basis"] = basis


def basis_of(obj: Any) -> Dict[str, Any]:
    """读出一个条目/字段的**有效** basis（没有 = `llm_prior`）。"""
    return normalize_basis(obj.get("basis") if isinstance(obj, dict) else None)


def normalize_param(raw: Any) -> Optional[Dict[str, Any]]:
    """参数 `{value, low?, high?, dist?, source?}`。裸数字视为 `{value}`；`value` 非有限数则整个丢弃；
    `low > high` 视为非法，**丢弃两个边界**（退为点估计，蒙特卡洛里会单独标出）；`dist` 不认识则丢弃该字段。"""
    if isinstance(raw, dict):
        value = _num(raw.get("value"))
        if value is None:
            return None
        out: Dict[str, Any] = {"value": value}
        low, high = _num(raw.get("low")), _num(raw.get("high"))
        if low is not None and high is not None and low <= high:
            out["low"], out["high"] = low, high
        elif low is not None and high is None:
            out["low"] = low
        elif high is not None and low is None:
            out["high"] = high
        if raw.get("dist") in DISTS:
            out["dist"] = raw["dist"]
        source = _text(raw.get("source"), MAX_NAME_LEN)
        if source:
            out["source"] = source
        return out
    value = _num(raw)
    return {"value": value} if value is not None else None


def _refs(raw: Any) -> List[str]:
    out: List[str] = []
    for item in er._str_list(raw):
        text = "_".join(item.split()) if False else item.strip()
        if text and len(text) <= 2 * MAX_ID_LEN + 16 and text not in out:
            out.append(text)
        if len(out) >= MAX_REFS:
            break
    return out


def parse_ref(ref: Any) -> Tuple[Optional[str], Optional[str], str]:
    """`"[元素#]kind:id"` / `"元素#子项id"` → `(元素 或 None, kind 或 None, id)`。
    本元素内的引用写 `component:electrolyte`；跨元素写 `battery#component:electrolyte`，
    条件里的指标引用沿用计划的 `battery#energy_density`（无 kind，kind 由条件的键给出）。"""
    text = str(ref if ref is not None else "").strip()
    element: Optional[str] = None
    if "#" in text:
        element, text = text.split("#", 1)
        element = element.strip() or None
    kind: Optional[str] = None
    if ":" in text:
        head, rest = text.split(":", 1)
        if head.strip() in REF_KINDS:
            kind, text = head.strip(), rest
    return element, kind, text.strip()


# ── 规整：条件树 ─────────────────────────────────────────────────────


def _scalar_leaf(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, val in raw.items():
        if len(out) >= 6:
            break
        if isinstance(val, bool) or val is None:
            out[str(key)] = val
        elif isinstance(val, (int, float)):
            n = _num(val)
            if n is not None:
                out[str(key)] = n
        elif isinstance(val, str) and val.strip():
            out[str(key)] = val.strip()[:MAX_NAME_LEN]
    return out


def _norm_leaf(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if "metric" in raw:
        ref = _text(raw.get("metric"), 2 * MAX_ID_LEN)
        value = _num(raw.get("value"))
        if not ref or raw.get("op") not in OPS or value is None:
            return None
        return {"metric": ref, "op": raw["op"], "value": value}
    if "component" in raw:
        ref = _text(raw.get("component"), 2 * MAX_ID_LEN)
        ready = _unit(raw.get("min_readiness"))
        if not ref or ready is None:
            return None
        return {"component": ref, "min_readiness": ready}
    if "bottleneck" in raw:
        ref = _text(raw.get("bottleneck"), 2 * MAX_ID_LEN)
        if not ref or raw.get("status") not in BOTTLENECK_STATUSES:
            return None
        return {"bottleneck": ref, "status": raw["status"]}
    # 既有条件语法（事件先验 / 树接地用的 `var` / `tech` / `element`）：只保留标量键，不替它们校验语义。
    if any(k in raw for k in ("var", "tech", "element")):
        leaf = _scalar_leaf(raw)
        return leaf if any(k in leaf for k in ("var", "tech", "element")) else None
    return None


def normalize_criteria(raw: Any, _depth: int = 0, _budget: Optional[List[int]] = None) -> Optional[Dict[str, Any]]:
    """结构化条件树（里程碑 / 采用门槛 / 解决路径前置）：`{"all":[...]}` / `{"any":[...]}` / `{"not": 节点}` / 叶子。
    叶子认三种新引用（`metric` / `component` / `bottleneck`，见 `_norm_leaf`）与既有的 `var`/`tech`/`element`；
    其余形状整体丢弃。深度/节点数有上限；规整后为空返回 None。A1 只规整形状，**不求值**（求值在 A4）。"""
    budget = _budget if _budget is not None else [MAX_CRITERIA_NODES]
    if not isinstance(raw, dict) or _depth > MAX_CRITERIA_DEPTH or budget[0] <= 0:
        return None
    budget[0] -= 1
    for key in ("all", "any"):
        if key in raw:
            kids = []
            for child in raw[key] if isinstance(raw[key], (list, tuple)) else []:
                node = normalize_criteria(child, _depth + 1, budget)
                if node is not None:
                    kids.append(node)
            return {key: kids} if kids else None
    if "not" in raw:
        node = normalize_criteria(raw["not"], _depth + 1, budget)
        return {"not": node} if node is not None else None
    return _norm_leaf(raw)


# ── 规整：各部分 ─────────────────────────────────────────────────────


def _item_head(raw: Any, part: str, rep: _Report, seen: set) -> Optional[Tuple[Dict[str, Any], str]]:
    """条目公共头：必须是 dict；`id` 缺省时从 `name` 派生；id 无效/重复整条丢弃。"""
    if not isinstance(raw, dict):
        rep.drop(part, "条目不是对象")
        return None
    cid = _clean_id(raw.get("id")) or _clean_id(er.norm_key(raw.get("name")))
    if not cid:
        rep.drop(part, "缺少有效 id（也无法由 name 派生；id 不能含 `#` 或 `:`）")
        return None
    if cid in seen:
        rep.drop(f"{part}.{cid}", "id 重复，保留先出现的")
        return None
    seen.add(cid)
    out: Dict[str, Any] = {"id": cid}
    name = _text(raw.get("name"), MAX_NAME_LEN)
    if name:
        out["name"] = name
    return out, cid


def _norm_components(raw: Any, rep: _Report) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        head = _item_head(item, "components", rep, seen)
        if head is None:
            continue
        comp, _ = head
        ready = _unit(item.get("readiness"))
        if ready is not None:
            comp["readiness"] = ready
        requires = _refs(item.get("requires"))
        if requires:
            comp["requires"] = requires
        desc = _text(item.get("desc"), MAX_TEXT_LEN)
        if desc:
            comp["desc"] = desc
        _put_basis(comp, item.get("basis"))
        out.append(comp)
    return out


def _norm_trend(raw: Any) -> Optional[Dict[str, Any]]:
    """趋势 `{kind, params, driver?, expr?, source?}`。**任何非空 `kind` 都保留**（含不认识的、`custom_expr`、
    `llm_reported`）——不设白名单（计划 §5.3.1）；参数各自规整，非法的丢掉。表达式只截断、不在 A1 解析（A4 用白名单 AST 求值）。"""
    if not isinstance(raw, dict):
        return None
    kind = _text(raw.get("kind"), MAX_NAME_LEN)
    if not kind:
        return None
    out: Dict[str, Any] = {"kind": kind}
    params: Dict[str, Any] = {}
    for name, val in (raw.get("params") if isinstance(raw.get("params"), dict) else {}).items():
        key = _clean_id(name)
        norm = normalize_param(val)
        if key and norm is not None and len(params) < MAX_TREND_PARAMS:
            params[key] = norm
    if params:
        out["params"] = params
    for key in ("driver", "source"):
        text = _text(raw.get(key), 2 * MAX_ID_LEN)
        if text:
            out[key] = text
    expr = _text(raw.get("expr"), MAX_TEXT_LEN)
    if expr:
        out["expr"] = expr
    return out


def _norm_metrics(raw: Any, rep: _Report) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        head = _item_head(item, "metrics", rep, seen)
        if head is None:
            continue
        met, _ = head
        unit = _text(item.get("unit"), 24)
        if unit:
            met["unit"] = unit
        cur = item.get("current")
        if isinstance(cur, dict):
            value = _num(cur.get("value"))
            if value is not None:
                c: Dict[str, Any] = {"value": value}
                as_of = _text(cur.get("as_of"), 24)
                if as_of:
                    c["as_of"] = as_of
                _put_basis(c, cur.get("basis"))
                met["current"] = c
        tgt = item.get("target")
        if isinstance(tgt, dict) and tgt.get("op") in OPS and _num(tgt.get("value")) is not None:
            met["target"] = {"op": tgt["op"], "value": _num(tgt["value"])}
        bounds = item.get("bounds")
        if isinstance(bounds, dict):
            lo, hi = _num(bounds.get("min")), _num(bounds.get("max"))
            if lo is not None and hi is not None and lo > hi:
                lo = hi = None
            b = {k: v for k, v in (("min", lo), ("max", hi)) if v is not None}
            if b:
                met["bounds"] = b
        trend = _norm_trend(item.get("trend"))
        if trend:
            met["trend"] = trend
        _put_basis(met, item.get("basis"))
        out.append(met)
    return out


def _norm_duration(raw: Any) -> Optional[Dict[str, Any]]:
    """`{low, mode, high}`（天，非负）。允许只给部分；给出的必须满足 `low <= mode <= high`，否则整个丢弃。"""
    if not isinstance(raw, dict):
        return None
    got = {k: _nonneg(raw.get(k)) for k in ("low", "mode", "high")}
    got = {k: v for k, v in got.items() if v is not None}
    if not got:
        return None
    seq = [got[k] for k in ("low", "mode", "high") if k in got]
    return got if seq == sorted(seq) else None


def _norm_path(raw: Any, seen: set) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    pid = _clean_id(raw.get("id"))
    if not pid or pid in seen:
        return None
    seen.add(pid)
    out: Dict[str, Any] = {"id": pid}
    desc = _text(raw.get("desc"), MAX_TEXT_LEN)
    if desc:
        out["desc"] = desc
    p = _unit(raw.get("p_success"))
    if p is not None:
        out["p_success"] = p
    dur = _norm_duration(raw.get("duration_days"))
    if dur:
        out["duration_days"] = dur
    req = normalize_criteria(raw.get("requires"))
    if req:
        out["requires"] = req
    # 引擎状态字段（A4 才会写）：若存在就规整保留，不在 A1 丢弃，免得后续阶段的数据被存取层吞掉。
    if raw.get("status") in PATH_STATUSES:
        out["status"] = raw["status"]
    due = _nonneg(raw.get("resolve_at_day"))
    if due is not None:
        out["resolve_at_day"] = due
    return out


def _norm_bottlenecks(raw: Any, rep: _Report) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        head = _item_head(item, "bottlenecks", rep, seen)
        if head is None:
            continue
        bn, _ = head
        kind = _text(item.get("type"), 24)
        if kind:
            bn["type"] = kind
        blocks = _refs(item.get("blocks"))
        if blocks:
            bn["blocks"] = blocks
        if item.get("severity") in SEVERITIES:
            bn["severity"] = item["severity"]
        if item.get("status") in BOTTLENECK_STATUSES:
            bn["status"] = item["status"]
        paths, pseen = [], set()
        for p in item.get("resolution_paths") if isinstance(item.get("resolution_paths"), (list, tuple)) else []:
            norm = _norm_path(p, pseen)
            if norm is None:
                rep.drop(f"bottlenecks.{bn['id']}", "解决路径无效或 id 重复")
            elif len(paths) < MAX_PATHS:
                paths.append(norm)
        if paths:
            bn["resolution_paths"] = paths
            fb = [x for x in _refs(item.get("fallback")) if x in pseen]
            if fb:
                bn["fallback"] = fb
        _put_basis(bn, item.get("basis"))
        out.append(bn)
    return out


def _norm_approaches(raw: Any, rep: _Report) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        head = _item_head(item, "approaches", rep, seen)
        if head is None:
            continue
        ap, _ = head
        desc = _text(item.get("desc"), MAX_TEXT_LEN)
        if desc:
            ap["desc"] = desc
        for key in ("metrics", "bottlenecks"):
            refs = _refs(item.get(key))
            if refs:
                ap[key] = refs
        _put_basis(ap, item.get("basis"))
        out.append(ap)
    return out


def _norm_gates(raw: Any, rep: _Report) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        head = _item_head(item, "adoption_gates", rep, seen)
        if head is None:
            continue
        gate, _ = head
        market = _text(item.get("market"), MAX_NAME_LEN)
        if market:
            gate["market"] = market
        crit = normalize_criteria(item.get("criteria"))
        if crit:
            gate["criteria"] = crit
        unlocks = item.get("unlocks")
        cap = _unit(unlocks.get("adoption_cap")) if isinstance(unlocks, dict) else None
        if cap is not None:  # A1 只认计划里给出的这一个解锁项，其余留给 A4 定义
            gate["unlocks"] = {"adoption_cap": cap}
        _put_basis(gate, item.get("basis"))
        out.append(gate)
    return out


def _norm_milestones(raw: Any, rep: _Report) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        head = _item_head(item, "milestones", rep, seen)
        if head is None:
            continue
        ms, _ = head
        desc = _text(item.get("desc"), MAX_TEXT_LEN)
        if desc:
            ms["desc"] = desc
        crit = normalize_criteria(item.get("criteria"))
        if crit:
            ms["criteria"] = crit
        stage = _text(item.get("maps_to_stage"), 24)
        if stage:
            ms["maps_to_stage"] = stage
        rs = er._int_or_none(item.get("reached_step"))
        if rs is not None and rs >= 0:  # 引擎状态字段（A4 写），同上：存在就保留
            ms["reached_step"] = rs
        rd = _nonneg(item.get("reached_sim_day"))
        if rd is not None:
            ms["reached_sim_day"] = rd
        _put_basis(ms, item.get("basis"))
        out.append(ms)
    return out


def _norm_if_false(raw: Any) -> Optional[Dict[str, Any]]:
    """`{"overrides": [{"ref", "set": {标量...}}]}`：假设不成立时骨架该怎么改（供 A6 敏感性分析）。A1 只规整形状。"""
    if not isinstance(raw, dict):
        return None
    overrides = []
    for ov in raw.get("overrides") if isinstance(raw.get("overrides"), (list, tuple)) else []:
        if not isinstance(ov, dict) or len(overrides) >= MAX_OVERRIDES:
            continue
        ref = _text(ov.get("ref"), 2 * MAX_ID_LEN)
        setv = _scalar_leaf(ov.get("set")) if isinstance(ov.get("set"), dict) else {}
        if ref and setv:
            overrides.append({"ref": ref, "set": dict(list(setv.items())[:MAX_OVERRIDE_KEYS])})
    return {"overrides": overrides} if overrides else None


def _norm_assumptions(raw: Any, rep: _Report) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        head = _item_head(item, "assumptions", rep, seen)
        if head is None:
            continue
        asm, _ = head
        statement = _text(item.get("statement"), MAX_TEXT_LEN)
        if not statement:
            rep.drop(f"assumptions.{asm['id']}", "缺少 statement（假设必须是一句可被推翻的话）")
            seen.discard(asm["id"])
            continue
        asm["statement"] = statement
        attached = _refs(item.get("attached_to"))
        if attached:
            asm["attached_to"] = attached
        p = _unit(item.get("prior_p_true"))
        if p is not None:
            asm["prior_p_true"] = p
        if_false = _norm_if_false(item.get("if_false"))
        if if_false:
            asm["if_false"] = if_false
        _put_basis(asm, item.get("basis"))
        out.append(asm)
    return out


def _norm_signals(raw: Any, rep: _Report) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        head = _item_head(item, "signals", rep, seen)
        if head is None:
            continue
        sig, _ = head
        watch = _text(item.get("watch"), MAX_TEXT_LEN)
        if not watch:
            rep.drop(f"signals.{sig['id']}", "缺少 watch（现实里该盯什么）")
            seen.discard(sig["id"])
            continue
        sig["watch"] = watch
        means = _text(item.get("means"), MAX_TEXT_LEN)
        if means:
            sig["means"] = means
        refs = _refs(item.get("refs"))
        if refs:
            sig["refs"] = refs
        _put_basis(sig, item.get("basis"))
        out.append(sig)
    return out


def _norm_meta(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    if raw.get("anatomy_status") in ANATOMY_STATUSES:
        out["anatomy_status"] = raw["anatomy_status"]
    if isinstance(raw.get("key"), bool):
        out["key"] = raw["key"]
    researched = _text(raw.get("researched_at"), 32)
    if researched:
        out["researched_at"] = researched
    template = _text(raw.get("template"), 24)
    if template:
        out["template"] = template
    return out


_NORMALIZERS = {
    "components": _norm_components, "metrics": _norm_metrics, "bottlenecks": _norm_bottlenecks,
    "approaches": _norm_approaches, "adoption_gates": _norm_gates, "milestones": _norm_milestones,
    "assumptions": _norm_assumptions, "signals": _norm_signals,
}


def normalize_anatomy_report(raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    """规整整份剖面，返回 `(剖面, 被丢弃项的说明)`。

    - 只处理本模块拥有的键（8 个部分 + `meta`）；未知键丢弃。
    - **只规整出现了的字段，不补缺省**；非法取值丢弃该字段/条目，不报错。
    - 超过 `MAX_ITEMS` 的条目丢弃（保留靠前的）。
    - 结果为空（没有任何部分、也没有 `meta`）时返回 `{}`。
    - **幂等**：`normalize(normalize(x)) == normalize(x)`（测试钉死）。
    """
    rep = _Report()
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out, rep.dropped
    for part in PARTS:
        if part not in raw:
            continue
        items = _NORMALIZERS[part](raw[part], rep)
        if len(items) > MAX_ITEMS[part]:
            rep.drop(part, f"条目数超过上限 {MAX_ITEMS[part]}，多出的 {len(items) - MAX_ITEMS[part]} 条被丢弃")
            items = items[: MAX_ITEMS[part]]
        if items:
            out[part] = items
    meta = _norm_meta(raw.get("meta"))
    if meta:
        out["meta"] = meta
    return out, rep.dropped


def normalize_anatomy(raw: Any) -> Dict[str, Any]:
    """`normalize_anatomy_report` 的只取剖面版本（`element_registry.normalize_element` 用）。"""
    return normalize_anatomy_report(raw)[0]


# ── 存取 ─────────────────────────────────────────────────────────────


def get_anatomy(line: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """一条线上的剖面（不拷贝，调用方不要改）；没有/不是 dict/为空 = None。"""
    value = (line or {}).get("anatomy") if isinstance(line, dict) else None
    return value if isinstance(value, dict) and value else None


def read_anatomy(settings: Optional[Dict[str, Any]], element_ref: Any) -> Optional[Dict[str, Any]]:
    """按 id / 名称 / 别名（精确规范化匹配，跟随合并）找到元素并读出剖面。"""
    return get_anatomy(er.resolve(settings or {}, element_ref))


def lines_with_anatomy(settings: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """带剖面的元素线（不拷贝）。"""
    return [line for line in er.get_lines(settings) if get_anatomy(line) is not None]


def has_anatomy(settings: Optional[Dict[str, Any]]) -> bool:
    return bool(lines_with_anatomy(settings))


def is_key(line: Optional[Dict[str, Any]]) -> bool:
    """是否重点元素（`anatomy.meta.key`）。"""
    meta = (get_anatomy(line) or {}).get("meta") or {}
    return meta.get("key") is True


def key_elements(settings: Optional[Dict[str, Any]]) -> List[str]:
    """重点元素的线 id（存活的、非领域线），保持登记顺序。"""
    return [
        str(line["id"]).strip() for line in lines_with_anatomy(settings)
        if is_key(line) and er.is_alive(line) and er.line_kind(line) != "domain"
    ]


def set_anatomy(
    settings: Dict[str, Any], element_ref: Any, anatomy: Any,
) -> Tuple[bool, str, List[str]]:
    """把规整后的剖面写到元素线上（**就地改 `settings`**，同 `write_tech_nodes` 的约定）。
    返回 `(是否写入, 错误, 被丢弃项说明)`。

    - 找不到元素、目标是领域线、元素已退场/已合并 → 不写，返回错误；
    - 规整后为空 → 视为"清除剖面"，摘掉 `anatomy` 键；
    - **不检查** `anatomy_enabled`：这是底层存取，开关由调用方（界面/后续阶段的引擎入口）决定。
    """
    line = er.resolve(settings or {}, element_ref, follow_merged=False)
    if line is None:
        return False, f"找不到元素「{element_ref}」", []
    lid = str(line.get("id")).strip()
    if er.line_kind(line) == "domain":
        return False, f"「{lid}」是领域线，剖面只属于元素", []
    if not er.is_alive(line):
        return False, f"「{lid}」已退场或已被合并，不再接受剖面", []
    norm, dropped = normalize_anatomy_report(anatomy)
    if norm:
        line["anatomy"] = norm
    else:
        line.pop("anatomy", None)
    return True, "", dropped


def clock_days(history: Optional[List[Any]], through_step: int) -> Tuple[float, bool]:
    """`through_step`（含）为止累计的模拟天数，及是否**完整**（每步都有有效 `elapsed_days`）。
    薄封装 `causal_engine.elapsed_between`——见模块文档"累计模拟日核对结论"，不新增存储。"""
    from world_simulator.causal_engine import elapsed_between  # 延迟导入：causal_engine 反过来依赖 element_registry

    return elapsed_between(history or [], 0, int(through_step))


# ── 统计与体检 ───────────────────────────────────────────────────────


def counts(anatomy: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """各部分条目数（只列有条目的部分）。"""
    return {p: len(anatomy[p]) for p in PARTS if isinstance((anatomy or {}).get(p), list) and anatomy[p]}


def _basis_carriers(anatomy: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for part in PARTS:
        for item in anatomy.get(part) or []:
            yield item
            cur = item.get("current") if part == "metrics" else None
            if isinstance(cur, dict):
                yield cur


def basis_stats(anatomy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """来源状态统计。口径：**每个条目**各算一个单位，再加上每个指标的 `current`（现值有自己的出处）。
    返回 `{total, sourced, llm_prior, user_confirmed, user_edited, llm_prior_ratio}`；没有单位时比例为 None。"""
    stats = {s: 0 for s in BASIS_STATES}
    total = 0
    for carrier in _basis_carriers(anatomy or {}):
        stats[basis_of(carrier)["state"]] += 1
        total += 1
    return {"total": total, **stats, "llm_prior_ratio": (stats["llm_prior"] / total) if total else None}


def validate_anatomy(
    anatomy: Optional[Dict[str, Any]], *, known_evidence_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, str]]:
    """只读体检，返回 `[{kind, where, message}]`，**不改数据、不阻断**（同 T9/A5：笔误不应造成死锁）。

    - `dangling_ref`：本元素内的引用（`kind:id` 形式）指向不存在的条目（跨元素引用不查，需要注册表）；
    - `param_out_of_range`：参数 `value` 不在自己声明的 `[low, high]` 内；
    - `current_out_of_bounds`：指标现值超出 `bounds`；
    - `no_criteria`：里程碑/采用门槛没有判据（引擎无法自动判定达成，只能靠 LLM 提议）；
    - `dangling_evidence`：传了 `known_evidence_ids` 时，basis 里引用了不存在的证据 id（A3 起接入）。
    """
    a = anatomy or {}
    ids: Dict[str, set] = {
        "component": {i["id"] for i in a.get("components") or []},
        "metric": {i["id"] for i in a.get("metrics") or []},
        "bottleneck": {i["id"] for i in a.get("bottlenecks") or []},
        "milestone": {i["id"] for i in a.get("milestones") or []},
        "assumption": {i["id"] for i in a.get("assumptions") or []},
        "approach": {i["id"] for i in a.get("approaches") or []},
        "signal": {i["id"] for i in a.get("signals") or []},
    }
    problems: List[Dict[str, str]] = []

    def check_ref(where: str, ref: str) -> None:
        element, kind, rid = parse_ref(ref)
        if element is not None or kind is None:
            return  # 跨元素引用，或写法不带 kind（条件里的 metric 引用），A1 不查
        if rid not in ids.get(kind, set()):
            problems.append({"kind": "dangling_ref", "where": where, "message": f"引用了不存在的 {kind}:{rid}"})

    for comp in a.get("components") or []:
        for r in comp.get("requires") or []:
            check_ref(f"components.{comp['id']}.requires", r)
    for bn in a.get("bottlenecks") or []:
        for r in bn.get("blocks") or []:
            check_ref(f"bottlenecks.{bn['id']}.blocks", r)
    for ap in a.get("approaches") or []:
        for key, kind in (("metrics", "metric"), ("bottlenecks", "bottleneck")):
            for r in ap.get(key) or []:
                check_ref(f"approaches.{ap['id']}.{key}", r if ":" in r else f"{kind}:{r}")
    for asm in a.get("assumptions") or []:
        for r in asm.get("attached_to") or []:
            check_ref(f"assumptions.{asm['id']}.attached_to", r)
    for sig in a.get("signals") or []:
        for r in sig.get("refs") or []:
            check_ref(f"signals.{sig['id']}.refs", r)
    for met in a.get("metrics") or []:
        for pname, p in ((met.get("trend") or {}).get("params") or {}).items():
            lo, hi, v = p.get("low"), p.get("high"), p.get("value")
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                problems.append({
                    "kind": "param_out_of_range", "where": f"metrics.{met['id']}.trend.params.{pname}",
                    "message": f"value={v} 不在声明区间 [{lo}, {hi}] 内",
                })
        cur, bounds = (met.get("current") or {}).get("value"), met.get("bounds") or {}
        if cur is not None and (
            ("min" in bounds and cur < bounds["min"]) or ("max" in bounds and cur > bounds["max"])
        ):
            problems.append({
                "kind": "current_out_of_bounds", "where": f"metrics.{met['id']}.current",
                "message": f"现值 {cur} 超出 bounds {bounds}",
            })
    for part in ("milestones", "adoption_gates"):
        for item in a.get(part) or []:
            if not item.get("criteria"):
                problems.append({"kind": "no_criteria", "where": f"{part}.{item['id']}", "message": "没有判据，引擎无法自动判定"})
    if known_evidence_ids is not None:
        known = {str(e) for e in known_evidence_ids}
        for part in PARTS:
            for item in a.get(part) or []:
                for carrier, label in ((item, f"{part}.{item['id']}"), (item.get("current"), f"{part}.{item['id']}.current")):
                    for ev in (basis_of(carrier).get("evidence_ids") or []) if isinstance(carrier, dict) else []:
                        if ev not in known:
                            problems.append({"kind": "dangling_evidence", "where": label, "message": f"证据 {ev} 不存在"})
    return problems


# ═════════════════════════════════════════════════════════════════════
# 第二十四轮 A2：创建期拆解（重点元素选择、`anatomy_seed` 提示与落成、向导审阅）
#
# 纯 Python、不调 LLM。LLM 只在创建 prompt 里被要求给 `anatomy_seed`（见 `create_hint`）；
# 引擎拿到后由 `apply_seeds` 落成 `anatomy`——**LLM 不能自己声明"有出处/已确认"，也不能写引擎状态**。
# ═════════════════════════════════════════════════════════════════════

SEED_KEY = "anatomy_seed"
# 创建期 LLM 不得写入的引擎状态字段（A4 引擎才拥有它们）。
_ENGINE_PATH_FIELDS = ("status", "resolve_at_day")
_ENGINE_MILESTONE_FIELDS = ("reached_step", "reached_sim_day")


def creation_enabled(settings: Optional[Dict[str, Any]]) -> bool:
    """创建阶段是否拆解重点元素。创建时 manifest 还没写开关，所以**缺省视为开启**（新实例默认开，计划 §7）；
    只有元素模式创建被关掉、或显式 `anatomy_enabled=False` 才关。"""
    return er.creation_enabled(settings) and (settings or {}).get("anatomy_enabled", True) is not False


def select_key_elements(
    lines: List[Dict[str, Any]], edges: Optional[List[Dict[str, Any]]], params: Dict[str, Any],
) -> List[str]:
    """重点元素选择（计划 §5.2.4）：存活的元素线（`kind=element`）中，按 **先验边端点 > `technology` 类型 > LLM 给的顺序**
    取前 `key_element_count` 个（`None` = 全部，`0` = 一个都不选）。返回线 id（按优先级排序）。"""
    limit = params.get("key_element_count")
    # 只有明确是元素（`kind=element`）的线参与：没有 `kind` 的旧式独立线不是"元素"，不拆解（保守，行为不变）。
    candidates = [x for x in lines if er.line_kind(x) == "element" and er.is_alive(x)]
    endpoint_ids: set = set()
    for edge in edges or []:
        if not isinstance(edge, dict):
            continue
        for key in ("from_line_id", "to_line_id"):
            hit = er.resolve(candidates, edge.get(key))
            if hit is not None:
                endpoint_ids.add(str(hit["id"]).strip())
    ranked = sorted(
        enumerate(candidates),
        key=lambda t: (
            0 if str(t[1]["id"]).strip() in endpoint_ids else 1,
            0 if str(t[1].get("element_type") or "").strip().lower() == er.TECHNOLOGY_TYPE else 1,
            t[0],
        ),
    )
    ids = [str(x["id"]).strip() for _, x in ranked]
    return ids if limit is None else ids[: max(0, int(limit))]


def seed_to_anatomy(seed: Any, element_type: Any) -> Tuple[Dict[str, Any], List[str]]:
    """LLM 的 `anatomy_seed` → 创建期剖面 `(剖面, 被丢弃项说明)`。

    规整后再**强制**：所有条目 `basis` 一律回到 `llm_prior`（LLM 无权声明出处/已确认）、剥掉引擎状态字段
    （路径 `status`/`resolve_at_day`、里程碑 `reached_*`）、`meta` 重写为 `{anatomy_status: draft, key: true, template}`。
    """
    anatomy, dropped = normalize_anatomy_report(seed)
    for part in PARTS:
        for item in anatomy.get(part) or []:
            item.pop("basis", None)
            cur = item.get("current")
            if isinstance(cur, dict):
                cur.pop("basis", None)
            for path in item.get("resolution_paths") or []:
                for f in _ENGINE_PATH_FIELDS:
                    path.pop(f, None)
            if part == "milestones":
                for f in _ENGINE_MILESTONE_FIELDS:
                    item.pop(f, None)
    anatomy["meta"] = {
        "anatomy_status": "draft", "key": True, "template": anatomy_templates.template_name(element_type),
    }
    return normalize_anatomy(anatomy), dropped


def _key_shell(element_type: Any) -> Dict[str, Any]:
    """没有内容的重点元素壳：`anatomy_status=none`，让后续研究阶段（A3）知道它是重点。"""
    return {"meta": {"anatomy_status": "none", "key": True, "template": anatomy_templates.template_name(element_type)}}


def apply_seeds(
    lines: List[Dict[str, Any]], edges: Optional[List[Dict[str, Any]]], settings: Optional[Dict[str, Any]],
) -> List[str]:
    """创建后处理（`element_registry.prepare_created_lines` 末尾调用）。**就地改 `lines`**，返回说明列表。

    - 任何情况下都把 `anatomy_seed` 摘掉（它不是持久字段，不能漏进落盘数据）；
    - 创建拆解开启时：选出重点元素，有种子的落成 `anatomy`（草稿），没种子的给一个空壳；
      非重点元素的种子丢弃（计划：只有重点元素享受拆解）；
    - 关闭时到此为止，线的内容与没有本功能时一致。
    """
    seeds: Dict[str, Any] = {}
    for line in lines:
        if SEED_KEY in line:
            seeds[str(line["id"]).strip()] = line.pop(SEED_KEY)
    if not creation_enabled(settings):
        return []
    notes: List[str] = []
    key_ids = set(select_key_elements(lines, edges, get_params(settings)))
    for line in lines:
        lid = str(line["id"]).strip()
        if lid not in key_ids:
            if lid in seeds:
                notes.append(f"{lid}：不是重点元素，拆解草稿未保留")
            continue
        if get_anatomy(line) is not None:
            continue
        seed = seeds.get(lid)
        if seed is not None:
            anatomy, dropped = seed_to_anatomy(seed, line.get("element_type"))
            notes.extend(f"{lid}：{d}" for d in dropped)
            if any(anatomy.get(p) for p in PARTS):
                line["anatomy"] = anatomy
                continue
        line["anatomy"] = _key_shell(line.get("element_type"))
    return notes


def apply_key_selection(lines: List[Dict[str, Any]], key_ids: Iterable[str]) -> List[Dict[str, Any]]:
    """向导里用户勾选的重点元素 → 返回新的线列表（不改入参）：被选中的标 `key=true`（没有剖面就给空壳），
    没被选中的把 `key` 置 false（**不删除**已有内容）。领域线与不存在的 id 忽略。"""
    wanted = {str(k).strip() for k in key_ids}
    out: List[Dict[str, Any]] = []
    for line in lines:
        line = dict(line)
        lid = str(line.get("id") or "").strip()
        if er.line_kind(line) == "element" and lid:
            cur = get_anatomy(line)
            if lid in wanted:
                if cur is None:
                    line["anatomy"] = _key_shell(line.get("element_type"))
                else:
                    line["anatomy"] = {**cur, "meta": {**(cur.get("meta") or {}), "key": True}}
            elif cur is not None and (cur.get("meta") or {}).get("key"):
                line["anatomy"] = {**cur, "meta": {**cur["meta"], "key": False}}
        out.append(line)
    return out


REVIEW_ACTIONS = ("keep", "confirm", "reject")


def _all_reviewed(anatomy: Dict[str, Any]) -> bool:
    """所有条目都经**用户**确认/编辑才算已审阅。

    A3 修订：A2 时曾把 `sourced`（有出处）也算作已审阅，那时证据库还不存在、`sourced` 不可能出现。
    A3 起 `sourced` 来自联网研究（LLM 声称的出处，用户没看过），不能因此就显示成"已审阅"——
    计划 §3.3"不自动把联网研究结果标成已核实（核实只能由用户操作）"。
    """
    carriers = list(_basis_carriers(anatomy))
    return bool(carriers) and all(basis_of(c)["state"] in ("user_confirmed", "user_edited") for c in carriers)


def apply_review(
    anatomy: Optional[Dict[str, Any]], decisions: Dict[Tuple[str, str], str],
) -> Dict[str, Any]:
    """向导逐字段审阅（计划 §5.4.4）：`decisions[(part, item_id)] ∈ keep/confirm/reject`。返回新剖面（不改入参）。

    - `confirm`：该条目 `basis.state = user_confirmed`（**保留已有证据 id**；指标的 `current` 一并确认）；
    - `reject`：整条删除（条目没了，引用它的别处会在体检里报悬空，不静默改别处）；
    - `keep` / 未列出：不动，仍是 `llm_prior`；
    - 全部条目都已确认/编辑/有出处 → `anatomy_status = reviewed`，否则保持 `draft`。
    """
    cur = normalize_anatomy(anatomy)
    if not cur:
        return {}
    out: Dict[str, Any] = {}
    for part in PARTS:
        kept = []
        for item in cur.get(part) or []:
            action = decisions.get((part, item["id"]), "keep")
            if action == "reject":
                continue
            item = dict(item)
            if action == "confirm":
                item["basis"] = {**basis_of(item), "state": "user_confirmed"}
                if isinstance(item.get("current"), dict):
                    item["current"] = {**item["current"], "basis": {**basis_of(item["current"]), "state": "user_confirmed"}}
            kept.append(item)
        if kept:
            out[part] = kept
    meta = dict(cur.get("meta") or {})
    if any(out.get(p) for p in PARTS):
        meta["anatomy_status"] = "reviewed" if _all_reviewed(out) else "draft"
    else:
        meta["anatomy_status"] = "none"
    out["meta"] = meta
    return normalize_anatomy(out)


# ── A3：联网研究 / 证据支撑（纯函数，不读写磁盘）──────────────────────


def derive_status(anatomy: Optional[Dict[str, Any]]) -> str:
    """按内容重算 `anatomy_status`：没有任何条目 → `none`；全部条目都经用户确认/编辑 → `reviewed`；否则 `draft`。
    （`verified` 是为"用户逐项核实"预留的状态，目前没有界面入口，本函数不产生它。）"""
    a = normalize_anatomy(anatomy)
    if not any(a.get(p) for p in PARTS):
        return "none"
    return "reviewed" if _all_reviewed(a) else "draft"


def strip_engine_state(anatomy: Dict[str, Any]) -> Dict[str, Any]:
    """**就地**剥掉引擎状态字段（路径 `status`/`resolve_at_day`、里程碑 `reached_*`），返回同一个对象。

    联网研究的 LLM 输出和创建期种子一样，**无权**声明"某条路径已成功/某个里程碑已达成"——这些是 A4 引擎的状态。
    """
    for part in PARTS:
        for item in anatomy.get(part) or []:
            for path in item.get("resolution_paths") or []:
                for f in _ENGINE_PATH_FIELDS:
                    path.pop(f, None)
            if part == "milestones":
                for f in _ENGINE_MILESTONE_FIELDS:
                    item.pop(f, None)
    return anatomy


def evidence_refs(anatomy: Optional[Dict[str, Any]]) -> List[str]:
    """剖面里所有被引用的证据 id（去重、保持首次出现顺序）。"""
    seen: Dict[str, None] = {}
    for carrier in _basis_carriers(anatomy or {}):
        for ev in basis_of(carrier).get("evidence_ids") or []:
            seen.setdefault(ev, None)
    return list(seen)


def strip_evidence(anatomy: Optional[Dict[str, Any]], ev_ids: Iterable[str]) -> Dict[str, Any]:
    """从剖面里摘掉对若干证据的引用（用户驳回证据时用）。返回新剖面，不改入参。

    - 摘掉 `evidence_ids` 里的这些 id；
    - 一个 `sourced` 条目摘完后没有剩余证据 → 回到 `llm_prior`（计划 §5.4.4"驳回后回到 llm_prior"，并清掉过时的 `confidence`）；
    - `user_confirmed`/`user_edited` 的条目**保持用户的决定**，只是不再带这条证据 id。
    """
    gone = {str(e) for e in ev_ids}
    cur = normalize_anatomy(anatomy)
    if not cur or not gone:
        return cur

    def fix(carrier: Dict[str, Any]) -> None:
        basis = basis_of(carrier)
        kept = [e for e in basis.get("evidence_ids") or [] if e not in gone]
        if kept == (basis.get("evidence_ids") or []):
            return
        if basis["state"] == "sourced" and not kept:
            carrier.pop("basis", None)
            return
        new = {k: v for k, v in basis.items() if k != "evidence_ids"}
        if kept:
            new["evidence_ids"] = kept
        if new == {"state": DEFAULT_BASIS_STATE}:
            carrier.pop("basis", None)
        else:
            carrier["basis"] = new

    for part in PARTS:
        for item in cur.get(part) or []:
            fix(item)
            if isinstance(item.get("current"), dict):
                fix(item["current"])
    return normalize_anatomy(cur)


def set_key(settings: Dict[str, Any], element_ref: Any, key: bool) -> Tuple[bool, str]:
    """把一个元素设为/取消重点元素（**就地改 `settings`**，同 `set_anatomy` 的约定）。返回 `(是否改动, 错误)`。

    - 设为重点：没有剖面就写一个空壳（`anatomy_status=none`），已有剖面只改 `meta.key`；
    - 取消重点：只把 `meta.key` 置 false，**不删除**已有内容；没有剖面的元素什么都不做；
    - 领域线 / 已退场 / 找不到 → 不改，返回错误。
    不检查 `anatomy_enabled`（底层存取，开关由调用方决定）。
    """
    line = er.resolve(settings or {}, element_ref, follow_merged=False)
    if line is None:
        return False, f"找不到元素「{element_ref}」"
    lid = str(line.get("id")).strip()
    if er.line_kind(line) == "domain":
        return False, f"「{lid}」是领域线，不能设为重点元素"
    if not er.is_alive(line):
        return False, f"「{lid}」已退场或已被合并"
    cur = get_anatomy(line)
    if key:
        if cur is None:
            line["anatomy"] = _key_shell(line.get("element_type"))
            return True, ""
        if (cur.get("meta") or {}).get("key") is True:
            return False, ""
        line["anatomy"] = {**cur, "meta": {**(cur.get("meta") or {}), "key": True}}
        return True, ""
    if cur is None or not (cur.get("meta") or {}).get("key"):
        return False, ""
    line["anatomy"] = {**cur, "meta": {**cur["meta"], "key": False}}
    return True, ""


def create_hint(settings: Optional[Dict[str, Any]]) -> str:
    """创建 prompt 里的剖面段落（拼在 `_element_create_hint` 末尾）。关闭时返回空串（prompt 逐字节不变）。
    只改"怎么要求 skill 规划"，输出仍是同一个 `causal_lines` 数组，条目多一个可选的 `anatomy_seed`。"""
    if not creation_enabled(settings):
        return ""
    limit = get_params(settings)["key_element_count"]
    count = "不限个数" if limit is None else f"大约 {limit} 个以内"
    if limit == 0:
        return ""
    slots = "；".join(anatomy_templates.slot_hint(t) for t in ("technology", "project", "policy"))
    return (
        f"\n**重点元素拆解（可选）**：对最关键的元素（{count}，优先选与其他元素有先验因果关系的、以及技术类），"
        "在它的 `causal_lines` 条目里额外给一个 `anatomy_seed` 对象，把它拆开而不是只当成一个整体。`anatomy_seed` 可含以下数组（都可省略）："
        "`components`（构成/子系统：`id`、`name`、`readiness` 0~1）、"
        "`metrics`（关键指标：`id`、`name`、`unit`、`current`:{`value`,`as_of`}、`target`:{`op`,`value`}、`trend`:{`kind`,`params`:{名:{`value`,`low`,`high`}}}）、"
        "`bottlenecks`（瓶颈：`id`、`type`、`severity`、`resolution_paths`:[{`id`,`desc`,`p_success`,`duration_days`:{`low`,`mode`,`high`}}]）、"
        "`milestones`（里程碑：`id`、`desc`、`criteria`、`maps_to_stage`）、"
        "`assumptions`（关键假设：`id`、`statement`、`prior_p_true`）、"
        "`signals`（先行信号：`id`、`watch`、`means`）。"
        f"不同类型关注的槽位不同，例如：{slots}。"
        "**不确定的数值就不要给，不要为了填满字段编造**——这些内容会被标成「LLM 先验」（没有外部出处的猜测），"
        "并由用户逐条审阅；不要在里面写出处、已确认之类的声明，也不要写瓶颈是否已被解决。"
        "没有把握拆解的元素就不给 `anatomy_seed`。"
    )


def apply_creation_review(
    lines: List[Dict[str, Any]], key_ids: Optional[Iterable[str]],
    decisions: Optional[Dict[Tuple[str, str, str], str]],
) -> List[Dict[str, Any]]:
    """向导"保存"时一次性落地：先按 `key_ids` 调整重点元素（`None` = 用户没动过，不改），
    再对每个元素套 `apply_review`。`decisions[(元素 id, part, 条目 id)]`。返回新线列表，不改入参。"""
    out = apply_key_selection(lines, key_ids) if key_ids is not None else [dict(x) for x in lines]
    by_element: Dict[str, Dict[Tuple[str, str], str]] = {}
    for (eid, part, iid), action in (decisions or {}).items():
        if action in REVIEW_ACTIONS and action != "keep":
            by_element.setdefault(str(eid), {})[(part, iid)] = action
    for line in out:
        lid = str(line.get("id") or "").strip()
        if lid in by_element and get_anatomy(line) is not None:
            reviewed = apply_review(line["anatomy"], by_element[lid])
            if reviewed:
                line["anatomy"] = reviewed
            else:
                line.pop("anatomy", None)
    return out
