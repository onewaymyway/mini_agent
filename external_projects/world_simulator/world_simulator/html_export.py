"""world_simulator/html_export.py — 把一次模拟导出成自包含的静态 HTML
网页（第十三轮，`next_doc/world_simulator_thirteenth_round_html_
export_plan.md`）。

对外只暴露一个函数 `export_simulation_html()`：给定 `sim_id`/
`branch`，读取已落盘的 manifest/history/复盘记录，拼出一份不依赖
任何外部资源（CDN/字体/JS 运行时）的完整 HTML 字符串。调用方
（`app.py`）自己决定怎么处理返回值——本项目里是塞进
`st.download_button()` 触发浏览器下载，本模块不写任何文件、不产生
任何副作用。

设计取舍（详见规划文档第 1、2、4 节）：
- 纯函数，无新增持久化结构，和 `causal_graph.py`/`achievements.py`
  "纯函数计算"的既有风格一致。
- 不 import `app.py`——`app.py` 是一个 Streamlit 脚本，顶层可能有
  执行期副作用（比如 `st.set_page_config()`），这个模块需要能在
  没有 Streamlit 运行时的环境里单独单测，所以格式化逻辑（图标映射、
  DOT 拼接）在这里独立实现一份，不共享 `app.py` 对应函数的代码——
  两者服务"持续操作的看板"和"一次性通读的静态报告"两种不同场景，
  本来就不要求布局代码复用（详见规划文档第 1 节）。
- 时间线按 `step` **正序**渲染，区别于 `app.py` 详情页现有的倒序
  展示——导出的网页是一次性通读的报告，正序更符合阅读因果顺序的
  直觉。
- 因果线图优先渲染成内嵌 SVG；`graphviz` 包不可用、或调用 `dot`
  二进制失败时，降级为 `causal_graph.format_edges_for_display()`
  的纯文字列表，不让整个导出因为这一步失败而失败（详见规划文档
  第 4 节）。
"""

from __future__ import annotations

import html as html_stdlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from world_simulator import branch_manager as bm
from world_simulator import causal_graph as cg_mod
from world_simulator import causal_tree as causal_tree_mod
from world_simulator import hypothesis as hyp_mod
from world_simulator import retrospective as retrospective_mod
from world_simulator.state_model import SimManifest, SimState
from world_simulator.store import SimStore

# ── 展示层映射表（独立于 app.py 的同名字典，见模块 docstring）───────

_CAPABILITY_KIND_LABELS = {
    "technology": "技术",
    "organization": "组织",
    "institution": "制度",
}
_CAPABILITY_KIND_ICONS = {
    "technology": "🔧",
    "organization": "🏢",
    "institution": "📜",
}
_MATURITY_STAGE_LABELS = {
    "lab": "实验室可行",
    "expert": "专家可用",
    "developer": "开发者可用",
    "consumer": "普通用户可用",
    "cheap_at_scale": "成本足够低/规模化",
    "infrastructure": "基础设施化/社会常态化",
}
_CHOSEN_BY_LABELS = {"user": "用户选择", "autopilot": "自动挡代选"}

# 第十四轮（`next_doc/world_simulator_fourteenth_round_causal_
# overview_export_plan.md`）：因果线「按线」总览新增的展示映射表，
# 直接对齐 `app.py::_render_causal_lines_overview()` 同名字典，
# 保证导出网页和详情页对同一份数据的措辞完全一致。
_TREND_LABELS = {
    "accelerating": "📈 加速",
    "steady": "➡️ 匀速延续",
    "decelerating": "📉 放缓",
    "reversing": "🔄 已反转",
}
_TREE_STATUS_ICONS = {
    "dormant": "○",
    "emerging": "🌱",
    "active": "◐",
    "resolved": "●",
    "expired": "⌛",
    "invalidated": "✕",
}
_TREE_STATUS_LABELS = {
    "dormant": "潜伏",
    "emerging": "形成中",
    "active": "已激活",
    "resolved": "已解决",
    "expired": "已错过",
    "invalidated": "已失效",
}
_LIKELIHOOD_LABELS = {"high": "可能性高", "medium": "可能性中", "low": "可能性低"}
_FUTURE_CONFIDENCE_LABELS = {
    "low": "低置信度·分叉可能大",
    "medium": "中等置信度",
    "high": "高置信度",
    "unknown": "置信度未知",
}
_TREE_LIFECYCLE_GROUPS = [
    ("🌱 仍在演化中（潜在 / 活跃）", ("dormant", "emerging", "active")),
    ("● 已发生（已确认/已解决）", ("resolved",)),
    ("✕ 已排除（错过窗口/已失效）", ("expired", "invalidated")),
]


def _esc(value: Any) -> str:
    """统一的 HTML 转义入口——所有拼进模板的自由文本都必须走这里，
    避免用户输入的 `intent`/`narrative` 等字段里出现 `<`/`>`/`&` 破坏
    页面结构（`state_model.py` 里这些字段都是自由文本，不做任何
    HTML 安全性保证，安全性完全是展示层的责任）。
    """
    return html_stdlib.escape(str(value if value is not None else ""), quote=True)


