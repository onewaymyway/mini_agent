"""world_simulator/html_export_mechanisms.py — 静态 HTML 导出接入第二十二轮新机制（P10）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §10 P10。
`html_export.py` 原先不认识一致性警告、技术状态、抽样事件、因果引擎、树接地/树影响；本模块把它们
**只读**地渲染成 HTML 片段，由 `html_export.py` 拼进去。

## 铁律

1. **没有数据就不输出任何东西**（返回空串，且不要求追加 CSS）——旧实例、没开新机制的实例，导出
   与接入前**逐字节相同**。每个函数的"有没有数据"判定写在各自 docstring 里。
2. **不重算业务逻辑**：状态/统计/时间线全部复用既有纯函数（`tech_model.summarize`、
   `causal_view.build_edge_view/edges_to_dot/build_due_timeline`、`consistency_guard.analyze_history`、
   `event_sampler.get_priors`、`tree_grounding.pending_suggestions`、`tree_effects.declared_effects`）。
3. **按分支取动态状态**：`tech_state`/`causal_pending`/`causal_lines` 是分支作用域的。导出某条分支时，
   用该分支最近一份 `dynamic_snapshot` 还原；没有快照时，仅当导出分支就是当前活跃分支才退回
   `manifest.settings`（它是活跃分支的工作副本），否则**不显示**动态状态（宁可缺，不拿别的时间线冒充）。
4. **转义**：所有自由文本走 `_esc`。
5. **措辞不夸大**：兑现统计/边状态是 LLM 自报；体检是结构性代理指标；未核对/未确认的先验显式标出。
6. 任一区块渲染抛异常 → 该区块降级为一行说明，不让整个导出失败（与 `html_export` 对 graphviz 的
   降级同一原则）。

## 已知边界

- 没有浏览器级目视验证（只有断言型测试）；着色图依赖 Graphviz，缺失时降级为文字表。
- 每步提示块与 `app.py` 的同名 helper 各自独立实现一份（沿用"导出模块不 import app.py"的约定），
  两处文案需要人工保持一致。
- 技术树/因果引擎区块反映的是**导出分支最后一步之后**的状态，不是逐步演化；逐步变化看时间线里的每步提示。
"""

from __future__ import annotations

import html as html_stdlib
from typing import Any, Dict, List, Optional, Sequence

from world_simulator import causal_engine, causal_view, dynamic_state
from world_simulator import event_sampler, tech_model, tree_effects, tree_grounding

# 只有渲染了本模块的任何区块时，才把这段 CSS 追加进页面（旧实例导出不变）。
EXTRA_CSS = """
<style>
.ws-mech-warn { margin-top: 0.4rem; padding: 0.35rem 0.6rem; border-left: 3px solid #f59e0b;
  background: rgba(245, 158, 11, 0.08); font-size: 0.88rem; border-radius: 4px; }
.ws-mech-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; margin: 0.4rem 0; }
.ws-mech-table th, .ws-mech-table td { text-align: left; padding: 0.3rem 0.5rem;
  border-bottom: 1px solid rgba(140, 122, 230, 0.25); vertical-align: top; }
.ws-mech-bar { display: inline-block; width: 6rem; height: 0.55rem; background: rgba(140,122,230,0.2);
  border-radius: 3px; vertical-align: middle; overflow: hidden; }
.ws-mech-bar > i { display: block; height: 100%; background: #8c7ae6; }
.ws-mech-badge { display: inline-block; padding: 0 0.4rem; border-radius: 8px; font-size: 0.78rem;
  border: 1px solid currentColor; margin-left: 0.3rem; }
.ws-mech-legend span { margin-right: 0.8rem; }
</style>
"""

_DISCLAIMER_SELF_REPORT = "兑现统计与边状态来自 AI 自报，引擎无法验证效果是否真的发生，不等于世界里被验证/被证伪。"


def _esc(value: Any) -> str:
    return html_stdlib.escape(str(value if value is not None else ""), quote=True)


def _muted(text: str) -> str:
    return f'<p class="ws-muted">{_esc(text)}</p>'


