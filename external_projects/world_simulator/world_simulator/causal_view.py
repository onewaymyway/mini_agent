"""world_simulator/causal_view.py — 因果引擎界面收尾 P5e（第二十二轮 WP3 的 3e）。

设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §4 WP3 3e：
因果图按边状态着色（假设 / 已观察 / 已证伪）、显示兑现率、到期因果时间线。

纯 Python、**不调 LLM、不落盘、不写任何状态**——只把 `causal_engine.edge_stats()`（从分支历史推导的
兑现统计）和 `causal_engine.summarize_open()`（未结案待兑现项）整理成界面好直接渲染的结构，
并生成 Graphviz DOT 字符串。放在独立模块里，是为了能脱离 Streamlit 单测（`app.py` 只负责渲染）。

## 边状态（`classify_edge`）——必须先讲清它**是什么、不是什么**

状态完全由**兑现统计**推出，而兑现统计是 **LLM 自报**的（引擎无法验证 `realized` 是否真写进了状态）。
所以下面的"已观察/已证伪"是"**AI 自己多次这么说**"，不是"世界里被验证/被证伪"：

| 状态 | 规则（阈值见 `DEFAULT_VIEW_PARAMS`） |
|---|---|
| `hypothesis` 假设 | 还没有任何有结论的处置（`concluded == 0`）——含"从未触发"和"只入队未到期" |
| `observed` 已观察 | `realized >= observed_min_realized` 且 `realized_rate >= observed_min_rate` |
| `refuted` 已证伪 | `realized == 0` 且 `countered >= refuted_min_countered` |
| `contested` 未定 | 有结论，但既不满足"已观察"也不满足"已证伪"（例如 1 次兑现 + 2 次抵消、只有 1 次抵消、或只有部分兑现） |
| `disabled` 已停用 | 边声明里 `enabled: false`（优先于上面所有状态） |

`contested` 是相对计划原文（三态）的**细化**：只有一条自报抵消就判"已证伪"证据太薄，把混合结果硬塞进
三态里任何一个都会误导，所以单列一档。**只有部分兑现（dampened）不算证伪**——效果发生了，只是比声明的弱。

## 已知边界（如实）

- 状态/兑现率是自报统计的函数，样本很小时（默认 1~2 次）波动大；界面同时显示"n 次有结论"，不单独给色。
- 阈值是通用占位值，不是领域事实；可用 `settings.causal_view_params` 覆盖（非法值忽略）。
- 到期时间线里的"还差多少"是按 `delay_days` / `elapsed_days` 估计的，`elapsed_days` 本身是 LLM 估计值，
  缺数据时退化为按步计数并标注精度（与 `causal_engine._is_due` 同口径）。
- 只覆盖 `declared_causal_graph` 的边和 P5d 的树边（`tree:` 前缀）；不覆盖 `causal_links` 的事后自述聚合
  （那是 `causal_graph.py` 的"跨线影响关系"，两者并存、口径不同）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from world_simulator import causal_engine, tree_effects

STATUS_HYPOTHESIS = "hypothesis"
STATUS_OBSERVED = "observed"
STATUS_REFUTED = "refuted"
STATUS_CONTESTED = "contested"
STATUS_DISABLED = "disabled"

STATUS_LABELS: Dict[str, str] = {
    STATUS_HYPOTHESIS: "假设",
    STATUS_OBSERVED: "已观察",
    STATUS_REFUTED: "已证伪",
    STATUS_CONTESTED: "未定",
    STATUS_DISABLED: "已停用",
}

# 边/字体颜色 + 线型。灰=没证据，绿=AI 多次自报兑现，红=AI 多次自报被抵消，橙=结论不一致。
STATUS_STYLES: Dict[str, Dict[str, str]] = {
    STATUS_HYPOTHESIS: {"color": "#94a3b8", "style": "dashed"},
    STATUS_OBSERVED: {"color": "#16a34a", "style": "solid"},
    STATUS_REFUTED: {"color": "#dc2626", "style": "solid"},
    STATUS_CONTESTED: {"color": "#d97706", "style": "solid"},
    STATUS_DISABLED: {"color": "#cbd5e1", "style": "dotted"},
}

DEFAULT_VIEW_PARAMS: Dict[str, Any] = {
    # 通用占位值，不是领域事实。
    "observed_min_realized": 1,
    "observed_min_rate": 0.5,
    "refuted_min_countered": 2,
    "recent_limit": 20,
}


def get_view_params(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`DEFAULT_VIEW_PARAMS` 叠加 `settings.causal_view_params`（非法值忽略）。"""
    params = dict(DEFAULT_VIEW_PARAMS)
    override = (settings or {}).get("causal_view_params")
    if not isinstance(override, dict):
        return params
    for key, default in DEFAULT_VIEW_PARAMS.items():
        num = causal_engine._num(override.get(key))
        if num is None:
            continue
        if key == "observed_min_rate":
            if 0.0 <= num <= 1.0:
                params[key] = float(num)
        elif num >= 1:
            params[key] = int(num)
    return params