def _json_block(data: Any) -> str:
    """把任意 JSON 兼容对象格式化成一个只读的 `<pre>` 代码块。"""
    if not data:
        return '<p class="ws-muted">（无）</p>'
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)
    return f'<pre class="ws-json-block">{_esc(text)}</pre>'


# ── 因果线图：DOT 拼接 + SVG/文字降级 ────────────────────────────────


def _edges_to_dot(edges: Sequence[Any]) -> str:
    """把 `causal_graph.build_causal_graph()` 的边集合转换成 Graphviz
    DOT 字符串。纯字符串拼接，逻辑对齐 `app.py::
    _causal_graph_edges_to_dot()`，但独立实现（见模块 docstring）。
    """
    lines: List[str] = [
        "digraph G {",
        "  rankdir=LR;",
        '  bgcolor="transparent";',
        '  node [shape=box, style="rounded,filled", fillcolor="#eef2ff", '
        'fontname="sans-serif", fontcolor="#12111c"];',
        '  edge [fontname="sans-serif", fontsize=10, color="#8c7ae6", '
        'fontcolor="#9b96b3"];',
    ]

    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')

    nodes = set()
    for edge in edges:
        nodes.add(edge.source_line)
        nodes.add(edge.target_line)
    for node in sorted(nodes):
        lines.append(f'  "{esc(node)}";')

    for edge in edges:
        label_parts = [
            f"{count}次{cg_mod.relation_type_label(rt)}"
            for rt, count in sorted(edge.relation_counts.items(), key=lambda kv: -kv[1])
        ]
        label = "，".join(label_parts)
        attrs = [f'label="{esc(label)}"']
        if getattr(edge, "has_delay", False):
            attrs.append('style="dashed"')
        lines.append(
            f'  "{esc(edge.source_line)}" -> "{esc(edge.target_line)}" '
            f'[{", ".join(attrs)}];'
        )

    lines.append("}")
    return "\n".join(lines)


def _render_causal_graph_visual(edges: Sequence[Any]) -> str:
    """把因果线边集合渲染成内嵌 SVG；渲染失败时降级为文字列表（见
    模块 docstring"设计取舍"第 4 条）。`edges` 为空时直接返回引导
    文案，不进入渲染流程。
    """
    if not edges:
        return '<p class="ws-muted">这次模拟没有记录跨线影响关系。</p>'

    try:
        dot = _edges_to_dot(edges)
        import graphviz  # 延迟导入：这是可选依赖，未安装不应该影响
        # 导出功能的其余部分可用。

        svg_bytes = graphviz.Source(dot).pipe(format="svg")
        svg_text = svg_bytes.decode("utf-8")
        # 去掉 XML 声明/DOCTYPE 头，避免和外层 HTML 文档的 <head> 冲突，
        # 只保留从 <svg 开始的部分。
        marker = "<svg"
        if marker in svg_text:
            svg_text = svg_text[svg_text.index(marker):]
        return f'<div class="ws-causal-graph">{svg_text}</div>'
    except Exception:  # noqa: BLE001 — 渲染失败的原因很多（未装
        # graphviz 包/系统没有 dot 二进制/DOT 语法问题），降级路径
        # 不需要区分具体原因，统一退化到文字列表。
        lines = cg_mod.format_edges_for_display(edges)
        items = "".join(f"<li>{_esc(line)}</li>" for line in lines)
        return (
            '<p class="ws-muted">因果线图形化渲染不可用（当前环境缺少 '
            "Graphviz），以下是文字版摘要：</p>"
            f"<ul>{items}</ul>"
        )


# ── 能力/理想状态等小片段的格式化 ────────────────────────────────────


def _capability_item_html(item: Dict[str, Any]) -> str:
    capability = str(item.get("capability") or "").strip()
    if not capability:
        return ""
    kind = str(item.get("capability_kind") or "").strip() or "technology"
    kind_label = _CAPABILITY_KIND_LABELS.get(kind, kind)
    maturity_stage = str(item.get("maturity_stage") or "").strip()
    bracket_parts = []
    if maturity_stage:
        bracket_parts.append(_MATURITY_STAGE_LABELS.get(maturity_stage, maturity_stage))
    bracket_parts.append(kind_label)
    suffix = " · ".join(bracket_parts)
    first_occurrence = bool(item.get("first_occurrence"))
    icon = "⭐" if first_occurrence else _CAPABILITY_KIND_ICONS.get(kind, "🔧")
    first_note = "（首次达成）" if first_occurrence else ""

    detail_lines = []
    enables = [str(e) for e in (item.get("enables") or []) if str(e).strip()]
    if enables:
        detail_lines.append(f"<div>让这些事变得可能：{_esc('、'.join(enables))}</div>")
    limitations = [str(x) for x in (item.get("limitations") or []) if str(x).strip()]
    if limitations:
        detail_lines.append(f"<div>目前的局限：{_esc('、'.join(limitations))}</div>")
    behavior_change = str(item.get("behavior_change") or "").strip()
    if behavior_change:
        detail_lines.append(f"<div>行为变化：{_esc(behavior_change)}</div>")
    structural_impact = str(item.get("structural_impact") or "").strip()
    if structural_impact:
        detail_lines.append(f"<div>结构性影响：{_esc(structural_impact)}</div>")

    return (
        '<div class="ws-capability-item">'
        f"{icon} {_esc(capability)}{first_note} "
        f'<span class="ws-muted">[{_esc(suffix)}]</span>'
        f'<div class="ws-muted ws-capability-detail">{"".join(detail_lines)}</div>'
        "</div>"
    )


