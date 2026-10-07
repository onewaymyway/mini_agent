"""world_simulator/html_export_anatomy.py — 静态 HTML 导出接入元素档案与预测简报（第二十四轮 A7）。

设计依据：计划 §5.8 第 5 条。沿用 `html_export_mechanisms` 的铁律：

1. **没有数据就不输出任何东西**（返回空串、不追加 CSS）：剖面总开关未开 / 没有任何带剖面的元素 → 导出与接入前逐字节相同；
2. **不重算业务**：档案用 `element_view.build_profile/build_progress`，简报用 `forecast_brief.run_brief`；
3. **按分支取动态状态**（`mech.branch_settings`）；
4. **所有自由文本转义**；证据链接只允许 http(s)；
5. 任一区块异常 → 降级成一行说明，不连累整个导出；
6. 自包含：图是内联 SVG，不引用外部资源。

## 说明

- 简报在**导出时现算**（蒙特卡洛不落盘）。固定种子且没被时间预算截断时，同一份数据导出结果逐字节一致；
  被截断会在页面上明示。导出页面**不写耗时**，以保持可复现。
- 区间旁固定标注\"不是校准过的概率\"，并展示置信等级及每条扣分原因。
"""

from __future__ import annotations

import html as html_stdlib
from typing import Any, Dict, List, Optional, Sequence

from world_simulator import anatomy as an
from world_simulator import anatomy_engine as ae
from world_simulator import element_view as ev
from world_simulator import forecast_brief as fb
from world_simulator import html_export_mechanisms as mech

EXTRA_CSS = """
<style>
.ws-anat-tag { display:inline-block; padding:0 .4rem; border-radius:8px; font-size:.78rem; border:1px solid currentColor; margin-left:.3rem; }
.ws-anat-high { color:#16a34a; } .ws-anat-medium { color:#d97706; } .ws-anat-low { color:#dc2626; }
.ws-anat-table { width:100%; border-collapse:collapse; font-size:.9rem; margin:.4rem 0; }
.ws-anat-table th, .ws-anat-table td { text-align:left; padding:.3rem .5rem; border-bottom:1px solid rgba(140,122,230,.25); vertical-align:top; }
.ws-anat-svg { max-width:100%; height:auto; }
.ws-anat-note { margin:.4rem 0; padding:.35rem .6rem; border-left:3px solid #f59e0b; background:rgba(245,158,11,.08); font-size:.88rem; border-radius:4px; }
</style>
"""

_PALETTE = {"band": "#8c7ae6", "line": "#6c5ce7", "grid": "#9ca3af", "thr": "#dc2626", "bar": "#8c7ae6"}


def _esc(value: Any) -> str:
    return html_stdlib.escape(str(value if value is not None else ""), quote=True)


def _note(text: str) -> str:
    return f'<div class="ws-anat-note">{_esc(text)}</div>'


# ── SVG ──────────────────────────────────────────────────────────────


def fan_svg(metric: Dict[str, Any], width: int = 360, height: int = 130) -> str:
    """指标分位带：P10–P90 填充带 + P50 线 + 目标阈值虚线（目标值会撑开纵轴，便于看出离目标多远）。数据缺失返回空串。"""
    days, p10, p50, p90 = metric.get("days") or [], metric.get("p10") or [], metric.get("p50") or [], metric.get("p90") or []
    pts = [(d, a, b, c) for d, a, b, c in zip(days, p10, p50, p90) if None not in (d, a, b, c)]
    if len(pts) < 2:
        return ""
    target = (metric.get("target") or {}).get("value")
    vals = [v for _d, a, _b, c in pts for v in (a, c)] + ([target] if isinstance(target, (int, float)) else [])
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1.0
    x0, x1 = pts[0][0], pts[-1][0]
    span = (x1 - x0) or 1.0
    px = lambda d: 30 + (d - x0) / span * (width - 40)  # noqa: E731
    py = lambda v: 10 + (hi - v) / (hi - lo) * (height - 28)  # noqa: E731
    upper = " ".join(f"{px(d):.1f},{py(c):.1f}" for d, _a, _b, c in pts)
    lower = " ".join(f"{px(d):.1f},{py(a):.1f}" for d, a, _b, _c in reversed(pts))
    mid = " ".join(f"{px(d):.1f},{py(b):.1f}" for d, _a, b, _c in pts)
    thr = ""
    if isinstance(target, (int, float)):  # 目标值已并入纵轴范围，所以总是画得出来
        thr = (f'<line x1="30" x2="{width - 10}" y1="{py(target):.1f}" y2="{py(target):.1f}" stroke="{_PALETTE["thr"]}" stroke-dasharray="4 3"/>'
               f'<text x="{width - 12}" y="{py(target) - 3:.1f}" font-size="9" text-anchor="end" fill="{_PALETTE["thr"]}">目标 {_esc(target)}</text>')
    return (
        f'<svg class="ws-anat-svg" viewBox="0 0 {width} {height}" width="{width}" role="img" aria-label="{_esc(metric.get("name"))} 分位带">'
        f'<polygon points="{upper} {lower}" fill="{_PALETTE["band"]}" fill-opacity=".25"/>'
        f'<polyline points="{mid}" fill="none" stroke="{_PALETTE["line"]}" stroke-width="2"/>{thr}'
        f'<text x="2" y="{py(hi):.1f}" font-size="9" fill="currentColor">{hi:g}</text>'
        f'<text x="2" y="{py(lo):.1f}" font-size="9" fill="currentColor">{lo:g}</text>'
        f'<text x="30" y="{height - 4}" font-size="9" fill="currentColor">现在</text>'
        f'<text x="{width - 10}" y="{height - 4}" font-size="9" text-anchor="end" fill="currentColor">{fb.fmt_days(x1)}后</text></svg>'
    )