# ── 状态 ────────────────────────────────────────────────────────────


def classify_edge(
    stat: Optional[Dict[str, Any]], *, enabled: bool = True, params: Optional[Dict[str, Any]] = None
) -> str:
    """一条边的状态（规则见模块 docstring）。`stat` 是 `edge_stats()[edge_id]`，没有则视为从未触发。"""
    if not enabled:
        return STATUS_DISABLED
    p = params or DEFAULT_VIEW_PARAMS
    if not stat or int(stat.get("concluded") or 0) <= 0:
        return STATUS_HYPOTHESIS
    realized = int(stat.get("realized") or 0)
    countered = int(stat.get("countered") or 0)
    rate = stat.get("realized_rate")
    if realized == 0 and countered >= p["refuted_min_countered"]:
        return STATUS_REFUTED
    if realized >= p["observed_min_realized"] and rate is not None and rate >= p["observed_min_rate"]:
        return STATUS_OBSERVED
    return STATUS_CONTESTED


def _rate_text(stat: Optional[Dict[str, Any]]) -> str:
    """`"67%（2/3）"`；没有结论时返回空串。"""
    if not stat or not int(stat.get("concluded") or 0):
        return ""
    rate = stat.get("realized_rate")
    pct = f"{rate:.0%}" if rate is not None else "—"
    return f"{pct}（{int(stat.get('realized') or 0)}/{int(stat.get('concluded') or 0)}）"


# ── 边视图 ──────────────────────────────────────────────────────────


def build_edge_view(settings: Optional[Dict[str, Any]], history: List[Any]) -> List[Dict[str, Any]]:
    """所有可画的边（声明的因果边 + 树边）→ 带状态/统计的行。

    行：`{edge_id, kind(declared|tree), from_id, to_id, from_name, to_name, enabled, mechanism, sign,
    stat, status, status_label, rate_text}`。声明的边按声明顺序在前；树边（声明的 + 历史里出现过但声明已被删掉的）
    在后。出错/没有数据时返回空列表（纯展示，不抛）。"""
    params = get_view_params(settings)
    stats = causal_engine.edge_stats(history)
    rows: List[Dict[str, Any]] = []
    seen: set = set()

    def _add(edge_id: str, kind: str, from_id: str, to_id: str, from_name: str, to_name: str,
             enabled: bool, mechanism: str, sign: Any) -> None:
        if edge_id in seen:
            return
        seen.add(edge_id)
        stat = stats.get(edge_id)
        status = classify_edge(stat, enabled=enabled, params=params)
        rows.append({
            "edge_id": edge_id, "kind": kind, "from_id": from_id, "to_id": to_id,
            "from_name": from_name, "to_name": to_name, "enabled": enabled,
            "mechanism": mechanism, "sign": sign, "stat": stat,
            "status": status, "status_label": STATUS_LABELS[status], "rate_text": _rate_text(stat),
        })

    edges, _problems = causal_engine.get_edges(settings)
    for e in edges:
        _add(e["id"], "declared", e["from_line_id"], e["to_line_id"],
             causal_engine._display_name(settings, e["from_line_id"]),
             causal_engine._display_name(settings, e["to_line_id"]),
             bool(e["enabled"]), e["mechanism"] or e["note"], e["sign"])

    for d in tree_effects.declared_effects((settings or {}).get("causal_lines")):
        eff = d["effect"]
        eid = tree_effects.edge_id_for(d["line_id"], d["branch_id"], eff["to_line_id"])
        _add(eid, "tree", f"{d['line_id']}/{d['branch_id']}", eff["to_line_id"],
             f"{causal_engine._display_name(settings, d['line_id'])}/{d['branch_id']}",
             causal_engine._display_name(settings, eff["to_line_id"]),
             True, eff["mechanism"] or eff["note"], eff["sign"])

    for eid in stats:  # 历史里出现过、但声明已被删掉的树边：仍然画出来，免得兑现记录凭空消失
        if tree_effects.is_tree_edge(eid) and eid not in seen:
            body = eid[len(tree_effects.TREE_EDGE_PREFIX):]
            src, _, dst = body.partition("->")
            _add(eid, "tree", src, dst, src, causal_engine._display_name(settings, dst), True, "", None)
    return rows