def _desired_state_block_html(block: Dict[str, Any]) -> str:
    parts = []
    for key, label in (
        ("conditions", "达成条件"),
        ("constraints", "约束"),
        ("assumptions", "假设"),
    ):
        values = [str(v) for v in (block.get(key) or []) if str(v).strip()]
        if not values:
            continue
        items = "".join(f"<li>{_esc(v)}</li>" for v in values)
        parts.append(f"<div><b>{label}</b><ul>{items}</ul></div>")
    return "".join(parts) or '<p class="ws-muted">未声明。</p>'


# ── 各章节渲染 ───────────────────────────────────────────────────────


def _render_header(
    manifest: SimManifest, branch: str, branches_detailed: List[Dict[str, Any]]
) -> str:
    status_label = {"active": "进行中", "paused": "已暂停", "ended": "已结束"}.get(
        manifest.status, manifest.status
    )
    origin_note = ""
    for info in branches_detailed:
        if info.get("branch") == branch and info.get("source_branch"):
            origin_note = (
                f'<div class="ws-muted">从 {_esc(info["source_branch"])} 分支'
                f'第 {_esc(info.get("from_step"))} 步分叉而来</div>'
            )
            break
    exported_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    return f"""
    <div class="ws-card">
      <h1 class="ws-serif">{_esc(manifest.title or manifest.sim_id)}</h1>
      <div class="ws-muted">实例 ID：{_esc(manifest.sim_id)} · 分支：{_esc(branch)}
        · 模板：{_esc(manifest.template)} · 状态：{_esc(status_label)}</div>
      {origin_note}
      <div class="ws-muted">导出时间：{_esc(exported_at)}</div>
    </div>
    """


def _render_input_and_background(manifest: SimManifest, state0: Optional[SimState]) -> str:
    settings = manifest.settings or {}
    desired_state = settings.get("desired_state") or {}
    per_entity = desired_state.get("per_entity") or {}

    desired_state_html = _desired_state_block_html(desired_state)
    per_entity_html = ""
    if isinstance(per_entity, dict) and per_entity:
        blocks = "".join(
            f"<div class='ws-card' style='margin-top:0.5rem;'>"
            f"<b>{_esc(name)}</b>{_desired_state_block_html(block or {})}</div>"
            for name, block in per_entity.items()
            if isinstance(block, dict)
        )
        per_entity_html = f'<div><b>各主体各自的理想状态</b>{blocks}</div>'

    objectives = settings.get("objectives") or []
    objectives_items = "".join(
        f"<li>{_esc(o if isinstance(o, str) else o.get('label', ''))}</li>"
        for o in objectives
    )
    resource_fields = settings.get("resource_fields") or []
    resource_items = "".join(
        f"<li>{_esc(f if isinstance(f, str) else f.get('field', ''))}</li>"
        for f in resource_fields
    )
    granularity_mode = settings.get("time_granularity_mode") or "fixed"

    initial_vars_html = _json_block(state0.vars if state0 else {})

    return f"""
    <div class="ws-card">
      <h2 class="ws-serif">模拟输入 / 背景</h2>
      <p><b>原始意图</b>：{_esc(manifest.intent)}</p>
      <p class="ws-muted">创建时间：{_esc(manifest.created_at)}</p>

      <h3 class="ws-serif">初始状态</h3>
      {initial_vars_html}

      <h3 class="ws-serif">理想状态</h3>
      {desired_state_html}
      {per_entity_html}

      <h3 class="ws-serif">其它设置</h3>
      <div class="ws-muted">时间粒度模式：{_esc(granularity_mode)}</div>
      {"<div><b>关注指标</b><ul>" + objectives_items + "</ul></div>" if objectives_items else ""}
      {"<div><b>声明的资源字段</b><ul>" + resource_items + "</ul></div>" if resource_items else ""}
    </div>
    """