def range_svg(rows: Sequence[Dict[str, Any]], horizon: float, width: int = 420) -> str:
    """里程碑 P10–P90 区间条 + P50 标记；横轴 = 从现在起到视野末。没有可画的行返回空串。"""
    rows = [r for r in rows if r.get("status") == "pending" and r.get("p10") is not None]
    if not rows or not horizon:
        return ""
    h = 22 * len(rows) + 14
    parts = []
    for i, r in enumerate(rows):
        y = 8 + i * 22
        a = 150 + r["p10"] / horizon * (width - 160)
        b = 150 + (r["p90"] if r.get("p90") is not None else horizon) / horizon * (width - 160)
        label = str(r["name"])[:10]
        parts.append(f'<text x="2" y="{y + 10}" font-size="10" fill="currentColor">{_esc(label)}</text>'
                     f'<rect x="{a:.1f}" y="{y + 2}" width="{max(2.0, b - a):.1f}" height="10" fill="{_PALETTE["bar"]}" fill-opacity=".45"/>')
        if r.get("p50") is not None:
            m = 150 + r["p50"] / horizon * (width - 160)
            parts.append(f'<line x1="{m:.1f}" x2="{m:.1f}" y1="{y}" y2="{y + 14}" stroke="{_PALETTE["line"]}" stroke-width="2"/>')
    return f'<svg class="ws-anat-svg" viewBox="0 0 {width} {h}" width="{width}" role="img" aria-label="里程碑时间分布">{"".join(parts)}</svg>'


def tornado_svg(rows: Sequence[Dict[str, Any]], width: int = 420) -> str:
    """敏感性龙卷风：每行一个参数，条长 = 打分（对目标里程碑 P50 / 概率的最大摆幅）。"""
    rows = [r for r in rows if r.get("score")][:8]
    if not rows:
        return ""
    top = max(r["score"] for r in rows) or 1.0
    h = 20 * len(rows) + 6
    parts = []
    for i, r in enumerate(rows):
        y = 4 + i * 20
        w = r["score"] / top * (width - 230)
        parts.append(f'<text x="2" y="{y + 11}" font-size="10" fill="currentColor">{_esc(str(r["label"])[-26:])}</text>'
                     f'<rect x="220" y="{y + 2}" width="{max(2.0, w):.1f}" height="10" fill="{_PALETTE["bar"]}" fill-opacity=".6"/>')
    return f'<svg class="ws-anat-svg" viewBox="0 0 {width} {h}" width="{width}" role="img" aria-label="敏感性（龙卷风）">{"".join(parts)}</svg>'


# ── 档案 ─────────────────────────────────────────────────────────────


def _evidence_html(view: Dict[str, Any]) -> str:
    if not view or not (view.get("items") or view.get("prior")):
        return ""
    rows = []
    for i in view["items"]:
        url = i["url"] if str(i["url"]).startswith(("http://", "https://")) else ""
        title = f'<a href="{_esc(url)}" rel="noopener noreferrer">{_esc(i["title"])}</a>' if url else _esc(i["title"])
        flags = "；".join(i["flags"])
        rows.append(
            f"<tr><td>{_esc(i['claim'])}{(' · ' + _esc(i['value_text'])) if i['value_text'] else ''}</td><td>{title}</td>"
            f"<td>{_esc(i['published_at'])}</td><td>{_esc(i['confidence_label'])}</td>"
            f"<td>{'已过期' if i['stale'] else ''}{(' ' + _esc(flags)) if flags else ''}</td></tr>"
        )
    out = ""
    if rows:
        out += ('<table class="ws-anat-table"><tr><th>要点</th><th>出处</th><th>发布</th><th>置信</th><th>提示</th></tr>' + "".join(rows) + "</table>")
    if view.get("prior"):
        out += f'<p class="ws-muted">仍是 LLM 先验（无外部出处）：{_esc("、".join(p["name"] for p in view["prior"][:12]))}</p>'
    return out


