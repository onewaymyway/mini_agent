"""world_simulator/forecast_brief.py — 预测简报与置信等级（第二十四轮 A7）。

设计依据：`next_doc/world_simulator_element_anatomy_evidence_and_forecast_plan.md` §5.7（置信等级由引擎按规则计算）、
§5.8（预测简报）、§8 A7。

## 这个模块做什么

把 A6 的蒙特卡洛 / 敏感性 / 监测清单**按用户的问题重新组织**成一页简报，回答五件事：
**能不能成 / 大概什么时候 / 卡在哪 / 靠什么假设 / 该盯什么信号**，并给出一个**由引擎按规则算出**的置信等级
（高/中/低 + 每条扣分原因）。

## 铁律

1. **置信等级不由 LLM 评判**：规则、扣分、封顶都写在 `confidence()` 里并随结果一起返回，界面/导出逐条展示；
   阈值放在 `settings.anatomy_params`（`conf_*`），可配置。
2. **不重算业务**：预测数字全部来自 `forecast.run_forecast / sensitivity / build_watchlist`，本模块只做**切片、排序、措辞**。
   `build_brief` 是纯函数（输入预测结果，输出简报），`run_brief` 才会调用预测（仍然不调 LLM、不读写磁盘）。
3. **措辞诚实**：区间不是校准概率（`forecast.HONEST_NOTE` 原样带出）；\"达成概率\"一律说成\"多少比例的推演运行里达成\"；
   没有回测支撑时明说（A8 之前恒为此项）。
4. **不静默**：元素预测失败 / 没有里程碑 / 判据缺失，都在对应段落里写明原因，不假装有结论。
5. 简报**不落盘**（同 A6）：每次生成现算；固定种子 + 未被时间预算截断时结果逐位可复现。

## 刻意不做

- 不对\"高\"做校准承诺：等级是**可信度的规则代理**，不是概率；
- 不替用户选\"目标里程碑\"：默认取元素里**最后一个有判据且未达成的里程碑**（列表顺序即声明顺序），界面同时列出全部里程碑。
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from world_simulator import anatomy as an
from world_simulator import anatomy_engine as ae
from world_simulator import element_registry as er
from world_simulator import element_view as ev
from world_simulator import forecast as fc

LEVELS = ("high", "medium", "low")
LEVEL_LABELS = {"high": "高", "medium": "中", "low": "低"}
_LEVEL_RANK = {"low": 0, "medium": 1, "high": 2}

RULE_LABELS = {
    "grounded_share": "关键条目的出处覆盖",
    "unreviewed": "剖面尚未经你审阅",
    "stale_evidence": "支撑关键字段的证据已过期",
    "wide_intervals": "参数区间很宽",
    "point_estimates": "多数参数只有点估计",
    "no_backtest": "没有同类回测案例",
    "time_precision": "时间精度降级",
    "truncated": "预测被时间预算截断",
}
# 扣分点数（规则固定；阈值在 settings.anatomy_params.conf_*）
POINTS = {
    "grounded_ok_miss": 20, "grounded_low": 35, "unreviewed": 10, "stale_evidence": 10,
    "wide_intervals": 10, "point_estimates": 10, "no_backtest": 10, "time_precision": 10, "truncated": 5,
}
POINT_SHARE_LIMIT = 0.5          # 趋势参数 + 路径耗时里\"只有点估计\"的占比超过它 → 扣分
MAX_BLOCKERS = 5
MAX_SIGNALS = 6
MAX_UNCERTAINTIES = 3
MAX_ASSUMPTIONS = 5
DEFAULT_BRIEF_BUDGET_SEC = 60.0
DEFAULT_SENS_BUDGET_SEC = 30.0

NO_BACKTEST_NOTE = "A8（指标级回测）尚未提供数据，骨架在同类案例上的区间覆盖率未知。"
HONEST_NOTE = fc.HONEST_NOTE
CONFIDENCE_NOTE = "置信等级由引擎按固定规则计算（见下方扣分），不是 AI 的主观评价，也不是概率；它衡量的是『这份剖面有多少依据』。"


# ═════════════════════════════════════════════════════════════════════
# 小工具
# ═════════════════════════════════════════════════════════════════════


def fmt_days(days: Any) -> str:
    """\"从现在起\"的天数 → 人读：`None` = 视野内未达成；< 60 天按天，< 2 年按月，其余按年。"""
    if days is None:
        return "视野内未达成"
    try:
        d = float(days)
    except (TypeError, ValueError):
        return "—"
    if d != d:
        return "—"
    if d < 60:
        return f"{max(0, round(d))} 天"
    if d < 730:
        return f"约 {d / 30.4:.0f} 个月"
    return f"约 {d / 365.25:.1f} 年"