def _future_tree_branches_html(line_meta: Optional[Dict[str, Any]]) -> str:
    """渲染一条因果线的"未来因果树"分支列表（按生命周期分组，只读），
    对齐 `app.py::_render_causal_lines_overview()` 里同一段渲染逻辑，
    去掉「标为已解决/已失效」这两个只在可交互详情页才有意义的按钮
    （见模块 docstring：导出网页是纯只读存档）。
    """
    future_tree = (line_meta or {}).get("future_tree") if line_meta else None
    branches = (
        [b for b in (future_tree or {}).get("branches", []) if isinstance(b, dict)]
        if isinstance(future_tree, dict)
        else []
    )
    if not branches:
        return '<p class="ws-causal-line-empty">这条线目前还没有未来树数据（旧实例可能缺失）。</p>'

    status_to_group = {
        status: name for name, statuses in _TREE_LIFECYCLE_GROUPS for status in statuses
    }
    grouped: Dict[str, List[Dict[str, Any]]] = {name: [] for name, _ in _TREE_LIFECYCLE_GROUPS}
    for branch in branches:
        b_status = causal_tree_mod.canonical_status(branch.get("status"))
        grouped[status_to_group.get(b_status, _TREE_LIFECYCLE_GROUPS[0][0])].append(branch)

    parts: List[str] = []
    for group_name, _statuses in _TREE_LIFECYCLE_GROUPS:
        group_branches = grouped[group_name]
        if not group_branches:
            continue
        rows = []
        for branch in group_branches:
            b_status = causal_tree_mod.canonical_status(branch.get("status"))
            b_desc = str(branch.get("description") or "")
            b_like = str(branch.get("likelihood") or "medium")
            icon = _TREE_STATUS_ICONS.get(b_status, "○")
            rows.append(
                f"<li>{icon} <b>{_esc(_TREE_STATUS_LABELS.get(b_status, b_status))}</b>"
                f"（{_esc(_LIKELIHOOD_LABELS.get(b_like, b_like))}）— {_esc(b_desc)}</li>"
            )
        parts.append(
            f'<div class="ws-muted" style="margin-top:0.3rem;font-size:0.85rem;">'
            f"{_esc(group_name)}（{len(group_branches)}）</div>"
            f'<ul>{"".join(rows)}</ul>'
        )
    return "".join(parts)


def _line_futures_html(futures: List[Dict[str, Any]]) -> str:
    """渲染一条因果线的"简单历史外推"（`hypothesis.project_line_
    futures()` 的结果），对齐 `app.py` 同一段。没有外推数据时返回
    空字符串——不留一个空标题。
    """
    if not futures:
        return ""
    rows = []
    for fut in futures:
        dim = str(fut.get("dimension") or "")
        scale = str(fut.get("time_scale") or "")
        desc = str(fut.get("description") or "")
        conf = str(fut.get("confidence") or "unknown")
        conf_label = _FUTURE_CONFIDENCE_LABELS.get(conf, conf)
        badge_class = conf if conf in ("low", "medium", "high") else "medium"
        rows.append(
            '<div class="ws-uncertain-field">'
            f'<span class="ws-uncertain-badge ws-uncertain-badge-{badge_class}">'
            f"{_esc(conf_label)}</span>"
            f"<b>{_esc(dim)}</b>（时间尺度：{_esc(scale)}）— {_esc(desc)}</div>"
        )
    return (
        '<div class="ws-muted" style="margin-top:0.3rem;">🔮 简单历史外推'
        "（基于最近因果链的单路径推演，仅作补充参考，完整的分叉可能见"
        "上方\u300c未来因果树\u300d）：</div>" + "".join(rows)
    )


def _render_causal_line_row(
    line_id: str,
    label: str,
    granularity: str,
    cadence_n: Optional[int],
    independent_note: str,
    history: List[SimState],
    line_meta: Optional[Dict[str, Any]],
    futures: List[Dict[str, Any]],
) -> str:
    """渲染单条因果线的完整卡片：时间点序列（时间正序）+ 关联因果链
    + 未来因果树 + 历史外推，对齐 `app.py::_render_causal_lines_
    overview()` 逐条线渲染的内容，去掉「提修改意见」等只在可交互
    详情页才有意义的表单（见模块 docstring）。

    `history` 需要按 step 升序传入，这样时间点序列才是"从早到晚"，
    符合"走势"的直觉，同 app.py 对这个函数的同一要求。
    """
    points: List[tuple] = []
    last_trend: Optional[str] = None
    for state in history:
        line_updates = getattr(state, "line_updates", None) or {}
        update = line_updates.get(line_id)
        if not isinstance(update, dict) or update.get("advanced") is False:
            continue
        time_label = str(update.get("time_label") or "").strip()
        summary = str(update.get("summary") or "").strip()
        points.append((state.step, time_label, summary))
        trend = str(update.get("trend") or "").strip().lower()
        if trend in _TREND_LABELS:
            last_trend = trend

    suffix_parts = [
        p for p in (
            granularity,
            (f"约每 {cadence_n} 步一动" if cadence_n else ""),
            independent_note,
        ) if p
    ]
    granularity_suffix = f"（{'，'.join(suffix_parts)}）" if suffix_parts else ""
    trend_badge = (
        f' <span class="ws-uncertain-badge">{_esc(_TREND_LABELS[last_trend])}</span>'
        if last_trend else ""
    )

    parts = [
        '<div class="ws-causal-line-row">'
        f'<div class="ws-causal-line-row-title">📈 {_esc(label)}{granularity_suffix} '
        f'<span class="ws-causal-line-row-id">{_esc(line_id)}</span>{trend_badge}</div>'
    ]

    if not points:
        parts.append('<div class="ws-causal-line-empty">这条线目前还没有推进记录。</div>')
    else:
        track_items = []
        for idx, (step, time_label, summary) in enumerate(points):
            if idx > 0:
                track_items.append('<span class="ws-causal-line-point-arrow">→</span>')
            time_text = time_label or f"第 {step} 步"
            track_items.append(
                '<span class="ws-causal-line-point">'
                f'<span class="ws-causal-line-point-time">{_esc(time_text)}</span>'
                f"{_esc(summary)}</span>"
            )
        parts.append(f'<div class="ws-causal-line-track">{"".join(track_items)}</div>')

    related_links = [
        (state.step, link)
        for state in history
        for link in (getattr(state, "causal_links", None) or [])
        if isinstance(link, dict) and str(link.get("line_id") or "") == line_id
    ]
    if related_links:
        rows = []
        for step, link in related_links:
            driver = str(link.get("driver") or "").strip()
            effect = str(link.get("effect") or "").strip()
            affected = link.get("affected_fields") or []
            affected_text = "、".join(str(x) for x in affected) if affected else ""
            detail = " → ".join(x for x in (driver, effect) if x)
            suffix = f"（影响：{affected_text}）" if affected_text else ""
            rows.append(f"<li>第 {step} 步 · {_esc(detail)}{_esc(suffix)}</li>")
        parts.append(
            f'<div class="ws-muted" style="margin-top:0.4rem;">关联因果链'
            f'（{len(related_links)} 条）</div><ul>{"".join(rows)}</ul>'
        )

    parts.append(
        '<div class="ws-muted" style="margin-top:0.4rem;">🌳 未来因果树'
        "（从当前节点出发的若干可能分支，不是只有一条路）：</div>"
    )
    parts.append(_future_tree_branches_html(line_meta))
    parts.append(_line_futures_html(futures))
    parts.append("</div>")
    return "".join(parts)


