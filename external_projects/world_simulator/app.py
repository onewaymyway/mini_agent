#!/usr/bin/env python3
"""world_simulator 独立看板（阶段二 MVP）。

对应 `next_doc/world_simulator_external_project_plan.md` 第 5 节：
沿用 stock_watch/app.py 的技术选型（Streamlit，本地
`streamlit run app.py` 启动），但视觉风格走"游戏感"方向而不是数据
看板风——主题取"夜航日志"：深靛蓝底 + 温暖灯笼金点缀，时间线用竖排
"章节卡片"呈现，分支选项渲染成可点击的选择卡片而不是下拉框。

页面（阶段二实现 1-3，阶段三新增 4：对比视图 + 分支管理，阶段七新增
5/6，对应方案第 5 节全部 6 个页面已落地）：
  1. 模拟列表
  2. 创建向导（意图 → 草稿 → 编辑/确认 → 创建）
  3. 实例详情/推进面板（时间线 + 推进下一步 + 候选分支卡片 + 分支管理）
  4. 对比视图（选两条时间线并排对比，可以是同一实例的不同分支，也可以
     是两个独立实例）
  5. 存档管理（全部实例总览 + 删除实例，二次确认，不可逆）
  6. 游戏化视图（同一份 state_history 按"章节"重新渲染成可翻页的故事
     回顾 + 成就徽章，纯展示层，不引入新的数据结构，见
     `world_simulator/achievements.py`）

启动方式：
    cd external_projects/world_simulator
    streamlit run app.py --server.port 8502
"""

from __future__ import annotations

import html as html_stdlib
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "entrypoints"))

import _common  # noqa: F401  — 触发 sys.path 设置，未使用其它内容
from world_simulator import branch_manager as bm
from world_simulator import causal_graph as cg_mod
from world_simulator import causal_tree
from world_simulator import hypothesis as hyp_mod
from world_simulator import reality_check as rc_mod
from world_simulator import retrospective as retrospective_mod
from world_simulator import agent_preview as agent_preview_mod
from world_simulator import policy_feedback as policy_feedback_mod
from world_simulator import quality_signals as quality_signals_mod
from world_simulator.autopilot import (
    AutopilotDisabledError, run_autopilot_step, run_comparison_experiment, run_repeated_experiment,
)
from world_simulator.analysis import aggregate_field_stats, normalize_objectives, rank_by_objectives
from world_simulator import attribution as attribution_mod
from world_simulator import trend as trend_mod
from world_simulator import relationship as relationship_mod
from world_simulator import knowledge_base as knowledge_base_mod
from world_simulator.config import DATA_DIR, ensure_dirs
from world_simulator.achievements import achievement_progress, compute_achievements
from world_simulator.engine import (
    OwnedVarsOverlapError,
    SimAlreadyEndedError,
    SimEngineError,
    SimPausedError,
    accept_suggested_causal_line,
    advance,
    advance_lines,
    apply_structural_change,
    delete_simulation,
    fast_forward,
    get_simulation,
    list_simulations,
    materialize_simulation,
    rename_simulation,
    reject_suggested_causal_line,
    set_pilot_config,
    set_status,
    update_settings,
)
from world_simulator.spec_generator import (
    ScenarioDraft,
    ScenarioGenerationError,
    generate_scenario,
    resolve_hints,
)
from world_simulator.state_model import ChoiceOption
from world_simulator.store import SimNotFoundError, SimStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("world_simulator.app")

ensure_dirs()

# ─────────────────────────────────────────────────────────────
# "夜航日志"主题：深靛蓝底 + 灯笼金点缀，卡片化章节/选择卡
# ─────────────────────────────────────────────────────────────

THEME_CSS = """
<style>
:root {
    --ws-bg: #12111c;
    --ws-bg-elevated: #1c1a2b;
    --ws-border: #322f49;
    --ws-text: #edeaf5;
    --ws-text-muted: #9b96b3;
    --ws-accent: #e8b559;
    --ws-accent-soft: rgba(232, 181, 89, 0.14);
    --ws-violet: #8c7ae6;
    --ws-success: #6fcf97;
    --ws-danger: #e2726e;
}

.stApp {
    background: radial-gradient(circle at 20% 0%, #1a1830 0%, var(--ws-bg) 55%);
    color: var(--ws-text);
}

h1, h2, h3, .ws-serif {
    font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
    letter-spacing: 0.01em;
}

section[data-testid="stSidebar"] {
    background: #0e0d17;
    border-right: 1px solid var(--ws-border);
}

.ws-card {
    background: var(--ws-bg-elevated);
    border: 1px solid var(--ws-border);
    border-radius: 10px;
    padding: 1.1rem 1.3rem;
    margin-bottom: 0.9rem;
}

.ws-card-title {
    font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
    font-size: 1.05rem;
    color: var(--ws-text);
    margin-bottom: 0.15rem;
}

.ws-muted { color: var(--ws-text-muted); font-size: 0.86rem; }

.ws-pill {
    display: inline-block;
    padding: 0.12rem 0.6rem;
    border-radius: 999px;
    font-size: 0.75rem;
    margin-right: 0.4rem;
}
.ws-pill-active { background: rgba(111, 207, 151, 0.16); color: var(--ws-success); }
.ws-pill-paused { background: rgba(232, 181, 89, 0.16); color: var(--ws-accent); }
.ws-pill-ended { background: rgba(155, 150, 179, 0.16); color: var(--ws-text-muted); }

.ws-chapter {
    border-left: 2px solid var(--ws-border);
    padding-left: 1rem;
    margin-left: 0.4rem;
    margin-bottom: 1.4rem;
    position: relative;
}
.ws-chapter::before {
    content: "";
    position: absolute;
    left: -5px;
    top: 0.35rem;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--ws-accent);
}
.ws-chapter-step {
    color: var(--ws-accent);
    font-size: 0.78rem;
    letter-spacing: 0.04em;
}
.ws-chapter-summary { font-size: 1rem; margin: 0.15rem 0 0.3rem; }
.ws-chapter-narrative { color: var(--ws-text-muted); font-size: 0.88rem; line-height: 1.55; }
.ws-chapter-choice {
    margin-top: 0.4rem;
    font-size: 0.82rem;
    color: var(--ws-violet);
}
.ws-chapter-granularity {
    margin-top: 0.2rem;
    font-size: 0.78rem;
    color: var(--ws-text-muted);
}
.ws-chapter-granularity-changed {
    color: var(--ws-accent);
    font-weight: 600;
}
.ws-chapter-resource-violation {
    margin-top: 0.2rem;
    font-size: 0.78rem;
    color: var(--ws-danger, #e0665a);
    font-weight: 600;
}
.ws-chapter-relation-violation {
    margin-top: 0.2rem;
    font-size: 0.78rem;
    color: #b8860b;
    font-weight: 600;
}
.ws-chapter-option-warning {
    margin-top: 0.2rem;
    font-size: 0.78rem;
    color: var(--ws-muted, #8a8f98);
    font-style: italic;
}
.ws-chapter-background-entity-note {
    margin-top: 0.2rem;
    font-size: 0.78rem;
    color: var(--ws-muted, #8a8f98);
    font-style: italic;
}
.ws-uncertain-field {
    margin: 0.15rem 0;
    font-size: 0.82rem;
    color: var(--ws-text-muted);
}
.ws-uncertain-badge {
    display: inline-block;
    padding: 0.05rem 0.4rem;
    border-radius: 6px;
    font-size: 0.72rem;
    font-weight: 600;
    margin-right: 0.4rem;
}
.ws-uncertain-badge-low { background: rgba(224, 102, 90, 0.15); color: var(--ws-danger, #e0665a); }
.ws-uncertain-badge-medium { background: rgba(224, 180, 90, 0.18); color: #b8860b; }
.ws-uncertain-badge-high { background: rgba(90, 160, 224, 0.15); color: #4a7fb5; }
.ws-urgency-badge-low { background: rgba(150, 150, 150, 0.16); color: #777; }
.ws-urgency-badge-medium { background: rgba(224, 180, 90, 0.18); color: #b8860b; }
.ws-urgency-badge-high { background: rgba(224, 140, 60, 0.2); color: #c1650c; }
.ws-urgency-badge-critical { background: rgba(224, 60, 60, 0.22); color: #c0271a; font-weight: 700; }
.ws-continue-badge { background: rgba(150, 150, 150, 0.16); color: #777; }
.ws-provenance-badge-fact { background: rgba(90, 200, 120, 0.16); color: #2f8f4e; }
.ws-provenance-badge-assumption { background: rgba(224, 180, 90, 0.18); color: #b8860b; }
.ws-provenance-badge-inference { background: rgba(90, 160, 224, 0.15); color: #4a7fb5; }
.ws-provenance-badge-unknown { background: rgba(150, 150, 150, 0.16); color: #777; }
.ws-key-drivers {
    margin-top: 0.25rem;
}
.ws-key-driver-tag {
    display: inline-block;
    padding: 0.08rem 0.5rem;
    margin: 0.1rem 0.3rem 0.1rem 0;
    border-radius: 10px;
    font-size: 0.74rem;
    background: var(--ws-bg-elevated);
    border: 1px solid var(--ws-border);
    color: var(--ws-violet, var(--ws-text-muted));
}
.ws-key-driver-details {
    display: inline-block;
    margin: 0.1rem 0.3rem 0.1rem 0;
    vertical-align: top;
}
.ws-key-driver-details > summary.ws-key-driver-tag {
    margin: 0;
    cursor: pointer;
    list-style: none;
}
.ws-key-driver-details > summary.ws-key-driver-tag::-webkit-details-marker {
    display: none;
}
.ws-causal-line-row {
    border: 1px solid var(--ws-border);
    border-radius: 10px;
    padding: 0.6rem 0.8rem;
    margin-bottom: 0.7rem;
    background: var(--ws-bg-elevated);
}
.ws-causal-line-row-title {
    font-weight: 600;
    font-size: 0.92rem;
    margin-bottom: 0.35rem;
}
.ws-causal-line-row-id {
    color: var(--ws-text-muted);
    font-weight: 400;
    font-size: 0.76rem;
}
.ws-causal-line-track {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
}
.ws-causal-line-point {
    display: inline-flex;
    flex-direction: column;
    padding: 0.15rem 0.55rem;
    margin: 0.1rem 0.35rem 0.1rem 0;
    border-radius: 10px;
    font-size: 0.74rem;
    background: var(--ws-bg-base, transparent);
    border: 1px solid var(--ws-border);
    color: var(--ws-text-muted);
    max-width: 220px;
}
.ws-causal-line-point-time {
    color: var(--ws-accent);
    font-weight: 600;
    font-size: 0.7rem;
}
.ws-causal-line-point-arrow {
    color: var(--ws-border);
    margin: 0 0.1rem;
    align-self: center;
}
.ws-causal-line-empty {
    color: var(--ws-text-muted);
    font-size: 0.82rem;
    font-style: italic;
}
.ws-key-driver-detail-body {
    margin: 0.25rem 0 0.15rem 0.2rem;
    padding: 0.35rem 0.6rem;
    border-left: 2px solid var(--ws-border);
    font-size: 0.78rem;
    color: var(--ws-text-muted);
}

div[data-testid="stButton"] > button,
div[data-testid="stPopover"] button {
    border-radius: 8px;
    border: 1px solid var(--ws-border);
    background: var(--ws-bg-elevated);
    color: var(--ws-text);
}
div[data-testid="stButton"] > button:hover,
div[data-testid="stPopover"] button:hover {
    border-color: var(--ws-accent);
    color: var(--ws-accent);
}

.ws-badge {
    display: inline-block;
    width: 100%;
    border: 1px solid var(--ws-border);
    border-radius: 10px;
    padding: 0.7rem 0.9rem;
    margin-bottom: 0.6rem;
    box-sizing: border-box;
}
.ws-badge-unlocked {
    background: var(--ws-accent-soft);
    border-color: var(--ws-accent);
}
.ws-badge-locked { opacity: 0.45; }
.ws-badge-title { font-weight: 600; }
.ws-badge-desc { color: var(--ws-text-muted); font-size: 0.82rem; }

.ws-danger-zone {
    border: 1px solid var(--ws-danger);
    border-radius: 10px;
    padding: 0.9rem 1.1rem;
    margin-top: 0.6rem;
}

/* ── 折叠面板 / 标签页配色修复 ──────────────────────────────
   Streamlit 的 stExpander / stTabs 原生组件默认走浅色主题的
   背景色（白/浅灰），这份自定义 CSS 只覆盖了 .stApp 的整体底色，
   没有覆盖这两个组件自己的容器背景——展开后标题栏和内容区背景
   仍是浅色，配上本主题的浅色文字（--ws-text）就变成了"白底白字"
   看不清楚。这里显式把折叠面板/标签页的所有状态（收起/展开/
   悬停/选中）都固定成主题的深色调色板，不依赖 Streamlit 当前用的
   是浅色还是深色主题。 */
div[data-testid="stExpander"] {
    border: 1px solid var(--ws-border);
    border-radius: 10px;
    background: var(--ws-bg-elevated);
}
div[data-testid="stExpander"] summary,
div[data-testid="stExpander"] > details > summary {
    background: var(--ws-bg-elevated) !important;
    color: var(--ws-text) !important;
    border-radius: 10px;
}
div[data-testid="stExpander"] summary:hover,
div[data-testid="stExpander"] summary:focus {
    background: var(--ws-accent-soft) !important;
    color: var(--ws-accent) !important;
}
div[data-testid="stExpander"] summary svg {
    fill: var(--ws-text) !important;
}
div[data-testid="stExpanderDetails"],
div[data-testid="stExpander"] div[data-testid="stVerticalBlock"] {
    background: var(--ws-bg-elevated) !important;
    color: var(--ws-text) !important;
}

div[data-testid="stTabs"] div[data-baseweb="tab-list"] {
    background: transparent;
    border-bottom: 1px solid var(--ws-border);
    gap: 0.25rem;
}
div[data-testid="stTabs"] button[data-baseweb="tab"] {
    background: var(--ws-bg-elevated) !important;
    color: var(--ws-text-muted) !important;
    border-radius: 8px 8px 0 0;
}
div[data-testid="stTabs"] button[data-baseweb="tab"]:hover {
    color: var(--ws-accent) !important;
}
div[data-testid="stTabs"] button[aria-selected="true"] {
    background: var(--ws-accent-soft) !important;
    color: var(--ws-accent) !important;
}
div[data-testid="stTabs"] div[data-testid="stVerticalBlock"] {
    color: var(--ws-text);
}

/* Streamlit 自带的顶部工具栏（含右上角"Deploy"按钮）默认是白底，
   跟"夜航日志"深色主题不一致，这里统一改成跟主题一致的深色。 */
header[data-testid="stHeader"] {
    background: var(--ws-bg) !important;
    border-bottom: 1px solid var(--ws-border);
}
div[data-testid="stDecoration"] {
    background: var(--ws-bg) !important;
    background-image: none !important;
}
div[data-testid="stToolbar"],
div[data-testid="stToolbarActions"] {
    background: transparent !important;
}
div[data-testid="stToolbar"] svg,
div[data-testid="stToolbarActions"] svg,
div[data-testid="stMainMenu"] svg {
    fill: var(--ws-text-muted) !important;
}
div[data-testid="stToolbar"] button:hover svg,
div[data-testid="stToolbarActions"] button:hover svg {
    fill: var(--ws-accent) !important;
}
div[data-testid="stAppDeployButton"] button {
    background: var(--ws-bg-elevated) !important;
    color: var(--ws-text) !important;
    border: 1px solid var(--ws-border) !important;
}
div[data-testid="stAppDeployButton"] button:hover {
    background: var(--ws-accent-soft) !important;
    color: var(--ws-accent) !important;
    border-color: var(--ws-accent) !important;
}
div[data-testid="stAppDeployButton"] button p {
    color: inherit !important;
}
</style>
"""


def _pill(status: str) -> str:
    label = {"active": "进行中", "paused": "已暂停", "ended": "已结束"}.get(status, status)
    cls = {"active": "ws-pill-active", "paused": "ws-pill-paused", "ended": "ws-pill-ended"}.get(
        status, "ws-pill-paused"
    )
    return f'<span class="ws-pill {cls}">{label}</span>'


def _format_local_time(iso_str: Optional[str]) -> str:
    """把 `store.now_iso()` 落盘的带时区偏移的 ISO 字符串（比如
    `2026-09-17T14:03:01+08:00`）渲染成人读的本地时间，供页面上所有
    "创建于 ..." 展示复用。

    `now_iso()` 存的本来就是"服务器当地时区"的时间（`datetime.now(utc)
    .astimezone()`），这里只是把 `T` 分隔符、时区偏移这些机器格式换成
    `YYYY-MM-DD HH:MM:SS` 的人类可读形式——不做任何时区换算，展示的还是
    落盘时记录的那个本地时刻。解析失败（数据损坏/字段缺失/历史脏数据）
    时原样返回，不让一条格式化失败的时间字符串搞挂整个页面。
    """
    if not iso_str:
        return "未知"
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return iso_str
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _html_text(value: str) -> str:
    """把一段自由文本（标题/摘要/叙事/理由，可能来自 LLM 生成，可能带
    真实换行）安全地嵌进内联 HTML 片段里。

    两个目的：
    1. 转义 `<`/`>`/`&` 等特殊字符，避免文本里恰好出现类似标签的内容时
       被当成真的 HTML 解析、破坏卡片布局。
    2. 把真实换行替换成 `<br>`——这是时间线卡片曾经渲染错乱（`</div>`
       原样露出来）的根因：一段多行 `f\"\"\"<div>...</div>\"\"\"` 里如果某个
       占位符插入的文本自身带空行/换行，markdown 解析器会把这个"空行"
       当成当前 HTML 块的结束，导致后面缩进的收尾标签被当成普通缩进
       代码块渲染，而不是继续当 HTML 解析。统一在插值前把换行转成
       `<br>`（不再是"真换行"），从源头上避免这个问题，而不是每处
       手动小心翼翼控制字符串里能不能有换行。
    """
    return html_stdlib.escape(value).replace("\n", "<br>")


def _granularity_note_html(state) -> str:
    """渲染时间线节点时，把这一步的粒度信息拼成一段 HTML（可能为空）。

    - 有粒度值时显示"本步粒度：X"。
    - `granularity_changed` 为真时额外加一个高亮标记 + 切换理由，让
      用户一眼看出"这里节奏变了、为什么变"（对应粒度自动切换功能里
      "可解释"的那部分设计）。
    """
    granularity = getattr(state, "time_granularity", "") or ""
    if not granularity:
        return ""
    if getattr(state, "granularity_changed", False):
        reason = getattr(state, "granularity_reason", None) or ""
        reason_html = f"：{_html_text(reason)}" if reason else ""
        return (
            f'<div class="ws-chapter-granularity ws-chapter-granularity-changed">'
            f"⏱ 本步粒度切换为「{_html_text(granularity)}」{reason_html}</div>"
        )
    return f'<div class="ws-chapter-granularity">本步粒度：{_html_text(granularity)}</div>'


def _resource_violations_html(state) -> str:
    """渲染这一步资源类字段被系统纠正的提示（阶段九，4.1 节，可能为空）。

    每条越界项单独一行，展示字段名 + LLM 原始值 + 被纠正后的值——
    保持透明，让用户知道"系统改过一处数值"而不是静默篡改。
    """
    violations = getattr(state, "resource_violations", None) or []
    if not violations:
        return ""
    lines = []
    for v in violations:
        field = _html_text(str(v.get("field", "")))
        llm_value = v.get("llm_value")
        clamped_value = v.get("clamped_value")
        lines.append(
            f'<div class="ws-chapter-resource-violation">⚠ 「{field}」原始值 '
            f"{_html_text(str(llm_value))} 不合理，已自动纠正为 "
            f"{_html_text(str(clamped_value))}</div>"
        )
    return "".join(lines)


def _relation_violations_html(state) -> str:
    """渲染这一步资源转移关系"不太守恒"的提示（阶段十六，4.8 节，
    可能为空）。

    和 `_resource_violations_html()` 不同：这里**没有修改任何数值**，
    只是提示"可能有问题"，措辞明确用"不一致"而不是"已自动纠正"。
    """
    violations = getattr(state, "relation_violations", None) or []
    if not violations:
        return ""
    lines = []
    for v in violations:
        if v.get("kind") == "production":
            # 第八轮批次二：production（持续产出）关系的速率偏差提示，
            # 和 transfer 提示共用同一个展示位置，但字段形状不同。
            field_name = _html_text(str(v.get("field", "")))
            expected = v.get("amount_per_step")
            actual = v.get("actual_delta")
            lines.append(
                f'<div class="ws-chapter-relation-violation">🔶 「{field_name}」'
                f"这一步产出与声明速率明显不符（声明每步 {_html_text(str(expected))}，"
                f"实际变化 {_html_text(str(actual))}）</div>"
            )
            continue
        from_field = _html_text(str(v.get("from", "")))
        to_field = _html_text(str(v.get("to", "")))
        delta_from = v.get("delta_from")
        delta_to = v.get("delta_to")
        lines.append(
            f'<div class="ws-chapter-relation-violation">🔶 「{from_field}」'
            f"（变化 {_html_text(str(delta_from))}）→「{to_field}」"
            f"（变化 {_html_text(str(delta_to))}）看起来不太守恒，"
            "可能是 AI 算错了或者有未说明的损耗</div>"
        )
    return "".join(lines)


def _option_warnings_html(state) -> str:
    """渲染这一步 `option_heuristics.py` 辅助校验产出的弱信号提示
    （阶段三十三第三批，4.4/4.5 节，可能为空）。

    和 `_resource_violations_html()`/`_relation_violations_html()`
    风格一致：只展示、不阻断，措辞明确用"建议检查"而不是"已经错了"，
    因为这两条检测规则本身就承认会有误报（见 `option_heuristics.py`
    docstring）。选项标题按 `option_id` 反查，找不到（比如历史数据里
    选项后来被移除）就退化为直接展示 id 本身。
    """
    warnings = getattr(state, "option_warnings", None) or []
    if not warnings:
        return ""
    options = getattr(state, "options", None) or []
    label_by_id = {opt.id: opt.label for opt in options}
    lines = []
    for w in warnings:
        option_id = str(w.get("option_id", ""))
        option_label = label_by_id.get(option_id, option_id)
        note = _html_text(str(w.get("note", "")))
        lines.append(
            f'<div class="ws-chapter-option-warning">🔍「{_html_text(option_label)}」'
            f"：{note}</div>"
        )
    return "".join(lines)


def _background_entities_html(state) -> str:
    """渲染这一步被 Hierarchical Agent 规则外推覆盖的背景角色提示
    （阶段十九，4.10 节设计草案第一步，可能为空）。纯信息性说明，不是
    "问题"，措辞和颜色都区别于资源相关的两种提示。"""
    applied = getattr(state, "background_entities_applied", None) or []
    if not applied:
        return ""
    names = "、".join(_html_text(str(n)) for n in applied)
    return (
        f'<div class="ws-chapter-background-entity-note">🧩 背景角色（{names}）'
        "这一步的数值由规则自动外推更新，未使用 AI 推理</div>"
    )


def _latest_beliefs(history, up_to_step: int) -> Dict[str, Any]:
    """在 `history`（按 step 顺序的 `SimState` 列表）里，从
    `up_to_step` 往前找每个字段最近一次有记录的 `beliefs` 值（4.3
    节，State/Belief 分离——`beliefs` 是稀疏字段，不是每一步都会给
    出，展示层需要"沿用最近一次的认知记录"而不是只看当前这一步）。
    """
    merged: Dict[str, Any] = {}
    for s in history:
        if s.step > up_to_step:
            break
        b = getattr(s, "beliefs", None) or {}
        merged.update(b)
    return merged


def _belief_comparison_html(vars_dict, beliefs: Dict[str, Any], belief_fields: List[str]) -> str:
    """渲染"真实值 vs 认知值"对比行（4.3 节）。只对同时满足以下条件
    的字段渲染：字段在 `belief_fields` 里声明过、`beliefs` 里有记录、
    且认知值与真实值不同（一致时不特别标注，避免变成每次都在的
    噪音）。未声明/无记录的字段不展示，不用占位值制造伪信息。
    """
    if not belief_fields or not beliefs:
        return ""
    lines = []
    for f in belief_fields:
        if f not in beliefs:
            continue
        real = vars_dict.get(f) if isinstance(vars_dict, dict) else None
        belief_val = beliefs[f]
        if real is not None and real == belief_val:
            continue
        if isinstance(belief_val, dict):
            belief_text = "、".join(f"{k}: {v}" for k, v in belief_val.items())
        else:
            belief_text = str(belief_val)
        lines.append(
            f'<div class="ws-uncertain-field">'
            f'<span class="ws-uncertain-badge ws-uncertain-badge-medium">认知偏差</span>'
            f"「{_html_text(f)}」真实：{_html_text(str(real))} · 你以为：{_html_text(belief_text)}"
            f"</div>"
        )
    return "".join(lines)


def _render_vars_display(vars_dict, multi_entity_mode: bool) -> None:
    """展示"关键变量"，多主体模式（阶段十七，4.9 节）下按 entity 分 tab
    展示各自的私有 `vars` + 一个"共享信息"tab，避免互相泄露；否则退化为
    原来的 `st.json(vars_dict)` 原样展示（阶段一起就有的行为，不受影响）。

    只有 `manifest.settings.multi_entity_mode` 为真、且 `vars` 里确实
    有一个非空的 `entities` 字典时才启用分 tab 展示——`multi_entity_mode`
    为真但 `vars` 里没有按约定给出 `entities` 结构（比如旧数据、或者
    skill 没有遵守约定）时，安全退化为原样展示，不报错。
    """
    entities = vars_dict.get("entities") if isinstance(vars_dict, dict) else None
    if not (multi_entity_mode and isinstance(entities, dict) and entities):
        st.json(vars_dict)
        return
    shared_vars = vars_dict.get("shared_vars")
    shared_vars = shared_vars if isinstance(shared_vars, dict) else {}
    entity_names = list(entities.keys())
    tab_labels = [f"🧑 {name}" for name in entity_names]
    if shared_vars:
        tab_labels.append("🌐 共享信息")
    tabs = st.tabs(tab_labels)
    for tab, name in zip(tabs, entity_names):
        with tab:
            st.caption(f"「{name}」的私有信息，其它主体看不到这一份。")
            st.json(entities[name])
    if shared_vars:
        with tabs[-1]:
            st.caption("所有主体共享的公开信息。")
            st.json(shared_vars)
    other_keys = {k: v for k, v in vars_dict.items() if k not in ("entities", "shared_vars")}
    if other_keys:
        st.caption("其它顶层字段：")
        st.json(other_keys)


