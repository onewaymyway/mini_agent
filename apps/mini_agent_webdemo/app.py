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
    background: #1A1500; border: 1px solid #3D3000;
    border-radius: 8px; padding: 10px; margin: 6px 0;
    font-family: monospace; font-size: 12px;
}
.tool-name { color: #FFB74D; font-weight: bold; margin-bottom: 6px; }

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
        # 用于清空输入框：每次发送后递增，作为 text_area 的 key
        "input_key":        0,
        # 用于触发自动滚动
        "scroll_trigger":   0,
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

    def events(self, since_id=0, limit=100):
        try:
            r = self.get("/events", params={"since_id": since_id, "limit": limit})
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
    msg = data.get("message", str(data))[:200]
    return f'<div class="{css}" style="padding:2px 0;font-size:12px">[{etype}] {msg}</div>'


def scroll_chat_to_bottom():
    """
    注入 JS 让对话容器滚动到底部。

    Streamlit 的 st.container(height=N) 会在 DOM 中生成一个
    data-testid="stVerticalBlockBorderWrapper" 的外层 div，
    其内部紧跟的第一个 div 就是实际带 overflow:auto 的滚动容器。

    策略：
      1. 找到所有 stVerticalBlockBorderWrapper
      2. 对每一个，尝试其直接子元素以及再向下一层，找 overflow:auto/scroll 的元素
      3. 多次延迟执行，确保 Streamlit 渲染完成后滚动生效
    """
    scroll_js = """
<script>
(function() {
    function scrollAll() {
        var doc = window.parent.document;

        // 策略1：找带固定高度边框包装的容器（st.container(height=...)）
        var wrappers = doc.querySelectorAll('[data-testid="stVerticalBlockBorderWrapper"]');
        wrappers.forEach(function(wrapper) {
            // 直接子 div 通常就是滚动容器
            var children = wrapper.children;
            for (var i = 0; i < children.length; i++) {
                var child = children[i];
                var style = window.parent.getComputedStyle(child);
                if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                    child.scrollTop = child.scrollHeight;
                }
            }
            // 备用：在后代中搜索（最多3层）
            var els = wrapper.querySelectorAll('div');
            for (var j = 0; j < Math.min(els.length, 10); j++) {
                var el = els[j];
                var s = window.parent.getComputedStyle(el);
                if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
                    el.scrollTop = el.scrollHeight;
                }
            }
        });

        // 策略2：兜底——找页面中所有可滚动 div（按高度排序取最大的那个，通常是聊天区）
        if (wrappers.length === 0) {
            var allDivs = Array.from(doc.querySelectorAll('div'));
            allDivs.filter(function(d) {
                var s = window.parent.getComputedStyle(d);
                return (s.overflowY === 'auto' || s.overflowY === 'scroll') && d.scrollHeight > d.clientHeight + 50;
            }).forEach(function(d) {
                d.scrollTop = d.scrollHeight;
            });
        }
    }

    // 多次触发：100ms（DOM 刚生成）、400ms（渲染稳定）、800ms（保险）
    setTimeout(scrollAll, 100);
    setTimeout(scrollAll, 400);
    setTimeout(scrollAll, 800);
})();
</script>
"""
    st.components.v1.html(scroll_js, height=0)


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
      if (e.type === "permission_req")  permReqs.push(e.data||{{}});
      if (e.type === "permission_done") permDones.push(e.data||{{}});
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


def _process_js_poll_callback():
    """
    处理 JS 轮询组件通过 query_params 或 session 传回的事件。
    Streamlit components 的 bidirectional 通信需要用 components.declare_component，
    这里用更简单的方案：JS 通过 sendPrompt 发一条特殊格式的聊天消息，
    Python 在发送前检测并处理它。

    由于 st.components.v1.html 是单向的（JS→Python 没有原生回调），
    我们改用另一个策略：JS 直接操作 REST API（已实现），
    Python 只需在每次 rerun 时主动拉取最新事件。
    """
    if not st.session_state.connected:
        return
    # 只在 running 或 waiting_permission 时同步
    if st.session_state.agent_state not in ("running", "waiting_permission", "unknown"):
        return

    client = get_client()
    # 拉取状态
    s = client.status()
    if s:
        new_state = s.get("state", st.session_state.agent_state)
        st.session_state.agent_state = new_state

    # 拉取新事件（更新消息列表和事件日志）
    evts = client.events(since_id=st.session_state.last_event_id, limit=200)
    if not (evts and evts.get("events")):
        return

    new_evts = evts["events"]
    shown_ids = {m.get("req_id") for m in st.session_state.messages if m.get("role") == "permission"}
    done_ids  = set()

    for evt in new_evts:
        evt_id = evt.get("id", 0)
        if evt_id > st.session_state.last_event_id:
            st.session_state.last_event_id = evt_id

        etype = evt.get("type", "")
        edata = evt.get("data", {})

        if etype == "token":
            # 累积 token 到最后一条 assistant 消息或新建
            text = edata.get("text", "")
            if text:
                # 找到最后一条 streaming 消息
                last_stream = None
                for m in reversed(st.session_state.messages):
                    if m.get("role") == "streaming":
                        last_stream = m
                        break
                if last_stream:
                    last_stream["content"] += text
                else:
                    st.session_state.messages.append({
                        "role": "streaming", "content": text,
                        "time": datetime.now().strftime("%H:%M:%S")
                    })

        elif etype == "turn_done":
            # 把 streaming 消息合并成 assistant 消息
            streaming_parts = []
            new_msgs = []
            for m in st.session_state.messages:
                if m.get("role") == "streaming":
                    streaming_parts.append(m["content"])
                else:
                    new_msgs.append(m)
            st.session_state.messages = new_msgs
            full = "".join(streaming_parts).strip()
            if full:
                st.session_state.messages.append({
                    "role": "assistant", "content": full,
                    "time": datetime.now().strftime("%H:%M:%S")
                })
            elif not full:
                # 没有 token 流，从历史取
                hist = client.history()
                if hist and hist.get("messages"):
                    for hm in reversed(hist["messages"]):
                        if hm.get("role") == "assistant":
                            c = hm.get("content", "")
                            if isinstance(c, list):
                                c = "".join(x.get("text","") for x in c
                                            if isinstance(x,dict) and x.get("type")=="text")
                            if c:
                                st.session_state.messages.append({
                                    "role": "assistant", "content": str(c),
                                    "time": datetime.now().strftime("%H:%M:%S")
                                })
                            break
            st.session_state.agent_state = "idle"
            st.session_state.scroll_trigger += 1

        elif etype == "permission_req":
            req_id_evt = edata.get("req_id", "")
            if req_id_evt and req_id_evt not in shown_ids:
                shown_ids.add(req_id_evt)
                tool_nm  = edata.get("tool_name", "unknown")
                tool_inp = edata.get("tool_input", {})
                st.session_state.messages.append({
                    "role":      "permission",
                    "req_id":    req_id_evt,
                    "tool_name": tool_nm,
                    "tool_input": tool_inp,
                    "approved":  None,   # None=待定
                    "time":      datetime.now().strftime("%H:%M:%S"),
                })
                st.session_state.agent_state = "waiting_permission"
                st.session_state.scroll_trigger += 1

        elif etype == "permission_done":
            req_id_done   = edata.get("req_id", "")
            approved_flag = edata.get("approved", False)
            reason        = edata.get("reason", "")
            if req_id_done and req_id_done not in done_ids:
                done_ids.add(req_id_done)
                for m in st.session_state.messages:
                    if m.get("role") == "permission" and m.get("req_id") == req_id_done:
                        m["approved"] = approved_flag
                        m["reason"]   = reason

        st.session_state.event_log.append(evt)


# ═══════════════════════════════════════════════════════════════════════════════
# 渲染函数
# ═══════════════════════════════════════════════════════════════════════════════

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

        # 连接状态
        if st.session_state.connected:
            st.markdown(
                f'<div class="conn-bar conn-ok">✓ 已连接 | {state_badge(st.session_state.agent_state)}</div>',
                unsafe_allow_html=True)
        else:
            st.markdown('<div class="conn-bar conn-fail">✗ 未连接</div>', unsafe_allow_html=True)

        # 统计
        if st.session_state.stats:
            st.markdown('<div class="sidebar-section">运行统计</div>', unsafe_allow_html=True)
            s = st.session_state.stats
            col1, col2 = st.columns(2)
            with col1:
                st.metric("总轮次", s.get("total_turns", 0))
            with col2:
                st.metric("Token", f"{s.get('total_tokens', 0):,}")

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


def render_permission_panel():
    """权限审批已由 JS 实时轮询组件接管，此函数保留为空以兼容调用点。"""
    pass


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
                content = msg.get("content", "")
                st.markdown(f"""<div class="msg-agent">
<div class="msg-role" style="color:#6fcf6f">🤖 Agent</div>
{content}
<div class="msg-time">{time_str}</div>
</div>""", unsafe_allow_html=True)

            elif role == "streaming":
                content = msg.get("content", "")
                st.markdown(f"""<div class="msg-agent" style="border-left-color:#FF9800">
<div class="msg-role" style="color:#FF9800">🤖 Agent (输出中...)</div>
{content}
<span style="display:inline-block;width:8px;height:14px;background:#FF9800;
animation:pulse 1s infinite;vertical-align:text-bottom;margin-left:2px">▊</span>
</div>""", unsafe_allow_html=True)

            elif role == "permission":
                # 权限请求消息：显示工具名、参数摘要、审批结果标记
                tool_name  = msg.get("tool_name", "unknown")
                tool_input = msg.get("tool_input", {})
                approved   = msg.get("approved")    # None=待定, True=批准, False=拒绝
                reason     = msg.get("reason", "")

                if approved is None:
                    status_html = '<div style="color:#FF7043;font-size:12px;margin-top:8px">⏳ 请在下方权限面板审批（或在命令行输入）</div>'
                elif approved:
                    src = {"cli":"命令行","http":"Web界面","timeout":"超时"}.get(reason, reason)
                    status_html = f'<div style="color:#4CAF50;font-size:12px;margin-top:8px">✅ 已批准（{src}）</div>'
                else:
                    src = {"cli":"命令行","http":"Web界面","timeout":"超时"}.get(reason, reason)
                    status_html = f'<div style="color:#EF5350;font-size:12px;margin-top:8px">❌ 已拒绝（{src}）</div>'

                input_preview = json.dumps(tool_input, ensure_ascii=False, indent=2)
                st.markdown(f"""<div class="permission-card">
<div class="permission-title">🔐 权限请求: {tool_name}</div>
<div class="permission-content">{input_preview}</div>
{status_html}
<div class="msg-time">{time_str}</div>
</div>""", unsafe_allow_html=True)


def handle_send(msg_text: str):
    """发送消息，立即返回。后续轮询由前端 JS 完成。"""
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
    # key 随 input_key 变化，从而实现发送后清空
    user_input = st.text_area(
        "消息输入",
        placeholder="输入消息... (点击「发送」按钮)",
        height=100,
        key=f"user_input_{st.session_state.input_key}",
        label_visibility="collapsed"
    )

    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([2, 1, 1, 1])
    with btn_col1:
        send_btn = st.button(
            "📨 发送", use_container_width=True, type="primary",
            disabled=not st.session_state.connected or not (user_input or "").strip()
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
                "status","error","info","warning"],
        default=["tool_call","tool_result","turn_start","turn_done",
                 "error","permission_req","warning"],
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

    # ── 接收来自前端 JS 轮询组件的回调消息 ─────────────────────────────────
    # JS 组件通过 sendPrompt() 发回格式为 "__agent_poll__:{json}" 的消息
    # 这里在每次 rerun 时处理它
    _process_js_poll_callback()

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

    # 权限审批
    render_permission_panel()

    # 主布局：对话区 + 事件流
    if st.session_state.show_events:
        chat_col, event_col = st.columns([3, 2])
    else:
        chat_col  = st.container()
        event_col = None

    with chat_col:
        # ── 对话消息区 ──
        chat_container = st.container(height=480)
        render_chat_messages(chat_container)

        # 每次有消息时都滚动到底（rerun 后页面重建，这里是正确的触发点）
        # scroll_trigger > 0 表示刚发过消息，始终滚到底；
        # 平时也对有消息的情况触发，确保刷新后不会跳回顶部
        if st.session_state.messages:
            scroll_chat_to_bottom()

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
                st.session_state.messages = []
                for m in hist["messages"]:
                    role = m.get("role","")
                    c    = m.get("content","")
                    text = ("".join(x.get("text","") for x in c
                                    if isinstance(x,dict) and x.get("type")=="text")
                            if isinstance(c, list) else str(c))
                    if role in ("user","assistant") and text.strip():
                        st.session_state.messages.append({"role":role,"content":text,"time":""})
                st.session_state.scroll_trigger += 1
                st.success(f"已同步 {len(st.session_state.messages)} 条历史")
                st.rerun()

        # 拉取事件
        if events_btn:
            evts = get_client().events(since_id=st.session_state.last_event_id, limit=100)
            if evts and evts.get("events"):
                new = evts["events"]
                st.session_state.event_log.extend(new)
                st.session_state.last_event_id = max(e.get("id",0) for e in new)
                st.success(f"拉取到 {len(new)} 条新事件")
                st.rerun()

        # Turn 历史
        if turns_btn:
            render_turns_panel()

    # 事件流面板
    if event_col is not None:
        with event_col:
            render_event_panel()

    # 文件系统
    render_fs_panel()

    # 底部状态栏
    render_footer()

    # ── 前端 JS 实时轮询组件（权限审批面板）────────────────────────────────
    # 仅在已连接时注入。JS 每秒轮询后端事件，直接渲染权限审批按钮（不依赖 Streamlit 重渲染）。
    if st.session_state.connected:
        _render_agent_poll_widget()

    # ── Python 端定时 rerun：驱动 _process_js_poll_callback 同步事件到消息列表 ──
    # 当 agent 处于活跃状态时，每 1.5 秒 rerun 一次，确保对话消息区实时更新。
    if st.session_state.connected and st.session_state.agent_state in ("running", "waiting_permission"):
        time.sleep(1.5)
        st.rerun()


if __name__ == "__main__" or True:
    main()