def _render_causal_lines_breakdown(manifest: SimManifest, history: List[SimState]) -> str:
    """按因果线聚合渲染每条线的完整卡片（第十四轮新增，`next_doc/
    world_simulator_fourteenth_round_causal_overview_export_plan.md`），
    对齐 `app.py::_render_causal_lines_overview()`：声明列表里没有的
    line_id，只要在历史里真的出现过（`line_updates`/`causal_links.
    line_id`）也纳入展示，label 退化为 id 本身——因果线不需要提前
    声明这个既有约束在导出报告里同样成立。

    `history` 要求按 step 升序传入（`export_simulation_html()` 直接
    传 `store.load_history()` 的结果，等价于升序，见 `_render_
    timeline()` 同一处注释）。
    """
    causal_lines_meta = list((manifest.settings or {}).get("causal_lines") or [])

    label_by_id: Dict[str, str] = {
        str(line.get("id")): str(line.get("label") or line.get("id"))
        for line in causal_lines_meta
        if isinstance(line, dict) and line.get("id")
    }
    granularity_by_id: Dict[str, str] = {
        str(line.get("id")): str(line.get("time_granularity") or "")
        for line in causal_lines_meta
        if isinstance(line, dict) and line.get("id")
    }
    cadence_by_id: Dict[str, int] = {}
    independent_by_id: Dict[str, str] = {}
    for line in causal_lines_meta:
        if not (isinstance(line, dict) and line.get("id")):
            continue
        line_id = str(line.get("id"))
        try:
            n = int(line.get("advance_every_n_steps") or 1)
        except (TypeError, ValueError):
            n = 1
        if n > 1:
            cadence_by_id[line_id] = n
        owned = line.get("owned_vars")
        if isinstance(owned, list) and owned:
            try:
                local_step = int(line.get("local_step") or 0)
            except (TypeError, ValueError):
                local_step = 0
            independent_by_id[line_id] = f"独立推进 · 本线第 {local_step} 步"

    # 退化路径：声明列表里没有的 id，只要在历史里真的出现过也纳入展示。
    for state in history:
        for lid in (getattr(state, "line_updates", None) or {}).keys():
            lid = str(lid).strip()
            if lid and lid not in label_by_id:
                label_by_id[lid] = lid
                granularity_by_id.setdefault(lid, "")
        for link in (getattr(state, "causal_links", None) or []):
            if isinstance(link, dict):
                lid = str(link.get("line_id") or "").strip()
                if lid and lid not in label_by_id:
                    label_by_id[lid] = lid
                    granularity_by_id.setdefault(lid, "")

    if not label_by_id:
        return (
            '<p class="ws-muted">因果线是模拟的默认基础机制，这次模拟还没有任何'
            "因果线推进记录。</p>"
        )

    futures_by_id: Dict[str, List[Dict[str, Any]]] = {}
    try:
        futures_by_id = {
            item["line_id"]: item["futures"]
            for item in hyp_mod.project_line_futures(manifest, history)
        }
    except Exception:  # noqa: BLE001 — 展望是辅助信息，算失败不影响总览本身。
        futures_by_id = {}

    rows_html = []
    for line_id, label in label_by_id.items():
        line_meta = next(
            (
                line for line in causal_lines_meta
                if isinstance(line, dict) and str(line.get("id")) == line_id
            ),
            None,
        )
        rows_html.append(
            _render_causal_line_row(
                line_id,
                label,
                granularity_by_id.get(line_id, ""),
                cadence_by_id.get(line_id),
                independent_by_id.get(line_id, ""),
                history,
                line_meta,
                futures_by_id.get(line_id) or [],
            )
        )
    return "".join(rows_html)