_CONFIDENCE_LABELS = {"low": "低置信度", "medium": "中置信度", "high": "高置信度"}


def _uncertain_fields_html(state) -> str:
    """渲染这一步被标注为"主观估计、置信度不高"的字段列表（阶段十一，
    4.3 节，可能为空）。

    每条一行：字段名 + 置信度徽章 + 可选说明——只在这里展示"哪些数值
    该打个问号"，不在"关键变量"原始 JSON 展示区做任何改动，避免破坏
    `st.json` 的默认渲染方式。
    """
    items = getattr(state, "uncertain_fields", None) or []
    if not items:
        return ""
    lines = []
    for item in items:
        field_name = _html_text(str(item.get("field", "")))
        if not field_name:
            continue
        confidence = str(item.get("confidence", "") or "").strip().lower()
        badge_label = _CONFIDENCE_LABELS.get(confidence, confidence or "未知置信度")
        badge_class = confidence if confidence in _CONFIDENCE_LABELS else "medium"
        note = str(item.get("note", "") or "").strip()
        note_html = f"：{_html_text(note)}" if note else ""
        lines.append(
            f'<div class="ws-uncertain-field">'
            f'<span class="ws-uncertain-badge ws-uncertain-badge-{badge_class}">{badge_label}</span>'
            f"「{field_name}」{note_html}</div>"
        )
    return "".join(lines)


_PROVENANCE_BADGE_LABELS = {
    "fact": "你说的",
    "assumption": "系统假设",
    "inference": "系统推断",
    "unknown": "未知，先占位",
}


def _field_provenance_html(state) -> str:
    """渲染初始状态每个顶层字段的来源标注（阶段三十二，4.2 节）。

    只有 `state0`（`field_provenance` 由 `generate_scenario` 生成）才
    有内容，其它步骤这个字段恒为空字典，函数直接返回空字符串——不
    展示任何多余提示，同 `_uncertain_fields_html` 的既有取舍。
    """
    items = getattr(state, "field_provenance", None) or {}
    if not items:
        return ""
    lines = []
    for field_name, source in items.items():
        field_name = _html_text(str(field_name))
        if not field_name:
            continue
        source = str(source or "").strip().lower()
        badge_label = _PROVENANCE_BADGE_LABELS.get(source, source or "未标注")
        badge_class = source if source in _PROVENANCE_BADGE_LABELS else "unknown"
        lines.append(
            f'<div class="ws-uncertain-field">'
            f'<span class="ws-uncertain-badge ws-provenance-badge-{badge_class}">{badge_label}</span>'
            f"「{field_name}」</div>"
        )
    return "".join(lines)


_RISK_LEVEL_LABELS = {"low": "低风险", "medium": "中风险", "high": "高风险"}
_URGENCY_LABELS = {"low": "紧急度低", "medium": "紧急度中", "high": "紧急度高", "critical": "⚠️紧急度极高"}
_TREND_LABELS = {
    "accelerating": "📈 加速",
    "steady": "➡️ 匀速延续",
    "decelerating": "📉 放缓",
    "reversing": "🔄 已反转",
}
"""第五轮方案 5.3 节：`SimState.line_updates[line_id]["trend"]` 四档
取值 → 展示文案，供 `_render_causal_lines_overview()` 在因果线标题旁
渲染趋势徽章，样式复用既有 `ws-uncertain-badge` 胶囊底样，未识别的
取值不展示（`causal_tree.normalize_line_trend()` 已经在落盘前把非法
值剔除，这里的兜底只是双重保险）。"""
_REVERSIBILITY_LABELS = {
    "reversible": "可逆",
    "hard_to_reverse": "难以逆转",
    "irreversible": "不可逆",
}
_ACTION_TYPE_LABELS = {"combo": "🧩组合方案", "conditional": "🔀条件方案"}
"""`single`（默认）不展示标签——只有组合/条件这两种非默认取值才
值得提醒用户"这不是一个单一原子行动"（阶段三十三第六批，4.7 节）。
"""


def _option_meta_html(opt) -> str:
    """渲染一个 `ChoiceOption` 的结构化维度标签（阶段三十二，4.1 节：
    风险等级/可逆性/涉及因果线/最大不确定性；阶段三十三第二批，4.2/
    4.3 节新增行动理由/紧急程度/时间窗口；阶段三十三第六批，4.7 节
    新增 `action_type` 组合/条件方案标签，`single` 默认值不展示）。
    未声明的字段不展示，不用"未知"占位制造伪信息；全部字段都未声明
    时返回空字符串。
    """
    badges = []
    risk = getattr(opt, "risk_level", None)
    if risk:
        badges.append(
            f'<span class="ws-uncertain-badge ws-uncertain-badge-{risk if risk in _RISK_LEVEL_LABELS else "medium"}">'
            f'{_RISK_LEVEL_LABELS.get(risk, risk)}</span>'
        )
    reversibility = getattr(opt, "reversibility", None)
    if reversibility:
        badges.append(
            f'<span class="ws-uncertain-badge ws-uncertain-badge-medium">'
            f'{_html_text(_REVERSIBILITY_LABELS.get(reversibility, reversibility))}</span>'
        )
    urgency = getattr(opt, "urgency", None)
    if urgency:
        badges.append(
            f'<span class="ws-uncertain-badge ws-urgency-badge-{urgency if urgency in _URGENCY_LABELS else "medium"}">'
            f'{_URGENCY_LABELS.get(urgency, urgency)}</span>'
        )
    if str(getattr(opt, "id", "") or "").startswith("continue_"):
        badges.append('<span class="ws-uncertain-badge ws-continue-badge">维持现状</span>')
    action_type = getattr(opt, "action_type", "single") or "single"
    if action_type in _ACTION_TYPE_LABELS:
        badges.append(
            f'<span class="ws-uncertain-badge ws-uncertain-badge-medium">'
            f'{_ACTION_TYPE_LABELS[action_type]}</span>'
        )
    lines_html = ""
    affected = getattr(opt, "affected_lines", None) or []
    if affected:
        lines_html = (
            f'<div class="ws-muted">涉及因果线：{_html_text("、".join(affected))}</div>'
        )
    uncertainty = getattr(opt, "key_uncertainty", "") or ""
    uncertainty_html = (
        f'<div class="ws-muted">最大不确定性：{_html_text(uncertainty)}</div>' if uncertainty else ""
    )
    time_window = getattr(opt, "time_window", "") or ""
    time_window_html = (
        f'<div class="ws-muted">时间窗口：{_html_text(time_window)}</div>' if time_window else ""
    )
    action_reason = getattr(opt, "action_reason", "") or ""
    action_reason_html = (
        f'<div class="ws-muted">为什么值得考虑：{_html_text(action_reason)}</div>'
        if action_reason else ""
    )
    # 第五轮方案 5.1 节：`prerequisites`/`consequences`，字段为空/
    # None 时完全不渲染对应小节，不用"未知"占位制造伪信息。
    prerequisites = getattr(opt, "prerequisites", None) or []
    prerequisites_html = (
        f'<div class="ws-muted">前提条件：{_html_text("、".join(prerequisites))}</div>'
        if prerequisites else ""
    )
    consequences = getattr(opt, "consequences", None) or {}
    consequences_parts = []
    if consequences.get("short_term"):
        consequences_parts.append(f'短期：{_html_text(consequences["short_term"])}')
    if consequences.get("long_term"):
        consequences_parts.append(f'长期：{_html_text(consequences["long_term"])}')
    consequences_html = (
        f'<div class="ws-muted">{"　".join(consequences_parts)}</div>' if consequences_parts else ""
    )
    badges_html = f'<div>{"".join(badges)}</div>' if badges else ""
    return (
        badges_html
        + lines_html
        + uncertainty_html
        + time_window_html
        + action_reason_html
        + prerequisites_html
        + consequences_html
    )


def _key_drivers_html(state) -> str:
    """渲染这一步的"划重点"关键驱动因素标签（阶段十三，4.5 节；阶段
    十五，4.7 节新增可展开详情，可能为空）。放在叙事文本之前，让用户
    不用逐字读完 `narrative` 就能先扫一眼这一步的关键信息。

    如果这一条短语在 `causal_links` 里有对应的结构化说明（按 `driver`
    文本匹配），用原生 `<details>/<summary>` 渲染成可点击展开的标签，
    展开后显示"受影响字段"和"具体后果"；没有对应说明时退化为阶段
    十三的纯标签展示，不强制升级 UI 复杂度（向后兼容）。
    """
    drivers = getattr(state, "key_drivers", None) or []
    if not drivers:
        return ""
    links = getattr(state, "causal_links", None) or []
    link_by_driver: Dict[str, Dict[str, Any]] = {}
    for link in links:
        if isinstance(link, dict) and str(link.get("driver", "")).strip():
            link_by_driver[str(link["driver"]).strip()] = link

    tags = []
    for d in drivers:
        label = str(d).strip()
        if not label:
            continue
        link = link_by_driver.get(label)
        if link:
            affected = link.get("affected_fields") or []
            affected_text = "、".join(_html_text(str(f)) for f in affected) or "（未说明）"
            effect_text = _html_text(str(link.get("effect", "")).strip()) or "（未说明）"
            tags.append(
                '<details class="ws-key-driver-details">'
                f'<summary class="ws-key-driver-tag">🔑 {_html_text(label)}</summary>'
                f'<div class="ws-key-driver-detail-body">'
                f'<div><b>受影响字段：</b>{affected_text}</div>'
                f'<div><b>具体后果：</b>{effect_text}</div>'
                '</div></details>'
            )
        else:
            tags.append(f'<span class="ws-key-driver-tag">🔑 {_html_text(label)}</span>')
    if not tags:
        return ""
    return f'<div class="ws-key-drivers">{"".join(tags)}</div>'


def _choice_label(options, option_id: Optional[str]) -> str:
    """把候选选项 id 转成人类可读的 label 用于展示。

    `chosen_option_id` 记的是 id 不是文案（见 `state_model.SimState`
    docstring），展示时如果直接把 id 秀出来（如 `custom_2ca5f0`），
    对用户没有任何意义；这里从"做出选择的那个状态自己的候选列表"
    （`options` 参数）里查一次 label。理论上 `chosen_option_id` 总能在
    对应状态自己的 `options` 里找到（引擎落盘前已校验过，见
    `engine.py::advance()`），但展示层不应该假设数据一定完美——查不到
    时退回显示原始 id，而不是抛错或显示空白。
    """
    if not option_id:
        return ""
    for opt in options or []:
        if opt.id == option_id:
            return opt.label or option_id
    return option_id


@st.cache_resource(show_spinner=False)
def _load_cfg():
    """加载一次 mini_agent AppConfig 并缓存——LLM 调用配置不会在一次
    `streamlit run` 生命周期内变化，重复加载没有意义。统一走
    `world_simulator.config.load_llm_cfg()`，确保本项目未注册进 daemon
    时也能自动继承主项目的 LLM 配置，见该函数注释。"""
    from world_simulator.config import load_llm_cfg

    return load_llm_cfg()


def _safe_json_loads(text: str, fallback: Any) -> Any:
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001 — 表单里的手输 JSON，格式错误很常见
        return fallback


# ─────────────────────────────────────────────────────────────
# 页面：模拟列表
# ─────────────────────────────────────────────────────────────


def page_list() -> None:
    st.markdown("## 模拟列表", unsafe_allow_html=True)

    manifests = list_simulations(DATA_DIR)
    col_new, _ = st.columns([1, 4])
    with col_new:
        if st.button("＋ 新建模拟", use_container_width=True):
            st.session_state["view"] = "create"
            st.rerun()

    if not manifests:
        st.markdown(
            '<div class="ws-card"><span class="ws-muted">'
            "还没有任何模拟实例——点右上角「新建模拟」，用一句话开始第一个。"
            "</span></div>",
            unsafe_allow_html=True,
        )
        return

    for m in manifests:
        try:
            branch_count = len(bm.list_branches(DATA_DIR, m.sim_id))
        except Exception:  # noqa: BLE001
            # 分支数据本身不影响列表页其它信息的展示，读取失败（比如
            # 某个分支目录损坏）不应该让整个列表页挂掉，退化成"至少有
            # main 这一条"。
            branch_count = 1

        with st.container():
            st.markdown(
                f"""<div class="ws-card">
                    <div class="ws-card-title">{m.title}</div>
                    <div class="ws-muted">{_pill(m.status)}
                        模板：{m.template} · 第 {m.current_step} 步 ·
                        推进模式：{"自动挡" if m.pilot_mode == "autopilot" else "手动挡"} ·
                        {branch_count} 条分支 · 创建于 {_format_local_time(m.created_at)}
                    </div>
                </div>""",
                unsafe_allow_html=True,
            )
            c1, c2, c3 = st.columns([1, 1, 1])
            with c1:
                if st.button("打开", key=f"open_{m.sim_id}", use_container_width=True):
                    st.session_state["view"] = "detail"
                    st.session_state["sim_id"] = m.sim_id
                    st.rerun()
            with c2:
                with st.popover("✏️ 改标题", use_container_width=True):
                    new_title = st.text_input(
                        "新标题", value=m.title, key=f"rename_input_{m.sim_id}",
                    )
                    if st.button("保存", key=f"rename_save_{m.sim_id}"):
                        try:
                            rename_simulation(DATA_DIR, m.sim_id, new_title)
                        except SimEngineError as exc:
                            st.error(str(exc))
                        except SimNotFoundError as exc:
                            st.error(str(exc))
                        else:
                            st.rerun()
            with c3:
                with st.popover("🗑 删除", use_container_width=True):
                    st.markdown(
                        f'<div class="ws-danger-zone"><b>删除「{m.title}」？</b>'
                        "<div class=\"ws-muted\">此操作不可逆，会连同全部分支/"
                        "历史一起删除。如果只是不想要某条时间线的后续走向，"
                        "去实例详情页的「分支」区块从历史节点分叉即可，原"
                        "时间线不会被销毁；真正确定不再需要一个实例时才用"
                        "这里的删除。</div></div>",
                        unsafe_allow_html=True,
                    )
                    confirmed = st.checkbox(
                        "我确认要删除这个实例", key=f"list_confirm_{m.sim_id}",
                    )
                    if st.button(
                        "确认删除", key=f"list_delete_{m.sim_id}", disabled=not confirmed,
                    ):
                        try:
                            delete_simulation(DATA_DIR, m.sim_id)
                        except SimNotFoundError as exc:
                            st.error(str(exc))
                        else:
                            st.success(f"已删除「{m.title}」。")
                            st.rerun()


# ─────────────────────────────────────────────────────────────
# 页面：创建向导
# ─────────────────────────────────────────────────────────────


def _new_custom_option_id(existing: List[ChoiceOption]) -> str:
    """给用户手动添加的候选方向生成一个不与现有 id 冲突的 id。"""
    import uuid

    existing_ids = {o.id for o in existing}
    while True:
        candidate = f"custom_{uuid.uuid4().hex[:6]}"
        if candidate not in existing_ids:
            return candidate