def _safe(fn, label: str) -> str:
    """区块渲染的统一兜底：异常 → 一行说明，不连累整个导出。"""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — 展示层兜底，原因不影响导出其余部分
        return _muted(f"（{label}渲染失败：{type(exc).__name__}，已跳过）")


# ── 按分支还原动态状态 ──────────────────────────────────────────────


def branch_settings(manifest: Any, history: List[Any], branch: str) -> Dict[str, Any]:
    """导出分支视角下的 settings：动态 key 取该分支最近快照；无快照时仅活跃分支退回工作副本。"""
    base = dict(getattr(manifest, "settings", None) or {})
    snap = dynamic_state.latest_snapshot(list(history or []))
    if isinstance(snap, dict):
        return dynamic_state.apply_to_settings(base, snap)
    if branch == getattr(manifest, "branch", None):
        return base
    for key in dynamic_state.DYNAMIC_KEYS:
        base.pop(key, None)
    return base


# ── 每步提示块（时间线里的状态卡片用）──────────────────────────────


def _warn(text: str) -> str:
    return f'<div class="ws-mech-warn">{text}</div>'


def step_notes_html(state: Any) -> str:
    """一步里所有新机制的审计提示。**全部为空时返回空串**。

    覆盖：一致性警告 / 技术违规与修复 / 抽样事件 / 因果入队与交代 / 因果违规 / 树接地。
    """
    try:
        return _step_notes(state)
    except Exception:  # noqa: BLE001 — 单步提示渲染失败不连累整个时间线
        return ""


def _step_notes(state: Any) -> str:
    parts: List[str] = []

    for w in getattr(state, "consistency_warnings", None) or []:
        if isinstance(w, dict):
            parts.append(_warn(f'🧭 一致性提示 {_esc(w.get("code", ""))}：{_esc(w.get("message", ""))}'))

    for v in getattr(state, "tech_violations", None) or []:
        if isinstance(v, dict):
            icon = "ℹ️" if v.get("severity") == "info" else "⚙️"
            parts.append(_warn(f'{icon} 技术模型 {_esc(v.get("code", ""))}：{_esc(v.get("message", ""))}'))
    repair = getattr(state, "tech_repair", None)
    if isinstance(repair, dict) and repair.get("status"):
        status_text = {
            "accepted": "已采纳修复结果（上面列出的是修复后剩余的违规）",
            "rejected": "修复后没有改善，保留了第一次裁决",
            "failed": "修复调用失败，保留了第一次裁决",
        }.get(str(repair.get("status")), str(repair.get("status")))
        before = "；".join(
            f'{_esc(v.get("code", ""))}：{_esc(v.get("message", ""))}'
            for v in (repair.get("violations_before") or []) if isinstance(v, dict)
        )
        parts.append(_warn(
            f"🛠️ 技术违规修复调用：{_esc(status_text)}。修复前的违规：{before or '（无）'}"
            "（修复只改写了技术提议，没有改写叙事。）"
        ))
    for a in getattr(state, "tech_updates", None) or []:
        if isinstance(a, dict) and a.get("action") == "time" and a.get("elapsed_source") == "fallback":
            parts.append(_warn("⏱️ 技术模型：这一步 AI 没有给出有效的 elapsed_days，已按每步固定天数推算（精度降级）。"))

    for e in getattr(state, "sampled_events", None) or []:
        if not isinstance(e, dict):
            continue
        note = "（本步命中数超过上限，未注入）" if e.get("suppressed_by_cap") else ""
        unverified = "" if e.get("verified") else "（先验未核对）"
        try:
            prob = f"{float(e.get('probability', 0)):.0%}"
        except (TypeError, ValueError):
            prob = "?"
        unconfirmed = "（AI 提议、用户未确认）" if e.get("confirmed") is False else ""
        parts.append(_warn(
            f'🎲 外生事件：{_esc(e.get("description", e.get("id", "")))}'
            f'（严重度 {_esc(e.get("severity", ""))}，抽样概率 {_esc(prob)}）{unverified}{unconfirmed}{note}'
        ))

    for q in getattr(state, "causal_queued", None) or []:
        if isinstance(q, dict) and q.get("action") == "queued":
            parts.append(_warn(
                f'🔗 因果入队：{_esc(q.get("edge_id", ""))}'
                f'（{_esc(causal_engine._reason_label(q.get("trigger_reason")))}），到期后会提醒 AI 交代效果'
            ))
    label = {"realized": "已兑现", "dampened": "部分兑现", "countered": "被抵消", "postponed": "推迟",
             "expired": "推迟过多自动结案", "unaddressed": "长期未交代自动结案"}
    for d in getattr(state, "effect_dispositions", None) or []:
        if not isinstance(d, dict):
            continue
        reason = f"：{_esc(d.get('reason'))}" if d.get("reason") else ""
        parts.append(_warn(
            f'🔗 因果交代 {_esc(d.get("edge_id", ""))} → '
            f'{_esc(label.get(str(d.get("disposition")), str(d.get("disposition"))))}（AI 自报）{reason}'
        ))
    for v in getattr(state, "causal_violations", None) or []:
        if isinstance(v, dict):
            parts.append(_warn(f'🔗 因果引擎 {_esc(v.get("code", ""))}：{_esc(v.get("message", ""))}'))

    icons = {"downgraded": "⬇️", "flagged": "⚠️", "auto_activated": "⚡", "auto_invalidated": "✂️", "error": "⚙️"}
    for e in getattr(state, "tree_grounding", None) or []:
        if isinstance(e, dict):
            parts.append(_warn(
                f'{icons.get(str(e.get("action")), "🌳")} 树接地 {_esc(e.get("code", ""))}：{_esc(e.get("message", ""))}'
            ))
    return "".join(parts)