def _render_causal_overview(manifest: SimManifest, history: List[SimState]) -> str:
    """渲染"因果线总览"整节：声明的因果线列表 → 按线聚合的时间点
    序列/未来因果树/历史外推（第十四轮新增，见 `_render_causal_
    lines_breakdown()`）→ 线到线影响关系图。三段都按时间正序组织，
    和 `_render_timeline()` 各自独立成节、互不混排（见模块 docstring
    "设计取舍"新增第 6 条）。
    """
    causal_lines = (manifest.settings or {}).get("causal_lines") or []
    lines_items = "".join(
        f"<li><b>{_esc(l.get('id', ''))}</b>"
        + (f"：{_esc(l.get('description', ''))}" if isinstance(l, dict) and l.get("description") else "")
        + "</li>"
        for l in causal_lines
        if isinstance(l, dict)
    )
    lines_html = (
        f"<ul>{lines_items}</ul>" if lines_items else '<p class="ws-muted">未声明因果线。</p>'
    )

    breakdown_html = _render_causal_lines_breakdown(manifest, history)

    edges = cg_mod.build_causal_graph(history)
    graph_html = _render_causal_graph_visual(edges)

    return f"""
    <div class="ws-card">
      <h2 class="ws-serif">因果线总览（正序）</h2>
      <h3 class="ws-serif">声明的因果线</h3>
      {lines_html}
      <h3 class="ws-serif">各因果线的时间点序列与未来因果树</h3>
      {breakdown_html}
      <h3 class="ws-serif">线到线影响关系</h3>
      {graph_html}
    </div>
    """


def _option_text_by_id(state: SimState, option_id: Optional[str]) -> Optional[str]:
    if not option_id:
        return None
    for opt in state.options or []:
        if getattr(opt, "id", None) == option_id:
            label = getattr(opt, "label", "") or ""
            description = getattr(opt, "description", "") or ""
            return f"{label}" + (f"：{description}" if description else "")
    return None


def _render_state_card(prev: Optional[SimState], state: SimState) -> str:
    time_label = state.time_label or f"第 {state.step} 步"
    granularity_note = ""
    if state.time_granularity:
        granularity_note = f'<span class="ws-muted">（{_esc(state.time_granularity)}）</span>'
        if state.granularity_changed and state.granularity_reason:
            granularity_note += (
                f'<div class="ws-muted">节奏切换：{_esc(state.granularity_reason)}</div>'
            )

    choice_html = ""
    if prev is not None and prev.chosen_option_id:
        option_text = _option_text_by_id(prev, prev.chosen_option_id)
        by_label = _CHOSEN_BY_LABELS.get(prev.chosen_by or "", prev.chosen_by or "")
        reason = f"（{_esc(prev.chosen_reason)}）" if prev.chosen_reason else ""
        if option_text:
            choice_html = (
                f'<div class="ws-chapter-choice">→ 选择了「{_esc(option_text)}」'
                f"，由{_esc(by_label)}{reason}</div>"
            )

    key_drivers = [str(d) for d in (state.key_drivers or []) if str(d).strip()]
    key_drivers_html = ""
    if key_drivers:
        items = "".join(f"<li>{_esc(d)}</li>" for d in key_drivers)
        key_drivers_html = f'<div><b>关键驱动因素</b><ul>{items}</ul></div>'

    causal_links_html = ""
    if state.causal_links:
        rows = []
        for link in state.causal_links:
            if not isinstance(link, dict):
                continue
            driver = str(link.get("driver") or "").strip()
            effect = str(link.get("effect") or "").strip()
            if not driver and not effect:
                continue
            rows.append(f"<li>{_esc(driver)} → {_esc(effect)}</li>")
        if rows:
            causal_links_html = f'<div><b>因果链</b><ul>{"".join(rows)}</ul></div>'

    capabilities_html = "".join(
        _capability_item_html(item)
        for item in (state.capabilities_gained or [])
        if isinstance(item, dict)
    )
    if capabilities_html:
        capabilities_html = f"<div><b>本步新增能力</b>{capabilities_html}</div>"

    violations_html = ""
    if state.resource_violations:
        rows = "".join(
            f"<li>{_esc(v.get('field', ''))}：{_esc(v.get('llm_value'))} → "
            f"{_esc(v.get('clamped_value'))}（系统纠正到下限）</li>"
            for v in state.resource_violations
            if isinstance(v, dict)
        )
        violations_html += (
            f'<div class="ws-chapter-resource-violation">资源下限纠正<ul>{rows}</ul></div>'
        )
    if state.relation_violations:
        rows = "".join(
            f"<li>{_esc(json.dumps(v, ensure_ascii=False))}</li>"
            for v in state.relation_violations
            if isinstance(v, dict)
        )
        violations_html += (
            f'<div class="ws-chapter-relation-violation">资源转移不一致提示<ul>{rows}</ul></div>'
        )

    major_class = " ws-chapter-major" if state.major_decision else ""
    major_badge = ' <span class="ws-pill ws-pill-major">重大决策</span>' if state.major_decision else ""

    return f"""
    <div class="ws-chapter{major_class}">
      <div class="ws-chapter-step">STEP {state.step} · {_esc(time_label)}{granularity_note}{major_badge}</div>
      <div class="ws-chapter-summary">{_esc(state.summary)}</div>
      <div class="ws-chapter-narrative">{_esc(state.narrative)}</div>
      {choice_html}
      {key_drivers_html}
      {causal_links_html}
      {capabilities_html}
      {violations_html}
    </div>
    """