def page_create() -> None:
    st.markdown("## 创建向导", unsafe_allow_html=True)
    st.markdown(
        '<span class="ws-muted">用一句话描述你想模拟的处境，引擎会先给出一份'
        "提案草稿（初始状态 + 关键变量 + 可能方向），你可以编辑、补充意见让它"
        "重新生成，或者直接选定一个方向后再确认创建——不需要从零填表单。</span>",
        unsafe_allow_html=True,
    )

    intent = st.text_area(
        "一句话模拟意图",
        value=st.session_state.get("create_intent", ""),
        placeholder="例：模拟一个刚从计算机专业毕业、在读研和工作之间犹豫的年轻人的人生",
        height=90,
    )
    template = st.selectbox(
        "场景模板", options=["life_sim", "group_evolution", "negotiation"],
        format_func=lambda t: {
            "life_sim": "人生模拟", "group_evolution": "群体演化",
            "negotiation": "多方谈判（多主体，阶段十七）",
        }.get(t, t),
    )

    if template == "negotiation":
        st.markdown(
            '<span class="ws-muted">「多方谈判」模板会启用多主体模式（阶段十七，4.9 节）：'
            "`vars` 按「各主体私有信息 + 共享信息」的结构组织，详情页会按主体分 tab 展示，"
            "互不泄露。</span>",
            unsafe_allow_html=True,
        )

    setting_cols = st.columns([1, 1])
    with setting_cols[0]:
        options_count = st.number_input(
            "候选方向数量上限（软上限，实际数量由情境决定）", min_value=2, max_value=8,
            value=int(st.session_state.get("create_options_count", 4)), step=1,
        )
    with setting_cols[1]:
        _MODE_LABELS = {
            "auto": "自动（推荐——AI 按情境自行判断，同一次模拟允许切换）",
            "guided": "引导（给一个偏好/基准，AI 仍自行判断）",
            "fixed": "固定（每一步都用同一个粒度，不会切换）",
        }
        mode_default = st.session_state.get("create_granularity_mode", "auto")
        time_granularity_mode = st.selectbox(
            "时间粒度模式", options=list(_MODE_LABELS),
            index=list(_MODE_LABELS).index(mode_default) if mode_default in _MODE_LABELS else 0,
            format_func=lambda m: _MODE_LABELS[m],
        )

    split_decision_calls = st.checkbox(
        "拆分为「世界演化」+「决策生成」两次调用（阶段三十三第八批，4.12 节"
        "第三步——更贴合「决策引擎独立于世界演化」的设计，但每步推进会翻倍"
        "延迟和 token 成本，默认开启；可以在创建后随时在详情页「模拟设置」"
        "里开关，下一步推进开始生效）",
        value=bool(st.session_state.get("create_split_decision_calls", True)),
    )
    split_creation_calls = st.checkbox(
        "创建这一步拆分为「世界构建」+「因果空间构建」两次调用（第五轮方案"
        "5.5 节，阶段三十四第六批——更贴合参考文档「World State Builder」"
        "与「Causal Line Generator + 候选行动生成」的分工设想，但创建这一步"
        "会翻倍延迟和 token 成本，默认开启；只影响这一次创建，不影响之后的"
        "推进）",
        value=bool(st.session_state.get("create_split_creation_calls", True)),
    )
    observer_mode = st.checkbox(
        "🔭 Observer 模式（世界独立演化最小实验，阶段三十一 4.25 节——仅自动挡"
        "下生效，提示 AI 优先让背景/宏观因果线自然演化、尽量不产生需要立刻"
        "打断的重大决策分支，不强制约束，默认开启；可以在创建后随时在详情页"
        "「模拟设置」里开关）",
        value=bool(st.session_state.get("create_observer_mode", True)),
    )

    time_granularity = ""
    time_granularity_guide = ""
    if time_granularity_mode == "fixed":
        granularity_presets = ["1 天", "1 周", "1 个月", "1 个季度", "1 年", "5 年", "10 年", "自定义…"]
        preset_default = st.session_state.get("create_granularity_preset", "1 年")
        granularity_preset = st.selectbox(
            "固定粒度（每一步都严格按这个跨度推进）", options=granularity_presets,
            index=granularity_presets.index(preset_default) if preset_default in granularity_presets else 0,
        )
        if granularity_preset == "自定义…":
            time_granularity = st.text_input(
                "自定义时间粒度", value=st.session_state.get("create_granularity_custom", ""),
                placeholder="例：3 个月 / 一场谈判的一轮 / 半局比赛",
            )
        else:
            time_granularity = granularity_preset
        st.session_state["create_granularity_preset"] = granularity_preset
    elif time_granularity_mode == "guided":
        time_granularity_guide = st.text_area(
            "节奏偏好/基准（不是精确值，AI 仍会自行判断，只是参考这个方向）",
            value=st.session_state.get("create_granularity_guide", ""),
            placeholder="例：日常按季度推进，遇到谈判/冲突等关键场景可以细到按轮次或周",
            height=70,
        )
        st.markdown(
            '<span class="ws-muted">留空等同于「自动」模式。</span>', unsafe_allow_html=True,
        )
    else:  # auto
        st.markdown(
            '<span class="ws-muted">AI 会按情境自行选择每一步的时间跨度，比如日常按年推进，'
            "遇到谈判/危机等密集情境时可能临时切到按轮次/周推进，事后再切回来——不需要"
            "预先指定，时间线上会标出每次切换的原因。</span>",
            unsafe_allow_html=True,
        )
    st.markdown(
        '<span class="ws-muted">这些设置会一起存进这个模拟实例的设置里，后面每一步推进'
        "都沿用；创建之后也可以在详情页里改（下一步开始生效，不影响已经推进过的历史）。"
        "</span>",
        unsafe_allow_html=True,
    )

    gen_col, back_col = st.columns([1, 1])
    with gen_col:
        gen_clicked = st.button("生成提案草稿", type="primary", use_container_width=True)
    with back_col:
        if st.button("← 返回列表", use_container_width=True):
            st.session_state["view"] = "list"
            st.rerun()

    if gen_clicked:
        if not intent.strip():
            st.warning("请先输入一句话模拟意图。")
        else:
            st.session_state["create_intent"] = intent
            st.session_state["create_options_count"] = int(options_count)
            st.session_state["create_granularity_mode"] = time_granularity_mode
            st.session_state["create_split_decision_calls"] = bool(split_decision_calls)
            st.session_state["create_split_creation_calls"] = bool(split_creation_calls)
            st.session_state["create_observer_mode"] = bool(observer_mode)
            if time_granularity_mode == "fixed":
                st.session_state["create_granularity_custom"] = time_granularity
            elif time_granularity_mode == "guided":
                st.session_state["create_granularity_guide"] = time_granularity_guide
            settings = {
                "options_count": int(options_count),
                "time_granularity_mode": time_granularity_mode,
                "time_granularity": time_granularity,
                "time_granularity_guide": time_granularity_guide,
                "multi_entity_mode": template == "negotiation",
                "split_decision_calls": bool(split_decision_calls),
                "split_creation_calls": bool(split_creation_calls),
                "observer_mode": bool(observer_mode),
            }
            st.session_state["create_settings"] = settings
            with st.spinner("正在生成提案草稿..."):
                try:
                    cfg = _load_cfg()
                    draft = generate_scenario(
                        cfg, PROJECT_ROOT, template=template, intent=intent, settings=settings,
                        data_dir=DATA_DIR,
                    )
                    st.session_state["draft"] = draft
                    st.session_state["draft_template"] = template
                    # 新一轮从零生成，之前的候选方向选择/意见输入都失效。
                    for key in ("create_chosen_option_id", "create_feedback"):
                        st.session_state.pop(key, None)
                except ScenarioGenerationError as exc:
                    st.error(f"生成草稿失败：{exc}")
                except ImportError as exc:
                    st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")

    draft: Optional[ScenarioDraft] = st.session_state.get("draft")
    if draft is None:
        return

    st.markdown("### 提案草稿（可编辑）")
    edited_title = st.text_input("标题", value=draft.title)
    edited_summary = st.text_area("初始状态摘要", value=draft.summary, height=70)
    if draft.field_provenance:
        # 阶段三十二（4.2 节）：每个顶层字段的来源标注，帮用户分清
        # "我说了啥、系统猜了啥"。纯展示，不提供额外的修正入口——
        # 编辑就用下面已有的"关键变量（JSON）"文本框，改了值之后是否
        # 需要把标签升级成"已确认"由用户自己判断，不做自动化推断。
        _PROVENANCE_LABELS = {
            "fact": "🟢 你说的",
            "assumption": "🟡 系统假设",
            "inference": "🔵 系统推断",
            "unknown": "⚪ 未知，先占位",
        }
        tags = " ".join(
            f"`{field_name}`: {_PROVENANCE_LABELS.get(src, src)}"
            for field_name, src in draft.field_provenance.items()
        )
        st.caption("字段来源标注：" + tags)
    edited_vars_text = st.text_area(
        "关键变量（JSON）", value=json.dumps(draft.vars, ensure_ascii=False, indent=2), height=160
    )

    # ── 资源类字段（阶段九，4.1 节）：声明后引擎会在每步推进时自动把
    # 这些字段夹到下限，不需要每次都填，留空则不做任何校验 ──
    resource_fields_default = ", ".join(
        (f.get("field", "") if isinstance(f, dict) else str(f))
        for f in (getattr(draft, "resource_fields", None) or [])
    )
    resource_fields_text = st.text_input(
        "资源类字段（逗号分隔，可选——引擎会自动校验这些字段不低于 0，比如「资金/储蓄」）",
        value=st.session_state.get("create_resource_fields", resource_fields_default),
        placeholder="例：resources.cash, resources.energy",
    )
    st.markdown(
        '<span class="ws-muted">这里填的是 `vars` 里的字段路径（嵌套字段用 `.` 连接，'
        "比如 `resources.cash`）。之后每一步推进，如果 AI 把这个字段算成负数，"
        "系统会自动纠正为 0 并在时间线上高亮提示，不影响推进本身。留空表示不做校验。"
        "</span>",
        unsafe_allow_html=True,
    )

    # ── 认知偏差声明字段（阶段三十四，4.3 节第二批）：声明后角色对这些
    # 字段的认知可能与真实值不同，详情页会做"真实 vs 你以为"对比。同
    # resource_fields 的既有 UI 模式：skill 在草稿里给建议值，这里展示
    # 出来允许用户编辑，最终结果随创建一起存进 settings.belief_fields。
    # 也可以留到创建之后再在"⚙️ 模拟设置"里补充声明（事后声明只对
    # 之后新产生的 step 生效，不回填历史）。 ──
    belief_fields_default = ", ".join(
        str(f) for f in (getattr(draft, "belief_fields", None) or [])
    )
    belief_fields_text = st.text_input(
        "认知偏差声明字段（逗号分隔，可选——声明后角色对这些字段的认知"
        "可能与真实值不同，展示层会做「真实 vs 你以为」对比）",
        value=st.session_state.get("create_belief_fields", belief_fields_default),
        placeholder="例：market_demand, competitor_strength",
    )
    if getattr(draft, "beliefs", None):
        st.caption(
            "AI 给出的初始认知偏差：" + "，".join(
                f"`{k}` 你以为 {v}" for k, v in draft.beliefs.items()
            )
        )

    # ── 资源转移关系（阶段十六，4.8 节）：声明后引擎会在每步推进时
    # 检查两个字段的变化量是否大致相反，不修改任何数值，只做不一致
    # 提示，留空则不做任何检查 ──
    with st.expander("高级：声明资源转移关系（阶段十六，可选）"):
        st.markdown(
            '<span class="ws-muted">用 JSON 数组声明两个资源字段之间的"转移"关系'
            "（比如\"花现金买库存\"），引擎会检查变化量是否大致相反（默认允许 10% "
            "偏差），不一致时只在时间线上提示，不会修改任何数值。例如：\n"
            '`[{"type": "transfer", "from": "resources.cash", '
            '"to": "resources.inventory", "tolerance": 0.1}]`。'
            "不填就跳过这一步。</span>",
            unsafe_allow_html=True,
        )
        resource_relations_default = json.dumps(
            list(getattr(draft, "resource_relations", None) or []), ensure_ascii=False
        ) if getattr(draft, "resource_relations", None) else ""
        resource_relations_text = st.text_area(
            "资源转移关系（JSON 数组，可选）",
            value=st.session_state.get("create_resource_relations", resource_relations_default),
            key="create_resource_relations_input",
            height=80,
            placeholder='[{"type": "transfer", "from": "cash", "to": "inventory.value"}]',
        )

    # ── 多尺度因果线（阶段二十二，4.13 节）：声明后 skill 会按需在
    # 每一步推进时给出 line_updates（每条线各自的时间点/摘要/是否
    # 推进），不声明则完全沿用单一 time_granularity 的既有行为 ──
    with st.expander("高级：手动调整因果线声明（可选，因果线本身默认就有）"):
        st.markdown(
            '<span class="ws-muted">创建模拟就会有核心因果线，每条线还自带一棵初始的'
            "\"未来因果树\"（`future_tree.branches`，几个有区分度的可能发展方向，不是"
            "只有一条延续路径）——AI 已经按下方草稿给出了建议，就算留空/AI 没给全，"
            "系统也会用通用模板兜底补全，保证不会出现\"没有因果线\"的情况。这里是"
            "手动覆盖/精修的入口：可以改线名/节奏，也可以直接编辑每条线的 "
            "future_tree.branches（每个分支形如 "
            '{"id": ..., "description": ..., "likelihood": "high"|"medium"|"low"}'
            "）。留空表示完全交给系统的默认草稿+兜底模板，不是关闭这个功能。</span>",
            unsafe_allow_html=True,
        )
        causal_lines_default = json.dumps(
            causal_tree.ensure_future_trees(getattr(draft, "causal_lines", None), as_of_step=0),
            ensure_ascii=False,
        )
        causal_lines_text = st.text_area(
            "因果线声明（含未来因果树，JSON 数组）",
            value=st.session_state.get("create_causal_lines", causal_lines_default),
            key="create_causal_lines_input",
            height=140,
            placeholder='[{"id": "tech", "label": "技术线", "time_granularity": "年", '
            '"future_tree": {"branches": [{"id": "fast", "description": "快速发展", '
            '"likelihood": "medium"}]}}]',
        )

    # ── 因果图先验声明（第五轮方案 5.4 节）：区别于「因果线总览」里
    # 能看到的"实际发生过"的历史统计，这里声明的是"这条线一般来说会
    # 影响那条线"这类不依赖本次模拟历史数据就成立的常识性结构 ──
    with st.expander("高级：手动调整因果图先验声明（第五轮方案 5.4 节，可选）"):
        st.markdown(
            '<span class="ws-muted">AI 已经按下方草稿给出了建议（因果线之间存在明显的、'
            "创建时就能判断的先验关系时才会给，没有的话留空是正常情况）——两段提示都会"
            "喂给下一步推进，由 AI 自己判断参考权重，不会强制先验优先于实际发生过的"
            "历史统计。留空表示没有值得声明的先验关系。</span>",
            unsafe_allow_html=True,
        )
        declared_causal_graph_default = json.dumps(
            getattr(draft, "declared_causal_graph", None) or [], ensure_ascii=False
        )
        declared_causal_graph_text = st.text_area(
            "因果图先验声明（JSON 数组，可选）",
            value=st.session_state.get("create_declared_causal_graph", declared_causal_graph_default),
            key="create_declared_causal_graph_input",
            height=80,
            placeholder='[{"from_line_id": "tech", "to_line_id": "industry", '
            '"note": "技术突破通常先影响行业格局"}]',
        )

    # ── Reality Sync 轻量版（阶段十八，4.11 节）：用户手动填一句真实
    # 世界参考信息，原样喂给 prompt，系统不做任何自动数据抓取/校准 ──
    with st.expander("高级：填入真实世界参考信息（阶段十八，可选）"):
        st.markdown(
            '<span class="ws-muted">填一句真实世界的参考数据（比如"参考：2024 年'
            '一线城市应届硕士平均起薪 1.2~1.8 万/月"），会原样传给 AI，'
            '供推演时"参考但不照抄"，不会做任何自动抓取/强制校准，'
            "不填就跳过这一步。</span>",
            unsafe_allow_html=True,
        )
        calibration_notes_text = st.text_area(
            "真实世界参考信息（可选）",
            value=st.session_state.get("create_calibration_notes", ""),
            key="create_calibration_notes_input",
            height=68,
            placeholder="参考：2024 年一线城市应届硕士平均起薪 1.2~1.8 万/月",
        )

    # ── Hierarchical Agent 设计草案第一步（阶段十九，4.10 节）：把部分
    # 主体声明为"背景角色"，engine 用简单规则外推强制覆盖 LLM 输出，
    # 只有 multi_entity_mode 场景（比如「多方谈判」模板）下才有意义 ──
    with st.expander("高级：声明背景角色，简化其推理（阶段十九，可选）"):
        st.markdown(
            '<span class="ws-muted">只对"多主体模式"（比如「多方谈判」模板）有意义：'
            "声明的主体名字（需要和 vars.entities 里的主体名字完全一致）会被当成"
            '"背景角色"——engine 每步用简单线性趋势外推它们的数值，'
            "**不采纳 AI 对它们的推理结果**，用来把推理精力留给关键角色。"
            "不填就跳过这一步。</span>",
            unsafe_allow_html=True,
        )
        background_entities_text = st.text_input(
            "背景角色名字（逗号分隔，可选）",
            value=st.session_state.get("create_background_entities", ""),
            key="create_background_entities_input",
            placeholder="背景NPC, 围观群众",
        )

    # ── 关注指标（阶段十二，4.4 节 Problem Compiler 雏形）：纯记录用途，
    # 不触发任何自动排序/推荐，只是给「对比实验」页面的关注字段提供
    # 默认值参考，留空不影响任何行为 ──
    objectives_default = ", ".join(
        str(o) for o in (getattr(draft, "objectives", None) or []) if not isinstance(o, dict)
    )
    objectives_text = st.text_input(
        "关注指标（逗号分隔，可选——只是记录这次模拟主要想看什么，"
        "不要求是精确字段名）",
        value=st.session_state.get("create_objectives", objectives_default),
        placeholder="例：资产净值, 工作满意度, 健康水平",
    )
    st.markdown(
        '<span class="ws-muted">记下来之后，「对比实验」页面的关注字段会默认带出这里的内容'
        "（可以再改）。</span>",
        unsafe_allow_html=True,
    )
    with st.expander("高级：声明可排序字段（阶段十四，可选）"):
        st.markdown(
            '<span class="ws-muted">上面填的是纯文字说明，不参与排序。如果想让'
            "「对比实验」页面按某个具体字段自动排序（仅供参考，不代表最优解），"
            "在这里用 JSON 数组声明，比如：\n"
            '`[{"label": "资产净值", "field": "resources.cash", '
            '"direction": "max"}]`。不填就跳过这一步，行为与阶段十二完全一致。'
            "</span>",
            unsafe_allow_html=True,
        )
        advanced_objectives_text = st.text_area(
            "结构化关注指标（JSON 数组，可选）",
            value=st.session_state.get("create_objectives_advanced", ""),
            key="create_objectives_advanced_input",
            height=80,
            placeholder='[{"label": "资产净值", "field": "resources.cash", "direction": "max"}]',
        )

    # ── 初始候选方向：可编辑文案、可删除、可手动新增，并且真的可以选 ──
    st.markdown("**初始候选方向**")
    st.markdown(
        '<span class="ws-muted">这些是模拟正式开始后，第一次推进时可以选的方向；'
        "可以直接编辑文案，删掉不想要的，或者在下面手动加一个自己想要的方向。"
        "选中某一项后，创建时可以选择「按此方向直接推进第一步」。</span>",
        unsafe_allow_html=True,
    )

    chosen_option_id = st.session_state.get("create_chosen_option_id")
    remaining_options: List[ChoiceOption] = []
    remove_id: Optional[str] = None
    for opt in draft.options:
        with st.container():
            st.markdown(f'<div class="ws-card">', unsafe_allow_html=True)
            row = st.columns([5, 5, 2, 2])
            with row[0]:
                new_label = st.text_input(
                    "方向名称", value=opt.label, key=f"opt_label_{opt.id}", label_visibility="collapsed",
                )
            with row[1]:
                new_desc = st.text_input(
                    "方向说明", value=opt.description, key=f"opt_desc_{opt.id}", label_visibility="collapsed",
                )
            with row[2]:
                is_chosen = st.checkbox(
                    "选定", value=(chosen_option_id == opt.id), key=f"opt_pick_{opt.id}",
                )
            with row[3]:
                if st.button("✕ 移除", key=f"opt_remove_{opt.id}"):
                    remove_id = opt.id
            st.markdown("</div>", unsafe_allow_html=True)
            remaining_options.append(ChoiceOption(id=opt.id, label=new_label, description=new_desc))
            if is_chosen:
                chosen_option_id = opt.id
            elif chosen_option_id == opt.id:
                chosen_option_id = None

    if remove_id is not None:
        remaining_options = [o for o in remaining_options if o.id != remove_id]
        if chosen_option_id == remove_id:
            chosen_option_id = None

    draft.options = remaining_options
    st.session_state["create_chosen_option_id"] = chosen_option_id

    with st.expander("+ 手动添加一个候选方向"):
        add_cols = st.columns([5, 5, 2])
        with add_cols[0]:
            manual_label = st.text_input("方向名称", key="manual_opt_label", label_visibility="collapsed", placeholder="方向名称")
        with add_cols[1]:
            manual_desc = st.text_input("方向说明", key="manual_opt_desc", label_visibility="collapsed", placeholder="方向说明（可选）")
        with add_cols[2]:
            if st.button("添加", key="manual_opt_add"):
                if not manual_label.strip():
                    st.warning("请先填写方向名称。")
                else:
                    new_id = _new_custom_option_id(draft.options)
                    draft.options.append(ChoiceOption(id=new_id, label=manual_label.strip(), description=manual_desc.strip()))
                    st.session_state["draft"] = draft
                    st.rerun()

    more_cols = st.columns([3, 5, 4])
    with more_cols[0]:
        more_count = st.number_input(
            "新增数量", min_value=1, max_value=5, value=2, step=1,
            key="more_opt_count", label_visibility="collapsed",
        )
    with more_cols[1]:
        generate_more_clicked = st.button("🤖 让引擎再想几个方向", key="generate_more_options")
    with more_cols[2]:
        st.markdown(
            '<span class="ws-muted">保留现有方向不变，只在后面补充新的候选方向。</span>',
            unsafe_allow_html=True,
        )

    if generate_more_clicked:
        with st.spinner("正在生成更多候选方向..."):
            try:
                cfg = _load_cfg()
                existing_ids = {o.id for o in draft.options}
                revised = generate_scenario(
                    cfg, PROJECT_ROOT,
                    template=st.session_state.get("draft_template", template),
                    intent=st.session_state.get("create_intent", intent),
                    feedback=(
                        f"不要删除、修改或替换任何一个已有的候选方向，保持它们的 id/文案"
                        f"原样不变；只在已有方向的基础上，额外再新增 {int(more_count)} 个方向"
                        f"明显不同、彼此也不重复的新候选方向，追加到 options 数组末尾。"
                    ),
                    previous_draft=draft,
                    settings=st.session_state.get("create_settings"),
                    data_dir=DATA_DIR,
                )
            except ScenarioGenerationError as exc:
                st.error(f"生成更多候选方向失败：{exc}")
            except ImportError as exc:
                st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")
            else:
                # 即使 skill 没完全遵守"不要改已有项"的要求，这里也只取真正
                # 新增的部分（id 不在原有列表里的），已有方向和用户在上面做的
                # 编辑/删除/手动新增一律保留，不被这次调用覆盖掉。
                new_ones = [o for o in revised.options if o.id not in existing_ids]
                if not new_ones:
                    st.warning("引擎这次没有给出新的候选方向，换一种说法或稍后再试试。")
                else:
                    draft.options = draft.options + new_ones
                    st.session_state["draft"] = draft
                    st.rerun()

    # ── 根据意见重新生成草稿 ──
    with st.expander("对草稿不满意？输入意见让它重新生成"):
        feedback = st.text_area(
            "补充意见",
            value=st.session_state.get("create_feedback", ""),
            placeholder="例：把候选方向里的「继续读研」去掉，换成一个「先工作两年再看」的方向；"
            "初始存款调低一些",
            height=80,
            key="create_feedback_input",
        )
        if st.button("根据意见重新生成草稿"):
            if not feedback.strip():
                st.warning("请先输入具体意见，否则和「重新生成提案草稿」没有区别。")
            else:
                st.session_state["create_feedback"] = feedback
                with st.spinner("正在根据意见修改草稿..."):
                    try:
                        cfg = _load_cfg()
                        revised = generate_scenario(
                            cfg, PROJECT_ROOT,
                            template=st.session_state.get("draft_template", template),
                            intent=st.session_state.get("create_intent", intent),
                            feedback=feedback,
                            previous_draft=draft,
                            settings=st.session_state.get("create_settings"),
                            data_dir=DATA_DIR,
                        )
                        st.session_state["draft"] = revised
                        st.session_state.pop("create_chosen_option_id", None)
                    except ScenarioGenerationError as exc:
                        st.error(f"根据意见修改草稿失败：{exc}")
                    except ImportError as exc:
                        st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")
                    else:
                        st.rerun()

    st.markdown("---")
    confirm_cols = st.columns([1, 1])
    with confirm_cols[0]:
        confirm_only_clicked = st.button("确认创建（方向留到之后再选）", use_container_width=True)
    with confirm_cols[1]:
        advance_after_create = st.button(
            "确认创建并按选定方向直接推进第一步",
            type="primary",
            use_container_width=True,
            disabled=chosen_option_id is None,
        )
        if chosen_option_id is None:
            st.markdown(
                '<span class="ws-muted">先在上面勾选一个方向的「选定」，才能用这个按钮。</span>',
                unsafe_allow_html=True,
            )

    if confirm_only_clicked or advance_after_create:
        edited_vars = _safe_json_loads(edited_vars_text, None)
        advanced_objectives = _safe_json_loads(advanced_objectives_text, None)
        advanced_objectives_invalid = bool(advanced_objectives_text.strip()) and not isinstance(
            advanced_objectives, list
        )
        resource_relations_parsed = _safe_json_loads(resource_relations_text, None)
        resource_relations_invalid = bool(resource_relations_text.strip()) and not isinstance(
            resource_relations_parsed, list
        )
        causal_lines_parsed = _safe_json_loads(causal_lines_text, None)
        causal_lines_invalid = bool(causal_lines_text.strip()) and not isinstance(
            causal_lines_parsed, list
        )
        declared_causal_graph_parsed = _safe_json_loads(declared_causal_graph_text, None)
        declared_causal_graph_invalid = bool(declared_causal_graph_text.strip()) and not isinstance(
            declared_causal_graph_parsed, list
        )
        if edited_vars is None:
            st.error("关键变量不是合法 JSON，请修正后再确认创建。")
        elif advanced_objectives_invalid:
            st.error("结构化关注指标不是合法的 JSON 数组，请修正后再确认创建（或清空这一栏跳过）。")
        elif resource_relations_invalid:
            st.error("资源转移关系不是合法的 JSON 数组，请修正后再确认创建（或清空这一栏跳过）。")
        elif causal_lines_invalid:
            st.error("因果线声明不是合法的 JSON 数组，请修正后再确认创建（或清空这一栏跳过）。")
        elif declared_causal_graph_invalid:
            st.error("因果图先验声明不是合法的 JSON 数组，请修正后再确认创建（或清空这一栏跳过）。")
        else:
            resource_fields = [
                f.strip() for f in resource_fields_text.split(",") if f.strip()
            ]
            belief_fields = [
                f.strip() for f in belief_fields_text.split(",") if f.strip()
            ]
            objectives = [o.strip() for o in objectives_text.split(",") if o.strip()]
            if isinstance(advanced_objectives, list):
                objectives = objectives + [o for o in advanced_objectives if isinstance(o, dict)]
            resource_relations = (
                [r for r in resource_relations_parsed if isinstance(r, dict)]
                if isinstance(resource_relations_parsed, list) else []
            )
            causal_lines = (
                [line for line in causal_lines_parsed if isinstance(line, dict)]
                if isinstance(causal_lines_parsed, list) else []
            )
            declared_causal_graph = (
                [item for item in declared_causal_graph_parsed if isinstance(item, dict)]
                if isinstance(declared_causal_graph_parsed, list) else []
            )
            create_settings = dict(st.session_state.get("create_settings") or {})
            create_settings["resource_fields"] = resource_fields
            create_settings["belief_fields"] = belief_fields
            create_settings["resource_relations"] = resource_relations
            create_settings["causal_lines"] = causal_lines
            create_settings["declared_causal_graph"] = declared_causal_graph
            create_settings["objectives"] = objectives
            create_settings["calibration_notes"] = calibration_notes_text.strip()
            background_entities = [
                e.strip() for e in background_entities_text.split(",") if e.strip()
            ]
            create_settings["hierarchical_agent_mode"] = bool(background_entities)
            create_settings["background_entities"] = background_entities
            manifest = materialize_simulation(
                DATA_DIR,
                template=st.session_state.get("draft_template", template),
                intent=st.session_state.get("create_intent", intent),
                title=edited_title,
                summary=edited_summary,
                vars=edited_vars,
                options=draft.options,
                settings=create_settings,
                time_label=draft.time_label,
                time_granularity=draft.time_granularity,
                field_provenance=draft.field_provenance,
                beliefs=draft.beliefs,
            )
            sim_id = manifest.sim_id
            if advance_after_create and chosen_option_id is not None:
                with st.spinner("正在按选定方向推进第一步..."):
                    try:
                        cfg = _load_cfg()
                        advance(
                            cfg, PROJECT_ROOT, DATA_DIR, sim_id,
                            choice_option_id=chosen_option_id, chosen_by="user",
                        )
                    except (SimEngineError, ScenarioGenerationError) as exc:
                        st.error(f"实例已创建（{sim_id}），但按选定方向推进第一步失败：{exc}")
                    except ImportError as exc:
                        st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")
            for key in (
                "draft", "draft_template", "create_intent", "create_chosen_option_id",
                "create_feedback", "create_settings", "create_options_count",
                "create_granularity_preset", "create_granularity_custom", "create_resource_fields",
                "create_resource_relations", "create_objectives", "create_calibration_notes",
                "create_background_entities", "create_causal_lines", "create_belief_fields",
                "create_split_decision_calls", "create_declared_causal_graph",
                "create_split_creation_calls", "create_observer_mode",
            ):
                st.session_state.pop(key, None)
            st.session_state["view"] = "detail"
            st.session_state["sim_id"] = sim_id
            st.rerun()


# ─────────────────────────────────────────────────────────────
# 页面：实例详情 / 推进面板
# ─────────────────────────────────────────────────────────────


def _line_updates_html(state, causal_lines_meta: Optional[List[Dict[str, Any]]] = None) -> str:
    """渲染这一步的\"因果线进展\"标签（阶段二十二，4.13 节，多尺度因果
    线：Causal Line 成为一等公民）。只展示这一步真的\"有动静\"的线
    （`advanced` 不为 `False`），每个标签形如\"📈 技术线 · 第 2 年：
    AI 成本持续下降\"。`causal_lines_meta` 是
    `manifest.settings.causal_lines`（可能为 None/空），用于把
    line_id 换成人类可读的 `label`；找不到对应声明时退化为直接展示
    line_id 本身，不影响展示（向后兼容旧数据/未声明的线）。
    """
    line_updates = getattr(state, "line_updates", None) or {}
    if not line_updates:
        return ""
    label_by_id = {
        str(line.get("id")): str(line.get("label") or line.get("id"))
        for line in (causal_lines_meta or [])
        if isinstance(line, dict) and line.get("id")
    }
    tags = []
    for line_id, update in line_updates.items():
        if not isinstance(update, dict):
            continue
        if update.get("advanced") is False:
            continue
        label = label_by_id.get(str(line_id), str(line_id))
        time_label = str(update.get("time_label") or "").strip()
        summary = str(update.get("summary") or "").strip()
        detail = " · ".join(x for x in (time_label, summary) if x)
        text = f"{label}：{detail}" if detail else label
        tags.append(f'<span class="ws-key-driver-tag">📈 {_html_text(text)}</span>')
    if not tags:
        return ""
    return f'<div class="ws-key-drivers">{"".join(tags)}</div>'


_STRUCTURAL_CHANGE_KIND_LABELS = {
    "new_entity": "🆕 新实体",
    "new_mechanism": "⚙️ 新机制",
    "regime_shift": "🌐 运行规则切换",
}


def _structural_change_html(state) -> str:
    """渲染这一步的"结构性变化"提示（阶段二十三，4.14 节，Model
    Regime Detection / Emergence）。只负责展示文字部分——是否已采纳的
    标记、"采纳"按钮由 `_render_timeline` 在调用这个函数之后另外用
    `st.button` 渲染（HTML 字符串里不能放 Streamlit 组件）。
    """
    change = getattr(state, "structural_change", None)
    if not isinstance(change, dict) or not change.get("description"):
        return ""
    kind_label = _STRUCTURAL_CHANGE_KIND_LABELS.get(
        str(change.get("kind") or ""), "🆕 结构性变化"
    )
    accepted = bool(change.get("accepted"))
    status = "（已采纳为正式结构）" if accepted else "（待确认，见下方按钮）"
    text = f"{kind_label}：{change.get('description')}{status}"
    return f'<div class="ws-key-drivers"><span class="ws-key-driver-tag">{_html_text(text)}</span></div>'


def _tree_updates_html(state, causal_lines_meta: Optional[List[Dict[str, Any]]] = None) -> str:
    """渲染这一步对因果线"未来树"的修正摘要（阶段二十六，`next_doc/
    world_simulator_causal_line_future_tree_plan.md`），对应
    `SimState.tree_updates`。没有任何修正时返回空字符串。"""
    updates = getattr(state, "tree_updates", None) or []
    if not updates:
        return ""
    label_by_id = {
        str(line.get("id")): str(line.get("label") or line.get("id"))
        for line in (causal_lines_meta or [])
        if isinstance(line, dict) and line.get("id")
    }
    parts = []
    for item in updates:
        if not isinstance(item, dict):
            continue
        line_id = str(item.get("line_id") or "")
        label = label_by_id.get(line_id, line_id)
        pieces = []
        if item.get("confirmed_branch"):
            pieces.append(f"印证了分支「{item['confirmed_branch']}」")
        pruned = item.get("pruned_branches") or []
        if pruned:
            pieces.append(f"排除了分支「{'、'.join(str(x) for x in pruned)}」")
        new_ids = item.get("new_branch_ids") or []
        if new_ids:
            pieces.append(f"新增了分支「{'、'.join(str(x) for x in new_ids)}」")
        if pieces:
            parts.append(f"{label}：{'；'.join(pieces)}")
    if not parts:
        return ""
    text = "🌳 未来树更新 — " + "；".join(parts)
    return f'<div class="ws-key-drivers"><span class="ws-key-driver-tag">{_html_text(text)}</span></div>'