# ── DOT ─────────────────────────────────────────────────────────────


def _esc(text: Any) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def edges_to_dot(rows: List[Dict[str, Any]]) -> str:
    """`build_edge_view()` 的结果 → Graphviz DOT（按状态着色，边标签带兑现率）。

    节点用 id 区分（显示名可能重名），标签显示可读名；边标签 = `状态` 或 `状态 · 兑现率（兑现/有结论）`。
    树边的起点是 `线/分支`，画成椭圆以便和因果线节点（圆角方框）区分。空输入返回空图。"""
    lines: List[str] = [
        "digraph G {",
        "  rankdir=LR;",
        '  node [shape=box, style="rounded,filled", fillcolor="#eef2ff", fontname="sans-serif"];',
        '  edge [fontname="sans-serif", fontsize=10];',
    ]
    nodes: Dict[str, Dict[str, str]] = {}
    for r in rows:
        nodes.setdefault(r["from_id"], {"name": r["from_name"], "tree": r["kind"] == "tree"})
        nodes.setdefault(r["to_id"], {"name": r["to_name"], "tree": False})
    for nid in sorted(nodes):
        n = nodes[nid]
        attrs = [f'label="{_esc(n["name"])}"']
        if n["tree"]:
            attrs.append('shape=ellipse, fillcolor="#f0fdf4"')
        lines.append(f'  "{_esc(nid)}" [{", ".join(attrs)}];')
    for r in rows:
        style = STATUS_STYLES[r["status"]]
        label = r["status_label"] + (f" · {r['rate_text']}" if r["rate_text"] else "")
        attrs = [
            f'label="{_esc(label)}"', f'color="{style["color"]}"', f'fontcolor="{style["color"]}"',
            f'style="{style["style"]}"',
        ]
        if r["status"] == STATUS_OBSERVED:
            attrs.append("penwidth=2")
        lines.append(f'  "{_esc(r["from_id"])}" -> "{_esc(r["to_id"])}" [{", ".join(attrs)}];')
    lines.append("}")
    return "\n".join(lines)


# ── 到期因果时间线 ───────────────────────────────────────────────────


def _remaining(entry: Dict[str, Any], history: List[Any], step: int) -> Dict[str, Any]:
    """一个未结案项距到期还差多少。`step` 是正在生成的那一步（同 `causal_engine._is_due`）。

    返回 `{due, precision(days|steps), remaining(数值,已到期为 0), text}`。"""
    due, precision, days = causal_engine._is_due(entry, history, step)
    if due:
        return {"due": True, "precision": precision, "remaining": 0, "text": "已到期，下一步会提醒"}
    if precision == "days":
        left = max(0.0, float(causal_engine._num(entry.get("delay_days")) or 0.0) - float(days or 0.0))
        return {"due": False, "precision": "days", "remaining": left, "text": f"还差约 {left:g} 天"}
    triggered = int(entry.get("triggered_at_step", 0))
    steps_since = max(0, (step - 1) - triggered)
    left_steps = max(0, int(causal_engine._num(entry.get("delay_steps")) or 0) - steps_since)
    return {"due": False, "precision": "steps", "remaining": left_steps, "text": f"还差 {left_steps} 步（按步计，精度较低）"}


