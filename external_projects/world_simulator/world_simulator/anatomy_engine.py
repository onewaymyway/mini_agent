"""world_simulator/anatomy_engine.py — 元素剖面的引擎定量骨架（第二十四轮 A4）。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.3（引擎定量骨架）、§8 A4。

## 这个模块做什么

A1–A3 让元素有了\"剖面\"（构成/指标/瓶颈/里程碑……）和\"出处\"，但剖面还是**静态**的。A4 让引擎**每步结算它**：

| 部分 | 引擎每步做什么 |
|---|---|
| 指标 `metrics` | 按趋势模型（推荐库 / 白名单表达式 / 未知则退化为\"只记录 LLM 报告值\"）从引擎当前值推进 `elapsed_days` |
| 组件 `components` | `readiness_trend`（可选）同样推进，夹在 [0,1] |
| 瓶颈 `bottlenecks` | 解决路径**启动时**由引擎抽样（成败 + 耗时，种子复现），到期结算；失败则按 `fallback` 启动下一条，全部失败 = `exhausted` |
| 里程碑 `milestones` | 判据（结构化条件）满足即 `reached`（只增不减）；有 `maps_to_stage` 的里程碑**派生**技术元素的阶段 |
| 采用门槛 `adoption_gates` | 判据满足 = 门槛打开；`unlocks.adoption_cap` 约束技术节点的采用率（A7） |

**引擎持有结构、LLM 判断语义**：LLM 不能宣布\"瓶颈已解决 / 里程碑达成 / 阶段跃迁\"，只能提议，引擎按判据裁决并留痕。

## 纯函数骨架

`apply_step()` 的输入是`settings` + 提议 + 随机源（`sim_id/branch/step` → `event_sampler.draw` 的确定性哈希），
输出是就地更新后的剖面 + 流水 + 违规。**同一输入同一种子结果逐位一致**；A6 蒙特卡洛会复用这份实现。
`apply_step()` 在所有计算完成后才一次性写回（工作副本），任何异常不会留下半改状态（`safe_apply_step` 再包一层 `A0`）。

## 与 T 码 / 阶段的关系（计划 §5.3.3）

LLM 声明的阶段迁移**先**过 `tech_model.apply_step` 的 T1–T11，**再**过本模块：
- 元素带剖面且 `anatomy_drives_stage` 开启且有 `maps_to_stage` 里程碑时，阶段 = 已达成里程碑里最高的一档（`progress` 变成派生显示值）；
- T 码放行的**上升**声明若没有对应里程碑 → `A3` 驳回并回滚到本步开始前的阶段；
- **下降**声明（T4 放行的倒退）同样 `A3` 驳回：里程碑只增不减，有剖面元素的阶段是里程碑的函数，要倒退请改剖面；
- 没有剖面 / 没有 `maps_to_stage` 里程碑的技术节点完全走旧逻辑。

## `A` 码

| 码 | 含义 | 结果 |
|---|---|---|
| A0 | 剖面引擎本步出错 | 整步跳过，状态不变（info） |
| A1 | 指标报告值偏离引擎模型超过阈值且没有原因/cause_ref | 保留引擎值（warn） |
| A2 | 声称瓶颈已解决但路径未到期 / 无原因地要求提前推迟 | 驳回（warn） |
| A3 | 声称里程碑/阶段迁移但判据未满足 | 驳回（warn） |
| A4 | 指标超出声明的 `bounds` | 夹值 |
| A5 | 引用了不存在的 id / 阶段名 | 不阻断，仅记录（info） |
| A6 | 试图修改引擎持有的参数/路径概率/到期日 | 忽略（info） |
| A7 | 采用率超过门槛允许的上限 | 夹值（叠加既有 T6） |
| A8 | 深度调用失败 / 降级为轻量（`anatomy_deepen.py` 产生，本模块不产生） | info |
| A9 | 关键指标的证据已过期仍在驱动引擎 | info |
| A10 | 引擎缺少推算所需数据（趋势 `llm_reported`/未知/缺参数、路径缺时长） | info |
| A11 | 流水自洽核对失败 / 流水超上限被截断（计划之外新增） | warn |
| A12 | 提议被忽略：新增子项缺依据/形状非法/id 重复/超上限、先行信号重复/超上限、深度调用越权提议其他元素、多条线重复提议（A5 新增） | info/warn |

## 提议的形状（`anatomy_updates`）

A4 实现**引擎侧的裁决接口**，A5 起 `{anatomy_hint}` 向 LLM 索要这个可选输出，并新增 `new_subitems`/`signals` 两类；`data` 里没有这个键就是空操作。形状：

```
{"metric_deviations":   [{"element", "metric", "value", "reason", "cause_ref"}],
 "bottleneck_proposals":[{"element", "bottleneck", "status": "resolved", "shift_days", "reason", "cause_ref"}],
 "milestone_claims":    [{"element", "milestone", "reason"}],          # 协议不邀请 LLM 写这个；写了也按 A3 裁决
 "new_subitems":        [{"element", "part": "component|metric|bottleneck", "reason", "cause_ref", "item": {...剖面格式}}],
 "signals":             [{"element", "watch", "means"}],
 "engine_edits":        [{"element", "ref", "field"}]}      # 任何想改引擎参数的企图 → A6 忽略
```

`apply_adjudication()`（A5）是**二次裁决**：本步 `apply_step` 结算之后，对一份提议（深度调用的产出）再裁决一遍，不推进时间，规则与 A 码完全共用。

## 刻意不做

- 组件就绪度的 LLM 提议（A5 的 `new_subitems` 只能新增，不能改已有组件的就绪度）；蒙特卡洛 / 敏感性（A6）；
- 路径\"投入允许\"（计划 §5.3.2 提到，但没有定义投入如何影响路径，A4 不做）；
- `custom_expr` 的参数不确定性（A6 才用 `low/high`）。

纯 Python：不调 LLM、不读写磁盘。
"""

from __future__ import annotations

import ast
import copy
import math
import operator
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from world_simulator import anatomy as an
from world_simulator import element_registry as er
from world_simulator import event_sampler, tech_model

# ── 常量 ─────────────────────────────────────────────────────────────

DEVIATION_THRESHOLD = 0.2        # 指标报告值与模型值的相对偏差阈值（超过且无原因 → A1）
MAX_TRACE = 120                  # 每步流水条数上限（风险 10）
MAX_HINT_ELEMENTS = 8
DAYS_PER_YEAR = 365.25
TRACE_TOL = 1e-6                 # 流水自洽核对的相对容差
MAX_NEW_SUBITEMS = 3             # A5：每个元素每次裁决最多接受的新增子项（组件/指标/瓶颈）数
MAX_NEW_SIGNALS = 3              # A5：每个元素每次裁决最多接受的新增先行信号数

_PART_OF_KIND = {
    "component": "components", "metric": "metrics", "bottleneck": "bottlenecks",
    "milestone": "milestones", "assumption": "assumptions", "approach": "approaches", "signal": "signals",
}
# 值是时间的绝对函数（不是自治流）的趋势：rebase 时要加偏移而不是直接换起点。
_ABSOLUTE_KINDS = ("custom_expr", "piecewise_table")
_ENGINE_KINDS = (
    "linear", "exponential", "saturating", "logistic", "learning_curve", "mean_reversion",
    "random_walk_drift", "piecewise_table", "step_events", "custom_expr",
)


# ═════════════════════════════════════════════════════════════════════
# 白名单表达式求值（`custom_expr`）——不使用 eval/compile
# ═════════════════════════════════════════════════════════════════════


class ExprError(ValueError):
    """表达式非法或求值失败。"""


_MAX_NODES = 64
_MAX_DEPTH = 24
_MAX_POW = 64.0
_FUNCS: Dict[str, Callable[..., float]] = {
    "min": min, "max": max, "abs": abs, "exp": math.exp, "log": math.log, "sqrt": math.sqrt,
}
_BINOPS: Dict[type, Callable[[float, float], float]] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}
_CMPOPS: Dict[type, Callable[[float, float], bool]] = {
    ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
}
_SCOPES = ("params", "metric")


def _parse_expr(expr: Any) -> ast.Expression:
    text = str(expr if expr is not None else "").strip()
    if not text:
        raise ExprError("表达式为空")
    if len(text) > an.MAX_TEXT_LEN:
        raise ExprError(f"表达式超过 {an.MAX_TEXT_LEN} 字")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"语法错误：{exc.msg}") from None
    if sum(1 for _ in ast.walk(tree)) > _MAX_NODES:
        raise ExprError(f"表达式节点数超过 {_MAX_NODES}")
    _check(tree.body, 0)
    return tree


def _check(node: ast.AST, depth: int) -> None:
    """静态白名单检查：只允许数字、四则与幂、比较、布尔、三元、少数函数、`params.x`/`metric.x`、变量名。"""
    if depth > _MAX_DEPTH:
        raise ExprError("表达式嵌套过深")
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            if not isinstance(node.value, bool):
                raise ExprError("只允许数字常量")
        return
    if isinstance(node, ast.Name):
        if node.id in _SCOPES or node.id in _FUNCS:
            raise ExprError(f"`{node.id}` 不能单独作为变量使用")
        return
    if isinstance(node, ast.Attribute):
        if not (isinstance(node.value, ast.Name) and node.value.id in _SCOPES):
            raise ExprError("不允许属性访问（只允许 params.<名> / metric.<id>）")
        return
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, (ast.Pow, *_BINOPS)):
            raise ExprError("不支持的运算符")
        _check(node.left, depth + 1)
        _check(node.right, depth + 1)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            raise ExprError("不支持的一元运算符")
        _check(node.operand, depth + 1)
        return
    if isinstance(node, ast.BoolOp):
        for v in node.values:
            _check(v, depth + 1)
        return
    if isinstance(node, ast.Compare):
        if not all(isinstance(op, tuple(_CMPOPS)) for op in node.ops):
            raise ExprError("不支持的比较运算符")
        _check(node.left, depth + 1)
        for c in node.comparators:
            _check(c, depth + 1)
        return
    if isinstance(node, ast.IfExp):
        for part in (node.test, node.body, node.orelse):
            _check(part, depth + 1)
        return
    if isinstance(node, ast.Call):
        if not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS) or node.keywords:
            raise ExprError("只允许调用 min/max/abs/exp/log/sqrt")
        if not 1 <= len(node.args) <= 3:
            raise ExprError("函数参数个数应为 1–3")
        for a in node.args:
            _check(a, depth + 1)
        return
    raise ExprError(f"不支持的语法：{type(node).__name__}")