def _render_timeline(
    history: List,
    *,
    sim_id: Optional[str] = None,
    source_branch: Optional[str] = None,
    causal_lines_meta: Optional[List[Dict[str, Any]]] = None,
    causal_line_filter: Optional[str] = None,
) -> None:
    """渲染时间线。

    `sim_id` + `source_branch` 同时给出时，每条节点下面会带一个「创建
    分支」按钮——语义是"在这个决策发生之后开一条新分支"，即
    `fork_branch(from_step=state.step)`：新分支包含到这一步为止的历史，
    从这一步之后可以重新选。不传这两个参数（比如对比视图里复用这个
    函数渲染只读时间线）就不显示按钮，避免在不该分支的地方长出按钮。

    `causal_lines_meta`：`manifest.settings.causal_lines`（阶段二十二，
    4.13 节），用于把 `line_updates`/`causal_links.line_id` 里的
    line_id 换成人类可读的 label；为 None/空表示这次模拟没有声明
    因果线，`_line_updates_html` 会退化为直接展示 line_id。

    `causal_line_filter`：非 None 时只渲染"这一步在指定因果线上有
    动静，或者这一步的 `causal_links` 里有条目关联到这条线"的节点
    （阶段二十二，"按因果线筛选"视图切换，为阶段二十五的因果线 UI
    打基础）；为 None（默认）表示不筛选，渲染全部节点，与引入这个
    功能之前完全一致。
    """
    can_fork = sim_id is not None and source_branch is not None
    for _idx, state in enumerate(history):
        if causal_line_filter:
            line_updates = getattr(state, "line_updates", None) or {}
            causal_links = getattr(state, "causal_links", None) or []
            matches_update = causal_line_filter in line_updates
            matches_link = any(
                isinstance(link, dict) and str(link.get("line_id") or "") == causal_line_filter
                for link in causal_links
            )
            if not (matches_update or matches_link):
                continue
        chosen_note = ""
        if state.chosen_option_id:
            who = "代理" if state.chosen_by == "autopilot" else "你"
            label = _choice_label(state.options, state.chosen_option_id)
            chosen_note = f'<div class="ws-chapter-choice">→ {who} 选择了「{_html_text(label)}」</div>'
        if state.chosen_by == "autopilot" and state.chosen_reason:
            chosen_note += f'<div class="ws-chapter-choice">　理由：{_html_text(state.chosen_reason)}</div>'
        narrative = (
            f'<div class="ws-chapter-narrative">{_html_text(state.narrative)}</div>'
            if state.narrative else ""
        )
        # 拼成单行（不在字符串里放真实换行）：见 `_html_text` 的说明，
        # 多行 f-string + 缩进曾经导致 markdown 把收尾标签当成缩进代码
        # 块渲染，拼单行从根上避免这个问题。
        step_time_suffix = f" · {_html_text(state.time_label)}" if state.time_label else ""
        granularity_note = _granularity_note_html(state)
        resource_note = _resource_violations_html(state)
        relation_note = _relation_violations_html(state)
        option_warnings_note = _option_warnings_html(state)
        background_note = _background_entities_html(state)
        key_drivers_note = _key_drivers_html(state)
        line_updates_note = _line_updates_html(state, causal_lines_meta)
        tree_updates_note = _tree_updates_html(state, causal_lines_meta)
        structural_change_note = _structural_change_html(state)
        html = (
            '<div class="ws-chapter">'
            f'<div class="ws-chapter-step">第 {state.step} 步{step_time_suffix}</div>'
            f'<div class="ws-chapter-summary">{_html_text(state.summary)}</div>'
            f"{granularity_note}{resource_note}{relation_note}{option_warnings_note}{background_note}{line_updates_note}{tree_updates_note}{key_drivers_note}{structural_change_note}{narrative}{chosen_note}"
            "</div>"
        )
        st.markdown(html, unsafe_allow_html=True)
        change = getattr(state, "structural_change", None)
        if can_fork and isinstance(change, dict) and change.get("description") and not change.get("accepted"):
            # 阶段二十三：只有能拿到 sim_id/source_branch（即时间线本身
            # 处于"可操作"上下文，同「创建分支」按钮的前提）时才渲染
            # 「采纳」按钮——对比视图等只读场景复用同一个渲染函数时不
            # 应该出现这个按钮，避免在不该改 settings 的地方长出入口。
            adopt_col, _spacer2 = st.columns([1, 5])
            with adopt_col:
                if st.button(
                    "采纳为正式结构",
                    key=f"adopt_structural_change_{sim_id}_{source_branch}_{state.step}_{_idx}",
                    help="确认后会把这条结构性变化记入模拟设置，后续推进会把它当成已知事实提示给系统；不会自动改写具体变量结构。",
                ):
                    try:
                        apply_structural_change(
                            DATA_DIR, sim_id, step=state.step, branch=source_branch
                        )
                    except SimEngineError as exc:
                        st.error(str(exc))
                    else:
                        st.success("已采纳，后续推进会把这条新结构当作已知事实提示给系统。")
                        st.rerun()
        if can_fork:
            existing_checks = rc_mod.find_for_step(
                DATA_DIR, sim_id, branch=source_branch, step=state.step
            )
            expander_label = (
                f"🔁 记录现实结果（已记录 {len(existing_checks)} 条）"
                if existing_checks else "🔁 记录现实结果"
            )
            with st.expander(expander_label):
                # 阶段二十四（4.16 节）：Reality Loop 完整版——事后回来
                # 对这一步的预测填"后来实际发生了什么"，verdict 由用户
                # 自己选（不做自动语义判定，见 `reality_check.py`
                # docstring）；`verdict == diverged` 时会尝试把这一步的
                # `causal_links` 匹配进知识库，命中的条目
                # `contradicted_count` 加一。
                for check in existing_checks:
                    verdict_label = {
                        "matched": "✅ 与预测相符",
                        "partially_matched": "🟡 部分相符",
                        "diverged": "❌ 与预测不符",
                    }.get(check.verdict, check.verdict)
                    st.markdown(
                        f'<div class="ws-chapter-choice">{verdict_label} · {_html_text(check.actual_outcome)}</div>',
                        unsafe_allow_html=True,
                    )
                with st.form(key=f"reality_check_form_{sim_id}_{source_branch}_{state.step}_{_idx}"):
                    st.caption(f"这一步当时的预测：{state.summary}")
                    actual_outcome_input = st.text_area(
                        "后来实际发生了什么？", key=f"reality_outcome_{sim_id}_{source_branch}_{state.step}_{_idx}",
                    )
                    verdict_input = st.radio(
                        "整体判断",
                        options=["matched", "partially_matched", "diverged"],
                        format_func=lambda v: {
                            "matched": "✅ 与预测相符",
                            "partially_matched": "🟡 部分相符",
                            "diverged": "❌ 与预测不符",
                        }[v],
                        key=f"reality_verdict_{sim_id}_{source_branch}_{state.step}_{_idx}",
                        horizontal=True,
                    )
                    # 阶段三十三（4.4 节）：verdict 选"与预测不符"时，
                    # 额外让用户说明"为什么错"——不做自动语义判定，同
                    # verdict 一样由用户自己选，帮用户判断该往哪个方向
                    # 改进（丰富因果线，还是接受"世界本来就有随机性"）。
                    error_category_input = "none"
                    error_category_input = st.selectbox(
                        "如果不符，大致是哪种原因？（仅「不符」时需要）",
                        options=[
                            "none", "data_error", "causal_error",
                            "agent_behavior_error", "random_event", "unknown_variable",
                        ],
                        format_func=lambda v: {
                            "none": "（与预测相符/部分相符，不需要分类）",
                            "data_error": "状态判断错（对当前情况的理解就有误）",
                            "causal_error": "因果机制错（忽略了某个约束/机制）",
                            "agent_behavior_error": "Agent 行为预测错（高估/低估了会怎么选）",
                            "random_event": "纯随机事件（不是模型的锅）",
                            "unknown_variable": "模型压根没考虑到的变量",
                        }[v],
                        key=f"reality_error_category_{sim_id}_{source_branch}_{state.step}_{_idx}",
                    )
                    if st.form_submit_button("记录"):
                        try:
                            result = rc_mod.record_and_apply(
                                DATA_DIR, sim_id,
                                branch=source_branch, step=state.step,
                                predicted_summary=state.summary,
                                actual_outcome=actual_outcome_input,
                                verdict=verdict_input,
                                causal_links=getattr(state, "causal_links", None),
                                error_category=(
                                    error_category_input if verdict_input == "diverged" else "none"
                                ),
                                model_version=str(manifest.settings.get("model_version") or ""),
                            )
                        except rc_mod.RealityCheckError as exc:
                            st.error(str(exc))
                        else:
                            contradicted = result["contradicted_knowledge_ids"]
                            if contradicted:
                                st.success(
                                    f"已记录，并把 {len(contradicted)} 条相关因果知识标记为「被证伪」。"
                                )
                            else:
                                st.success("已记录。")
                            st.rerun()
            # 阶段三十四（4.5 节第三批，用户反馈反哺画像，`next_doc/
            # world_simulator_agent_preview_and_adaptive_policy_plan.md`
            # 5.1 节）：只对代理（自动挡）选择的步骤展示——手动挡是用户
            # 自己选的，没有"反馈代理选择"的意义。子方案原文假设复用
            # 已有 review_mode 复核入口，但实际代码里并没有现成的"标记
            # 不符合预期"入口，这里是补上的最小必要入口（见 policy_
            # feedback.py 模块 docstring 的说明）。
            if state.chosen_by == "autopilot":
                existing_feedback = policy_feedback_mod.find_for_step(
                    DATA_DIR, sim_id, branch=source_branch, step=state.step
                )
                fb_label = (
                    f"🚩 反馈这次代理的选择（已反馈 {len(existing_feedback)} 条）"
                    if existing_feedback else "🚩 反馈这次代理的选择"
                )
                with st.expander(fb_label):
                    for fb in existing_feedback:
                        st.markdown(
                            '<div class="ws-chapter-choice">🚩 已标记「不符合预期」：'
                            f'{_html_text(fb.user_reason)}</div>',
                            unsafe_allow_html=True,
                        )
                    with st.form(
                        key=f"policy_feedback_form_{sim_id}_{source_branch}_{state.step}_{_idx}"
                    ):
                        st.caption("如果觉得代理这一步的选择不符合预期，可以写一句理由——"
                                   "不会自动改动自动挡画像，只会在画像编辑页汇总展示，由你自己"
                                   "决定要不要据此调整。")
                        reason_input = st.text_area(
                            "为什么觉得不符合预期？",
                            key=f"policy_feedback_reason_{sim_id}_{source_branch}_{state.step}_{_idx}",
                        )
                        if st.form_submit_button("标记为不符合预期"):
                            try:
                                policy_feedback_mod.record_feedback(
                                    DATA_DIR, sim_id,
                                    branch=source_branch, step=state.step,
                                    user_reason=reason_input,
                                )
                            except policy_feedback_mod.PolicyFeedbackError as exc:
                                st.error(str(exc))
                            else:
                                st.success("已记录，可以在下面「配置自动挡」的画像编辑区看到汇总。")
                                st.rerun()
        if can_fork:
            fork_col, _spacer = st.columns([1, 5])
            with fork_col:
                if st.button(
                    "创建分支",
                    key=f"fork_here_{sim_id}_{source_branch}_{state.step}_{_idx}",
                    type="primary",
                    help="从这一步之后开一条新分支，原时间线原样保留，可以在新分支上重新选。",
                ):
                    try:
                        new_branch = bm.fork_branch(
                            DATA_DIR, sim_id, from_step=state.step,
                            source_branch=source_branch, switch=True,
                        )
                    except bm.BranchError as exc:
                        st.error(str(exc))
                    else:
                        st.success(f"已在第 {state.step} 步之后创建分支 {new_branch} 并切换为当前分支。")
                        st.rerun()


def _render_causal_lines_overview(
    history: List,
    causal_lines_meta: List[Dict[str, Any]],
    *,
    manifest=None,
    sim_id: str = "",
) -> None:
    """渲染"因果线总览"视图（阶段二十五，4.17 节，因果线 UI；用户后续
    要求补充"未来展望 + 用户可修改因果线"，见下方新增两段）。

    依赖阶段二十二落地的 `SimState.line_updates`/`causal_links.line_id`
    结构：按 `causal_lines_meta` 声明的每条线，把历史上所有
    `line_updates` 里该线的记录（`advanced` 不为 `False` 的）按 step
    顺序取出来，渲染成一行"该线的时间点序列 + 每个点的一句话摘要"，
    多条线上下并排展示。点击展开可以看这条线关联的所有
    `causal_links`（通过 `line_id` 过滤）。

    `history` 需要按 step 升序传入（调用方传原始 `history`，不是
    `_render_timeline` 用的 `reversed(history)`），这样时间点序列才是
    从早到晚的顺序，符合"走势"的直觉。

    因果线不应该有前置条件（用户要求）：`causal_lines_meta` 为空时不
    再直接返回——退化为从 `history` 里扫描 `line_updates`/
    `causal_links.line_id` 实际出现过的 id，当作"还没有正式 label 的
    自发因果线"继续展示，只是标注还没有中文名/节奏声明。这一步和
    `engine._auto_register_causal_lines()` 是同一件事在两处的体现：
    引擎负责把新 id 登记进 `manifest.settings`，这里负责"就算还没登记
    上，也不能让用户看不到"。

    不做力导向图/桑基图之类的复杂可视化——用最朴素的"多行文字时间轴
    并排"验证"按线看"这个信息组织方式本身是否有用，参考文档第
    五十二~五十三节的"跨因果线连接"/"因果贡献"暂不在本阶段实现。
    """
    label_by_id = {
        str(line.get("id")): str(line.get("label") or line.get("id"))
        for line in causal_lines_meta
        if isinstance(line, dict) and line.get("id")
    }
    granularity_by_id = {
        str(line.get("id")): str(line.get("time_granularity") or "")
        for line in causal_lines_meta
        if isinstance(line, dict) and line.get("id")
    }
    # 阶段三十一，4.20 节：节奏慢的线（`advance_every_n_steps` > 1）
    # 在展示上标个"约每 N 步一动"，避免长时间没有变化看起来像"卡住了"。
    cadence_by_id: Dict[str, int] = {}
    for line in causal_lines_meta:
        if not (isinstance(line, dict) and line.get("id")):
            continue
        try:
            n = int(line.get("advance_every_n_steps") or 1)
        except (TypeError, ValueError):
            n = 1
        if n > 1:
            cadence_by_id[str(line.get("id"))] = n
    # 阶段三十六第三批（2.3 节）：声明了 `owned_vars` 的线标注\"独立
    # 推进\"状态和当前 `local_step`，让用户能直观看出\"这条线为什么
    # 有时候好几步都没动静\"——不是卡住了，是本步没到它的节奏。
    independent_by_id: Dict[str, str] = {}
    for line in causal_lines_meta:
        if not (isinstance(line, dict) and line.get("id")):
            continue
        owned = line.get("owned_vars")
        if isinstance(owned, list) and owned:
            try:
                local_step = int(line.get("local_step") or 0)
            except (TypeError, ValueError):
                local_step = 0
            independent_by_id[str(line.get("id"))] = f"独立推进 · 本线第 {local_step} 步"
    # 退化路径：声明列表里没有的 id，只要在历史里真的出现过，也纳入
    # 展示（label 退化为 id 本身）。
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
        st.markdown(
            '<span class="ws-muted">因果线是模拟的默认基础机制，不需要提前声明——'
            "推进下一步之后，AI 判断出的因果线会自动出现在这里。</span>",
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        '<span class="ws-muted">按因果线聚合展示每条线各自的时间点序列'
        "（比如技术线走到第几年、谈判线走到第几轮），点击展开可以看"
        "只属于这条线的因果链条目；每条线下方是这条线的\"未来因果树\"——"
        "创建模拟时就已经生成，展示从当前节点出发的若干可能分支（不是"
        "只有一条延续路径），推进过程中会随实际走向自动修正，也可以"
        "手动标记\"已解决/已失效\"；再下方是可选的历史外推参考，并可以"
        "直接留下修改意见。</span>",
        unsafe_allow_html=True,
    )

    futures_by_id: Dict[str, List[Dict[str, Any]]] = {}
    if manifest is not None:
        try:
            futures_by_id = {
                item["line_id"]: item["futures"]
                for item in hyp_mod.project_line_futures(manifest, history)
            }
        except Exception:  # noqa: BLE001 — 展望是辅助信息，算失败不影响总览本身
            futures_by_id = {}

    for line_id, label in label_by_id.items():
        points = []
        last_trend = None
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

        granularity = granularity_by_id.get(line_id, "")
        cadence_n = cadence_by_id.get(line_id)
        independent_note = independent_by_id.get(line_id, "")
        _suffix_parts = [
            p for p in (
                granularity,
                (f"约每 {cadence_n} 步一动" if cadence_n else ""),
                independent_note,
            ) if p
        ]
        granularity_suffix = f"（{'，'.join(_suffix_parts)}）" if _suffix_parts else ""
        trend_badge = (
            f' <span class="ws-uncertain-badge">{_html_text(_TREND_LABELS[last_trend])}</span>'
            if last_trend else ""
        )
        title_html = (
            '<div class="ws-causal-line-row">'
            f'<div class="ws-causal-line-row-title">📈 {_html_text(label)}'
            f'{granularity_suffix} <span class="ws-causal-line-row-id">{_html_text(line_id)}</span>'
            f'{trend_badge}</div>'
        )
        if not points:
            title_html += '<div class="ws-causal-line-empty">这条线目前还没有推进记录。</div></div>'
            st.markdown(title_html, unsafe_allow_html=True)
            continue

        track_items = []
        for idx, (step, time_label, summary) in enumerate(points):
            if idx > 0:
                track_items.append('<span class="ws-causal-line-point-arrow">→</span>')
            time_text = time_label or f"第 {step} 步"
            point_html = (
                '<span class="ws-causal-line-point">'
                f'<span class="ws-causal-line-point-time">{_html_text(time_text)}</span>'
                f"{_html_text(summary)}"
                "</span>"
            )
            track_items.append(point_html)
        title_html += f'<div class="ws-causal-line-track">{"".join(track_items)}</div></div>'
        st.markdown(title_html, unsafe_allow_html=True)

        related_links = []
        for state in history:
            for link in (getattr(state, "causal_links", None) or []):
                if isinstance(link, dict) and str(link.get("line_id") or "") == line_id:
                    related_links.append((state.step, link))
        with st.expander(f"展开「{label}」关联的因果链（{len(related_links)} 条）"):
            if not related_links:
                st.markdown(
                    '<span class="ws-causal-line-empty">这条线目前还没有关联到具体的因果链条目。'
                    "（`causal_links` 需要带上 `line_id` 才会出现在这里）</span>",
                    unsafe_allow_html=True,
                )
            else:
                for step, link in related_links:
                    driver = str(link.get("driver") or "").strip()
                    effect = str(link.get("effect") or "").strip()
                    affected = link.get("affected_fields") or []
                    affected_text = "、".join(str(x) for x in affected) if affected else ""
                    detail = " → ".join(x for x in (driver, effect) if x)
                    suffix = f"（影响：{affected_text}）" if affected_text else ""
                    st.markdown(
                        f'<div class="ws-chapter-choice">第 {step} 步 · {_html_text(detail)}{_html_text(suffix)}</div>',
                        unsafe_allow_html=True,
                    )

        # 阶段二十六（`next_doc/world_simulator_causal_line_future_tree_
        # plan.md`）：因果树——创建模拟时就已经落盘（见
        # `causal_tree.ensure_future_trees()`），不要求先推进一步才能
        # 看到。阶段三十三第四批起状态从 3 态扩展为 6 态（4.9 节：
        # dormant/emerging/active/resolved/expired/invalidated），
        # 手动按钮只保留"标为已解决/标为已失效"这两个高价值操作，
        # 其余状态变化（emerging/active）交给 `tree_updates.
        # status_updates` 由 LLM 判断声明。
        line_meta_dict = next(
            (line for line in causal_lines_meta if isinstance(line, dict) and str(line.get("id")) == line_id),
            None,
        )
        future_tree = (line_meta_dict or {}).get("future_tree") if line_meta_dict else None
        branches = [b for b in (future_tree or {}).get("branches", []) if isinstance(b, dict)] if isinstance(future_tree, dict) else []
        st.markdown(
            f'<div class="ws-muted" style="margin-top:0.4rem;">🌳 「{_html_text(label)}」未来因果树'
            "（从当前节点出发的若干可能分支，不是只有一条路）：</div>",
            unsafe_allow_html=True,
        )
        if not branches:
            st.markdown(
                '<span class="ws-causal-line-empty">这条线目前还没有未来树数据（旧实例可能缺失）。</span>',
                unsafe_allow_html=True,
            )
        else:
            status_icon = {
                "dormant": "○", "emerging": "🌱", "active": "◐",
                "resolved": "●", "expired": "⌛", "invalidated": "✕",
            }
            status_label = {
                "dormant": "潜伏", "emerging": "形成中", "active": "已激活",
                "resolved": "已解决", "expired": "已错过", "invalidated": "已失效",
            }
            likelihood_label = {"high": "可能性高", "medium": "可能性中", "low": "可能性低"}

            # 第五轮方案 5.8 节（`next_doc/world_simulator_decision_
            # engine_round2_gap_analysis_plan.md`，阶段三十四第五批）：
            # 参考文档"三层未来空间"（潜在/活跃/已发生）在数据层面已经
            # 被现有六态生命周期覆盖，不新增字段——这里只是展示层按
            # 生命周期阶段分组，让"已发生历史"这一层在界面上有直接
            # 对应的可视化位置，不需要用户翻 `history` 自己拼凑。
            # `expired`/`invalidated` 不属于参考文档三层里的任何一层
            # （既不是"潜在/活跃"也不是"已发生"，是"已排除"的分支），
            # 单独成组，不强行并入前两组造成误导。
            lifecycle_groups = [
                ("🌱 仍在演化中（潜在 / 活跃）", ("dormant", "emerging", "active")),
                ("● 已发生（已确认/已解决）", ("resolved",)),
                ("✕ 已排除（错过窗口/已失效）", ("expired", "invalidated")),
            ]
            grouped: Dict[str, List[Dict[str, Any]]] = {name: [] for name, _ in lifecycle_groups}
            status_to_group = {
                status: name for name, statuses in lifecycle_groups for status in statuses
            }
            for branch in branches:
                b_status = causal_tree.canonical_status(branch.get("status"))
                group_name = status_to_group.get(b_status, lifecycle_groups[0][0])
                grouped[group_name].append(branch)

            for group_name, _statuses in lifecycle_groups:
                group_branches = grouped[group_name]
                if not group_branches:
                    continue
                st.markdown(
                    f'<div class="ws-muted" style="margin-top:0.3rem;font-size:0.85rem;">{group_name}'
                    f"（{len(group_branches)}）</div>",
                    unsafe_allow_html=True,
                )
                for branch in group_branches:
                    b_id = str(branch.get("id") or "")
                    b_status = causal_tree.canonical_status(branch.get("status"))
                    b_desc = str(branch.get("description") or "")
                    b_like = str(branch.get("likelihood") or "medium")
                    icon = status_icon.get(b_status, "○")
                    cols = st.columns([6, 1, 1])
                    with cols[0]:
                        st.markdown(
                            f'<div class="ws-chapter-choice">{icon} <b>{_html_text(status_label.get(b_status, b_status))}</b>'
                            f"（{_html_text(likelihood_label.get(b_like, b_like))}）— {_html_text(b_desc)}</div>",
                            unsafe_allow_html=True,
                        )
                    if manifest is not None and sim_id and b_status not in ("resolved",):
                        with cols[1]:
                            if st.button("标为已解决", key=f"tree_confirm_{sim_id}_{line_id}_{b_id}"):
                                update_settings(
                                    DATA_DIR, sim_id,
                                    causal_lines=causal_tree.set_branch_status(
                                        causal_lines_meta, line_id, b_id, "resolved"
                                    ),
                                )
                                st.rerun()
                    if manifest is not None and sim_id and b_status not in ("invalidated",):
                        with cols[2]:
                            if st.button("标为已失效", key=f"tree_prune_{sim_id}_{line_id}_{b_id}"):
                                update_settings(
                                    DATA_DIR, sim_id,
                                    causal_lines=causal_tree.set_branch_status(
                                        causal_lines_meta, line_id, b_id, "invalidated"
                                    ),
                                )
                                st.rerun()

        futures = futures_by_id.get(line_id) or []
        if futures:
            st.markdown(
                f'<div class="ws-muted" style="margin-top:0.3rem;">🔮 「{_html_text(label)}」的简单历史外推'
                "（基于最近因果链的单路径推演，仅作补充参考，完整的分叉可能见上方\"未来因果树\"）：</div>",
                unsafe_allow_html=True,
            )
            for fut in futures:
                dim = _html_text(str(fut.get("dimension") or ""))
                scale = _html_text(str(fut.get("time_scale") or ""))
                desc = _html_text(str(fut.get("description") or ""))
                conf = str(fut.get("confidence") or "unknown")
                conf_label = {
                    "low": "低置信度·分叉可能大", "medium": "中等置信度",
                    "high": "高置信度", "unknown": "置信度未知",
                }.get(conf, conf)
                st.markdown(
                    '<div class="ws-uncertain-field">'
                    f'<span class="ws-uncertain-badge ws-uncertain-badge-{conf if conf in ("low", "medium", "high") else "medium"}">'
                    f"{_html_text(conf_label)}</span>"
                    f"<b>{dim}</b>（时间尺度：{scale}）— {desc}</div>",
                    unsafe_allow_html=True,
                )

        if manifest is not None and sim_id:
            with st.expander(f"✏️ 对「{label}」这条线提修改意见"):
                st.markdown(
                    '<span class="ws-muted">写下你觉得这条线接下来应该怎么发展/'
                    "哪里推演得不对，下一步推进时会作为明确要求喂给 AI。</span>",
                    unsafe_allow_html=True,
                )
                existing_feedback = ""
                for line in causal_lines_meta:
                    if isinstance(line, dict) and str(line.get("id")) == line_id:
                        existing_feedback = str(line.get("user_feedback") or "")
                        break
                feedback_key = f"causal_line_feedback_{sim_id}_{line_id}"
                new_feedback = st.text_area(
                    "修改意见", value=existing_feedback, key=feedback_key,
                    height=70, label_visibility="collapsed",
                    placeholder="例：谈判线节奏太快了，接下来两步希望更胶着一些",
                )
                if st.button("保存意见", key=f"{feedback_key}_save"):
                    updated_lines = []
                    found_line = False
                    for line in causal_lines_meta:
                        if isinstance(line, dict) and str(line.get("id")) == line_id:
                            found_line = True
                            merged = dict(line)
                            merged["user_feedback"] = new_feedback.strip()
                            updated_lines.append(merged)
                        else:
                            updated_lines.append(line)
                    if not found_line:
                        updated_lines.append(
                            {
                                "id": line_id, "label": label,
                                "time_granularity": granularity_by_id.get(line_id, ""),
                                "user_feedback": new_feedback.strip(),
                            }
                        )
                    update_settings(DATA_DIR, sim_id, causal_lines=updated_lines)
                    st.success("已保存，下一步推进会把这条意见提示给 AI。")
                    st.rerun()

    if manifest is not None and sim_id:
        _render_suggested_causal_lines(manifest, sim_id)

    _render_causal_graph_section(history)


