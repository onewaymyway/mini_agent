"""
Mini-Agent 看板 (Kanban Dashboard)
==================================
基于 Streamlit 的一体化观测/交互面板：
  - 顶部：全局状态条（运行状态、自主等级、待审批数）
  - Tab1 💬 对话：聊天 + 事件流 + 权限审批
  - Tab2 🗂️ 会话管理：会话列表 / 新建 / 恢复 / 删除
  - Tab3 📌 看板：Goal / Objective / Cron Job 看板列
  - Tab4 📁 产出物：.agent/ 目录文件浏览与预览下载
  - Tab5 🧠 自我状态：具身智能自省信息、SessionPool 概况
  - Tab6 🔧 诊断：/diagnostics 原始信息，方便调试

运行方式：
    streamlit run apps/mini_agent_kanban/app.py
"""
import time
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

from client import AgentClient

# ═══════════════════════════════════════════════════════════════════════
# 页面配置
# ═══════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Mini Agent 看板",
    page_icon="🗂️",
    layout="wide",
    initial_sidebar_state="expanded",
)

def _event_text(e: dict) -> str:
    """按事件类型正确提取展示文本。/events 接口把 data 字段展开到了事件顶层，
    不同类型的关键信息在不同的 key 里（tool_call 是 tool_name/tool_input，
    tool_result 是 tool_name/result，等等），不能都当成 'message' 取。"""
    etype = e.get("type", "")
    if etype == "tool_call":
        return f"🔧 调用工具 `{e.get('tool_name','?')}` · 参数: {str(e.get('tool_input',''))[:200]}"
    if etype == "tool_result":
        return f"✅ 工具结果 `{e.get('tool_name','?')}` · {str(e.get('result',''))[:200]}"
    if etype == "tool_error":
        return f"❌ 工具出错 `{e.get('tool_name','?')}` · {str(e.get('error', e.get('message','')))[:200]}"
    if etype == "permission_req":
        return f"🔐 请求权限 `{e.get('tool_name','?')}` · {str(e.get('tool_input',''))[:200]}"
    if etype == "permission_done":
        approved = e.get("approve", e.get("approved"))
        return f"{'✅ 已批准' if approved else '❌ 已拒绝'} req={e.get('req_id','')}"
    if etype == "turn_start":
        return f"▶️ 开始新一轮: {str(e.get('message',''))[:150]}"
    if etype == "turn_done":
        return f"⏹ 本轮结束 · {str(e.get('text',''))[:150]}"
    if etype == "error":
        return f"❌ {str(e.get('message',''))[:200]}"
    if etype == "token":
        return ""  # 逐 token 事件太碎，不在这里展示
    return str(e.get("message", e.get("text", "")))[:150]


def _inject_scroll_script():
    """把聊天区滚动到底部锚点。同时兼容新版 st.iframe 和旧版 components.html。"""
    script = """
    <script>
    (function() {
        const doc = window.parent.document;
        const anchor = doc.getElementById('chat-bottom-anchor');
        if (anchor) { anchor.scrollIntoView({behavior: 'instant', block: 'end'}); }
    })();
    </script>
    """
    if hasattr(st, "iframe"):
        st.iframe(script, height=1)
    else:
        components.html(script, height=0)


STATE_LABELS = {
    "idle": ("🟢", "空闲"),
    "running": ("🟠", "运行中"),
    "waiting_permission": ("🔴", "等待审批"),
    "error": ("🔴", "错误"),
    "unknown": ("⚪", "未知"),
}

GOAL_STATUS_COLUMNS = [
    ("active", "🔵 进行中"),
    ("paused", "⏸️ 暂停"),
    ("completed", "✅ 已完成"),
    ("abandoned", "🗑️ 已放弃"),
]


