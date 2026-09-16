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

import json
import logging
import sys
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
)
from world_simulator.spec_generator import ScenarioDraft, ScenarioGenerationError, generate_scenario
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
            with st.spinner("正在生成提案草稿..."):
                try:
                    cfg = _load_cfg()
                    draft = generate_scenario(cfg, PROJECT_ROOT, template=template, intent=intent)
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
                "create_feedback",
            ):
                st.session_state.pop(key, None)
            st.session_state["view"] = "detail"
            st.session_state["sim_id"] = sim_id
            st.rerun()


# ─────────────────────────────────────────────────────────────
# 页面：实例详情 / 推进面板
# ─────────────────────────────────────────────────────────────


def _render_timeline(history: List) -> None:
    for state in history:
        chosen_note = ""
        if state.chosen_option_id:
            who = "代理" if state.chosen_by == "autopilot" else "你"
            chosen_note = f'<div class="ws-chapter-choice">→ {who} 选择了「{state.chosen_option_id}」</div>'
        if state.chosen_by == "autopilot" and state.chosen_reason:
            chosen_note += f'<div class="ws-chapter-choice">　理由：{state.chosen_reason}</div>'
        narrative = f'<div class="ws-chapter-narrative">{state.narrative}</div>' if state.narrative else ""
        st.markdown(
            f"""<div class="ws-chapter">
                <div class="ws-chapter-step">第 {state.step} 步</div>
                <div class="ws-chapter-summary">{state.summary}</div>
                {narrative}
                {chosen_note}
            </div>""",
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

    st.markdown("#### 当前状态")
    st.markdown(
        f'<div class="ws-card"><div class="ws-card-title">{current.summary}</div>'
        + (f'<div class="ws-muted">{current.narrative}</div>' if current.narrative else "")
        + "</div>",
        unsafe_allow_html=True,
    )
    if current.vars:
        with st.expander("关键变量"):
            st.json(current.vars)

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
    st.markdown(f'<span class="ws-muted">当前：{mode_label}</span>', unsafe_allow_html=True)

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
        if st.button("▶ 手动触发自动挡推进一步（用于测试代理决策）"):
            with st.spinner("代理正在决策并推进..."):
                try:
                    cfg = _load_cfg()
                    run_autopilot_step(cfg, PROJECT_ROOT, DATA_DIR, sim_id)
                except (SimEngineError, AutopilotDisabledError) as exc:
                    st.error(f"自动挡推进失败：{exc}")
                except ImportError as exc:
                    st.error(f"未检测到 mini_agent 框架，无法调用推演引擎：{exc}")
                else:
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
    _render_timeline(list(reversed(history)))

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
        cols = st.columns([2, 1, 1])
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

    with st.expander("从历史节点开一条新分支（回滚重新选）"):
        max_step = history[-1].step if history else 0
        fork_step = st.number_input(
            "从第几步开始分叉（该步之后的历史不会带入新分支）",
            min_value=0, max_value=max_step, value=max_step, step=1, key="fork_step",
        )
        if st.button("创建分支并切换过去"):
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
        chosen_note = f'<div class="ws-chapter-choice">→ {who} 选择了「{s.chosen_option_id}」</div>'
        if s.chosen_by == "autopilot" and s.chosen_reason:
            chosen_note += f'<div class="ws-chapter-choice">　理由：{s.chosen_reason}</div>'
    major_tag = " · ⚡命运转折点" if s.major_decision else ""

    st.markdown(
        f"""<div class="ws-card" style="min-height: 220px;">
            <div class="ws-chapter-step">第 {s.step} 章{major_tag}</div>
            <div class="ws-chapter-summary" style="font-size:1.15rem;">{s.summary}</div>
            <div class="ws-chapter-narrative">{s.narrative or '（这一章还没有更多叙事文本。）'}</div>
            {chosen_note}
        </div>""",
        unsafe_allow_html=True,
    )
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