def validate_expr(expr: Any) -> Optional[str]:
    """表达式合法返回 None，否则返回原因（给界面/体检用；不求值）。"""
    try:
        _parse_expr(expr)
        return None
    except ExprError as exc:
        return str(exc)


def _truthy(x: float) -> bool:
    return bool(x)


def _ev(node: ast.AST, env: Dict[str, Any]) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        names = env["names"]
        if node.id not in names:
            raise ExprError(f"未知变量 {node.id}")
        return float(names[node.id])
    if isinstance(node, ast.Attribute):
        scope = env[node.value.id]  # type: ignore[attr-defined]
        if node.attr not in scope:
            raise ExprError(f"未知引用 {node.value.id}.{node.attr}")  # type: ignore[attr-defined]
        return float(scope[node.attr])
    if isinstance(node, ast.UnaryOp):
        v = _ev(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return v
        return 0.0 if _truthy(v) else 1.0
    if isinstance(node, ast.BinOp):
        a, b = _ev(node.left, env), _ev(node.right, env)
        try:
            if isinstance(node.op, ast.Pow):
                if abs(b) > _MAX_POW:
                    raise ExprError("指数过大")
                r = a ** b
                if isinstance(r, complex):
                    raise ExprError("幂运算结果是复数")
                return float(r)
            return float(_BINOPS[type(node.op)](a, b))
        except (OverflowError, ZeroDivisionError, ValueError) as exc:
            raise ExprError(f"运算出错：{exc}") from None
    if isinstance(node, ast.BoolOp):
        vals = [_ev(v, env) for v in node.values]
        ok = all(_truthy(v) for v in vals) if isinstance(node.op, ast.And) else any(_truthy(v) for v in vals)
        return 1.0 if ok else 0.0
    if isinstance(node, ast.Compare):
        left = _ev(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            right = _ev(comp, env)
            if not _CMPOPS[type(op)](left, right):
                return 0.0
            left = right
        return 1.0
    if isinstance(node, ast.IfExp):
        return _ev(node.body if _truthy(_ev(node.test, env)) else node.orelse, env)
    if isinstance(node, ast.Call):
        args = [_ev(a, env) for a in node.args]
        try:
            return float(_FUNCS[node.func.id](*args))  # type: ignore[attr-defined]
        except (OverflowError, ZeroDivisionError, ValueError, TypeError) as exc:
            raise ExprError(f"函数调用出错：{exc}") from None
    raise ExprError("不支持的语法")  # pragma: no cover — `_check` 已挡住


def eval_expr(
    expr: Any, *, names: Optional[Dict[str, float]] = None, params: Optional[Dict[str, float]] = None,
    metrics: Optional[Dict[str, float]] = None,
) -> float:
    """求值白名单表达式。变量：`names`（本引擎传 `t`/`v0`）、`params.<名>`、`metric.<id>`。结果必须是有限数，否则 `ExprError`。"""
    tree = _parse_expr(expr)
    try:
        out = _ev(tree.body, {"names": dict(names or {}), "params": dict(params or {}), "metric": dict(metrics or {})})
    except OverflowError as exc:  # 超大整数字面量转 float 等
        raise ExprError(f"数值溢出：{exc}") from None
    if not math.isfinite(out):
        raise ExprError("结果不是有限数")
    return out


# ═════════════════════════════════════════════════════════════════════
# 开关与时间
# ═════════════════════════════════════════════════════════════════════


def has_engine_content(anatomy: Optional[Dict[str, Any]]) -> bool:
    """剖面里有没有引擎能结算的东西（指标/组件/瓶颈/里程碑/门槛）。只有 meta/假设/信号的壳不算。"""
    a = anatomy or {}
    return any(a.get(p) for p in ("metrics", "components", "bottlenecks", "milestones", "adoption_gates"))


def _engine_lines(settings: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        x for x in an.lines_with_anatomy(settings)
        if er.is_alive(x) and er.line_kind(x) != "domain" and has_engine_content(an.get_anatomy(x))
    ]


def is_active(settings: Optional[Dict[str, Any]]) -> bool:
    """引擎本步是否有事可做：剖面总开关开启，且至少有一个元素带引擎能结算的内容。
    **未开启 / 没有任何剖面 = False**，此时 prompt、落盘与没有本功能时逐字节一致。"""
    return bool(settings) and an.is_enabled(settings) and bool(_engine_lines(settings))


def needs_elapsed(settings: Optional[Dict[str, Any]]) -> bool:
    """本步是否需要 LLM 给 `elapsed_days`（计划 §5.3.6：开启 anatomy 的实例必须索要）。"""
    return is_active(settings)


def _drives(item: Dict[str, Any], params: Dict[str, Any]) -> bool:
    """`require_review_to_drive` 开启时，只有用户确认/编辑过的条目才驱动引擎（其余只展示）。"""
    if not params.get("require_review_to_drive"):
        return True
    return an.basis_of(item)["state"] in ("user_confirmed", "user_edited")


# ═════════════════════════════════════════════════════════════════════
# 数值序列（指标现值 / 组件就绪度）
# ═════════════════════════════════════════════════════════════════════


def _src_value(holder: Dict[str, Any], kind: str) -> Optional[float]:
    if kind == "metric":
        cur = holder.get("current")
        return cur.get("value") if isinstance(cur, dict) else None
    return holder.get("readiness")


def _trend_of(holder: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
    return holder.get("trend") if kind == "metric" else holder.get("readiness_trend")


def series_value(holder: Dict[str, Any], kind: str = "metric") -> Optional[float]:
    """一个指标/组件的\"当前值\"：引擎推进过就取引擎值，否则取声明的现值/就绪度。"""
    eng = holder.get("engine")
    if isinstance(eng, dict) and "v" in eng:
        return eng["v"]
    return _src_value(holder, kind)


def metric_value(metric: Dict[str, Any]) -> Optional[float]:
    return series_value(metric, "metric")


def component_readiness(component: Dict[str, Any]) -> Optional[float]:
    return series_value(component, "component")


def _pv(trend: Dict[str, Any], name: str) -> Optional[float]:
    p = (trend.get("params") or {}).get(name)
    return p.get("value") if isinstance(p, dict) else None


def _r(x: Any) -> Any:
    return round(x, 9) if isinstance(x, float) else x


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TRACE_TOL * max(1.0, abs(a), abs(b))


def _tri(u: float, low: Optional[float], mode: Optional[float], high: Optional[float]) -> float:
    """三角分布逆 CDF；只给部分参数时：只有 mode → 定值；low+high → mode 取中点。"""
    lo = low if low is not None else (mode if mode is not None else high)
    hi = high if high is not None else (mode if mode is not None else low)
    if lo is None or hi is None:
        return 0.0
    m = mode if mode is not None else (lo + hi) / 2
    if hi <= lo:
        return float(lo)
    c = (m - lo) / (hi - lo)
    if u < c:
        return lo + math.sqrt(max(0.0, u * (hi - lo) * (m - lo)))
    return hi - math.sqrt(max(0.0, (1 - u) * (hi - lo) * (hi - m)))


# ═════════════════════════════════════════════════════════════════════
# 引擎上下文
# ═════════════════════════════════════════════════════════════════════


class _Ctx:
    """一次 `apply_step` 的工作上下文：工作副本（剖面 + 生命周期）、记账、随机源。"""

    def __init__(
        self, settings: Dict[str, Any], *, step: int, sim_id: str, branch: str, vars_: Optional[Dict[str, Any]],
        events: Iterable[Any], stale_ids: Iterable[str], max_trace: Optional[int] = None,
    ) -> None:
        self.settings = settings
        self.max_trace = MAX_TRACE if max_trace is None else int(max_trace)  # A5：二次裁决只能用掉本步剩余的流水额度
        self.params = an.get_params(settings)
        self.step = int(step)
        self.sim_id = str(sim_id or "")
        self.branch = str(branch or "")
        self.vars = vars_ or {}
        self.salt = "anatomy|" + str(settings.get("event_sampling_salt") or "")
        self.common = bool(settings.get("event_sampling_common_random_numbers"))
        self.hit_events: Set[str] = {
            str(e["id"]) for e in events or [] if isinstance(e, dict) and e.get("id") and not e.get("suppressed_by_cap")
        }
        self.stale_ids: Set[str] = {str(x) for x in stale_ids or []}
        self.tech_on = tech_model.is_enabled(settings)
        self.trace: List[Dict[str, Any]] = []
        self.violations: List[Dict[str, Any]] = []
        self._seen: Set[Tuple[str, str]] = set()
        self._dropped = 0
        self.lines: Dict[str, Dict[str, Any]] = {}
        self.work: Dict[str, Dict[str, Any]] = {}
        self.life: Dict[str, Dict[str, Any]] = {}
        self.order: List[str] = []
        self.clock0: Dict[str, float] = {}
        self.clock1: Dict[str, float] = {}
        self.before: Dict[Tuple[str, str], float] = {}

    # —— 记账 ——
    def viol(self, code: str, message: str, severity: str = "warn", **detail: Any) -> None:
        key = (code, message)
        if key in self._seen:
            return
        self._seen.add(key)
        self.violations.append({"code": code, "severity": severity, "message": message, "detail": detail})

    def add(self, element: str, kind: str, ref: str, field: str, before: Any, after: Any, reason: str, source: str, **extra: Any) -> None:
        if len(self.trace) >= self.max_trace:
            self._dropped += 1
            return
        entry: Dict[str, Any] = {
            "element": element, "kind": kind, "ref": ref, "field": field,
            "value_before": _r(before), "value_after": _r(after), "reason": reason, "source": source,
        }
        for k, v in extra.items():
            if v not in (None, "", [], {}):
                entry[k] = v
        self.trace.append(entry)

    # —— 引用解析 ——
    def resolve_item(self, cur_el: str, ref: Any, default_kind: str) -> Optional[Tuple[str, str, Dict[str, Any]]]:
        element, kind, rid = an.parse_ref(ref)
        kind = kind or default_kind
        eid = cur_el
        if element:
            line = er.resolve(self.settings, element)
            if line is None:
                return None
            eid = str(line.get("id") or "").strip()
        part = _PART_OF_KIND.get(kind)
        work = self.work.get(eid)
        if part is None or work is None:
            return None
        for it in work.get(part) or []:
            if it.get("id") == rid:
                return eid, kind, it
        return None

    def find_unique(self, kind: str, rid: str) -> Optional[str]:
        """提议没写 `element` 时，在所有带剖面的元素里找**唯一**含此 id 的那个。"""
        part = _PART_OF_KIND[kind]
        hits = [eid for eid in self.order if any(it.get("id") == rid for it in self.work[eid].get(part) or [])]
        return hits[0] if len(hits) == 1 else None


def _label(ctx: _Ctx, el: str) -> str:
    return str(ctx.lines[el].get("label") or el)


# ═════════════════════════════════════════════════════════════════════
# 条件判据
# ═════════════════════════════════════════════════════════════════════

_CMP = {
    ">=": lambda a, b: a >= b or _close(a, b), ">": lambda a, b: a > b and not _close(a, b),
    "<=": lambda a, b: a <= b or _close(a, b), "<": lambda a, b: a < b and not _close(a, b),
    "==": _close, "!=": lambda a, b: not _close(a, b),
}


def _eval_leaf(ctx: _Ctx, el: str, leaf: Dict[str, Any]) -> bool:
    if "metric" in leaf:
        hit = ctx.resolve_item(el, leaf["metric"], "metric")
        if hit is None:
            ctx.viol("A5", f"判据引用了不存在的指标 {leaf['metric']}", "info")
            return False
        val = metric_value(hit[2])
        return val is not None and _drives(hit[2], ctx.params) and bool(_CMP[leaf["op"]](val, leaf["value"]))
    if "component" in leaf:
        hit = ctx.resolve_item(el, leaf["component"], "component")
        if hit is None:
            ctx.viol("A5", f"判据引用了不存在的组件 {leaf['component']}", "info")
            return False
        val = component_readiness(hit[2])
        return val is not None and _drives(hit[2], ctx.params) and val >= leaf["min_readiness"] - 1e-12
    if "bottleneck" in leaf:
        hit = ctx.resolve_item(el, leaf["bottleneck"], "bottleneck")
        if hit is None:
            ctx.viol("A5", f"判据引用了不存在的瓶颈 {leaf['bottleneck']}", "info")
            return False
        return (hit[2].get("status") or "open") == leaf["status"]
    ok, _why = event_sampler.evaluate_condition(leaf, ctx.vars, ctx.settings)  # var / tech / element 沿用既有求值
    return ok


def _eval_node(ctx: _Ctx, el: str, node: Dict[str, Any]) -> Tuple[bool, int, int]:
    """返回 `(是否满足, 叶子总数, 为真的叶子数)`。"""
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        res = [_eval_node(ctx, el, c) for c in node[key]]
        ok = all(r[0] for r in res) if key == "all" else any(r[0] for r in res)
        return ok, sum(r[1] for r in res), sum(r[2] for r in res)
    if "not" in node:
        ok, total, true = _eval_node(ctx, el, node["not"])
        return (not ok), total, total - true
    ok = _eval_leaf(ctx, el, node)
    return ok, 1, 1 if ok else 0


def evaluate_leaf(settings: Optional[Dict[str, Any]], leaf: Any, vars_: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """给 `event_sampler.evaluate_condition` 的委托入口（计划 §5.3.4：条件语法扩展，事件先验/树分支/门槛共用一套求值器）。

    读**已落盘**的剖面（不是引擎工作副本）。叶子里的 `metric`/`component`/`bottleneck` 写 `<元素>#<子项 id>`；
    没写元素时在所有带剖面的元素里找唯一含该 id 的，找不到或不唯一 → 不满足（保守）。
    `all` / `any` / `not` 节点也能求。**不满足、引用不存在、写法非法一律返回 False + 原因**，不抛异常。
    """
    try:
        norm = an.normalize_criteria(leaf)
        if norm is None:
            return False, "条件写法非法"
        ctx = _Ctx(settings or {}, step=0, sim_id="", branch="", vars_=vars_, events=[], stale_ids=[])
        for line in an.lines_with_anatomy(settings):
            lid = str(line.get("id") or "").strip()
            ctx.lines[lid] = line
            ctx.work[lid] = an.get_anatomy(line) or {}
            ctx.order.append(lid)
        owner = _leaf_owner(ctx, norm)
        ok, _t, _tt = _eval_node(ctx, owner, norm)
        if ok:
            return True, ""
        why = ctx.violations[0]["message"] if ctx.violations else "剖面条件不满足"
        return False, why
    except Exception as exc:  # noqa: BLE001 — 条件求值不能拖垮调用方
        return False, f"剖面条件求值出错：{exc}"


def _leaf_owner(ctx: _Ctx, node: Dict[str, Any]) -> str:
    """没有\"当前元素\"的外部条件：取第一个无元素前缀的叶子引用，按唯一 id 找归属元素；找不到返回空串（引用即不存在）。"""
    for key, kind in (("metric", "metric"), ("component", "component"), ("bottleneck", "bottleneck")):
        if key in node:
            element, k, rid = an.parse_ref(node[key])
            if element:
                return ""
            return ctx.find_unique(k or kind, rid) or ""
    for child in (node.get("all") or node.get("any") or ([node["not"]] if "not" in node else [])):
        owner = _leaf_owner(ctx, child)
        if owner:
            return owner
    return ""


# ═════════════════════════════════════════════════════════════════════
# 指标 / 组件推进
# ═════════════════════════════════════════════════════════════════════


def _ensure_state(holder: Dict[str, Any], kind: str) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
    """保证有引擎状态。返回 `(状态, 重新锚定前的旧引擎值)`；旧值不是 None 表示这一步发生了重新锚定
    （用户/研究改了声明的现值）。没有现值也没有状态 → `(None, None)`。"""
    src = _src_value(holder, kind)
    eng = holder.get("engine") if isinstance(holder.get("engine"), dict) else None
    if src is None:
        return eng, None
    if eng is None:
        holder["engine"] = {"v": src, "t": 0, "a": src, "off": 0, "src": src}
        return holder["engine"], None
    if eng.get("src") != src:
        old = eng["v"]
        holder["engine"] = {"v": src, "t": 0, "a": src, "off": 0, "src": src}
        return holder["engine"], old
    return eng, None


def _bounds_of(holder: Dict[str, Any], kind: str) -> Tuple[Optional[float], Optional[float]]:
    if kind == "component":
        return 0.0, 1.0
    b = holder.get("bounds") or {}
    return b.get("min"), b.get("max")


def _clamp(value: float, lo: Optional[float], hi: Optional[float]) -> float:
    if lo is not None and value < lo:
        return float(lo)
    if hi is not None and value > hi:
        return float(hi)
    return value


def _interp(table: List[List[float]], t: float) -> float:
    if t <= table[0][0]:
        return float(table[0][1])
    if t >= table[-1][0]:
        return float(table[-1][1])
    for (t0, v0), (t1, v1) in zip(table, table[1:]):
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return float(table[-1][1])  # pragma: no cover


def _normal(ctx: _Ctx, el: str, ref: str) -> float:
    u1 = max(event_sampler.draw(ctx.sim_id, ctx.branch, ctx.step, ctx.salt, f"{el}/{ref}/z1", common=ctx.common), 1e-12)
    u2 = event_sampler.draw(ctx.sim_id, ctx.branch, ctx.step, ctx.salt, f"{el}/{ref}/z2", common=ctx.common)
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _model_next(
    ctx: _Ctx, el: str, holder: Dict[str, Any], kind: str, eng: Dict[str, Any], trend: Dict[str, Any], dt: float,
) -> Tuple[Optional[float], str, str]:
    """模型推进一步。返回 `(新值, 来源, 说明)`；新值 None = 引擎不推算（说明是原因）。"""
    kd = trend["kind"]
    v, dy = eng["v"], dt / DAYS_PER_YEAR
    ref = f"{kind}:{holder['id']}"

    def need(*names: str) -> Optional[Dict[str, float]]:
        got = {n: _pv(trend, n) for n in names}
        return got if all(x is not None for x in got.values()) else None

    if kd == "linear":
        p = need("slope_per_year")
        return (v + p["slope_per_year"] * dy, "model", "linear") if p else (None, "model", "缺少参数 slope_per_year")
    if kd == "exponential":
        p = need("rate_per_year")
        if not p:
            return None, "model", "缺少参数 rate_per_year"
        if 1 + p["rate_per_year"] <= 0:
            return None, "model", "rate_per_year ≤ -1，无法按年复合"
        return v * (1 + p["rate_per_year"]) ** dy, "model", "exponential"
    if kd in ("saturating", "mean_reversion"):
        target = "cap" if kd == "saturating" else "mean"
        p = need(target, "rate_per_year")
        if not p:
            return None, "model", f"缺少参数 {target} / rate_per_year"
        return p[target] - (p[target] - v) * math.exp(-p["rate_per_year"] * dy), "model", kd
    if kd == "logistic":
        p = need("cap", "rate_per_year")
        if not p:
            return None, "model", "缺少参数 cap / rate_per_year"
        cap = p["cap"]
        if not 0 < v < cap:
            return v, "model", "logistic 要求 0 < 当前值 < cap，已保持不变"
        return cap / (1 + (cap - v) / v * math.exp(-p["rate_per_year"] * dy)), "model", "logistic"
    if kd == "learning_curve":
        p = need("b")
        driver = trend.get("driver")
        hit = ctx.resolve_item(el, driver, "metric") if driver else None
        if not p or hit is None:
            return None, "model", "learning_curve 需要参数 b 和存在的驱动指标 driver"
        q1 = metric_value(hit[2])
        q0 = eng.get("dq")
        eng["dq"] = q1 if q1 is not None else q0
        if q1 is None or q1 <= 0 or q0 is None or q0 <= 0 or v <= 0:
            return v, "model", "驱动量/当前值非正，学习曲线本步保持不变"
        return v * (q1 / q0) ** (-p["b"]), "model", "learning_curve"
    if kd == "random_walk_drift":
        p = need("drift_per_year")
        if not p:
            return None, "model", "缺少参数 drift_per_year"
        sigma = _pv(trend, "vol_per_sqrt_year") or 0.0
        noise = sigma * math.sqrt(dy) * _normal(ctx, el, ref) if sigma else 0.0
        return v + p["drift_per_year"] * dy + noise, "model", "random_walk_drift"
    if kd == "piecewise_table":
        table = trend.get("table")
        if not table:
            return None, "model", "piecewise_table 缺少 table"
        return _interp(table, eng["t"] + dt) + eng.get("off", 0), "model", "piecewise_table"
    if kd == "custom_expr":
        expr = trend.get("expr")
        if not expr:
            return None, "model", "custom_expr 缺少 expr"
        params = {k: pr["value"] for k, pr in (trend.get("params") or {}).items() if isinstance(pr, dict) and "value" in pr}
        metrics: Dict[str, float] = {}
        for it in (ctx.work.get(el) or {}).get("metrics") or []:
            mv = metric_value(it)
            if mv is not None:
                metrics[it["id"]] = mv
        try:
            val = eval_expr(expr, names={"t": eng["t"] + dt, "v0": eng.get("a", v)}, params=params, metrics=metrics)
        except ExprError as exc:
            return None, "model", f"custom_expr 无法求值：{exc}"
        return val + eng.get("off", 0), "model", "custom_expr"
    if kd == "step_events":
        value, hit_any = v, []
        for ev in trend.get("events") or []:
            if ev["event"] in ctx.hit_events:
                value = value * ev["factor"] if "factor" in ev else value
                value = value + ev["delta"] if "delta" in ev else value
                hit_any.append(ev["event"])
        return value, ("event" if hit_any else "model"), ("事件 " + "、".join(hit_any) if hit_any else "step_events（本步无命中事件）")
    return None, "model", f"趋势 {kd!r} 引擎不认识（llm_reported 或自定义名）"


def _order_metrics(metrics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """驱动指标先于被驱动指标（只看同元素的 `driver`；有环就保持声明顺序）。"""
    by_id = {m["id"]: m for m in metrics}
    out: List[Dict[str, Any]] = []
    state: Dict[str, int] = {}

    def visit(m: Dict[str, Any]) -> bool:
        s = state.get(m["id"], 0)
        if s == 2:
            return True
        if s == 1:
            return False
        state[m["id"]] = 1
        drv = (m.get("trend") or {}).get("driver")
        if drv:
            _el, _k, rid = an.parse_ref(drv)
            dep = by_id.get(rid) if not _el else None
            if dep is not None and dep is not m and not visit(dep):
                state[m["id"]] = 0
                return False
        state[m["id"]] = 2
        out.append(m)
        return True

    for m in metrics:
        if state.get(m["id"], 0) == 0:
            visit(m)
    for m in metrics:  # 有环时漏掉的按声明顺序补上
        if m not in out:
            out.append(m)
    return out


def _advance_series(ctx: _Ctx, el: str, holder: Dict[str, Any], kind: str, dt: float, eng: Dict[str, Any]) -> None:
    trend = _trend_of(holder, kind)
    ref = f"{kind}:{holder['id']}"
    field = "value" if kind == "metric" else "readiness"
    lo, hi = _bounds_of(holder, kind)
    before = eng["v"]
    if not trend or not _drives(holder, ctx.params):
        eng["t"] = eng.get("t", 0) + dt
        return
    evidence = (an.basis_of(holder.get("current") if kind == "metric" else holder).get("evidence_ids") or [])
    if kind == "metric" and evidence and ctx.stale_ids.intersection(evidence) and not eng.get("sf"):
        eng["sf"] = True
        ctx.viol("A9", f"指标「{holder.get('name') or holder['id']}」的现值证据已过期，仍在驱动引擎", "info", ref=ref)
    new, source, note = _model_next(ctx, el, holder, kind, eng, trend, dt)
    if new is None:
        if not eng.get("na"):
            eng["na"] = True
            ctx.viol("A10", f"{_label(ctx, el)}/{holder.get('name') or holder['id']}：{note}，引擎不推算（只记录 LLM 报告值）", "info", ref=ref)
        eng["t"] = eng.get("t", 0) + dt
        return
    if eng.get("na"):
        eng.pop("na", None)  # 补全了参数/趋势 → 恢复推算
    new = float(new)
    if not math.isfinite(new):
        new = before
        note = f"{note}（结果非有限数，已保持不变）"
    clamped = _clamp(new, lo, hi)
    eng["t"] = eng.get("t", 0) + dt
    if not _close(new, before):
        ctx.add(el, kind, ref, field, before, new, note, source, evidence_ids=evidence)
    if not _close(clamped, new):
        ctx.add(el, kind, ref, field, new, clamped, f"超出声明边界 [{lo}, {hi}]，已夹值", "clamp")
        ctx.viol("A4", f"{_label(ctx, el)}/{holder.get('name') or holder['id']} 推算值 {new:g} 超出边界，已夹到 {clamped:g}", "info", ref=ref)
    eng["v"] = clamped


def _advance_all(ctx: _Ctx, days: float) -> None:
    for el in ctx.order:
        a = ctx.work[el]
        for comp in a.get("components") or []:
            eng = comp.get("engine")
            if eng:
                _advance_series(ctx, el, comp, "component", days, eng)
        for met in _order_metrics(a.get("metrics") or []):
            eng = met.get("engine")
            if eng:
                _advance_series(ctx, el, met, "metric", days, eng)


# ═════════════════════════════════════════════════════════════════════
# 瓶颈：解决路径抽样与结算
# ═════════════════════════════════════════════════════════════════════


def _path_order(bn: Dict[str, Any]) -> List[str]:
    declared = [p["id"] for p in bn.get("resolution_paths") or []]
    first = [x for x in bn.get("fallback") or [] if x in declared]
    return first + [x for x in declared if x not in first]


def _refresh_exhausted(ctx: _Ctx, el: str, bn: Dict[str, Any]) -> None:
    paths = bn.get("resolution_paths") or []
    if bn.get("status", "open") == "open" and paths and all(p.get("status") == "failed" for p in paths):
        bn["status"] = "exhausted"
        ctx.add(el, "bottleneck", f"bottleneck:{bn['id']}", "status", "open", "exhausted", "所有解决路径都已失败", "model")


def _start_paths(ctx: _Ctx, el: str, bn: Dict[str, Any], at_day: float) -> None:
    if (bn.get("status") or "open") != "open" or not _drives(bn, ctx.params):
        return
    paths = bn.get("resolution_paths") or []
    if not paths or any(p.get("status") == "running" for p in paths):
        return
    by_id = {p["id"]: p for p in paths}
    for pid in _path_order(bn):
        p = by_id[pid]
        if p.get("status") not in (None, "pending"):
            continue
        if p.get("requires") and not _eval_node(ctx, el, p["requires"])[0]:
            continue
        dur = p.get("duration_days")
        if not dur:
            ctx.viol("A10", f"{_label(ctx, el)}/瓶颈「{bn.get('name') or bn['id']}」的路径「{pid}」缺少 duration_days，引擎无法调度", "info")
            continue
        key = f"{el}/{bn['id']}/{pid}"
        u_ok = event_sampler.draw(ctx.sim_id, ctx.branch, ctx.step, ctx.salt, key + "/ok", common=ctx.common)
        u_dur = event_sampler.draw(ctx.sim_id, ctx.branch, ctx.step, ctx.salt, key + "/dur", common=ctx.common)
        p_success = p.get("p_success", 1.0)
        days = _tri(u_dur, dur.get("low"), dur.get("mode"), dur.get("high"))
        p.update({
            "status": "running", "outcome": "success" if u_ok < p_success else "fail",
            "started_day": at_day, "started_step": ctx.step, "resolve_at_day": at_day + days,
        })
        ctx.add(
            el, "bottleneck", f"bottleneck:{bn['id']}", "path", "pending", f"running:{pid}",
            f"路径「{pid}」启动，预计约 {days:.0f} 天后到期", "model", path=pid,
        )
        return
    _refresh_exhausted(ctx, el, bn)


def _start_all(ctx: _Ctx, at_day_of: Callable[[str], float]) -> None:
    for el in ctx.order:
        for bn in ctx.work[el].get("bottlenecks") or []:
            _start_paths(ctx, el, bn, at_day_of(el))


def _settle_paths(ctx: _Ctx) -> None:
    for el in ctx.order:
        c1 = ctx.clock1[el]
        for bn in ctx.work[el].get("bottlenecks") or []:
            for _ in range(len(bn.get("resolution_paths") or []) + 1):
                running = next((p for p in bn.get("resolution_paths") or [] if p.get("status") == "running"), None)
                if running is None or running.get("resolve_at_day", math.inf) > c1 + 1e-9:
                    break
                due, pid = running["resolve_at_day"], running["id"]
                ref = f"bottleneck:{bn['id']}"
                if running.get("outcome") == "success":
                    running["status"] = "succeeded"
                    bn.update({"status": "resolved", "resolved_step": ctx.step, "resolved_day": due, "resolved_by": pid})
                    ctx.add(el, "bottleneck", ref, "status", "open", "resolved", f"路径「{pid}」到期并成功", "model", path=pid)
                    break
                running["status"] = "failed"
                ctx.add(el, "bottleneck", ref, "path", f"running:{pid}", f"failed:{pid}", f"路径「{pid}」到期但失败", "model", path=pid)
                _start_paths(ctx, el, bn, due)
                if (bn.get("status") or "open") != "open":
                    break


# ═════════════════════════════════════════════════════════════════════
# 里程碑 / 门槛 / 阶段 / 采用率
# ═════════════════════════════════════════════════════════════════════


def _settle_milestones(ctx: _Ctx) -> None:
    for el in ctx.order:
        for ms in ctx.work[el].get("milestones") or []:
            if "reached_step" in ms or not ms.get("criteria") or not _drives(ms, ctx.params):
                continue
            if _eval_node(ctx, el, ms["criteria"])[0]:
                ms["reached_step"], ms["reached_sim_day"] = ctx.step, ctx.clock1[el]
                ctx.add(el, "milestone", f"milestone:{ms['id']}", "reached", False, True, f"里程碑「{ms.get('name') or ms['id']}」的判据已满足", "model")


def _settle_gates(ctx: _Ctx) -> None:
    for el in ctx.order:
        for gate in ctx.work[el].get("adoption_gates") or []:
            if not gate.get("criteria") or not _drives(gate, ctx.params):
                continue
            ok = _eval_node(ctx, el, gate["criteria"])[0]
            prev = gate.get("open")
            gate["open"] = ok
            if ok and "opened_step" not in gate:
                gate["opened_step"] = ctx.step
            if prev is not None and prev != ok or (prev is None and ok):
                ctx.add(el, "gate", f"adoption_gate:{gate['id']}", "open", bool(prev), ok, "判据满足，门槛打开" if ok else "判据不再满足，门槛关闭", "model")


def _stage_of_milestone(ctx: _Ctx, el: str, ms: Dict[str, Any]) -> Optional[int]:
    name = ms.get("maps_to_stage")
    if not name:
        return None
    idx = tech_model.stage_index(name)
    if idx is None:
        ctx.viol("A5", f"{_label(ctx, el)}/里程碑「{ms.get('name') or ms['id']}」的 maps_to_stage={name!r} 不是合法阶段名", "info")
    return idx


def drives_stage(ctx_params: Dict[str, Any], anatomy: Optional[Dict[str, Any]], lifecycle: Any) -> bool:
    """该元素的阶段是否由里程碑派生：开关开启、有生命周期（技术节点）、且至少一个里程碑带 `maps_to_stage`。"""
    return bool(
        ctx_params.get("anatomy_drives_stage") and isinstance(lifecycle, dict)
        and any(m.get("maps_to_stage") for m in (anatomy or {}).get("milestones") or [])
    )


def _settle_stage(ctx: _Ctx, pre: Dict[str, Dict[str, Any]]) -> None:
    stages = tech_model.STAGES
    for el in ctx.order:
        life = ctx.life.get(el)
        a = ctx.work[el]
        if not ctx.tech_on or not drives_stage(ctx.params, a, life):
            continue
        now_idx = tech_model.stage_index(life.get("stage")) or 0
        info = pre.get(el) or {}
        pre_idx = tech_model.stage_index(info.get("stage")) if info.get("stage") else now_idx
        pre_idx = now_idx if pre_idx is None else pre_idx
        reached = [i for i in (_stage_of_milestone(ctx, el, m) for m in a.get("milestones") or [] if "reached_step" in m) if i is not None]
        reached_idx = max(reached) if reached else -1
        name = _label(ctx, el)
        if now_idx != pre_idx and (now_idx < pre_idx or reached_idx < now_idx):
            claimed = life.get("stage")
            for key in ("stage", "progress", "dwell_days", "last_transition_step", "stalled_steps"):
                if key in info:
                    life[key] = info[key]
                else:
                    life.pop(key, None)  # 本步迁移新写的键（本来就没有）也要还原
            ctx.viol(
                "A3",
                f"{name}：声明阶段 {info.get('stage')} → {claimed}，但没有对应里程碑达成（有剖面的元素阶段由里程碑派生，"
                "里程碑只增不减），已驳回并回滚",
                "warn", claimed=claimed, from_stage=info.get("stage"),
            )
            ctx.add(el, "stage", "stage", "stage", info.get("stage"), claimed, "阶段声明缺少里程碑支撑，已驳回", "rejected")
            now_idx = pre_idx
        target = max(now_idx, reached_idx)
        if target > now_idx:
            before_stage = life.get("stage")
            life.update({"stage": stages[target], "progress": 0.0, "dwell_days": 0.0, "stalled_steps": 0, "last_transition_step": ctx.step})
            ctx.add(el, "stage", "stage", "stage", before_stage, stages[target], "由已达成的里程碑派生", "milestone")
            now_idx = target
        life["progress"] = round(_derived_progress(ctx, el, a, now_idx), 6)


def _derived_progress(ctx: _Ctx, el: str, a: Dict[str, Any], now_idx: int) -> float:
    """下一档里最接近达成的里程碑已满足的叶子占比（< 1；没有下一档目标则 0）。"""
    best = 0.0
    for m in a.get("milestones") or []:
        if "reached_step" in m or not m.get("criteria") or _stage_of_milestone(ctx, el, m) != now_idx + 1:
            continue
        _ok, total, true = _eval_node(ctx, el, m["criteria"])
        if total:
            best = max(best, true / total)
    return min(0.99, best)


def _settle_adoption(ctx: _Ctx, pre: Dict[str, Dict[str, Any]]) -> None:
    for el in ctx.order:
        life = ctx.life.get(el)
        if not ctx.tech_on or not isinstance(life, dict) or life.get("adoption") is None:
            continue
        market = str(life.get("market") or "").strip().lower()
        gates = [
            g for g in ctx.work[el].get("adoption_gates") or []
            if (g.get("unlocks") or {}).get("adoption_cap") is not None and g.get("criteria")
            and (not g.get("market") or str(g["market"]).strip().lower() == market)
        ]
        if not gates:
            continue
        cap = max([g["unlocks"]["adoption_cap"] for g in gates if g.get("open")] or [0.0])
        now, before = life["adoption"], (pre.get(el) or {}).get("adoption")
        if now > cap + 1e-12 and (before is None or now > before + 1e-12):
            limit = max(cap, before or 0.0)
            life["adoption"] = limit
            ctx.viol("A7", f"{_label(ctx, el)}：采用率 {now:.2f} 超过已打开门槛允许的上限 {cap:.2f}，已夹到 {limit:.2f}", "warn", claimed=now, cap=cap)
            ctx.add(el, "adoption", "adoption", "adoption", now, limit, "采用率超过门槛上限，已夹值", "clamp")


# ═════════════════════════════════════════════════════════════════════
# LLM 提议的裁决（`anatomy_updates`）
# ═════════════════════════════════════════════════════════════════════


def _items(raw: Any, key: str) -> List[Dict[str, Any]]:
    block = raw.get(key) if isinstance(raw, dict) else None
    return [x for x in block if isinstance(x, dict)] if isinstance(block, list) else []


def _target_el(ctx: _Ctx, prop: Dict[str, Any], kind: str, rid: str) -> Optional[str]:
    ref = prop.get("element")
    if ref:
        line = er.resolve(ctx.settings, ref)
        eid = str(line.get("id") or "").strip() if line else ""
        return eid if eid in ctx.work else None
    return ctx.find_unique(kind, rid)


def _find(ctx: _Ctx, el: Optional[str], part: str, rid: str) -> Optional[Dict[str, Any]]:
    if el is None:
        return None
    return next((x for x in ctx.work[el].get(part) or [] if x.get("id") == rid), None)


def _apply_deviations(ctx: _Ctx, raw: Any) -> None:
    for prop in _items(raw, "metric_deviations"):
        rid = str(prop.get("metric") or "").strip()
        el = _target_el(ctx, prop, "metric", rid)
        met = _find(ctx, el, "metrics", rid)
        obs = an._num(prop.get("value"))
        if met is None or obs is None:
            ctx.viol("A5", f"metric_deviations 引用了不存在的元素/指标 {prop.get('element')!r}/{rid!r}（或值不是数字）", "info")
            continue
        eng = met.get("engine")
        reason = str(prop.get("reason") or "").strip()
        cause = str(prop.get("cause_ref") or "").strip()
        ref, name = f"metric:{rid}", f"{_label(ctx, el)}/{met.get('name') or rid}"
        lo, hi = _bounds_of(met, "metric")
        has_model = bool(eng) and bool(met.get("trend")) and not eng.get("na") and _drives(met, ctx.params)
        if eng is None:
            eng = met["engine"] = {"v": obs, "t": 0, "a": obs, "off": 0}
            ctx.before.setdefault((el, ref), obs)
        model = eng["v"]
        value = _clamp(float(obs), lo, hi)
        if not _close(value, float(obs)):
            ctx.viol("A4", f"{name} 报告值 {obs:g} 超出边界，已夹到 {value:g}", "warn", ref=ref)
        if not has_model:
            if not _close(value, model):
                eng["v"] = value
                ctx.add(el, "metric", ref, "value", model, value, reason or "LLM 报告值（该指标没有引擎模型）", "llm_reported", cause_ref=cause)
            continue
        dev = abs(value - model) / max(abs(model), abs(value), 1e-9)
        if dev > DEVIATION_THRESHOLD and not (reason or cause):
            ctx.viol(
                "A1", f"{name}：报告值 {value:g} 偏离引擎模型值 {model:g}（{dev:.0%}）却没有原因/cause_ref，保留引擎值",
                "warn", ref=ref, model=model, reported=value,
            )
            continue
        if _close(value, model):
            continue
        policy = ctx.params.get("deviation_policy", "rebase")
        why = reason or ("小幅偏离（未给原因）" if dev <= DEVIATION_THRESHOLD else "")
        if policy == "keep_model":
            ctx.add(el, "metric", ref, "value", model, model, f"偏离已记录未采纳（keep_model）：{why}", "deviation", reported_value=value, cause_ref=cause)
            continue
        if (met.get("trend") or {}).get("kind") in _ABSOLUTE_KINDS:
            eng["off"] = eng.get("off", 0) + (value - model)
        eng["v"] = value
        ctx.add(el, "metric", ref, "value", model, value, why, "deviation", policy="rebase", cause_ref=cause)


def _apply_shifts(ctx: _Ctx, raw: Any) -> None:
    for prop in _items(raw, "bottleneck_proposals"):
        if an._num(prop.get("shift_days")) is None:
            continue
        rid = str(prop.get("bottleneck") or "").strip()
        el = _target_el(ctx, prop, "bottleneck", rid)
        bn = _find(ctx, el, "bottlenecks", rid)
        if bn is None:
            ctx.viol("A5", f"bottleneck_proposals 引用了不存在的元素/瓶颈 {prop.get('element')!r}/{rid!r}", "info")
            continue
        reason, cause = str(prop.get("reason") or "").strip(), str(prop.get("cause_ref") or "").strip()
        running = next((p for p in bn.get("resolution_paths") or [] if p.get("status") == "running"), None)
        name = f"{_label(ctx, el)}/瓶颈「{bn.get('name') or rid}」"
        if running is None or not (reason or cause):
            ctx.viol(
                "A2", f"{name}：要求把到期日平移 {prop.get('shift_days')} 天，但" + ("没有进行中的路径" if running is None else "没有原因/cause_ref"),
                "warn", shift_days=prop.get("shift_days"),
            )
            continue
        old = running["resolve_at_day"]
        new = max(ctx.clock0[el], old + float(an._num(prop["shift_days"])))
        if not _close(new, old):
            running["resolve_at_day"] = new
            ctx.add(el, "bottleneck", f"bottleneck:{rid}", "resolve_at_day", old, new, reason or "外部因素改变了到期日", "deviation", path=running["id"], cause_ref=cause)


def _check_claims(ctx: _Ctx, raw: Any) -> None:
    for prop in _items(raw, "bottleneck_proposals"):
        if prop.get("status") != "resolved":
            continue
        rid = str(prop.get("bottleneck") or "").strip()
        el = _target_el(ctx, prop, "bottleneck", rid)
        bn = _find(ctx, el, "bottlenecks", rid)
        if bn is None:
            ctx.viol("A5", f"bottleneck_proposals 引用了不存在的元素/瓶颈 {prop.get('element')!r}/{rid!r}", "info")
            continue
        if (bn.get("status") or "open") == "resolved":
            continue
        running = next((p for p in bn.get("resolution_paths") or [] if p.get("status") == "running"), None)
        due = f"，路径「{running['id']}」预计第 {running['resolve_at_day']:.0f} 天到期（当前第 {ctx.clock1[el]:.0f} 天）" if running else ""
        ctx.viol("A2", f"{_label(ctx, el)}/瓶颈「{bn.get('name') or rid}」被声称已解决，但引擎判定路径未到期{due}，已驳回", "warn", bottleneck=rid)
    for prop in _items(raw, "milestone_claims"):
        rid = str(prop.get("milestone") or "").strip()
        el = _target_el(ctx, prop, "milestone", rid)
        ms = _find(ctx, el, "milestones", rid)
        if ms is None:
            ctx.viol("A5", f"milestone_claims 引用了不存在的元素/里程碑 {prop.get('element')!r}/{rid!r}", "info")
            continue
        if "reached_step" in ms:
            continue
        if not ms.get("criteria"):  # 没有判据 = 引擎无从核验，只能采纳 LLM 的提议（体检里已有 no_criteria 提示）
            ms["reached_step"], ms["reached_sim_day"] = ctx.step, ctx.clock1[el]
            ctx.add(el, "milestone", f"milestone:{rid}", "reached", False, True, str(prop.get("reason") or "").strip() or "LLM 提议（该里程碑没有可核验的判据）", "llm_reported")
            continue
        ctx.viol("A3", f"{_label(ctx, el)}/里程碑「{ms.get('name') or rid}」被声称已达成，但判据未满足，已驳回", "warn", milestone=rid)
    for prop in _items(raw, "engine_edits"):
        ctx.viol("A6", f"试图修改引擎持有的字段 {prop.get('ref')!r}/{prop.get('field')!r}，已忽略", "info", ref=prop.get("ref"), field=prop.get("field"))


_SUBITEM_PARTS = {"component": "components", "metric": "metrics", "bottleneck": "bottlenecks"}


def _norm_text(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def _el_of(ctx: _Ctx, ref: Any) -> str:
    """提议里的 `element` 解析成线 id；解析不了或不在本次结算范围内返回空串。"""
    line = er.resolve(ctx.settings, ref) if ref else None
    eid = str(line.get("id") or "").strip() if line else ""
    return eid if eid in ctx.work else ""


def _downgrade_review(ctx: _Ctx, el: str) -> None:
    """元素里多了一条没人审阅过的新条目：原来的 `reviewed`/`verified` 不再成立，退回 `draft`。"""
    meta = dict(ctx.work[el].get("meta") or {})
    if meta.get("anatomy_status") in ("reviewed", "verified"):
        meta["anatomy_status"] = "draft"
        ctx.work[el]["meta"] = meta


def _apply_new_subitems(ctx: _Ctx, raw: Any) -> None:
    """A5：LLM 在推进中发现了剖面里没有的组件/指标/瓶颈（`new_subitems`）。**引擎持有结构**，所以只有同时满足下面这些才接受：
    元素存在、类型在 component/metric/bottleneck 之内、带 `item` 与 `reason`/`cause_ref`、规整后形状合法、id 不与已有的重复
    （**不覆盖**）、没超过该部分的条数上限、没超过每元素每次 `MAX_NEW_SUBITEMS`。接受后：来源一律 `llm_prior`（没有出处），
    引擎状态一律剥掉，瓶颈一律 `open`；原来是 `reviewed` 的元素退回 `draft`。不满足的写 `A12`（引用错误是 `A5`），不阻断。"""
    accepted: Dict[str, int] = {}
    for prop in _items(raw, "new_subitems"):
        kind = str(prop.get("part") or prop.get("kind") or "").strip().lower()
        part = _SUBITEM_PARTS.get(kind)
        el = _el_of(ctx, prop.get("element"))
        if part is None or not el:
            ctx.viol("A5", f"new_subitems 引用了不存在的元素或不支持的类型 {prop.get('element')!r}/{kind!r}（只支持 component/metric/bottleneck）", "info")
            continue
        name = _label(ctx, el)
        item = prop.get("item")
        reason, cause = str(prop.get("reason") or "").strip(), str(prop.get("cause_ref") or "").strip()
        label = (item.get("id") or item.get("name")) if isinstance(item, dict) else None
        if not isinstance(item, dict):
            ctx.viol("A12", f"{name}：新增{kind}缺少 item，已忽略", "info")
            continue
        if not (reason or cause):
            ctx.viol("A12", f"{name}：新增{kind}「{label}」没有 reason/cause_ref（新结构必须说明依据），已忽略", "warn", part=part)
            continue
        if accepted.get(el, 0) >= MAX_NEW_SUBITEMS:
            ctx.viol("A12", f"{name}：本次新增子项已达上限 {MAX_NEW_SUBITEMS} 个，「{label}」已忽略", "info", part=part)
            continue
        norm, notes = an.normalize_anatomy_report({part: [item]})
        got = norm.get(part) or []
        if not got:
            ctx.viol("A12", f"{name}：新增{kind}「{label}」形状不合法（{notes[0] if notes else '规整后为空'}），已忽略", "warn", part=part)
            continue
        new = got[0]
        existing = ctx.work[el].setdefault(part, [])
        if any(x.get("id") == new["id"] for x in existing):
            ctx.viol("A12", f"{name}：新增{kind}的 id「{new['id']}」已存在，不覆盖，已忽略", "info", part=part)
            continue
        if len(existing) >= an.MAX_ITEMS[part]:
            ctx.viol("A12", f"{name}：{part} 已达条数上限 {an.MAX_ITEMS[part]}，「{new['id']}」已忽略", "info", part=part)
            continue
        tmp = {part: [new]}
        for carrier in an._basis_carriers(tmp):  # 没有出处：来源一律回到默认的 llm_prior
            carrier.pop("basis", None)
        an.strip_engine_state(tmp)
        if part == "bottlenecks":
            if item.get("status") not in (None, "open"):
                ctx.viol("A12", f"{name}：新增瓶颈「{new['id']}」声明了状态 {item.get('status')!r}，新瓶颈只能是 open", "info", part=part)
            new["status"] = "open"
        existing.append(new)
        accepted[el] = accepted.get(el, 0) + 1
        if part in ("metrics", "components"):
            eng, _old = _ensure_state(new, "metric" if part == "metrics" else "component")
            if eng is not None:
                ctx.before[(el, f"{'metric' if part == 'metrics' else 'component'}:{new['id']}")] = eng["v"]
        _downgrade_review(ctx, el)
        ctx.add(el, "subitem", f"{kind}:{new['id']}", "created", None, new.get("name") or new["id"],
                reason or "LLM 提议新增（依据见 cause_ref）", "proposed", cause_ref=cause)


def _apply_signals(ctx: _Ctx, raw: Any) -> None:
    """A5：LLM 提议的先行信号（`signals`：现实里该盯什么 / 先发生什么意味着什么）。只追加，同样的 `watch` 不重复，
    来源 `llm_prior`；每元素每次最多 `MAX_NEW_SIGNALS` 条、总数不超过 `MAX_ITEMS["signals"]`。"""
    accepted: Dict[str, int] = {}
    for prop in _items(raw, "signals"):
        el = _el_of(ctx, prop.get("element"))
        if not el:
            ctx.viol("A5", f"signals 引用了不存在的元素 {prop.get('element')!r}", "info")
            continue
        name = _label(ctx, el)
        watch = " ".join(str(prop.get("watch") or "").split())[: an.MAX_TEXT_LEN]
        if not watch:
            ctx.viol("A12", f"{name}：信号缺少 watch（现实里该盯什么），已忽略", "info")
            continue
        sigs = ctx.work[el].setdefault("signals", [])
        if any(_norm_text(x.get("watch")) == _norm_text(watch) for x in sigs):
            ctx.viol("A12", f"{name}：已有同样的先行信号「{watch[:30]}」，已忽略", "info")
            continue
        if accepted.get(el, 0) >= MAX_NEW_SIGNALS or len(sigs) >= an.MAX_ITEMS["signals"]:
            ctx.viol("A12", f"{name}：先行信号已达本次上限或总数上限，「{watch[:30]}」已忽略", "info")
            continue
        used = {x.get("id") for x in sigs}
        n = 1
        while f"sig_{n}" in used:
            n += 1
        obj: Dict[str, Any] = {"id": f"sig_{n}", "watch": watch}
        if prop.get("means"):
            obj["means"] = prop.get("means")
        norm, _notes = an.normalize_anatomy_report({"signals": [obj]})
        got = norm.get("signals") or []
        if not got:
            ctx.viol("A12", f"{name}：先行信号「{watch[:30]}」规整后为空，已忽略", "info")
            continue
        sigs.append(got[0])
        accepted[el] = accepted.get(el, 0) + 1
        _downgrade_review(ctx, el)
        ctx.add(el, "subitem", f"signal:{got[0]['id']}", "created", None, watch, "LLM 提议的先行信号", "proposed")


# ═════════════════════════════════════════════════════════════════════
# 每步结算
# ═════════════════════════════════════════════════════════════════════


def stale_evidence_ids(settings: Optional[Dict[str, Any]], records: Optional[Iterable[Dict[str, Any]]]) -> Set[str]:
    """已过期（现实天数 > `research_ttl_days`）的证据 id（A9 用）。没有记录就是空集；出错也是空集。"""
    try:
        from world_simulator import evidence

        ttl = an.get_params(settings)["research_ttl_days"]
        return {str(r["ev_id"]) for r in records or [] if isinstance(r, dict) and r.get("ev_id") and evidence.is_stale(r, ttl=ttl)}
    except Exception:  # noqa: BLE001
        return set()


def capture_pre(settings: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """技术裁决**之前**的生命周期快照（A3 回滚 / A7 采用率增量判断用）。只记带剖面的元素。"""
    out: Dict[str, Dict[str, Any]] = {}
    for line in an.lines_with_anatomy(settings):
        life = line.get("lifecycle")
        if isinstance(life, dict):
            out[str(line.get("id") or "").strip()] = {
                k: copy.deepcopy(life.get(k))
                for k in ("stage", "progress", "dwell_days", "last_transition_step", "stalled_steps", "adoption")
                if k in life
            }
    return out


def _check_trace(ctx: _Ctx) -> None:
    """流水自洽核对（计划 §5.7）：同一指标的流水首尾衔接、起点 = 步初值、终点 = 步末引擎值。不一致写违规，不静默修正。"""
    chains: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for e in ctx.trace:
        if e["kind"] in ("metric", "component"):
            chains.setdefault((e["element"], e["ref"]), []).append(e)
    for (el, ref), rows in chains.items():
        start = ctx.before.get((el, ref))
        prev = start
        for e in rows:
            if prev is not None and not _close(float(e["value_before"]), prev):
                ctx.viol("A11", f"{el}/{ref} 的流水不衔接：期望起点 {prev:g}，记录为 {e['value_before']}", "warn", ref=ref)
                break
            prev = float(e["value_after"])
        item = _find_series(ctx, el, ref)
        if item is not None and prev is not None:
            final = series_value(item[0], item[1])
            if final is not None and not _close(final, prev):
                ctx.viol("A11", f"{el}/{ref} 流水终点 {prev:g} 与引擎当前值 {final:g} 不一致", "warn", ref=ref)


def _find_series(ctx: _Ctx, el: str, ref: str) -> Optional[Tuple[Dict[str, Any], str]]:
    kind, _, rid = ref.partition(":")
    part = "metrics" if kind == "metric" else "components"
    for it in ctx.work[el].get(part) or []:
        if it.get("id") == rid:
            return it, kind
    return None


def _load_lines(ctx: _Ctx, settings: Dict[str, Any], days: float, *, skip_settled: bool) -> None:
    """把带可结算内容的元素线装进工作副本。`skip_settled=True`（`apply_step`）跳过本步已结算过的线（防同一步重复结算）；
    `False`（`apply_adjudication`）装全部——二次裁决要看到完整的世界（跨元素判据、门槛），不能只装一个元素。"""
    for line in _engine_lines(settings):
        lid = str(line.get("id") or "").strip()
        meta = (an.get_anatomy(line) or {}).get("meta") or {}
        if skip_settled and meta.get("last_step") == ctx.step:
            continue  # 同一步已结算过（两条推进路径不会重复结算）
        ctx.lines[lid] = line
        ctx.work[lid] = copy.deepcopy(an.get_anatomy(line))
        if isinstance(line.get("lifecycle"), dict):
            ctx.life[lid] = copy.deepcopy(line["lifecycle"])
        ctx.order.append(lid)
        ctx.clock0[lid] = float(meta.get("clock_day") or 0.0)
        ctx.clock1[lid] = ctx.clock0[lid] + days


def _anchor_all(ctx: _Ctx) -> None:
    """① 初始化 / 重新锚定：保证每个指标/组件都有引擎状态；现值被用户/研究改过则重新锚定并留一条 `anchor` 流水。"""
    for el in ctx.order:
        a = ctx.work[el]
        for part, kind in (("components", "component"), ("metrics", "metric")):
            for holder in a.get(part) or []:
                eng, old = _ensure_state(holder, kind)
                if eng is None:
                    continue
                ref = f"{kind}:{holder['id']}"
                if old is not None and not _close(old, eng["v"]):
                    ctx.add(el, kind, ref, "value" if kind == "metric" else "readiness", old, eng["v"],
                            "声明的现值被更新（用户编辑 / 联网研究），引擎重新锚定", "anchor")
                    ctx.before[(el, ref)] = old
                else:
                    ctx.before[(el, ref)] = eng["v"]


def apply_step(
    settings: Dict[str, Any],
    proposals: Any = None,
    *,
    step: int,
    elapsed_days_raw: Any = None,
    pre: Optional[Dict[str, Dict[str, Any]]] = None,
    sim_id: str = "",
    branch: str = "",
    vars_: Optional[Dict[str, Any]] = None,
    sampled_events: Optional[List[Dict[str, Any]]] = None,
    stale_evidence_ids: Optional[Iterable[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """结算一步，就地更新 `settings["causal_lines"]` 上各元素的 `anatomy`（以及技术元素的 `lifecycle`）。
    返回 `(流水, 违规)`。未开启 / 没有可结算的元素返回两个空列表。**所有计算在工作副本上完成，最后一次性写回**。

    结算顺序：①初始化/重新锚定 → ②在步初启动可启动的路径 → ③推进指标与组件 → ④LLM 偏离 → ⑤到期日平移 →
    ⑥结算到期路径 → ⑦在步末启动新可启动的路径 → ⑧里程碑与门槛 → ⑨阶段（A3）与采用率（A7）→ ⑩记账与自洽核对。
    """
    if not is_active(settings):
        return [], []
    pre = pre or {}
    ctx = _Ctx(
        settings, step=step, sim_id=sim_id, branch=branch, vars_=vars_, events=sampled_events or [],
        stale_ids=stale_evidence_ids or [],
    )
    days, source, note = tech_model.resolve_elapsed(elapsed_days_raw, tech_model.get_params(settings))
    time_entry: Dict[str, Any] = {
        "element": "", "kind": "time", "ref": "elapsed", "field": "elapsed_days", "value_before": None,
        "value_after": _r(days), "reason": note or ("LLM 报告的跨度" if source == "reported" else "LLM 没有给 elapsed_days，按占位天数估计，时间精度降级"),
        "source": source,
    }
    _load_lines(ctx, settings, days, skip_settled=True)
    if not ctx.order:
        return [], []
    ctx.trace.append(time_entry)
    if source == "fallback":
        ctx.viol("A10", "本步没有可用的 elapsed_days，按占位天数结算，预测区间会标注时间精度降级", "info", elapsed_source=source)

    # ① 初始化 / 重新锚定
    _anchor_all(ctx)
    # ② – ⑧
    _start_all(ctx, lambda el: ctx.clock0[el])
    _advance_all(ctx, days)
    _apply_deviations(ctx, proposals)
    _apply_shifts(ctx, proposals)
    _settle_paths(ctx)
    _start_all(ctx, lambda el: ctx.clock1[el])
    _settle_milestones(ctx)
    _settle_gates(ctx)
    # ⑨
    _settle_stage(ctx, pre)
    _settle_adoption(ctx, pre)
    _check_claims(ctx, proposals)
    _apply_new_subitems(ctx, proposals)   # A5：新增子项 / 先行信号只追加，不改已有结构
    _apply_signals(ctx, proposals)
    # ⑩
    for el in ctx.order:
        meta = dict(ctx.work[el].get("meta") or {})
        meta["clock_day"], meta["last_step"] = ctx.clock1[el], ctx.step
        ctx.work[el]["meta"] = meta
    _check_trace(ctx)
    if ctx._dropped:
        ctx.viol("A11", f"本步流水超过上限 {MAX_TRACE} 条，多出的 {ctx._dropped} 条未记录", "warn")
    # 写回（全部算完才动原数据）
    for el in ctx.order:
        ctx.lines[el]["anatomy"] = ctx.work[el]
        if el in ctx.life:
            ctx.lines[el]["lifecycle"] = ctx.life[el]
    return ctx.trace, ctx.violations


def safe_apply_step(*args: Any, **kwargs: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """`apply_step()` 的兜底版，给 `advance()` 用：任何异常都不连累本次推进，返回空流水 + 一条 info 级 `A0`。
    `apply_step()` 只在最后才写回，所以异常时剖面保持原样。"""
    try:
        return apply_step(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — 旁路功能，绝不让它中断推进
        return [], [{"code": "A0", "severity": "info", "message": f"剖面引擎本步出错，已跳过（状态未改动）：{exc}", "detail": {}}]


def apply_adjudication(
    settings: Dict[str, Any],
    proposals: Any = None,
    *,
    step: int,
    sim_id: str = "",
    branch: str = "",
    vars_: Optional[Dict[str, Any]] = None,
    sampled_events: Optional[List[Dict[str, Any]]] = None,
    trace_limit: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """**二次裁决**（A5 深度调用用）：本步已经 `apply_step` 结算过之后，再对一份提议做一遍**同样的裁决**，**不推进时间**。

    和 `apply_step` 的区别只有两处：① 时间跨度为 0（不推进指标/组件）；② 不写 `meta.last_step`/`clock_day`（时间已经由 `apply_step` 推进过了）。
    裁决规则、`A` 码、流水自洽核对完全共用——**深度模式没有特权**（计划 §5.5.2）。提议里的偏离/平移/声称/新增子项/信号被裁决后，
    再把由此引起的后果（到期的路径、里程碑、门槛、阶段、采用率）重新结算一遍。装进工作副本的是**全部**可结算元素（跨元素判据、
    门槛需要完整的世界），所以对没有被提议触及的元素这是空操作。

    `trace_limit`：本次可用的流水额度（调用方传 `MAX_TRACE - 本步已有流水数`，保证同一步总流水不超上限）。所有计算在副本上完成，最后才写回。
    返回 `(新增流水, 违规)`。"""
    if not is_active(settings):
        return [], []
    ctx = _Ctx(
        settings, step=step, sim_id=sim_id, branch=branch, vars_=vars_, events=sampled_events or [], stale_ids=[],
        max_trace=trace_limit,
    )
    _load_lines(ctx, settings, 0.0, skip_settled=False)
    if not ctx.order:
        return [], []
    pre = capture_pre(settings)
    _anchor_all(ctx)
    _apply_deviations(ctx, proposals)
    _apply_shifts(ctx, proposals)
    _settle_paths(ctx)
    _start_all(ctx, lambda el: ctx.clock1[el])
    _settle_milestones(ctx)
    _settle_gates(ctx)
    _settle_stage(ctx, pre)
    _settle_adoption(ctx, pre)
    _check_claims(ctx, proposals)
    _apply_new_subitems(ctx, proposals)
    _apply_signals(ctx, proposals)
    _check_trace(ctx)
    if ctx._dropped:
        ctx.viol("A11", f"本次流水超过剩余额度，多出的 {ctx._dropped} 条未记录", "warn")
    for el in ctx.order:
        ctx.lines[el]["anatomy"] = ctx.work[el]
        if el in ctx.life:
            ctx.lines[el]["lifecycle"] = ctx.life[el]
    return ctx.trace, ctx.violations


# ═════════════════════════════════════════════════════════════════════
# 提示词（`{anatomy_hint}`）与预览
# ═════════════════════════════════════════════════════════════════════

_ELAPSED_ASK = (
    "请同时在输出里给出可选字段 `elapsed_days`（这一步在模拟世界里跨越的天数，正数，量级估计即可）：\n"
    "剖面引擎用它推进指标、瓶颈路径和里程碑；不给则按占位天数估计，预测的时间精度会降级。"
)

_PROTOCOL = (
    "【元素剖面已开启】下列元素带有定量剖面，**引擎**负责推进它们的指标、瓶颈解决路径、里程碑与阶段。"
    "其中给出的数值与\"预计本步发生\"的事项是**引擎的既成事实，不是你的选择**：请把它们自然地写进叙事，不要否认或替换；"
    "也**不要自行宣布**瓶颈已解决、里程碑已达成或阶段跃迁——引擎按判据裁决，无据的声明会被驳回。"
    "如果叙事里确有偏离引擎数值的事件（外部冲击、决策后果），请在叙事里写明原因。"
)


_UPDATES_PROTOCOL = (
    "【可选输出 `anatomy_updates`】只有当叙事里**确有**偏离引擎数值的事件、或发现了剖面里还没有的关键子项/信号时，才在输出里加一个 "
    "`anatomy_updates` 对象（各键都可省略；没有就整个不要输出）。引擎裁决，越权或无据的会被驳回并记录：\n"
    "- `metric_deviations`：`[{\"element\", \"metric\", \"value\", \"reason\"（必填：是哪件事造成了偏离）, \"cause_ref\"（可选：事件/决策 id）}]`——"
    "只报叙事里**真的发生了偏离**的指标，不要每步都顺手校准；\n"
    "- `bottleneck_proposals`：`[{\"element\", \"bottleneck\", \"shift_days\"（把进行中路径的到期日提前（负）/推迟（正）的天数）, \"reason\"}]`——"
    "只能申请平移到期日，**不要**写\"已解决\"，是否解决由引擎按到期裁决；\n"
    "- `new_subitems`：`[{\"element\", \"part\": \"component|metric|bottleneck\", \"reason\"（必填）, \"item\": {与剖面相同格式的一条}}]`——"
    "只能新增，不能改已有条目；新增的一律视为\"LLM 先验\"；\n"
    "- `signals`：`[{\"element\", \"watch\"（现实里该盯什么）, \"means\"（先发生什么意味着走向哪条分支）}]`。"
)


def preview_step(
    settings: Dict[str, Any], history: List[Any], step: int, *, sim_id: str, branch: str,
    vars_: Optional[Dict[str, Any]] = None, sampled_events: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], float, str]:
    """在**副本**上按估计跨度空跑一步，得到\"预计本步发生\"的流水（不改任何入参）。返回 `(流水, 估计天数, 估计来源)`。
    抽样是确定性的：本步实际跨度等于估计值时，真实结算会得到完全相同的结果。"""
    basis, basis_source = event_sampler.estimate_basis_days(history, event_sampler.get_params(settings))
    trial = {**settings, "causal_lines": copy.deepcopy([x for x in settings.get("causal_lines") or [] if isinstance(x, dict)])}
    trace, _viol = safe_apply_step(
        trial, None, step=step, elapsed_days_raw=basis, pre=capture_pre(trial), sim_id=sim_id, branch=branch,
        vars_=vars_, sampled_events=sampled_events,
    )
    return trace, basis, basis_source


def _fmt_val(v: Any) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def _digest(line: Dict[str, Any], predicted: List[Dict[str, Any]], limit: int, clock: float) -> str:
    a = an.get_anatomy(line) or {}
    lid = str(line.get("id") or "").strip()
    head = f"- [{lid}] {line.get('label') or lid}"
    rows: List[str] = []
    mets = []
    for m in a.get("metrics") or []:
        v = metric_value(m)
        if v is not None:
            tr = (m.get("trend") or {}).get("kind")
            mets.append(f"{m.get('name') or m['id']} {_fmt_val(v)}{(' ' + m['unit']) if m.get('unit') else ''}" + (f"（{tr}）" if tr else ""))
    if mets:
        rows.append("指标：" + "；".join(mets))
    bns = []
    for b in a.get("bottlenecks") or []:
        st = b.get("status") or "open"
        piece = f"{b.get('name') or b['id']}（{ {'open': '未解决', 'resolved': '已解决', 'exhausted': '路径耗尽'}.get(st, st) }"
        run = next((p for p in b.get("resolution_paths") or [] if p.get("status") == "running"), None)
        if st == "open" and run:
            piece += f"，路径「{run['id']}」进行中，约 {max(0.0, run['resolve_at_day'] - clock):.0f} 天后到期"
        bns.append(piece + "）")
    if bns:
        rows.append("瓶颈：" + "；".join(bns))
    ms_open = [m for m in a.get("milestones") or [] if "reached_step" not in m]
    ms_done = [m for m in a.get("milestones") or [] if "reached_step" in m]
    if ms_done:
        rows.append("已达成里程碑：" + "、".join(str(m.get("name") or m["id"]) for m in ms_done))
    if ms_open:
        rows.append("待达成里程碑：" + "、".join(str(m.get("name") or m["id"]) for m in ms_open[:4]))
    events: List[str] = []
    for e in predicted:
        if e["element"] != lid:
            continue
        if e["kind"] == "bottleneck" and e["field"] == "status":
            events.append(f"瓶颈「{e['ref'].split(':', 1)[1]}」{'已解决' if e['value_after'] == 'resolved' else '路径耗尽'}（{e['reason']}）")
        elif e["kind"] == "bottleneck" and str(e["value_after"]).startswith("failed:"):
            events.append(f"瓶颈「{e['ref'].split(':', 1)[1]}」的路径失败（{e['reason']}）")
        elif e["kind"] == "milestone":
            events.append(f"里程碑「{e['ref'].split(':', 1)[1]}」达成")
        elif e["kind"] == "stage":
            events.append(f"阶段 {e['value_before']} → {e['value_after']}")
        elif e["kind"] == "gate" and e["value_after"] is True:
            events.append(f"采用门槛「{e['ref'].split(':', 1)[1]}」打开")
    if events:
        rows.append("**预计本步发生**：" + "；".join(events))
    text = head + "\n" + "\n".join("    " + r for r in rows) if rows else head
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def build_hint(
    settings: Optional[Dict[str, Any]], history: List[Any], step: int, *, sim_id: str = "", branch: str = "",
    vars_: Optional[Dict[str, Any]] = None, sampled_events: Optional[List[Dict[str, Any]]] = None,
    ask_elapsed_override: Optional[bool] = None,
) -> str:
    """喂给 `advance_step`/`world_evolve` 的 `{anatomy_hint}`。未开启 / 没有可结算的元素返回空字符串（prompt 与未开启时等价）。

    内容：协议（引擎是权威、LLM 不能宣布解决）+ 每个元素的精简摘要（受 `light_digest_chars` 约束，重点元素优先、最多
    `MAX_HINT_ELEMENTS` 个）+ 引擎空跑得到的\"预计本步发生\"事项（作为既成事实）+ 必要时索要 `elapsed_days`。"""
    if not is_active(settings):
        return ""
    s = settings or {}
    params = an.get_params(s)
    trace, basis, basis_source = preview_step(
        s, history, step, sim_id=sim_id, branch=branch, vars_=vars_, sampled_events=sampled_events,
    )
    lines = _engine_lines(s)
    lines.sort(key=lambda x: 0 if an.is_key(x) else 1)
    parts = [_PROTOCOL, f"引擎估计本步约跨越 {basis:g} 天（{'最近几步的中位数' if basis_source == 'history_median' else '占位估计'}）；"
             "若实际跨度与此差异很大，预计事项会按实际跨度重新结算（到期的顺延、未到期的不会发生）。"]
    for line in lines[:MAX_HINT_ELEMENTS]:
        meta = (an.get_anatomy(line) or {}).get("meta") or {}
        parts.append(_digest(line, trace, int(params["light_digest_chars"]), float(meta.get("clock_day") or 0.0)))
    if len(lines) > MAX_HINT_ELEMENTS:
        parts.append(f"（另有 {len(lines) - MAX_HINT_ELEMENTS} 个带剖面的元素本步不展示摘要，引擎照常结算。）")
    parts.append(_UPDATES_PROTOCOL)
    ask = ask_elapsed_override
    if ask is None:
        from world_simulator import causal_engine  # 延迟导入：与 `mechanisms` 一致，避免循环

        ask = not (tech_model.is_enabled(s) or event_sampler.is_enabled(s) or causal_engine.needs_elapsed(s))
    if ask:
        parts.append(_ELAPSED_ASK)
    return "\n".join(parts)


def safe_build_hint(*args: Any, **kwargs: Any) -> str:
    """`build_hint()` 的兜底版：在调用 LLM *之前*执行，出错不能拖垮整步推进——出错退化为空字符串。"""
    try:
        return build_hint(*args, **kwargs)
    except Exception:  # noqa: BLE001 — 旁路功能
        return ""


# ═════════════════════════════════════════════════════════════════════
# 展示用（只读）
# ═════════════════════════════════════════════════════════════════════


def trajectory(history: Iterable[Any], element_id: str) -> Dict[str, Any]:
    """从分支历史的 `anatomy_trace` 还原一个元素各指标/组件**已发生**的取值序列，以及逐步变化流水。

    返回 `{"series": {ref: [(step, value), ...]}, "rows": [{step, kind, ref, change, reason, source}]}`（`kind=subitem` 是 A5 新增的子项/信号）。
    只含有变化的步（没有变化的步不写流水）；界面按步号画折线，缺的步沿用上一个值。"""
    series: Dict[str, List[Tuple[int, float]]] = {}
    rows: List[Dict[str, Any]] = []
    for state in history or []:
        step = int(getattr(state, "step", 0) or 0)
        for e in getattr(state, "anatomy_trace", None) or []:
            if not isinstance(e, dict) or e.get("element") != element_id or e.get("kind") == "time":
                continue
            if e.get("kind") in ("metric", "component") and isinstance(e.get("value_after"), (int, float)):
                pts = series.setdefault(e["ref"], [])
                if not pts and isinstance(e.get("value_before"), (int, float)) and step > 0:
                    pts.append((step - 1, float(e["value_before"])))
                if pts and pts[-1][0] == step:
                    pts[-1] = (step, float(e["value_after"]))
                else:
                    pts.append((step, float(e["value_after"])))
            change = (
                f"新增「{e.get('value_after')}」" if e.get("kind") == "subitem"
                else f"{_fmt_val(e.get('value_before'))} → {_fmt_val(e.get('value_after'))}"
            )
            rows.append({
                "step": step, "kind": e.get("kind"), "ref": e.get("ref"),
                "change": change,
                "reason": e.get("reason") or "", "source": e.get("source") or "",
            })
    return {"series": series, "rows": rows}