def _profile_html(settings: Dict[str, Any], line: Dict[str, Any], evidence: Optional[Sequence[Dict[str, Any]]], history: Any) -> str:
    prof = ev.build_profile(settings, line["id"], evidence=list(evidence) if evidence is not None else None)
    if not prof.get("has_anatomy"):
        return ""
    st = prof["stats"]
    ratio = f"（占 {st['llm_prior_ratio']:.0%}）" if st["llm_prior_ratio"] is not None else ""
    head = (
        f'<h3 class="ws-serif">🧬 {_esc(prof["label"])} <span class="ws-anat-tag">{_esc(prof["template_label"])}模板</span>'
        f'<span class="ws-anat-tag">{_esc(prof["status_label"])}</span>{"<span class=\"ws-anat-tag\">重点</span>" if prof["key"] else ""}</h3>'
        f'<p class="ws-muted">共 {st["total"]} 项；有出处 {st["sourced"]}，已确认 {st["user_confirmed"]}，已编辑 {st["user_edited"]}，'
        f'LLM 先验 {st["llm_prior"]}{ratio}。LLM 先验 = 没有外部出处，仅是模型的猜测。</p>'
    )
    body = []
    for sec in prof["sections"]:
        rows = "".join(
            f"<tr><td>{_esc(r['name'])}</td><td>{_esc(r['basis'])}</td><td>{_esc(r['detail'])}</td></tr>" for r in sec["rows"]
        )
        body.append(f'<h4>{_esc(sec["label"])}</h4><table class="ws-anat-table"><tr><th>名称</th><th>来源</th><th>说明</th></tr>{rows}</table>')
    if prof["problems"]:
        body.append(_note("体检提示（不影响使用）：" + "；".join(f"{p['where']}：{p['message']}" for p in prof["problems"][:8])))
    prog = ev.build_progress(settings, history, line["id"])
    if prog.get("has_progress"):
        reached = [m["name"] for m in prog["milestones"] if m["reached_step"] is not None]
        body.append(
            f'<p class="ws-muted">引擎推进到第 {_esc(prog["last_step"])} 步（模拟第 {_esc(round(prog["clock_day"]))} 天）；'
            f'已达成里程碑：{_esc("、".join(reached) if reached else "暂无")}。</p>'
        )
    body.append(_evidence_html(prof.get("evidence") or {}))
    return f'<div class="ws-card">{head}{"".join(body)}</div>'


# ── 简报 ─────────────────────────────────────────────────────────────


def _confidence_html(conf: Dict[str, Any], title: str) -> str:
    rows = "".join(f"<li>{_esc(d['label'])}（−{d['points']}）：{_esc(d['detail'])}</li>" for d in conf.get("deductions") or [])
    caps = "".join(f"<li>封顶「{_esc(fb.LEVEL_LABELS[c['level']])}」：{_esc(c['detail'])}</li>" for c in conf.get("caps") or [])
    return (
        f'<p><b>{_esc(title)}</b><span class="ws-anat-tag ws-anat-{_esc(conf["level"])}">{_esc(conf["level_label"])}（{_esc(conf["score"])} 分）</span></p>'
        f'{("<ul>" + rows + caps + "</ul>") if rows or caps else ""}'
    )