def _render_suggested_causal_lines(manifest, sim_id: str) -> None:
    """渲染"因果线建议"区块（阶段二十九，4.26 节，开放世界闭环收尾）。

    展示 `settings.suggested_causal_lines`（`apply_structural_change()`
    采纳 `new_mechanism`/`regime_shift` 时自动生成，见 `engine.py`），
    每条建议提供"接受这条建议线"/"忽略"两个按钮，分别调用
    `accept_suggested_causal_line()`/`reject_suggested_causal_line()`。
    没有任何建议时不渲染这个区块（不是空区块占位），避免大多数实例
    的因果线总览页面被一个常年空着的折叠区打扰。
    """
    suggested = manifest.settings.get("suggested_causal_lines") or []
    suggested = [s for s in suggested if isinstance(s, dict) and s.get("id")]
    if not suggested:
        return
    with st.expander(f"💡 因果线建议（{len(suggested)} 条待处理）", expanded=True):
        st.markdown(
            '<span class="ws-muted">系统在这几步发现了新的机制/规则性变化并已被采纳，'
            "建议为它们各开一条独立因果线继续追踪；接受后会带着默认未来树加入下方"
            "「因果线总览」，也可以直接忽略。</span>",
            unsafe_allow_html=True,
        )
        for item in suggested:
            suggestion_id = str(item.get("id"))
            label = str(item.get("label") or suggestion_id)
            description = str(item.get("source_description") or "")
            step = item.get("source_step")
            step_text = f"第 {step} 步 · " if step is not None else ""
            cols = st.columns([6, 1, 1])
            with cols[0]:
                st.markdown(
                    f'<div class="ws-chapter-choice">{step_text}{_html_text(description)}'
                    f"（建议命名：{_html_text(label)}）</div>",
                    unsafe_allow_html=True,
                )
            with cols[1]:
                if st.button("接受", key=f"accept_suggested_line_{sim_id}_{suggestion_id}"):
                    try:
                        accept_suggested_causal_line(DATA_DIR, sim_id, suggestion_id)
                    except SimEngineError as exc:
                        st.error(str(exc))
                    else:
                        st.success(f"已接受，「{label}」现在是一条正式因果线。")
                        st.rerun()
            with cols[2]:
                if st.button("忽略", key=f"reject_suggested_line_{sim_id}_{suggestion_id}"):
                    reject_suggested_causal_line(DATA_DIR, sim_id, suggestion_id)
                    st.rerun()


def _render_causal_graph_section(history: List) -> None:
    """渲染"跨线影响关系"区块（阶段二十七，4.19 节，因果线耦合结构化）。

    聚合历史 `causal_links` 里的 `line_id`/`source_line_id`/
    `relation_type`，展示"哪条线在影响哪条线、是什么类型的影响、
    出现过几次"，帮用户回答"哪条线真正决定了结果"这类问题，而不是
    只能逐条翻 `causal_links` 的自由文本。纯展示层、不新增任何持久化
    结构，见 `causal_graph.py` 模块说明。
    """
    edges = cg_mod.build_causal_graph(history)
    with st.expander("🔗 跨线影响关系（哪条线在影响哪条线）"):
        if not edges:
            st.markdown(
                '<span class="ws-muted">暂无可聚合的跨线影响记录——'
                "`causal_links` 里还没有出现过 `line_id`，继续推进几步，"
                "或在因果链输出里带上归属线之后再回来看。</span>",
                unsafe_allow_html=True,
            )
            return
        st.markdown(
            '<span class="ws-muted">按「发起线 → 落地线」聚合统计，'
            "同一条边下方可展开看最多 3 条具体因果关系示例。</span>",
            unsafe_allow_html=True,
        )
        for edge in edges:
            parts = [
                f"{count} 次{cg_mod.relation_type_label(rt)}"
                for rt, count in sorted(
                    edge.relation_counts.items(), key=lambda kv: -kv[1]
                )
            ]
            st.markdown(
                f"**{_html_text(edge.source_line)} → {_html_text(edge.target_line)}**"
                f"　{'，'.join(parts)}",
            )
            for example in edge.examples:
                driver = _html_text(str(example.get("driver") or ""))
                effect = _html_text(str(example.get("effect") or ""))
                st.markdown(
                    f'<div class="ws-muted" style="margin-left:1rem;">'
                    f"· {driver}{'　→　' + effect if effect else ''}</div>",
                    unsafe_allow_html=True,
                )


def _render_attribution_section(history: List, manifest) -> None:
    """渲染"这个结果是怎么来的"折叠区（阶段二十九，4.21 节，归因/贡献
    拆解报告）。

    只对 `manifest.settings.objectives` 里声明过 `field` 的目标字段
    提供归因入口——没有声明可排序字段的实例，这里退化为一句引导文案
    而不是强行猜一个字段（同 `rank_by_objectives()` 的前置条件），
    纯展示层调用 `attribution.summarize_contributions()`，不发起任何
    新的 LLM 调用。

    归因清单下方额外附带"顺势/逆势/改变趋势"判断（阶段三十一，4.23
    节，见 `trend.classify_trend()`）——需要用户额外选择"哪条因果线
    代表自己"，只有实例声明过至少一条 `causal_lines` 时才展示这部分。
    """
    objectives = normalize_objectives((manifest.settings or {}).get("objectives") or [])
    field_objectives = [o for o in objectives if o.field]
    with st.expander("🧭 这个结果是怎么来的（归因/贡献拆解，可选）"):
        if not field_objectives:
            st.markdown(
                '<span class="ws-muted">还没有声明可排序的关注字段——在'
                '"⚙️ 模拟设置"里的"声明可排序字段"填一个 `field`，'
                "就能在这里看它的贡献来源清单。</span>",
                unsafe_allow_html=True,
            )
            return
        options = {o.label: o.field for o in field_objectives}
        picked_label = st.selectbox(
            "选择一个目标字段", list(options.keys()), key="attribution_target_field_picker",
        )
        target_field = options.get(picked_label) or ""
        report = attribution_mod.summarize_contributions(history, target_field)
        if not report.sources:
            st.markdown(
                '<span class="ws-muted">目前的因果链条目里还没有出现过标注了'
                f'`affected_fields` 命中 `{_html_text(target_field)}` 的记录，'
                "继续推进几步，或者让 `causal_links` 输出时带上这个字段名。"
                "</span>",
                unsafe_allow_html=True,
            )
            return
        if report.caveat:
            st.markdown(
                f'<div class="ws-uncertain-field">'
                f'<span class="ws-uncertain-badge ws-uncertain-badge-low">提示</span>'
                f"{_html_text(report.caveat)}</div>",
                unsafe_allow_html=True,
            )
        st.markdown(
            '<span class="ws-muted">按贡献来源线聚合，"高/中/低相关"只是基于'
            "出现次数占比的粗略分档，不是精确的统计显著性检验。</span>",
            unsafe_allow_html=True,
        )
        for source in report.sources:
            st.markdown(
                f"**{_html_text(source.source_line)}**　"
                f"{source.level_label}（{source.count} 次相关因果链）",
            )
            for example in source.examples:
                driver = _html_text(str(example.get("driver") or ""))
                effect = _html_text(str(example.get("effect") or ""))
                step = example.get("step")
                step_prefix = f"第 {step} 步 · " if step is not None else ""
                st.markdown(
                    f'<div class="ws-muted" style="margin-left:1rem;">'
                    f"· {step_prefix}{driver}{'　→　' + effect if effect else ''}</div>",
                    unsafe_allow_html=True,
                )

        # ── 顺势/逆势/改变趋势判断（阶段三十一，4.23 节）：依赖上面的
        # 归因结果，需要用户指定"哪条因果线代表自己/所在实体"——系统
        # 无法自动识别，必须由用户选择，这也是这个判断"启发式、不追求
        # 精确"定位的直接体现 ──
        line_ids = [
            str(line.get("id")) for line in (manifest.settings or {}).get("causal_lines") or []
            if isinstance(line, dict) and line.get("id")
        ]
        if line_ids:
            st.markdown("---")
            self_line_id = st.selectbox(
                "哪条因果线代表\"你/所在实体\"（用于顺势/逆势/改变趋势判断）",
                options=line_ids, key="trend_self_line_picker",
            )
            judgement = trend_mod.classify_trend(history, target_field, self_line_id)
            st.markdown(f"**顺势/逆势判断**：{_html_text(judgement.verdict_label)}")
            if judgement.caveat:
                st.markdown(
                    f'<span class="ws-muted">{_html_text(judgement.caveat)}</span>',
                    unsafe_allow_html=True,
                )
            for ev in judgement.evidence:
                driver = _html_text(str(ev.get("driver") or ""))
                effect = _html_text(str(ev.get("effect") or ""))
                step = ev.get("step")
                step_prefix = f"第 {step} 步 · " if step is not None else ""
                st.markdown(
                    f'<div class="ws-muted" style="margin-left:1rem;">'
                    f"· {step_prefix}{driver}{'　→　' + effect if effect else ''}</div>",
                    unsafe_allow_html=True,
                )


