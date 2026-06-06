"""
Mini-Agent Web Demo
基于 Streamlit 的 mini-agent HTTP 服务交互界面
"""

import streamlit as st
import requests
import json
import time
import threading
import os
from pathlib import Path
from datetime import datetime
from typing import Optional
import queue as Queue

# ── 页面配置 ──────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Mini Agent Web Demo",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 样式 ──────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
/* 主色调 */
:root {
    --primary: #6C63FF;
    --primary-light: #8B85FF;
    --bg-card: #1E1E2E;
    --bg-input: #252535;
    --text-muted: #888;
    --border: #333350;
    --success: #4CAF50;
    --warning: #FF9800;
    --error: #F44336;
    --info: #2196F3;
}

/* 隐藏 Streamlit 默认元素 */
#MainMenu, footer, header {visibility: hidden;}
.block-container {padding-top: 1rem; padding-bottom: 1rem;}

/* 对话消息样式 */
.msg-user {
    background: linear-gradient(135deg, #6C63FF22, #6C63FF11);
    border-left: 3px solid #6C63FF;
    border-radius: 0 12px 12px 12px;
    padding: 12px 16px;
    margin: 8px 0 8px 40px;
    font-size: 14px;
    line-height: 1.6;
}
.msg-agent {
    background: linear-gradient(135deg, #1E1E2E, #252535);
    border-left: 3px solid #4CAF50;
    border-radius: 0 12px 12px 12px;
    padding: 12px 16px;
    margin: 8px 40px 8px 0;
    font-size: 14px;
    line-height: 1.6;
}
.msg-role {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    margin-bottom: 6px;
    opacity: 0.7;
}
.msg-time {
    font-size: 10px;
    opacity: 0.4;
    text-align: right;
    margin-top: 4px;
}

/* 事件流样式 */
.event-token { color: #e0e0e0; }
.event-tool_call { color: #FFB74D; }
.event-tool_result { color: #81C784; }
.event-tool_error { color: #E57373; }
.event-turn_start { color: #64B5F6; font-weight: bold; }
.event-turn_done { color: #4CAF50; font-weight: bold; }
.event-permission_req { color: #FF7043; font-weight: bold; }
.event-status { color: #BA68C8; }
.event-error { color: #EF5350; }
.event-info { color: #90A4AE; }
.event-warning { color: #FFCC02; }

/* 状态指示器 */
.status-idle {
    display: inline-block;
    width: 8px; height: 8px;
    background: #4CAF50;
    border-radius: 50%;
    margin-right: 6px;
    box-shadow: 0 0 6px #4CAF50;
}
.status-running {
    display: inline-block;
    width: 8px; height: 8px;
    background: #FF9800;
    border-radius: 50%;
    margin-right: 6px;
    animation: pulse 1s infinite;
    box-shadow: 0 0 6px #FF9800;
}
.status-waiting_permission {
    display: inline-block;
    width: 8px; height: 8px;
    background: #F44336;
    border-radius: 50%;
    margin-right: 6px;
    animation: pulse 0.5s infinite;
    box-shadow: 0 0 6px #F44336;
}
.status-error {
    display: inline-block;
    width: 8px; height: 8px;
    background: #F44336;
    border-radius: 50%;
    margin-right: 6px;
}
@keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.5; transform: scale(1.2); }
}

/* 权限审批卡片 */
.permission-card {
    background: #2D1B00;
    border: 1px solid #FF7043;
    border-radius: 10px;
    padding: 14px;
    margin: 10px 0;
}
.permission-title {
    color: #FF7043;
    font-weight: bold;
    font-size: 13px;
    margin-bottom: 8px;
}
.permission-content {
    font-family: monospace;
    font-size: 12px;
    background: #1A1000;
    padding: 8px;
    border-radius: 6px;
    white-space: pre-wrap;
    word-break: break-all;
}

/* 连接状态栏 */
.conn-bar {
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 13px;
    margin-bottom: 10px;
}
.conn-ok { background: #0A2F0A; border: 1px solid #2E7D32; }
.conn-fail { background: #2F0A0A; border: 1px solid #7D2E2E; }

/* 工具调用展示 */
.tool-box {
    background: #1A1500;
    border: 1px solid #3D3000;
    border-radius: 8px;
    padding: 10px;
    margin: 6px 0;
    font-family: monospace;
    font-size: 12px;
}
.tool-name {
    color: #FFB74D;
    font-weight: bold;
    margin-bottom: 6px;
}

/* 自定义滚动条 */
::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #444; border-radius: 2px; }

/* 输入区域 */
.stTextArea textarea {
    font-size: 14px !important;
    border-radius: 10px !important;
}

/* 侧栏 section 标题 */
.sidebar-section {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: #888;
    margin: 16px 0 8px;
    padding-bottom: 4px;
    border-bottom: 1px solid #333;
}

/* 代码块 */
pre code {
    font-size: 12px !important;
}
</style>
""", unsafe_allow_html=True)

# ── Session State 初始化 ──────────────────────────────────────────────────────

def init_session():
    defaults = {
        "messages": [],          # 对话记录 [{role, content, time}]
        "api_base": "http://127.0.0.1:8765/v1",
        "token": "",
        "connected": False,
        "agent_state": "unknown",
        "turn_id": None,
        "streaming_output": "",  # 当前流式输出缓冲
        "event_log": [],         # 事件日志
        "pending_permissions": [],
        "auto_refresh": False,
        "last_event_id": 0,
        "stats": {},
        "read_token_from_file": False,
        "key_file_path": "",
        "show_events": True,
        "show_fs": False,
        "fs_path": ".",
        "fs_entries": [],
        "current_turn_tokens": "",  # 实时token流
        "is_streaming": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_session()

# ── API 客户端 ────────────────────────────────────────────────────────────────

class AgentClient:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _url(self, path):
        return f"{self.base}{path}"

    def get(self, path, params=None, timeout=5):
        return requests.get(self._url(path), headers=self.headers, params=params, timeout=timeout)

    def post(self, path, json_body=None, timeout=10):
        return requests.post(self._url(path), headers=self.headers, json=json_body, timeout=timeout)

    def delete(self, path, timeout=5):
        return requests.delete(self._url(path), headers=self.headers, timeout=timeout)

    def health(self):
        try:
            r = self.get("/health", timeout=3)
            return r.status_code == 200
        except:
            return False

    def status(self):
        try:
            r = self.get("/status")
            if r.status_code == 200:
                data = r.json()
                # 防御：stats 可能是字符串，统一转为 dict
                if not isinstance(data.get("stats"), dict):
                    data["stats"] = {"summary": str(data.get("stats", ""))}
                return data
        except:
            pass
        return None

    def chat(self, message: str):
        try:
            r = self.post("/chat", {"message": message})
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            return {"error": str(e)}
        return None

    def interrupt(self):
        try:
            r = self.post("/interrupt")
            return r.status_code == 200
        except:
            return False

    def history(self):
        try:
            r = self.get("/history")
            if r.status_code == 200:
                return r.json()
        except:
            pass
        return None

    def clear_history(self):
        try:
            r = self.delete("/history")
            return r.status_code == 200
        except:
            return False

    def events(self, since_id=0, limit=100, event_type=None):
        try:
            params = {"since_id": since_id, "limit": limit}
            if event_type:
                params["type"] = event_type
            r = self.get("/events", params=params)
            if r.status_code == 200:
                return r.json()
        except:
            pass
        return None

    def turns(self):
        try:
            r = self.get("/turns")
            if r.status_code == 200:
                return r.json()
        except:
            pass
        return None

    def pending_permissions(self):
        try:
            r = self.get("/permissions/pending")
            if r.status_code == 200:
                return r.json()
        except:
            pass
        return None

    def approve_permission(self, req_id: str, approve: bool):
        try:
            r = self.post(f"/permissions/{req_id}", {"approve": approve})
            return r.status_code == 200
        except:
            return False

    def fs_list(self, path="."):
        try:
            r = self.get("/fs/list", params={"path": path})
            if r.status_code == 200:
                return r.json()
        except:
            pass
        return None

    def fs_read(self, path):
        try:
            r = self.get("/fs/read", params={"path": path})
            if r.status_code == 200:
                return r.json()
        except:
            pass
        return None

    def stream_turn(self, turn_id: str, timeout=120):
        """SSE 流式读取指定 turn 的输出，返回事件列表"""
        try:
            url = f"{self.base}/stream/{turn_id}"
            events = []
            with requests.get(url, headers=self.headers, stream=True, timeout=timeout) as resp:
                for line in resp.iter_lines(decode_unicode=True):
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        try:
                            events.append(json.loads(data_str))
                        except:
                            pass
                    elif line.startswith("event:"):
                        pass
            return events
        except:
            return []


def get_client():
    return AgentClient(st.session_state.api_base, st.session_state.token)


# ── Token 读取 ────────────────────────────────────────────────────────────────

def try_read_token_from_file(file_path: str) -> str:
    """从 agent_api.key 读取 token"""
    try:
        p = Path(file_path)
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except:
        pass
    return ""


def find_key_file_auto() -> str:
    """自动查找 agent_api.key"""
    candidates = [
        Path.cwd() / "agent_api.key",
        Path.home() / "agent_api.key",
        Path("/tmp/agent_api.key"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return ""


# ── 格式化函数 ────────────────────────────────────────────────────────────────

def format_event_html(event: dict) -> str:
    etype = event.get("type", "info")
    ts = event.get("ts", 0)
    data = event.get("data", event)
    t_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""

    css_class = f"event-{etype}"

    if etype == "token":
        text = data.get("text", "")
        return f'<span class="{css_class}">{text}</span>'

    elif etype == "tool_call":
        name = data.get("name", "unknown")
        args = data.get("input", {})
        args_str = json.dumps(args, ensure_ascii=False, indent=2)[:300]
        return f'''<div class="tool-box">
<div class="tool-name">🔧 {name}</div>
<pre style="margin:0;font-size:11px;color:#ccc">{args_str}</pre>
<div style="font-size:10px;color:#666;margin-top:4px">{t_str}</div>
</div>'''

    elif etype == "tool_result":
        output = str(data.get("output", ""))[:500]
        return f'''<div class="tool-box" style="border-color:#2D4A2D">
<div style="color:#81C784;font-size:11px;font-weight:bold">✅ 工具结果</div>
<pre style="margin:4px 0 0;font-size:11px;color:#aaa">{output}</pre>
</div>'''

    elif etype == "turn_start":
        text = data.get("message", data.get("input", ""))
        return f'<div class="{css_class}" style="padding:4px 0">▶ 开始处理: {text[:80]}</div>'

    elif etype == "turn_done":
        return f'<div class="{css_class}" style="padding:4px 0">✓ 处理完成 [{t_str}]</div>'

    elif etype == "permission_req":
        tool = data.get("tool_name", "unknown")
        desc = data.get("description", "")
        return f'<div class="{css_class}" style="padding:4px 0">⚠️ 权限请求: {tool} — {desc}</div>'

    elif etype == "error":
        msg = data.get("message", str(data))
        return f'<div class="{css_class}" style="padding:4px 0">✗ 错误: {msg}</div>'

    else:
        msg = data.get("message", str(data))[:200]
        return f'<div class="{css_class}" style="padding:2px 0;font-size:12px">[{etype}] {msg}</div>'


def state_badge(state: str) -> str:
    labels = {"idle": "空闲", "running": "运行中", "waiting_permission": "等待审批", "error": "错误", "unknown": "未知"}
    css_state = state if state in ["idle", "running", "waiting_permission", "error"] else "error"
    label = labels.get(state, state)
    return f'<span class="status-{css_state}"></span>{label}'


# ── 侧栏 ──────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🤖 Mini Agent")
    st.markdown("---")

    # 连接配置
    st.markdown('<div class="sidebar-section">服务配置</div>', unsafe_allow_html=True)

    api_base = st.text_input(
        "API 地址",
        value=st.session_state.api_base,
        placeholder="http://127.0.0.1:8765/v1",
        key="input_api_base"
    )
    st.session_state.api_base = api_base

    # Token 配置
    st.markdown('<div class="sidebar-section">认证 Token</div>', unsafe_allow_html=True)

    token_mode = st.radio(
        "Token 来源",
        ["手动输入", "从文件读取"],
        horizontal=True,
        label_visibility="collapsed"
    )

    if token_mode == "手动输入":
        token = st.text_input(
            "Bearer Token",
            value=st.session_state.token,
            type="password",
            placeholder="输入 API Token（留空则无认证）"
        )
        st.session_state.token = token
    else:
        # 文件读取模式
        auto_path = find_key_file_auto()
        key_file = st.text_input(
            "Key 文件路径",
            value=st.session_state.key_file_path or auto_path,
            placeholder="agent_api.key 路径"
        )
        st.session_state.key_file_path = key_file
        if st.button("📂 读取 Token", use_container_width=True):
            t = try_read_token_from_file(key_file)
            if t:
                st.session_state.token = t
                st.success(f"已读取 Token: {t[:8]}...")
            else:
                st.error("读取失败，文件不存在或为空")
        if st.session_state.token:
            st.caption(f"当前: `{st.session_state.token[:8]}...`")

    # 连接测试
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔌 连接", use_container_width=True, type="primary"):
            client = get_client()
            if client.health():
                st.session_state.connected = True
                status = client.status()
                if status:
                    st.session_state.agent_state = status.get("state", "unknown")
                    st.session_state.stats = status.get("stats", {})
                st.success("连接成功！")
            else:
                st.session_state.connected = False
                st.error("连接失败")
    with col2:
        if st.button("🔄 刷新", use_container_width=True):
            if st.session_state.connected:
                client = get_client()
                status = client.status()
                if status:
                    st.session_state.agent_state = status.get("state", "unknown")
                    st.session_state.stats = status.get("stats", {})
                    perms = client.pending_permissions()
                    if perms:
                        st.session_state.pending_permissions = perms.get("requests", [])

    # 连接状态显示
    if st.session_state.connected:
        st.markdown(
            f'<div class="conn-bar conn-ok">✓ 已连接 | {state_badge(st.session_state.agent_state)}</div>',
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            '<div class="conn-bar conn-fail">✗ 未连接</div>',
            unsafe_allow_html=True
        )

    # Agent 统计
    if st.session_state.stats:
        st.markdown('<div class="sidebar-section">运行统计</div>', unsafe_allow_html=True)
        stats = st.session_state.stats
        col1, col2 = st.columns(2)
        with col1:
            st.metric("总轮次", stats.get("total_turns", 0))
        with col2:
            st.metric("Token用量", f"{stats.get('total_tokens', 0):,}")

    # 视图选项
    st.markdown('<div class="sidebar-section">视图</div>', unsafe_allow_html=True)
    st.session_state.show_events = st.toggle("显示事件流", value=st.session_state.show_events)
    st.session_state.show_fs = st.toggle("文件系统", value=st.session_state.show_fs)

    # 操作按钮
    st.markdown('<div class="sidebar-section">操作</div>', unsafe_allow_html=True)

    if st.button("⏹ 中断执行", use_container_width=True, disabled=not st.session_state.connected):
        client = get_client()
        if client.interrupt():
            st.success("已发送中断信号")
        else:
            st.error("中断失败")

    if st.button("🗑 清空对话历史", use_container_width=True, disabled=not st.session_state.connected):
        client = get_client()
        if client.clear_history():
            st.session_state.messages = []
            st.session_state.event_log = []
            st.session_state.last_event_id = 0
            st.rerun()

    if st.button("🧹 清空事件日志", use_container_width=True):
        st.session_state.event_log = []
        st.rerun()


# ── 主界面 ────────────────────────────────────────────────────────────────────

# 标题栏
title_col, status_col = st.columns([3, 1])
with title_col:
    st.markdown("### 💬 Agent 对话")
with status_col:
    if st.session_state.connected:
        st.markdown(
            f'<div style="text-align:right;padding-top:8px">{state_badge(st.session_state.agent_state)}</div>',
            unsafe_allow_html=True
        )

# 权限审批区域（高优先级显示）
if st.session_state.connected:
    client = get_client()
    perms = client.pending_permissions()
    pending = perms.get("requests", []) if perms else []
    if pending:
        st.markdown("### ⚠️ 权限审批请求")
        for perm in pending:
            req_id = perm.get("id", "")
            tool_name = perm.get("tool_name", "unknown")
            description = perm.get("description", "")
            input_data = perm.get("input", {})

            with st.container():
                st.markdown(f"""<div class="permission-card">
<div class="permission-title">🔐 工具需要权限: {tool_name}</div>
<div style="color:#ccc;font-size:13px;margin-bottom:8px">{description}</div>
<div class="permission-content">{json.dumps(input_data, ensure_ascii=False, indent=2)}</div>
</div>""", unsafe_allow_html=True)

                col1, col2, col3 = st.columns([1, 1, 3])
                with col1:
                    if st.button(f"✅ 批准", key=f"approve_{req_id}"):
                        if client.approve_permission(req_id, True):
                            st.success("已批准")
                            st.rerun()
                with col2:
                    if st.button(f"❌ 拒绝", key=f"reject_{req_id}"):
                        if client.approve_permission(req_id, False):
                            st.info("已拒绝")
                            st.rerun()

# ── 布局：对话 + 事件流 ───────────────────────────────────────────────────────

if st.session_state.show_events:
    chat_col, event_col = st.columns([3, 2])
else:
    chat_col = st.container()
    event_col = None

with chat_col:
    # 对话历史展示
    chat_container = st.container(height=480)
    with chat_container:
        if not st.session_state.messages:
            st.markdown("""
<div style="text-align:center;padding:60px 20px;color:#555">
<div style="font-size:48px;margin-bottom:16px">🤖</div>
<div style="font-size:16px;color:#666">连接到 Agent 后开始对话</div>
<div style="font-size:13px;color:#444;margin-top:8px">支持多轮对话、工具调用、流式输出</div>
</div>
""", unsafe_allow_html=True)
        else:
            for msg in st.session_state.messages:
                role = msg["role"]
                content = msg["content"]
                time_str = msg.get("time", "")

                if role == "user":
                    st.markdown(f"""<div class="msg-user">
<div class="msg-role" style="color:#6C63FF">👤 你</div>
{content}
<div class="msg-time">{time_str}</div>
</div>""", unsafe_allow_html=True)
                elif role == "assistant":
                    st.markdown(f"""<div class="msg-agent">
<div class="msg-role" style="color:#4CAF50">🤖 Agent</div>
{content}
<div class="msg-time">{time_str}</div>
</div>""", unsafe_allow_html=True)
                elif role == "streaming":
                    # 实时流式输出占位
                    st.markdown(f"""<div class="msg-agent" style="border-left-color:#FF9800">
<div class="msg-role" style="color:#FF9800">🤖 Agent (输出中...)</div>
{content}
<span style="display:inline-block;width:8px;height:14px;background:#FF9800;animation:pulse 1s infinite;vertical-align:text-bottom;margin-left:2px">▊</span>
</div>""", unsafe_allow_html=True)

    # ── 输入区域 ──────────────────────────────────────────────────────────────

    input_container = st.container()
    with input_container:
        user_input = st.text_area(
            "消息输入",
            placeholder="输入消息，Ctrl+Enter 发送... (或点击发送按钮)",
            height=100,
            key="user_input",
            label_visibility="collapsed"
        )

        btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([2, 1, 1, 1])

        with btn_col1:
            send_disabled = not st.session_state.connected or not user_input.strip()
            send_btn = st.button(
                "📨 发送",
                use_container_width=True,
                type="primary",
                disabled=send_disabled
            )

        with btn_col2:
            sync_history = st.button("📋 同步历史", use_container_width=True,
                                      disabled=not st.session_state.connected)

        with btn_col3:
            st.button("📊 查看 Turns", use_container_width=True,
                      disabled=not st.session_state.connected,
                      key="show_turns_btn")

        with btn_col4:
            fetch_events_btn = st.button("🔔 拉取事件", use_container_width=True,
                                          disabled=not st.session_state.connected)

    # 发送消息处理
    if send_btn and user_input.strip():
        client = get_client()
        msg_text = user_input.strip()
        now_str = datetime.now().strftime("%H:%M:%S")

        # 添加用户消息
        st.session_state.messages.append({
            "role": "user",
            "content": msg_text,
            "time": now_str
        })

        # 发送到 Agent
        result = client.chat(msg_text)

        if result and "turn_id" in result:
            turn_id = result["turn_id"]
            st.session_state.turn_id = turn_id

            # 轮询等待完成 + 实时收集事件
            st.session_state.is_streaming = True
            response_tokens = []
            last_id = st.session_state.last_event_id
            max_wait = 120  # 最长等待120秒
            start_time = time.time()

            with st.spinner(f"Agent 处理中... (turn: {turn_id[:8]})"):
                while time.time() - start_time < max_wait:
                    # 拉取新事件
                    evts = client.events(since_id=last_id, limit=200)
                    if evts and evts.get("events"):
                        for evt in evts["events"]:
                            evt_id = evt.get("id", 0)
                            if evt_id > last_id:
                                last_id = evt_id
                            etype = evt.get("type", "")
                            edata = evt.get("data", {})
                            # 收集token
                            if etype == "token":
                                response_tokens.append(edata.get("text", ""))
                            # 添加到事件日志
                            st.session_state.event_log.append(evt)

                    # 检查Agent状态
                    status = client.status()
                    if status:
                        state = status.get("state", "unknown")
                        st.session_state.agent_state = state
                        if state == "idle":
                            break
                        elif state == "waiting_permission":
                            break

                    time.sleep(0.5)

            st.session_state.last_event_id = last_id
            st.session_state.is_streaming = False

            # 构建 agent 回复
            full_response = "".join(response_tokens).strip()
            if not full_response:
                # 尝试从历史中获取
                hist = client.history()
                if hist and hist.get("messages"):
                    msgs = hist["messages"]
                    for m in reversed(msgs):
                        if m.get("role") == "assistant":
                            content = m.get("content", "")
                            if isinstance(content, list):
                                full_response = "".join(
                                    c.get("text", "") for c in content
                                    if isinstance(c, dict) and c.get("type") == "text"
                                )
                            elif isinstance(content, str):
                                full_response = content
                            break

            if full_response:
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": full_response,
                    "time": datetime.now().strftime("%H:%M:%S")
                })
            else:
                state_final = st.session_state.agent_state
                if state_final == "waiting_permission":
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": "⚠️ Agent 正在等待权限审批，请在上方审批区域操作",
                        "time": datetime.now().strftime("%H:%M:%S")
                    })
                else:
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": f"✓ 处理完成（turn: {turn_id[:8]}），请查看事件流获取详细输出",
                        "time": datetime.now().strftime("%H:%M:%S")
                    })
        else:
            err_msg = result.get("error", "未知错误") if result else "发送失败，请检查连接"
            st.error(f"发送失败: {err_msg}")

        st.rerun()

    # 同步历史
    if sync_history:
        client = get_client()
        hist = client.history()
        if hist and hist.get("messages"):
            st.session_state.messages = []
            for m in hist["messages"]:
                role = m.get("role", "")
                content = m.get("content", "")
                if isinstance(content, list):
                    text = "".join(
                        c.get("text", "") for c in content
                        if isinstance(c, dict) and c.get("type") == "text"
                    )
                else:
                    text = str(content)
                if role in ("user", "assistant") and text.strip():
                    st.session_state.messages.append({
                        "role": role,
                        "content": text,
                        "time": ""
                    })
            st.success(f"已同步 {len(st.session_state.messages)} 条历史消息")
            st.rerun()

    # 拉取事件
    if fetch_events_btn:
        client = get_client()
        evts = client.events(since_id=st.session_state.last_event_id, limit=100)
        if evts and evts.get("events"):
            new_events = evts["events"]
            st.session_state.event_log.extend(new_events)
            if new_events:
                st.session_state.last_event_id = max(e.get("id", 0) for e in new_events)
            st.success(f"拉取到 {len(new_events)} 条新事件")
            st.rerun()


# 事件流面板
if st.session_state.show_events and event_col is not None:
    with event_col:
        st.markdown("#### 📡 实时事件流")

        # 事件过滤
        filter_types = st.multiselect(
            "过滤事件类型",
            ["token", "tool_call", "tool_result", "tool_error",
             "turn_start", "turn_done", "permission_req", "status",
             "error", "info", "warning"],
            default=["tool_call", "tool_result", "turn_start", "turn_done",
                     "error", "permission_req", "warning"],
            label_visibility="collapsed"
        )

        event_display = st.container(height=520)
        with event_display:
            events_to_show = st.session_state.event_log
            if filter_types:
                events_to_show = [e for e in events_to_show
                                  if e.get("type") in filter_types]

            if not events_to_show:
                st.markdown(
                    '<div style="color:#555;text-align:center;padding:40px">暂无事件，点击「拉取事件」获取</div>',
                    unsafe_allow_html=True
                )
            else:
                # 只显示最新的200条
                recent_events = events_to_show[-200:]
                html_parts = []
                for evt in recent_events:
                    html_parts.append(format_event_html(evt))
                st.markdown(
                    '<div style="font-family:monospace;font-size:12px;line-height:1.8">' +
                    "".join(html_parts) +
                    '</div>',
                    unsafe_allow_html=True
                )


# ── Turns 面板（可展开） ──────────────────────────────────────────────────────

if st.session_state.connected and st.session_state.get("show_turns_btn"):
    with st.expander("📊 Turn 历史记录", expanded=True):
        client = get_client()
        turns_data = client.turns()
        if turns_data and turns_data.get("turns"):
            turns = turns_data["turns"]
            for t in reversed(turns[-20:]):  # 最新20条
                tid = t.get("turn_id", "")[:12]
                state = t.get("state", "")
                inp = t.get("input", "")[:60]
                tc = t.get("token_count", 0)
                started = t.get("started_at", 0)
                t_str = datetime.fromtimestamp(started).strftime("%H:%M:%S") if started else ""

                state_colors = {
                    "done": "#4CAF50", "running": "#FF9800",
                    "error": "#F44336", "interrupted": "#FF7043"
                }
                color = state_colors.get(state, "#888")
                st.markdown(
                    f'<div style="padding:6px;border-bottom:1px solid #333;font-size:12px">'
                    f'<span style="color:#666">{t_str}</span> '
                    f'<code style="color:#aaa">{tid}</code> '
                    f'<span style="color:{color}">[{state}]</span> '
                    f'<span style="color:#888">{inp}</span> '
                    f'<span style="color:#555;float:right">{tc} tokens</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )
        else:
            st.info("暂无 Turn 记录")


# ── 文件系统浏览器 ────────────────────────────────────────────────────────────

if st.session_state.show_fs and st.session_state.connected:
    st.markdown("---")
    st.markdown("### 📁 文件系统")

    client = get_client()
    fs_cols = st.columns([3, 1, 1])

    with fs_cols[0]:
        fs_path = st.text_input("路径", value=st.session_state.fs_path,
                                 label_visibility="collapsed",
                                 placeholder="输入路径")
        st.session_state.fs_path = fs_path

    with fs_cols[1]:
        if st.button("📂 浏览", use_container_width=True):
            result = client.fs_list(fs_path)
            if result:
                st.session_state.fs_entries = result.get("entries", [])
            else:
                st.error("读取失败")

    with fs_cols[2]:
        if st.button("🏠 根目录", use_container_width=True):
            st.session_state.fs_path = "."
            result = client.fs_list(".")
            if result:
                st.session_state.fs_entries = result.get("entries", [])

    if st.session_state.fs_entries:
        entries = st.session_state.fs_entries
        col1, col2, col3 = st.columns([3, 1, 1])
        with col1:
            st.caption("名称")
        with col2:
            st.caption("大小")
        with col3:
            st.caption("操作")

        for entry in sorted(entries, key=lambda x: (not x.get("is_dir"), x.get("name", ""))):
            name = entry.get("name", "")
            path = entry.get("path", "")
            is_dir = entry.get("is_dir", False)
            size = entry.get("size", 0)

            icon = "📁" if is_dir else "📄"
            size_str = f"{size/1024:.1f}K" if size > 1024 else f"{size}B" if not is_dir else ""

            c1, c2, c3 = st.columns([3, 1, 1])
            with c1:
                st.markdown(f'{icon} `{name}`')
            with c2:
                st.caption(size_str)
            with c3:
                if is_dir:
                    if st.button("进入", key=f"fs_enter_{path}", use_container_width=True):
                        st.session_state.fs_path = path
                        result = client.fs_list(path)
                        if result:
                            st.session_state.fs_entries = result.get("entries", [])
                        st.rerun()
                else:
                    if st.button("查看", key=f"fs_view_{path}", use_container_width=True):
                        file_data = client.fs_read(path)
                        if file_data:
                            content = file_data.get("content", "")
                            with st.expander(f"📄 {name}", expanded=True):
                                st.code(content[:5000], language=None)

# ── 底部状态栏 ────────────────────────────────────────────────────────────────

st.markdown("---")
footer_cols = st.columns([2, 2, 2, 2])
with footer_cols[0]:
    if st.session_state.connected:
        st.caption(f"🟢 已连接 `{st.session_state.api_base}`")
    else:
        st.caption("🔴 未连接")
with footer_cols[1]:
    st.caption(f"💬 对话 {len([m for m in st.session_state.messages if m['role'] != 'streaming'])} 条")
with footer_cols[2]:
    st.caption(f"📡 事件 {len(st.session_state.event_log)} 条")
with footer_cols[3]:
    if st.session_state.turn_id:
        st.caption(f"🔄 Turn: `{st.session_state.turn_id[:12]}`")
