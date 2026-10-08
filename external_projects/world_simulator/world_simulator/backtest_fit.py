"""world_simulator/backtest_fit.py — 指标级回测的机械参数拟合（第二十四轮 A8）。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §8 A8。

## 为什么需要它

指标级回测要回答\"骨架给出的区间是不是太窄/太宽\"。骨架的输入是趋势参数 `{value, low, high}`，正常运行时由 LLM 研究 + 联网证据给出；
回测不能联网（会看到未来），也不能让写案例的人（包括我）凭对结果的了解手填区间——那是**后见之明泄漏**。

所以回测里的参数全部由本模块从**截止日之前**的历史点**机械地**算出来：同样的输入永远得到同样的参数，没有任何一个常数是照着真值调的。
本模块不依赖 numpy，只用标准库。

## 测的到底是什么（必须如实标注）

测的是\"**骨架 + 机械拟合出来的参数区间**\"，**不是** LLM 研究得到的参数质量。所以：

- 回测给出的覆盖率说明\"按历史点机械拟合出区间、再交给骨架外推\"这条路在这些案例上的表现；
- 它是对\"参数区间驱动的预测普遍偏窄还是偏宽\"的**旁证**，不是对某个具体元素区间的校准；
- 拟合方法本身的选择（见下）会影响覆盖率；本模块的常数是**事先声明的默认值，没有拿回测案例调过**。

## 拟合规则（全部只用 `t <= 截止日` 的点）

| 趋势 | 参数 | 规则 |
|---|---|---|
| `exponential` | `rate_per_year` | `ln v` 对 `t` 做最小二乘，斜率 `s` → `exp(s)-1`；区间 = `s ± t₈₀(n-2)·se(s)`，`t₈₀` 为 80% 双侧 t 分位 |
| `logistic` | `cap`、`rate_per_year` | 在 `[1.02·max(v), cap_max]` 上做几何网格；每个 `cap` 在 logit 空间 `ln(v/(cap-v))` 对 `t` 最小二乘；残差平方和 ≤ `tol` × 最小值的 `cap` 都算\"可接受\"，`cap` 区间取可接受集合的两端，`rate` 区间取这些 `cap` 下 `rate` 的两端；`cap_max = min(物理上限, 5·max(v))` |
| `learning_curve` | `b` | `ln(成本)` 对 `ln(驱动量)` 做最小二乘，`b = -斜率`；区间同上（t 分位 × 斜率标准误） |
| 项目工期 | 路径 `duration_days` | `low` = 公告里剩余工期；`mode` = 剩余 × 迄今超期倍数 `f`；`high` = 剩余 × `f²`；`f = max(终版公告总工期 / 初版公告总工期, 超期下限 1.25)` |

## 刻意的局限

- 区间只来自**拟合误差**，不含\"这条曲线会不会拐弯\"的结构不确定性——回测多半会发现区间偏窄，这正是它要暴露的东西；
- 最小二乘假设残差独立，真实序列自相关，标准误偏小；
- `logistic` 的 `cap` 与 `rate` 在引擎里被当成独立参数各自抽样，实际它们负相关，区间的联合支撑比真实的更宽（偏保守）；
- 项目工期规则是一条**启发式**（复利式超期），不是统计校准过的参照类预测；项目案例数很少，且是公认的超期大项目（选择偏差）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

DAYS_PER_YEAR = 365.25

# 事先声明的默认值（不依赖任何回测案例）
INTERVAL = 0.8                 # 参数区间对应的名义概率
LOGISTIC_TOL = 2.0             # logit 空间残差平方和相对最小值的容忍倍数
LOGISTIC_GRID = 80             # cap 网格点数
LOGISTIC_CAP_FACTOR = 5.0      # 没有物理上限时 cap 的上界 = 此倍数 × 历史最大值
OVERRUN_FLOOR = 1.25           # 项目超期倍数下限（没出现过超期时也留出空间）
MIN_POINTS = {"exponential": 3, "logistic": 4, "learning_curve": 4}

Point = Tuple[float, float]


class FitError(ValueError):
    """历史点不足或不满足拟合条件。"""


# 80% 双侧 t 分位（= 单侧 0.9）；df 之间取不超过它的最近一档（偏保守地取更大的值）
_T80 = [(1, 3.078), (2, 1.886), (3, 1.638), (4, 1.533), (5, 1.476), (6, 1.440), (7, 1.415), (8, 1.397), (9, 1.383),
        (10, 1.372), (12, 1.356), (15, 1.341), (20, 1.325), (25, 1.316), (30, 1.310)]
_Z80 = 1.2816


def t80(df: int) -> float:
    """80% 双侧 t 分位；`df` 不在表里时取不超过它的最近一档（更大的分位 = 更宽）。"""
    if df < 1:
        raise FitError("自由度不足")
    best = _T80[0][1]
    for d, v in _T80:
        if d <= df:
            best = v
        else:
            break
    return _Z80 if df > 30 else best


def _ols(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float, float, float, float]:
    """最小二乘 `y = a + b x`。返回 `(b, a, se_b, sse, resid_sd)`；n=2 时 se 记 0（没有自由度）。"""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        raise FitError("自变量没有变化，无法拟合")
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    sse = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    if n <= 2:
        return b, a, 0.0, sse, 0.0
    sd = math.sqrt(sse / (n - 2))
    return b, a, sd / math.sqrt(sxx), sse, sd


def _window(points: Sequence[Point], window_years: Optional[float], window_points: Optional[int]) -> List[Point]:
    pts = sorted((float(t), float(v)) for t, v in points)
    if window_years is not None and pts:
        last = pts[-1][0]
        pts = [p for p in pts if p[0] >= last - float(window_years)]
    if window_points is not None:
        pts = pts[-int(window_points):]
    return pts


def _param(value: float, low: float, high: float, **extra: Any) -> Dict[str, Any]:
    lo, hi = min(low, value, high), max(low, value, high)
    out = {"value": value, "low": lo, "high": hi, "dist": "triangular"}
    out.update(extra)
    return out


def fit_exponential(points: Sequence[Point], *, window_years: Optional[float] = None, window_points: Optional[int] = None) -> Dict[str, Any]:
    """`rate_per_year`（年增长率，复利）。要求所有值 > 0。"""
    pts = _window(points, window_years, window_points)
    if len(pts) < MIN_POINTS["exponential"]:
        raise FitError(f"指数拟合至少需要 {MIN_POINTS['exponential']} 个点，只有 {len(pts)} 个")
    if any(v <= 0 for _t, v in pts):
        raise FitError("指数拟合要求所有值为正")
    slope, _a, se, _sse, _sd = _ols([t for t, _ in pts], [math.log(v) for _, v in pts])
    k = t80(len(pts) - 2)
    return {
        "rate_per_year": _param(math.expm1(slope), math.expm1(slope - k * se), math.expm1(slope + k * se)),
        "n": len(pts), "se_log_slope": se, "t_from": pts[0][0], "t_to": pts[-1][0],
    }


def fit_linear(points: Sequence[Point], *, window_years: Optional[float] = None, window_points: Optional[int] = None) -> Dict[str, Any]:
    """`slope_per_year`（每年绝对增量）。"""
    pts = _window(points, window_years, window_points)
    if len(pts) < 3:
        raise FitError(f"线性拟合至少需要 3 个点，只有 {len(pts)} 个")
    slope, _a, se, _sse, _sd = _ols([t for t, _ in pts], [v for _, v in pts])
    k = t80(len(pts) - 2)
    return {"slope_per_year": _param(slope, slope - k * se, slope + k * se), "n": len(pts), "t_from": pts[0][0], "t_to": pts[-1][0]}


def fit_logistic(
    points: Sequence[Point], *, cap_max: Optional[float] = None, window_years: Optional[float] = None, window_points: Optional[int] = None,
    tol: float = LOGISTIC_TOL,
) -> Dict[str, Any]:
    """`cap` 与 `rate_per_year`。`cap_max` 是物理上限（如百分比 100）；缺省时用 `LOGISTIC_CAP_FACTOR × max(v)`。"""
    pts = _window(points, window_years, window_points)
    if len(pts) < MIN_POINTS["logistic"]:
        raise FitError(f"逻辑斯蒂拟合至少需要 {MIN_POINTS['logistic']} 个点，只有 {len(pts)} 个")
    if any(v <= 0 for _t, v in pts):
        raise FitError("逻辑斯蒂拟合要求所有值为正")
    vmax = max(v for _t, v in pts)
    lo_cap = vmax * 1.02
    hi_cap = LOGISTIC_CAP_FACTOR * vmax
    if cap_max is not None:
        hi_cap = min(hi_cap, float(cap_max))
    if hi_cap <= lo_cap:
        raise FitError(f"cap 上限 {hi_cap:g} 不高于历史最大值的 1.02 倍 {lo_cap:g}，无法拟合")
    ratio = (hi_cap / lo_cap) ** (1.0 / (LOGISTIC_GRID - 1))
    ts = [t for t, _ in pts]
    fits: List[Tuple[float, float, float]] = []   # (cap, rate, sse)
    for i in range(LOGISTIC_GRID):
        cap = lo_cap * ratio ** i
        ys = [math.log(v / (cap - v)) for _t, v in pts]
        slope, _a, _se, sse, _sd = _ols(ts, ys)
        fits.append((cap, slope, sse))
    sse_min = min(f[2] for f in fits)
    floor = 1e-9 * len(pts)
    ok = [f for f in fits if f[2] <= max(sse_min * tol, sse_min + floor)]
    best = min(fits, key=lambda f: f[2])
    caps = [f[0] for f in ok]
    rates = [f[1] for f in ok]
    return {
        "cap": _param(best[0], min(caps), max(caps)),
        "rate_per_year": _param(best[1], min(rates), max(rates)),
        "n": len(pts), "n_acceptable_caps": len(ok), "cap_search": [lo_cap, hi_cap], "t_from": pts[0][0], "t_to": pts[-1][0],
    }


def fit_learning_curve(cost: Sequence[Point], driver: Sequence[Point], *, window_years: Optional[float] = None) -> Dict[str, Any]:
    """学习率指数 `b`：`成本 ∝ 驱动量^(-b)`。两条序列按**相同的 t** 配对。"""
    d = {float(t): float(v) for t, v in driver}
    pairs = sorted((float(t), float(v), d[float(t)]) for t, v in cost if float(t) in d)
    if window_years is not None and pairs:
        pairs = [p for p in pairs if p[0] >= pairs[-1][0] - float(window_years)]
    if len(pairs) < MIN_POINTS["learning_curve"]:
        raise FitError(f"学习曲线拟合至少需要 {MIN_POINTS['learning_curve']} 对点，只有 {len(pairs)} 对")
    if any(c <= 0 or q <= 0 for _t, c, q in pairs):
        raise FitError("学习曲线拟合要求成本与驱动量均为正")
    slope, _a, se, _sse, _sd = _ols([math.log(q) for _t, _c, q in pairs], [math.log(c) for _t, c, _q in pairs])
    k = t80(len(pairs) - 2)
    b = -slope
    return {"b": _param(b, b - k * se, b + k * se), "n": len(pairs), "se_slope": se, "t_from": pairs[0][0], "t_to": pairs[-1][0]}


def fit_schedule_overrun(
    *, baseline_announced: float, baseline_completion: float, latest_completion: float, cutoff: float,
    overrun_floor: float = OVERRUN_FLOOR,
) -> Dict[str, Any]:
    """项目剩余工期（天）的 `low/mode/high`。时间都是十进制年。

    `baseline_*`：最初公告的时点与计划完工时点；`latest_completion`：截止日前最近一次公告的计划完工；
    `cutoff` 之后的公告一律不得传入（调用方负责，`backtest_metrics` 在上游裁剪）。
    """
    remaining = latest_completion - cutoff
    if remaining <= 0:
        raise FitError("截止日时公告的计划完工已过，没有剩余工期可预测")
    base_span = baseline_completion - baseline_announced
    if base_span <= 0:
        raise FitError("初版计划完工不晚于初版公告时点")
    f = max(float(overrun_floor), (latest_completion - baseline_announced) / base_span)
    days = remaining * DAYS_PER_YEAR
    return {
        "low": days, "mode": days * f, "high": days * f * f,
        "remaining_days": days, "overrun_factor": f, "baseline_span_years": base_span,
    }


def to_decimal_year(text: Any) -> float:
    """`2018-02-15` / `2018` / `2018.5` → 十进制年（按 365.25 天折算，闰年误差忽略）。"""
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip()
    parts = s.split("-")
    try:
        y = int(parts[0])
        if len(parts) == 1:
            return float(y)
        m = int(parts[1])
        d = int(parts[2]) if len(parts) > 2 else 1
    except ValueError as exc:
        raise FitError(f"无法解析日期 {text!r}") from exc
    if not (1 <= m <= 12 and 1 <= d <= 31):
        raise FitError(f"日期越界 {text!r}")
    cum = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    return y + (cum[m - 1] + d - 1) / 365.0