def page_detail() -> None:
    sim_id = st.session_state.get("sim_id")
    if not sim_id:
        st.session_state["view"] = "list"
        st.rerun()
        return

    try:
        manifest, current, history = get_simulation(DATA_DIR, sim_id)
    except SimEngineError as exc:
        st.error(f"加载模拟实例失败：{exc}")
        if st.button("← 返回列表"):
            st.session_state["view"] = "list"
            st.rerun()
        return

    # ── 自动挡"连续推进"的续跑逻辑 ──
    #
    # 按钮点击时不在一次脚本执行里用 for 循环跑完所有步数——那样页面
    # 只会在全部步数跑完后才刷新一次，中间过程用户什么都看不到。这里
    # 改成"每次 rerun 只推进一步，推进完把 manifest/current/history 换成
    # 最新的，正常走完这一次页面渲染（当前状态卡片、时间线都会用新
    # 数据），再在函数末尾触发下一次 rerun"——每一步之间都有一次完整
    # 的页面刷新，效果上就是"自动连续推进、每步都能看到"，而不是等到
    # 最后一步才刷新。
    schedule_next_autostep = False
    auto_run = st.session_state.get("autopilot_run")
    if auto_run and auto_run.get("sim_id") == sim_id and auto_run.get("remaining", 0) > 0:
        if manifest.status != "active":
            st.session_state.pop("autopilot_run", None)
            st.info("模拟状态已变化（暂停/结束），自动连续推进已停止。")
        else:
            try:
                cfg = _load_cfg()
            except ImportError as exc:
                st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")
                st.session_state.pop("autopilot_run", None)
            else:
                try:
                    with st.spinner(
                        f"代理正在决策并推进第 {auto_run['done'] + 1}/{auto_run['target']} 步..."
                    ):
                        next_state = run_autopilot_step(cfg, PROJECT_ROOT, DATA_DIR, sim_id)
                except (SimEngineError, AutopilotDisabledError) as exc:
                    st.error(f"自动挡推进失败，已停止连续推进：{exc}")
                    st.session_state.pop("autopilot_run", None)
                else:
                    auto_run["done"] += 1
                    auto_run["remaining"] -= 1
                    # 重新加载最新状态，让这一次渲染（当前状态卡片、时间线）
                    # 反映刚刚推进完的这一步，而不是这一步开始前的旧数据。
                    manifest, current, history = get_simulation(DATA_DIR, sim_id)
                    review_mode = (manifest.autopilot or {}).get("review_mode", "silent")
                    if review_mode == "pause_on_major_decision" and next_state.major_decision:
                        st.session_state.pop("autopilot_run", None)
                        st.success(f"已连续推进 {auto_run['done']} 步（遇到重大决策，已按配置暂停）。")
                    elif auto_run["remaining"] <= 0:
                        st.session_state.pop("autopilot_run", None)
                        st.success(f"已连续推进 {auto_run['done']} 步。")
                    else:
                        st.session_state["autopilot_run"] = auto_run
                        schedule_next_autostep = True
                        st.info(
                            f"✅ 已完成第 {auto_run['done']}/{auto_run['target']} 步"
                            "（下面的当前状态、时间线已经是最新的），"
                            "页面会在片刻后自动继续下一步……"
                        )

    top_l, top_r = st.columns([4, 1])
    with top_l:
        st.markdown(f"## {manifest.title}", unsafe_allow_html=True)
        st.markdown(
            f'{_pill(manifest.status)}<span class="ws-muted">模板：{manifest.template} · '
            f"当前第 {manifest.current_step} 步</span>",
            unsafe_allow_html=True,
        )
    with top_r:
        if st.button("📖 游戏化视图"):
            st.session_state["view"] = "game"
            st.session_state.pop("game_chapter_idx", None)
            st.rerun()
        if st.button("← 返回列表"):
            st.session_state["view"] = "list"
            st.rerun()

    step_time_suffix = f" · {_html_text(current.time_label)}" if current.time_label else ""
    st.markdown(f"#### 当前状态（第 {current.step} 步{step_time_suffix}）", unsafe_allow_html=True)
    st.markdown(
        f'<div class="ws-card"><div class="ws-card-title">{_html_text(current.summary)}</div>'
        + _granularity_note_html(current)
        + (f'<div class="ws-muted">{_html_text(current.narrative)}</div>' if current.narrative else "")
        + "</div>",
        unsafe_allow_html=True,
    )
    if current.vars:
        with st.expander("关键变量"):
            uncertain_html = _uncertain_fields_html(current)
            if uncertain_html:
                st.markdown(uncertain_html, unsafe_allow_html=True)
            provenance_html = _field_provenance_html(current)
            if provenance_html:
                st.markdown(provenance_html, unsafe_allow_html=True)
            belief_html = _belief_comparison_html(
                current.vars,
                _latest_beliefs(history, current.step),
                list(manifest.settings.get("belief_fields") or []),
            )
            if belief_html:
                st.markdown(belief_html, unsafe_allow_html=True)
            _render_vars_display(current.vars, bool(manifest.settings.get("multi_entity_mode")))

    _render_attribution_section(history, manifest)

    # 阶段三十三（4.4 节）：按错误分类/模型版本统计——帮用户判断"预测
    # 有没有变准"、该往哪个方向改进。只在这个分支已经有过至少一条现实
    # 回填记录时才展示，避免给从没用过 Reality Check 功能的实例增加
    # 一个空落落的入口。
    all_checks = [
        c for c in rc_mod.load_all(DATA_DIR, sim_id) if c.branch == manifest.branch
    ]
    if all_checks:
        with st.expander(f"📊 预测准确性统计（共 {len(all_checks)} 条现实回填记录）"):
            stats = rc_mod.stats_by_error_category(all_checks)
            st.markdown(
                f"共 {stats['total']} 条记录，其中 {stats['diverged_total']} 条与预测不符。"
            )
            if stats["by_category"]:
                _ERROR_CATEGORY_LABELS = {
                    "data_error": "状态判断错",
                    "causal_error": "因果机制错",
                    "agent_behavior_error": "Agent 行为预测错",
                    "random_event": "纯随机事件",
                    "unknown_variable": "模型没考虑到的变量",
                    "uncategorized": "未分类",
                }
                st.caption("按错误分类：" + "，".join(
                    f"{_ERROR_CATEGORY_LABELS.get(k, k)} {v} 次"
                    for k, v in stats["by_category"].items()
                ))
            if len(stats["by_model_version"]) > 1:
                st.caption("按模型/Skill 版本：" + "，".join(
                    f"{ver}（{b['total']} 条，{b['diverged']} 条不符）"
                    for ver, b in stats["by_model_version"].items()
                ))

    # 第五轮方案 5.7 节（`next_doc/world_simulator_decision_engine_
    # round2_gap_analysis_plan.md`，阶段三十四第四批）：评估标准的
    # 轻量自评模块——纯统计代理指标，不是质量评分，默认折叠避免
    # 信息过载。
    with st.expander("📐 质量信号（统计代理指标，非评分）"):
        st.markdown(
            '<span class="ws-muted">这些是基于已有字段的客观计数统计，'
            "用来反映决策引擎设计在几个维度上的信息密度——数字高不代表"
            "这次模拟一定更好，只是反映了某个维度用得多不多；不是给这次"
            "模拟打分。</span>",
            unsafe_allow_html=True,
        )
        signals = quality_signals_mod.summarize_quality_signals(
            history, causal_lines=manifest.settings.get("causal_lines")
        )

        def _pct(ratio: Optional[float]) -> str:
            return f"{ratio:.0%}" if ratio is not None else "暂无数据"

        exp = signals["explainability"]
        st.caption(
            f"可解释性密度：{_pct(exp['ratio'])}"
            f"（{exp['decision_points']} 个决策点中 {exp['with_reason']} 个"
            "标注了决策/行动理由）"
        )
        cross = signals["cross_line_influence"]
        st.caption(
            f"跨线影响密度：{_pct(cross['ratio'])}"
            f"（{cross['total_causal_links']} 条因果链中 "
            f"{cross['with_source_line']} 条标注了发起线）"
        )
        div = signals["branch_diversity"]
        st.caption(
            f"分支差异性：{_pct(div['ratio'])}"
            f"（{div['decision_points']} 个决策点中 {div['multi_option']} 个"
            "给出了 2 个以上选项）"
        )
        exp_use = signals["expansion_usage"]
        st.caption(
            f"渐进展开使用率：{_pct(exp_use['ratio'])}"
            f"（{exp_use['total_branches']} 个未来树分支中 "
            f"{exp_use['expanded']} 个已展开）"
        )
        unc = signals["uncertainty_coverage"]
        st.caption(
            f"不确定性标注覆盖率：{_pct(unc['ratio'])}"
            f"（{unc['total_steps']} 步中 {unc['with_uncertain_fields']} 步"
            "标注了不确定字段）"
        )
        st.caption(
            "因果一致性、决策真实性这两条评价标准需要理解语义内容才能"
            "判断，暂时没有自动化的衡量方式，仍需自行阅读叙事/因果图判断。"
        )

    # 阶段三十二（4.6 节，用户本次明确要求）：模拟复盘 / 经验教训总结。
    # 不自动触发——LLM 调用有成本，且复盘本身应该是用户主动想回顾时
    # 才做的事，不是每步都算一次。
    with st.expander("📖 模拟复盘"):
        st.markdown(
            '<span class="ws-muted">把这个分支从起点到现在的决策和结果，整理成一份'
            "结构化的经验教训总结——只总结已经发生的内容，不做新的预测。</span>",
            unsafe_allow_html=True,
        )
        if manifest.status == "ended":
            st.info("这个实例已经结束，是个复盘的好时机。")
        existing_retros = retrospective_mod.load_for_branch(DATA_DIR, sim_id, manifest.branch)
        if st.button("生成复盘报告", key=f"retro_generate_{sim_id}_{manifest.branch}"):
            with st.spinner("正在整理这一路的决策和结果..."):
                try:
                    cfg = _load_cfg()
                    retrospective_mod.generate_retrospective(
                        cfg, PROJECT_ROOT, DATA_DIR, sim_id, branch=manifest.branch,
                    )
                except retrospective_mod.RetrospectiveError as exc:
                    st.error(f"生成复盘报告失败：{exc}")
                except ImportError as exc:
                    st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")
                else:
                    st.success("复盘报告已生成。")
                    st.rerun()

        if not existing_retros:
            st.caption("这个分支还没有生成过复盘报告。")
        else:
            for i, record in enumerate(existing_retros):
                report = record.report
                title = f"第 {record.up_to_step} 步为止的复盘 · {record.created_at}"
                with st.expander(title, expanded=(i == 0)):
                    if report.turning_points:
                        st.markdown("**关键转折点**")
                        for tp in report.turning_points:
                            st.markdown(
                                f"- 第 {tp.get('step', '?')} 步「{_html_text(str(tp.get('chosen_option', '')))}」"
                                f"：{_html_text(str(tp.get('why', '')))}",
                                unsafe_allow_html=True,
                            )
                    if report.what_went_well:
                        st.markdown("**做得好的地方**")
                        for item in report.what_went_well:
                            st.markdown(
                                f"- {_html_text(str(item.get('point', '')))}"
                                f"（依据：{_html_text(str(item.get('evidence', '')))}）",
                                unsafe_allow_html=True,
                            )
                    if report.what_to_reflect_on:
                        st.markdown("**值得反思的地方**")
                        for item in report.what_to_reflect_on:
                            st.markdown(
                                f"- {_html_text(str(item.get('point', '')))}"
                                f"（依据：{_html_text(str(item.get('evidence', '')))}）",
                                unsafe_allow_html=True,
                            )
                    if report.lessons:
                        st.markdown("**经验教训**")
                        for item in report.lessons:
                            st.markdown(
                                f"- {_html_text(str(item.get('lesson', '')))}"
                                f"（来自：{_html_text(str(item.get('source', '')))}）",
                                unsafe_allow_html=True,
                            )
                    if report.caveats:
                        st.caption("；".join(report.caveats))

    with st.expander("⚙️ 模拟设置（候选方向数量 / 时间粒度）"):
        cur_settings = manifest.settings or {}
        st.markdown(
            '<span class="ws-muted">改了之后从下一步推进开始生效，不会改写已经产生的历史。'
            "</span>",
            unsafe_allow_html=True,
        )
        new_options_count = st.number_input(
            "候选方向数量上限（软上限，实际数量由情境决定）", min_value=2, max_value=8,
            value=int(cur_settings.get("options_count", 4) or 4), step=1,
            key="settings_options_count",
        )
        _MODE_LABELS_SETTINGS = {
            "auto": "自动（AI 按情境自行判断，同一次模拟允许切换）",
            "guided": "引导（给一个偏好/基准，AI 仍自行判断）",
            "fixed": "固定（每一步都用同一个粒度，不会切换）",
        }
        cur_mode = str(cur_settings.get("time_granularity_mode") or "auto")
        if cur_mode not in _MODE_LABELS_SETTINGS:
            cur_mode = "auto"
        new_mode = st.selectbox(
            "时间粒度模式", options=list(_MODE_LABELS_SETTINGS),
            index=list(_MODE_LABELS_SETTINGS).index(cur_mode),
            format_func=lambda m: _MODE_LABELS_SETTINGS[m],
            key="settings_granularity_mode",
        )
        new_time_granularity = str(cur_settings.get("time_granularity") or "")
        new_time_granularity_guide = str(cur_settings.get("time_granularity_guide") or "")
        if new_mode == "fixed":
            new_time_granularity = st.text_input(
                "固定粒度（每一步都严格按这个跨度推进）",
                value=new_time_granularity, placeholder="例：1 个月 / 1 年 / 一场谈判的一轮",
                key="settings_time_granularity",
            )
        elif new_mode == "guided":
            new_time_granularity_guide = st.text_area(
                "节奏偏好/基准（不是精确值，AI 仍会自行判断）",
                value=new_time_granularity_guide,
                placeholder="例：日常按季度推进，遇到谈判/冲突等关键场景可以细到按轮次或周",
                height=70, key="settings_time_granularity_guide",
            )
        else:
            st.markdown(
                '<span class="ws-muted">AI 会按情境自行选择每一步的时间跨度，时间线上会标出'
                "每次切换的原因。</span>",
                unsafe_allow_html=True,
            )
        cur_resource_fields = cur_settings.get("resource_fields") or []
        cur_resource_fields_text = ", ".join(
            (f.get("field", "") if isinstance(f, dict) else str(f))
            for f in cur_resource_fields
        )
        new_resource_fields_text = st.text_input(
            "资源类字段（逗号分隔，可选——低于 0 会被自动纠正为 0）",
            value=cur_resource_fields_text,
            placeholder="例：resources.cash, resources.energy",
            key="settings_resource_fields",
        )
        cur_belief_fields = cur_settings.get("belief_fields") or []
        new_belief_fields_text = st.text_input(
            "认知偏差声明字段（逗号分隔，可选，4.3 节——声明后角色对这些"
            "字段的认知可能与真实值不同，展示层会做\"真实 vs 你以为\"对比，"
            "建议先在单个模拟里小范围试用）",
            value=", ".join(str(f) for f in cur_belief_fields),
            placeholder="例：market_demand, competitor_strength",
            key="settings_belief_fields",
        )
        cur_multi_entity_mode = bool(cur_settings.get("multi_entity_mode", False))
        new_multi_entity_mode = st.checkbox(
            "多主体模式（阶段十七，4.9 节——`vars` 需要按「entities 私有信息 + "
            "shared_vars 共享信息」的结构组织，详情页才会按主体分 tab 展示）",
            value=cur_multi_entity_mode, key="settings_multi_entity_mode",
        )
        cur_split_decision_calls = bool(cur_settings.get("split_decision_calls", False))
        new_split_decision_calls = st.checkbox(
            "拆分为「世界演化」+「决策生成」两次调用（阶段三十三第八批，4.12 节"
            "第三步——更贴合「决策引擎独立于世界演化」的设计，但每步推进会翻倍"
            "延迟和 token 成本）",
            value=cur_split_decision_calls, key="settings_split_decision_calls",
        )
        cur_resource_relations = cur_settings.get("resource_relations") or []
        with st.expander("高级：声明资源转移关系（阶段十六，可选）"):
            st.markdown(
                '<span class="ws-muted">声明两个资源字段之间的"转移"关系后，引擎会在'
                "每步推进时检查变化量是否大致相反（默认允许 10% 偏差），不一致时只在"
                "时间线上提示，不修改任何数值。</span>",
                unsafe_allow_html=True,
            )
            new_resource_relations_text = st.text_area(
                "资源转移关系（JSON 数组，可选）",
                value=json.dumps(cur_resource_relations, ensure_ascii=False) if cur_resource_relations else "",
                key="settings_resource_relations",
                height=80,
                placeholder='[{"type": "transfer", "from": "cash", "to": "inventory.value"}]',
            )
        cur_causal_lines = cur_settings.get("causal_lines") or []
        with st.expander("高级：手动调整因果线声明与未来因果树（可选，因果线本身默认就有）"):
            st.markdown(
                '<span class="ws-muted">因果线是默认基础机制，每条线自带的未来因果树'
                "（`future_tree.branches`）也已经落盘——这里只是提供手动覆盖/改名/直接"
                "编辑分支的入口。要标记某个分支\"已解决/已失效\"，更推荐去「因果线总览」"
                "标签页对应那条线下面直接点按钮；对某条线的整体修改意见，也建议在"
                "「因果线总览」里填写，会更精准地喂给下一步推进。</span>",
                unsafe_allow_html=True,
            )
            new_causal_lines_text = st.text_area(
                "因果线声明（含未来因果树，JSON 数组）",
                value=json.dumps(cur_causal_lines, ensure_ascii=False) if cur_causal_lines else "",
                key="settings_causal_lines",
                height=140,
                placeholder='[{"id": "tech", "label": "技术线", "time_granularity": "年", '
                '"future_tree": {"branches": [{"id": "fast", "description": "快速发展"}]}}]',
            )
        cur_declared_causal_graph = cur_settings.get("declared_causal_graph") or []
        with st.expander("高级：手动调整因果图先验声明（第五轮方案 5.4 节，可选）"):
            st.markdown(
                '<span class="ws-muted">先验声明的是"这条线一般来说会影响那条线"这类不依赖'
                "本次模拟历史数据就成立的常识性结构，和「因果线总览」里能看到的\"实际发生过\""
                "的因果链统计是两回事——两者都会喂给下一步推进，由 AI 自己判断参考权重，不会"
                "强制哪个优先。不支持系统自动更新，只能在这里手动增删。</span>",
                unsafe_allow_html=True,
            )
            new_declared_causal_graph_text = st.text_area(
                "因果图先验声明（JSON 数组，可选）",
                value=json.dumps(cur_declared_causal_graph, ensure_ascii=False) if cur_declared_causal_graph else "",
                key="settings_declared_causal_graph",
                height=80,
                placeholder='[{"from_line_id": "tech", "to_line_id": "industry", '
                '"note": "技术突破通常先影响行业格局"}]',
            )
        cur_calibration_notes = str(cur_settings.get("calibration_notes") or "")
        with st.expander("高级：填入真实世界参考信息（阶段十八，可选）"):
            st.markdown(
                '<span class="ws-muted">填一句真实世界的参考数据，会原样传给 AI，'
                "供推演时参考但不照抄，不会做任何自动抓取/强制校准。</span>",
                unsafe_allow_html=True,
            )
            new_calibration_notes_text = st.text_area(
                "真实世界参考信息（可选）", value=cur_calibration_notes,
                key="settings_calibration_notes", height=68,
                placeholder="参考：2024 年一线城市应届硕士平均起薪 1.2~1.8 万/月",
            )
        cur_background_entities = cur_settings.get("background_entities") or []
        with st.expander("高级：声明背景角色，简化其推理（阶段十九，可选）"):
            st.markdown(
                '<span class="ws-muted">只对"多主体模式"有意义：声明的主体名字会被当成'
                '"背景角色"，engine 每步用简单线性趋势外推它们的数值，'
                "**不采纳 AI 对它们的推理结果**。</span>",
                unsafe_allow_html=True,
            )
            new_background_entities_text = st.text_input(
                "背景角色名字（逗号分隔，可选）",
                value=", ".join(str(e) for e in cur_background_entities),
                key="settings_background_entities", placeholder="背景NPC, 围观群众",
            )
        cur_relationships = cur_settings.get("relationships") or []
        with st.expander("高级：声明关系（Relationship，阶段三十一最小起步版，可选）"):
            st.markdown(
                '<span class="ws-muted">把主体之间的关系从叙事自由文本升级为一份可'
                "独立查看的结构化列表——**不是**完整的 Influence Field/关系图架构，"
                "没有影响半径/传播路径/自动推进，只是方便浏览。</span>",
                unsafe_allow_html=True,
            )
            new_relationships_text = st.text_area(
                "关系声明（JSON 数组，可选）",
                value=json.dumps(cur_relationships, ensure_ascii=False) if cur_relationships else "",
                key="settings_relationships", height=80,
                placeholder='[{"from": "甲方", "to": "乙方", "kind": "rival", '
                '"strength": "high", "note": "对同一块市场份额有直接竞争"}]',
            )
        cur_observer_mode = bool(cur_settings.get("observer_mode", False))
        new_observer_mode = st.checkbox(
            "🔭 Observer 模式（世界独立演化最小实验，阶段三十一 4.25 节——仅自动挡下"
            "生效，提示 AI 优先让背景/宏观因果线自然演化、尽量不产生需要立刻打断的"
            "重大决策分支，不强制约束）",
            value=cur_observer_mode, key="settings_observer_mode",
        )
        cur_objectives = cur_settings.get("objectives") or []
        cur_objectives_text = ", ".join(
            str(o) for o in cur_objectives if not isinstance(o, dict)
        )
        new_objectives_text = st.text_input(
            "关注指标（逗号分隔，可选——纯记录用途，供「对比实验」页面默认关注字段参考）",
            value=cur_objectives_text,
            placeholder="例：资产净值, 工作满意度, 健康水平",
            key="settings_objectives",
        )
        new_model_version_text = st.text_input(
            "模型/Skill 版本标签（可选，阶段三十三——换了一版 prompt/skill 后自己"
            "标一下，供「预测准确性统计」按版本分组）",
            value=str(cur_settings.get("model_version") or ""),
            placeholder="例：v1 / v2-加强因果线",
            key="settings_model_version",
        )
        cur_objectives_advanced = [o for o in cur_objectives if isinstance(o, dict)]
        with st.expander("高级：声明可排序字段（阶段十四，可选）"):
            st.markdown(
                '<span class="ws-muted">声明后「对比实验」页面会出现"按关注指标排序"的辅助'
                "展示区（仅供参考，不代表最优解）。</span>",
                unsafe_allow_html=True,
            )
            new_objectives_advanced_text = st.text_area(
                "结构化关注指标（JSON 数组，可选）",
                value=json.dumps(cur_objectives_advanced, ensure_ascii=False) if cur_objectives_advanced else "",
                key="settings_objectives_advanced",
                height=80,
                placeholder='[{"label": "资产净值", "field": "resources.cash", "direction": "max"}]',
            )
        if st.button("保存设置", key="settings_save"):
            new_objectives = [o.strip() for o in new_objectives_text.split(",") if o.strip()]
            new_objectives_advanced = _safe_json_loads(new_objectives_advanced_text, None)
            new_resource_relations = _safe_json_loads(new_resource_relations_text, None)
            objectives_advanced_invalid = (
                new_objectives_advanced_text.strip() and not isinstance(new_objectives_advanced, list)
            )
            resource_relations_invalid = (
                new_resource_relations_text.strip() and not isinstance(new_resource_relations, list)
            )
            new_causal_lines = _safe_json_loads(new_causal_lines_text, None)
            causal_lines_invalid = (
                new_causal_lines_text.strip() and not isinstance(new_causal_lines, list)
            )
            new_declared_causal_graph = _safe_json_loads(new_declared_causal_graph_text, None)
            declared_causal_graph_invalid = (
                new_declared_causal_graph_text.strip() and not isinstance(new_declared_causal_graph, list)
            )
            new_relationships = _safe_json_loads(new_relationships_text, None)
            relationships_invalid = (
                new_relationships_text.strip() and not isinstance(new_relationships, list)
            )
            if objectives_advanced_invalid:
                st.error("结构化关注指标不是合法的 JSON 数组，设置未保存，请修正后重试。")
            elif resource_relations_invalid:
                st.error("资源转移关系不是合法的 JSON 数组，设置未保存，请修正后重试。")
            elif causal_lines_invalid:
                st.error("因果线声明不是合法的 JSON 数组，设置未保存，请修正后重试。")
            elif declared_causal_graph_invalid:
                st.error("因果图先验声明不是合法的 JSON 数组，设置未保存，请修正后重试。")
            elif relationships_invalid:
                st.error("关系声明不是合法的 JSON 数组，设置未保存，请修正后重试。")
            else:
                if isinstance(new_objectives_advanced, list):
                    new_objectives = new_objectives + [
                        o for o in new_objectives_advanced if isinstance(o, dict)
                    ]
                resource_relations_to_save = (
                    [r for r in new_resource_relations if isinstance(r, dict)]
                    if isinstance(new_resource_relations, list) else []
                )
                causal_lines_to_save = (
                    [line for line in new_causal_lines if isinstance(line, dict)]
                    if isinstance(new_causal_lines, list) else []
                )
                declared_causal_graph_to_save = (
                    [item for item in new_declared_causal_graph if isinstance(item, dict)]
                    if isinstance(new_declared_causal_graph, list) else []
                )
                relationships_to_save = (
                    relationship_mod.normalize_relationships(new_relationships)
                    if isinstance(new_relationships, list) else []
                )
                new_background_entities = [
                    e.strip() for e in new_background_entities_text.split(",") if e.strip()
                ]
                update_settings(
                    DATA_DIR, sim_id,
                    options_count=int(new_options_count),
                    time_granularity_mode=new_mode,
                    time_granularity=new_time_granularity,
                    time_granularity_guide=new_time_granularity_guide,
                    resource_fields=[
                        f.strip() for f in new_resource_fields_text.split(",") if f.strip()
                    ],
                    belief_fields=[
                        f.strip() for f in new_belief_fields_text.split(",") if f.strip()
                    ],
                    resource_relations=resource_relations_to_save,
                    causal_lines=causal_lines_to_save,
                    declared_causal_graph=declared_causal_graph_to_save,
                    objectives=new_objectives,
                    multi_entity_mode=bool(new_multi_entity_mode),
                    split_decision_calls=bool(new_split_decision_calls),
                    calibration_notes=new_calibration_notes_text.strip(),
                    background_entities=new_background_entities,
                    hierarchical_agent_mode=bool(new_background_entities),
                    relationships=relationships_to_save,
                    observer_mode=bool(new_observer_mode),
                    model_version=new_model_version_text.strip(),
                )
                st.success("设置已更新，下一步推进开始生效。")
                st.rerun()

    # ── 控制条：暂停/恢复/结束 ──
    ctrl1, ctrl2, ctrl3 = st.columns(3)
    with ctrl1:
        if manifest.status == "active":
            if st.button("暂停", use_container_width=True):
                set_status(DATA_DIR, sim_id, "paused")
                st.rerun()
        elif manifest.status == "paused":
            if st.button("恢复", use_container_width=True):
                set_status(DATA_DIR, sim_id, "active")
                st.rerun()
    with ctrl2:
        if manifest.status != "ended" and st.button("标记为已结束", use_container_width=True):
            set_status(DATA_DIR, sim_id, "ended")
            st.rerun()

    # ── 自动挡配置 ──
    st.markdown("#### 推进模式")
    is_autopilot = manifest.pilot_mode == "autopilot"
    mode_label = "自动挡（代理代选）" if is_autopilot else "手动挡（你来选）"
    st.markdown(
        f'<span class="ws-muted">当前分支「{manifest.branch}」：{mode_label}'
        "，每条分支的推进模式/自动挡画像各自独立，互不影响；新分支创建时"
        "会继承来源分支当时的配置，之后可以各自单独调整。</span>",
        unsafe_allow_html=True,
    )

    with st.expander("配置自动挡", expanded=False):
        ap_cfg = manifest.autopilot or {}
        enabled = st.checkbox("开启自动挡", value=is_autopilot and bool(ap_cfg.get("enabled")))
        principles_text = st.text_area(
            "原则/偏好（每行一条）",
            value="\n".join(ap_cfg.get("principles") or []),
            height=90,
            help="会原样拼进推进 prompt，作为代理做选择时的约束条件",
        )
        risk_preference = st.selectbox(
            "风险偏好", options=["conservative", "balanced", "aggressive"],
            index=["conservative", "balanced", "aggressive"].index(
                ap_cfg.get("risk_preference", "balanced")
            ),
            format_func=lambda v: {"conservative": "保守", "balanced": "均衡", "aggressive": "进取"}[v],
        )
        review_mode = st.selectbox(
            "review_mode", options=["silent", "notify_each_step", "pause_on_major_decision"],
            index=["silent", "notify_each_step", "pause_on_major_decision"].index(
                ap_cfg.get("review_mode", "silent")
            ),
            format_func=lambda v: {
                "silent": "静默托管",
                "notify_each_step": "每步通知",
                "pause_on_major_decision": "重大决策时暂停",
            }[v],
        )
        allow_custom_options = st.checkbox(
            "允许代理跳出候选列表，自己提出更合理的选项",
            value=bool(ap_cfg.get("allow_custom_options")),
            help="系统给出的候选方向只是系统的建议；开启后，代理如果判断候选列表里"
                 "没有一个足够合理，可以自己提出一个候选列表之外的新方向，而不是"
                 "被迫矮子里拔将军。",
        )
        # 阶段三十二后续（4.5 节第一批，情境化条件策略，`next_doc/
        # world_simulator_agent_preview_and_adaptive_policy_plan.md`）：
        # `if` 只识别 4.1 已落地的 risk_level/reversibility 六个枚举值，
        # 不支持自定义条件表达式，所以用下拉框限制输入，从源头避免出现
        # 无法识别的条件（同 `autopilot._normalize_conditional_
        # policies()` 的兜底逻辑相呼应，双重保险）。
        st.markdown("**情境化条件策略（可选）**")
        st.caption("当某个候选选项满足下面选的条件时，额外给代理一条倾向提示。")
        cur_conditional_policies = ap_cfg.get("conditional_policies") or []
        if "autopilot_cp_rows" not in st.session_state:
            st.session_state["autopilot_cp_rows"] = (
                [dict(p) for p in cur_conditional_policies] if cur_conditional_policies else [{}]
            )
        cp_rows = st.session_state["autopilot_cp_rows"]
        _CP_IF_OPTIONS = [
            "reversible", "hard_to_reverse", "irreversible", "low", "medium", "high",
        ]
        _CP_IF_LABELS = {
            "reversible": "可逆性：可逆", "hard_to_reverse": "可逆性：难以逆转",
            "irreversible": "可逆性：不可逆", "low": "风险等级：低",
            "medium": "风险等级：中", "high": "风险等级：高",
        }
        new_conditional_policies = []
        for i, row in enumerate(cp_rows):
            cp_col1, cp_col2 = st.columns([1, 2])
            with cp_col1:
                cur_if = row.get("if") if row.get("if") in _CP_IF_OPTIONS else _CP_IF_OPTIONS[0]
                cp_if = st.selectbox(
                    "条件", options=_CP_IF_OPTIONS, index=_CP_IF_OPTIONS.index(cur_if),
                    format_func=lambda v: _CP_IF_LABELS[v], key=f"autopilot_cp_if_{i}",
                    label_visibility="collapsed",
                )
            with cp_col2:
                cp_then = st.text_input(
                    "倾向", value=row.get("then", ""), key=f"autopilot_cp_then_{i}",
                    placeholder="例：倾向选择更有探索性的选项", label_visibility="collapsed",
                )
            if cp_then.strip():
                new_conditional_policies.append({"if": cp_if, "then": cp_then.strip()})
        if st.button("+ 添加一条情境化条件", key="autopilot_cp_add"):
            st.session_state["autopilot_cp_rows"].append({})
            st.rerun()
        if st.button("保存自动挡配置"):
            set_pilot_config(
                DATA_DIR, sim_id,
                pilot_mode="autopilot" if enabled else "manual",
                autopilot={
                    "enabled": enabled,
                    "principles": [p.strip() for p in principles_text.splitlines() if p.strip()],
                    "risk_preference": risk_preference,
                    "review_mode": review_mode,
                    "allow_custom_options": allow_custom_options,
                    "conditional_policies": new_conditional_policies,
                },
            )
            st.rerun()

        # 阶段三十二后续（4.5 节第二批，Agent Preview，`next_doc/
        # world_simulator_agent_preview_and_adaptive_policy_plan.md`
        # 4.4 节）：用内置测试情境单独跑一次画像选择，不写入任何正式
        # 模拟数据，缩短"改画像 → 靠真实跑模拟才能看到效果"的反馈
        # 周期。每次点击会产生真实的 LLM 调用（4 个内置情境 = 4 次
        # 独立调用），不自动触发。
        st.markdown("---")
        st.caption(
            f"🔍 预览这个画像的决策倾向（会用 {len(agent_preview_mod.BUILTIN_SCENARIOS)} "
            "个内置通用测试情境各跑一次，不影响这个实例的正式模拟数据）"
        )
        if st.button("运行 Agent Preview", key=f"agent_preview_run_{sim_id}"):
            preview_profile = {
                "principles": [p.strip() for p in principles_text.splitlines() if p.strip()],
                "risk_preference": risk_preference,
                "conditional_policies": new_conditional_policies,
            }
            with st.spinner("正在用测试情境跑一遍这个画像..."):
                try:
                    cfg = _load_cfg()
                    preview_results = agent_preview_mod.run_agent_preview(
                        cfg, PROJECT_ROOT, preview_profile,
                    )
                except agent_preview_mod.AgentPreviewError as exc:
                    st.error(f"Agent Preview 运行失败：{exc}")
                except ImportError as exc:
                    st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")
                else:
                    st.session_state[f"agent_preview_results_{sim_id}"] = preview_results

        preview_results = st.session_state.get(f"agent_preview_results_{sim_id}")
        if preview_results:
            for r in preview_results:
                if r.error:
                    st.markdown(
                        f'<div class="ws-card"><div class="ws-card-title">测试情境：{_html_text(r.scenario_title)}</div>'
                        f'<div class="ws-muted">运行失败：{_html_text(r.error)}</div></div>',
                        unsafe_allow_html=True,
                    )
                    continue
                policy_line = (
                    f'<div class="ws-muted">引用的原则：{_html_text(r.referenced_policy)}</div>'
                    if r.referenced_policy else ""
                )
                st.markdown(
                    f'<div class="ws-card"><div class="ws-card-title">测试情境：{_html_text(r.scenario_title)}</div>'
                    f'<div class="ws-chapter-choice">选择了：{_html_text(r.chosen_option_label)}</div>'
                    f'<div class="ws-muted">理由：{_html_text(r.reason)}</div>'
                    + policy_line + "</div>",
                    unsafe_allow_html=True,
                )

        # 阶段三十四（4.5 节第三批，用户反馈反哺画像，`next_doc/
        # world_simulator_agent_preview_and_adaptive_policy_plan.md`
        # 5.3/5.4 节）：汇总这条分支下尚未确认的「不符合预期」反馈
        # （时间线上「🚩 反馈这次代理的选择」入口写入的），命中同一
        # 关键词分组次数够多时额外给一句归纳提示；系统只展示/建议，
        # 画像的实际取值调整（风险偏好/情境化条件策略）仍然只能由
        # 用户在上面手动改完点「保存自动挡配置」。
        branch_feedback = [
            f for f in policy_feedback_mod.load_all(DATA_DIR, sim_id) if f.branch == manifest.branch
        ]
        if branch_feedback:
            fb_summary = policy_feedback_mod.summarize_feedback(branch_feedback, branch=manifest.branch)
            unacked = fb_summary["unacknowledged"]
            st.markdown("---")
            with st.expander(f"📋 最近的反馈（{len(unacked)} 条未读）", expanded=bool(unacked)):
                if fb_summary["suggestion"]:
                    st.info(fb_summary["suggestion"])
                if not unacked:
                    st.caption("没有未读反馈。")
                for fb in unacked:
                    fb_col1, fb_col2 = st.columns([5, 1])
                    with fb_col1:
                        st.markdown(
                            f'<div class="ws-card"><div class="ws-card-title">第 {fb.step} 步</div>'
                            f'<div class="ws-muted">{_html_text(fb.user_reason)}</div></div>',
                            unsafe_allow_html=True,
                        )
                    with fb_col2:
                        if st.button("已阅", key=f"policy_feedback_ack_{fb.id}"):
                            policy_feedback_mod.acknowledge(DATA_DIR, sim_id, fb.id)
                            st.rerun()

    if is_autopilot and manifest.status == "active":
        active_run = st.session_state.get("autopilot_run")
        is_running = bool(active_run and active_run.get("sim_id") == sim_id)
        if is_running:
            st.markdown(
                f'<span class="ws-muted">正在自动连续推进：已完成 {active_run["done"]}/'
                f'{active_run["target"]} 步，即将继续……</span>',
                unsafe_allow_html=True,
            )
            if st.button("⏹ 停止连续推进", key="autopilot_run_cancel"):
                st.session_state.pop("autopilot_run", None)
                st.rerun()
        else:
            st.markdown(
                '<span class="ws-muted">自动挡配置好之后，代理会自己在候选方向里选一个继续'
                "推进；点「连续自动推进」之后每完成一步都会刷新一次页面（当前状态卡片、时间线"
                "跟着更新），不用等全部步数跑完才看到结果——如果 review_mode 配置成「重大决策时"
                "暂停」，遇到重大决策会自动停下来等你确认，不会一直跑到步数用完。</span>",
                unsafe_allow_html=True,
            )
            auto_cols = st.columns([2, 3, 3])
            with auto_cols[0]:
                auto_steps = st.number_input(
                    "连续推进步数", min_value=1, max_value=50, value=5, step=1,
                    key="autopilot_run_steps", label_visibility="collapsed",
                )
            with auto_cols[1]:
                run_clicked = st.button("▶▶ 连续自动推进", key="autopilot_run_continuous", type="primary")
            with auto_cols[2]:
                single_clicked = st.button("▶ 只推进一步（测试代理决策）", key="autopilot_run_single")

            if run_clicked or single_clicked:
                target_steps = 1 if single_clicked else int(auto_steps)
                st.session_state["autopilot_run"] = {
                    "sim_id": sim_id, "remaining": target_steps, "target": target_steps, "done": 0,
                }
                st.rerun()

    # ── 推进面板 ──
    st.markdown("#### 推进下一步")
    if manifest.status == "ended":
        st.markdown('<span class="ws-muted">模拟已结束，无法继续推进。</span>', unsafe_allow_html=True)
    elif manifest.status == "paused":
        st.markdown('<span class="ws-muted">模拟已暂停，恢复后才能继续推进。</span>', unsafe_allow_html=True)
    else:
        chosen_id: Optional[str] = None
        if current.options:
            opportunity = getattr(current, "decision_opportunity", None)
            decision_reason = ""
            if isinstance(opportunity, dict):
                decision_reason = str(opportunity.get("decision_reason") or "").strip()
            if not decision_reason:
                # 兼容旧数据（阶段三十三第二批产出、还没有
                # `decision_opportunity` 容器的历史状态）。
                decision_reason = getattr(current, "decision_reason", "") or ""
            if decision_reason:
                st.markdown(
                    f'<div class="ws-muted">📌 为什么现在需要决定：{_html_text(decision_reason)}</div>',
                    unsafe_allow_html=True,
                )
            # 第五轮方案 5.2 节：`decision_opportunity.max_urgency` 是
            # 这一批 options 里最高的紧急档位（纯聚合，不是新的 LLM
            # 判断），复用既有的紧急度徽章样式展示在决策背景说明旁边。
            max_urgency = ""
            if isinstance(opportunity, dict):
                max_urgency = str(opportunity.get("max_urgency") or "").strip()
            if max_urgency and max_urgency in _URGENCY_LABELS:
                st.markdown(
                    f'<div class="ws-muted">整体紧急度：'
                    f'<span class="ws-uncertain-badge ws-urgency-badge-{max_urgency}">'
                    f'{_URGENCY_LABELS.get(max_urgency, max_urgency)}</span></div>',
                    unsafe_allow_html=True,
                )
            st.markdown('<span class="ws-muted">选一个方向继续，或直接点「按默认走向推进」。</span>', unsafe_allow_html=True)
            # `continue_` 前缀的"维持现状"选项统一排在列表最后展示
            # （阶段三十三第二批，4.6 节），不改变其它选项的相对顺序。
            ordered_options = sorted(
                current.options,
                key=lambda o: str(getattr(o, "id", "") or "").startswith("continue_"),
            )
            cols = st.columns(min(len(ordered_options), 3) or 1)
            for i, opt in enumerate(ordered_options):
                with cols[i % len(cols)]:
                    st.markdown(
                        f'<div class="ws-card"><div class="ws-card-title">{opt.label}</div>'
                        f'<div class="ws-muted">{opt.description}</div>'
                        + _option_meta_html(opt)
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                    if st.button(f"选择「{opt.label}」", key=f"choose_{opt.id}"):
                        chosen_id = opt.id

        default_clicked = st.button("按默认走向推进")

        custom_clicked = False
        custom_label = ""
        custom_desc = ""
        with st.expander("✏️ 都不满意？自己写一个选项"):
            st.markdown(
                '<span class="ws-muted">系统给出的候选方向终究只是建议，想到更合理的走向时，'
                "直接填在这里，点「应用这个选项」就会按你写的方向推进，不需要先加进候选列表。"
                "</span>",
                unsafe_allow_html=True,
            )
            custom_label = st.text_input("选项标题", key="custom_option_label", placeholder="例：先按兵不动，观察一个季度再说")
            custom_desc = st.text_area("选项说明（可选）", key="custom_option_desc", height=70)
            custom_clicked = st.button("应用这个选项", key="custom_option_apply")

        if chosen_id or default_clicked or custom_clicked:
            custom_payload = None
            if custom_clicked:
                if not (custom_label or "").strip():
                    st.error("自定义选项至少需要填写标题。")
                    custom_payload = "__invalid__"
                else:
                    custom_payload = {"label": custom_label.strip(), "description": custom_desc.strip()}

            if custom_payload != "__invalid__":
                with st.spinner("正在推进..."):
                    try:
                        cfg = _load_cfg()
                        advance(
                            cfg, PROJECT_ROOT, DATA_DIR, sim_id,
                            choice_option_id=None if custom_payload else chosen_id,
                            custom_option=custom_payload if isinstance(custom_payload, dict) else None,
                            chosen_by="user",
                        )
                    except (SimEngineError, SimAlreadyEndedError, SimPausedError) as exc:
                        st.error(f"推进失败：{exc}")
                    except ImportError as exc:
                        st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")
                    else:
                        st.rerun()

        # ── 快进（Event-Driven 决策点引擎，`next_doc/world_simulator_
        # event_driven_engine_and_full_architecture_plan.md` 2.1 节
        # 第一批）：和上面"推进 1 步"并列、不替换，用户设定一个步数
        # 上限，点击后连续调用默认走向的 advance()，遇到
        # major_decision 或出现候选 options 就停下来，展示"跳过了
        # N 步"的摘要；被折叠的每一步可以展开查看原始细节（数据本来
        # 就完整落盘了，只是 UI 默认折叠）。
        with st.expander("⏩ 快进（连续推进，遇到重大决策或候选分支自动停下）"):
            st.markdown(
                '<span class="ws-muted">适合"这几步大概率没什么值得决定的事"的场景——'
                "不用一步步手动点，遇到需要你做决定的时刻会自动停下来。"
                "</span>",
                unsafe_allow_html=True,
            )
            ff_max_steps = st.number_input(
                "最多快进多少步", min_value=1, max_value=50, value=10, step=1, key="fast_forward_max_steps",
            )
            ff_clicked = st.button("开始快进", key="fast_forward_button")
            if ff_clicked:
                with st.spinner("正在快进..."):
                    try:
                        cfg = _load_cfg()
                        ff_result = fast_forward(
                            cfg, PROJECT_ROOT, DATA_DIR, sim_id, max_steps=int(ff_max_steps),
                        )
                    except (SimEngineError, SimAlreadyEndedError, SimPausedError) as exc:
                        st.error(f"快进失败：{exc}")
                    except ImportError as exc:
                        st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")
                    else:
                        st.session_state["fast_forward_last_result"] = ff_result.to_dict()
                        st.session_state["fast_forward_last_skipped"] = [
                            {"step": s.step, "time_label": s.time_label, "summary": s.summary, "narrative": s.narrative}
                            for s in ff_result.skipped_states
                        ]
                        st.rerun()

            last_ff = st.session_state.get("fast_forward_last_result")
            if last_ff and last_ff.get("sim_id") == sim_id:
                _stop_reason_labels = {
                    "major_decision": "遇到重大决策，已停下等待你确认",
                    "options": "出现了候选分支，已停下等待你选择",
                    "max_steps": "已达到本次设定的步数上限",
                    "ended": "快进过程中模拟已结束",
                    "paused": "快进过程中模拟已暂停",
                }
                stop_label = _stop_reason_labels.get(last_ff.get("stop_reason", ""), last_ff.get("stop_reason", ""))
                st.markdown(
                    f'<div class="ws-muted">上次快进：从第 {last_ff.get("start_step")} 步推进到第 '
                    f'{last_ff.get("final_step")} 步（实际调用 {last_ff.get("steps_run")} 步）。'
                    f'{stop_label}。</div>',
                    unsafe_allow_html=True,
                )
                if last_ff.get("summary_text"):
                    st.markdown(f'<div class="ws-muted">{_html_text(last_ff["summary_text"])}</div>', unsafe_allow_html=True)
                skipped = st.session_state.get("fast_forward_last_skipped") or []
                if skipped:
                    with st.expander(f"展开查看被折叠的 {len(skipped)} 步原始细节"):
                        for item in skipped:
                            label = item.get("time_label") or f"第 {item.get('step')} 步"
                            st.markdown(f"**{label}**")
                            st.markdown(f'<div class="ws-muted">{_html_text(item.get("summary") or "")}</div>', unsafe_allow_html=True)
                            if item.get("narrative"):
                                st.markdown(_html_text(item["narrative"]))
                            st.markdown("---")

        # 阶段三十六第三批（2.3 节）：`independent_line_advance` 开启
        # 且至少有一条线声明了 `owned_vars` 时，额外给一个"独立推进
        # 一步"入口，调用 `engine.advance_lines()` 而不是上面的
        # `advance()`——两条路径并列展示，不互相替代，用户自己选用
        # 哪一条（比如决策点用 `advance()`，纯背景线用这里）。
        if manifest.settings.get("independent_line_advance") and any(
            isinstance(line, dict) and isinstance(line.get("owned_vars"), list) and line.get("owned_vars")
            for line in (manifest.settings.get("causal_lines") or [])
        ):
            with st.expander("🧵 独立推进因果线（只推进到点的线，不产出候选选项）"):
                st.markdown(
                    '<span class="ws-muted">已开启多尺度因果线独立推进（实验性）。'
                    "点击后只有本步到点的线会真正发起调用，其它线保持不变；"
                    "这一步不会产出候选分支，重大决策仍然要用上面的\"推进\"。"
                    "</span>",
                    unsafe_allow_html=True,
                )
                il_clicked = st.button("独立推进一步", key="independent_advance_button")
                if il_clicked:
                    with st.spinner("正在独立推进..."):
                        try:
                            cfg = _load_cfg()
                            advance_lines(cfg, PROJECT_ROOT, DATA_DIR, sim_id)
                        except OwnedVarsOverlapError as exc:
                            st.error(f"独立推进失败：{exc}")
                        except (SimEngineError, SimAlreadyEndedError, SimPausedError) as exc:
                            st.error(f"独立推进失败：{exc}")
                        except ImportError as exc:
                            st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")
                        else:
                            st.rerun()

    causal_lines_meta = manifest.settings.get("causal_lines") or []

    def _render_timeline_subview() -> None:
        causal_line_filter = None
        if causal_lines_meta:
            line_options = ["全部"] + [
                str(line.get("id")) for line in causal_lines_meta if isinstance(line, dict) and line.get("id")
            ]
            line_labels = {"全部": "全部"}
            line_labels.update(
                {
                    str(line.get("id")): f'{line.get("label") or line.get("id")}（{line.get("id")}）'
                    for line in causal_lines_meta if isinstance(line, dict) and line.get("id")
                }
            )
            picked_line = st.selectbox(
                "按因果线筛选（阶段二十二，可选）",
                options=line_options,
                format_func=lambda x: line_labels.get(x, x),
                key="timeline_causal_line_filter",
            )
            causal_line_filter = None if picked_line == "全部" else picked_line
        _render_timeline(
            list(reversed(history)), sim_id=sim_id, source_branch=manifest.branch,
            causal_lines_meta=causal_lines_meta, causal_line_filter=causal_line_filter,
        )

    # 阶段二十五（4.17 节，因果线 UI）：因果线是模拟的默认基础机制，
    # 不再要求实例声明过 `causal_lines` 才显示"因果线总览"子标签页——
    # 即使还没有任何声明/自动登记的线，总览标签页也会展示引导文案，
    # 推进第一步之后会自动出现内容（见 `_render_causal_lines_overview`）。
    overview_tab, timeline_tab = st.tabs(["📊 因果线总览", "📜 时间线"])
    with overview_tab:
        _render_causal_lines_overview(
            history, causal_lines_meta, manifest=manifest, sim_id=sim_id,
        )
    with timeline_tab:
        _render_timeline_subview()

    # ── 分支管理 ──
    st.markdown("#### 分支")
    try:
        branches_detailed = bm.list_branches_detailed(DATA_DIR, sim_id)
    except Exception as exc:  # noqa: BLE001
        st.error(f"读取分支列表失败：{exc}")
        branches_detailed = [{"branch": "main", "is_current": True}]

    st.markdown(
        '<span class="ws-muted">当前活跃分支：<b>' + manifest.branch + "</b>"
        "。每条分支都是一条独立时间线，互不覆盖——「回滚重新选」的做法是"
        "从某个历史节点开一条新分支，原时间线原样保留，可以在下方「对比"
        "视图」里并排查看。</span>",
        unsafe_allow_html=True,
    )

    _REVIEW_MODE_LABEL = {
        "silent": "静默托管", "notify_each_step": "每步通知",
        "pause_on_major_decision": "重大决策暂停",
    }
    _RISK_LABEL = {"conservative": "保守", "balanced": "均衡", "aggressive": "进取"}

    for info in branches_detailed:
        b = info["branch"]
        cols = st.columns([3, 1, 1, 1])
        with cols[0]:
            marker = " ← 当前" if info.get("is_current") else ""
            origin = (
                f"，分叉自 {info['source_branch']}/第 {info['from_step']} 步"
                if info.get("source_branch") else "，实例本体"
            )
            progress = (
                f"进度：第 {info.get('current_step', '?')} 步（历史 {info.get('step_count', '?')} 条）"
            )
            if info.get("pilot_mode") == "autopilot" and info.get("autopilot_enabled"):
                custom_note = "，可跳出候选自选" if info.get("allow_custom_options") else ""
                pilot_note = (
                    f"自动挡 · {_RISK_LABEL.get(info.get('risk_preference'), info.get('risk_preference'))} · "
                    f"{_REVIEW_MODE_LABEL.get(info.get('review_mode'), info.get('review_mode'))}"
                    f"{custom_note} · {info.get('principles_count', 0)} 条原则"
                )
            else:
                pilot_note = "手动挡"
            st.markdown(
                f'<div><b>{b}</b>{marker}</div>'
                f'<div class="ws-muted">创建于 {_format_local_time(info.get("created_at"))}{origin}</div>'
                f'<div class="ws-muted">{progress} · {pilot_note}</div>',
                unsafe_allow_html=True,
            )
        with cols[1]:
            if not info.get("is_current") and st.button("切换到这条", key=f"switch_{b}"):
                try:
                    bm.switch_branch(DATA_DIR, sim_id, b)
                except bm.BranchError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        with cols[2]:
            if st.button("加入对比", key=f"cmp_add_{b}"):
                selection = st.session_state.setdefault("compare_selection", [])
                pair = (sim_id, b)
                if pair not in selection:
                    selection.append(pair)
                st.session_state["view"] = "compare"
                st.rerun()
        with cols[3]:
            # `main` 不可删（是实例本体），当前活跃分支也不可删（要删得
            # 先切走）——两种情况都不出按钮，而不是出了按钮再报错，减少
            # 一次无意义的点击往返。
            if b == "main":
                st.markdown('<span class="ws-muted">—</span>', unsafe_allow_html=True)
            elif info.get("is_current"):
                st.markdown('<span class="ws-muted">先切走才能删</span>', unsafe_allow_html=True)
            else:
                confirm_key = f"confirm_delete_{b}"
                if st.session_state.get(confirm_key):
                    if st.button("确认删除？", key=f"delete_confirm_{b}", type="primary"):
                        try:
                            bm.delete_branch(DATA_DIR, sim_id, b)
                        except bm.BranchError as exc:
                            st.error(str(exc))
                        finally:
                            st.session_state.pop(confirm_key, None)
                        st.rerun()
                else:
                    if st.button("🗑 删除", key=f"delete_ask_{b}"):
                        st.session_state[confirm_key] = True
                        st.rerun()

    with st.expander("从历史节点开一条新分支（回滚重新选）"):
        max_step = history[-1].step if history else 0
        st.markdown(
            '<span class="ws-muted">分叉出来的新分支，选中那一步会重新变成"还没做过选择"的'
            "状态（候选方向原样保留），可以直接在上面「推进下一步」里重新选——不会是"
            "\"看起来回滚了，其实还是当时选的那个\"。第 0 步就是最开始创建时的初始状态。"
            "</span>",
            unsafe_allow_html=True,
        )
        restart_col, fork_col = st.columns([1, 2])
        with restart_col:
            if st.button("🔄 直接从最开始重新开始", key="fork_restart_from_zero"):
                try:
                    new_branch = bm.fork_branch(
                        DATA_DIR, sim_id, from_step=0,
                        source_branch=manifest.branch, switch=True,
                    )
                except bm.BranchError as exc:
                    st.error(str(exc))
                else:
                    st.success(f"已回到最开始，新分支 {new_branch} 可以重新选了。")
                    st.rerun()
        with fork_col:
            fork_step = st.number_input(
                "或者选一个具体的步数（0 = 最开始）",
                min_value=0, max_value=max_step, value=max_step, step=1, key="fork_step",
            )
            if st.button("从这一步创建分支并切换过去"):
                try:
                    new_branch = bm.fork_branch(
                        DATA_DIR, sim_id, from_step=int(fork_step),
                        source_branch=manifest.branch, switch=True,
                    )
                except bm.BranchError as exc:
                    st.error(str(exc))
                else:
                    st.success(f"已创建分支 {new_branch} 并切换为当前分支。")
                    st.rerun()

    # 连续自动推进：这一步跑完、页面正常渲染完（当前状态卡片、时间线都
    # 已经是刚推进完的最新数据）之后，再触发下一次 rerun 去跑下一步。
    #
    # 这里特意先 `time.sleep()` 停顿一下再 `st.rerun()`，不是可有可无的
    # 装饰——如果渲染完立刻 rerun，两次 rerun 之间"完整渲染、没有被遮罩
    # 变灰"的这一帧存在时间极短（远小于一次网络往返/浏览器重绘的时间），
    # 用户实际观感就是"一直卡在旋转指示器上，正文一直是灰的"，看不到
    # 任何数据更新——这正是之前反馈的问题。停顿几秒钟，让浏览器有机会
    # 真正把这一步的最新结果绘出来、用户看得到，再进入下一步。
    if schedule_next_autostep:
        time.sleep(2.5)
        st.rerun()


# ─────────────────────────────────────────────────────────────
# 页面：对比视图
# ─────────────────────────────────────────────────────────────


def page_compare() -> None:
    st.markdown("## 对比视图", unsafe_allow_html=True)
    st.markdown(
        '<span class="ws-muted">并排对比两条时间线——可以是同一实例的不同'
        "分支（决策推演分析），也可以是两个独立实例。</span>",
        unsafe_allow_html=True,
    )

    all_manifests = list_simulations(DATA_DIR)
    if len(all_manifests) < 1:
        st.info("还没有可对比的模拟实例，先去创建一个。")
        return

    sim_options = {m.sim_id: m.title for m in all_manifests}
    selection: List[Any] = st.session_state.setdefault("compare_selection", [])

    st.markdown("#### 选择两条时间线")
    col_a, col_b = st.columns(2)
    picks = []
    for i, col in enumerate((col_a, col_b)):
        with col:
            default_sim = selection[i][0] if i < len(selection) else list(sim_options)[0]
            sim_choice = st.selectbox(
                f"实例 {i + 1}", options=list(sim_options), format_func=lambda k: sim_options[k],
                index=list(sim_options).index(default_sim) if default_sim in sim_options else 0,
                key=f"cmp_sim_{i}",
            )
            try:
                branch_options = bm.list_branches(DATA_DIR, sim_choice)
            except Exception:  # noqa: BLE001
                branch_options = ["main"]
            default_branch = (
                selection[i][1] if i < len(selection) and selection[i][0] == sim_choice else "main"
            )
            branch_choice = st.selectbox(
                f"分支 {i + 1}", options=branch_options,
                index=branch_options.index(default_branch) if default_branch in branch_options else 0,
                key=f"cmp_branch_{i}",
            )
            picks.append((sim_choice, branch_choice))

    if st.button("← 返回列表"):
        st.session_state["view"] = "list"
        st.rerun()

    # ── Hypothesis Engine 轻量入口（阶段二十一，4.15 节）：只针对
    # "实例 1" 的当前状态给建议——不确定性识别、分叉、稳健性判断都是
    # 单实例内部的事情，两条时间线的"对比"语义在这里只是复用页面位置，
    # 不代表这个功能本身是"对比两个实例" ──
    with st.expander("🔍 让系统建议关键不确定性（阶段二十一，可选）"):
        hyp_sim_id, hyp_branch = picks[0]
        st.markdown(
            f'<span class="ws-muted">针对「实例 1」（{sim_options.get(hyp_sim_id, hyp_sim_id)} · '
            f"分支 {hyp_branch}）当前状态里被标注为「低置信度估计」的字段给出建议，"
            "选一个字段分叉出几个假设世界看看结果是否稳健。</span>",
            unsafe_allow_html=True,
        )
        if st.button("分析关键不确定性", key="hyp_suggest_btn"):
            try:
                hyp_store = SimStore.for_root(DATA_DIR, hyp_sim_id)
                hyp_manifest = hyp_store.load_manifest()
                hyp_current = hyp_store.load_current_state(hyp_branch)
                if hyp_current is None:
                    st.error("这条分支还没有可用的当前状态。")
                else:
                    st.session_state["hyp_suggestions"] = hyp_mod.suggest_critical_uncertainties(
                        hyp_manifest, hyp_current
                    )
                    st.session_state["hyp_source"] = (hyp_sim_id, hyp_branch, hyp_current.step)
            except Exception as exc:  # noqa: BLE001
                st.error(f"分析失败：{exc}")

        suggestions = st.session_state.get("hyp_suggestions")
        if suggestions is not None:
            if not suggestions:
                st.info("当前状态没有被标注为「低置信度估计」的字段，暂无建议。")
            else:
                st.markdown("**候选关键不确定性**（按是多少条因果链的共同起点排序）：")
                for s in suggestions:
                    st.markdown(f"- `{s['field']}`：{s['why']}")
                field_options = [s["field"] for s in suggestions]
                chosen_field = st.selectbox("选一个字段分叉假设世界", options=field_options, key="hyp_field")

                # ── 第八轮批次三（4.2 节）：半自动实验设计建议，展示在
                # 手动填写假设方向之前，用户可以采纳也可以完全忽略，
                # 原有手动交互路径不变 ──
                if st.button("💡 让系统建议一组实验设计", key="hyp_design_suggest_btn"):
                    try:
                        cfg = _load_cfg()
                        with st.spinner("正在生成实验设计建议..."):
                            st.session_state["hyp_design_suggestions"] = hyp_mod.suggest_experiment_design(
                                cfg, PROJECT_ROOT, suggestions,
                            )
                    except ImportError as exc:
                        st.error(f"未检测到 mini_agent 框架，无法生成建议：{exc}")
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"生成实验设计建议失败：{exc}")

                design_suggestions = st.session_state.get("hyp_design_suggestions")
                if design_suggestions is not None:
                    if not design_suggestions:
                        st.info("系统没有给出有区分度的组合建议，可以继续手动填写假设方向。")
                    else:
                        st.markdown("**建议的实验组合**（仅供参考，判断标准是「提示而非精确计算」，可以不采纳）：")
                        adopt_flags = []
                        for idx, combo in enumerate(design_suggestions):
                            combo_label = "；".join(
                                f"{k}={v}" for k, v in combo["combination"].items()
                            )
                            checked = st.checkbox(
                                f"{combo_label}（{combo['why']}）",
                                key=f"hyp_design_pick_{idx}",
                            )
                            adopt_flags.append(checked)
                        if st.button("采纳选中组合到假设列表", key="hyp_design_adopt_btn"):
                            picked = [
                                c for c, flag in zip(design_suggestions, adopt_flags) if flag
                            ]
                            if not picked:
                                st.warning("请至少勾选一组建议组合再采纳。")
                            else:
                                lines = []
                                for combo in picked:
                                    label = "；".join(
                                        f"{k}={v}" for k, v in combo["combination"].items()
                                    )
                                    why = str(combo.get("why") or "")
                                    lines.append(f"{label}: {why}" if why else label)
                                # 只更新即将渲染的假设方向文本框的初始值，不代替
                                # 用户点击「运行假设世界」——分叉本身仍需用户
                                # 手动确认，本批不做自动触发。
                                st.session_state["hyp_hypotheses_text"] = "\n".join(lines)
                                st.success("已采纳到下方假设方向文本框，确认无误后点击「运行假设世界」。")

                hyp_text = st.text_area(
                    "假设方向（每行一条，格式「方向标签」或「方向标签: 理由」）",
                    value="快速下降: 行业价格战加剧\n缓慢下降\n基本不变",
                    key="hyp_hypotheses_text", height=90,
                )
                hyp_steps = st.number_input("每个假设世界推进的步数", min_value=1, max_value=10, value=3, key="hyp_steps")
                if st.button("运行假设世界", key="hyp_run_btn"):
                    hypotheses = []
                    for line in hyp_text.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        if ":" in line:
                            label, why = line.split(":", 1)
                        elif "：" in line:
                            label, why = line.split("：", 1)
                        else:
                            label, why = line, ""
                        hypotheses.append({"assumption": label.strip(), "why": why.strip()})
                    if len(hypotheses) < 2:
                        st.error("至少需要两条假设方向才有对比意义。")
                    else:
                        src_sim_id, src_branch, src_step = st.session_state["hyp_source"]
                        try:
                            cfg = _load_cfg()
                            with st.spinner("正在分叉并推进各个假设世界..."):
                                results = hyp_mod.run_hypothesis_worlds(
                                    cfg, PROJECT_ROOT, DATA_DIR, src_sim_id,
                                    field=chosen_field, hypotheses=hypotheses,
                                    steps=int(hyp_steps), source_branch=src_branch, from_step=src_step,
                                )
                            st.session_state["hyp_results"] = results
                            st.session_state["hyp_results_sim_id"] = src_sim_id
                        except ImportError as exc:
                            st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"运行假设世界失败：{exc}")

        hyp_results = st.session_state.get("hyp_results")
        if hyp_results:
            st.markdown("**假设世界运行结果**：")
            for r in hyp_results:
                label = str(r.hypothesis.get("assumption") or r.hypothesis.get("label") or "（未命名）")
                if r.error:
                    st.markdown(f"- {label} → 分支 `{r.branch}`：❌ {r.error}")
                else:
                    st.markdown(f"- {label} → 分支 `{r.branch}`：完成 {r.steps_done} 步")
            valid_branches = [r.branch for r in hyp_results if r.branch != "?"]
            if len(valid_branches) >= 2:
                robust_fields_text = st.text_input(
                    "要检查稳健性的结果字段（逗号分隔）",
                    value=str(st.session_state.get("hyp_field", "")),
                    key="hyp_robust_fields_input",
                )
                if st.button("查看稳健性判断", key="hyp_robust_btn"):
                    fields = [f.strip() for f in robust_fields_text.split(",") if f.strip()]
                    if not fields:
                        st.error("至少填一个要检查的字段。")
                    else:
                        outcome = hyp_mod.find_robust_outcomes(
                            DATA_DIR, st.session_state["hyp_results_sim_id"], valid_branches, fields,
                        )
                        if outcome["robust_fields"]:
                            st.success("稳健结果（跨所有假设世界方向/量级基本一致）：" + "、".join(outcome["robust_fields"]))
                        if outcome["fragile_fields"]:
                            st.warning("分歧结果（不同假设世界差异明显，验证了对应字段确实关键）：" + "、".join(outcome["fragile_fields"]))
                        for s in outcome["stats"]:
                            if s.kind == "numeric":
                                st.markdown(
                                    f"- `{s.field}`：均值 {s.mean:.2f}，范围 [{s.min:.2f}, {s.max:.2f}]，"
                                    f"标准差 {s.stdev:.2f}（{s.count} 个样本）"
                                )
                            elif s.kind == "categorical":
                                dist_text = "、".join(f"{k}×{v}" for k, v in s.distribution.items())
                                st.markdown(f"- `{s.field}`：取值分布 {dist_text}（{s.count} 个样本）")
                            else:
                                st.markdown(f"- `{s.field}`：所有分支都取不到这个字段的值。")

    # ── 反事实矩阵向导（阶段三十一，4.22 节，控制变量法）：只针对
    # "实例 1" 的当前状态，一次声明多个字段各自的候选取值，批量生成
    # "每次只变一个字段"的正交世界，不需要用户手动逐个开实验 ──
    with st.expander("🧪 反事实矩阵向导（控制变量法，阶段三十一，可选）"):
        cf_sim_id, cf_branch = picks[0]
        st.markdown(
            f'<span class="ws-muted">针对「实例 1」（{sim_options.get(cf_sim_id, cf_sim_id)} · '
            f"分支 {cf_branch}）当前状态，一次声明多个字段各自的候选取值，"
            "每个取值单独生成一个世界（只变这一个字段，其它声明字段不额外"
            "设定方向），不做多字段交叉的完整析因设计。</span>",
            unsafe_allow_html=True,
        )
        cf_fields_text = st.text_area(
            "字段与候选取值（每行一个字段，格式「字段路径: 取值1, 取值2, ...」）",
            value="resources.cash: 快速增长, 基本持平, 快速下降",
            key="cf_fields_text", height=90,
        )
        cf_steps = st.number_input("每个世界推进的步数", min_value=1, max_value=10, value=3, key="cf_steps")
        if st.button("运行反事实矩阵", key="cf_run_btn"):
            fields_config: Dict[str, List[str]] = {}
            for line in cf_fields_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if ":" in line:
                    fpath, values_text = line.split(":", 1)
                elif "：" in line:
                    fpath, values_text = line.split("：", 1)
                else:
                    continue
                fpath = fpath.strip()
                values = [v.strip() for v in values_text.split(",") if v.strip()]
                if fpath and values:
                    fields_config[fpath] = values
            if not fields_config:
                st.error("至少声明一个「字段: 取值1, 取值2」格式的字段。")
            else:
                try:
                    cfg = _load_cfg()
                    with st.spinner("正在分叉并推进各个反事实世界..."):
                        cf_results = hyp_mod.build_counterfactual_matrix(
                            cfg, PROJECT_ROOT, DATA_DIR, cf_sim_id,
                            fields=fields_config, steps=int(cf_steps),
                            source_branch=cf_branch,
                        )
                    st.session_state["cf_results"] = cf_results
                except ImportError as exc:
                    st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"运行反事实矩阵失败：{exc}")

        cf_results = st.session_state.get("cf_results")
        if cf_results:
            st.markdown("**反事实矩阵运行结果**（按变化的字段分组）：")
            grouped: Dict[str, List[Any]] = {}
            for r in cf_results:
                grouped.setdefault(r.field, []).append(r)
            for field_name, variants in grouped.items():
                st.markdown(f"「{_html_text(field_name)}」：", unsafe_allow_html=True)
                for r in variants:
                    if r.error:
                        st.markdown(f"　- {r.value} → 分支 `{r.branch}`：❌ {r.error}")
                    else:
                        st.markdown(f"　- {r.value} → 分支 `{r.branch}`：完成 {r.steps_done} 步")

    if picks[0] == picks[1]:
        st.warning("请选择两条不同的时间线（实例+分支组合需不同）。")
        return

    result = bm.compare_timelines(DATA_DIR, picks)
    line_a, line_b = result["lines"]

    st.markdown("#### 并排时间线")
    col_a, col_b = st.columns(2)
    for col, line in ((col_a, line_a), (col_b, line_b)):
        with col:
            st.markdown(
                f'<div class="ws-card"><div class="ws-card-title">{line["manifest"].title}</div>'
                f'<div class="ws-muted">{line["sim_id"]} · 分支 {line["branch"]}</div></div>',
                unsafe_allow_html=True,
            )
            _render_timeline(
                list(reversed(line["history"])),
                causal_lines_meta=line["manifest"].settings.get("causal_lines"),
            )

    st.markdown("#### 关键变量对比（按 step 对齐）")
    max_len = max(len(line_a["history"]), len(line_b["history"]))
    rows = []
    for i in range(max_len):
        row: Dict[str, Any] = {"step": i}
        if i < len(line_a["history"]):
            row[f"A · {line_a['sim_id']}/{line_a['branch']}"] = json.dumps(
                line_a["history"][i].vars, ensure_ascii=False
            )
        if i < len(line_b["history"]):
            row[f"B · {line_b['sim_id']}/{line_b['branch']}"] = json.dumps(
                line_b["history"][i].vars, ensure_ascii=False
            )
        rows.append(row)
    st.table(rows)


