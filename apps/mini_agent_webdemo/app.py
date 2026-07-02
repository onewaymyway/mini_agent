"""
Mini-Agent Web Demo
基于 Streamlit 的 mini-agent HTTP 服务交互界面
"""

import streamlit as st
import requests
import json
import time
from pathlib import Path
from datetime import datetime


# ═══════════════════════════════════════════════════════════════════════════════
# 页面配置（必须在最顶层，不能放进函数）
# ═══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Mini Agent Web Demo",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ═══════════════════════════════════════════════════════════════════════════════
# 样式
# ═══════════════════════════════════════════════════════════════════════════════

def inject_styles():
    st.markdown("""
<style>
#MainMenu, footer, header {visibility: hidden;}
.block-container {padding-top: 1rem; padding-bottom: 1rem;}

/* ── 对话消息 ── */
.msg-user {
    background: linear-gradient(135deg, #2a2560, #1e1a4a);
    border-left: 3px solid #6C63FF;
    border-radius: 0 12px 12px 12px;
    padding: 12px 16px;
    margin: 8px 0 8px 40px;
    font-size: 14px;
    line-height: 1.7;
    color: #ddd8ff;
}
.msg-agent {
    background: #1a2a1a;
    border-left: 3px solid #4CAF50;
    border-radius: 0 12px 12px 12px;
    padding: 12px 16px;
    margin: 8px 40px 8px 0;
    font-size: 14px;
    line-height: 1.7;
    color: #d4f0d4;
}
.msg-agent pre, .msg-agent code {
    background: #0f1f0f;
    color: #a8e6a8;
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 13px;
}
.msg-role {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    margin-bottom: 6px;
}
.msg-time {
    font-size: 10px;
    opacity: 0.45;
    text-align: right;
    margin-top: 6px;
}

/* ── 事件流 ── */
.event-token       { color: #d0d0d0; }
.event-tool_call   { color: #FFB74D; }
.event-tool_result { color: #81C784; }
.event-tool_error  { color: #E57373; }
.event-turn_start  { color: #64B5F6; font-weight: bold; }
.event-turn_done   { color: #4CAF50; font-weight: bold; }
.event-permission_req { color: #FF7043; font-weight: bold; }
.event-status      { color: #BA68C8; }
.event-error       { color: #EF5350; }
.event-info        { color: #90A4AE; }
.event-warning     { color: #FFCC02; }
.event-session_switched { color: #4DD0E1; font-weight: bold; }

/* ── 状态点 ── */
.status-idle            { display:inline-block;width:8px;height:8px;background:#4CAF50;border-radius:50%;margin-right:6px;box-shadow:0 0 6px #4CAF50; }
.status-running         { display:inline-block;width:8px;height:8px;background:#FF9800;border-radius:50%;margin-right:6px;animation:pulse 1s infinite;box-shadow:0 0 6px #FF9800; }
.status-waiting_permission { display:inline-block;width:8px;height:8px;background:#F44336;border-radius:50%;margin-right:6px;animation:pulse 0.5s infinite;box-shadow:0 0 6px #F44336; }
.status-error           { display:inline-block;width:8px;height:8px;background:#F44336;border-radius:50%;margin-right:6px; }
@keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.5;transform:scale(1.2)} }

/* ── 权限卡片 ── */
.permission-card {
    background: #2D1B00; border: 1px solid #FF7043;
    border-radius: 10px; padding: 14px; margin: 10px 0;
}
.permission-title { color: #FF7043; font-weight: bold; font-size: 13px; margin-bottom: 8px; }
.permission-content {
    font-family: monospace; font-size: 12px; background: #1A1000;
    padding: 8px; border-radius: 6px; white-space: pre-wrap; word-break: break-all; color: #ccc;
}

/* ── 连接状态栏 ── */
.conn-bar { padding: 8px 12px; border-radius: 8px; font-size: 13px; margin-bottom: 10px; }
.conn-ok   { background: #0A2F0A; border: 1px solid #2E7D32; color: #81C784; }
.conn-fail { background: #2F0A0A; border: 1px solid #7D2E2E; color: #E57373; }

/* ── 工具调用 ── */
.tool-box {
    background: #1C1600 !important; border: 1px solid #5C4800 !important;
    border-radius: 8px; padding: 10px; margin: 6px 0;
    font-family: monospace; font-size: 12px;
}
.tool-name { color: #FFD54F !important; font-weight: bold; margin-bottom: 6px; }
.tool-box pre, .tool-box code {
    color: #F5F5F5 !important;   /* 强制亮白，Streamlit不会覆盖 */
    background: transparent !important;
    white-space: pre-wrap !important;
    word-break: break-all;
    margin: 4px 0 0 !important;
    font-size: 11px !important;
}

/* ── 侧栏分节 ── */
.sidebar-section {
    font-size: 11px; font-weight: 700; letter-spacing: 1px;
    text-transform: uppercase; color: #888;
    margin: 16px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #333;
}

::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-thumb { background: #444; border-radius: 2px; }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Session State
# ═══════════════════════════════════════════════════════════════════════════════

def init_session():
    defaults = {
        "messages":         [],
        "api_base":         "http://127.0.0.1:8765/v1",
        "token":            "",
        "connected":        False,
        "agent_state":      "unknown",
        "turn_id":          None,
        "event_log":        [],
        "last_event_id":    0,
        "stats":            {},
        "key_file_path":    "",
        "show_events":      True,
        "show_fs":          False,
        "fs_path":          ".",
        "fs_entries":       [],
        "is_streaming":     False,
        "input_key":        0,
        "scroll_trigger":   0,
        "debug_log":            [],   # 调试日志
        "_pending_idle_sync":   0,    # idle 后补同步计数
        # ── Session 管理 ──
        "sessions_list":        [],   # 所有 session 列表（来自 /v1/sessions）
        "current_session_id":   "",   # 当前激活 session id
        "preview_session_id":   "",   # 侧栏选择器中选中的 session id（用于预览）
        "session_preview":      None, # 预览缓存（/v1/sessions/{id} 返回）
        "session_action_msg":   "",   # 切换/新建/删除后的提示信息
        "sessions_unavailable": False, # Agent 未启用 session 持久化（--no-save-session）
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ═══════════════════════════════════════════════════════════════════════════════
# API 客户端
# ═══════════════════════════════════════════════════════════════════════════════

class AgentClient:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _url(self, path): return f"{self.base}{path}"

    def get(self, path, params=None, timeout=5):
        return requests.get(self._url(path), headers=self.headers, params=params, timeout=timeout)

    def post(self, path, json_body=None, timeout=10):
        return requests.post(self._url(path), headers=self.headers, json=json_body, timeout=timeout)

    def delete(self, path, timeout=5):
        return requests.delete(self._url(path), headers=self.headers, timeout=timeout)

    def health(self):
        try:
            return self.get("/health", timeout=3).status_code == 200
        except:
            return False

    def status(self):
        try:
            r = self.get("/status")
            if r.status_code == 200:
                data = r.json()
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
            return self.post("/interrupt").status_code == 200
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
            return self.delete("/history").status_code == 200
        except:
            return False

    def events(self, since_id=0, limit=100, session_id=None):
        try:
            params = {"since_id": since_id, "limit": limit}
            if session_id:
                params["session_id"] = session_id
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
            return self.post(f"/permissions/{req_id}", {"approve": approve}).status_code == 200
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

    # ── Session 管理 ─────────────────────────────────────────────────────

    def sessions(self, limit: int = 50):
        try:
            r = self.get("/sessions", params={"limit": limit})
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return {"_unavailable": True}
        except:
            pass
        return None

    def session_detail(self, session_id: str):
        try:
            r = self.get(f"/sessions/{session_id}")
            if r.status_code == 200:
                return r.json()
            return {"_error": r.json().get("detail", f"HTTP {r.status_code}")}
        except Exception as e:
            return {"_error": str(e)}

    def resume_session(self, session_id: str):
        try:
            r = self.post(f"/sessions/{session_id}/resume")
            if r.status_code == 200:
                return r.json()
            return {"ok": False, "_error": r.json().get("detail", f"HTTP {r.status_code}")}
        except Exception as e:
            return {"ok": False, "_error": str(e)}

    def new_session(self):
        try:
            r = self.post("/sessions/new")
            if r.status_code == 200:
                return r.json()
            return {"ok": False, "_error": r.json().get("detail", f"HTTP {r.status_code}")}
        except Exception as e:
            return {"ok": False, "_error": str(e)}

    def delete_session(self, session_id: str):
        try:
            r = self.delete(f"/sessions/{session_id}")
            if r.status_code == 200:
                return r.json()
            return {"ok": False, "_error": r.json().get("detail", f"HTTP {r.status_code}")}
        except Exception as e:
            return {"ok": False, "_error": str(e)}


def get_client():
    return AgentClient(st.session_state.api_base, st.session_state.token)


# ═══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════════

def try_read_token_from_file(file_path: str) -> str:
    try:
        p = Path(file_path)
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except:
        pass
    return ""


def find_key_file_auto() -> str:
    candidates = [
        Path.cwd() / "agent_api.key",
        Path.home() / "agent_api.key",
        Path("/tmp/agent_api.key"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return ""


def state_badge(state: str) -> str:
    labels = {
        "idle": "空闲", "running": "运行中",
        "waiting_permission": "等待审批", "error": "错误", "unknown": "未知"
    }
    css = state if state in ["idle", "running", "waiting_permission", "error"] else "error"
    return f'<span class="status-{css}"></span>{labels.get(state, state)}'


def format_event_html(event: dict) -> str:
    etype = event.get("type", "info")
    ts    = event.get("ts", 0)
    data  = event.get("data", event)
    t_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""
    css   = f"event-{etype}"

    if etype == "token":
        return f'<span class="{css}">{data.get("text", "")}</span>'
    if etype == "tool_call":
        name = data.get("name", "unknown")
        args = json.dumps(data.get("input", {}), ensure_ascii=False, indent=2)[:300]
        return (f'<div class="tool-box"><div class="tool-name">🔧 {name}</div>'
                f'<pre style="margin:0;font-size:11px;color:#ccc">{args}</pre>'
                f'<div style="font-size:10px;color:#666;margin-top:4px">{t_str}</div></div>')
    if etype == "tool_result":
        output = str(data.get("output", ""))[:500]
        return (f'<div class="tool-box" style="border-color:#2D4A2D">'
                f'<div style="color:#81C784;font-size:11px;font-weight:bold">✅ 工具结果</div>'
                f'<pre style="margin:4px 0 0;font-size:11px;color:#aaa">{output}</pre></div>')
    if etype == "turn_start":
        text = data.get("message", data.get("input", ""))
        return f'<div class="{css}" style="padding:4px 0">▶ 开始处理: {text[:80]}</div>'
    if etype == "turn_done":
        return f'<div class="{css}" style="padding:4px 0">✓ 处理完成 [{t_str}]</div>'
    if etype == "permission_req":
        return (f'<div class="{css}" style="padding:4px 0">'
                f'⚠️ 权限请求: {data.get("tool_name","unknown")} — {data.get("description","")}</div>')
    if etype == "error":
        return f'<div class="{css}" style="padding:4px 0">✗ {data.get("message", str(data))}</div>'
    if etype == "session_switched":
        sid   = data.get("session_id", "")[:8]
        title = data.get("title", "")
        extra = f" — {title}" if title else ""
        return f'<div class="{css}" style="padding:4px 0">🔀 切换会话 → {sid}{extra}</div>'
    msg = data.get("message", str(data))[:200]
    return f'<div class="{css}" style="padding:2px 0;font-size:12px">[{etype}] {msg}</div>'


def scroll_chat_to_bottom():
    """
    让对话容器滚动到底部。

    此函数必须在 chat_container 的 with 块内调用，
    这样 iframe 会被放置在对话容器内部，
    window.frameElement.closest() 才能精确找到对应的可滚动容器。

    原理：
    - st.markdown 的 <script> 永远不执行（浏览器 innerHTML 安全限制）
    - st.container(autoscroll=True) 对 rerun 模式无效（组件重建时状态重置）
    - st.components.v1.html 在独立 iframe 里执行 JS，
      window.frameElement 指向父页面里这个 iframe 的 DOM 元素，
      .closest('[data-testid="stVerticalBlockBorderWrapper"]') 精确找到
      st.container(height=...) 生成的可滚动容器
    """
    js = """
<script>
(function() {
  function doScroll() {
    try {
      var frame = window.frameElement;
      if (!frame) return false;

      // st.container(height=...) 对应的 DOM 是 stVerticalBlockBorderWrapper
      var container = frame.closest('[data-testid="stVerticalBlockBorderWrapper"]');
      if (container) {
        container.scrollTop = container.scrollHeight;
        return true;
      }

      // fallback：沿父元素链找第一个 overflow:auto/scroll 的容器
      var el = frame.parentElement;
      while (el && el !== window.parent.document.body) {
        var ov = window.parent.getComputedStyle(el).overflowY;
        if ((ov === 'auto' || ov === 'scroll') && el.scrollHeight > el.clientHeight) {
          el.scrollTop = el.scrollHeight;
          return true;
        }
        el = el.parentElement;
      }
    } catch(e) {}
    return false;
  }

  // 立即执行 + 短延迟重试（等 Streamlit 把新内容渲染完）
  doScroll();
  setTimeout(doScroll, 80);
  setTimeout(doScroll, 250);
  setTimeout(doScroll, 500);
})();
</script>
"""
    st.components.v1.html(js, height=0, scrolling=False)


# ═══════════════════════════════════════════════════════════════════════════════
# JS 实时轮询组件
# ═══════════════════════════════════════════════════════════════════════════════

def _render_agent_poll_widget():
    """
    注入前端 JS 轮询组件。
    每秒请求后端 /status 和 /events，将结果通过 sendPrompt() 发回 Python。
    仅在 agent 处于 running / waiting_permission 状态时发消息（避免噪音）。
    权限审批操作（approve/deny）也在 JS 中直接 POST，然后通知 Python 刷新。
    """
    api_base  = st.session_state.api_base
    token     = st.session_state.token
    last_id   = st.session_state.last_event_id
    agent_state = st.session_state.agent_state

    widget_html = f"""
<div id="agent-poll-root"></div>
<script>
(function() {{
  var BASE    = {json.dumps(api_base)};
  var TOKEN   = {json.dumps(token)};
  var lastId  = {last_id};
  var headers = TOKEN ? {{"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}}
                      : {{"Content-Type": "application/json"}};
  var pollTimer = null;
  var permShown = {{}};   // req_id -> true，已发送过的权限请求
  var permDone  = {{}};   // req_id -> true，已发送过结果的

  function sendToStreamlit(payload) {{
    // sendPrompt 是 Streamlit components 提供的方法
    try {{
      window.parent.postMessage({{
        type: "streamlit:setComponentValue",
        value: JSON.stringify(payload)
      }}, "*");
    }} catch(e) {{}}
  }}

  function buildAuthHeader() {{
    return TOKEN ? {{"Authorization": "Bearer " + TOKEN}} : {{}};
  }}

  async function fetchJson(path, opts) {{
    try {{
      var r = await fetch(BASE + path, Object.assign({{headers: buildAuthHeader()}}, opts||{{}}));
      if (r.ok) return await r.json();
    }} catch(e) {{}}
    return null;
  }}

  async function poll() {{
    // 1. 拉取状态
    var status = await fetchJson("/status");
    if (!status) return;
    var state = status.state || "unknown";

    // 2. 拉取新事件
    var evtData = await fetchJson("/events?since_id=" + lastId + "&limit=100");
    var newEvents = (evtData && evtData.events) ? evtData.events : [];

    // 筛选出权限类事件
    var permReqs  = [];
    var permDones = [];
    var hasToken  = false;
    var maxId     = lastId;

    for (var i = 0; i < newEvents.length; i++) {{
      var e = newEvents[i];
      if ((e.id||0) > maxId) maxId = e.id;
      // 注意：/events 接口将 data 字段展开到顶层，req_id/tool_name/tool_input 直接在 e 上
      if (e.type === "permission_req")  permReqs.push(e);
      if (e.type === "permission_done") permDones.push(e);
      if (e.type === "token") hasToken = true;
    }}

    if (maxId > lastId) lastId = maxId;

    // 3. 只有在 running/waiting_permission 或有新事件时才通知 Python
    var hasNew = (newEvents.length > 0) || (state !== (window._lastState||""));
    window._lastState = state;

    if (hasNew || state === "running" || state === "waiting_permission") {{
      sendToStreamlit({{
        type: "poll_update",
        state: state,
        last_event_id: lastId,
        perm_reqs:  permReqs.filter(function(r){{ return !permShown[r.req_id]; }}),
        perm_dones: permDones.filter(function(r){{ return !permDone[r.req_id]; }}),
        has_token: hasToken,
        raw_events: newEvents
      }});
      permReqs.forEach(function(r){{ if(r.req_id) permShown[r.req_id]=true; }});
      permDones.forEach(function(r){{ if(r.req_id) permDone[r.req_id]=true; }});
    }}

    // 4. 渲染内联权限审批面板（直接在 iframe 里展示，不依赖 Streamlit 重渲染）
    renderPermPanel(state);
  }}

  // ── 内联权限审批面板 ──────────────────────────────────────────────────
  async function renderPermPanel(state) {{
    var root = document.getElementById("agent-poll-root");
    if (!root) return;

    if (state !== "waiting_permission") {{
      root.innerHTML = "";
      return;
    }}

    var perms = await fetchJson("/permissions/pending");
    var pending = (perms && perms.permissions) ? perms.permissions : [];

    if (pending.length === 0) {{
      root.innerHTML = '<div style="color:#888;font-size:12px;text-align:center;padding:4px">⏳ 等待权限审批...</div>';
      return;
    }}

    var html = '<div style="font-family:sans-serif">';
    pending.forEach(function(perm) {{
      var reqId     = perm.req_id || "";
      var toolName  = perm.tool_name || "unknown";
      var toolInput = perm.tool_input || {{}};
      var isBash    = toolName === "bash";
      var inputJson = JSON.stringify(toolInput, null, 2);

      html += '<div style="background:#2D1B00;border:1px solid #FF7043;border-radius:10px;padding:14px;margin:6px 0">';
      html += '<div style="color:#FF7043;font-weight:bold;font-size:13px;margin-bottom:8px">🔐 权限请求: ' + toolName + '</div>';
      html += '<pre style="font-size:11px;background:#1A1000;padding:8px;border-radius:6px;white-space:pre-wrap;word-break:break-all;color:#ccc;margin:0 0 10px">' + escHtml(inputJson) + '</pre>';

      // 按钮行：与命令行选项一致
      html += '<div style="display:flex;flex-wrap:wrap;gap:6px">';
      html += btnHtml(reqId, "approve_once",   "#2E7D32", "#4CAF50", "✅ 批准(y)");
      html += btnHtml(reqId, "approve_always", "#1B5E20", "#81C784", "⚡ 永久批准(a)");
      html += btnHtml(reqId, "deny_once",      "#7D2E2E", "#EF5350", "❌ 拒绝(n)");
      html += btnHtml(reqId, "deny_always",    "#4A0000", "#B71C1C", "🚫 永久拒绝(d)");
      html += btnHtml(reqId, "show_detail",    "#1A237E", "#90CAF9", "🔍 查看详情(s)");
      if (isBash) {{
        html += btnHtml(reqId, "edit_cmd", "#3E2723", "#FFCC80", "✏️ 编辑命令(e)");
      }}
      html += '</div>';

      // 详情区（默认隐藏，点 show_detail 展开）
      html += '<div id="detail_' + reqId + '" style="display:none;margin-top:10px">';
      html += '<pre style="font-size:11px;background:#0D0D0D;color:#aaa;padding:8px;border-radius:4px;white-space:pre-wrap;overflow-x:auto">' + escHtml(inputJson) + '</pre>';
      html += '</div>';

      // 编辑命令区（仅 bash，默认隐藏）
      if (isBash) {{
        var cmd = (toolInput.command || "").replace(/"/g, "&quot;");
        html += '<div id="edit_' + reqId + '" style="display:none;margin-top:10px">';
        html += '<div style="color:#ccc;font-size:12px;margin-bottom:4px">编辑命令后点「确认编辑」：</div>';
        html += '<textarea id="editarea_' + reqId + '" style="width:100%;height:80px;background:#111;color:#eee;border:1px solid #555;border-radius:4px;padding:6px;font-family:monospace;font-size:12px;resize:vertical">' + escHtml(toolInput.command||"") + '</textarea>';
        html += '<div style="margin-top:6px">';
        html += btnHtml(reqId, "edit_confirm", "#2E7D32", "#4CAF50", "✅ 确认编辑");
        html += btnHtml(reqId, "edit_cancel",  "#555",    "#ccc",    "取消");
        html += '</div></div>';
      }}

      html += '</div>';  // permission-card
    }});
    html += '</div>';
    root.innerHTML = html;

    // 绑定按钮事件
    pending.forEach(function(perm) {{
      var reqId    = perm.req_id || "";
      var isBash   = perm.tool_name === "bash";
      var toolInput = perm.tool_input || {{}};

      bindBtn(reqId, "approve_once",   function() {{ doApprove(reqId, true,  null, "once");    }});
      bindBtn(reqId, "approve_always", function() {{ doApprove(reqId, true,  null, "always");  }});
      bindBtn(reqId, "deny_once",      function() {{ doApprove(reqId, false, null, "once");    }});
      bindBtn(reqId, "deny_always",    function() {{ doApprove(reqId, false, null, "always");  }});
      bindBtn(reqId, "show_detail",    function() {{ toggleEl("detail_" + reqId); }});
      if (isBash) {{
        bindBtn(reqId, "edit_cmd", function() {{
          toggleEl("edit_" + reqId);
        }});
        bindBtn(reqId, "edit_confirm", function() {{
          var ta = document.getElementById("editarea_" + reqId);
          var newCmd = ta ? ta.value : (toolInput.command||"");
          var edited = Object.assign({{}}, toolInput, {{command: newCmd}});
          doApprove(reqId, true, edited, "once");
        }});
        bindBtn(reqId, "edit_cancel", function() {{
          var el = document.getElementById("edit_" + reqId);
          if (el) el.style.display = "none";
        }});
      }}
    }});
  }}

  function btnHtml(reqId, action, bg, color, label) {{
    return '<button id="btn_' + reqId + '_' + action + '" '
      + 'style="background:' + bg + ';color:' + color + ';border:1px solid ' + color + ';'
      + 'border-radius:6px;padding:4px 10px;font-size:12px;cursor:pointer;white-space:nowrap">'
      + label + '</button>';
  }}

  function bindBtn(reqId, action, fn) {{
    var el = document.getElementById("btn_" + reqId + "_" + action);
    if (el) el.onclick = fn;
  }}

  function toggleEl(id) {{
    var el = document.getElementById(id);
    if (el) el.style.display = (el.style.display === "none") ? "block" : "none";
  }}

  function escHtml(s) {{
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }}

  async function doApprove(reqId, approve, editedInput, mode) {{
    var body = {{approve: approve, mode: mode}};
    if (editedInput) body.edited_input = editedInput;
    try {{
      await fetch(BASE + "/permissions/" + reqId, {{
        method: "POST",
        headers: Object.assign({{"Content-Type":"application/json"}}, buildAuthHeader()),
        body: JSON.stringify(body)
      }});
    }} catch(e) {{}}
    // 通知 Python 刷新
    sendToStreamlit({{type:"permission_actioned", req_id: reqId, approved: approve}});
    // 立即重绘
    setTimeout(function(){{ renderPermPanel("waiting_permission"); }}, 300);
  }}

  // ── 启动轮询 ──────────────────────────────────────────────────────────
  function startPoll() {{
    if (pollTimer) clearInterval(pollTimer);
    poll();
    pollTimer = setInterval(poll, 1200);
  }}
  startPoll();
}})();
</script>
"""
    st.components.v1.html(widget_html, height=160, scrolling=False)


def _sync_events():
    """
    每次 rerun 时主动拉取后端最新事件，分发处理后写入 session_state。

    事件结构（/events 接口把 data 字段展开到顶层）：
        {"id": N, "type": "xxx", "turn_id": "...", "ts": 1.0, <data字段直接在顶层>}
    """
    if not st.session_state.connected:
        return
    # idle 状态下也同步一次，确保不漏掉 turn_done / permission_done 等事件
    # 仅在完全静止且没有未处理事件时跳过
    if st.session_state.agent_state not in ("running", "waiting_permission", "unknown", "idle"):
        return

    client = get_client()

    # ── 1. 拉取 agent 状态 ──
    s = client.status()
    if s:
        new_state = s.get("state", st.session_state.agent_state)
        # 关键保护：bridge._state 在权限等待结束后立刻被设回 running，
        # 但 web 端可能在同一次 rerun 内先处理了 permission_req 事件（把本地设为 waiting_permission），
        # 再拉 status（此时 bridge 可能已经是 waiting_permission 或 running）。
        # 规则：
        #   本地 waiting_permission + 后端 running  → 保留 waiting_permission（等 permission_done 事件来清除）
        #   本地 running             + 后端 waiting_permission → 直接跟随后端（可信）
        if st.session_state.agent_state == "waiting_permission" and new_state == "running":
            new_state = "waiting_permission"  # 保留，等 permission_done 事件
        if new_state != st.session_state.agent_state:
            st.session_state.debug_log.append(
                f"[{_ts()}] state: {st.session_state.agent_state} -> {new_state}"
            )
        st.session_state.agent_state = new_state
        st.session_state.stats = s.get("stats", {})

    # ── 2. 拉取新事件（增量） ──
    # 修复：之前这里没有按 session_id 过滤，daemon 模式下所有 session 共用
    # 同一个全局事件流——只要有别的 CLI 终端切换过 session，本页面拉到的
    # token/tool_call 等事件就会混进"当前显示的 session 不认识的内容"，
    # 表现为对话区莫名多出不属于这个对话的文字/工具调用。按当前 session_id
    # 过滤后，只有属于这个 session（以及不带 session 标签的系统级事件，比如
    # session_switched 本身）会被拉取到。
    evts = client.events(
        since_id=st.session_state.last_event_id, limit=200,
        session_id=st.session_state.current_session_id or None,
    )
    if not (evts and evts.get("events")):
        return

    new_evts = evts["events"]
    if new_evts:
        types_summary = {}
        for e in new_evts:
            t = e.get("type","?")
            types_summary[t] = types_summary.get(t, 0) + 1
        st.session_state.debug_log.append(
            f"[{_ts()}] fetched {len(new_evts)} events "
            f"(since_id={st.session_state.last_event_id}, "
            f"summary={types_summary})"
        )

    # 已在消息列表里的权限请求 req_id（去重用）
    shown_perm_ids = {m.get("req_id") for m in st.session_state.messages
                      if m.get("role") == "permission"}

    for evt in new_evts:
        evt_id = evt.get("id", 0)
        if evt_id > st.session_state.last_event_id:
            st.session_state.last_event_id = evt_id

        etype = evt.get("type", "")

        if etype == "turn_start":
            # 修复：turn_start 携带用户输入，添加到对话面板（避免页面刷新后消失）
            msg_text = evt.get("message", evt.get("input", ""))
            if msg_text:
                last_user = next(
                    (m for m in reversed(st.session_state.messages)
                     if m.get("role") == "user"), None
                )
                if not last_user or last_user.get("content", "").strip() != msg_text.strip():
                    st.session_state.messages.append({
                        "role": "user", "content": msg_text, "time": _ts()
                    })

        elif etype == "token":
            text = evt.get("text", "")
            if text:
                # 关键修复：只有 streaming 消息是 messages 列表里"最末尾"的消息时
                # 才追加 text；否则说明 streaming 已被 tool_call 等消息隔断，
                # 需要新建一个 streaming 消息（这样 agent 文本和工具调用才能交错显示）
                msgs = st.session_state.messages
                if msgs and msgs[-1].get("role") == "streaming":
                    msgs[-1]["content"] += text
                else:
                    reason_for_new = msgs[-1].get("role","none") if msgs else "empty"
                    st.session_state.debug_log.append(
                        f"[{_ts()}] new streaming block (prev_role={reason_for_new})"
                    )
                    msgs.append({
                        "role": "streaming", "content": text,
                        "time": _ts()
                    })

        elif etype == "tool_call":
            # 修复：工具调用显示到对话面板
            tool_name  = evt.get("tool_name", evt.get("name", "unknown"))
            tool_input = evt.get("tool_input", evt.get("input", {}))
            st.session_state.debug_log.append(
                f"[{_ts()}] tool_call: {tool_name} | last_msg_role={st.session_state.messages[-1].get('role') if st.session_state.messages else 'none'}"
            )
            st.session_state.messages.append({
                "role": "tool_call",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "time": _ts(),
            })

        elif etype == "tool_result":
            # 修复：工具结果显示到对话面板
            tool_name   = evt.get("tool_name", evt.get("name", "unknown"))
            result_text = evt.get("result", evt.get("output", ""))
            st.session_state.debug_log.append(
                f"[{_ts()}] tool_result: {tool_name} | len={len(str(result_text))}"
            )
            st.session_state.messages.append({
                "role": "tool_result",
                "tool_name": tool_name,
                "content": str(result_text)[:1000],
                "time": _ts(),
            })

        elif etype == "tool_error":
            tool_name = evt.get("tool_name", "unknown")
            err_msg   = evt.get("message", "")
            st.session_state.messages.append({
                "role": "tool_error",
                "tool_name": tool_name,
                "content": err_msg,
                "time": _ts(),
            })

        elif etype == "turn_done":
            # 将所有 streaming 中间态转换为已完成状态（用 history 的最终文本替换）
            # 注意：messages 里可能有多个 streaming 消息块（每次 tool_call 后新建的）
            # 这些都是本 turn 的中间输出，turn_done 时统一处理
            st.session_state.messages = [m for m in st.session_state.messages
                                          if m.get("role") != "streaming"]
            # 从后端 history 拉取最新完整的 assistant 消息追加到末尾
            hist = client.history()
            full = ""
            if hist:
                for hm in reversed(hist.get("messages", [])):
                    if hm.get("role") == "assistant":
                        c = hm.get("content", "")
                        if isinstance(c, list):
                            c = "".join(x.get("text","") for x in c
                                        if isinstance(x, dict) and x.get("type") == "text")
                        full = str(c).strip()
                        break
            if full:
                last_asst = next(
                    (m for m in reversed(st.session_state.messages)
                     if m.get("role") == "assistant"), None
                )
                if not last_asst or last_asst.get("content", "").strip() != full:
                    st.session_state.messages.append({
                        "role": "assistant", "content": full, "time": _ts()
                    })
            st.session_state.agent_state = "idle"
            st.session_state.scroll_trigger += 1
            st.session_state.debug_log.append(f"[{_ts()}] turn_done -> idle")

        elif etype == "permission_req":
            req_id   = evt.get("req_id", "")
            tool_nm  = evt.get("tool_name", "unknown")
            tool_inp = evt.get("tool_input", {})
            st.session_state.debug_log.append(
                f"[{_ts()}] permission_req req_id={req_id!r} tool={tool_nm!r}"
            )
            if req_id and req_id not in shown_perm_ids:
                shown_perm_ids.add(req_id)
                st.session_state.messages.append({
                    "role":       "permission",
                    "req_id":     req_id,
                    "tool_name":  tool_nm,
                    "tool_input": tool_inp,
                    "approved":   None,
                    "time":       _ts(),
                })
                st.session_state.agent_state = "waiting_permission"
                st.session_state.scroll_trigger += 1

        elif etype == "permission_done":
            req_id        = evt.get("req_id", "")
            approved_flag = evt.get("approved", False)
            reason        = evt.get("reason", "")
            st.session_state.debug_log.append(
                f"[{_ts()}] permission_done req_id={req_id!r} "
                f"approved={approved_flag} reason={reason!r}"
            )
            for m in st.session_state.messages:
                if m.get("role") == "permission" and m.get("req_id") == req_id:
                    m["approved"] = approved_flag
                    m["reason"]   = reason
            # 修复：收到 permission_done 后本地状态改为 running
            # bridge 侧也已修复，/v1/status 将同步返回 running，下次轮询不会再覆盖回来
            if st.session_state.agent_state == "waiting_permission":
                st.session_state.agent_state = "running"
                st.session_state.debug_log.append(f"[{_ts()}] permission_done -> running")

        elif etype == "session_switched":
            # session 在后端被切换（可能是本页面操作，也可能是 CLI 或其他客户端）。
            # 若与本地记录的当前 session 不一致，重新加载历史与会话列表，
            # 确保多端 / CLI 切换 session 后 Web 端能保持同步。
            new_sid = evt.get("session_id", "")
            if new_sid and new_sid != st.session_state.current_session_id:
                st.session_state.debug_log.append(
                    f"[{_ts()}] session_switched -> {new_sid!r}"
                )
                st.session_state.current_session_id = new_sid
                st.session_state.preview_session_id = new_sid
                hist = client.history()
                st.session_state.messages = (
                    _history_to_messages(hist["messages"]) if hist and hist.get("messages") else []
                )
                st.session_state.event_log = []
                st.session_state.turn_id = None
                st.session_state.scroll_trigger += 1
                _refresh_sessions(client)

        st.session_state.event_log.append(evt)

    if len(st.session_state.debug_log) > 200:
        st.session_state.debug_log = st.session_state.debug_log[-200:]


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _history_to_messages(hist_messages: list) -> list:
    """将后端 /v1/history 返回的 messages 转换为对话面板格式（仅 user/assistant 纯文本）。

    使用 _type 字段精确过滤（向后兼容：无 _type 时用字符串前缀判断）。
    仅展示真实用户输入（user_input）和 assistant 回复（assistant_reply）。
    """
    # 仅展示的 _type 白名单（None 表示旧格式条目，按字符串前缀判断）
    _SHOW_TYPES = {"user_input", "assistant_reply", None}
    # 旧格式排除前缀
    _SKIP_PREFIXES = ("<tool_result", "[Previous", "[Compressed")

    result = []
    for m in hist_messages or []:
        role = m.get("role", "")
        c    = m.get("content", "")
        _type = m.get("_type")

        # 有 _type 时精确过滤
        if _type is not None:
            if _type not in _SHOW_TYPES:
                continue
        else:
            # 向后兼容：无 _type 时用旧逻辑
            if role == "user" and isinstance(c, str):
                if any(c.startswith(p) for p in _SKIP_PREFIXES):
                    continue

        text = ("".join(x.get("text", "") for x in c
                        if isinstance(x, dict) and x.get("type") == "text")
                if isinstance(c, list) else str(c))
        if role in ("user", "assistant") and text.strip():
            result.append({"role": role, "content": text, "time": ""})
    return result


def _refresh_sessions(client) -> None:
    """刷新会话列表与当前 session id（不触发 rerun）。"""
    data = client.sessions()
    if data is None:
        return
    if data.get("_unavailable"):
        st.session_state.sessions_list = []
        st.session_state.sessions_unavailable = True
        return
    st.session_state.sessions_unavailable = False
    st.session_state.sessions_list = data.get("sessions", [])
    st.session_state.current_session_id = data.get("current_session_id", "") or ""
    if not st.session_state.preview_session_id:
        st.session_state.preview_session_id = st.session_state.current_session_id


def _on_session_switched(client, result: dict, msg: str) -> None:
    """切换 / 新建 session 成功后，重置本地对话状态并重新加载历史。"""
    new_sid = result.get("session_id", "") or ""
    st.session_state.current_session_id = new_sid
    st.session_state.preview_session_id = new_sid
    st.session_state.event_log = []
    st.session_state.turn_id = None
    st.session_state.session_action_msg = msg
    st.session_state.session_preview = None

    hist = client.history()
    st.session_state.messages = (
        _history_to_messages(hist["messages"]) if hist and hist.get("messages") else []
    )
    st.session_state.scroll_trigger += 1

    status = client.status()
    if status:
        st.session_state.agent_state = status.get("state", "idle")
        st.session_state.stats = status.get("stats", {})

    _refresh_sessions(client)


# ═══════════════════════════════════════════════════════════════════════════════
# 渲染函数
# ═══════════════════════════════════════════════════════════════════════════════

def render_permission_panel():
    """
    权限审批面板（纯 Streamlit 原生组件）。
    渲染在对话区正下方，waiting_permission 状态 + 有待审批项时显示。
    """
    if not st.session_state.connected:
        return
    if st.session_state.agent_state != "waiting_permission":
        return

    client  = get_client()
    result  = client.pending_permissions()
    pending = result.get("permissions", []) if result else []

    st.markdown("---")
    st.markdown("### ⚠️ 权限审批")

    if not pending:
        st.info("⏳ 等待权限审批中（命令行也可以输入）...")
        return

    for perm in pending:
        req_id    = perm.get("req_id", "")
        tool_name = perm.get("tool_name", "unknown")
        tool_inp  = perm.get("tool_input", {})
        is_bash   = (tool_name == "bash")
        key_sfx   = req_id[:8] if req_id else "noid"

        with st.container(border=True):
            st.markdown(f"**🔐 工具请求权限：`{tool_name}`**")
            with st.expander("查看完整参数", expanded=True):
                st.code(json.dumps(tool_inp, ensure_ascii=False, indent=2), language="json")

            btn_cols = st.columns(4 if not is_bash else 5)

            with btn_cols[0]:
                if st.button("✅ 批准(y)", key=f"py_{key_sfx}", use_container_width=True, type="primary"):
                    try:
                        r = get_client().post(f"/permissions/{req_id}", {"approve": True, "mode": "once"})
                        if r.status_code == 200:
                            st.session_state.debug_log.append(f"[{_ts()}] web y req={req_id!r}")
                            st.rerun()
                    except Exception as e:
                        st.error(str(e))

            with btn_cols[1]:
                if st.button("⚡ 永久批准(a)", key=f"pa_{key_sfx}", use_container_width=True):
                    try:
                        r = get_client().post(f"/permissions/{req_id}", {"approve": True, "mode": "always"})
                        if r.status_code == 200:
                            st.session_state.debug_log.append(f"[{_ts()}] web a req={req_id!r}")
                            st.rerun()
                    except Exception as e:
                        st.error(str(e))

            with btn_cols[2]:
                if st.button("❌ 拒绝(n)", key=f"pn_{key_sfx}", use_container_width=True):
                    try:
                        r = get_client().post(f"/permissions/{req_id}", {"approve": False, "mode": "once"})
                        if r.status_code == 200:
                            st.session_state.debug_log.append(f"[{_ts()}] web n req={req_id!r}")
                            st.rerun()
                    except Exception as e:
                        st.error(str(e))

            with btn_cols[3]:
                if st.button("🚫 永久拒绝(d)", key=f"pd_{key_sfx}", use_container_width=True):
                    try:
                        r = get_client().post(f"/permissions/{req_id}", {"approve": False, "mode": "deny_always"})
                        if r.status_code == 200:
                            st.session_state.debug_log.append(f"[{_ts()}] web d req={req_id!r}")
                            st.rerun()
                    except Exception as e:
                        st.error(str(e))

            if is_bash and len(btn_cols) > 4:
                with btn_cols[4]:
                    if st.button("✏️ 编辑(e)", key=f"pe_{key_sfx}", use_container_width=True):
                        st.session_state[f"edit_open_{key_sfx}"] = True

            if is_bash and st.session_state.get(f"edit_open_{key_sfx}"):
                new_cmd = st.text_area("编辑命令", value=tool_inp.get("command",""),
                                       key=f"ecmd_{key_sfx}", height=80)
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("确认编辑", key=f"eok_{key_sfx}", type="primary"):
                        try:
                            r = get_client().post(f"/permissions/{req_id}",
                                                  {"approve": True, "mode": "once",
                                                   "edited_input": dict(tool_inp, command=new_cmd)})
                            if r.status_code == 200:
                                st.session_state[f"edit_open_{key_sfx}"] = False
                                st.rerun()
                        except Exception as e:
                            st.error(str(e))
                with c2:
                    if st.button("取消", key=f"ecancel_{key_sfx}"):
                        st.session_state[f"edit_open_{key_sfx}"] = False
                        st.rerun()


def render_debug_panel():
    """调试日志面板"""
    with st.expander("🔧 调试日志（排查问题用）", expanded=False):
        log = st.session_state.get("debug_log", [])
        c1, c2 = st.columns([4, 1])
        with c1:
            st.caption(f"agent_state={st.session_state.agent_state!r}  "
                       f"last_event_id={st.session_state.last_event_id}  "
                       f"msgs={len(st.session_state.messages)}  "
                       f"events={len(st.session_state.event_log)}")
        with c2:
            if st.button("清空", key="clear_dbg"):
                st.session_state.debug_log = []
                st.rerun()
        if log:
            st.code("\n".join(log[-60:]), language=None)
        else:
            st.caption("暂无日志")


def render_session_panel():
    """侧栏「会话管理」面板：列出所有 session，支持预览内容、切换/新建/删除。"""
    client = get_client()

    # 首次进入或列表为空时自动拉取一次（已知不可用时不再重复请求）
    if not st.session_state.sessions_list and not st.session_state.sessions_unavailable:
        _refresh_sessions(client)

    st.markdown('<div class="sidebar-section">会话管理</div>', unsafe_allow_html=True)

    if st.session_state.sessions_unavailable:
        st.caption("⚠️ 当前 Agent 未启用会话持久化（--no-save-session），无法管理会话")
        return

    if st.session_state.session_action_msg:
        st.success(st.session_state.session_action_msg)
        st.session_state.session_action_msg = ""

    sessions = st.session_state.sessions_list
    busy = st.session_state.agent_state not in ("idle", "unknown")

    hdr_c1, hdr_c2 = st.columns([4, 1])
    with hdr_c1:
        cur = st.session_state.current_session_id
        st.caption(f"当前会话: `{cur[:8]}`" if cur else "当前会话: 未知")
    with hdr_c2:
        if st.button("🔄", help="刷新会话列表", use_container_width=True, key="btn_refresh_sessions"):
            _refresh_sessions(client)
            st.rerun()

    if not sessions:
        st.caption("暂无会话记录")
    else:
        ids = [s["id"] for s in sessions]

        def _fmt(sid: str) -> str:
            s = next((x for x in sessions if x["id"] == sid), None)
            if not s:
                return sid
            mark  = "⭐" if s.get("is_current") else "▫️"
            title = s.get("title") or "(未命名)"
            if len(title) > 16:
                title = title[:16] + "…"
            return f'{mark} {title} · {s.get("turns", 0)}轮 · {s.get("age", "")}'

        default_id = st.session_state.preview_session_id or st.session_state.current_session_id
        default_idx = ids.index(default_id) if default_id in ids else 0

        selected_id = st.selectbox(
            "选择会话", options=ids, index=default_idx,
            format_func=_fmt, key="session_select_box", label_visibility="collapsed",
        )
        st.session_state.preview_session_id = selected_id

        sel_meta  = next((x for x in sessions if x["id"] == selected_id), None)
        is_current = bool(sel_meta and sel_meta.get("is_current"))

        # 内容预览（按 selected_id 缓存，避免 agent 运行时每 1.5s 重复请求）
        with st.expander("📜 预览会话内容", expanded=False):
            cache = st.session_state.session_preview
            if not cache or cache.get("_id") != selected_id:
                detail = get_client().session_detail(selected_id)
                cache = {**(detail or {}), "_id": selected_id}
                st.session_state.session_preview = cache

            if "_error" in cache:
                st.caption(f"加载失败: {cache['_error']}")
            else:
                msgs = _history_to_messages(cache.get("history", []))
                if not msgs:
                    st.caption("（暂无对话内容）")
                else:
                    for m in msgs[-8:]:
                        icon = "👤" if m["role"] == "user" else "🤖"
                        text = m["content"]
                        if len(text) > 200:
                            text = text[:200] + "…"
                        st.markdown(f"**{icon}** {text}")

        # 操作按钮
        act_c1, act_c2 = st.columns(2)
        with act_c1:
            if st.button("▶ 切换并继续", use_container_width=True, type="primary",
                          disabled=is_current or busy, key="btn_resume_session"):
                result = get_client().resume_session(selected_id)
                if result.get("ok"):
                    _on_session_switched(client, result, f"已切换到会话 {selected_id[:8]}")
                    st.rerun()
                else:
                    st.error(result.get("_error", "切换失败"))
        with act_c2:
            if st.button("🆕 新建会话", use_container_width=True,
                          disabled=busy, key="btn_new_session"):
                result = get_client().new_session()
                if result.get("ok"):
                    _on_session_switched(client, result, "已新建并切换到空会话")
                    st.rerun()
                else:
                    st.error(result.get("_error", "新建失败"))

        if busy:
            st.caption("⏳ Agent 运行中，请先中断 / 等待空闲后再切换会话")

        if not is_current:
            if st.button("🗑 删除该会话", use_container_width=True, key="btn_delete_session"):
                result = get_client().delete_session(selected_id)
                if result.get("ok"):
                    if st.session_state.preview_session_id == selected_id:
                        st.session_state.preview_session_id = st.session_state.current_session_id
                    _refresh_sessions(client)
                    st.success("已删除")
                    st.rerun()
                else:
                    st.error(result.get("_error", "删除失败"))


def render_sidebar():
    with st.sidebar:
        st.markdown("## 🤖 Mini Agent")
        st.markdown("---")

        # 连接配置
        st.markdown('<div class="sidebar-section">服务配置</div>', unsafe_allow_html=True)
        api_base = st.text_input(
            "API 地址", value=st.session_state.api_base,
            placeholder="http://127.0.0.1:8765/v1"
        )
        st.session_state.api_base = api_base

        # Token 配置
        st.markdown('<div class="sidebar-section">认证 Token</div>', unsafe_allow_html=True)
        token_mode = st.radio("Token 来源", ["手动输入", "从文件读取"],
                               horizontal=True, label_visibility="collapsed")

        if token_mode == "手动输入":
            token = st.text_input("Bearer Token", value=st.session_state.token,
                                   type="password", placeholder="输入 API Token（留空则无认证）")
            st.session_state.token = token
        else:
            auto_path = find_key_file_auto()
            key_file = st.text_input("Key 文件路径",
                                      value=st.session_state.key_file_path or auto_path,
                                      placeholder="agent_api.key 路径")
            st.session_state.key_file_path = key_file
            if st.button("📂 读取 Token", use_container_width=True):
                t = try_read_token_from_file(key_file)
                if t:
                    st.session_state.token = t
                    st.success(f"已读取: {t[:8]}...")
                else:
                    st.error("读取失败")
            if st.session_state.token:
                st.caption(f"当前: `{st.session_state.token[:8]}...`")

        # 连接/刷新
        st.markdown("---")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔌 连接", use_container_width=True, type="primary"):
                client = get_client()
                if client.health():
                    st.session_state.connected = True
                    s = client.status()
                    if s:
                        st.session_state.agent_state = s.get("state", "unknown")
                        st.session_state.stats = s.get("stats", {})
                    _refresh_sessions(client)
                    st.success("连接成功！")
                else:
                    st.session_state.connected = False
                    st.error("连接失败")
        with c2:
            if st.button("🔄 刷新", use_container_width=True):
                if st.session_state.connected:
                    client = get_client()
                    s = client.status()
                    if s:
                        st.session_state.agent_state = s.get("state", "unknown")
                        st.session_state.stats = s.get("stats", {})
                    _refresh_sessions(client)

        # 连接状态
        if st.session_state.connected:
            st.markdown(
                f'<div class="conn-bar conn-ok">✓ 已连接 | {state_badge(st.session_state.agent_state)}</div>',
                unsafe_allow_html=True)
        else:
            st.markdown('<div class="conn-bar conn-fail">✗ 未连接</div>', unsafe_allow_html=True)

        # 会话管理
        if st.session_state.connected:
            render_session_panel()

        # 统计
        if st.session_state.stats:
            st.markdown('<div class="sidebar-section">运行统计</div>', unsafe_allow_html=True)
            s = st.session_state.stats
            col1, col2 = st.columns(2)
            with col1:
                st.metric("总轮次", s.get("total_turns", 0))
                st.metric("工具调用", s.get("tool_calls", 0))
            with col2:
                total_tok = s.get("total_tokens", 0)
                st.metric("Token", f"{total_tok:,}" if total_tok else "0")
                elapsed = s.get("elapsed", "")
                if elapsed:
                    st.metric("耗时", elapsed)

        # 视图开关
        st.markdown('<div class="sidebar-section">视图</div>', unsafe_allow_html=True)
        st.session_state.show_events = st.toggle("显示事件流", value=st.session_state.show_events)
        st.session_state.show_fs     = st.toggle("文件系统",   value=st.session_state.show_fs)

        # 操作按钮
        st.markdown('<div class="sidebar-section">操作</div>', unsafe_allow_html=True)

        if st.button("⏹ 中断执行", use_container_width=True,
                     disabled=not st.session_state.connected):
            if get_client().interrupt():
                st.success("已中断")
            else:
                st.error("中断失败")

        if st.button("🗑 清空对话历史", use_container_width=True,
                     disabled=not st.session_state.connected):
            if get_client().clear_history():
                st.session_state.messages      = []
                st.session_state.event_log     = []
                st.session_state.last_event_id = 0
                st.rerun()

        if st.button("🧹 清空事件日志", use_container_width=True):
            st.session_state.event_log = []
            st.rerun()


# render_permission_panel 已在上方定义（第781行），此处不重复定义


def render_chat_messages(container):
    """在给定容器内渲染对话消息"""
    with container:
        if not st.session_state.messages:
            st.markdown("""
<div style="text-align:center;padding:60px 20px;color:#555">
<div style="font-size:48px;margin-bottom:16px">🤖</div>
<div style="font-size:16px;color:#666">连接到 Agent 后开始对话</div>
<div style="font-size:13px;color:#444;margin-top:8px">支持多轮对话、工具调用、流式输出</div>
</div>""", unsafe_allow_html=True)
            return

        for msg in st.session_state.messages:
            role     = msg["role"]
            time_str = msg.get("time", "")

            if role == "user":
                content = msg.get("content", "")
                st.markdown(f"""<div class="msg-user">
<div class="msg-role" style="color:#a09aff">👤 你</div>
{content}
<div class="msg-time">{time_str}</div>
</div>""", unsafe_allow_html=True)

            elif role == "assistant":
                import re as _re, html as _html
                content = msg.get("content", "")
                # 过滤 system_tool_call 模式的工具调用标签
                clean = _re.sub(r"<tool_use>.*?</tool_use>", "", content, flags=_re.DOTALL).strip()
                if clean:
                    clean_escaped = _html.escape(clean)
                    st.markdown(f"""<div class="msg-agent">
<div class="msg-role" style="color:#6fcf6f">🤖 Agent</div>
<pre style="white-space:pre-wrap;margin:4px 0;font-size:13px;background:transparent;border:none">{clean_escaped}</pre>
<div class="msg-time">{time_str}</div>
</div>""", unsafe_allow_html=True)

            elif role == "streaming":
                import re as _re
                content = msg.get("content", "")
                # 过滤掉 system_tool_call 模式下 LLM 输出的 <tool_use>...</tool_use> 标签
                # 这些工具调用已经由 tool_call 事件单独显示，streaming 框里只保留纯文本
                clean = _re.sub(r"<tool_use>.*?</tool_use>", "", content, flags=_re.DOTALL).strip()
                if not clean:
                    # 内容全是 tool_use 标签，不渲染空框
                    pass
                else:
                    # 对 HTML 特殊字符做转义（避免 agent 输出破坏 HTML 结构）
                    import html as _html
                    clean_escaped = _html.escape(clean)
                    st.markdown(f"""<div class="msg-agent" style="border-left-color:#FF9800">
<div class="msg-role" style="color:#FF9800">🤖 Agent (输出中...)</div>
<pre style="white-space:pre-wrap;margin:4px 0;font-size:13px;background:transparent;border:none">{clean_escaped}</pre>
<span style="display:inline-block;width:8px;height:14px;background:#FF9800;
animation:pulse 1s infinite;vertical-align:text-bottom;margin-left:2px">▊</span>
</div>""", unsafe_allow_html=True)

            elif role == "permission":
                tool_name  = msg.get("tool_name", "unknown")
                tool_input = msg.get("tool_input", {})
                approved   = msg.get("approved")    # None=待定, True=批准, False=拒绝
                reason     = msg.get("reason", "")

                if approved is None:
                    # 待审批：显示完整卡片
                    status_html = """
<div style="margin-top:10px;background:#1A1200;border:1px solid #FF7043;border-radius:8px;padding:10px">
  <div style="color:#FF7043;font-size:12px;font-weight:bold;margin-bottom:6px">⏳ 等待审批 — 可在下方输入框快捷操作：</div>
  <div style="display:flex;flex-wrap:wrap;gap:6px;font-size:12px">
    <span style="background:#1B3A1B;color:#81C784;border:1px solid #2E7D32;border-radius:4px;padding:2px 8px"><b>y</b> 批准（本次）</span>
    <span style="background:#1B2F1B;color:#A5D6A7;border:1px solid #388E3C;border-radius:4px;padding:2px 8px"><b>a</b> 永久批准</span>
    <span style="background:#3A1B1B;color:#EF9A9A;border:1px solid #7D2E2E;border-radius:4px;padding:2px 8px"><b>n</b> 拒绝（本次）</span>
    <span style="background:#2F1B1B;color:#FFCDD2;border:1px solid #B71C1C;border-radius:4px;padding:2px 8px"><b>d</b> 永久拒绝</span>
  </div>
  <div style="color:#888;font-size:11px;margin-top:6px">或直接点击下方权限面板的按钮</div>
</div>"""
                    input_preview = json.dumps(tool_input, ensure_ascii=False, indent=2)
                    st.markdown(f"""<div class="permission-card">
<div class="permission-title">🔐 权限请求: {tool_name}</div>
<div class="permission-content">{input_preview}</div>
{status_html}
<div class="msg-time">{time_str}</div>
</div>""", unsafe_allow_html=True)

                else:
                    # 已处理：只显示一行简洁状态，不显示完整卡片
                    src = {"cli":"命令行","http":"Web界面","timeout":"超时","user":"Web界面"}.get(reason, reason or "")
                    if approved:
                        icon, color, label = "✅", "#4CAF50", f"已批准"
                    else:
                        icon, color, label = "❌", "#EF5350", f"已拒绝"
                    src_str = f"（{src}）" if src else ""
                    # 工具调用摘要（只取关键字段）
                    summary_fields = {k: v for k, v in list(tool_input.items())[:2]}
                    summary = ", ".join(f"{k}={repr(str(v))[:30]}" for k, v in summary_fields.items())
                    st.markdown(f"""<div style="padding:4px 10px;border-left:3px solid {color};margin:2px 0;font-size:12px;color:#CCCCCC !important;background:#111">
{icon} <span style="color:{color} !important">{label}{src_str}</span> &nbsp;·&nbsp; <span style="color:#AAAAAA !important">{tool_name}</span> <span style="color:#888888 !important;font-size:11px">{summary}</span>
<span style="float:right;font-size:10px;color:#666 !important">{time_str}</span></div>""", unsafe_allow_html=True)

            elif role == "tool_call":
                tool_name  = msg.get("tool_name", "unknown")
                tool_input = msg.get("tool_input", {})
                import html as _html
                args_str = _html.escape(json.dumps(tool_input, ensure_ascii=False, indent=2)[:600])
                st.markdown(f"""<div style="background:#1C1600 !important;border:1px solid #7A6000 !important;border-radius:8px;padding:10px;margin:6px 0;font-family:monospace;font-size:12px;color:#FFFFFF !important">
<div style="color:#FFD54F !important;font-weight:bold !important;margin-bottom:6px !important;font-size:12px !important">🔧 工具调用: {tool_name}</div>
<pre style="color:#FFFFFF !important;font-size:11px !important;white-space:pre-wrap !important;background:transparent !important;margin:0 !important;word-break:break-all">{args_str}</pre>
<div style="color:#666 !important;font-size:10px !important;text-align:right;margin-top:4px">{time_str}</div>
</div>""", unsafe_allow_html=True)

            elif role == "tool_result":
                tool_name = msg.get("tool_name", "unknown")
                content   = msg.get("content", "")
                import html as _html
                content_escaped = _html.escape(content)
                st.markdown(f"""<div style="background:#061A06 !important;border:1px solid #2E7D32 !important;border-radius:8px;padding:10px;margin:6px 0;font-family:monospace;font-size:12px;color:#FFFFFF !important">
<div style="color:#00E676 !important;font-weight:bold !important;margin-bottom:6px !important;font-size:12px !important">✅ 工具结果: {tool_name}</div>
<pre style="color:#E8F5E9 !important;font-size:11px !important;white-space:pre-wrap !important;background:transparent !important;margin:0 !important;word-break:break-all">{content_escaped}</pre>
<div style="color:#666 !important;font-size:10px !important;text-align:right;margin-top:4px">{time_str}</div>
</div>""", unsafe_allow_html=True)

            elif role == "tool_error":
                tool_name = msg.get("tool_name", "unknown")
                content   = msg.get("content", "")
                import html as _html
                content_escaped = _html.escape(content)
                st.markdown(f"""<div style="background:#1A0606 !important;border:1px solid #C62828 !important;border-radius:8px;padding:10px;margin:6px 0;font-family:monospace;font-size:12px;color:#FFFFFF !important">
<div style="color:#FF5252 !important;font-weight:bold !important;margin-bottom:6px !important;font-size:12px !important">❌ 工具错误: {tool_name}</div>
<pre style="color:#FFEBEE !important;font-size:11px !important;white-space:pre-wrap !important;background:transparent !important;margin:0 !important;word-break:break-all">{content_escaped}</pre>
<div style="color:#666 !important;font-size:10px !important;text-align:right;margin-top:4px">{time_str}</div>
</div>""", unsafe_allow_html=True)


def _handle_permission_input(msg_text: str) -> bool:
    """
    在 waiting_permission 状态下，尝试将输入解析为权限审批指令。
    支持：y/yes、a/always、n/no、d/deny
    返回 True 表示已处理（不需要再走普通发送流程）。
    """
    cmd = msg_text.strip().lower()
    APPROVE_MAP = {
        "y": ("approve", True,  "once"),
        "yes": ("approve", True,  "once"),
        "a": ("approve", True,  "always"),
        "always": ("approve", True, "always"),
        "n": ("approve", False, "once"),
        "no": ("approve", False, "once"),
        "d": ("approve", False, "deny_always"),
        "deny": ("approve", False, "deny_always"),
    }
    if cmd not in APPROVE_MAP:
        return False

    client  = get_client()
    result  = client.pending_permissions()
    pending = result.get("permissions", []) if result else []
    if not pending:
        return False

    # 取第一个待审批项
    perm   = pending[0]
    req_id = perm.get("req_id", "")
    if not req_id:
        return False

    _, approve, mode = APPROVE_MAP[cmd]
    now_str = datetime.now().strftime("%H:%M:%S")

    try:
        r = client.post(f"/permissions/{req_id}", {"approve": approve, "mode": mode})
        if r.status_code == 200:
            action_label = {
                "once":         "✅ 批准(y)" if approve else "❌ 拒绝(n)",
                "always":       "⚡ 永久批准(a)",
                "deny_always":  "🚫 永久拒绝(d)",
            }.get(mode, cmd)
            st.session_state.messages.append({
                "role": "user", "content": f"[权限审批] {action_label}", "time": now_str
            })
            st.session_state.input_key += 1
            st.session_state.debug_log.append(
                f"[{_ts()}] perm input '{cmd}' -> req={req_id!r} approve={approve} mode={mode}"
            )
            st.session_state.agent_state = "running"
            st.session_state.scroll_trigger += 1
            return True
    except Exception as e:
        st.session_state.debug_log.append(f"[{_ts()}] perm input error: {e}")
    return False


def handle_send(msg_text: str):
    """发送消息，立即返回。后续轮询由前端 JS 完成。"""
    # waiting_permission 状态下，优先尝试解析为权限审批指令
    if st.session_state.agent_state == "waiting_permission":
        if _handle_permission_input(msg_text):
            return

    client  = get_client()
    now_str = datetime.now().strftime("%H:%M:%S")

    st.session_state.messages.append({"role": "user", "content": msg_text, "time": now_str})
    st.session_state.input_key += 1

    result = client.chat(msg_text)
    if not (result and "turn_id" in result):
        err = result.get("error", "未知错误") if result else "发送失败，请检查连接"
        st.session_state.messages.append({
            "role": "assistant", "content": f"❌ {err}",
            "time": datetime.now().strftime("%H:%M:%S")
        })
        return

    turn_id = result["turn_id"]
    st.session_state.turn_id    = turn_id
    st.session_state.agent_state = "running"
    st.session_state.scroll_trigger += 1


def render_input_area():
    """输入框 + 按钮区域，返回需要执行的操作"""
    is_waiting_perm = st.session_state.agent_state == "waiting_permission"

    placeholder = (
        "输入权限指令：y 批准 / a 永久批准 / n 拒绝 / d 永久拒绝"
        if is_waiting_perm
        else "输入消息... (点击「发送」按钮)"
    )

    # key 随 input_key 变化，从而实现发送后清空
    user_input = st.text_area(
        "消息输入",
        placeholder=placeholder,
        height=100,
        key=f"user_input_{st.session_state.input_key}",
        label_visibility="collapsed"
    )

    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([2, 1, 1, 1])
    with btn_col1:
        # waiting_permission 时允许发送（用于输入审批指令）
        send_disabled = (
            not st.session_state.connected
            or not (user_input or "").strip()
        )
        send_label = "🔐 提交审批" if is_waiting_perm else "📨 发送"
        send_btn = st.button(
            send_label, use_container_width=True, type="primary",
            disabled=send_disabled
        )
    with btn_col2:
        sync_btn = st.button("📋 同步历史", use_container_width=True,
                              disabled=not st.session_state.connected)
    with btn_col3:
        turns_btn = st.button("📊 查看 Turns", use_container_width=True,
                               disabled=not st.session_state.connected)
    with btn_col4:
        events_btn = st.button("🔔 拉取事件", use_container_width=True,
                                disabled=not st.session_state.connected)

    return user_input or "", send_btn, sync_btn, turns_btn, events_btn


def render_event_panel():
    """右侧事件流面板"""
    st.markdown("#### 📡 实时事件流")
    filter_types = st.multiselect(
        "过滤", ["token","tool_call","tool_result","tool_error",
                "turn_start","turn_done","permission_req",
                "status","error","info","warning","session_switched"],
        default=["tool_call","tool_result","turn_start","turn_done",
                 "error","permission_req","warning","session_switched"],
        label_visibility="collapsed"
    )
    container = st.container(height=520)
    with container:
        events = st.session_state.event_log
        if filter_types:
            events = [e for e in events if e.get("type") in filter_types]
        if not events:
            st.markdown('<div style="color:#555;text-align:center;padding:40px">暂无事件</div>',
                        unsafe_allow_html=True)
        else:
            parts = [format_event_html(e) for e in events[-200:]]
            st.markdown(
                '<div style="font-family:monospace;font-size:12px;line-height:1.8">'
                + "".join(parts) + '</div>',
                unsafe_allow_html=True
            )


def render_turns_panel():
    """Turn 历史展开面板"""
    with st.expander("📊 Turn 历史记录", expanded=True):
        data = get_client().turns()
        if not (data and data.get("turns")):
            st.info("暂无 Turn 记录")
            return
        colors = {"done":"#4CAF50","running":"#FF9800","error":"#F44336","interrupted":"#FF7043"}
        for t in reversed(data["turns"][-20:]):
            tid   = t.get("turn_id","")[:12]
            state = t.get("state","")
            inp   = t.get("input","")[:60]
            tc    = t.get("token_count",0)
            ts    = t.get("started_at",0)
            t_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""
            color = colors.get(state,"#888")
            st.markdown(
                f'<div style="padding:6px;border-bottom:1px solid #333;font-size:12px">'
                f'<span style="color:#666">{t_str}</span> '
                f'<code style="color:#aaa">{tid}</code> '
                f'<span style="color:{color}">[{state}]</span> '
                f'<span style="color:#888">{inp}</span>'
                f'<span style="color:#555;float:right">{tc} tokens</span></div>',
                unsafe_allow_html=True
            )


def render_fs_panel():
    """文件系统浏览面板"""
    if not (st.session_state.show_fs and st.session_state.connected):
        return
    st.markdown("---")
    st.markdown("### 📁 文件系统")
    client = get_client()

    c1, c2, c3 = st.columns([3, 1, 1])
    with c1:
        fs_path = st.text_input("路径", value=st.session_state.fs_path,
                                 label_visibility="collapsed", placeholder="输入路径")
        st.session_state.fs_path = fs_path
    with c2:
        if st.button("📂 浏览", use_container_width=True):
            r = client.fs_list(fs_path)
            st.session_state.fs_entries = r.get("entries", []) if r else []
    with c3:
        if st.button("🏠 根目录", use_container_width=True):
            st.session_state.fs_path = "."
            r = client.fs_list(".")
            st.session_state.fs_entries = r.get("entries", []) if r else []

    if not st.session_state.fs_entries:
        return

    ch1, ch2, ch3 = st.columns([3, 1, 1])
    with ch1: st.caption("名称")
    with ch2: st.caption("大小")
    with ch3: st.caption("操作")

    for entry in sorted(st.session_state.fs_entries,
                        key=lambda x: (not x.get("is_dir"), x.get("name",""))):
        name   = entry.get("name","")
        path   = entry.get("path","")
        is_dir = entry.get("is_dir", False)
        size   = entry.get("size", 0)
        size_s = f"{size/1024:.1f}K" if size > 1024 else (f"{size}B" if not is_dir else "")
        icon   = "📁" if is_dir else "📄"

        ec1, ec2, ec3 = st.columns([3, 1, 1])
        with ec1: st.markdown(f'{icon} `{name}`')
        with ec2: st.caption(size_s)
        with ec3:
            if is_dir:
                if st.button("进入", key=f"fs_enter_{path}", use_container_width=True):
                    st.session_state.fs_path = path
                    r = client.fs_list(path)
                    st.session_state.fs_entries = r.get("entries",[]) if r else []
                    st.rerun()
            else:
                if st.button("查看", key=f"fs_view_{path}", use_container_width=True):
                    fd = client.fs_read(path)
                    if fd:
                        with st.expander(f"📄 {name}", expanded=True):
                            st.code(fd.get("content","")[:5000], language=None)


def render_footer():
    st.markdown("---")
    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
    with c1:
        label = f"🟢 `{st.session_state.api_base}`" if st.session_state.connected else "🔴 未连接"
        st.caption(label)
    with c2:
        st.caption(f"💬 {len([m for m in st.session_state.messages if m['role'] != 'streaming'])} 条消息")
    with c3:
        st.caption(f"📡 {len(st.session_state.event_log)} 条事件")
    with c4:
        if st.session_state.turn_id:
            st.caption(f"🔄 Turn: `{st.session_state.turn_id[:12]}`")


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    inject_styles()
    init_session()

    # ── 每次 rerun 时同步后端事件到 session_state ──────────────────────────
    _sync_events()

    # 侧栏
    render_sidebar()

    # 标题栏
    title_col, status_col = st.columns([3, 1])
    with title_col:
        st.markdown("### 💬 Agent 对话")
    with status_col:
        if st.session_state.connected:
            st.markdown(
                f'<div style="text-align:right;padding-top:8px">'
                f'{state_badge(st.session_state.agent_state)}</div>',
                unsafe_allow_html=True
            )

    # 主布局：对话区 + 事件流
    if st.session_state.show_events:
        chat_col, event_col = st.columns([3, 2])
    else:
        chat_col  = st.container()
        event_col = None

    with chat_col:
        # ── 对话消息区 ──
        # autoscroll=True 对 rerun 模式不可靠（每次 rerun 组件树重建，useRef 状态重置）
        # 改用 components.v1.html 在容器内注入 JS 精确滚动
        chat_container = st.container(height=480)
        render_chat_messages(chat_container)
        # scroll iframe 必须放在 chat_container 内部，
        # 这样 window.frameElement.closest(...) 才能找到正确的父容器
        with chat_container:
            if st.session_state.messages:
                scroll_chat_to_bottom()

        # ── 权限审批面板（在对话区下方，纯 Streamlit 原生组件）──
        render_permission_panel()

        # ── 输入区 ──
        user_input, send_btn, sync_btn, turns_btn, events_btn = render_input_area()

        # 处理发送
        if send_btn and user_input.strip():
            handle_send(user_input.strip())
            st.rerun()

        # 同步历史
        if sync_btn:
            hist = get_client().history()
            if hist and hist.get("messages"):
                st.session_state.messages = _history_to_messages(hist["messages"])
                st.session_state.scroll_trigger += 1
                st.success(f"已同步 {len(st.session_state.messages)} 条历史")
                st.rerun()

        # 手动拉取事件
        if events_btn:
            _sync_events()
            st.success("已同步事件")
            st.rerun()

        # Turn 历史
        if turns_btn:
            render_turns_panel()

        # 调试日志（折叠）
        render_debug_panel()

    # 事件流面板
    if event_col is not None:
        with event_col:
            render_event_panel()

    # 文件系统
    render_fs_panel()

    # 底部状态栏
    render_footer()

    # ── 定时 rerun：agent 活跃时每 1.5s 自动刷新，驱动 _sync_events ──────
    if st.session_state.connected and st.session_state.agent_state in ("running", "waiting_permission"):
        time.sleep(1.5)
        st.rerun()
    elif st.session_state.connected and st.session_state.agent_state == "idle":
        # idle 状态下短暂再刷新一次，确保最后一批事件（turn_done、permission_done）已同步
        if st.session_state.get("_pending_idle_sync", 0) < 2:
            st.session_state["_pending_idle_sync"] = st.session_state.get("_pending_idle_sync", 0) + 1
            time.sleep(1.0)
            st.rerun()
        else:
            st.session_state["_pending_idle_sync"] = 0
    else:
        st.session_state["_pending_idle_sync"] = 0


if __name__ == "__main__" or True:
    main()