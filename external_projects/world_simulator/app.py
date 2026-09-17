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
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "entrypoints"))

import _common  # noqa: F401  — 触发 sys.path 设置，未使用其它内容
from world_simulator import branch_manager as bm
from world_simulator.autopilot import AutopilotDisabledError, run_autopilot_step
from world_simulator.config import DATA_DIR, ensure_dirs
from world_simulator.achievements import achievement_progress, compute_achievements
from world_simulator.engine import (
    SimAlreadyEndedError,
    SimEngineError,
    SimPausedError,
    advance,
    delete_simulation,
    get_simulation,
    list_simulations,
    materialize_simulation,
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
from world_simulator.store import SimNotFoundError

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

div[data-testid="stButton"] > button {
    border-radius: 8px;
    border: 1px solid var(--ws-border);
    background: var(--ws-bg-elevated);
    color: var(--ws-text);
}
div[data-testid="stButton"] > button:hover {
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
</style>
"""


def _pill(status: str) -> str:
    label = {"active": "进行中", "paused": "已暂停", "ended": "已结束"}.get(status, status)
    cls = {"active": "ws-pill-active", "paused": "ws-pill-paused", "ended": "ws-pill-ended"}.get(
        status, "ws-pill-paused"
    )
    return f'<span class="ws-pill {cls}">{label}</span>'


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
        with st.container():
            st.markdown(
                f"""<div class="ws-card">
                    <div class="ws-card-title">{m.title}</div>
                    <div class="ws-muted">{_pill(m.status)}
                        模板：{m.template} · 第 {m.current_step} 步 ·
                        推进模式：{"自动挡" if m.pilot_mode == "autopilot" else "手动挡"}
                    </div>
                </div>""",
                unsafe_allow_html=True,
            )
            c1, c2 = st.columns([1, 5])
            with c1:
                if st.button("打开", key=f"open_{m.sim_id}"):
                    st.session_state["view"] = "detail"
                    st.session_state["sim_id"] = m.sim_id
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
        "场景模板", options=["life_sim", "group_evolution"],
        format_func=lambda t: {"life_sim": "人生模拟", "group_evolution": "群体演化"}.get(t, t),
    )

    setting_cols = st.columns([1, 1])
    with setting_cols[0]:
        options_count = st.number_input(
            "每一步候选方向数量", min_value=2, max_value=8,
            value=int(st.session_state.get("create_options_count", 4)), step=1,
        )
    with setting_cols[1]:
        granularity_presets = ["自动（由情节决定）", "1 天", "1 周", "1 个月", "1 个季度", "1 年", "5 年", "10 年", "自定义…"]
        preset_default = st.session_state.get("create_granularity_preset", "自动（由情节决定）")
        granularity_preset = st.selectbox(
            "时间粒度（每一步大致代表多长时间）", options=granularity_presets,
            index=granularity_presets.index(preset_default) if preset_default in granularity_presets else 0,
        )
        if granularity_preset == "自定义…":
            time_granularity = st.text_input(
                "自定义时间粒度", value=st.session_state.get("create_granularity_custom", ""),
                placeholder="例：3 个月 / 一场谈判的一轮 / 半局比赛",
            )
        elif granularity_preset == "自动（由情节决定）":
            time_granularity = ""
        else:
            time_granularity = granularity_preset
    st.markdown(
        '<span class="ws-muted">这两项会一起存进这个模拟实例的设置里，后面每一步推进'
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
            st.session_state["create_granularity_preset"] = granularity_preset
            if granularity_preset == "自定义…":
                st.session_state["create_granularity_custom"] = time_granularity
            settings = {"options_count": int(options_count), "time_granularity": time_granularity}
            st.session_state["create_settings"] = settings
            with st.spinner("正在生成提案草稿..."):
                try:
                    cfg = _load_cfg()
                    draft = generate_scenario(
                        cfg, PROJECT_ROOT, template=template, intent=intent, settings=settings,
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
    edited_vars_text = st.text_area(
        "关键变量（JSON）", value=json.dumps(draft.vars, ensure_ascii=False, indent=2), height=160
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
        if edited_vars is None:
            st.error("关键变量不是合法 JSON，请修正后再确认创建。")
        else:
            manifest = materialize_simulation(
                DATA_DIR,
                template=st.session_state.get("draft_template", template),
                intent=st.session_state.get("create_intent", intent),
                title=edited_title,
                summary=edited_summary,
                vars=edited_vars,
                options=draft.options,
                settings=st.session_state.get("create_settings"),
                time_label=draft.time_label,
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
                "create_granularity_preset", "create_granularity_custom",
            ):
                st.session_state.pop(key, None)
            st.session_state["view"] = "detail"
            st.session_state["sim_id"] = sim_id
            st.rerun()


# ─────────────────────────────────────────────────────────────
# 页面：实例详情 / 推进面板
# ─────────────────────────────────────────────────────────────


def _render_timeline(
    history: List,
    *,
    sim_id: Optional[str] = None,
    source_branch: Optional[str] = None,
) -> None:
    """渲染时间线。

    `sim_id` + `source_branch` 同时给出时，每条节点下面会带一个「创建
    分支」按钮——语义是"在这个决策发生之后开一条新分支"，即
    `fork_branch(from_step=state.step)`：新分支包含到这一步为止的历史，
    从这一步之后可以重新选。不传这两个参数（比如对比视图里复用这个
    函数渲染只读时间线）就不显示按钮，避免在不该分支的地方长出按钮。
    """
    can_fork = sim_id is not None and source_branch is not None
    for state in history:
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
        html = (
            '<div class="ws-chapter">'
            f'<div class="ws-chapter-step">第 {state.step} 步{step_time_suffix}</div>'
            f'<div class="ws-chapter-summary">{_html_text(state.summary)}</div>'
            f"{narrative}{chosen_note}"
            "</div>"
        )
        st.markdown(html, unsafe_allow_html=True)
        if can_fork:
            fork_col, _spacer = st.columns([1, 5])
            with fork_col:
                if st.button(
                    "创建分支",
                    key=f"fork_here_{sim_id}_{source_branch}_{state.step}",
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
        + (f'<div class="ws-muted">{_html_text(current.narrative)}</div>' if current.narrative else "")
        + "</div>",
        unsafe_allow_html=True,
    )
    if current.vars:
        with st.expander("关键变量"):
            st.json(current.vars)

    with st.expander("⚙️ 模拟设置（候选方向数量 / 时间粒度）"):
        cur_settings = manifest.settings or {}
        st.markdown(
            '<span class="ws-muted">改了之后从下一步推进开始生效，不会改写已经产生的历史。'
            "</span>",
            unsafe_allow_html=True,
        )
        set_cols = st.columns([1, 2, 1])
        with set_cols[0]:
            new_options_count = st.number_input(
                "每一步候选方向数量", min_value=2, max_value=8,
                value=int(cur_settings.get("options_count", 4) or 4), step=1,
                key="settings_options_count",
            )
        with set_cols[1]:
            new_time_granularity = st.text_input(
                "时间粒度（留空 = 自动，由情节决定）",
                value=str(cur_settings.get("time_granularity") or ""),
                placeholder="例：1 个月 / 1 年 / 5 年 / 一场谈判的一轮",
                key="settings_time_granularity",
            )
        with set_cols[2]:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("保存设置", key="settings_save"):
                update_settings(
                    DATA_DIR, sim_id,
                    options_count=int(new_options_count),
                    time_granularity=new_time_granularity,
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
        if st.button("保存自动挡配置"):
            set_pilot_config(
                DATA_DIR, sim_id,
                pilot_mode="autopilot" if enabled else "manual",
                autopilot={
                    "enabled": enabled,
                    "principles": [p.strip() for p in principles_text.splitlines() if p.strip()],
                    "risk_preference": risk_preference,
                    "review_mode": review_mode,
                },
            )
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
            st.markdown('<span class="ws-muted">选一个方向继续，或直接点「按默认走向推进」。</span>', unsafe_allow_html=True)
            cols = st.columns(min(len(current.options), 3) or 1)
            for i, opt in enumerate(current.options):
                with cols[i % len(cols)]:
                    st.markdown(
                        f'<div class="ws-card"><div class="ws-card-title">{opt.label}</div>'
                        f'<div class="ws-muted">{opt.description}</div></div>',
                        unsafe_allow_html=True,
                    )
                    if st.button(f"选择「{opt.label}」", key=f"choose_{opt.id}"):
                        chosen_id = opt.id

        default_clicked = st.button("按默认走向推进")

        if chosen_id or default_clicked:
            with st.spinner("正在推进..."):
                try:
                    cfg = _load_cfg()
                    advance(
                        cfg, PROJECT_ROOT, DATA_DIR, sim_id,
                        choice_option_id=chosen_id, chosen_by="user",
                    )
                except (SimEngineError, SimAlreadyEndedError, SimPausedError) as exc:
                    st.error(f"推进失败：{exc}")
                except ImportError as exc:
                    st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")
                else:
                    st.rerun()

    st.markdown("#### 时间线")
    _render_timeline(list(reversed(history)), sim_id=sim_id, source_branch=manifest.branch)

    # ── 分支管理 ──
    st.markdown("#### 分支")
    try:
        branches = bm.list_branches(DATA_DIR, sim_id)
    except Exception as exc:  # noqa: BLE001
        st.error(f"读取分支列表失败：{exc}")
        branches = ["main"]

    st.markdown(
        '<span class="ws-muted">当前活跃分支：<b>' + manifest.branch + "</b>"
        "。每条分支都是一条独立时间线，互不覆盖——「回滚重新选」的做法是"
        "从某个历史节点开一条新分支，原时间线原样保留，可以在下方「对比"
        "视图」里并排查看。</span>",
        unsafe_allow_html=True,
    )

    for b in branches:
        cols = st.columns([2, 1, 1, 1])
        with cols[0]:
            marker = " ← 当前" if b == manifest.branch else ""
            st.markdown(f'<span class="ws-muted">{b}{marker}</span>', unsafe_allow_html=True)
        with cols[1]:
            if b != manifest.branch and st.button("切换到这条", key=f"switch_{b}"):
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
            elif b == manifest.branch:
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
            _render_timeline(list(reversed(line["history"])))

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
# 页面：存档管理（阶段七）
# ─────────────────────────────────────────────────────────────


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
                    {len(branches)} 条分支 · 创建于 {m.created_at}
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
    narrative_text = _html_text(s.narrative) if s.narrative else "（这一章还没有更多叙事文本。）"

    html = (
        '<div class="ws-card" style="min-height: 220px;">'
        f'<div class="ws-chapter-step">第 {s.step} 章{step_time_suffix}{major_tag}</div>'
        f'<div class="ws-chapter-summary" style="font-size:1.15rem;">{_html_text(s.summary)}</div>'
        f'<div class="ws-chapter-narrative">{narrative_text}</div>'
        f"{chosen_note}"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)
    if s.vars:
        with st.expander("这一章的关键变量"):
            st.json(s.vars)


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
        if st.button("🗄 存档管理", use_container_width=True):
            st.session_state["view"] = "archive"
            st.rerun()

    view = st.session_state["view"]
    if view == "create":
        page_create()
    elif view == "detail":
        page_detail()
    elif view == "compare":
        page_compare()
    elif view == "archive":
        page_archive()
    elif view == "game":
        page_game()
    else:
        page_list()


if __name__ == "__main__":
    main()