# ─────────────────────────────────────────────────────────────
# 页面：对比实验（多策略自动挡批量对比）
# ─────────────────────────────────────────────────────────────


def page_experiment() -> None:
    st.markdown("## 🧪 对比实验", unsafe_allow_html=True)
    st.markdown(
        '<span class="ws-muted">从同一个历史节点分叉出多条分支，跑完之后去「对比视图」'
        "并排查看结果。默认「横向对比模式」：每条分支套用一份不同的自动挡策略画像，"
        "适合回答「哪个策略更好」；也可以切到「重复模式」：同一份策略画像重复跑 N 次，"
        "适合回答「这个策略稳不稳定」（阶段十，参考数据的均值/极差/标准差摘要）。"
        "不需要手动一条条分叉、一条条配置、一步步点推进。</span>",
        unsafe_allow_html=True,
    )

    all_manifests = list_simulations(DATA_DIR)
    if not all_manifests:
        st.info("还没有可用的模拟实例，先去创建一个。")
        return

    sim_options = {m.sim_id: m.title for m in all_manifests}
    sim_id = st.selectbox(
        "选择模拟实例", options=list(sim_options), format_func=lambda k: sim_options[k], key="exp_sim",
    )
    try:
        branch_options = bm.list_branches(DATA_DIR, sim_id)
    except Exception as exc:  # noqa: BLE001
        st.error(f"读取分支列表失败：{exc}")
        return

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        source_branch = st.selectbox("从哪条分支分叉（起点）", options=branch_options, key="exp_source_branch")
    with col2:
        history = bm.load_branch_timeline(DATA_DIR, sim_id, source_branch)
        max_step = history[-1].step if history else 0
        from_step = st.number_input("分叉自第几步", min_value=0, max_value=max_step, value=max_step, step=1, key="exp_from_step")
    with col3:
        steps = st.number_input("每条策略推进步数", min_value=1, max_value=30, value=3, step=1, key="exp_steps")

    st.markdown("#### 策略画像")
    repeat_mode = st.checkbox(
        "🔁 重复模式（1 个策略 × N 次重复，用于看波动/分布，而不是策略之间的横向对比）",
        key="exp_repeat_mode",
    )

    if repeat_mode:
        st.markdown(
            '<span class="ws-muted">用**同一份**策略画像重复跑 N 次，差异只来自 LLM 输出'
            "本身的随机性——适合回答「这个策略的产出稳不稳定」，而不是「哪个策略更好」。"
            "</span>",
            unsafe_allow_html=True,
        )
        rep_cols = st.columns([3, 1])
        with rep_cols[0]:
            st.markdown("**策略画像**")
            name = st.text_input("名称", value="重复采样", key="exp_rep_name")
            risk = st.selectbox(
                "风险偏好", options=["conservative", "balanced", "aggressive"],
                format_func=lambda v: {"conservative": "保守", "balanced": "均衡", "aggressive": "进取"}[v],
                key="exp_rep_risk",
            )
            review = st.selectbox(
                "review_mode", options=["silent", "notify_each_step", "pause_on_major_decision"],
                format_func=lambda v: {
                    "silent": "静默托管", "notify_each_step": "每步通知",
                    "pause_on_major_decision": "重大决策暂停",
                }[v],
                key="exp_rep_review",
            )
            allow_custom = st.checkbox("允许跳出候选自选", key="exp_rep_custom")
            principles_text = st.text_area("原则/偏好（每行一条）", height=80, key="exp_rep_principles")
            profile = {
                "name": name,
                "risk_preference": risk,
                "review_mode": review,
                "allow_custom_options": allow_custom,
                "principles": [p.strip() for p in principles_text.splitlines() if p.strip()],
            }
        with rep_cols[1]:
            n_repeats = st.number_input("重复次数", min_value=2, max_value=20, value=5, step=1, key="exp_n_repeats")

        focus_fields_default = ""
        try:
            _exp_manifest, _exp_current, _ = get_simulation(DATA_DIR, sim_id)
            focus_fields_default = ", ".join(
                str(o) for o in (_exp_manifest.settings.get("objectives") or [])
            )
        except Exception:  # noqa: BLE001 — 拿不到就用空默认值，不影响主流程
            pass
        focus_fields_text = st.text_input(
            "关注哪些变量字段做统计摘要（逗号分隔，比如 resources.cash）",
            value=st.session_state.get("exp_focus_fields", focus_fields_default),
            key="exp_focus_fields",
            placeholder="例：age, resources.cash",
        )

        if st.button("▶▶ 运行重复实验", type="primary"):
            with st.spinner(f"正在用同一份策略画像重复推进 {int(n_repeats)} 次，每次 {int(steps)} 步……"):
                try:
                    cfg = _load_cfg()
                    results = run_repeated_experiment(
                        cfg, PROJECT_ROOT, DATA_DIR, sim_id,
                        source_branch=source_branch, from_step=int(from_step), steps=int(steps),
                        profile=profile, n_repeats=int(n_repeats),
                    )
                except ImportError as exc:
                    st.error(f"未检测到 mini_agent 框架，无法运行实验：{exc}")
                    results = None

            if results:
                try:
                    _rank_manifest, _, _ = get_simulation(DATA_DIR, sim_id)
                    _objectives_for_rank = _rank_manifest.settings.get("objectives") or []
                except Exception:  # noqa: BLE001 — 拿不到就不展示排序区，不影响主流程
                    _objectives_for_rank = []
                st.session_state["experiment_results"] = {
                    "sim_id": sim_id, "results": results, "mode": "repeat",
                    "focus_fields": [f.strip() for f in focus_fields_text.split(",") if f.strip()],
                    "objectives": _objectives_for_rank,
                }
    else:
        st.markdown(
            '<span class="ws-muted">至少配置 2 份策略画像才有对比意义；每份画像独立跑在自己的'
            "分支上，互不干扰。</span>",
            unsafe_allow_html=True,
        )
        profile_count = st.number_input("策略数量", min_value=2, max_value=6, value=2, step=1, key="exp_profile_count")

        profiles = []
        profile_cols = st.columns(int(profile_count))
        for i in range(int(profile_count)):
            with profile_cols[i]:
                st.markdown(f"**策略 {i + 1}**")
                name = st.text_input("名称", value=f"策略{i + 1}", key=f"exp_name_{i}")
                risk = st.selectbox(
                    "风险偏好", options=["conservative", "balanced", "aggressive"],
                    format_func=lambda v: {"conservative": "保守", "balanced": "均衡", "aggressive": "进取"}[v],
                    key=f"exp_risk_{i}",
                )
                review = st.selectbox(
                    "review_mode", options=["silent", "notify_each_step", "pause_on_major_decision"],
                    format_func=lambda v: {
                        "silent": "静默托管", "notify_each_step": "每步通知",
                        "pause_on_major_decision": "重大决策暂停",
                    }[v],
                    key=f"exp_review_{i}",
                )
                allow_custom = st.checkbox("允许跳出候选自选", key=f"exp_custom_{i}")
                principles_text = st.text_area("原则/偏好（每行一条）", height=80, key=f"exp_principles_{i}")
                profiles.append({
                    "name": name,
                    "risk_preference": risk,
                    "review_mode": review,
                    "allow_custom_options": allow_custom,
                    "principles": [p.strip() for p in principles_text.splitlines() if p.strip()],
                })

        if st.button("▶▶ 运行对比实验", type="primary"):
            with st.spinner(f"正在为 {len(profiles)} 份策略画像各自推进 {int(steps)} 步……"):
                try:
                    cfg = _load_cfg()
                    results = run_comparison_experiment(
                        cfg, PROJECT_ROOT, DATA_DIR, sim_id,
                        source_branch=source_branch, from_step=int(from_step), steps=int(steps),
                        profiles=profiles,
                    )
                except ImportError as exc:
                    st.error(f"未检测到 mini_agent 框架，无法运行实验：{exc}")
                    results = None

            if results:
                st.session_state["experiment_results"] = {
                    "sim_id": sim_id, "results": results, "mode": "compare",
                }

    exp_results = st.session_state.get("experiment_results")
    if exp_results and exp_results.get("sim_id") == sim_id:
        st.markdown("#### 实验结果")
        if exp_results.get("mode") == "repeat":
            focus_fields = exp_results.get("focus_fields") or []
            ok_results = [r for r in exp_results["results"] if r.final_vars is not None]
            if focus_fields and ok_results:
                stats = aggregate_field_stats([r.final_vars for r in ok_results], focus_fields)
                st.markdown("**统计摘要**")
                for s in stats:
                    if s.kind == "numeric":
                        st.markdown(
                            f'<div class="ws-card"><div class="ws-card-title">{s.field}</div>'
                            f'<div class="ws-muted">均值 {s.mean:.2f} · 最小 {s.min:.2f} · '
                            f'最大 {s.max:.2f} · 标准差 {s.stdev:.2f}（{s.count} 个有效样本）</div></div>',
                            unsafe_allow_html=True,
                        )
                    elif s.kind == "categorical":
                        dist_text = "，".join(f"{k}×{v}" for k, v in s.distribution.items())
                        st.markdown(
                            f'<div class="ws-card"><div class="ws-card-title">{s.field}</div>'
                            f'<div class="ws-muted">分布：{dist_text}（{s.count} 个有效样本）</div></div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            f'<div class="ws-card"><div class="ws-card-title">{s.field}</div>'
                            f'<div class="ws-muted">没有任何一条分支给出这个字段的值。</div></div>',
                            unsafe_allow_html=True,
                        )
            elif not focus_fields:
                st.markdown(
                    '<span class="ws-muted">没有填写要关注的字段，只展示每条分支的完成情况；'
                    "填写字段路径可以看到均值/极差/标准差摘要。</span>",
                    unsafe_allow_html=True,
                )

            # ── 按关注指标排序（阶段十四，4.6 节）：只有 objectives 里至少
            # 一条声明了 field 才会有结果，否则 rank_by_objectives 返回空
            # 列表，这里不展示任何东西 ──
            objectives_for_rank = exp_results.get("objectives") or []
            if ok_results:
                ranked = rank_by_objectives(
                    [r.final_vars for r in ok_results],
                    objectives_for_rank,
                    labels=[f"分支 {r.branch}" for r in ok_results],
                )
                if ranked:
                    st.markdown("**按关注指标排序**")
                    st.markdown(
                        '<span class="ws-muted">仅供参考，不代表系统认定的最优解——'
                        "最终判断仍然由你自己来。</span>",
                        unsafe_allow_html=True,
                    )
                    for rb in ranked:
                        values_text = "，".join(
                            f"{k}={'—' if v is None else round(v, 2)}"
                            for k, v in rb.values.items()
                        )
                        st.markdown(
                            f'<div class="ws-card"><div class="ws-card-title">'
                            f'{rb.label} · 胜出 {rb.score} 项</div>'
                            f'<div class="ws-muted">{values_text}</div></div>',
                            unsafe_allow_html=True,
                        )
        for r in exp_results["results"]:
            status = "⚠️ 提前结束" if r.ended_early else "✅ 正常跑完"
            err_note = f"（{r.error}）" if r.error else ""
            cols = st.columns([3, 1])
            with cols[0]:
                st.markdown(
                    f'<div class="ws-card"><div class="ws-card-title">{r.profile_name}</div>'
                    f'<div class="ws-muted">分支 {r.branch} · 完成 {r.steps_done} 步 · {status}{err_note}</div></div>',
                    unsafe_allow_html=True,
                )
            with cols[1]:
                if r.branch != "?" and st.button("加入对比", key=f"exp_cmp_{r.branch}"):
                    selection = st.session_state.setdefault("compare_selection", [])
                    pair = (sim_id, r.branch)
                    if pair not in selection:
                        selection.append(pair)
                    st.session_state["view"] = "compare"
                    st.rerun()