def _element_brief_html(e: Dict[str, Any], horizon: Any) -> str:
    out = [f'<div class="ws-card"><h3 class="ws-serif">🔮 {_esc(e["label"])}</h3>', f"<p>{_esc(e['headline'])}</p>", _confidence_html(e["confidence"], "本元素置信等级")]
    for b in e.get("banners") or []:
        out.append(_note(b))
    ans = e.get("answers")
    if ans:
        if ans["blockers"]:
            out.append("<h4>卡在哪</h4><ul>" + "".join(f"<li><b>{_esc(b['name'])}</b>：{_esc(b['text'])}</li>" for b in ans["blockers"]) + "</ul>")
        if ans["assumptions"]:
            out.append("<h4>靠什么假设</h4><ul>" + "".join(
                f"<li><b>{_esc(a['statement'])}</b>（先验成立概率 {_esc(a['prior_p_true'])}）：{_esc(a['text'])}</li>" for a in ans["assumptions"]) + "</ul>")
        if e["uncertainties"]:
            out.append("<h4>前三个关键不确定性</h4><ol>" + "".join(
                f"<li><b>{_esc(u['label'])}</b>：{_esc(u['detail'])}（{'敏感性分析' if u['source'] == 'sensitivity' else '预测信号'}）</li>" for u in e["uncertainties"]) + "</ol>")
        if ans["signals"]:
            out.append("<h4>该盯的先行信号</h4><ul>" + "".join(
                f"<li><b>{_esc(s['watch'])}</b>：{_esc(s['means'])}</li>" for s in ans["signals"]) + "</ul>")
        ms = [m for m in e["milestones"] if m.get("status") == "pending"]
        if ms:
            rows = "".join(
                f"<tr><td>{_esc(m['name'])}</td><td>{m['p_reached']:.0%}</td><td>{_esc(fb.fmt_days(m['p10']))}</td><td>{_esc(fb.fmt_days(m['p50']))}</td><td>{_esc(fb.fmt_days(m['p90']))}</td></tr>"
                for m in ms)
            out.append(f'<h4>里程碑时间分布</h4>{range_svg(ms, horizon or 1)}<table class="ws-anat-table"><tr><th>里程碑</th><th>视野内达成</th><th>P10</th><th>P50</th><th>P90</th></tr>{rows}</table>')
        for m in e["metrics"]:
            svg = fan_svg(m)
            if svg:
                out.append(f'<h4>指标 {_esc(m["name"])}{(" (" + _esc(m["unit"]) + ")") if m.get("unit") else ""}</h4>{svg}')
        if e.get("sensitivity_rows"):
            out.append(f'<h4>敏感性（龙卷风）：{_esc(e.get("sensitivity_target"))}</h4>{tornado_svg(e["sensitivity_rows"])}')
    if e.get("evidence"):
        ev_s = e["evidence"]
        out.append(f'<p class="ws-muted">证据：有效 {ev_s["active"]} 条，其中过期 {ev_s["stale"]} 条；仍是 LLM 先验的条目 {ev_s["prior_items"]} 个。</p>')
    out.append("</div>")
    return "".join(out)


def brief_html(brief: Dict[str, Any]) -> str:
    if not brief or not brief.get("ok"):
        return ""
    meta = brief["meta"]
    head = (
        '<div class="ws-card"><h2 class="ws-serif">🔮 预测简报</h2>'
        f'<p class="ws-muted">{_esc(brief["honesty"])}（{_esc(meta.get("runs_requested"))} 次推演，种子 {_esc(meta.get("seed"))}，{_esc(fb._fmt_horizon(meta.get("horizon_days")))}。'
        f'{_esc(brief["confidence_note"])}）</p>'
        + _confidence_html(brief["overall"], f"整体置信等级 — {brief['overall']['rule']}：")
    )
    for b in brief.get("banners") or []:
        head += _note(b)
    if meta.get("truncated"):
        head += _note("至少有一个元素的预测被时间预算截断，区间噪声更大。")
    head += "<ul>" + "".join(f"<li>{_esc(s)}</li>" for s in brief["summary"]) + "</ul></div>"
    return head + "".join(_element_brief_html(e, meta.get("horizon_days")) for e in brief["elements"])


# ── 入口 ─────────────────────────────────────────────────────────────


def sections_html(
    manifest: Any, history: List[Any], branch: str, *, evidence: Optional[Sequence[Dict[str, Any]]] = None,
    with_forecast: bool = True, runs: Any = None, time_budget_sec: Any = 30.0, sens_budget_sec: Any = 10.0,
) -> str:
    """元素档案（每个带剖面的元素一张卡）+ 预测简报。没有数据返回空串。"""
    try:
        settings = mech.branch_settings(manifest, history, branch)
    except Exception:  # noqa: BLE001 — 动态状态还原失败：整组跳过
        return ""
    if not an.is_enabled(settings) or not an.has_anatomy(settings):
        return ""
    sim_id = str(getattr(manifest, "sim_id", "") or "")
    parts: List[str] = []
    if with_forecast and ae.is_active(settings):
        parts.append(mech._safe(lambda: brief_html(fb.run_brief(
            settings, history, evidence=evidence, runs=runs, time_budget_sec=time_budget_sec, sens_budget_sec=sens_budget_sec, sim_id=sim_id)), "预测简报"))
    for line in an.lines_with_anatomy(settings):
        parts.append(mech._safe(lambda line=line: _profile_html(settings, line, evidence, history), "元素档案"))
    body = "".join(parts)
    return body if body.strip() else ""