def any_step_notes(history: Sequence[Any]) -> bool:
    return any(step_notes_html(s) for s in history or [])


# ── 真实性体检摘要 ──────────────────────────────────────────────────


def health_html(manifest: Any, history: List[Any], settings: Dict[str, Any]) -> str:
    """真实性体检摘要。**仅当历史里出现过一致性守卫的痕迹**（任一步有 `consistency_warnings`，或有
    `dynamic_snapshot`）才渲染——旧实例没有这些痕迹，导出不变。"""
    if not any(getattr(s, "consistency_warnings", None) or getattr(s, "dynamic_snapshot", None) for s in history):
        return ""

    def _build() -> str:
        from world_simulator import quality_signals

        health = quality_signals.summarize_realism_health(
            history,
            causal_lines=settings.get("causal_lines"),
            declared_causal_graph=settings.get("declared_causal_graph"),
            config={"likelihood_nominal": settings.get("likelihood_nominal") or None},
            settings=settings,
        )
        counts = health["warning_counts"]
        rows: List[str] = [
            f'<p class="ws-muted">{_esc(health["disclaimer"])}存在误报；语义上是否合理仍需自行判断。</p>',
            "<p><b>结构性告警：</b>"
            + (_esc("，".join(f"{c} × {n}" for c, n in sorted(counts.items()))) if counts else "暂无")
            + "</p>",
        ]
        cov = health["coverage"]
        rows.append(_muted(
            f"分支状态检查（C2/C3/C4）覆盖 {cov['tree_transition_pairs_checked']} 处快照变化——"
            "只覆盖新版本之后写入的步，旧步无法核验。"
        ))
        dens = health["c5_event_density"]
        if dens.get("ratio") is not None:
            rows.append(_muted(
                f"事件密度（C5）：{dens['advanced_steps']} 步中 {dens['dramatic_steps']} 步有重大决策/结构性变化/新能力"
                f"（{dens['ratio']:.0%}），最长连续 {dens['longest_run']} 步。"
            ))
        edge = health["c7_edge_coverage"]
        if edge.get("ratio") is not None:
            rows.append(_muted(
                f"跨线因果链未在先验因果图声明的占比（C7）：{edge['ratio']:.0%}（{edge['cross_line_links']} 条中 {edge['undeclared']} 条）"
            ))
        names = {"high": "高", "medium": "中", "low": "低"}
        calib = health["c8_tree_calibration"]
        for level in ("high", "medium", "low"):
            g = calib.get(level) or {}
            if g.get("terminal"):
                rows.append(_muted(
                    f"未来树校准（C8）· 可能性「{names[level]}」：已终结 {g['terminal']} 个，命中 {g['resolved']} 个"
                    f"（{g['hit_rate']:.0%}）——样本少时不具统计意义。"
                ))
        ledger = health.get("c8_likelihood_ledger") or {}
        for level in ("high", "medium", "low"):
            g = (ledger.get("by_likelihood") or {}).get(level) or {}
            if g.get("n"):
                gap = f"，与名义值相差 {g['gap']:+.0%}" if g.get("gap") is not None else ""
                rows.append(_muted(
                    f"校准账本 ·「{names[level]}」：{g['n']} 个，命中 {g['resolved']} 个（{g['hit_rate']:.0%}）{gap}"
                    + ("" if g.get("enough_samples") else "——样本不足")
                ))
        for inv in ledger.get("inversions") or []:
            rows.append(_muted(f"⚠️ 档位倒挂：{inv.get('message', '')}（可能性档位没有区分度，或样本偶然）"))
        c9 = health.get("c9_element_health")
        for note in (c9 or {}).get("notes") or []:
            rows.append(_muted(f"🧩 元素（C9，只读提示）：{note}"))
        recent = health["warnings"][-20:]
        if recent:
            rows.append("<ul>" + "".join(
                f"<li>第 {_esc(w['step'])} 步 · {_esc(w['code'])}：{_esc(w['message'])}</li>" for w in recent
            ) + "</ul>")
        return (
            '<div class="ws-card"><h2 class="ws-serif">🩺 真实性体检（结构性检查，非真实性评分）</h2>'
            + "".join(rows) + "</div>"
        )

    return _safe(_build, "真实性体检")