def build_due_timeline(
    settings: Optional[Dict[str, Any]], history: List[Any], step: int
) -> Dict[str, List[Dict[str, Any]]]:
    """到期因果时间线：`{"open": [...], "recent": [...]}`。

    - `open`：当前未结案项，**已到期的在前**（忽略次数多的更靠前），其余按"还差多少"升序（天/步分开排，天在前）。
      行：`{pending_id, edge_id, from_name, to_name, reason_label, triggered_at_step, due, precision, remaining,
      text, postponed_count, ignored_count, retrigger_count, source}`。
    - `recent`：最近已处置的记录（来自历史里的 `effect_dispositions`，新的在前，条数见 `recent_limit`）。
      行：`{step, edge_id, from_name, to_name, disposition, reason, closed, auto}`。
    因果引擎没开时 `open` 为空（`summarize_open` 的口径）；`recent` 只看历史，开不开都能给。"""
    params = get_view_params(settings)
    open_rows: List[Dict[str, Any]] = []
    for p in causal_engine.summarize_open(settings, history, step):
        rem = _remaining(p, history, step)
        open_rows.append({
            "pending_id": p.get("pending_id"), "edge_id": p.get("edge_id"),
            "from_name": _node_name(settings, p, "from_line_id"),
            "to_name": causal_engine._display_name(settings, str(p.get("to_line_id") or "")),
            "reason_label": causal_engine._reason_label(p.get("trigger_reason")),
            "triggered_at_step": p.get("triggered_at_step"), "due": rem["due"],
            "precision": rem["precision"], "remaining": rem["remaining"], "text": rem["text"],
            "postponed_count": int(p.get("postponed_count", 0)),
            "ignored_count": int(p.get("ignored_count", 0)),
            "retrigger_count": int(p.get("retrigger_count", 0)),
            "source": "tree" if tree_effects.is_tree_edge(p.get("edge_id")) else "edge",
        })
    open_rows.sort(key=lambda r: (
        0 if r["due"] else 1,
        -r["ignored_count"] if r["due"] else 0,
        0 if r["precision"] == "days" else 1,
        r["remaining"],
        str(r["pending_id"]),
    ))

    recent: List[Dict[str, Any]] = []
    for state in reversed(list(history or [])):
        for d in reversed(list(getattr(state, "effect_dispositions", None) or [])):
            if not isinstance(d, dict):
                continue
            recent.append({
                "step": int(getattr(state, "step", 0) or 0), "edge_id": d.get("edge_id"),
                "from_name": _node_name(settings, d, "from_line_id"),
                "to_name": causal_engine._display_name(settings, str(d.get("to_line_id") or "")),
                "disposition": d.get("disposition"), "reason": d.get("reason") or "",
                "closed": bool(d.get("closed")), "auto": bool(d.get("auto")),
            })
            if len(recent) >= params["recent_limit"]:
                return {"open": open_rows, "recent": recent}
    return {"open": open_rows, "recent": recent}


def _node_name(settings: Optional[Dict[str, Any]], row: Dict[str, Any], key: str) -> str:
    """待兑现项/处置记录的起点显示名。树边的起点是 `线/分支`：待兑现项的 `from_line_id` 只存线 id，
    所以从 `edge_id`（`tree:线/分支->目标`）里解析出分支，只翻译线的部分。"""
    edge_id = row.get("edge_id")
    if tree_effects.is_tree_edge(edge_id):
        src = str(edge_id)[len(tree_effects.TREE_EDGE_PREFIX):].partition("->")[0]
        line, _, branch = src.partition("/")
        if branch:
            return f"{causal_engine._display_name(settings, line)}/{branch}"
    return causal_engine._display_name(settings, str(row.get(key) or ""))