def _render_timeline(history: List[SimState]) -> str:
    ordered = sorted(history, key=lambda s: s.step)  # 显式正序，即便入参
    # 已经是正序也不依赖调用方保证（`store.load_history()` 目前确实是
    # 按落盘顺序返回，等价于正序，但这里不假设这个隐含前提）。
    cards = []
    prev: Optional[SimState] = None
    for state in ordered:
        cards.append(_render_state_card(prev, state))
        prev = state
    body = "".join(cards) or '<p class="ws-muted">这条分支还没有任何推进记录。</p>'
    return f"""
    <div class="ws-card">
      <h2 class="ws-serif">时间线（正序）</h2>
      {body}
    </div>
    """


def _render_retrospective_record(record: Any) -> str:
    report = record.report

    def _section(title: str, items: List[Dict[str, Any]]) -> str:
        """每个板块的条目字段名不统一（`workflows/retrospective.yaml`
        逐板块定义）：`turning_points` 用 `why`（+ `step`/
        `chosen_option`）、`what_went_well`/`what_to_reflect_on` 用
        `point`（+ `evidence`）、`lessons` 用 `lesson`（+ `source`）。
        按已知字段名依次尝试，都没有才退化为整条目的 JSON 原文，
        保证旧数据/字段缺失也不会让这一条静默消失。
        """
        if not items:
            return ""
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = item.get("why") or item.get("point") or item.get("lesson")
            if not text:
                text = json.dumps(item, ensure_ascii=False)
            extra = item.get("evidence") or item.get("source")
            step = item.get("step")
            prefix = f"[第 {step} 步] " if step is not None else ""
            extra_html = f"（{_esc(extra)}）" if extra else ""
            rows.append(f"<li>{prefix}{_esc(text)}{extra_html}</li>")
        if not rows:
            return ""
        return f"<div><b>{title}</b><ul>{''.join(rows)}</ul></div>"

    caveats_html = ""
    if report.caveats:
        items = "".join(f"<li>{_esc(c)}</li>" for c in report.caveats)
        caveats_html = f"<div><b>注意事项</b><ul>{items}</ul></div>"

    return f"""
    <div class="ws-card">
      <div class="ws-muted">生成时间：{_esc(record.created_at)} · 覆盖至第 {record.up_to_step} 步</div>
      {_section("转折点", report.turning_points)}
      {_section("做得好的地方", report.what_went_well)}
      {_section("值得反思的地方", report.what_to_reflect_on)}
      {_section("经验教训", report.lessons)}
      {caveats_html}
    </div>
    """


def _render_retrospectives(data_dir: Path, sim_id: str, branch: str) -> str:
    records = retrospective_mod.load_for_branch(data_dir, sim_id, branch)
    if not records:
        # 这条分支从未生成过复盘：按规划文档第 3 节要求，整节不渲染，
        # 不留一个空标题。
        return ""
    body = "".join(_render_retrospective_record(r) for r in records)
    return f"""
    <div class="ws-card">
      <h2 class="ws-serif">复盘报告</h2>
      {body}
    </div>
    """


# ── 页面外壳（CSS 沿用"夜航日志"主题，见 `app.py::THEME_CSS`）──────