# ── 技术树 ──────────────────────────────────────────────────────────

_TECH_STATUS_TEXT = {"ready": "✅ 可迁移", "blocked": "⛔ 受阻", "stalled": "🕸️ 停滞",
                     "progressing": "🔧 推进中", "mature": "🏁 已达最高阶段"}
_TECH_STATUS_COLOR = {"ready": "#16a34a", "blocked": "#dc2626", "stalled": "#f59e0b",
                      "progressing": "#8c7ae6", "mature": "#0ea5e9"}


def _tech_dot(rows: List[Dict[str, Any]]) -> str:
    def q(text: Any) -> str:
        return str(text).replace("\\", "\\\\").replace('"', '\\"')

    lines = ["digraph T {", "  rankdir=LR;", '  bgcolor="transparent";',
             '  node [shape=box, style="rounded,filled", fillcolor="#eef2ff", fontname="sans-serif"];']
    ids = {r["node"]["id"] for r in rows}
    for r in rows:
        n = r["node"]
        color = _TECH_STATUS_COLOR.get(r["status"], "#8c7ae6")
        label = f'{n["name"]}\\n{tech_model.STAGE_LABELS.get(n["stage"], n["stage"])} {n["progress"]:.0%}'
        lines.append(f'  "{q(n["id"])}" [label="{q(label)}", color="{color}", penwidth=2];')
    for r in rows:
        for req in r["node"].get("requires") or []:
            tid = req.get("tech_id")
            if tid in ids:
                style = "dashed" if req.get("mode") == "soft" else "solid"
                lines.append(f'  "{q(tid)}" -> "{q(r["node"]["id"])}" [style="{style}"];')
    lines.append("}")
    return "\n".join(lines)


def _dot_to_svg(dot: str) -> Optional[str]:
    """DOT → 内嵌 SVG；Graphviz 不可用返回 None（调用方降级）。"""
    try:
        import graphviz  # 可选依赖

        svg = graphviz.Source(dot).pipe(format="svg").decode("utf-8")
        return svg[svg.index("<svg"):] if "<svg" in svg else None
    except Exception:  # noqa: BLE001
        return None


