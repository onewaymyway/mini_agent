"""world_simulator/forecast.py — 蒙特卡洛 / 敏感性 / 假设条件化 / 监测清单（第二十四轮 A6）。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.6、§8 A6。

## 这个模块做什么

A4 让引擎能按趋势模型 + 瓶颈路径抽样 + 里程碑判据**确定性地推演**一条未来。这条未来只是\"所有参数取点值、随机性取一个种子\"
下的一次抽样。本模块对同一份骨架（`anatomy_engine.simulate_step`，**不调 LLM**）反复推演，回答\"分布\"而不是\"一条故事\"：

| 能力 | 输出 |
|---|---|
| `run_forecast` 蒙特卡洛 | 每个里程碑/门槛/瓶颈的达成时间 P10/P50/P90 + \"视野内达成概率\"、每个指标的分位带、瓶颈的解决路径与顺序频率、未来树分支（结构化触发条件）的激活频率、阶段分布、**假设条件化结果** |
| `sensitivity` 敏感性 | 对每个带 `low/high` 的参数、每条路径时长、每个假设、每个事件频率区间，一次改一个，按对目标里程碑的影响排序（龙卷风图数据） |
| `what_if` | 改几个参数/假设/路径/事件频率后用**同一种子**重跑，和基线逐项比较 |
| `build_watchlist` | 先行信号监测清单：已有信号 + 由预测结果规则生成的\"该盯什么、先发生什么意味着走向哪条分支\"（不调 LLM） |
| `signal_observations` | 把现实回填里勾选的信号统计出来（接入 `reality_check`） |

## 随机性（计划 §5.6 的五个来源）

1. **参数不确定性**：趋势参数 `{value, low, high, dist}`，两端都有才抽样（triangular/uniform/lognormal，截断在 [low, high]）；
   只给一端或没给的参数视为**点估计**，在结果里单独列出（`meta.point_estimates`）。
2. **瓶颈路径**：成败（`p_success`）与耗时（`duration_days` 三角分布）。**真实推进里已经\"进行中\"的路径会被重新抽样**
   （成败重抽，耗时按\"已经过了这么久还没到期\"条件化重抽）——不泄漏真实运行里藏着的抽样结果。
3. **假设**：`prior_p_true` 的 Bernoulli；不成立时应用 `if_false.overrides`。
4. **事件先验**：泊松到达；先验带 `rate_range` 时频率本身也在区间内抽样；可选 `effects` 对骨架做数值冲击。
5. **随机游走趋势** 的噪声（引擎内部用同一套确定性哈希）。

**可复现**：第 `r` 次运行的全部随机性只由 `(seed, r)` 和各项的**身份**（元素/参数/路径名）决定，与方案里别的东西无关；
结果与是否被时间预算截断无关（截断只是少跑几次）。

## 诚实标注（不可省略）

所有区间旁固定标注 `HONEST_NOTE`：区间是**已声明参数的不确定性的函数，不是校准过的概率**；
`meta.params.llm_prior_share` 给出参数里仍是 LLM 先验的比例。时间精度降级（`elapsed_days` 缺失）也会标出。

## 刻意的取舍（如实记录，详见 `docs/anatomy_guide.md` §15）

- 预测不带 `vars`：事件先验/树分支条件里用 `var` 的写法在预测里**无法求值**（列在 `warnings`，永远不触发），
  只有 `metric/component/bottleneck` 判据能在骨架状态上求值；
- 树分支的\"激活\"= 触发条件**首次满足**，不模拟前置/互斥组裁决（`tree_grounding` 的完整规则要读 `vars` 和分支状态）；
- 路径 `p_success` 没有 `low/high`，不参与不确定性与敏感性，只在 `point_estimates` 里列出（`what_if` 可手改）；
- 精简模式不结算采用率（没有 LLM 在推进它），门槛 `open` 照常结算；
- 步长由预测自己选（默认视野约 5 年、60 步）：引擎的自治趋势与步长无关，里程碑时刻有一步的量化误差（`meta.time_resolution_days`）。

纯 Python：不调 LLM、不读写磁盘；唯一的墙钟依赖是时间预算（可注入时钟，测试用）。
"""

from __future__ import annotations

import copy
import math
import time
from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from world_simulator import anatomy as an
from world_simulator import anatomy_engine as ae
from world_simulator import element_registry as er
from world_simulator import event_sampler

# ── 常量 ─────────────────────────────────────────────────────────────

HONEST_NOTE = "区间由已声明参数的不确定性产生，不是校准过的概率。"

DEFAULT_HORIZON_DAYS = 1826.0       # 约 5 年
MAX_HORIZON_DAYS = 36500.0          # 约 100 年
DEFAULT_STEPS = 60
MIN_STEPS = 4
MAX_STEPS = 240
DEFAULT_TIME_BUDGET_SEC = 60.0
MIN_RUNS_BEFORE_TRUNCATE = 50       # 时间预算用尽时至少要跑完这么多次（不足 runs 时取 runs）
MAX_RUNS = 20000
CHECKPOINTS = 24                    # 指标分位带的取点数上限
MIN_COND_RUNS = 20                  # 假设条件化：每一侧至少这么多次运行才给结果
SENS_MIN_RUNS = 50
SENS_MAX_RUNS = 200
MAX_SENS_TARGETS = 8
MAX_ORDER_BOTTLENECKS = 8           # 瓶颈多于此数时不枚举完整顺序，只给\"最先解决\"频率
MAX_WATCH_FORECAST = 12             # 由预测生成的监测项条数上限
DISTS = ("triangular", "uniform", "lognormal")
_Z90 = 3.2897                       # 2×1.645：lognormal 把 [low, high] 当作约 P5–P95

Settings = Dict[str, Any]


# ═════════════════════════════════════════════════════════════════════
# 小工具
# ═════════════════════════════════════════════════════════════════════


