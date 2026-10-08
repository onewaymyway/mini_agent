"""world_simulator/backtest_metrics.py — 指标级回测（第二十四轮 A8）。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.7、§8 A8。

## 它回答什么

第二十二轮的 `backtest.py` 回答\"整套推演引擎涌现的里程碑顺序对不对\"（要真实 LLM）。本模块回答另一个问题：

> **骨架（A4 引擎 + A6 蒙特卡洛）给出的 P10–P90 区间，在真实历史序列上是偏窄还是偏宽？**

不需要 LLM，不联网，不写任何数据目录：纯 Python、固定种子、可复现。

## 流程（每个案例 × 每个截止日一次\"运行\"）

1. `load_case()`：读 `backtest_cases/metric/*.yaml`（真实历史数值序列 / 项目公告时间线，含截止日**之后**的真值）。
2. `build_pack()`：**只用截止日及之前的点**，由 `backtest_fit` 机械地拟合出趋势参数区间，搭一份带剖面的 `settings`。
3. `run_forecast()`：对这份 `settings` 做蒙特卡洛，**和产品里预测简报用的是同一个函数**。
4. `score_run()`：把预测分位带 / 里程碑时间分布，与截止日之后的真值对账。
5. `summarize()`：跨运行汇总——区间覆盖率、分位损失、里程碑时点误差，给出\"偏窄 / 基本合适 / 偏宽 / 样本不足\"的结论。
6. `assess_for_element()`：A7 置信等级规则的输入——按元素的趋势族找\"同类\"案例，汇总结论变成扣分项（见 `forecast_brief.confidence`）。

## 防泄漏（计划 §8 A8 的硬要求）

- 回测**不跑实时检索**；\"截止日前资料包\"就是案例文件里 `t <= 截止日` 的点和 `date <= 截止日` 的公告；
- `build_pack()` 只接收截止日前的数据，截止日之后的点在入口处就被切掉；
- 运行时还有一道**金丝雀**：把案例里截止日之后的真值全部改成别的值再建一次包，两次的 `settings` 必须逐字节相同，
  否则抛 `BacktestLeak`（这是对\"有人往拟合里偷塞了未来信息\"的运行期保险，测试里也覆盖）；
- 参数拟合规则与常数（`backtest_fit`）事先声明，没有拿案例调过。

## 评分

| 指标 | 含义 |
|---|---|
| `coverage` | 真值落在 [P10, P90] 内的比例；名义值 0.8。低于 0.8 = 区间偏窄（过度自信），明显高于 0.8 = 偏宽 |
| `pinball` | 分位损失（q = 0.1 / 0.5 / 0.9 的平均）；对数尺度的序列在 ln 空间算，线性序列除以截止日的值，使不同案例可比 |
| `mape_p50` / `mean_abs_log_error` | 中位预测的平均绝对百分比误差 / 平均绝对对数误差 |
| `width_ratio` | 区间宽度：对数尺度 P90/P10，线性尺度 (P90-P10)/|截止日值| |
| 里程碑 `inside` / `position` / `p50_error_days` | 真值是否落在 [P10, P90]、落在哪一档、中位预测的带符号误差（正 = 预测更晚） |

汇总的**判定**看\"按运行平均的覆盖率\"（每个运行一票，避免长序列压倒短序列）：

| 平均覆盖率 | 判定 |
|---|---|
| < 0.5 | 严重偏窄（`narrow_severe`） |
| 0.5 – 0.7 | 偏窄（`narrow`） |
| 0.7 – 0.95 | 基本合适（`ok`） |
| > 0.95 | 偏宽（`wide`） |
| 案例数 < 3 | 样本不足（`insufficient`），不下结论 |

## 局限（报告里每次都会带上）

- 测的是\"骨架 + 机械拟合参数\"，**不是** LLM 研究出的参数质量（见 `backtest_fit`）；
- 案例很少、序列自相关、同一案例的不同截止日相互强相关，覆盖率的统计功效很弱：这是**一个旁证，不是校准**；
- 案例是公开的、容易找到数据的序列（策展偏差），项目案例还是公认的超期项目（选择偏差）；
- 真值来自摘要抓取，没有人工逐点核对（`verified: false`）；
- 蒙特卡洛的带宽只有 24 个检查点，真值落在点与点之间时按线性/对数插值（对数序列在 ln 空间插值，对指数曲线是精确的）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from world_simulator import anatomy as an
from world_simulator import backtest_fit as bf
from world_simulator import forecast as fc

DAYS_PER_YEAR = bf.DAYS_PER_YEAR
CASE_DIR = Path(__file__).resolve().parent.parent / "backtest_cases" / "metric"
SUMMARY_NAME = "summary.json"

DEFAULT_RUNS = 1000
DEFAULT_SEED = 2024
MAX_STEPS = fc.MAX_STEPS
EXPECTED_COVERAGE = 0.8
NARROW_SEVERE = 0.5
NARROW = 0.7
WIDE = 0.95
MIN_CASES_FOR_VERDICT = 3
QUANTILES = (0.1, 0.5, 0.9)
COVER_TOL = 1e-4                # 覆盖判定的相对容差：区间退化为一个点时，步长离散与插值带来约 1e-5 的相对误差也算覆盖

FAMILIES = ("exponential", "logistic", "learning_curve", "linear", "project_schedule")
KINDS = ("series", "schedule")
_TREND_FAMILY = {"exponential": "exponential", "logistic": "logistic", "saturating": "logistic", "learning_curve": "learning_curve", "linear": "linear"}

VERDICT_LABELS = {
    "narrow_severe": "严重偏窄", "narrow": "偏窄", "ok": "基本合适", "wide": "偏宽", "insufficient": "样本不足",
}
HONEST_NOTE = (
    "回测测的是『骨架 + 按历史点机械拟合出的参数区间』，不是 LLM 研究出的参数质量；案例少、序列自相关、同一案例的截止日强相关，"
    "覆盖率只是旁证，不是校准。"
)
CAVEATS: Tuple[str, ...] = (
    HONEST_NOTE,
    "案例是公开且容易找到数据的序列（策展偏差）；项目案例是公认的超期项目（选择偏差），两个机组强相关。",
    "真值来自摘要抓取，未逐点核对原始资料（案例 verified: false）。",
    "拟合常数事先声明、没有拿案例调过；换常数会改变覆盖率，应当用新的案例而不是这些案例去选常数。",
)


class BacktestMetricError(RuntimeError):
    """案例非法或回测流程无法继续。"""


class BacktestLeak(BacktestMetricError):
    """检测到拟合用到了截止日之后的信息。"""


# ═════════════════════════════════════════════════════════════════════
# 案例
# ═════════════════════════════════════════════════════════════════════


@dataclass
class SeriesSpec:
    id: str
    name: str
    unit: str
    scored: bool
    scale: str                      # log | linear
    points: List[Tuple[float, float]]


@dataclass
class MetricSpec:
    series: str
    trend: str
    driver: str = ""
    fit: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricCase:
    id: str
    title: str
    family: str
    kind: str
    verified: bool
    disclaimer: str
    provenance: List[Dict[str, Any]]
    cutoffs: List[Any]              # 序列案例：年；项目案例：日期字符串（保留原样用作标签）
    series: List[SeriesSpec] = field(default_factory=list)
    metrics: List[MetricSpec] = field(default_factory=list)
    cap_max: Optional[float] = None
    project: Dict[str, Any] = field(default_factory=dict)


def _fail(cid: str, msg: str) -> BacktestMetricError:
    return BacktestMetricError(f"指标级案例 {cid or '?'} 非法：{msg}")


def _num(x: Any) -> Optional[float]:
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_case(data: Any) -> MetricCase:
    if not isinstance(data, dict):
        raise _fail("", "顶层必须是映射")
    cid = str(data.get("id") or "").strip()
    if not cid:
        raise _fail("", "缺少 id")
    family = str(data.get("family") or "").strip()
    if family not in FAMILIES:
        raise _fail(cid, f"family 必须是 {FAMILIES} 之一，收到 {family!r}")
    kind = str(data.get("kind") or "").strip()
    if kind not in KINDS:
        raise _fail(cid, f"kind 必须是 {KINDS} 之一，收到 {kind!r}")
    if (kind == "schedule") != (family == "project_schedule"):
        raise _fail(cid, "schedule 案例的 family 必须是 project_schedule，反之亦然")
    cutoffs = data.get("cutoffs")
    if not isinstance(cutoffs, list) or not cutoffs:
        raise _fail(cid, "cutoffs 必须是非空列表")
    case = MetricCase(
        id=cid, title=str(data.get("title") or cid), family=family, kind=kind, verified=bool(data.get("verified", False)),
        disclaimer=str(data.get("disclaimer") or ""), provenance=[p for p in data.get("provenance") or [] if isinstance(p, dict)],
        cutoffs=list(cutoffs),
    )
    if kind == "series":
        _parse_series_case(case, data)
    else:
        _parse_schedule_case(case, data)
    return case


def _parse_series_case(case: MetricCase, data: Dict[str, Any]) -> None:
    cid = case.id
    raw_series = data.get("series")
    if not isinstance(raw_series, list) or not raw_series:
        raise _fail(cid, "series 必须是非空列表")
    ids = set()
    for raw in raw_series:
        if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
            raise _fail(cid, "series 条目缺少 id")
        sid = str(raw["id"]).strip()
        if sid in ids:
            raise _fail(cid, f"series id 重复：{sid}")
        ids.add(sid)
        scale = str(raw.get("scale") or "linear")
        if scale not in ("log", "linear"):
            raise _fail(cid, f"series {sid} 的 scale 必须是 log 或 linear")
        pts: List[Tuple[float, float]] = []
        for p in raw.get("points") or []:
            if not (isinstance(p, (list, tuple)) and len(p) == 2) or _num(p[0]) is None or _num(p[1]) is None:
                raise _fail(cid, f"series {sid} 的点必须是 [t, value] 数值对，收到 {p!r}")
            pts.append((float(p[0]), float(p[1])))
        pts.sort()
        if len({t for t, _ in pts}) != len(pts):
            raise _fail(cid, f"series {sid} 有重复的 t")
        if scale == "log" and any(v <= 0 for _t, v in pts):
            raise _fail(cid, f"series {sid} 是对数尺度，所有值必须为正")
        case.series.append(SeriesSpec(sid, str(raw.get("name") or sid), str(raw.get("unit") or ""), bool(raw.get("scored", False)), scale, pts))
    if not any(s.scored for s in case.series):
        raise _fail(cid, "至少要有一个 scored: true 的序列")
    model = data.get("model") or {}
    metrics = model.get("metrics") if isinstance(model, dict) else None
    if not isinstance(metrics, list) or not metrics:
        raise _fail(cid, "model.metrics 必须是非空列表")
    seen = set()
    for m in metrics:
        if not isinstance(m, dict) or m.get("series") not in ids:
            raise _fail(cid, f"model.metrics 引用了不存在的序列：{m!r}")
        if m["series"] in seen:
            raise _fail(cid, f"序列 {m['series']} 在 model.metrics 里出现了两次")
        seen.add(m["series"])
        trend = str(m.get("trend") or "")
        if trend not in _TREND_FAMILY:
            raise _fail(cid, f"不支持的趋势 {trend!r}，可选 {sorted(_TREND_FAMILY)}")
        driver = str(m.get("driver") or "")
        if trend == "learning_curve" and driver not in ids:
            raise _fail(cid, f"learning_curve 需要 driver 指向一个序列，收到 {driver!r}")
        fit = m.get("fit") or {}
        if not isinstance(fit, dict):
            raise _fail(cid, "fit 必须是映射")
        case.metrics.append(MetricSpec(m["series"], trend, driver, dict(fit)))
    for m in case.metrics:
        if m.trend == "learning_curve" and m.driver not in seen:
            raise _fail(cid, f"学习曲线的驱动序列 {m.driver!r} 必须也出现在 model.metrics 里（引擎要推进它）")
    scored = [s.id for s in case.series if s.scored]
    if not set(scored) <= seen:
        raise _fail(cid, "scored 序列必须出现在 model.metrics 里")
    if case.family not in {_TREND_FAMILY[m.trend] for m in case.metrics if m.series in scored}:
        raise _fail(cid, f"family={case.family} 与被评分序列的趋势族不一致")
    cm = _num((model or {}).get("cap_max"))
    case.cap_max = cm
    for c in case.cutoffs:
        if _num(c) is None:
            raise _fail(cid, f"序列案例的截止日必须是数值（年），收到 {c!r}")
        for s in case.series:
            if not any(t <= float(c) for t, _ in s.points) or not any(t > float(c) for t, _ in s.points):
                if s.scored or any(m.series == s.id for m in case.metrics):
                    raise _fail(cid, f"截止日 {c} 把序列 {s.id} 切成了空的历史或空的真值")


def _parse_schedule_case(case: MetricCase, data: Dict[str, Any]) -> None:
    cid = case.id
    proj = data.get("project")
    if not isinstance(proj, dict):
        raise _fail(cid, "schedule 案例缺少 project")
    try:
        base = proj["baseline"]
        b_ann, b_comp = bf.to_decimal_year(base["announced"]), bf.to_decimal_year(base["completion"])
        actual = bf.to_decimal_year(proj["actual"])
        anns = []
        for a in proj.get("announcements") or []:
            anns.append({"date": str(a["date"]), "t": bf.to_decimal_year(a["date"]), "completion": str(a["completion"]), "tc": bf.to_decimal_year(a["completion"])})
    except (KeyError, TypeError, bf.FitError) as exc:
        raise _fail(cid, f"project 字段不完整或日期非法：{exc}") from exc
    anns.sort(key=lambda a: a["t"])
    if b_comp <= b_ann:
        raise _fail(cid, "baseline 的计划完工不晚于公告日")
    case.project = {
        "name": str(proj.get("name") or cid), "baseline": {"announced": str(base["announced"]), "completion": str(base["completion"]), "t": b_ann, "tc": b_comp},
        "announcements": anns, "actual": str(proj["actual"]), "actual_t": actual,
    }
    for c in case.cutoffs:
        try:
            ct = bf.to_decimal_year(c)
        except bf.FitError as exc:
            raise _fail(cid, f"截止日 {c!r} 非法") from exc
        if ct >= actual:
            raise _fail(cid, f"截止日 {c} 不早于实际完工 {proj['actual']}")
        if ct <= b_ann:
            raise _fail(cid, f"截止日 {c} 不晚于初版公告 {base['announced']}")


def load_case(path: Path) -> MetricCase:
    path = Path(path)
    if not path.exists():
        raise BacktestMetricError(f"案例文件不存在：{path}")
    import yaml

    return parse_case(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_cases(directory: Optional[Path] = None) -> List[MetricCase]:
    d = Path(directory) if directory else CASE_DIR
    files = sorted(list(d.glob("*.yaml")) + list(d.glob("*.yml")))
    if not files:
        raise BacktestMetricError(f"目录里没有案例文件：{d}")
    cases = [load_case(f) for f in files]
    ids = [c.id for c in cases]
    if len(set(ids)) != len(ids):
        raise BacktestMetricError(f"案例 id 重复：{sorted(i for i in set(ids) if ids.count(i) > 1)}")
    return cases


# ═════════════════════════════════════════════════════════════════════
# 截止日前资料包 → settings
# ═════════════════════════════════════════════════════════════════════


def cutoff_t(case: MetricCase, cutoff: Any) -> float:
    return float(cutoff) if case.kind == "series" else bf.to_decimal_year(cutoff)


@dataclass
class Pack:
    """只含截止日及之前信息的资料包。"""
    case_id: str
    cutoff: Any
    cutoff_t: float
    settings: Dict[str, Any]
    fits: Dict[str, Any]
    history_counts: Dict[str, int]
    horizon_days: float
    steps: int
    element: str = "el"

    def digest(self) -> str:
        blob = json.dumps({"settings": self.settings, "fits": self.fits, "horizon": self.horizon_days, "steps": self.steps}, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _history(spec: SeriesSpec, ct: float) -> List[Tuple[float, float]]:
    return [(t, v) for t, v in spec.points if t <= ct]


def _steps_for(horizon_days: float) -> int:
    return int(min(MAX_STEPS, max(fc.MIN_STEPS, round(horizon_days / 30.4))))


def build_pack(case: MetricCase, cutoff: Any, *, future_horizon_days: float) -> Pack:
    """建资料包。`future_horizon_days` 只决定预测视野长度（评分要覆盖到最后一个真值），不携带任何真值信息。"""
    ct = cutoff_t(case, cutoff)
    line: Dict[str, Any] = {"id": "el", "label": case.title, "kind": "element", "element_type": "technology" if case.kind == "series" else "project"}
    fits: Dict[str, Any] = {}
    counts: Dict[str, int] = {}
    try:
        if case.kind == "series":
            hist = {s.id: _history(s, ct) for s in case.series}
            counts = {k: len(v) for k, v in hist.items()}
            metrics: List[Dict[str, Any]] = []
            for m in case.metrics:
                spec = next(s for s in case.series if s.id == m.series)
                h = hist[m.series]
                if not h:
                    raise bf.FitError(f"序列 {m.series} 在截止日前没有历史点")
                params, extra = _fit_metric(case, m, h, hist)
                fits[m.series] = {"trend": m.trend, "params": params, **extra}
                trend: Dict[str, Any] = {"kind": m.trend, "params": {k: dict(v) for k, v in params.items()}}
                if m.trend == "learning_curve":
                    trend["driver"] = f"metric:{m.driver}"
                metrics.append({"id": m.series, "name": spec.name, "unit": spec.unit, "current": {"value": h[-1][1]}, "trend": trend})
            anatomy = {"metrics": metrics}
        else:
            anns = [a for a in case.project["announcements"] if a["t"] <= ct]
            latest = anns[-1]["tc"] if anns else case.project["baseline"]["tc"]
            fit = bf.fit_schedule_overrun(
                baseline_announced=case.project["baseline"]["t"], baseline_completion=case.project["baseline"]["tc"],
                latest_completion=latest, cutoff=ct,
            )
            fits["schedule"] = fit
            counts = {"announcements": len(anns)}
            anatomy = {
                "bottlenecks": [{"id": "b", "name": case.project["name"], "status": "open", "resolution_paths": [
                    {"id": "p", "p_success": 1.0, "duration_days": {"low": fit["low"], "mode": fit["mode"], "high": fit["high"]}}]}],
                "milestones": [{"id": "ms", "name": case.project["name"], "criteria": {"bottleneck": "b", "status": "resolved"}}],
            }
    except bf.FitError as exc:
        raise BacktestMetricError(f"案例 {case.id} 截止日 {cutoff}：{exc}") from exc
    line["anatomy"] = an.normalize_anatomy(anatomy)
    settings = {"element_modeling_enabled": True, "anatomy_enabled": True, "causal_lines": [line]}
    horizon = float(max(future_horizon_days, 30.0))
    return Pack(case.id, cutoff, ct, settings, fits, counts, horizon, _steps_for(horizon))


def _fit_metric(case: MetricCase, m: MetricSpec, h: List[Tuple[float, float]], hist: Dict[str, List[Tuple[float, float]]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    fit = dict(m.fit)
    if m.trend == "exponential":
        r = bf.fit_exponential(h, window_years=fit.get("window_years"), window_points=fit.get("window_points"))
        return {"rate_per_year": r["rate_per_year"]}, {"n": r["n"], "window": [r["t_from"], r["t_to"]]}
    if m.trend == "linear":
        r = bf.fit_linear(h, window_years=fit.get("window_years"), window_points=fit.get("window_points"))
        return {"slope_per_year": r["slope_per_year"]}, {"n": r["n"], "window": [r["t_from"], r["t_to"]]}
    if m.trend in ("logistic", "saturating"):
        if m.trend == "saturating":
            raise bf.FitError("saturating 暂无机械拟合规则")
        r = bf.fit_logistic(h, cap_max=case.cap_max, window_years=fit.get("window_years"), window_points=fit.get("window_points"))
        return {"cap": r["cap"], "rate_per_year": r["rate_per_year"]}, {"n": r["n"], "n_acceptable_caps": r["n_acceptable_caps"], "cap_search": r["cap_search"], "window": [r["t_from"], r["t_to"]]}
    r = bf.fit_learning_curve(h, hist[m.driver], window_years=fit.get("window_years"))
    return {"b": r["b"]}, {"n": r["n"], "window": [r["t_from"], r["t_to"]]}


# ═════════════════════════════════════════════════════════════════════
# 真值
# ═════════════════════════════════════════════════════════════════════


def extract_truth(case: MetricCase, cutoff: Any) -> Dict[str, Any]:
    """截止日**之后**的真值（评分用，不进资料包）。"""
    ct = cutoff_t(case, cutoff)
    if case.kind == "series":
        out = {s.id: [(t, v) for t, v in s.points if t > ct] for s in case.series if s.scored}
        return {"series": out}
    return {"milestone_day": (case.project["actual_t"] - ct) * DAYS_PER_YEAR}


def future_horizon_days(case: MetricCase, cutoff: Any, truth: Dict[str, Any], pack_fit: Optional[Dict[str, Any]] = None) -> float:
    """预测视野：序列案例 = 到最后一个真值的天数；项目案例 = 路径 `high` 的 1.1 倍与真值天数的较大者。

    项目案例的视野依赖 `high`（来自截止日前的拟合）与真值天数。真值天数只用来保证\"视野不短于真值\"，不影响抽样本身；
    为了让金丝雀能比较资料包，这里把视野放进 `Pack`，并在金丝雀里**固定**使用同一个视野。
    """
    ct = cutoff_t(case, cutoff)
    if case.kind == "series":
        last = max(t for pts in truth["series"].values() for t, _ in pts)
        return (last - ct) * DAYS_PER_YEAR
    anns = [a for a in case.project["announcements"] if a["t"] <= ct]
    latest = anns[-1]["tc"] if anns else case.project["baseline"]["tc"]
    fit = bf.fit_schedule_overrun(
        baseline_announced=case.project["baseline"]["t"], baseline_completion=case.project["baseline"]["tc"], latest_completion=latest, cutoff=ct,
    )
    return max(fit["high"] * 1.1, truth["milestone_day"] * 1.1)


# ═════════════════════════════════════════════════════════════════════
# 金丝雀：截止日之后的任何改动都不能影响资料包
# ═════════════════════════════════════════════════════════════════════


def _perturb_future(case: MetricCase, cutoff: Any) -> MetricCase:
    ct = cutoff_t(case, cutoff)
    other = copy.deepcopy(case)
    if other.kind == "series":
        for s in other.series:
            s.points = [(t, v if t <= ct else v * (7.3 + (i % 5))) for i, (t, v) in enumerate(s.points)]
    else:
        other.project["actual_t"] += 9.0
        other.project["actual"] = "9999-01-01"
        for a in other.project["announcements"]:
            if a["t"] > ct:
                a["tc"] += 3.0
                a["completion"] = "9999-12-31"
    return other


def leak_check(case: MetricCase, cutoff: Any, horizon_days: float) -> str:
    """建两次资料包（真实真值 / 被篡改的未来），不一致就抛 `BacktestLeak`。返回资料包摘要。"""
    a = build_pack(case, cutoff, future_horizon_days=horizon_days)
    b = build_pack(_perturb_future(case, cutoff), cutoff, future_horizon_days=horizon_days)
    if a.digest() != b.digest():
        raise BacktestLeak(f"案例 {case.id} 截止日 {cutoff}：改动截止日之后的数据会改变资料包，说明拟合用到了未来信息")
    return a.digest()


# ═════════════════════════════════════════════════════════════════════
# 评分
# ═════════════════════════════════════════════════════════════════════


def band_at(row: Dict[str, Any], day: float) -> Optional[Dict[str, float]]:
    """在预测分位带上取第 `day` 天的 P10/P50/P90（相邻检查点之间插值；出了视野返回 None）。

    三条带在两端点都为正时在 ln 空间插值（对指数曲线精确），否则线性插值。
    """
    days = row.get("days") or []
    if not days or day < days[0] - 1e-9 or day > days[-1] + 1e-9:
        return None
    lo = 0
    for i in range(len(days) - 1):
        if days[i] <= day <= days[i + 1]:
            lo = i
            break
    hi = min(lo + 1, len(days) - 1)
    out: Dict[str, float] = {}
    for name in ("p10", "p50", "p90"):
        a, b = row[name][lo], row[name][hi]
        if a is None or b is None:
            return None
        if hi == lo or days[hi] == days[lo]:
            out[name] = float(a)
            continue
        w = (day - days[lo]) / (days[hi] - days[lo])
        if a > 0 and b > 0:
            out[name] = math.exp(math.log(a) + (math.log(b) - math.log(a)) * w)
        else:
            out[name] = float(a) + (float(b) - float(a)) * w
    return out


def pinball(y: float, qhat: float, q: float) -> float:
    d = y - qhat
    return max(q * d, (q - 1.0) * d)


def _score_series(spec: SeriesSpec, row: Dict[str, Any], truth: Sequence[Tuple[float, float]], ct: float, start_value: float) -> Dict[str, Any]:
    pts: List[Dict[str, Any]] = []
    out_of_horizon = 0
    for t, y in truth:
        day = (t - ct) * DAYS_PER_YEAR
        band = band_at(row, day)
        if band is None:
            out_of_horizon += 1
            continue
        tol = COVER_TOL * max(abs(y), abs(band["p50"]), 1e-300)
        covered = band["p10"] - tol <= y <= band["p90"] + tol
        pts.append({"t": t, "truth": y, "p10": band["p10"], "p50": band["p50"], "p90": band["p90"], "covered": covered})
    n = len(pts)
    base: Dict[str, Any] = {"id": spec.id, "name": spec.name, "unit": spec.unit, "scale": spec.scale, "n_truth": len(truth), "n_scored": n, "n_out_of_horizon": out_of_horizon, "points": pts}
    if n == 0:
        base.update(coverage=None, covered=0, pinball=None, mape_p50=None, mean_abs_log_error=None, width_ratio=None, below=0, above=0)
        return base
    covered = sum(1 for p in pts if p["covered"])
    use_log = spec.scale == "log" and all(p["truth"] > 0 and p["p10"] > 0 and p["p50"] > 0 and p["p90"] > 0 for p in pts)
    losses = []
    for p in pts:
        for q, key in zip(QUANTILES, ("p10", "p50", "p90")):
            if use_log:
                losses.append(pinball(math.log(p["truth"]), math.log(p[key]), q))
            else:
                losses.append(pinball(p["truth"], p[key], q) / max(abs(start_value), 1e-12))
    mape = sum(abs(p["p50"] - p["truth"]) / abs(p["truth"]) for p in pts if p["truth"]) / max(1, sum(1 for p in pts if p["truth"]))
    mal = sum(abs(math.log(p["p50"] / p["truth"])) for p in pts) / n if all(p["truth"] > 0 and p["p50"] > 0 for p in pts) else None
    if use_log:
        width = sum(math.log(p["p90"] / p["p10"]) for p in pts) / n
        width = math.exp(width)
    else:
        width = sum((p["p90"] - p["p10"]) / max(abs(start_value), 1e-12) for p in pts) / n
    base.update(
        coverage=covered / n, covered=covered, pinball=sum(losses) / len(losses), mape_p50=mape, mean_abs_log_error=mal, width_ratio=width,
        below=sum(1 for p in pts if p["truth"] < p["p10"]), above=sum(1 for p in pts if p["truth"] > p["p90"]), pinball_space="log" if use_log else "relative",
    )
    return base


def _score_milestone(row: Optional[Dict[str, Any]], truth_day: float, name: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"id": "ms", "name": name, "truth_day": truth_day}
    if not row or row.get("status") != "pending":
        out.update(inside=None, position="unscored", p_reached=None, p10=None, p50=None, p90=None, p50_error_days=None, p50_error_rel=None)
        return out
    p10, p50, p90 = row.get("p10"), row.get("p50"), row.get("p90")
    out.update(p_reached=row.get("p_reached"), p10=p10, p50=p50, p90=p90)
    hi = p90 if p90 is not None else math.inf
    lo = p10 if p10 is not None else math.inf
    if truth_day < lo:
        pos = "below_p10"
    elif p50 is not None and truth_day < p50:
        pos = "p10_p50"
    elif truth_day <= hi:
        pos = "p50_p90"
    else:
        pos = "above_p90"
    out["position"] = pos
    out["inside"] = pos in ("p10_p50", "p50_p90")
    out["p50_error_days"] = None if p50 is None else p50 - truth_day
    out["p50_error_rel"] = None if p50 is None or not truth_day else (p50 - truth_day) / truth_day
    return out


def score_run(case: MetricCase, cutoff: Any, forecast: Dict[str, Any], truth: Dict[str, Any], pack: Pack) -> Dict[str, Any]:
    ct = pack.cutoff_t
    res: Dict[str, Any] = {
        "case_id": case.id, "family": case.family, "kind": case.kind, "cutoff": cutoff, "verified": case.verified,
        "runs_done": (forecast.get("meta") or {}).get("runs_done"), "horizon_days": pack.horizon_days, "steps": pack.steps,
        "fits": pack.fits, "history_counts": pack.history_counts, "series": [], "milestones": [],
    }
    if case.kind == "series":
        rows = {m["id"]: m for m in forecast.get("metrics") or []}
        for s in case.series:
            if not s.scored:
                continue
            row = rows.get(s.id)
            start = next(m for m in pack.settings["causal_lines"][0]["anatomy"]["metrics"] if m["id"] == s.id)["current"]["value"]
            if row is None:
                res["series"].append({"id": s.id, "name": s.name, "n_scored": 0, "coverage": None, "points": [], "note": "预测里没有这条指标"})
                continue
            res["series"].append(_score_series(s, row, truth["series"][s.id], ct, float(start)))
        cov = [s["coverage"] for s in res["series"] if s.get("coverage") is not None]
        res["run_coverage"] = sum(cov) / len(cov) if cov else None
    else:
        ms = next((m for m in forecast.get("milestones") or [] if m["id"] == "ms"), None)
        scored = _score_milestone(ms, truth["milestone_day"], case.project["name"])
        res["milestones"].append(scored)
        res["run_coverage"] = None if scored["inside"] is None else (1.0 if scored["inside"] else 0.0)
    return res


# ═════════════════════════════════════════════════════════════════════
# 运行
# ═════════════════════════════════════════════════════════════════════


def run_case(case: MetricCase, cutoff: Any, *, runs: int = DEFAULT_RUNS, seed: int = DEFAULT_SEED) -> Dict[str, Any]:
    """一个案例的一个截止日。返回评分结果（可 JSON 序列化）；失败时返回 `{"ok": False, "error": ...}`。"""
    try:
        truth = extract_truth(case, cutoff)
        horizon = future_horizon_days(case, cutoff, truth)
        digest = leak_check(case, cutoff, horizon)
        pack = build_pack(case, cutoff, future_horizon_days=horizon)
    except BacktestLeak:
        raise
    except BacktestMetricError as exc:
        return {"ok": False, "case_id": case.id, "family": case.family, "cutoff": cutoff, "error": str(exc)}
    forecast = fc.run_forecast(
        pack.settings, None, runs=runs, seed=seed, horizon_days=pack.horizon_days, steps=pack.steps, time_budget_sec=0,
        sim_id=f"backtest-metric:{case.id}:{cutoff}",
    )
    if not forecast.get("ok"):
        return {"ok": False, "case_id": case.id, "family": case.family, "cutoff": cutoff, "error": f"预测失败：{forecast.get('reason')}"}
    res = score_run(case, cutoff, forecast, truth, pack)
    res.update(ok=True, pack_digest=digest, seed=seed, runs=runs, forecast_meta={k: forecast["meta"].get(k) for k in ("runs_done", "truncated", "time_resolution_days", "time_precision_degraded")})
    return res


def run_all(cases: Optional[Sequence[MetricCase]] = None, *, runs: int = DEFAULT_RUNS, seed: int = DEFAULT_SEED, only: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    cs = list(cases) if cases is not None else load_cases()
    if only:
        keep = set(only)
        cs = [c for c in cs if c.id in keep]
    out: List[Dict[str, Any]] = []
    for c in cs:
        for cutoff in c.cutoffs:
            out.append(run_case(c, cutoff, runs=runs, seed=seed))
    return out


# ═════════════════════════════════════════════════════════════════════
# 汇总
# ═════════════════════════════════════════════════════════════════════


def wilson(k: int, n: int, z: float = 1.96) -> Optional[List[float]]:
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def verdict_for(coverage: Optional[float], n_cases: int) -> str:
    if coverage is None or n_cases < MIN_CASES_FOR_VERDICT:
        return "insufficient"
    if coverage < NARROW_SEVERE:
        return "narrow_severe"
    if coverage < NARROW:
        return "narrow"
    if coverage > WIDE:
        return "wide"
    return "ok"


def _mean(xs: Sequence[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _group(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [r for r in results if r.get("ok") and r.get("run_coverage") is not None]
    case_ids = sorted({r["case_id"] for r in ok})
    pts = [p for r in ok for s in r["series"] for p in s.get("points", [])]
    n_pts, k_pts = len(pts), sum(1 for p in pts if p["covered"])
    ms = [m for r in ok for m in r["milestones"] if m.get("inside") is not None]
    cov_runs = [r["run_coverage"] for r in ok]
    cov = _mean(cov_runs)
    pin = [s["pinball"] for r in ok for s in r["series"] if s.get("pinball") is not None]
    return {
        "cases": len(case_ids), "case_ids": case_ids, "runs": len(ok), "run_coverages": cov_runs,
        "coverage": cov, "expected": EXPECTED_COVERAGE, "verdict": verdict_for(cov, len(case_ids)),
        "points": {"n": n_pts, "covered": k_pts, "coverage": (k_pts / n_pts) if n_pts else None, "wilson95": wilson(k_pts, n_pts),
                   "below": sum(1 for p in pts if p["truth"] < p["p10"]), "above": sum(1 for p in pts if p["truth"] > p["p90"])},
        "milestones": {"n": len(ms), "inside": sum(1 for m in ms if m["inside"]), "below_p10": sum(1 for m in ms if m["position"] == "below_p10"),
                       "above_p90": sum(1 for m in ms if m["position"] == "above_p90"),
                       "mean_p50_error_rel": _mean([m["p50_error_rel"] for m in ms])},
        "mean_pinball": _mean(pin),
        "mean_mape_p50": _mean([s["mape_p50"] for r in ok for s in r["series"] if s.get("mape_p50") is not None]),
        "mean_width_ratio": _mean([s["width_ratio"] for r in ok for s in r["series"] if s.get("width_ratio") is not None]),
    }


def summarize(results: Sequence[Dict[str, Any]], *, now: Optional[str] = None, runs: Optional[int] = None, seed: Optional[int] = None) -> Dict[str, Any]:
    """跨运行汇总。`by_family` 与 `overall` 里都带 `run_coverages`，供 `assess_for_element` 按元素的趋势族再汇总。"""
    ok = [r for r in results if r.get("ok")]
    failed = [{"case_id": r.get("case_id"), "cutoff": r.get("cutoff"), "error": r.get("error")} for r in results if not r.get("ok")]
    by_family = {f: _group([r for r in ok if r["family"] == f]) for f in FAMILIES if any(r["family"] == f for r in ok)}
    overall = _group(ok)
    stamp = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "version": 1, "generated_at": stamp, "runs_per_forecast": runs, "seed": seed,
        "cases": overall["cases"], "runs": overall["runs"], "coverage": overall["coverage"], "verdict": overall["verdict"],
        "overall": overall, "by_family": by_family, "failed": failed, "expected_coverage": EXPECTED_COVERAGE,
        "thresholds": {"narrow_severe": NARROW_SEVERE, "narrow": NARROW, "wide": WIDE, "min_cases": MIN_CASES_FOR_VERDICT},
        "unverified_cases": sorted({r["case_id"] for r in ok if not r.get("verified")}),
        "caveats": list(CAVEATS), "honesty": HONEST_NOTE,
    }


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def default_summary_paths(reports_dir: Optional[Path] = None) -> List[Path]:
    """`load_summary` 的查找顺序：用户自己跑出来的（`reports/backtest_metric/summary.json`）优先，其次随仓库发布的基线。"""
    out: List[Path] = []
    if reports_dir is None:
        from world_simulator.config import REPORTS_DIR

        reports_dir = REPORTS_DIR
    out.append(Path(reports_dir) / "backtest_metric" / SUMMARY_NAME)
    out.append(CASE_DIR / "baseline_summary.json")   # 仅 `load_summary()` 无参调用时兜底；预测简报不用它（见 `feedback_summary`）
    return out


def load_summary(path: Optional[Path] = None, *, reports_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """读汇总；读不到、损坏、或不是预期结构时返回 None（调用方按\"没有回测\"处理，不报错）。"""
    paths = [Path(path)] if path else default_summary_paths(reports_dir)
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("by_family"), dict) and isinstance(data.get("overall"), dict):
            return data
    return None