def tech_html(settings: Dict[str, Any]) -> str:
    """技术树。**仅当技术模型开启且该分支有节点**才渲染。"""
    if not tech_model.is_enabled(settings):
        return ""

    def _build() -> str:
        rows = tech_model.summarize(settings)
        if not rows:
            return ""
        body: List[str] = []
        for r in rows:
            n = r["node"]
            pct = max(0.0, min(1.0, float(n["progress"]))) * 100
            unverified = "" if n["dwell_verified"] else "（典型时长未验证）"
            extra: List[str] = []
            if r["unmet_hard"]:
                extra.append("硬前置未满足：" + "、".join(str(q.get("tech_id")) for q in r["unmet_hard"]))
            if n["bottleneck"]:
                extra.append(f"瓶颈：{n['bottleneck']}")
            if n["perceived_stage"] and n["perceived_stage"] != n["stage"]:
                extra.append(f"公众以为：{tech_model.STAGE_LABELS.get(n['perceived_stage'], n['perceived_stage'])}")
            if n["preexisting"]:
                extra.append("既有技术（未经验证）")
            body.append(
                f"<tr><td><b>{_esc(n['name'])}</b><br><span class=\"ws-muted\">{_esc(n['id'])}</span></td>"
                f"<td>{_esc(tech_model.STAGE_LABELS.get(n['stage'], n['stage']))}</td>"
                f'<td><span class="ws-mech-bar"><i style="width:{pct:.0f}%"></i></span> {pct:.0f}%</td>'
                f"<td>已停留 {n['dwell_days']:.0f}/{r['typical_dwell_days']:.0f} 天{_esc(unverified)}</td>"
                f"<td>{_esc(_TECH_STATUS_TEXT.get(r['status'], r['status']))}</td>"
                f"<td>{_esc('；'.join(extra))}</td></tr>"
            )
        has_edges = any((r["node"].get("requires") or []) for r in rows)
        svg = _dot_to_svg(_tech_dot(rows)) if has_edges else None
        graph = f'<div class="ws-causal-graph">{svg}</div>' if svg else ""
        return (
            f'<div class="ws-card"><h2 class="ws-serif">🧬 技术发展模型（{len(rows)} 个节点，导出分支末状态）</h2>'
            + _muted("阶段/进度由引擎按 elapsed_days 推算并裁决，AI 只能提议；典型停留时长标注“未验证”表示来自估计或通用占位值，不是领域事实。")
            + '<table class="ws-mech-table"><tr><th>技术</th><th>阶段</th><th>阶段内进度</th><th>停留</th><th>状态</th><th>备注</th></tr>'
            + "".join(body) + "</table>" + graph + "</div>"
        )

    return _safe(_build, "技术树")


# ── 外生事件先验 ────────────────────────────────────────────────────


def events_html(settings: Dict[str, Any]) -> str:
    """外生事件先验清单。**仅当事件采样开启且有先验（或有被丢弃的先验）**才渲染。
    不预告任何\"下一步会抽中什么\"——导出是通读报告，不剧透也不重算概率。"""
    if not event_sampler.is_enabled(settings):
        return ""

    def _build() -> str:
        priors, problems = event_sampler.get_priors(settings)
        if not priors and not problems:
            return ""
        rows: List[str] = []
        for p in priors:
            flags: List[str] = []
            if p.get("confirmed") is False:
                flags.append("AI 提议、你还没确认（不参与抽样）")
            if not p.get("verified"):
                flags.append("⚠️ 未核对")
            if p.get("source") and p.get("source") != "user":
                flags.append(f"来源 {p['source']}")
            if p.get("rationale"):
                flags.append(f"理由（提议者自述）：{p['rationale']}")
            try:
                rate_text = f"{float(p.get('rate_per_year', 0)):g}"
            except (TypeError, ValueError):
                rate_text = "?"
            rows.append(
                f"<tr><td><b>{_esc(p.get('description', ''))}</b><br><span class=\"ws-muted\">{_esc(p.get('id', ''))}</span></td>"
                f"<td>每年约 {_esc(rate_text)} 次</td>"
                f"<td>{_esc(p.get('severity', ''))}</td><td>{_esc('；'.join(flags))}</td></tr>"
            )
        warn = "".join(_warn(f"先验被忽略：{_esc(why)}") for why in problems)
        table = (
            '<table class="ws-mech-table"><tr><th>事件</th><th>基准频率</th><th>严重度</th><th>标注</th></tr>'
            + "".join(rows) + "</table>"
        ) if rows else ""
        return (
            f'<div class="ws-card"><h2 class="ws-serif">🎲 外生事件先验（{len(priors)} 条）</h2>'
            + _muted("事件由引擎按先验概率抽样、作为“本步已发生的事实”注入；先验数值由用户声明或 AI 提议，引擎不知道任何领域的真实频率。每步抽中的事件见时间线。")
            + table + warn + "</div>"
        )

    return _safe(_build, "外生事件先验")