def _num(x: Any) -> Optional[float]:
    if isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _quantile(sorted_vals: List[float], q: float) -> Optional[float]:
    """线性插值分位数；`sorted_vals` 可含 `inf`（= 视野内未达成）。结果为 `inf` 时返回 `None`。"""
    n = len(sorted_vals)
    if n == 0:
        return None
    pos = q * (n - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    a, b = sorted_vals[lo], sorted_vals[hi]
    if math.isinf(a):
        return None                      # a <= b，所以 b 也是 inf
    if math.isinf(b):
        return None                      # 落在有限值与 inf 之间 → 视为 inf（视野内未达成）
    return a + (b - a) * (pos - lo)


def _qs(vals: List[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    s = sorted(vals)
    return _quantile(s, 0.1), _quantile(s, 0.5), _quantile(s, 0.9)


def _round(x: Optional[float], nd: int = 3) -> Optional[float]:
    return None if x is None else round(float(x), nd)


def _label(line: Dict[str, Any]) -> str:
    return str(line.get("label") or line.get("id") or "")


def _lid(line: Dict[str, Any]) -> str:
    return str(line.get("id") or "").strip()


def _walk_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)


def _u(branch: str, tag: str) -> float:
    """按**身份**（而不是消耗顺序）取的确定性均匀数：`tag` 写元素/参数/路径的名字。方案结构变化（限定闭包、what-if 增删元素）
    不会让同一次运行里其它项的随机数错位——公共随机数因此与方案结构无关。"""
    return event_sampler.draw("forecast-rng", branch, 0, "fc", tag)


def _sample_param(spec: Dict[str, Any], u1: float, u2: float) -> float:
    """按 `dist` 在 `[low, high]` 内抽一个值（两端都有才调用）。`u1/u2` 是固定消耗的两个均匀数。"""
    lo, hi, val = spec["low"], spec["high"], spec["value"]
    if hi <= lo:
        return float(lo)
    dist = spec.get("dist") or "triangular"
    if dist == "uniform":
        return lo + u1 * (hi - lo)
    if dist == "lognormal" and lo > 0 and hi > 0:
        sigma = (math.log(hi) - math.log(lo)) / _Z90
        mu = math.log(val) if lo <= val <= hi and val > 0 else (math.log(lo) + math.log(hi)) / 2
        z = math.sqrt(-2.0 * math.log(max(u1, 1e-12))) * math.cos(2.0 * math.pi * u2)
        return min(hi, max(lo, math.exp(mu + sigma * z)))
    return ae._tri(u1, lo, min(hi, max(lo, val)), hi)


# ═════════════════════════════════════════════════════════════════════
# 编辑（what-if / 敏感性共用）
# ═════════════════════════════════════════════════════════════════════


def _find_line(lines: List[Dict[str, Any]], settings: Settings, element: Any) -> Optional[Dict[str, Any]]:
    ref = str(element or "").strip()
    if not ref:
        return None
    hit = er.resolve(settings, ref)
    if hit is None:
        return None
    lid = _lid(hit)
    return next((x for x in lines if _lid(x) == lid), None)


def _holder(a: Dict[str, Any], kind: str, rid: str) -> Optional[Dict[str, Any]]:
    part = {"metric": "metrics", "component": "components", "bottleneck": "bottlenecks"}.get(kind)
    if part is None:
        return None
    return next((x for x in a.get(part) or [] if x.get("id") == rid), None)


def apply_edits(settings: Settings, edits: Any) -> Tuple[Settings, List[Dict[str, Any]], List[str]]:
    """对 `settings` 做一组 what-if 编辑，返回 `(新 settings, 已应用的编辑, 错误说明)`。**不改入参**（写时复制：只深拷贝被改的元素）。

    编辑（`kind`）：
    - `param`：`{element, holder: "metric:<id>"|"component:<id>", name, field: "value"|"low"|"high", value}` 趋势参数；
    - `current`：`{element, holder, value}` 指标现值 / 组件就绪度（引擎会重新锚定）；
    - `duration`：`{element, bottleneck, path, field: "low"|"mode"|"high"|"all", value}` 路径耗时；
    - `p_success`：`{element, bottleneck, path, value}`；
    - `assumption`：`{element, id, p_true}`（0/1 即强制不成立/成立）；
    - `event_rate`：`{id, rate}` 事件先验频率（每年）。
    解析不到或写法非法的编辑不抛异常，原因写进错误说明（该条不生效）。
    """
    new = {**settings}
    lines = list(settings.get("causal_lines") or [])
    new["causal_lines"] = lines
    copied: Dict[str, Dict[str, Any]] = {}
    applied: List[Dict[str, Any]] = []
    errors: List[str] = []

    def writable(element: Any) -> Optional[Dict[str, Any]]:
        line = _find_line(lines, new, element)
        if line is None or not isinstance(line.get("anatomy"), dict):
            return None
        lid = _lid(line)
        if lid not in copied:
            cp = dict(line)
            cp["anatomy"] = copy.deepcopy(line["anatomy"])
            lines[lines.index(line)] = cp
            copied[lid] = cp
        return copied[lid]

    for i, ed in enumerate(edits if isinstance(edits, (list, tuple)) else []):
        if not isinstance(ed, dict):
            errors.append(f"第 {i + 1} 条编辑不是对象")
            continue
        kind = ed.get("kind")
        try:
            if kind == "event_rate":
                pid, rate = str(ed.get("id") or "").strip(), _num(ed.get("rate"))
                pri = [p for p in new.get("event_priors") or [] if isinstance(p, dict) and str(p.get("id") or p.get("description") or "").strip() == pid]
                if not pri or rate is None or rate < 0:
                    errors.append(f"event_rate 编辑无效：{pid!r}")
                    continue
                new["event_priors"] = [dict(p, rate_per_year=rate) if p is pri[0] else p for p in new["event_priors"]]
                applied.append(ed)
                continue
            line = writable(ed.get("element"))
            if line is None:
                errors.append(f"{kind} 编辑找不到带剖面的元素 {ed.get('element')!r}")
                continue
            a = line["anatomy"]
            if kind in ("param", "current"):
                _el, hk, hid = an.parse_ref(ed.get("holder"))
                h = _holder(a, hk or "metric", hid)
                v = _num(ed.get("value"))
                if h is None or v is None:
                    errors.append(f"{kind} 编辑找不到 {ed.get('holder')!r} 或值不是数字")
                    continue
                if kind == "current":
                    if (hk or "metric") == "metric":
                        h.setdefault("current", {})["value"] = v
                    else:
                        h["readiness"] = v
                else:
                    field = ed.get("field") or "value"
                    tkey = "trend" if (hk or "metric") == "metric" else "readiness_trend"
                    params = (h.get(tkey) or {}).get("params") or {}
                    name = str(ed.get("name") or "")
                    if field not in ("value", "low", "high") or name not in params or not isinstance(params[name], dict):
                        errors.append(f"param 编辑找不到参数 {ed.get('holder')}.{name}（或 field 非法）")
                        continue
                    params[name][field] = v
                applied.append(ed)
            elif kind in ("duration", "p_success"):
                bn = _holder(a, "bottleneck", str(ed.get("bottleneck") or ""))
                path = next((p for p in (bn or {}).get("resolution_paths") or [] if p.get("id") == ed.get("path")), None)
                v = _num(ed.get("value"))
                if path is None or v is None:
                    errors.append(f"{kind} 编辑找不到路径 {ed.get('bottleneck')}/{ed.get('path')} 或值不是数字")
                    continue
                if kind == "p_success":
                    path["p_success"] = min(1.0, max(0.0, v))
                else:
                    field = ed.get("field") or "all"
                    dur = path.setdefault("duration_days", {})
                    for k in (("low", "mode", "high") if field == "all" else (field,)):
                        if k in ("low", "mode", "high"):
                            dur[k] = max(0.0, v)
                applied.append(ed)
            elif kind == "assumption":
                asm = next((x for x in a.get("assumptions") or [] if x.get("id") == ed.get("id")), None)
                p = _num(ed.get("p_true"))
                if asm is None or p is None:
                    errors.append(f"assumption 编辑找不到假设 {ed.get('id')!r} 或 p_true 不是数字")
                    continue
                asm["prior_p_true"] = min(1.0, max(0.0, p))
                applied.append(ed)
            else:
                errors.append(f"未知编辑类型 {kind!r}")
        except Exception as exc:  # noqa: BLE001 — 编辑写法千奇百怪，不能让它抛出
            errors.append(f"第 {i + 1} 条编辑出错：{exc}")
    return new, applied, errors


# ═════════════════════════════════════════════════════════════════════
# 方案（plan）：从 settings 里抽出\"要抽样什么、要记录什么\"
# ═════════════════════════════════════════════════════════════════════


def _closure(settings: Settings, element: Any) -> Optional[List[str]]:
    """`element` 及其通过 `元素#子项` 显式引用（判据/前置/驱动量/覆盖项）到的元素（传递闭包）。解析不到 → `None`。
    引擎里跨元素只能靠显式前缀引用（本元素内的无前缀引用只在本元素解析），所以这个闭包是**精确**的。"""
    root = er.resolve(settings, str(element or "").strip()) if element else None
    if root is None:
        return None
    by_id = {_lid(x): x for x in ae._engine_lines(settings)}
    start = _lid(root)
    if start not in by_id:
        return None
    seen, todo = {start}, [start]
    while todo:
        cur = todo.pop()
        for text in _walk_strings(by_id[cur].get("anatomy") or {}):
            if "#" not in text:
                continue
            prefix = text.split("#", 1)[0].strip()
            if not prefix or len(prefix) > 80:
                continue
            hit = er.resolve(settings, prefix)
            hid = _lid(hit) if hit else ""
            if hid in by_id and hid not in seen:
                seen.add(hid)
                todo.append(hid)
    return [x for x in by_id if x in seen]


def _compile_cond(settings: Settings, cond: Any) -> Dict[str, Any]:
    """把条件编译成预测里能求值的形态。`fast`：只含 metric/component/bottleneck 叶子（走引擎求值器，读骨架状态）；
    `generic`：含 `tech`/`element` 叶子（走 `event_sampler.evaluate_condition`）；`unevaluable`：含 `var`（预测不带 vars）或非法。"""
    if cond in (None, [], {}):
        return {"mode": "always"}
    raw = {"all": cond} if isinstance(cond, list) else cond
    leaves: List[Dict[str, Any]] = []

    def walk(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        for key in ("all", "any"):
            if key in node:
                return isinstance(node[key], list) and all(walk(c) for c in node[key])
        if "not" in node:
            return walk(node["not"])
        leaves.append(node)
        return True

    if not walk(raw):
        return {"mode": "unevaluable", "reason": "条件写法非法"}
    if any("var" in lf for lf in leaves):
        return {"mode": "unevaluable", "reason": "条件依赖 vars（预测不模拟 vars）"}
    kinds = {k for lf in leaves for k in ("metric", "component", "bottleneck", "tech", "element") if k in lf}
    if not kinds:
        return {"mode": "unevaluable", "reason": "条件没有可求值的叶子"}
    if kinds <= {"metric", "component", "bottleneck"}:
        norm = an.normalize_criteria(raw)
        if norm is None:
            return {"mode": "unevaluable", "reason": "条件写法非法"}
        probe = ae._Ctx(settings, step=0, sim_id="", branch="", vars_={}, events=[], stale_ids=[], lean=True)
        for line in an.lines_with_anatomy(settings):
            lid = _lid(line)
            probe.lines[lid], probe.work[lid] = line, an.get_anatomy(line) or {}
            probe.order.append(lid)
        return {"mode": "fast", "norm": norm, "owner": ae._leaf_owner(probe, norm)}
    return {"mode": "generic", "cond": cond}


class _Plan:
    """一次预测要抽样/记录的东西（只读，所有运行共用）。"""

    def __init__(self, settings: Settings, elements: Optional[List[str]] = None) -> None:
        self.settings = settings
        all_lines = [x for x in settings.get("causal_lines") or [] if isinstance(x, dict)]
        engine = ae._engine_lines(settings)
        if elements is not None:
            keep = set(elements)
            engine = [x for x in engine if _lid(x) in keep]
        self.engine_ids = [_lid(x) for x in engine]
        self.engine_set = set(self.engine_ids)
        # 试验用线：被模拟的元素换成可改的副本（只拷贝 anatomy / lifecycle），其它线共用引用；被排除的引擎线不放进来
        dropped = {_lid(x) for x in ae._engine_lines(settings)} - self.engine_set
        self.base_lines = [x for x in all_lines if _lid(x) not in dropped]
        self.label = {_lid(x): _label(x) for x in all_lines}
        self.clock0: Dict[str, float] = {}
        self.params: List[Dict[str, Any]] = []
        self.paths: List[Dict[str, Any]] = []
        self.assumptions: List[Dict[str, Any]] = []
        self.milestones: List[Dict[str, Any]] = []
        self.metrics: List[Dict[str, Any]] = []
        self.bottlenecks: List[Dict[str, Any]] = []
        self.gates: List[Dict[str, Any]] = []
        self.stage_els: List[str] = []
        self.priors: List[Dict[str, Any]] = []
        self.branches: List[Dict[str, Any]] = []
        self.warnings: List[str] = []
        self.max_events = int(event_sampler.get_params(settings)["max_events_per_step"])
        self.event_salt = str(settings.get("event_sampling_salt") or "")
        self._scan(engine, all_lines)

    # —— 扫描 ——
    def _scan(self, engine: List[Dict[str, Any]], all_lines: List[Dict[str, Any]]) -> None:
        for line in engine:
            el, a = _lid(line), an.get_anatomy(line) or {}
            self.clock0[el] = float((a.get("meta") or {}).get("clock_day") or 0.0)
            for part, kind in (("metrics", "metric"), ("components", "component")):
                for h in a.get(part) or []:
                    tkey = "trend" if kind == "metric" else "readiness_trend"
                    state = an.basis_of(h.get("current") if kind == "metric" else h)["state"]
                    for name, p in ((h.get(tkey) or {}).get("params") or {}).items():
                        if not isinstance(p, dict) or _num(p.get("value")) is None:
                            continue
                        lo, hi = _num(p.get("low")), _num(p.get("high"))
                        self.params.append({
                            "element": el, "kind": kind, "hid": h["id"], "name": name, "value": float(p["value"]),
                            "low": lo, "high": hi, "dist": p.get("dist") if p.get("dist") in DISTS else None,
                            "ranged": lo is not None and hi is not None, "prior": state == "llm_prior",
                            "label": f"{self.label.get(el, el)} / {h.get('name') or h['id']}.{name}",
                        })
                    if kind == "metric":
                        cur = h.get("current") or {}
                        self.metrics.append({
                            "element": el, "id": h["id"], "name": h.get("name") or h["id"], "unit": h.get("unit") or "",
                            "start": ae.series_value(h, "metric"), "target": h.get("target"), "has_trend": bool(h.get("trend")),
                            "as_of": cur.get("as_of"),
                        })
            for bn in a.get("bottlenecks") or []:
                state = an.basis_of(bn)["state"]
                self.bottlenecks.append({"element": el, "id": bn["id"], "name": bn.get("name") or bn["id"], "status0": bn.get("status") or "open",
                                         "paths": [p["id"] for p in bn.get("resolution_paths") or []]})
                for p in bn.get("resolution_paths") or []:
                    dur = p.get("duration_days") or {}
                    self.paths.append({
                        "element": el, "bottleneck": bn["id"], "path": p["id"], "p_success": p.get("p_success"),
                        "duration": {k: _num(dur.get(k)) for k in ("low", "mode", "high")}, "prior": state == "llm_prior",
                        "label": f"{self.label.get(el, el)} / 瓶颈 {bn.get('name') or bn['id']} / 路径 {p['id']}",
                    })
            for ms in a.get("milestones") or []:
                self.milestones.append({
                    "element": el, "id": ms["id"], "name": ms.get("name") or ms.get("desc") or ms["id"],
                    "reached0": "reached_step" in ms, "day0": ms.get("reached_sim_day"), "stage": ms.get("maps_to_stage") or "",
                    "has_criteria": bool(ms.get("criteria")),
                })
            for g in a.get("adoption_gates") or []:
                self.gates.append({"element": el, "id": g["id"], "name": g.get("name") or g.get("market") or g["id"], "open0": bool(g.get("open")),
                                   "has_criteria": bool(g.get("criteria"))})
            for asm in a.get("assumptions") or []:
                p = _num(asm.get("prior_p_true"))
                self.assumptions.append({
                    "element": el, "id": asm["id"], "statement": asm.get("statement") or asm["id"], "p_true": p,
                    "overrides": (asm.get("if_false") or {}).get("overrides") or [],
                })
            if isinstance(line.get("lifecycle"), dict) and ae.drives_stage(an.get_params(self.settings), a, line["lifecycle"]):
                self.stage_els.append(el)
        for p in event_sampler.get_priors(self.settings)[0]:
            if not p["enabled"] or not p["confirmed"]:
                continue
            comp = _compile_cond(self.settings, p["condition"])
            if comp["mode"] == "unevaluable":
                self.warnings.append(f"事件先验「{p['id']}」在预测里永远不会触发：{comp['reason']}")
                continue
            self.priors.append({**p, "comp": comp})
        from world_simulator import tree_grounding as tg

        for line in all_lines:
            for br in tg._top_branches(line):
                cond = br.get("trigger_condition")
                if not cond or tg._status(br) not in tg._OPEN:
                    continue
                comp = _compile_cond(self.settings, cond)
                self.branches.append({
                    "element": _lid(line), "id": tg._bid(br), "label": br.get("label") or br.get("name") or tg._bid(br),
                    "comp": comp,
                })
        self.horizon_auto_days = self._auto_horizon()

    def _auto_horizon(self) -> float:
        longest = max([p["duration"].get("high") or p["duration"].get("mode") or 0.0 for p in self.paths] or [0.0])
        return min(MAX_HORIZON_DAYS, max(DEFAULT_HORIZON_DAYS, math.ceil(longest * 1.25)))

    # —— 统计用 ——
    def param_stats(self) -> Dict[str, Any]:
        rows = self.params
        total = len(rows) + len(self.paths) * 2  # 路径各有 p_success 与耗时两类参数
        ranged = sum(1 for r in rows if r["ranged"]) + sum(1 for p in self.paths if p["duration"].get("low") is not None and p["duration"].get("high") is not None)
        prior = sum(1 for r in rows if r["prior"]) + sum(2 for p in self.paths if p["prior"])
        point = [r["label"] + "（趋势参数，只给点值/单端）" for r in rows if not r["ranged"]]
        point += [p["label"] + "（p_success 没有区间，按点估计抽成败）" for p in self.paths]
        point += [p["label"] + "（耗时缺 low/high，按点值或半区间处理）" for p in self.paths
                  if p["duration"].get("low") is None or p["duration"].get("high") is None]
        return {
            "total": total, "ranged": ranged, "point": total - ranged,
            "llm_prior_share": round(prior / total, 3) if total else None,
            "point_list": point,
        }


# ═════════════════════════════════════════════════════════════════════
# 单次运行
# ═════════════════════════════════════════════════════════════════════


def _fresh_lines(plan: _Plan) -> List[Dict[str, Any]]:
    """一次运行的试验用线：被模拟的元素只深拷贝 `anatomy`/`lifecycle`（线上的大块 `future_tree` 等共用引用，运行里不会被改）。"""
    out: List[Dict[str, Any]] = []
    for line in plan.base_lines:
        if _lid(line) in plan.engine_set:
            cp = dict(line)
            cp["anatomy"] = copy.deepcopy(line.get("anatomy"))
            if isinstance(line.get("lifecycle"), dict):
                cp["lifecycle"] = copy.deepcopy(line["lifecycle"])
            out.append(cp)
        else:
            out.append(line)
    return out


def _index(trial_lines: List[Dict[str, Any]], plan: _Plan) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    idx: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for line in trial_lines:
        el = _lid(line)
        if el not in plan.engine_set:
            continue
        a = line["anatomy"]
        for part, kind in (("metrics", "metric"), ("components", "component"), ("bottlenecks", "bottleneck"),
                           ("milestones", "milestone"), ("adoption_gates", "gate")):
            for h in a.get(part) or []:
                idx[(el, kind, h["id"])] = h
    return idx


def _apply_override(plan: _Plan, idx: Dict[Tuple[str, str, str], Dict[str, Any]], host: str, ov: Dict[str, Any], warned: set) -> None:
    element, kind, rid = an.parse_ref(ov.get("ref"))
    el = host
    if element:
        hit = er.resolve(plan.settings, element)
        el = _lid(hit) if hit else ""
    h = idx.get((el, kind or "", rid))
    if h is None:
        warned.add(f"假设覆盖项引用不存在：{ov.get('ref')!r}")
        return
    for key, val in (ov.get("set") or {}).items():
        v = _num(val)
        if kind in ("metric", "component"):
            tkey = "trend" if kind == "metric" else "readiness_trend"
            params = (h.get(tkey) or {}).get("params") or {}
            if key == "value" and kind == "metric" and v is not None:
                h.setdefault("current", {})["value"] = v
            elif key == "readiness" and kind == "component" and v is not None:
                h["readiness"] = v
            elif key in params and isinstance(params[key], dict) and v is not None:
                params[key]["value"] = v
            else:
                warned.add(f"假设覆盖项未应用：{kind}:{rid} 不认识键 {key!r}")
        elif kind == "bottleneck":
            if key == "status" and val in ("open", "resolved", "exhausted"):
                h["status"] = val
            elif key == "duration_scale" and v is not None and v > 0:
                for p in h.get("resolution_paths") or []:
                    d = p.get("duration_days") or {}
                    for k in ("low", "mode", "high"):
                        if _num(d.get(k)) is not None:
                            d[k] = d[k] * v
            elif key == "p_success_scale" and v is not None and v >= 0:
                for p in h.get("resolution_paths") or []:
                    p["p_success"] = min(1.0, (p.get("p_success", 1.0)) * v)
            else:
                warned.add(f"假设覆盖项未应用：bottleneck:{rid} 不认识键 {key!r}")
        else:
            warned.add(f"假设覆盖项未应用：不支持的引用类型 {ov.get('ref')!r}")


def _redraw_running(plan: _Plan, idx: Dict[Tuple[str, str, str], Dict[str, Any]], branch: str) -> None:
    """真实推进里\"进行中\"的路径：成败与耗时**重抽**（耗时条件于\"已经过了这么久还没到期\"，最多重试 8 次，仍不满足就取\"已过时长 + 1 天\"），
    不泄漏真实运行藏着的抽样结果。随机数按路径身份取（见 `_u`）。"""
    for (el, kind, bid), bn in idx.items():
        if kind != "bottleneck":
            continue
        for p in bn.get("resolution_paths") or []:
            if p.get("status") != "running":
                continue
            tag = f"redraw/{el}/{bid}/{p['id']}"
            u_ok = _u(branch, tag + "/ok")
            dur = p.get("duration_days") or {}
            started = float(p.get("started_day") or 0.0)
            elapsed = max(0.0, plan.clock0.get(el, 0.0) - started)
            days = next(
                (d for d in (ae._tri(_u(branch, f"{tag}/dur{i}"), dur.get("low"), dur.get("mode"), dur.get("high")) for i in range(8)) if d > elapsed),
                elapsed + 1.0,
            )
            p["outcome"] = "success" if u_ok < p.get("p_success", 1.0) else "fail"
            p["resolve_at_day"] = started + days


def _apply_effect(plan: _Plan, idx: Dict[Tuple[str, str, str], Dict[str, Any]], effect: Dict[str, Any], unresolved: set) -> None:
    element, kind, rid = an.parse_ref(effect.get("ref"))
    el = None
    if element:
        hit = er.resolve(plan.settings, element)
        el = _lid(hit) if hit else None
    else:
        owners = [e for (e, k, i) in idx if k == (kind or "metric") and i == rid]
        el = owners[0] if len(owners) == 1 else None
    h = idx.get((el or "", kind or "metric", rid))
    if h is None:
        unresolved.add(str(effect.get("ref")))
        return
    if "shift_days" in effect:
        for p in h.get("resolution_paths") or []:
            if p.get("status") == "running":
                p["resolve_at_day"] = max(plan.clock0.get(el or "", 0.0), p.get("resolve_at_day", 0.0) + effect["shift_days"])
        return
    cur = ae.series_value(h, kind or "metric")
    if cur is None:
        return
    v = effect["value"]
    new = cur + v if effect["op"] == "add" else (cur * v if effect["op"] == "mul" else v)
    ae.set_series_value(h, kind or "metric", new)


class _Run:
    """一次运行的结果记录。"""

    __slots__ = ("ms", "cp", "bn", "gates", "branch", "stage", "truth")

    def __init__(self) -> None:
        self.ms: Dict[Tuple[str, str], float] = {}
        self.cp: Dict[Tuple[str, str], List[float]] = {}
        self.bn: Dict[Tuple[str, str], Tuple[Optional[float], str, str]] = {}
        self.gates: Dict[Tuple[str, str], Optional[float]] = {}
        self.branch: Dict[Tuple[str, str], Optional[float]] = {}
        self.stage: Dict[str, str] = {}
        self.truth: Dict[Tuple[str, str], bool] = {}


def _eval_comp(comp: Dict[str, Any], ctx: Any, trial: Settings) -> bool:
    mode = comp["mode"]
    if mode == "always":
        return True
    if mode == "fast":
        return bool(ae._eval_node(ctx, comp["owner"], comp["norm"])[0])
    if mode == "generic":
        return bool(event_sampler.evaluate_condition(comp["cond"], {}, trial)[0])
    return False


def _simulate(
    plan: _Plan, r: int, *, seed: int, steps: int, step_days: float, sim_id: str, point: bool, cp_steps: List[int],
) -> Tuple[_Run, set]:
    branch = f"mc|{seed}|{r}"
    trial_lines = _fresh_lines(plan)
    trial = {**plan.settings, "causal_lines": trial_lines, "event_sampling_common_random_numbers": False}
    idx = _index(trial_lines, plan)
    warned: set = set()
    rec = _Run()
    # —— 随机数一律按身份取（`_u`），公共随机数与方案结构无关 ——
    _redraw_running(plan, idx, branch)
    # 参数
    if not point:
        for spec in plan.params:
            if not spec["ranged"]:
                continue
            h = idx.get((spec["element"], spec["kind"], spec["hid"]))
            tkey = "trend" if spec["kind"] == "metric" else "readiness_trend"
            tag = f"param/{spec['element']}/{spec['kind']}:{spec['hid']}.{spec['name']}"
            if h is not None:
                h[tkey]["params"][spec["name"]]["value"] = _sample_param(spec, _u(branch, tag + "/1"), _u(branch, tag + "/2"))
    # 假设
    for asm in plan.assumptions:
        p = asm["p_true"]
        if p is None:
            continue
        truth = (p >= 0.5) if point else (_u(branch, f"assumption/{asm['element']}/{asm['id']}") < p)
        rec.truth[(asm["element"], asm["id"])] = truth
        if not truth:
            for ov in asm["overrides"]:
                _apply_override(plan, idx, asm["element"], ov, warned)
    # 事件频率（有 rate_range 才抽）
    rates: List[float] = []
    for pri in plan.priors:
        rr = pri.get("rate_range")
        rates.append(
            pri["rate_per_year"] if (point or not rr)
            else ae._tri(_u(branch, f"rate/{pri['id']}"), rr["low"], min(rr["high"], max(rr["low"], pri["rate_per_year"])), rr["high"])
        )
    # 初始锚定（零时长）：保证每个指标都有引擎状态，覆盖项/现值编辑在这里生效
    ae.simulate_step(trial, step=0, days=0.0, sim_id=sim_id, branch=branch)
    # 条件求值上下文（读同一批原地修改的剖面）
    cctx = ae._Ctx(trial, step=0, sim_id="", branch="", vars_={}, events=[], stale_ids=[], lean=True)
    for line in trial_lines:
        lid = _lid(line)
        if lid in plan.engine_set:
            cctx.lines[lid], cctx.work[lid] = line, line["anatomy"]
            cctx.order.append(lid)
    last_hit: Dict[str, int] = {}
    gate_open: Dict[Tuple[str, str], Optional[float]] = {(g["element"], g["id"]): None for g in plan.gates}
    br_day: Dict[Tuple[str, str], Optional[float]] = {(b["element"], b["id"]): None for b in plan.branches if b["comp"]["mode"] in ("fast", "generic", "always")}
    cps = set(cp_steps)
    series_keys = [(m["element"], m["id"]) for m in plan.metrics]
    for key in series_keys:
        rec.cp[key] = []

    def snapshot() -> None:
        for el, mid in series_keys:
            v = ae.series_value(idx[(el, "metric", mid)], "metric")
            rec.cp[(el, mid)].append(float("nan") if v is None else float(v))

    if 0 in cps:
        snapshot()
    for step in range(1, steps + 1):
        hits: List[Dict[str, Any]] = []
        for pri, rate in zip(plan.priors, rates):
            last = last_hit.get(pri["id"])
            if pri["cooldown_steps"] > 0 and last is not None and step - last <= pri["cooldown_steps"]:
                continue
            if pri["comp"]["mode"] != "always" and not _eval_comp(pri["comp"], cctx, trial):
                continue
            u = event_sampler.draw(sim_id, branch, step, plan.event_salt, pri["id"])
            if u < event_sampler.step_probability(rate, step_days):
                hits.append({"id": pri["id"], "draw": u, "prior": pri})
        hits.sort(key=lambda h: h["draw"])
        hits = hits[: plan.max_events]
        unresolved: set = set()
        for h in hits:
            last_hit[h["id"]] = step
            for eff in h["prior"].get("effects") or []:
                _apply_effect(plan, idx, eff, unresolved)
        warned |= {f"事件 effects 引用解析不到：{x}" for x in unresolved}
        ae.simulate_step(trial, step=step, days=step_days, sim_id=sim_id, branch=branch, sampled_events=[{"id": h["id"]} for h in hits])
        now = step * step_days
        for key in gate_open:
            if gate_open[key] is None and idx[(key[0], "gate", key[1])].get("open"):
                gate_open[key] = now
        for b in plan.branches:
            key = (b["element"], b["id"])
            if key in br_day and br_day[key] is None and _eval_comp(b["comp"], cctx, trial):
                br_day[key] = now
        if step in cps:
            snapshot()
    # —— 收尾 ——
    for m in plan.milestones:
        if m["reached0"]:
            continue
        h = idx[(m["element"], "milestone", m["id"])]
        rec.ms[(m["element"], m["id"])] = max(0.0, float(h["reached_sim_day"]) - plan.clock0[m["element"]]) if "reached_step" in h else math.inf
    for b in plan.bottlenecks:
        h = idx[(b["element"], "bottleneck", b["id"])]
        done = h.get("status") == "resolved" and b["status0"] != "resolved"
        rec.bn[(b["element"], b["id"])] = (
            max(0.0, float(h["resolved_day"]) - plan.clock0[b["element"]]) if done and "resolved_day" in h else None,
            str(h.get("resolved_by") or ""), str(h.get("status") or "open"),
        )
    rec.gates = gate_open
    rec.branch = br_day
    for line in trial_lines:
        if _lid(line) in plan.stage_els:
            rec.stage[_lid(line)] = str((line.get("lifecycle") or {}).get("stage") or "")
    return rec, warned


# ═════════════════════════════════════════════════════════════════════
# 聚合
# ═════════════════════════════════════════════════════════════════════


def _time_precision(history: Any) -> Tuple[bool, str]:
    """最近几步里引擎有没有因为 LLM 没给 `elapsed_days` 而按占位天数结算（A4 流水里 `time.source == "fallback"`）。"""
    states = [s for s in (history or []) if int(getattr(s, "step", 0) or 0) > 0][-10:]
    bad = 0
    for st in states:
        for e in getattr(st, "anatomy_trace", None) or []:
            if isinstance(e, dict) and e.get("kind") == "time" and e.get("source") == "fallback":
                bad += 1
                break
    if bad:
        return True, f"最近 {len(states)} 步里有 {bad} 步没有 elapsed_days，引擎按占位天数结算——时间精度降级，区间可能系统性偏移"
    return False, ""


def _pct(vals: List[float]) -> Dict[str, Optional[float]]:
    p10, p50, p90 = _qs(vals)
    return {"p10": _round(p10, 1), "p50": _round(p50, 1), "p90": _round(p90, 1)}


def _reached_frac(vals: List[float]) -> float:
    return sum(1 for v in vals if not math.isinf(v)) / len(vals) if vals else 0.0


def _aggregate(plan: _Plan, recs: List[_Run], step_days: float, cp_steps: List[int]) -> Dict[str, Any]:
    n = len(recs)
    lab = plan.label
    out: Dict[str, Any] = {}
    # —— 里程碑 ——
    ms_rows = []
    for m in plan.milestones:
        key = (m["element"], m["id"])
        row = {"element": m["element"], "element_label": lab.get(m["element"], m["element"]), "id": m["id"], "name": m["name"], "stage": m["stage"]}
        if m["reached0"]:
            day0 = _num(m["day0"])
            row.update(status="reached", reached_day=_round(None if day0 is None else day0 - plan.clock0[m["element"]], 1))
        elif not m["has_criteria"]:
            row.update(status="no_criteria", p_reached=None, p10=None, p50=None, p90=None)
        else:
            vals = [r.ms[key] for r in recs]
            row.update(status="pending", p_reached=round(_reached_frac(vals), 4), **_pct(vals))
        ms_rows.append(row)
    out["milestones"] = ms_rows
    # —— 指标分位带 ——
    metric_rows = []
    for m in plan.metrics:
        key = (m["element"], m["id"])
        cols = list(zip(*[r.cp[key] for r in recs])) if recs else []
        bands = {"p10": [], "p50": [], "p90": []}
        for col in cols:
            vals = sorted(v for v in col if v == v)
            for name, q in (("p10", 0.1), ("p50", 0.5), ("p90", 0.9)):
                bands[name].append(_round(_quantile(vals, q), 6) if vals else None)
        metric_rows.append({
            "element": m["element"], "element_label": lab.get(m["element"], m["element"]), "id": m["id"], "name": m["name"], "unit": m["unit"],
            "start": _round(m["start"], 6), "target": m["target"], "has_trend": m["has_trend"],
            "days": [_round(c * step_days, 1) for c in cp_steps], **bands,
        })
    out["metrics"] = metric_rows
    # —— 瓶颈 ——
    bn_rows, open_keys = [], []
    for b in plan.bottlenecks:
        key = (b["element"], b["id"])
        row = {"element": b["element"], "element_label": lab.get(b["element"], b["element"]), "id": b["id"], "name": b["name"], "status0": b["status0"]}
        if b["status0"] == "resolved":
            row["status"] = "resolved"
        else:
            open_keys.append(key)
            vals = [(r.bn[key][0] if r.bn[key][0] is not None else math.inf) for r in recs]
            by = Counter(r.bn[key][1] for r in recs if r.bn[key][0] is not None and r.bn[key][1])
            done = sum(by.values()) or 1
            row.update(
                status="open", p_resolved=round(_reached_frac(vals), 4), **_pct(vals),
                by_path={pid: round(c / n, 4) for pid, c in sorted(by.items())},
                exhausted=round(sum(1 for r in recs if r.bn[key][2] == "exhausted") / n, 4),
            )
            row["by_path_share"] = {pid: round(c / done, 4) for pid, c in sorted(by.items())}
        bn_rows.append(row)
    out["bottlenecks"] = bn_rows
    order: Dict[str, Any] = {"first_resolved": {}, "none_resolved": 0.0, "orders": []}
    if open_keys:
        firsts: Counter = Counter()
        orders: Counter = Counter()
        for r in recs:
            done = sorted(((r.bn[k][0], k) for k in open_keys if r.bn[k][0] is not None))
            if not done:
                firsts["__none__"] += 1
                continue
            firsts[done[0][1]] += 1
            if len(open_keys) <= MAX_ORDER_BOTTLENECKS:
                orders[tuple(k for _d, k in done)] += 1
        name = {(b["element"], b["id"]): f"{lab.get(b['element'], b['element'])}/{b['name']}" for b in plan.bottlenecks}
        order["none_resolved"] = round(firsts.pop("__none__", 0) / n, 4)
        order["first_resolved"] = {name[k]: round(c / n, 4) for k, c in firsts.most_common()}
        order["orders"] = [{"order": [name[k] for k in ks], "freq": round(c / n, 4)} for ks, c in orders.most_common(5)]
    out["resolution_order"] = order
    # —— 门槛 ——
    gate_rows = []
    for g in plan.gates:
        key = (g["element"], g["id"])
        row = {"element": g["element"], "element_label": lab.get(g["element"], g["element"]), "id": g["id"], "name": g["name"]}
        if g["open0"]:
            row["status"] = "open"
        elif not g["has_criteria"]:
            row["status"] = "no_criteria"
        else:
            vals = [(r.gates[key] if r.gates.get(key) is not None else math.inf) for r in recs]
            row.update(status="closed", p_ever_open=round(_reached_frac(vals), 4), **_pct(vals))
        gate_rows.append(row)
    out["gates"] = gate_rows
    # —— 未来树分支 ——
    br_rows = []
    for b in plan.branches:
        key = (b["element"], b["id"])
        row = {"element": b["element"], "element_label": lab.get(b["element"], b["element"]), "branch_id": b["id"], "label": b["label"]}
        if b["comp"]["mode"] == "unevaluable":
            row.update(evaluable=False, reason=b["comp"]["reason"])
        else:
            vals = [(r.branch[key] if r.branch.get(key) is not None else math.inf) for r in recs]
            row.update(evaluable=True, p_active=round(_reached_frac(vals), 4), **_pct(vals))
        br_rows.append(row)
    out["branches"] = br_rows
    # —— 阶段分布 ——
    out["stages"] = [
        {"element": el, "element_label": lab.get(el, el),
         "final": {k: round(c / n, 4) for k, c in Counter(r.stage.get(el, "") for r in recs).most_common()}}
        for el in plan.stage_els
    ]
    # —— 假设条件化 ——
    cond_rows = []
    pending = [m for m in plan.milestones if not m["reached0"] and m["has_criteria"]]
    for asm in plan.assumptions:
        akey = (asm["element"], asm["id"])
        if asm["p_true"] is None:
            continue
        tr = [r for r in recs if r.truth.get(akey) is True]
        fa = [r for r in recs if r.truth.get(akey) is False]
        row = {
            "element": asm["element"], "element_label": lab.get(asm["element"], asm["element"]), "id": asm["id"], "statement": asm["statement"],
            "prior_p_true": asm["p_true"], "n_true": len(tr), "n_false": len(fa), "has_overrides": bool(asm["overrides"]),
            "sufficient": len(tr) >= MIN_COND_RUNS and len(fa) >= MIN_COND_RUNS, "effects": [],
        }
        if row["sufficient"]:
            for m in pending:
                mk = (m["element"], m["id"])
                vt, vf = [r.ms[mk] for r in tr], [r.ms[mk] for r in fa]
                pt, pf = _reached_frac(vt), _reached_frac(vf)
                _a, t50, _b = _qs(vt)
                _c, f50, _d = _qs(vf)
                row["effects"].append({
                    "milestone": m["id"], "element": m["element"], "name": m["name"],
                    "p_reached_true": round(pt, 4), "p_reached_false": round(pf, 4),
                    "p50_true": _round(t50, 1), "p50_false": _round(f50, 1),
                    "delta_p50": _round(None if t50 is None or f50 is None else f50 - t50, 1), "delta_p": round(pf - pt, 4),
                })
            row["effects"].sort(key=lambda e: -(abs(e["delta_p"]) + abs(e["delta_p50"] or 0.0) / max(1.0, step_days * max(cp_steps or [1]))))
            row["effects"] = row["effects"][:5]
        cond_rows.append(row)
    out["conditional"] = cond_rows
    return out


# ═════════════════════════════════════════════════════════════════════
# 蒙特卡洛
# ═════════════════════════════════════════════════════════════════════


def _prepare(settings: Optional[Settings], element: Any) -> Tuple[Optional[_Plan], str]:
    if not settings or not an.is_enabled(settings):
        return None, "剖面功能未开启（需要元素模式与 anatomy_enabled）"
    if not ae.is_active(settings):
        return None, "没有带可结算内容（指标/组件/瓶颈/里程碑/门槛）的元素，无法预测"
    elements = None
    if element:
        elements = _closure(settings, element)
        if elements is None:
            return None, f"找不到带可结算剖面的元素 {element!r}"
    return _Plan(settings, elements), ""


def _grid(plan: _Plan, params: Dict[str, Any], runs: Any, seed: Any, horizon_days: Any, steps: Any) -> Dict[str, Any]:
    r = _num(runs)
    nruns = int(min(MAX_RUNS, max(1, r if r is not None else params["mc_runs"])))
    sd = _num(seed)
    h = _num(horizon_days)
    horizon = float(min(MAX_HORIZON_DAYS, max(30.0, h if h is not None and h > 0 else plan.horizon_auto_days)))
    st = _num(steps)
    nsteps = int(min(MAX_STEPS, max(MIN_STEPS, st if st is not None else DEFAULT_STEPS)))
    k = min(CHECKPOINTS, nsteps + 1)
    cp = sorted({int(round(i * nsteps / (k - 1))) for i in range(k)}) if k > 1 else [0]
    return {
        "runs": nruns, "seed": int(sd) if sd is not None else int(params["mc_seed"]), "horizon_days": horizon,
        "steps": nsteps, "step_days": horizon / nsteps, "cp_steps": cp, "horizon_auto": h is None or h <= 0,
    }


def _budget(value: Any) -> float:
    b = _num(value)
    if b is None:
        return DEFAULT_TIME_BUDGET_SEC
    return math.inf if b <= 0 else b


def run_forecast(
    settings: Optional[Settings],
    history: Any = None,
    *,
    runs: Any = None,
    seed: Any = None,
    horizon_days: Any = None,
    steps: Any = None,
    time_budget_sec: Any = None,
    sim_id: str = "",
    element: Any = None,
    point: bool = False,
    _clock: Optional[Callable[[], float]] = None,
) -> Dict[str, Any]:
    """对骨架做蒙特卡洛，返回可 JSON 序列化的结果（见模块文档）。**不改 `settings`，不调 LLM，不读写磁盘。**

    - `runs` 默认 `anatomy_params.mc_runs`（1000），`seed` 默认 `mc_seed`；`horizon_days` 默认约 5 年（有更长的路径时自动拉长），
      `steps` 默认 60 步（步长 = 视野 / 步数）；
    - `time_budget_sec`：墙钟预算（默认 60 秒；`<= 0` 不限）。预算用尽且已跑满 `MIN_RUNS_BEFORE_TRUNCATE` 次时提前收工，
      `meta.truncated = True`、`meta.runs_done` 为实际次数——**前 N 次的结果与不截断时完全一致**；
    - `element`：只模拟这个元素及其显式引用的元素（依赖闭包），其余元素不参与（更快）；
    - `point=True`：点估计情景（参数取点值、假设取更可能的一侧、事件频率取点值；路径与随机游走仍随机）——敏感性分析的基线。
    不能预测时返回 `{"ok": False, "reason": ...}`。
    """
    plan, why = _prepare(settings, element)
    if plan is None:
        return {"ok": False, "reason": why, "honesty": HONEST_NOTE}
    assert settings is not None
    clock = _clock or time.monotonic
    params = an.get_params(settings)
    grid = _grid(plan, params, runs, seed, horizon_days, steps)
    budget = _budget(time_budget_sec)
    sim = str(sim_id or "forecast")
    t0 = clock()
    recs: List[_Run] = []
    warned: set = set()
    floor = min(grid["runs"], MIN_RUNS_BEFORE_TRUNCATE)
    for r in range(grid["runs"]):
        if len(recs) >= floor and clock() - t0 > budget:
            break
        rec, w = _simulate(plan, r, seed=grid["seed"], steps=grid["steps"], step_days=grid["step_days"], sim_id=sim, point=point, cp_steps=grid["cp_steps"])
        recs.append(rec)
        warned |= w
    degraded, degraded_note = _time_precision(history)
    stats = plan.param_stats()
    unsampled = [f"{plan.label.get(a['element'], a['element'])} / 假设「{a['statement']}」" for a in plan.assumptions if a["p_true"] is None]
    result: Dict[str, Any] = {
        "ok": True,
        "meta": {
            "mode": "point" if point else "mc", "runs_requested": grid["runs"], "runs_done": len(recs), "truncated": len(recs) < grid["runs"],
            "elapsed_sec": round(clock() - t0, 3), "seed": grid["seed"], "horizon_days": grid["horizon_days"], "horizon_auto": grid["horizon_auto"],
            "steps": grid["steps"], "step_days": round(grid["step_days"], 3), "time_resolution_days": round(grid["step_days"], 3),
            "time_precision_degraded": degraded, "time_precision_note": degraded_note,
            "elements": list(plan.engine_ids), "params": {k: v for k, v in stats.items() if k != "point_list"},
            "point_estimates": stats["point_list"], "assumptions_unsampled": unsampled,
            "events": {"sampled": len(plan.priors), "with_effects": sum(1 for p in plan.priors if p.get("effects"))},
            "warnings": plan.warnings + sorted(warned), "honesty": HONEST_NOTE,
            "origin_clock_day": round(max(plan.clock0.values() or [0.0]), 1),
        },
    }
    result.update(_aggregate(plan, recs, grid["step_days"], grid["cp_steps"]))
    return result


# ═════════════════════════════════════════════════════════════════════
# 敏感性（一次一个，公共随机数）
# ═════════════════════════════════════════════════════════════════════


def _scenario_edits(plan: _Plan) -> List[Dict[str, Any]]:
    """从方案里列出所有\"一次改一个\"的情景。每项 `{label, kind, element, ref, low: edits, high: edits, flip?}`。"""
    out: List[Dict[str, Any]] = []
    for sp in plan.params:
        if not sp["ranged"] or sp["high"] <= sp["low"]:
            continue
        mk = lambda v, sp=sp: [{"kind": "param", "element": sp["element"], "holder": f"{sp['kind']}:{sp['hid']}", "name": sp["name"], "field": "value", "value": v}]
        out.append({"label": sp["label"], "kind": "param", "element": sp["element"], "ref": f"{sp['kind']}:{sp['hid']}.{sp['name']}",
                    "low_value": sp["low"], "high_value": sp["high"], "low": mk(sp["low"]), "high": mk(sp["high"])})
    for p in plan.paths:
        lo, hi = p["duration"].get("low"), p["duration"].get("high")
        if lo is None or hi is None or hi <= lo:
            continue
        mk = lambda v, p=p: [{"kind": "duration", "element": p["element"], "bottleneck": p["bottleneck"], "path": p["path"], "field": "all", "value": v}]
        out.append({"label": p["label"] + " 耗时", "kind": "duration", "element": p["element"], "ref": f"bottleneck:{p['bottleneck']}/{p['path']}",
                    "low_value": lo, "high_value": hi, "low": mk(lo), "high": mk(hi)})
    for a in plan.assumptions:
        if a["p_true"] is None:
            continue
        base_true = a["p_true"] >= 0.5
        flipped = [{"kind": "assumption", "element": a["element"], "id": a["id"], "p_true": 0.0 if base_true else 1.0}]
        out.append({"label": f"{plan.label.get(a['element'], a['element'])} / 假设「{a['statement']}」", "kind": "assumption", "element": a["element"],
                    "ref": f"assumption:{a['id']}", "low_value": "不成立", "high_value": "成立",
                    "low": flipped if base_true else [], "high": [] if base_true else flipped})
    for pr in plan.priors:
        rr = pr.get("rate_range")
        if not rr or rr["high"] <= rr["low"]:
            continue
        mk = lambda v, pr=pr: [{"kind": "event_rate", "id": pr["id"], "rate": v}]
        out.append({"label": f"事件「{pr['id']}」频率", "kind": "event_rate", "element": "", "ref": f"event:{pr['id']}",
                    "low_value": rr["low"], "high_value": rr["high"], "low": mk(rr["low"]), "high": mk(rr["high"])})
    return out


def _run_many(plan: _Plan, settings: Settings, grid: Dict[str, Any], sim: str, n: int, clock: Callable[[], float], deadline: float) -> Optional[List[_Run]]:
    """点估计情景跑 `n` 次；超过 `deadline`（墙钟）返回 `None`（这个情景作废，不给半截数据）。"""
    recs: List[_Run] = []
    for r in range(n):
        if clock() > deadline:
            return None
        rec, _w = _simulate(plan, r, seed=grid["seed"], steps=grid["steps"], step_days=grid["step_days"], sim_id=sim, point=True, cp_steps=[])
        recs.append(rec)
    return recs


def sensitivity(
    settings: Optional[Settings],
    history: Any = None,
    *,
    element: Any = None,
    target: Any = None,
    runs: Any = None,
    seed: Any = None,
    horizon_days: Any = None,
    steps: Any = None,
    time_budget_sec: Any = None,
    sim_id: str = "",
    _clock: Optional[Callable[[], float]] = None,
) -> Dict[str, Any]:
    """一次改一个的敏感性分析（龙卷风图数据）。

    基线 = 点估计情景（`run_forecast(point=True)` 的口径）；对每个带 `low/high` 的趋势参数、每条路径耗时、每个带 `prior_p_true` 的假设、
    每个带 `rate_range` 的事件，分别把它推到低端/高端（假设：翻到另一侧），**用同一批随机流**（公共随机数）各跑 `runs` 次
    （默认 `mc_runs // 10`，夹在 50–200），比较目标里程碑的 P50 与视野内达成概率。
    `element` 限定只模拟该元素的依赖闭包（强烈建议：敏感性的情景数 × 运行数很大）；`target` 写 `元素#里程碑id` 或唯一的里程碑 id。
    墙钟预算用尽时，没跑完的情景列入 `skipped`（`meta.truncated = True`），不给半截数据。
    """
    scope = element
    if scope is None and target:
        te, _k, _rid = an.parse_ref(target)
        scope = te
    plan, why = _prepare(settings, scope)
    if plan is None:
        return {"ok": False, "reason": why, "honesty": HONEST_NOTE}
    assert settings is not None
    clock = _clock or time.monotonic
    params = an.get_params(settings)
    grid = _grid(plan, params, runs, seed, horizon_days, steps)
    k = _num(runs)
    n = int(min(MAX_RUNS, max(1, k))) if k is not None else int(min(SENS_MAX_RUNS, max(SENS_MIN_RUNS, params["mc_runs"] // 10)))
    sim = str(sim_id or "forecast")
    t_start = clock()
    deadline = t_start + _budget(time_budget_sec)
    pending = [m for m in plan.milestones if not m["reached0"] and m["has_criteria"]]
    if target:
        te, _k2, tid = an.parse_ref(target)
        hit = [m for m in pending if m["id"] == tid and (not te or er.resolve(settings, te) and _lid(er.resolve(settings, te)) == m["element"])]
        pending = hit if len(hit) == 1 else []
        if not pending:
            return {"ok": False, "reason": f"找不到唯一的未达成里程碑 {target!r}", "honesty": HONEST_NOTE}
    pending = pending[:MAX_SENS_TARGETS]
    base_recs = _run_many(plan, settings, grid, sim, n, clock, deadline)
    if base_recs is None:
        return {"ok": False, "reason": "时间预算连基线情景都没跑完，请调高预算或减少运行次数", "honesty": HONEST_NOTE}
    scen = _scenario_edits(plan)
    results: List[Tuple[Dict[str, Any], Optional[List[_Run]], Optional[List[_Run]]]] = []
    skipped: List[str] = []
    for sc in scen:
        sides: List[Optional[List[_Run]]] = []
        for side in ("low", "high"):
            edits = sc[side]
            if not edits:
                sides.append(base_recs)  # 假设的\"基线侧\"：直接用基线结果
                continue
            edited, applied, _errs = apply_edits(settings, edits)
            if not applied:
                sides.append(None)
                continue
            sub = _Plan(edited, plan.engine_ids)
            sides.append(_run_many(sub, edited, grid, sim, n, clock, deadline))
        if sides[0] is None or sides[1] is None:
            skipped.append(sc["label"])
            continue
        results.append((sc, sides[0], sides[1]))
    horizon = grid["horizon_days"]
    targets = []
    for m in pending:
        key = (m["element"], m["id"])
        bv = [r.ms[key] for r in base_recs]
        rows = []
        for sc, lo_recs, hi_recs in results:
            lv, hv = [r.ms[key] for r in lo_recs or []], [r.ms[key] for r in hi_recs or []]
            _a, l50, _b = _qs(lv)
            _c, h50, _d = _qs(hv)
            pl, ph = _reached_frac(lv), _reached_frac(hv)
            swing_days = None if l50 is None or h50 is None else abs(h50 - l50)
            swing_p = abs(ph - pl)
            rows.append({
                "label": sc["label"], "kind": sc["kind"], "element": sc["element"], "ref": sc["ref"],
                "low": {"value": sc["low_value"], "p_reached": round(pl, 4), "p50": _round(l50, 1)},
                "high": {"value": sc["high_value"], "p_reached": round(ph, 4), "p50": _round(h50, 1)},
                "swing_days": _round(swing_days, 1), "swing_p": round(swing_p, 4),
                "score": round(max((swing_days or 0.0) / horizon, swing_p), 4),
            })
        rows.sort(key=lambda x: (-x["score"], x["label"]))
        _a2, b50, _b2 = _qs(bv)
        targets.append({
            "element": m["element"], "element_label": plan.label.get(m["element"], m["element"]), "id": m["id"], "name": m["name"],
            "baseline": {"p_reached": round(_reached_frac(bv), 4), "p50": _round(b50, 1)}, "rows": rows[:30],
        })
    stats = plan.param_stats()
    return {
        "ok": True,
        "meta": {
            "runs_per_scenario": n, "scenarios_total": len(scen), "scenarios_done": len(results), "truncated": bool(skipped), "skipped": skipped,
            "seed": grid["seed"], "horizon_days": horizon, "steps": grid["steps"], "step_days": round(grid["step_days"], 3),
            "elements": list(plan.engine_ids), "elapsed_sec": round(clock() - t_start, 3),
            "point_estimates": stats["point_list"], "params": {k2: v for k2, v in stats.items() if k2 != "point_list"},
            "baseline": "点估计情景：参数取点值，假设取更可能的一侧（prior_p_true ≥ 0.5 视为成立），路径成败与耗时仍随机",
            "honesty": HONEST_NOTE,
        },
        "targets": targets,
    }


# ═════════════════════════════════════════════════════════════════════
# what-if
# ═════════════════════════════════════════════════════════════════════


def _delta(a: Optional[float], b: Optional[float], nd: int = 1) -> Optional[float]:
    return None if a is None or b is None else round(b - a, nd)


def what_if(
    settings: Optional[Settings],
    history: Any = None,
    edits: Any = None,
    *,
    base: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """改几个参数/假设/路径/事件频率，用**同一种子、同一视野与步数**重跑，和基线逐项比较。

    `edits` 的写法见 `apply_edits`。`base` 是之前跑过的 `run_forecast` 结果（省一次基线重算；其 `meta` 里的种子/视野/步数会被沿用，
    保证两次可比）；不给就现算。编辑全部无效时不重跑，直接返回错误。返回
    `{ok, applied, errors, result, diff}`；`diff` 逐项给出里程碑/瓶颈/门槛的 P50 与概率变化。
    """
    if not settings:
        return {"ok": False, "reason": "没有 settings", "honesty": HONEST_NOTE}
    edited, applied, errors = apply_edits(settings, edits)
    if not applied:
        return {"ok": False, "reason": "没有任何有效的编辑", "errors": errors, "applied": [], "honesty": HONEST_NOTE}
    opts = dict(kwargs)
    if base is not None and base.get("ok"):
        bm = base["meta"]
        opts.setdefault("seed", bm["seed"])
        opts.setdefault("horizon_days", bm["horizon_days"])
        opts.setdefault("steps", bm["steps"])
        opts.setdefault("runs", bm["runs_requested"])
    if base is None or not base.get("ok"):
        base = run_forecast(settings, history, **opts)
        if not base.get("ok"):
            return base
        opts.update(seed=base["meta"]["seed"], horizon_days=base["meta"]["horizon_days"], steps=base["meta"]["steps"])
    res = run_forecast(edited, history, **opts)
    if not res.get("ok"):
        return {**res, "errors": errors, "applied": applied}
    diff: Dict[str, Any] = {"milestones": [], "bottlenecks": [], "gates": []}
    for part, key, pkey in (("milestones", "id", "p_reached"), ("bottlenecks", "id", "p_resolved"), ("gates", "id", "p_ever_open")):
        bidx = {(r["element"], r[key]): r for r in base.get(part) or []}
        for row in res.get(part) or []:
            b = bidx.get((row["element"], row[key]))
            if not b or pkey not in b or pkey not in row:
                continue
            d_p = round(row[pkey] - b[pkey], 4)
            d50 = _delta(b.get("p50"), row.get("p50"))
            if d_p != 0 or (d50 or 0) != 0:
                diff[part].append({"element": row["element"], "element_label": row["element_label"], "id": row[key], "name": row["name"],
                                   "p_before": b[pkey], "p_after": row[pkey], "delta_p": d_p,
                                   "p50_before": b.get("p50"), "p50_after": row.get("p50"), "delta_p50": d50})
    for lst in diff.values():
        lst.sort(key=lambda x: -(abs(x["delta_p"]) + abs(x["delta_p50"] or 0.0) / max(1.0, base["meta"]["horizon_days"])))
    comparable = base["meta"]["runs_done"] == res["meta"]["runs_done"]
    return {"ok": True, "applied": applied, "errors": errors, "base_meta": base["meta"], "result": res, "diff": diff,
            "comparable": comparable,
            "note": "" if comparable else "两次实际运行次数不同（时间预算截断），差异里含抽样噪声",
            "honesty": HONEST_NOTE}


# ═════════════════════════════════════════════════════════════════════
# 先行信号监测清单 / 现实回填
# ═════════════════════════════════════════════════════════════════════


def _fmt_day(d: Optional[float]) -> str:
    return "视野内未达成" if d is None else f"约 {d:.0f} 天后"


def build_watchlist(
    settings: Optional[Settings], forecast: Optional[Dict[str, Any]] = None, sens: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """先行信号监测清单（计划 §5.6）：\"现实里该盯什么、先发生什么意味着走向哪条分支\"。**规则生成，不调 LLM。**

    两个来源：① 元素剖面里已有的 `signals`（`source: "anatomy"`，A5/用户写的，原样带出）；
    ② 由预测结果生成（`source: "forecast"`，最多 `MAX_WATCH_FORECAST` 条，按\"不确定性\"排序）：
    多路径瓶颈（哪条路径先走通）、假设（成立/不成立对里程碑的影响）、临界里程碑、敏感性最大的参数。
    每项 `{key, element, element_label, watch, means, refs, source, basis, score}`；`key` 稳定，供 `reality_check` 回填勾选。
    """
    items: List[Dict[str, Any]] = []
    for line in an.lines_with_anatomy(settings):
        el = _lid(line)
        for sig in (an.get_anatomy(line) or {}).get("signals") or []:
            items.append({
                "key": f"{el}#{sig['id']}", "element": el, "element_label": _label(line), "watch": sig.get("watch", ""),
                "means": sig.get("means", ""), "refs": list(sig.get("refs") or []), "source": "anatomy",
                "basis": an.basis_of(sig)["state"], "score": None,
            })
    derived: List[Dict[str, Any]] = []
    if forecast and forecast.get("ok"):
        for b in forecast.get("bottlenecks") or []:
            share = b.get("by_path_share") or {}
            if b.get("status") != "open" or len(share) < 2:
                continue
            p = b.get("p_resolved") or 0.0
            derived.append({
                "key": f"fc:bottleneck:{b['element']}#{b['id']}", "element": b["element"], "element_label": b["element_label"],
                "watch": f"瓶颈「{b['name']}」：哪条解决路径先出现实质进展",
                "means": "；".join(f"路径 {pid} 走通的运行占 {s:.0%}" for pid, s in share.items()) + f"；整体 {_fmt_day(b.get('p50'))}解决（视野内解决概率 {p:.0%}）",
                "refs": [f"bottleneck:{b['id']}"], "score": min(p, 1 - p) + 0.1,
            })
        for a in forecast.get("conditional") or []:
            if not a.get("sufficient") or not a.get("effects"):
                continue
            e = a["effects"][0]
            derived.append({
                "key": f"fc:assumption:{a['element']}#{a['id']}", "element": a["element"], "element_label": a["element_label"],
                "watch": f"假设「{a['statement']}」是否成立",
                "means": f"成立 → 里程碑「{e['name']}」{_fmt_day(e['p50_true'])}（视野内 {e['p_reached_true']:.0%}）；"
                         f"不成立 → {_fmt_day(e['p50_false'])}（视野内 {e['p_reached_false']:.0%}）",
                "refs": [f"assumption:{a['id']}"], "score": abs(e["delta_p"]) + abs(e["delta_p50"] or 0.0) / max(1.0, forecast["meta"]["horizon_days"]),
            })
        for m in forecast.get("milestones") or []:
            if m.get("status") != "pending" or not (0.0 < (m.get("p_reached") or 0.0) < 1.0):
                continue
            p = m["p_reached"]
            derived.append({
                "key": f"fc:milestone:{m['element']}#{m['id']}", "element": m["element"], "element_label": m["element_label"],
                "watch": f"里程碑「{m['name']}」的判据进展（判据里的指标 / 瓶颈 / 组件有没有朝达成方向走）",
                "means": f"当前预测 {_fmt_day(m.get('p50'))}（P10 {_fmt_day(m.get('p10'))}，P90 {_fmt_day(m.get('p90'))}），视野内达成概率 {p:.0%}",
                "refs": [f"milestone:{m['id']}"], "score": min(p, 1 - p),
            })
    if sens and sens.get("ok"):
        for t in sens.get("targets") or []:
            for row in t.get("rows", [])[:2]:
                if not row["score"]:
                    continue
                derived.append({
                    "key": f"fc:sens:{t['element']}#{t['id']}:{row['ref']}", "element": row.get("element") or t["element"],
                    "element_label": t["element_label"], "watch": f"{row['label']} 的真实取值（它是「{t['name']}」时间的主要敏感项）",
                    "means": f"在 [{row['low']['value']}, {row['high']['value']}] 之间摆动，会让该里程碑的 P50 从 {_fmt_day(row['low']['p50'])} 变到 {_fmt_day(row['high']['p50'])}",
                    "refs": [row["ref"]], "score": row["score"],
                })
    seen: set = set()
    for d in sorted(derived, key=lambda x: (-(x["score"] or 0.0), x["key"])):
        if d["key"] in seen or len([i for i in items if i["source"] == "forecast"]) >= MAX_WATCH_FORECAST:
            continue
        seen.add(d["key"])
        items.append({**d, "source": "forecast", "basis": "derived", "score": round(d["score"], 4)})
    return items


def signal_observations(checks: Iterable[Any], keys: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, Any]]:
    """统计现实回填里被勾选的先行信号：`{key: {count, last_step, verdicts: {matched/partially_matched/diverged: 次数}}}`。
    `keys` 给出时只统计这些 key；`checks` 是 `reality_check.RealityCheck` 列表（读 `signals_observed`/`step`/`verdict`）。"""
    wanted = None if keys is None else set(keys)
    out: Dict[str, Dict[str, Any]] = {}
    for c in checks or []:
        for k in getattr(c, "signals_observed", None) or []:
            if wanted is not None and k not in wanted:
                continue
            d = out.setdefault(k, {"count": 0, "last_step": None, "verdicts": {}})
            d["count"] += 1
            step = int(getattr(c, "step", 0) or 0)
            d["last_step"] = step if d["last_step"] is None else max(d["last_step"], step)
            v = str(getattr(c, "verdict", "") or "")
            if v:
                d["verdicts"][v] = d["verdicts"].get(v, 0) + 1
    return out