def feedback_summary(settings: Optional[Dict[str, Any]], *, reports_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """预测简报该用哪份回测汇总：`anatomy_params.backtest_feedback` 关闭（默认）→ None（行为与没有本功能时一致）；
    开启 → 读用户自己跑出来的 `reports/backtest_metric/summary.json`（随仓库的基线不自动使用）。"""
    if not an.get_params(settings).get("backtest_feedback"):
        return None
    from world_simulator.config import REPORTS_DIR

    return load_summary(Path(reports_dir or REPORTS_DIR) / "backtest_metric" / SUMMARY_NAME)


# ═════════════════════════════════════════════════════════════════════
# 与置信等级衔接（A7 的 `no_backtest` 规则在这里升级成按同类案例扣分）
# ═════════════════════════════════════════════════════════════════════


def families_for_anatomy(anatomy: Optional[Dict[str, Any]]) -> List[str]:
    """一份剖面涉及哪些回测趋势族：指标的趋势类型 → 族；有带解决路径的瓶颈 → `project_schedule`。"""
    out: List[str] = []
    a = anatomy or {}
    for m in a.get("metrics") or []:
        kd = ((m.get("trend") or {}).get("kind")) if isinstance(m, dict) else None
        fam = _TREND_FAMILY.get(str(kd))
        if fam and fam not in out:
            out.append(fam)
    for bn in a.get("bottlenecks") or []:
        if isinstance(bn, dict) and bn.get("resolution_paths"):
            if "project_schedule" not in out:
                out.append("project_schedule")
            break
    return out


def assess_for_element(summary: Optional[Dict[str, Any]], anatomy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把回测汇总变成对一个元素的评估。

    返回 `{has_data, scope, families, cases, runs, coverage, verdict, verdict_label, detail}`：
    - `scope == "family"`：该元素的趋势族在汇总里有 ≥ `MIN_CASES_FOR_VERDICT` 个案例（可以用同类案例下结论）；
    - `scope == "overall"`：同类案例不足，退回整体汇总（所有案例，不分族）——只当旁证；
    - `scope == "thin"`：整体案例也不足，没有结论；
    - `has_data == False`：根本没有回测汇总。
    旧格式（只有 `{"cases": n}`，没有 `by_family`）视为\"有回测、无细节\"，`legacy: True`，不产生扣分。
    """
    if not isinstance(summary, dict) or int(summary.get("cases") or 0) < 1:
        return {"has_data": False, "scope": "none", "verdict": None, "detail": "没有回测汇总"}
    if not isinstance(summary.get("by_family"), dict) or not isinstance(summary.get("overall"), dict):
        return {"has_data": True, "legacy": True, "scope": "legacy", "cases": int(summary.get("cases") or 0), "verdict": None, "detail": "回测汇总没有按趋势族拆分，只记『有回测』"}
    fams = families_for_anatomy(anatomy)
    mine = [f for f in fams if f in summary["by_family"]]
    case_ids = sorted({c for f in mine for c in summary["by_family"][f].get("case_ids") or []})
    covs = [c for f in mine for c in summary["by_family"][f].get("run_coverages") or []]
    if mine and len(case_ids) >= MIN_CASES_FOR_VERDICT and covs:
        cov = sum(covs) / len(covs)
        return _assessed("family", mine, case_ids, len(covs), cov)
    ov = summary["overall"]
    ov_cases = int(ov.get("cases") or 0)
    if ov_cases >= MIN_CASES_FOR_VERDICT and ov.get("coverage") is not None:
        return _assessed("overall", mine, ov.get("case_ids") or [], int(ov.get("runs") or 0), float(ov["coverage"]), same_family_cases=len(case_ids))
    return {
        "has_data": True, "scope": "thin", "families": mine, "cases": ov_cases, "runs": int(ov.get("runs") or 0), "coverage": ov.get("coverage"),
        "verdict": "insufficient", "verdict_label": VERDICT_LABELS["insufficient"],
        "detail": f"回测案例只有 {ov_cases} 个（< {MIN_CASES_FOR_VERDICT}），不下结论",
    }


def _assessed(scope: str, fams: List[str], case_ids: Sequence[str], runs: int, cov: float, same_family_cases: Optional[int] = None) -> Dict[str, Any]:
    v = verdict_for(cov, len(case_ids))
    where = "同类趋势族" if scope == "family" else "全部案例（同类案例不足，只作旁证）"
    extra = "" if same_family_cases is None else f"；同类案例 {same_family_cases} 个"
    return {
        "has_data": True, "scope": scope, "families": fams, "cases": len(case_ids), "runs": runs, "coverage": cov,
        "verdict": v, "verdict_label": VERDICT_LABELS[v],
        "detail": f"{where}的 {len(case_ids)} 个回测案例（{runs} 次运行）里，名义 80% 的区间平均只覆盖了 {cov:.0%} 的真值{extra}" if v in ("narrow", "narrow_severe")
        else f"{where}的 {len(case_ids)} 个回测案例（{runs} 次运行）里，名义 80% 的区间平均覆盖了 {cov:.0%} 的真值（判定：{VERDICT_LABELS[v]}）{extra}",
    }