# ── 因果引擎 + 树接地/树影响 ────────────────────────────────────────


def _status_legend() -> str:
    parts = []
    for key, label in causal_view.STATUS_LABELS.items():
        color = causal_view.STATUS_STYLES[key]["color"]
        parts.append(f'<span style="color:{_esc(color)}">● {_esc(label)}</span>')
    return '<div class="ws-muted ws-mech-legend">' + "".join(parts) + "</div>"


def causal_engine_html(settings: Dict[str, Any], history: List[Any]) -> str:
    """因果引擎：着色关系图 + 兑现统计 + 到期时间线。**仅当因果引擎开启**才渲染（树声明依赖它）。"""
    if not causal_engine.is_enabled(settings):
        return ""

    def _build() -> str:
        step = (int(getattr(history[-1], "step", 0)) + 1) if history else 1
        rows = causal_view.build_edge_view(settings, history)
        tl = causal_view.build_due_timeline(settings, history, step)
        if not rows and not tl["open"] and not tl["recent"]:
            return ""
        out: List[str] = [_muted(_DISCLAIMER_SELF_REPORT)]

        if rows:
            svg = _dot_to_svg(causal_view.edges_to_dot(rows))
            if svg:
                out.append("<h3 class=\"ws-serif\">着色关系图</h3>" + _status_legend()
                           + f'<div class="ws-causal-graph">{svg}</div>')
            trs = "".join(
                f"<tr><td>{_esc(r['edge_id'])}{' 🌳' if r['kind'] == 'tree' else ''}</td>"
                f"<td>{_esc(r['from_name'])} → {_esc(r['to_name'])}</td>"
                f"<td>{_esc(r['status_label'])}</td><td>{_esc(r['rate_text'] or '—')}</td>"
                f"<td>{_esc(r.get('mechanism') or '')}</td></tr>"
                for r in rows
            )
            out.append(
                "<h3 class=\"ws-serif\">边状态与兑现率（AI 自报）</h3>"
                '<table class="ws-mech-table"><tr><th>边</th><th>方向</th><th>状态</th><th>兑现率（兑现/有结论）</th><th>机制</th></tr>'
                + trs + "</table>"
            )

        out.append("<h3 class=\"ws-serif\">⏰ 到期因果时间线</h3>")
        if tl["open"]:
            items = []
            for r in tl["open"]:
                extra = []
                if r["postponed_count"]:
                    extra.append(f"推迟 {r['postponed_count']} 次")
                if r["ignored_count"]:
                    extra.append(f"到期后已 {r['ignored_count']} 步未交代")
                if r["retrigger_count"]:
                    extra.append(f"源头又有进展 {r['retrigger_count']} 次")
                items.append(
                    f"<li>{'⏰' if r['due'] else '⏳'} <code>{_esc(r['pending_id'])}</code> "
                    f"{_esc(r['from_name'])} → {_esc(r['to_name'])} · {_esc(r['reason_label'])}"
                    f"（第 {_esc(r['triggered_at_step'])} 步触发）· {_esc(r['text'])}"
                    + (f" · {_esc('，'.join(extra))}" if extra else "") + "</li>"
                )
            out.append("<p><b>待兑现（已到期的在前）</b></p><ul>" + "".join(items) + "</ul>")
        else:
            out.append(_muted("导出分支末状态没有未结案的待兑现项。"))
        if tl["recent"]:
            icons = {"realized": "✅", "dampened": "🔸", "postponed": "⏸️", "countered": "❌",
                     "expired": "🕳️", "unaddressed": "🕳️"}
            items = "".join(
                f"<li>{icons.get(str(r['disposition']), '•')} 第 {_esc(r['step'])} 步 · "
                f"{_esc(r['from_name'])} → {_esc(r['to_name'])} · {_esc(r['disposition'])}"
                f"{'（自动结案）' if r['auto'] else ''}{(' · ' + _esc(r['reason'])) if r['reason'] else ''}</li>"
                for r in tl["recent"]
            )
            out.append("<p><b>最近的处置记录（AI 自报）</b></p><ul>" + items + "</ul>")

        decl = tree_effects.declared_effects(settings.get("causal_lines")) if tree_effects.is_enabled(settings) else []
        if decl:
            lis = "".join(
                f"<li><code>{_esc(d['line_id'])}/{_esc(d['branch_id'])}</code>（{_esc(d['status'])}）→ "
                f"{_esc(d['effect']['to_line_id'])}"
                f"{(' · ' + _esc(d['effect']['mechanism'])) if d['effect'].get('mechanism') else ''}</li>"
                for d in decl
            )
            out.append(
                "<h3 class=\"ws-serif\">🌳 树分支声明的影响</h3>"
                + _muted("声明由 AI 或用户填写，是假设不是事实；分支新变为 active 时入队，之后与因果边同一条路径交代。")
                + f"<ul>{lis}</ul>"
            )
        return '<div class="ws-card"><h2 class="ws-serif">🔗 因果引擎（导出分支末状态）</h2>' + "".join(out) + "</div>"

    return _safe(_build, "因果引擎")