def _fmt_horizon(days: Any) -> str:
    try:
        d = float(days)
    except (TypeError, ValueError):
        return "预测视野"
    return f"{d / 365.25:.1f} 年视野" if d >= 730 else f"{d:.0f} 天视野"


def _bucket(p: Optional[float]) -> str:
    """把\"达成比例\"说成人话。注意措辞是**推演运行**的比例，不是现实概率。"""
    if p is None:
        return "无法判定"
    if p >= 0.8:
        return "多数推演运行里达成"
    if p >= 0.5:
        return "过半推演运行里达成"
    if p >= 0.2:
        return "少数推演运行里达成"
    if p > 0:
        return "极少推演运行里达成"
    return "没有任何推演运行达成"


def _line_label(line: Dict[str, Any]) -> str:
    return str(line.get("label") or line.get("id") or "").strip()


def _rank_level(level: str) -> int:
    return _LEVEL_RANK.get(level, 0)


# ═════════════════════════════════════════════════════════════════════
# 置信等级
# ═════════════════════════════════════════════════════════════════════


def _thresholds(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    p = an.get_params(settings)
    high, medium = int(p["conf_high_min"]), int(p["conf_medium_min"])
    if medium > high:  # 配反了：把\"中\"的门槛压到不超过\"高\"，避免出现永远到不了的档位
        medium = high
    ok, low = float(p["conf_grounded_ok"]), float(p["conf_grounded_low"])
    if low > ok:
        low = ok
    return {"high_min": high, "medium_min": medium, "grounded_ok": ok, "grounded_low": low, "wide_ratio": float(p["conf_wide_ratio"])}


GROUNDED_STATES = ("sourced", "user_confirmed", "user_edited")


def _grounded(anatomy: Dict[str, Any]) -> Dict[str, Any]:
    """关键条目 = 指标（条目本身 + 现值各算一个）、瓶颈、假设。`有依据` = 有出处 / 已确认 / 已编辑。"""
    total = grounded = 0
    for m in anatomy.get("metrics") or []:
        carriers = [m] + ([m["current"]] if isinstance(m.get("current"), dict) else [])
        for c in carriers:
            total += 1
            grounded += an.basis_of(c)["state"] in GROUNDED_STATES
    for part in ("bottlenecks", "assumptions"):
        for it in anatomy.get(part) or []:
            total += 1
            grounded += an.basis_of(it)["state"] in GROUNDED_STATES
    return {"total": total, "grounded": grounded, "share": (grounded / total) if total else 0.0}


def _widths(anatomy: Dict[str, Any]) -> Dict[str, Any]:
    """趋势参数 / 就绪度趋势参数 / 路径耗时的区间情况。`ratios` 是带区间且 low>0 的 high/low。"""
    total = ranged = 0
    ratios: List[float] = []

    def take(low: Any, high: Any) -> None:
        nonlocal total, ranged
        total += 1
        lo, hi = fc._num(low), fc._num(high)
        if lo is not None and hi is not None:
            ranged += 1
            if lo > 0:
                ratios.append(hi / lo)

    for part, key in (("metrics", "trend"), ("components", "readiness_trend")):
        for h in anatomy.get(part) or []:
            for p in ((h.get(key) or {}).get("params") or {}).values():
                if isinstance(p, dict) and fc._num(p.get("value")) is not None:
                    take(p.get("low"), p.get("high"))
    for bn in anatomy.get("bottlenecks") or []:
        for path in bn.get("resolution_paths") or []:
            dur = path.get("duration_days") or {}
            take(dur.get("low"), dur.get("high"))
    return {
        "total": total, "ranged": ranged, "point_share": ((total - ranged) / total) if total else None,
        "median_ratio": statistics.median(ratios) if ratios else None,
    }


def confidence(
    settings: Optional[Dict[str, Any]], element: Any, *, forecast_meta: Optional[Dict[str, Any]] = None,
    evidence: Optional[Sequence[Dict[str, Any]]] = None, now: Any = None, backtest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """一个元素的置信等级（纯函数）。

    起点 100 分，按下列规则扣分，分数 ≥ `conf_high_min` → 高，≥ `conf_medium_min` → 中，否则低；另有两条**封顶**规则：
    关键条目有依据的比例 < `conf_grounded_low` → 最高「低」；剖面未经审阅（`draft`/`none`）→ 最高「中」。

    | 规则 | 条件 | 扣分 |
    |---|---|---|
    | grounded_share | 有依据比例 < `conf_grounded_ok`（< `conf_grounded_low` 扣更多并封顶「低」） | 20 / 35 |
    | unreviewed | `anatomy_status` 是 `draft`/`none`（封顶「中」） | 10 |
    | stale_evidence | 被字段引用的证据已过期（只有传了 `evidence` 才检查） | 10 |
    | wide_intervals | 带区间参数的 high/low 中位数 ≥ `conf_wide_ratio` | 10 |
    | point_estimates | 趋势参数 + 路径耗时里只有点估计的占比 > 50% | 10 |
    | no_backtest | `backtest` 为空（A8 之前恒为此项） | 10 |
    | time_precision | 预测标了\"时间精度降级\" | 10 |
    | truncated | 预测被时间预算截断 | 5 |

    返回 `{level, level_label, score, deductions[], caps[], inputs, thresholds, note}`；每条扣分带 `rule/label/points/detail`。
    """
    line = er.resolve(settings or {}, element) if not isinstance(element, dict) else element
    anatomy = an.get_anatomy(line) or {}
    th = _thresholds(settings)
    deductions: List[Dict[str, Any]] = []
    caps: List[Dict[str, str]] = []

    def deduct(rule: str, points: int, detail: str) -> None:
        deductions.append({"rule": rule, "label": RULE_LABELS[rule], "points": points, "detail": detail})

    g = _grounded(anatomy)
    if g["total"] == 0:
        deduct("grounded_share", POINTS["grounded_low"], "没有指标/瓶颈/假设条目可核对出处")
        caps.append({"rule": "grounded_share", "level": "low", "detail": "没有任何可核对出处的关键条目"})
    elif g["share"] < th["grounded_low"]:
        deduct("grounded_share", POINTS["grounded_low"], f"{g['grounded']}/{g['total']} 个关键条目有依据（{g['share']:.0%} < {th['grounded_low']:.0%}），其余仍是 LLM 先验")
        caps.append({"rule": "grounded_share", "level": "low", "detail": f"有依据的关键条目只占 {g['share']:.0%}"})
    elif g["share"] < th["grounded_ok"]:
        deduct("grounded_share", POINTS["grounded_ok_miss"], f"{g['grounded']}/{g['total']} 个关键条目有依据（{g['share']:.0%} < {th['grounded_ok']:.0%}）")

    meta = anatomy.get("meta") or {}
    status = meta.get("anatomy_status", "draft")
    if status in ("none", "draft"):
        deduct("unreviewed", POINTS["unreviewed"], "剖面状态为「未审阅」：拆解/研究草稿默认就驱动引擎，但没有人确认过")
        caps.append({"rule": "unreviewed", "level": "medium", "detail": "未经审阅的剖面最高只给「中」"})

    stale = 0
    if evidence is not None and isinstance(line, dict):
        view = ev.build_evidence_view(settings, line, evidence, now=now)
        stale = sum(1 for i in view["items"] if i["stale"] and i["used_by"])
        if stale:
            deduct("stale_evidence", POINTS["stale_evidence"], f"{stale} 条被字段引用的证据已超过 {view['ttl_days']} 天（现实时间）")

    w = _widths(anatomy)
    if w["median_ratio"] is not None and w["median_ratio"] >= th["wide_ratio"]:
        deduct("wide_intervals", POINTS["wide_intervals"], f"带区间参数的 high/low 中位数 {w['median_ratio']:.1f} ≥ {th['wide_ratio']:g}：区间很宽，结论对参数取值很敏感")
    if w["point_share"] is not None and w["point_share"] > POINT_SHARE_LIMIT:
        deduct("point_estimates", POINTS["point_estimates"], f"{w['total'] - w['ranged']}/{w['total']} 个趋势参数/路径耗时只有点估计，区间会低估不确定性")

    has_backtest = isinstance(backtest, dict) and int(backtest.get("cases") or 0) >= 1
    if not has_backtest:
        deduct("no_backtest", POINTS["no_backtest"], NO_BACKTEST_NOTE)

    fm = forecast_meta or {}
    if fm.get("time_precision_degraded"):
        deduct("time_precision", POINTS["time_precision"], str(fm.get("time_precision_note") or "最近几步 LLM 没有给 elapsed_days，按兜底天数推算"))
    if fm.get("truncated"):
        deduct("truncated", POINTS["truncated"], f"只跑了 {fm.get('runs_done')}/{fm.get('runs_requested')} 次，区间噪声更大")

    score = max(0, 100 - sum(d["points"] for d in deductions))
    level = "high" if score >= th["high_min"] else "medium" if score >= th["medium_min"] else "low"
    for cap in caps:
        if _rank_level(level) > _rank_level(cap["level"]):
            level = cap["level"]
    return {
        "level": level, "level_label": LEVEL_LABELS[level], "score": score, "deductions": deductions, "caps": caps,
        "inputs": {
            "grounded": g, "anatomy_status": status, "stale_evidence": stale, "evidence_checked": evidence is not None,
            "widths": w, "has_backtest": has_backtest,
        },
        "thresholds": th, "note": CONFIDENCE_NOTE,
    }


def overall_confidence(per_element: Sequence[Tuple[str, str, Dict[str, Any]]]) -> Dict[str, Any]:
    """整个模拟的等级 = 各重点元素中**最低**的一档（保守）；扣分原因取最低的那个元素的。
    `per_element` 是 `[(元素 id, 展示名, confidence 结果)]`。没有元素 → 低，并说明原因。"""
    if not per_element:
        return {"level": "low", "level_label": "低", "score": 0, "deductions": [], "caps": [], "worst": None, "per_element": [],
                "rule": "没有可预测的重点元素", "note": CONFIDENCE_NOTE}
    worst = min(per_element, key=lambda t: (_rank_level(t[2]["level"]), t[2]["score"]))
    c = worst[2]
    return {
        "level": c["level"], "level_label": c["level_label"], "score": c["score"], "deductions": c["deductions"], "caps": c["caps"],
        "worst": {"id": worst[0], "label": worst[1]},
        "per_element": [{"id": i, "label": lab, "level": cc["level"], "level_label": cc["level_label"], "score": cc["score"]} for i, lab, cc in per_element],
        "rule": "取各重点元素中最低的一档（保守）", "note": CONFIDENCE_NOTE,
    }


# ═════════════════════════════════════════════════════════════════════
# 简报（纯函数：输入预测结果，输出简报）
# ═════════════════════════════════════════════════════════════════════


def select_elements(settings: Optional[Dict[str, Any]], elements: Optional[Sequence[Any]] = None) -> Tuple[List[str], bool]:
    """简报覆盖哪些元素：显式传入的优先；否则取**重点元素里引擎能结算的**；一个都没有就退回全部可结算元素。
    返回 `(元素 id 列表, 是否退回了全部)`。"""
    engine = [str(x["id"]).strip() for x in ae._engine_lines(settings)]
    if elements:
        out: List[str] = []
        for ref in elements:
            line = er.resolve(settings or {}, ref)
            lid = str(line["id"]).strip() if isinstance(line, dict) and line.get("id") else ""
            if lid in engine and lid not in out:
                out.append(lid)
        return out, False
    keys = [e for e in an.key_elements(settings) if e in engine]
    if keys:
        return keys, False
    return engine, bool(engine)


def _target_milestone(rows: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str]:
    """`(目标里程碑行, 状态)`。状态：`pending`（有目标）/ `all_reached` / `no_criteria` / `none`。"""
    pending = [r for r in rows if r.get("status") == "pending"]
    if pending:
        return pending[-1], "pending"
    if any(r.get("status") == "reached" for r in rows):
        return None, "all_reached"
    if any(r.get("status") == "no_criteria" for r in rows):
        return None, "no_criteria"
    return None, "none"


def _answer_success(target: Optional[Dict[str, Any]], state: str, horizon: Any) -> Dict[str, Any]:
    if state == "pending" and target is not None:
        p = target.get("p_reached")
        text = f"在已声明的参数下，「{target['name']}」在 {_fmt_horizon(horizon)}内：{_bucket(p)}（约 {p:.0%} 的运行）。"
        return {"state": state, "text": text, "milestone": {"id": target["id"], "name": target["name"]}, "p_reached": p}
    texts = {
        "all_reached": "这个元素声明的里程碑都已经达成。",
        "no_criteria": "这个元素的里程碑没有可判定的判据，引擎无法预测能否达成（只能靠 LLM 提议）。",
        "none": "这个元素没有声明里程碑，无法回答「能不能成」；可看下面的指标区间与瓶颈。",
    }
    return {"state": state, "text": texts.get(state, ""), "milestone": None, "p_reached": None}


def _answer_when(target: Optional[Dict[str, Any]], state: str) -> Dict[str, Any]:
    if state != "pending" or target is None:
        return {"text": "", "p10": None, "p50": None, "p90": None}
    p10, p50, p90 = target.get("p10"), target.get("p50"), target.get("p90")
    if p50 is not None:
        hi = f"{fmt_days(p90)}后" if p90 is not None else "超出预测视野"
        text = f"中位数（P50）{fmt_days(p50)}后；P10 {fmt_days(p10)}后，P90 {hi}（均从现在起）。"
    else:
        text = f"视野内达成的运行不足一半，P50 没有出现" + (f"；最乐观的 10% 运行里约 {fmt_days(p10)}后达成。" if p10 is not None else "。")
    return {"text": text, "p10": p10, "p50": p50, "p90": p90}


def _blockers(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for b in rows:
        if b.get("status") != "open":
            continue
        share = b.get("by_path_share") or {}
        top = max(share.items(), key=lambda kv: kv[1]) if share else None
        out.append({
            "id": b["id"], "name": b["name"], "p_resolved": b.get("p_resolved"), "p10": b.get("p10"), "p50": b.get("p50"), "p90": b.get("p90"),
            "exhausted": b.get("exhausted"), "by_path_share": share, "top_path": top[0] if top else "",
            "text": (
                f"视野内解决的运行占 {b.get('p_resolved', 0):.0%}；P50 {fmt_days(b.get('p50'))}"
                + (f"；解决时最常走的路径「{top[0]}」（{top[1]:.0%}）" if top else "")
                + (f"；全部路径都失败 {b['exhausted']:.0%}" if b.get("exhausted") else "")
            ),
        })
    out.sort(key=lambda x: ((x["p_resolved"] if x["p_resolved"] is not None else 0.0), x["name"]))
    return out[:MAX_BLOCKERS]


def _assumptions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for a in rows:
        item = {
            "id": a["id"], "statement": a["statement"], "prior_p_true": a["prior_p_true"], "sufficient": a["sufficient"],
            "has_overrides": a["has_overrides"], "effect": None, "text": "",
        }
        if not a["sufficient"]:
            item["text"] = f"样本不足（成立 {a['n_true']}、不成立 {a['n_false']}），不给条件化结果。"
        elif not a["has_overrides"]:
            item["text"] = "没有声明 if_false 覆盖项，不成立时骨架不变，所以对结果没有影响。"
        elif a["effects"]:
            e = a["effects"][0]
            item["effect"] = e
            item["text"] = (
                f"对「{e['name']}」：成立 → {fmt_days(e['p50_true'])}（{e['p_reached_true']:.0%}）；"
                f"不成立 → {fmt_days(e['p50_false'])}（{e['p_reached_false']:.0%}）。"
            )
        out.append(item)

    def spread(x: Dict[str, Any]) -> float:
        e = x["effect"]
        return -(abs(e["delta_p"]) + abs(e["delta_p50"] or 0.0) / 3650.0) if e else 0.0

    out.sort(key=lambda x: (spread(x), x["statement"]))
    return out[:MAX_ASSUMPTIONS]


def _uncertainties(target: Optional[Dict[str, Any]], sens_rows: List[Dict[str, Any]], blockers: List[Dict[str, Any]], assumptions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """前三个关键不确定性：有敏感性结果就先用它（对目标里程碑的摆动最大的参数/路径/假设），不够再用预测本身的信号补。"""
    out: List[Dict[str, Any]] = []
    for r in sens_rows:
        if not r.get("score"):
            continue
        out.append({
            "label": r["label"], "source": "sensitivity", "score": r["score"],
            "detail": f"在 [{r['low']['value']}, {r['high']['value']}] 之间摆动，会让目标里程碑的 P50 从 {fmt_days(r['low']['p50'])} 变到 {fmt_days(r['high']['p50'])}",
        })
    extra: List[Dict[str, Any]] = []
    for a in assumptions:
        if a["effect"]:
            e = a["effect"]
            extra.append({"label": f"假设「{a['statement']}」是否成立", "source": "forecast", "score": abs(e["delta_p"]) + abs(e["delta_p50"] or 0.0) / 3650.0, "detail": a["text"]})
    for b in blockers:
        p = b["p_resolved"]
        if p is not None and 0.0 < p < 1.0:
            extra.append({"label": f"瓶颈「{b['name']}」能否解决、何时解决", "source": "forecast", "score": min(p, 1 - p), "detail": b["text"]})
    if target is not None and 0.0 < (target.get("p_reached") or 0.0) < 1.0:
        p = target["p_reached"]
        extra.append({"label": f"目标里程碑「{target['name']}」本身是否会达成", "source": "forecast", "score": min(p, 1 - p), "detail": f"视野内达成的运行占 {p:.0%}"})
    extra.sort(key=lambda x: (-x["score"], x["label"]))
    seen = {o["label"] for o in out}
    for x in extra:
        if len(out) >= MAX_UNCERTAINTIES:
            break
        if x["label"] not in seen:
            out.append(x)
            seen.add(x["label"])
    return out[:MAX_UNCERTAINTIES]


def _signals(watch: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mine = sorted(watch, key=lambda w: (0 if w.get("source") == "anatomy" else 1, -(w.get("score") or 0.0), w.get("key", "")))
    return [{"key": w["key"], "watch": w["watch"], "means": w["means"], "source": w["source"], "basis": w.get("basis")} for w in mine[:MAX_SIGNALS]]


def _element_section(
    settings: Dict[str, Any], eid: str, result: Optional[Dict[str, Any]], sens: Optional[Dict[str, Any]],
    evidence: Optional[Sequence[Dict[str, Any]]], now: Any, backtest: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    line = er.resolve(settings, eid) or {}
    anatomy = an.get_anatomy(line) or {}
    meta_a = anatomy.get("meta") or {}
    ok = bool(result and result.get("ok"))
    fmeta = result["meta"] if ok else None
    conf = confidence(settings, line, forecast_meta=fmeta, evidence=evidence, now=now, backtest=backtest)
    sec: Dict[str, Any] = {
        "id": eid, "label": _line_label(line) or eid, "type_label": ev.type_label(line.get("element_type")),
        "status": meta_a.get("anatomy_status", "draft"), "status_label": ev.STATUS_LABELS.get(meta_a.get("anatomy_status", "draft"), "未审阅"),
        "key": an.is_key(line), "has_forecast": ok, "reason": "" if ok else str((result or {}).get("reason") or "没有预测结果"),
        "confidence": conf,
    }
    ev_summary = None
    if evidence is not None:
        view = ev.build_evidence_view(settings, line, evidence, now=now)
        ev_summary = {"active": len(view["items"]), "stale": view["stale_count"], "prior_items": len(view["prior"]), "researched": view["researched"]}
    sec["evidence"] = ev_summary
    if not ok:
        sec.update(headline=f"「{sec['label']}」：无法预测（{sec['reason']}）", answers=None, uncertainties=[], milestones=[], metrics=[], banners=[])
        return sec
    mine = lambda rows: [r for r in rows or [] if r.get("element") == eid]  # noqa: E731
    ms_rows = mine(result.get("milestones"))
    horizon = fmeta["horizon_days"]
    target, state = _target_milestone(ms_rows)
    success = _answer_success(target, state, horizon)
    when = _answer_when(target, state)
    blockers = _blockers(mine(result.get("bottlenecks")))
    assumptions = _assumptions(mine(result.get("conditional")))
    sens_rows: List[Dict[str, Any]] = []
    if sens and sens.get("ok") and target is not None:
        for t in sens.get("targets") or []:
            if t.get("element") == eid and t.get("id") == target["id"]:
                sens_rows = list(t.get("rows") or [])
                break
    watch = [w for w in fc.build_watchlist(settings, result, sens) if w.get("element") == eid]
    uncertainties = _uncertainties(target, sens_rows, blockers, assumptions)
    banners: List[str] = []
    if fmeta.get("truncated"):
        banners.append(f"时间预算用尽，只跑了 {fmeta['runs_done']}/{fmeta['runs_requested']} 次——区间噪声更大。")
    if fmeta.get("time_precision_degraded"):
        banners.append(fmeta.get("time_precision_note") or "时间精度降级")
    share = (fmeta.get("params") or {}).get("llm_prior_share")
    if share:
        banners.append(f"参数里有 {share:.0%} 仍是 LLM 先验（没有出处/未经你确认）。")
    if sens is not None and sens.get("ok") and (sens.get("meta") or {}).get("skipped"):
        banners.append("敏感性分析时间预算用尽，部分情景没跑完：" + "；".join(sens["meta"]["skipped"][:4]))
    metrics = [m for m in mine(result.get("metrics")) if m.get("p50") and any(v is not None for v in m["p50"])]
    sec.update(
        headline=f"「{sec['label']}」：{success['text']}" + (f" {when['text']}" if when["text"] else ""),
        answers={"success": success, "when": when, "blockers": blockers, "assumptions": assumptions, "signals": _signals(watch)},
        uncertainties=uncertainties, milestones=ms_rows, metrics=metrics, banners=banners,
        gates=mine(result.get("gates")), branches=mine(result.get("branches")),
        sensitivity_rows=sens_rows[:10], sensitivity_target=(target or {}).get("name", ""),
    )
    return sec


def build_brief(
    settings: Optional[Dict[str, Any]], results: Dict[str, Any], *, evidence: Optional[Sequence[Dict[str, Any]]] = None,
    now: Any = None, backtest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把预测结果组织成简报（纯函数）。`results = {"elements": [id...], "forecast": {id: run_forecast 结果},
    "sens": {id: sensitivity 结果 | None}, "fallback_all": bool}`（`run_brief` 的产物）。"""
    if not settings or not an.is_enabled(settings):
        return {"ok": False, "reason": "剖面功能未开启（需要元素模式与 anatomy_enabled）", "honesty": HONEST_NOTE}
    ids = list(results.get("elements") or [])
    if not ids:
        return {"ok": False, "reason": "没有带可结算内容（指标/组件/瓶颈/里程碑/门槛）的元素，无法生成预测简报", "honesty": HONEST_NOTE}
    sections = [
        _element_section(settings, eid, (results.get("forecast") or {}).get(eid), (results.get("sens") or {}).get(eid), evidence, now, backtest)
        for eid in ids
    ]
    overall = overall_confidence([(s["id"], s["label"], s["confidence"]) for s in sections])
    banners: List[str] = []
    if results.get("fallback_all"):
        banners.append("没有被标为「重点」的可结算元素，简报改为覆盖全部可结算元素。")
    metas = [(results.get("forecast") or {}).get(i, {}).get("meta") for i in ids]
    metas = [m for m in metas if m]
    first = metas[0] if metas else {}
    return {
        "ok": True,
        "meta": {
            "elements": ids, "runs_requested": first.get("runs_requested"), "runs_done": [m.get("runs_done") for m in metas],
            "seed": first.get("seed"), "horizon_days": first.get("horizon_days"), "mode": first.get("mode"),
            "truncated": any(m.get("truncated") for m in metas), "time_precision_degraded": any(m.get("time_precision_degraded") for m in metas),
            "sensitivity": any(bool(s) and s.get("ok") for s in (results.get("sens") or {}).values()),
            "fallback_all": bool(results.get("fallback_all")),
        },
        "honesty": HONEST_NOTE, "confidence_note": CONFIDENCE_NOTE, "overall": overall, "banners": banners,
        "summary": [s["headline"] for s in sections], "elements": sections,
    }


# ═════════════════════════════════════════════════════════════════════
# 编排：跑预测 → 组织简报（仍不调 LLM、不读写磁盘）
# ═════════════════════════════════════════════════════════════════════


def _split(total: Any, n: int, default: float) -> float:
    """把总墙钟预算平均分给 n 个元素；`<= 0` = 不限（沿用 `forecast` 的约定）。"""
    t = fc._num(total)
    if t is None:
        t = default
    if t <= 0:
        return 0.0
    return t / max(1, n)  # 不设下限：总预算就是总预算；`run_forecast` 自己保证至少跑完 50 次


def run_brief(
    settings: Optional[Dict[str, Any]], history: Any = None, *, evidence: Optional[Sequence[Dict[str, Any]]] = None,
    elements: Optional[Sequence[Any]] = None, runs: Any = None, seed: Any = None, horizon_days: Any = None,
    time_budget_sec: Any = None, with_sensitivity: bool = True, sens_budget_sec: Any = None, sens_runs: Any = None, sim_id: str = "",
    now: Any = None, backtest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """对每个重点元素各做一次（依赖闭包内的）蒙特卡洛，再对它做敏感性（可关），然后 `build_brief`。

    - `time_budget_sec` / `sens_budget_sec` 是**总**预算，平均分给各元素（`<= 0` 不限；不设单元素下限，但每个元素的预测
      至少会跑完 50 次，见 `forecast.MIN_RUNS_BEFORE_TRUNCATE`）；被截断的结果在简报里明示（`meta.truncated`、置信扣分）。
    - `sens_runs`：敏感性每个情景的运行次数（缺省沿用 `forecast.sensitivity` 的默认：`mc_runs // 10`，夹在 50–200）；
    - 固定种子且没被时间预算截断时，结果逐位可复现（每个元素的闭包结果与\"全量运行\"一致，见 A6）。
    - 不能预测 → `{"ok": False, "reason": ...}`。
    """
    if not settings or not an.is_enabled(settings):
        return {"ok": False, "reason": "剖面功能未开启（需要元素模式与 anatomy_enabled）", "honesty": HONEST_NOTE}
    ids, fallback = select_elements(settings, elements)
    if not ids:
        return {"ok": False, "reason": "没有带可结算内容（指标/组件/瓶颈/里程碑/门槛）的元素，无法生成预测简报", "honesty": HONEST_NOTE}
    fb = _split(time_budget_sec, len(ids), DEFAULT_BRIEF_BUDGET_SEC)
    sb = _split(sens_budget_sec, len(ids), DEFAULT_SENS_BUDGET_SEC)
    forecasts: Dict[str, Any] = {}
    sens: Dict[str, Any] = {}
    for eid in ids:
        forecasts[eid] = fc.run_forecast(settings, history, runs=runs, seed=seed, horizon_days=horizon_days, time_budget_sec=fb, sim_id=sim_id, element=eid)
        sens[eid] = None
        if with_sensitivity and forecasts[eid].get("ok"):
            sens[eid] = fc.sensitivity(settings, history, element=eid, runs=sens_runs, seed=seed, horizon_days=horizon_days, time_budget_sec=sb, sim_id=sim_id)
    return build_brief(
        settings, {"elements": ids, "forecast": forecasts, "sens": sens, "fallback_all": fallback},
        evidence=evidence, now=now, backtest=backtest,
    )