# ─────────────────────────────────────────────────────────────
# 页面：存档管理（阶段七）
# ─────────────────────────────────────────────────────────────


def page_knowledge() -> None:
    """📚 知识库浏览页（第八轮批次一，`world_simulator_c_category_
    precision_upgrade_improvement_plan.md` 第 2 节）：跨模拟因果知识库
    的只读展示——只是补"能看见"这一层，不做任何编辑/删除操作入口，
    延续 4.12 节原方案"不做知识库管理 UI"的既有判断。
    """
    st.markdown("## 📚 知识库", unsafe_allow_html=True)
    st.markdown(
        '<span class="ws-muted">跨模拟沉淀下来的因果知识——每条推进产出的'
        "结构化因果链，如果和已有条目关键词相似就合并计数，否则新增一条。"
        "这里只做只读浏览，不支持手工编辑或删除。</span>",
        unsafe_allow_html=True,
    )

    items = knowledge_base_mod.load_all(DATA_DIR)
    if not items:
        st.info("知识库还是空的——推进几个模拟实例、产生结构化因果链之后，这里就会有内容。")
        return

    sort_key = st.radio(
        "排序方式", ["按置信度", "按印证次数"], horizontal=True, key="kb_sort"
    )
    _confidence_rank = {"confirmed": 3, "supported": 2, "hypothesis": 1, "speculative": 0}
    if sort_key == "按置信度":
        items = sorted(
            items,
            key=lambda it: (_confidence_rank.get(it.confidence, 0), it.validated_count),
            reverse=True,
        )
    else:
        items = sorted(items, key=lambda it: it.validated_count, reverse=True)

    st.caption(f"共 {len(items)} 条知识")
    for item in items:
        track_record = f"印证 {item.validated_count} 次"
        if item.contradicted_count:
            track_record += f" · 证伪 {item.contradicted_count} 次"
        title = f"{item.cause} → {item.effect}（{item.confidence} · {track_record}）"
        with st.expander(title):
            if item.mechanism:
                st.markdown(f"**机制**：{_html_text(item.mechanism)}", unsafe_allow_html=True)
            if item.valid_range:
                st.markdown(
                    f"**适用范围**：{_html_text(item.valid_range)}", unsafe_allow_html=True
                )
            st.caption(
                f"来源模板：{item.source_template or '未知'} · "
                f"首次记录：{item.created_at or '未知'} · 版本：v{item.version}"
            )
            if item.evidence:
                st.markdown("**来源引用**：" + "、".join(item.evidence))
            if item.notes:
                st.markdown("**观察标注**")
                for note in item.notes:
                    st.markdown(f"- {_html_text(note)}", unsafe_allow_html=True)


def page_archive() -> None:
    st.markdown("## 存档管理", unsafe_allow_html=True)
    st.markdown(
        '<span class="ws-muted">全部模拟实例总览。「删除实例」不可逆——'
        "如果只是不想要某条时间线的后续走向，去实例详情页的「分支」区块"
        "从历史节点分叉即可，原时间线不会被销毁；真正确定不再需要一个"
        "实例时才用这里的删除。</span>",
        unsafe_allow_html=True,
    )

    manifests = list_simulations(DATA_DIR)
    if not manifests:
        st.info("还没有任何模拟实例。")
        return

    for m in manifests:
        try:
            branches = bm.list_branches(DATA_DIR, m.sim_id)
        except Exception:  # noqa: BLE001
            branches = ["main"]

        st.markdown(
            f"""<div class="ws-card">
                <div class="ws-card-title">{m.title}</div>
                <div class="ws-muted">{_pill(m.status)}
                    {m.sim_id} · 模板：{m.template} · 第 {m.current_step} 步 ·
                    {len(branches)} 条分支 · 创建于 {_format_local_time(m.created_at)}
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
        c1, c2, c3 = st.columns([1, 1, 3])
        with c1:
            if st.button("打开", key=f"arch_open_{m.sim_id}"):
                st.session_state["view"] = "detail"
                st.session_state["sim_id"] = m.sim_id
                st.rerun()
        with c2:
            confirm_key = f"arch_confirm_{m.sim_id}"
            with st.popover("🗑 删除"):
                st.markdown(
                    f'<div class="ws-danger-zone"><b>删除「{m.title}」？</b>'
                    "<div class=\"ws-muted\">此操作不可逆，会连同全部分支/"
                    "历史一起删除。</div></div>",
                    unsafe_allow_html=True,
                )
                confirmed = st.checkbox("我确认要删除这个实例", key=confirm_key)
                if st.button("确认删除", key=f"arch_delete_{m.sim_id}", disabled=not confirmed):
                    try:
                        delete_simulation(DATA_DIR, m.sim_id)
                    except SimNotFoundError as exc:
                        st.error(str(exc))
                    else:
                        st.success(f"已删除「{m.title}」。")
                        st.rerun()


# ─────────────────────────────────────────────────────────────
# 页面：游戏化视图（阶段七）
# ─────────────────────────────────────────────────────────────


def page_game() -> None:
    sim_id = st.session_state.get("sim_id")
    if not sim_id:
        st.info("请先从「模拟列表」打开一个实例，再切到游戏化视图。")
        if st.button("← 返回列表"):
            st.session_state["view"] = "list"
            st.rerun()
        return

    try:
        manifest, current, history = get_simulation(DATA_DIR, sim_id)
    except SimEngineError as exc:
        st.error(f"加载模拟实例失败：{exc}")
        return

    top_l, top_r = st.columns([4, 1])
    with top_l:
        st.markdown(f"## 📖 {manifest.title}", unsafe_allow_html=True)
        st.markdown(
            f'<span class="ws-muted">{_pill(manifest.status)}分支：{manifest.branch} · '
            f"已写到第 {manifest.current_step} 章</span>",
            unsafe_allow_html=True,
        )
    with top_r:
        if st.button("回到推进面板"):
            st.session_state["view"] = "detail"
            st.rerun()

    achievements = compute_achievements(manifest, history)
    progress = achievement_progress(achievements)

    st.markdown("#### 🏅 成就")
    st.progress(progress["ratio"], text=f"{progress['unlocked_count']}/{progress['total_count']} 已解锁")
    badge_cols = st.columns(3)
    for i, a in enumerate(achievements):
        with badge_cols[i % 3]:
            cls = "ws-badge-unlocked" if a.unlocked else "ws-badge-locked"
            icon = "🏆" if a.unlocked else "🔒"
            st.markdown(
                f'<div class="ws-badge {cls}"><div class="ws-badge-title">{icon} {a.label}</div>'
                f'<div class="ws-badge-desc">{a.description}</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown("#### 📚 章节回顾")
    st.markdown(
        '<span class="ws-muted">按时间正序，把这条时间线当一本正在写的书翻一遍。'
        "想继续写下去，回到「推进面板」即可。</span>",
        unsafe_allow_html=True,
    )
    if not history:
        st.info("这段故事还没有开始。")
        return

    chapter_labels = [f"第 {s.step} 章" for s in history]
    idx = st.session_state.setdefault("game_chapter_idx", len(history) - 1)
    idx = max(0, min(idx, len(history) - 1))

    nav_prev, nav_pos, nav_next = st.columns([1, 3, 1])
    with nav_prev:
        if st.button("◀ 上一章", disabled=idx <= 0):
            idx -= 1
    with nav_pos:
        idx = st.select_slider("跳到章节", options=list(range(len(history))), value=idx,
                                format_func=lambda i: chapter_labels[i])
    with nav_next:
        if st.button("下一章 ▶", disabled=idx >= len(history) - 1):
            idx += 1
    st.session_state["game_chapter_idx"] = idx

    s = history[idx]
    chosen_note = ""
    if s.chosen_option_id:
        who = "代理" if s.chosen_by == "autopilot" else "你"
        label = _choice_label(s.options, s.chosen_option_id)
        chosen_note = f'<div class="ws-chapter-choice">→ {who} 选择了「{_html_text(label)}」</div>'
        if s.chosen_by == "autopilot" and s.chosen_reason:
            chosen_note += f'<div class="ws-chapter-choice">　理由：{_html_text(s.chosen_reason)}</div>'
    major_tag = " · ⚡命运转折点" if s.major_decision else ""
    step_time_suffix = f" · {_html_text(s.time_label)}" if s.time_label else ""
    granularity_note = _granularity_note_html(s)
    resource_note = _resource_violations_html(s)
    relation_note = _relation_violations_html(s)
    option_warnings_note = _option_warnings_html(s)
    background_note = _background_entities_html(s)
    key_drivers_note = _key_drivers_html(s)
    line_updates_note = _line_updates_html(s, manifest.settings.get("causal_lines"))
    structural_change_note = _structural_change_html(s)
    narrative_text = _html_text(s.narrative) if s.narrative else "（这一章还没有更多叙事文本。）"

    html = (
        '<div class="ws-card" style="min-height: 220px;">'
        f'<div class="ws-chapter-step">第 {s.step} 章{step_time_suffix}{major_tag}</div>'
        f'<div class="ws-chapter-summary" style="font-size:1.15rem;">{_html_text(s.summary)}</div>'
        f"{granularity_note}{resource_note}{relation_note}{option_warnings_note}{background_note}{line_updates_note}{key_drivers_note}{structural_change_note}"
        f'<div class="ws-chapter-narrative">{narrative_text}</div>'
        f"{chosen_note}"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)
    change = getattr(s, "structural_change", None)
    if isinstance(change, dict) and change.get("description") and not change.get("accepted"):
        if st.button(
            "采纳为正式结构",
            key=f"adopt_structural_change_game_{manifest.sim_id}_{manifest.branch}_{s.step}",
            help="确认后会把这条结构性变化记入模拟设置，后续推进会把它当成已知事实提示给系统；不会自动改写具体变量结构。",
        ):
            try:
                apply_structural_change(
                    DATA_DIR, manifest.sim_id, step=s.step, branch=manifest.branch
                )
            except SimEngineError as exc:
                st.error(str(exc))
            else:
                st.success("已采纳，后续推进会把这条新结构当作已知事实提示给系统。")
                st.rerun()
    if s.vars:
        with st.expander("这一章的关键变量"):
            uncertain_html = _uncertain_fields_html(s)
            if uncertain_html:
                st.markdown(uncertain_html, unsafe_allow_html=True)
            _render_vars_display(s.vars, bool(manifest.settings.get("multi_entity_mode")))


# ─────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────


def main() -> None:
    st.set_page_config(page_title="world_simulator", page_icon="🌌", layout="wide")
    st.markdown(THEME_CSS, unsafe_allow_html=True)

    st.session_state.setdefault("view", "list")

    with st.sidebar:
        st.markdown("### 🌌 world_simulator", unsafe_allow_html=True)
        st.markdown('<span class="ws-muted">万物模拟器</span>', unsafe_allow_html=True)
        st.markdown("---")
        if st.button("📜 模拟列表", use_container_width=True):
            st.session_state["view"] = "list"
            st.rerun()
        if st.button("＋ 新建模拟", use_container_width=True):
            st.session_state["view"] = "create"
            st.rerun()
        if st.button("⚖ 对比视图", use_container_width=True):
            st.session_state["view"] = "compare"
            st.rerun()
        if st.button("🧪 对比实验", use_container_width=True):
            st.session_state["view"] = "experiment"
            st.rerun()
        if st.button("🗄 存档管理", use_container_width=True):
            st.session_state["view"] = "archive"
            st.rerun()
        if st.button("📚 知识库", use_container_width=True):
            st.session_state["view"] = "knowledge"
            st.rerun()

    view = st.session_state["view"]
    if view == "create":
        page_create()
    elif view == "detail":
        page_detail()
    elif view == "compare":
        page_compare()
    elif view == "experiment":
        page_experiment()
    elif view == "archive":
        page_archive()
    elif view == "knowledge":
        page_knowledge()
    elif view == "game":
        page_game()
    else:
        page_list()


if __name__ == "__main__":
    main()