def tree_grounding_html(settings: Dict[str, Any], history: List[Any]) -> str:
    """树接地的当前建议/约束。**仅当树接地开启且有建议**才渲染（逐步的降级/自动迁移记录在每步提示里）。"""
    if not tree_grounding.is_enabled(settings):
        return ""

    def _build() -> str:
        vars_ = (getattr(history[-1], "vars", None) or {}) if history else {}
        sugs = tree_grounding.pending_suggestions(settings.get("causal_lines"), vars_, settings)
        if not sugs:
            return ""
        auto = tree_grounding.auto_transition_enabled(settings)
        kind_text = {"trigger_met": "建议激活", "trigger_blocked": "触发受阻", "exclusive_loser": "互斥落败",
                     "prereq_unmet": "前置未满足"}
        lis = "".join(
            f"<li>{_esc(kind_text.get(str(s.get('kind')), s.get('kind', '')))} · {_esc(tree_grounding._describe(s))}</li>"
            for s in sugs if isinstance(s, dict)
        )
        return (
            '<div class="ws-card"><h2 class="ws-serif">🌳 因果树接地：当前建议/约束</h2>'
            + _muted("自动迁移已开启，满足条件的建议会被引擎直接执行。" if auto
                     else "自动迁移未开启：以下只是建议，不会改变树状态。")
            + f"<ul>{lis}</ul></div>"
        )

    return _safe(_build, "树接地")


def mechanisms_sections_html(manifest: Any, history: List[Any], branch: str) -> str:
    """所有新机制的文档级区块（体检 / 技术树 / 事件先验 / 因果引擎 / 树接地）。全部无数据时返回空串。"""
    try:
        settings = branch_settings(manifest, history, branch)
    except Exception:  # noqa: BLE001 — 动态状态还原失败：整组区块跳过，不连累导出
        return ""
    return "".join([
        health_html(manifest, history, settings),
        tech_html(settings),
        events_html(settings),
        causal_engine_html(settings, history),
        tree_grounding_html(settings, history),
    ])