def inject_css():
    st.markdown("""
<style>
#MainMenu, footer {visibility: hidden;}
.block-container {padding-top: 1rem; padding-bottom: 2rem;}

.topbar {
    display:flex; gap:18px; align-items:center; flex-wrap:wrap;
    background:#161821; border:1px solid #2a2d3a; border-radius:10px;
    padding:10px 16px; margin-bottom:14px; font-size:13px;
}
.topbar .item {display:flex; align-items:center; gap:6px; color:#ccc;}
.topbar .label {color:#888; font-size:11px; text-transform:uppercase; letter-spacing:.5px;}

.kanban-col {
    background:#14161f; border:1px solid #262838; border-radius:10px;
    padding:10px; min-height:120px;
}
.kanban-col h4 {margin:0 0 8px 0; font-size:13px; color:#bbb;}
.kanban-card {
    background:#1e2130; border:1px solid #333750; border-left:3px solid #6C63FF;
    border-radius:8px; padding:8px 10px; margin-bottom:8px; font-size:12.5px;
}
.kanban-card .title {font-weight:600; color:#e8e8ff; margin-bottom:3px;}
.kanban-card .meta {color:#888; font-size:11px;}

.msg-user {
    background: linear-gradient(135deg, #2a2560, #1e1a4a);
    border-left: 3px solid #6C63FF; border-radius: 0 12px 12px 12px;
    padding: 10px 14px; margin: 6px 0 6px 30px; font-size: 14px; color: #ddd8ff;
}
.msg-agent {
    background: #16201a; border-left: 3px solid #4CAF50; border-radius: 0 12px 12px 12px;
    padding: 10px 14px; margin: 6px 30px 6px 0; font-size: 14px; color: #d4f0d4;
}
.permission-card {
    background: #2D1B00; border: 1px solid #FF7043; border-radius: 10px;
    padding: 12px; margin: 8px 0;
}
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════
# Session State
# ═══════════════════════════════════════════════════════════════════════
def init_state():
    defaults = {
        "api_base": "http://127.0.0.1:8765/v1",
        "token": "",
        "messages": [],
        "last_event_id": 0,
        "event_log": [],
        "autorefresh_tick": 0,
        "fs_path": ".",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def get_client() -> AgentClient:
    return AgentClient(st.session_state.api_base, st.session_state.token)


# ═══════════════════════════════════════════════════════════════════════
# 侧栏：连接配置
# ═══════════════════════════════════════════════════════════════════════
def render_sidebar():
    st.sidebar.markdown("### ⚙️ 连接配置")
    st.session_state.api_base = st.sidebar.text_input("API Base URL", st.session_state.api_base)
    st.session_state.token = st.sidebar.text_input("Token", st.session_state.token, type="password")

    client = get_client()
    ok = client.health()
    if ok:
        st.sidebar.success("已连接")
    else:
        st.sidebar.error("无法连接到 Agent 服务，请检查地址/Token")

    if st.sidebar.button("🔄 手动刷新全部"):
        st.rerun()

    st.session_state["auto_refresh"] = st.sidebar.checkbox(
        "⏱️ 自动刷新（每 3 秒）", value=st.session_state.get("auto_refresh", True),
        help="Agent 运行中（running/等待审批）时建议开启，聊天与事件为轮询式获取，不开自动刷新需要手动点刷新才能看到最新状态",
    )

    st.sidebar.caption("提示：daemon 模式启动后默认监听 http://127.0.0.1:8765/v1")
    return client


# ═══════════════════════════════════════════════════════════════════════
# 顶部状态条
# ═══════════════════════════════════════════════════════════════════════
def render_topbar(client: AgentClient):
    status = client.status() or {}
    if "_error" in status:
        st.warning(f"状态获取失败：{status['_error']}")
        return

    state = status.get("state", "unknown")
    icon, label = STATE_LABELS.get(state, STATE_LABELS["unknown"])
    autonomy = status.get("autonomy_level", "passive")
    tick_count = status.get("tick_count", 0)
    subscribers = status.get("subscribers", 0)

    pending = client.pending_permissions() or {}
    pending_list = pending.get("permissions", [])
    pending_n = len(pending_list) if isinstance(pending_list, list) else 0

    autostat = client.autonomous_status() or {}
    next_tick = autostat.get("next_tick_in")
    next_tick_str = f"{next_tick:.0f}s" if isinstance(next_tick, (int, float)) else "—"

    st.markdown(f"""
<div class="topbar">
  <div class="item"><span class="label">状态</span> {icon} {label}</div>
  <div class="item"><span class="label">Turn</span> {status.get('turn_id') or '—'}</div>
  <div class="item"><span class="label">自主等级</span> {autonomy}</div>
  <div class="item"><span class="label">距下次Tick</span> {next_tick_str}</div>
  <div class="item"><span class="label">Tick计数</span> {tick_count}</div>
  <div class="item"><span class="label">订阅者</span> {subscribers}</div>
  <div class="item"><span class="label">待审批</span> {'🔴 ' + str(pending_n) if pending_n else '0'}</div>
</div>
""", unsafe_allow_html=True)

    if pending_n:
        with st.expander(f"⚠️ 有 {pending_n} 个待审批权限请求，点击处理", expanded=True):
            render_permissions(client, pending_list)


def render_permissions(client: AgentClient, pending_list):
    for req in pending_list:
        req_id = req.get("req_id")
        st.markdown(f"""
<div class="permission-card">
  <b>{req.get('tool_name', '未知工具')}</b>　<span style="color:#888;font-size:11px;">turn: {req.get('turn_id','')}</span><br/>
  <code style="font-size:11px;">{str(req.get('tool_input', ''))[:300]}</code>
</div>
""", unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        if c1.button("✅ 允许一次", key=f"appr_once_{req_id}"):
            client.respond_permission(req_id, True, "once")
            st.rerun()
        if c2.button("♾️ 始终允许", key=f"appr_always_{req_id}"):
            client.respond_permission(req_id, True, "always")
            st.rerun()
        if c3.button("❌ 拒绝", key=f"deny_{req_id}"):
            client.respond_permission(req_id, False, "once")
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════
# Tab 1: 对话
# ═══════════════════════════════════════════════════════════════════════
def render_chat_tab(client: AgentClient):
    col_chat, col_events = st.columns([2, 1])

    with col_chat:
        st.markdown("#### 💬 对话")
        cur_status = client.status() or {}
        if cur_status.get("state") == "running":
            st.caption("⏳ Agent 正在处理中…（页面会自动刷新，无需等待）")
        hist = client.history() or {}
        entries = hist.get("messages", [])
        # 拉最近事件，把 tool_call / tool_result / tool_error / permission_req 也
        # 渲染进聊天流里，让用户能看到 Agent 实际调用了什么工具、结果是什么。
        ev_data = client.events(since_id=0, limit=100) or {}
        tool_events = [
            e for e in ev_data.get("events", [])
            if e.get("type") in ("tool_call", "tool_result", "tool_error",
                                  "permission_req", "permission_done")
        ]

        chat_box = st.container(height=460, border=True)
        with chat_box:
            if isinstance(entries, list):
                for e in entries[-60:]:
                    role = e.get("role", "")
                    content = e.get("content", "")
                    if isinstance(content, list):
                        # content 可能是多模态 block 列表（tool_use/tool_result/text）
                        content = "\n".join(
                            b.get("text", str(b)) if isinstance(b, dict) else str(b)
                            for b in content
                        )
                    if role in ("user", "human"):
                        st.markdown(f'<div class="msg-user">{content}</div>', unsafe_allow_html=True)
                    elif role in ("assistant", "agent"):
                        st.markdown(f'<div class="msg-agent">{content}</div>', unsafe_allow_html=True)

            # 本轮/最近的工具活动，紧跟在消息之后展示
            if tool_events:
                st.markdown('<div style="color:#888;font-size:11px;margin:6px 0;">🔧 最近工具活动</div>',
                             unsafe_allow_html=True)
                for e in tool_events[-30:]:
                    etype = e.get("type")
                    if etype == "permission_req":
                        req_id = e.get("req_id")
                        st.markdown(f"""
<div class="permission-card">
  <b>🔐 权限请求：{e.get('tool_name','未知工具')}</b><br/>
  <code style="font-size:11px;">{str(e.get('tool_input',''))[:300]}</code>
</div>
""", unsafe_allow_html=True)
                        pc1, pc2, pc3 = st.columns(3)
                        if pc1.button("✅ 允许一次", key=f"chat_appr_once_{req_id}"):
                            client.respond_permission(req_id, True, "once")
                            st.rerun()
                        if pc2.button("♾️ 始终允许", key=f"chat_appr_always_{req_id}"):
                            client.respond_permission(req_id, True, "always")
                            st.rerun()
                        if pc3.button("❌ 拒绝", key=f"chat_deny_{req_id}"):
                            client.respond_permission(req_id, False, "once")
                            st.rerun()
                    else:
                        st.caption(_event_text(e))

            # 滚动锚点：每次渲染后用下面注入的 JS 把它滚到可视区域，从而把整个
            # 固定高度容器滚到底部，实现"自动滚动到最新消息"。
            st.markdown('<div id="chat-bottom-anchor"></div>', unsafe_allow_html=True)
        _inject_scroll_script()

        with st.form("chat_form", clear_on_submit=True):
            msg = st.text_area("输入消息", height=80, label_visibility="collapsed",
                                placeholder="和 Agent 说点什么…")
            c1, c2 = st.columns([1, 1])
            send = c1.form_submit_button("发送 ➤", use_container_width=True)
            interrupt = c2.form_submit_button("⏹ 中断当前任务", use_container_width=True)
        if send and msg.strip():
            res = client.chat(msg.strip())
            if res and "_error" in res:
                st.error(res["_error"])
            # /chat 是异步排队接口，消息本身会立刻写入 /history（不等 Agent 处理完）。
            # 所以这里立即 rerun 就能马上看到自己刚发的消息；Agent 的回复由下面
            # 默认开启的"自动刷新"轮询捕捉，不在这里阻塞等待。
            st.rerun()
        if interrupt:
            client.interrupt()
            st.rerun()

        if st.button("🗑️ 清空历史"):
            client.clear_history()
            st.rerun()

    with col_events:
        st.markdown("#### 📡 事件流")
        ev = client.events(since_id=0, limit=100) or {}
        events = ev.get("events", [])
        box = st.container(height=480)
        with box:
            for e in events[-100:]:
                ts = e.get("ts")
                t_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""
                etype = e.get("type", "info")
                text = _event_text(e)
                if text:
                    st.caption(f"`{t_str}` **{etype}** — {text}")


# ═══════════════════════════════════════════════════════════════════════
# Tab 2: 会话管理
# ═══════════════════════════════════════════════════════════════════════
def render_sessions_tab(client: AgentClient):
    st.markdown("#### 🗂️ 会话管理")

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("➕ 新建会话", use_container_width=True):
            res = client.new_session()
            if res and "_error" not in res:
                st.success("已创建新会话")
            else:
                st.error((res or {}).get("_error", "创建失败"))
            st.rerun()

    data = client.sessions(limit=50) or {}
    if "_error" in data:
        st.info(f"会话列表不可用：{data['_error']}（可能未开启 session 持久化）")
        return

    sessions = data.get("sessions", [])
    if not sessions:
        st.info("暂无会话记录")
        return

    for s in sessions:
        sid = s.get("id", "")
        current_mark = " 🟢当前" if s.get("is_current") else ""
        with st.expander(f"🗂️ {sid}{current_mark}　·　轮次 {s.get('turns', '?')}　·　{s.get('age', s.get('updated_at',''))}"):
            st.json(s, expanded=False)
            cc1, cc2 = st.columns(2)
            if cc1.button("▶️ 恢复此会话", key=f"resume_{sid}"):
                res = client.resume_session(sid)
                st.success("已切换") if res and "_error" not in res else st.error(res.get("_error", "失败"))
                st.rerun()
            if cc2.button("🗑️ 删除", key=f"del_{sid}"):
                client.delete_session(sid)
                st.rerun()


# ═══════════════════════════════════════════════════════════════════════
# Tab 3: 看板（Goals / Objectives / Cron）
# ═══════════════════════════════════════════════════════════════════════
def render_kanban_tab(client: AgentClient):
    st.markdown("#### 📌 目标看板 (Goal Backlog)")

    with st.expander("➕ 新建目标"):
        with st.form("new_goal"):
            title = st.text_input("标题")
            desc = st.text_area("描述", height=60)
            priority = st.slider("优先级", 0, 100, 50)
            submitted = st.form_submit_button("创建")
        if submitted and title.strip():
            res = client.add_goal(title.strip(), desc, priority)
            if res and "_error" in res:
                st.error(res["_error"])
            st.rerun()

    goals_data = client.goals() or {}
    if "_error" in goals_data:
        st.warning(f"目标数据获取失败：{goals_data['_error']}")
    else:
        goals = goals_data.get("goals", [])
        objectives = goals_data.get("objectives", [])
        all_nodes = goals + objectives

        cols = st.columns(len(GOAL_STATUS_COLUMNS))
        for col, (status_key, status_label) in zip(cols, GOAL_STATUS_COLUMNS):
            with col:
                st.markdown(f'<div class="kanban-col"><h4>{status_label}</h4>', unsafe_allow_html=True)
                bucket = [n for n in all_nodes if n.get("status") == status_key]
                for n in bucket:
                    level_tag = "🎯目标" if n.get("level") == "objective" else "🌱心愿"
                    st.markdown(f"""
<div class="kanban-card">
  <div class="title">{level_tag} {n.get('title','(无标题)')}</div>
  <div class="meta">来源:{n.get('source','')}　优先级:{n.get('priority',0)}</div>
</div>
""", unsafe_allow_html=True)
                    new_status = st.selectbox(
                        "状态", [s for s, _ in GOAL_STATUS_COLUMNS],
                        index=[s for s, _ in GOAL_STATUS_COLUMNS].index(status_key),
                        key=f"goalstatus_{n.get('id')}", label_visibility="collapsed",
                    )
                    if new_status != status_key:
                        client.update_goal(n.get("id"), status=new_status)
                        st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### ⏰ Cron Jobs")
    cron = client.cron_jobs() or {}
    jobs = cron.get("jobs", [])
    if cron.get("note"):
        st.caption(cron["note"])
    if not jobs:
        st.info("暂无定时任务")
    for j in jobs:
        c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
        c1.markdown(f"**{j.get('name')}**　`{j.get('schedule')}`")
        c2.caption(f"下次: {j.get('next_run_str','?')}　运行次数: {j.get('run_count',0)}")
        if c3.button("▶️ 立即运行", key=f"runjob_{j.get('id')}"):
            client.run_cron_job_now(j.get("id"))
            st.rerun()
        toggle_label = "⏸️ 禁用" if j.get("enabled") else "▶️ 启用"
        if c4.button(toggle_label, key=f"togglejob_{j.get('id')}"):
            client.update_cron_job(j.get("id"), enabled=not j.get("enabled"))
            st.rerun()

    st.markdown("---")
    st.markdown("#### 🎯 Objective 执行进度")
    autostat = client.autonomous_status() or {}
    execs = autostat.get("objective_executions", [])
    if not execs:
        st.caption("当前没有正在执行的 Objective")
    for ex in execs:
        st.json(ex, expanded=False)


# ═══════════════════════════════════════════════════════════════════════
# Tab 4: 产出物（文件浏览）
# ═══════════════════════════════════════════════════════════════════════
def render_artifacts_tab(client: AgentClient):
    st.markdown("#### 📁 产出物浏览")
    c1, c2 = st.columns([4, 1])
    path = c1.text_input("路径", st.session_state.fs_path)
    if c2.button("⬆️ 上级目录"):
        parts = st.session_state.fs_path.rstrip("/").split("/")
        path = "/".join(parts[:-1]) or "."
    st.session_state.fs_path = path

    listing = client.fs_list(path) or {}
    if "_error" in listing:
        st.error(listing["_error"])
        return

    entries = listing.get("entries", [])
    for e in entries:
        name = e.get("name", "")
        is_dir = e.get("is_dir", False)
        full_path = e.get("path") or name
        c1, c2, c3 = st.columns([4, 1, 1])
        c1.write(("📁 " if is_dir else "📄 ") + name)
        if is_dir:
            if c2.button("打开", key=f"open_{full_path}"):
                st.session_state.fs_path = full_path
                st.rerun()
        else:
            if c2.button("预览", key=f"prev_{full_path}"):
                content = client.fs_read(full_path) or {}
                st.session_state[f"preview_{full_path}"] = content.get("content", content.get("_error", ""))
            c3.markdown(f"[⬇️下载]({client.fs_download_url(full_path)})")
        preview_key = f"preview_{full_path}"
        if preview_key in st.session_state:
            with st.expander(f"预览: {name}", expanded=True):
                st.code(st.session_state[preview_key][:5000], language=None)


# ═══════════════════════════════════════════════════════════════════════
# Tab 5: 自我状态
# ═══════════════════════════════════════════════════════════════════════
def render_self_tab(client: AgentClient):
    st.markdown("#### 🧠 自我状态 (Self / Embodied Intelligence)")
    data = client.self_status() or {}
    if "_error" in data:
        st.warning(data["_error"])
        return

    al = data.get("autonomous_loop")
    if al:
        st.markdown("**自主循环摘要**")
        st.json(al, expanded=False)

    st.markdown("**活跃目标 / 心愿**")
    goals_info = data.get("goals", {})
    c1, c2 = st.columns(2)
    c1.metric("活跃 Objective 数", len(goals_info.get("active_objectives", [])))
    c2.metric("活跃 Goal 数", len(goals_info.get("active_goals", [])))

    st.markdown("**最近活动摘要**（近24小时）")
    recent = data.get("recent_activity", [])
    if recent:
        for r in recent[-20:]:
            st.caption(f"`{r.get('ts','')}` {r.get('type','')} — {str(r.get('detail', r))[:150]}")
    else:
        st.caption("暂无记录")

    pool = data.get("session_pool")
    if pool:
        st.markdown("**多用户会话池**")
        st.metric("活跃会话数", pool.get("active_count", 0))
        for s in pool.get("sessions", []):
            st.caption(f"👤 {s.get('user_id')} / {s.get('session_id')} — 角色:{s.get('role')} "
                       f"空闲:{s.get('idle_seconds')}s {'🟢' if s.get('is_alive') else '⚪'}")


# ═══════════════════════════════════════════════════════════════════════
# Tab 6: 诊断
# ═══════════════════════════════════════════════════════════════════════
def render_diagnostics_tab(client: AgentClient):
    st.markdown("#### 🔧 诊断信息")
    diag = client.diagnostics() or {}
    st.json(diag, expanded=True)


# ═══════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════
def main():
    inject_css()
    init_state()
    client = render_sidebar()

    if not client.health():
        st.info("请先在左侧确认 API Base URL / Token，并确保 mini-agent daemon 已启动。")
        return

    render_topbar(client)

    tabs = st.tabs(["💬 对话", "🗂️ 会话管理", "📌 目标看板", "📁 产出物", "🧠 自我状态", "🔧 诊断"])
    with tabs[0]:
        render_chat_tab(client)
    with tabs[1]:
        render_sessions_tab(client)
    with tabs[2]:
        render_kanban_tab(client)
    with tabs[3]:
        render_artifacts_tab(client)
    with tabs[4]:
        render_self_tab(client)
    with tabs[5]:
        render_diagnostics_tab(client)

    if st.session_state.get("auto_refresh"):
        time.sleep(3)
        st.rerun()


if __name__ == "__main__":
    main()