_PAGE_CSS = """
<style>
:root {
    --ws-bg: #12111c;
    --ws-bg-elevated: #1c1a2b;
    --ws-border: #322f49;
    --ws-text: #edeaf5;
    --ws-text-muted: #9b96b3;
    --ws-accent: #e8b559;
    --ws-violet: #8c7ae6;
    --ws-danger: #e2726e;
}
body {
    background: radial-gradient(circle at 20% 0%, #1a1830 0%, var(--ws-bg) 55%);
    color: var(--ws-text);
    font-family: -apple-system, "Segoe UI", "PingFang SC", sans-serif;
    line-height: 1.6;
    max-width: 860px;
    margin: 0 auto;
    padding: 2rem 1.2rem 4rem;
}
h1, h2, h3 { font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif; }
.ws-serif { font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif; }
.ws-card {
    background: var(--ws-bg-elevated);
    border: 1px solid var(--ws-border);
    border-radius: 10px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 1.2rem;
}
.ws-muted { color: var(--ws-text-muted); font-size: 0.86rem; }
.ws-json-block {
    background: #0e0d17;
    border: 1px solid var(--ws-border);
    border-radius: 6px;
    padding: 0.8rem;
    overflow-x: auto;
    font-size: 0.82rem;
}
.ws-chapter {
    border-left: 2px solid var(--ws-border);
    padding-left: 1rem;
    margin: 0 0 1.4rem 0.4rem;
}
.ws-chapter-major { border-left-color: var(--ws-accent); }
.ws-chapter-step { color: var(--ws-accent); font-size: 0.78rem; letter-spacing: 0.04em; }
.ws-chapter-summary { font-size: 1rem; margin: 0.15rem 0 0.3rem; font-weight: 600; }
.ws-chapter-narrative { color: var(--ws-text-muted); font-size: 0.92rem; }
.ws-chapter-choice { margin-top: 0.4rem; font-size: 0.86rem; color: var(--ws-violet); }
.ws-chapter-resource-violation, .ws-chapter-relation-violation {
    margin-top: 0.4rem; font-size: 0.82rem; color: var(--ws-danger); font-weight: 600;
}
.ws-pill {
    display: inline-block; padding: 0.05rem 0.5rem; border-radius: 999px;
    font-size: 0.7rem; margin-left: 0.4rem;
}
.ws-pill-major { background: rgba(232, 181, 89, 0.16); color: var(--ws-accent); }
.ws-capability-item { margin: 0.3rem 0; }
.ws-capability-detail { margin-left: 1.2rem; }
.ws-causal-graph svg { max-width: 100%; height: auto; }
ul { margin: 0.3rem 0 0.3rem 1.2rem; }
.ws-uncertain-field { margin: 0.15rem 0; font-size: 0.82rem; color: var(--ws-text-muted); }
.ws-uncertain-badge {
    display: inline-block; padding: 0.05rem 0.4rem; border-radius: 6px;
    font-size: 0.72rem; font-weight: 600; margin-right: 0.4rem;
    background: rgba(140, 122, 230, 0.16); color: var(--ws-violet);
}
.ws-uncertain-badge-low { background: rgba(226, 114, 110, 0.15); color: var(--ws-danger); }
.ws-uncertain-badge-medium { background: rgba(232, 181, 89, 0.18); color: var(--ws-accent); }
.ws-uncertain-badge-high { background: rgba(90, 160, 224, 0.15); color: #7fb3e8; }
.ws-causal-line-row {
    border: 1px solid var(--ws-border);
    border-radius: 10px;
    padding: 0.6rem 0.8rem;
    margin-bottom: 0.7rem;
    background: var(--ws-bg);
}
.ws-causal-line-row-title { font-weight: 600; font-size: 0.92rem; margin-bottom: 0.35rem; }
.ws-causal-line-row-id { color: var(--ws-text-muted); font-weight: 400; font-size: 0.76rem; }
.ws-causal-line-track { display: flex; flex-wrap: wrap; align-items: center; }
.ws-causal-line-point {
    display: inline-flex; flex-direction: column;
    padding: 0.15rem 0.55rem; margin: 0.1rem 0.35rem 0.1rem 0;
    border-radius: 10px; font-size: 0.74rem;
    background: transparent; border: 1px solid var(--ws-border);
    color: var(--ws-text-muted); max-width: 220px;
}
.ws-causal-line-point-time { color: var(--ws-accent); font-weight: 600; font-size: 0.7rem; }
.ws-causal-line-point-arrow { color: var(--ws-border); margin: 0 0.1rem; align-self: center; }
.ws-causal-line-empty { color: var(--ws-text-muted); font-size: 0.82rem; font-style: italic; }
</style>
"""


def export_simulation_html(data_dir: Path, sim_id: str, branch: str = "main") -> str:
    """导出入口。返回完整的 HTML 字符串，不写任何文件。

    Args:
        data_dir: `SimStore` 数据根目录（一般是 `config.DATA_DIR`）。
        sim_id: 模拟实例 id。
        branch: 要导出的分支，默认 `main`。只导出这一条分支的时间线，
            不在一次导出里塞进所有分支（见规划文档第 5 节）。

    Raises:
        SimNotFoundError: `sim_id` 不存在（沿用 `store.py` 的异常
            类型，调用方按处理其它 `store` 调用同样的方式处理即可）。
    """
    data_dir = Path(data_dir)
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    history = store.load_history(branch)
    state0 = history[0] if history else None

    try:
        branches_detailed = bm.list_branches_detailed(data_dir, sim_id)
    except Exception:  # noqa: BLE001 — 分支元信息读取失败不应该让
        # 整个导出失败，页头退化为不展示"分叉自"这一行。
        branches_detailed = []

    body = "".join(
        [
            _render_header(manifest, branch, branches_detailed),
            _render_input_and_background(manifest, state0),
            _render_causal_overview(manifest, history),
            _render_timeline(history),
            _render_retrospectives(data_dir, sim_id, branch),
        ]
    )

    title = _esc(manifest.title or manifest.sim_id)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · 模拟导出</title>
{_PAGE_CSS}
</head>
<body>
{body}
</body>
</html>
"""
