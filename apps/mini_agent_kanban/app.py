"""
Mini-Agent 看板 (Kanban Dashboard)
==================================
基于 Streamlit 的一体化观测/交互面板：
  - 顶部：全局状态条（运行状态、自主等级、待审批数）
  - Tab1 💬 对话：聊天 + 事件流 + 权限审批
  - Tab2 🗂️ 会话管理：会话列表 / 新建 / 恢复 / 删除
  - Tab3 📌 看板：Goal / Objective / Cron Job 看板列
  - Tab4 📁 产出物：.agent/ 目录文件浏览与预览下载
  - Tab4.5 🖼️ 产出预览：按任务/session 登记的产出物 manifest 语义化展示
                  （图片内联预览、文档下载，支持 ?manifest_id=/?session_id= 深链接）
  - Tab5 🧠 自我状态：具身智能自省信息、SessionPool 概况
  - Tab6 🔧 诊断：/diagnostics 原始信息，方便调试

运行方式：
    streamlit run apps/mini_agent_kanban/app.py
"""
import html
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from client import AgentClient


def _esc_html(text) -> str:
    """转义 HTML 特殊字符后再插入 unsafe_allow_html 的 div。

    聊天历史里的 content 经常带 <tool_result>...</tool_result>、代码块里的
    <xxx> 等尖括号文本，如果不转义直接塞进 unsafe_allow_html，会被浏览器
    当成真实 HTML 标签解析，轻则样式错乱，重则把后面本该正常显示的消息
    "吃掉"（未闭合/嵌套标签打乱了后续 DOM 结构）。这里统一转义，并把换行
    还原成 <br>，保持原有的换行显示效果。
    """
    return html.escape(str(text)).replace("\n", "<br>")



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
.msg-tool {
    background: #14202b; border-left: 3px solid #3aa0d1; border-radius: 0 12px 12px 12px;
    padding: 8px 12px; margin: 4px 30px 4px 0; font-size: 12.5px; color: #bfe3f5;
    font-family: "SF Mono", Consolas, monospace;
}
.msg-tool-error {
    background: #2b1414; border-left: 3px solid #e05555; border-radius: 0 12px 12px 12px;
    padding: 8px 12px; margin: 4px 30px 4px 0; font-size: 12.5px; color: #f5c2c2;
    font-family: "SF Mono", Consolas, monospace;
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
def _parse_cli_args():
    """
    解析启动命令行参数（streamlit 需要把参数放在 `--` 之后转发给脚本）：
        streamlit run apps/mini_agent_kanban/app.py -- --auto-token --project-root "E:\\codes\\mini_claude_code"
    用 parse_known_args 忽略 streamlit 自身可能残留的未知参数，避免直接
    `python app.py` 或不同 streamlit 版本转发方式不一致时报错退出。
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--auto-token", action="store_true", dest="auto_token",
                         help="启动时自动从项目 .agent 目录读取 token，不用手动粘贴")
    parser.add_argument("--project-root", dest="project_root", default="",
                         help="项目根目录（包含 .agent/ 子目录），配合 --auto-token 使用；不传则用当前工作目录")
    parser.add_argument("--require-login", action="store_true", dest="require_login",
                         help="开启看板登录门禁：必须先用账户密码登录才能看到看板内容")
    parser.add_argument("--users-file", dest="users_file", default="",
                         help="账户文件路径，配合 --require-login 使用；不传则用 "
                              "<项目根目录>/.agent/kanban_users.json（相对路径按项目根目录解析）")
    args, _unknown = parser.parse_known_args(sys.argv[1:])
    return args


def init_state():
    cli_args = _parse_cli_args()
    defaults = {
        "api_base": "http://127.0.0.1:8765/v1",
        "token": "",
        "project_root": cli_args.project_root,
        "auto_token": cli_args.auto_token,
        "messages": [],
        "last_event_id": 0,
        "event_log": [],
        "autorefresh_tick": 0,
        "fs_path": ".",
        "artifacts_session_filter": "",
        "artifacts_open_id": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def apply_deep_link_query_params():
    """支持通过 URL 深链接直接定位到某次产出/某个 session 的产出列表：
        ?manifest_id=xxx            打开该 manifest 详情
        ?session_id=xxx             产出预览 tab 按该 session 过滤
        ?session_id=xxx&tab=artifacts   同上，并提示应打开产出预览 tab
    只在参数存在时写入 session_state，交由渲染函数消费；不清空未出现的参数，
    避免用户手动改 URL 时把其它 state 冲掉。"""
    qp = st.query_params
    manifest_id = qp.get("manifest_id")
    session_id = qp.get("session_id")
    if manifest_id:
        st.session_state.artifacts_open_id = manifest_id
    if session_id:
        st.session_state.artifacts_session_filter = session_id


def get_client() -> AgentClient:
    return AgentClient(st.session_state.api_base, st.session_state.token)


def _read_token_from_project(project_root: str) -> tuple:
    """
    按 mini-agent 自身的约定去项目目录里找 token 明文文件，返回
    (token_or_None, 命中的文件路径_or_None, 已尝试的路径列表)。
    查找顺序（与 cli/daemon.py::DaemonClient 保持一致，外加 owner.key 兜底）：
        1. <project_root>/.agent/agent_api.key         （单用户模式主 token）
        2. <project_root>/.agent/users/tokens/owner.key （多用户模式 owner token）
    project_root 为空时用当前工作目录。会去掉传参时可能带的首尾引号
    （比如从资源管理器地址栏复制路径时经常带双引号）。
    """
    raw = (project_root or "").strip().strip('"').strip("'")
    root = Path(raw).expanduser().resolve() if raw else Path.cwd().resolve()
    candidates = [
        root / ".agent" / "agent_api.key",
        root / ".agent" / "users" / "tokens" / "owner.key",
    ]
    tried = [str(p) for p in candidates]
    for p in candidates:
        try:
            if p.is_file():
                text = p.read_text(encoding="utf-8").strip()
                if text:
                    return text, str(p), tried
        except Exception:
            continue
    return None, None, tried


# ═══════════════════════════════════════════════════════════════════════
# 登录门禁（可选，--require-login 开启）
# ═══════════════════════════════════════════════════════════════════════
def _auth_paths(cli_args) -> tuple:
    """解析账户文件 / 签名密钥文件 / 登录失败限流记录文件的实际路径，都挂在
    项目根目录的 .agent/ 下，和 token 自动读取共用同一个"项目根目录"概念，
    避免用户要理解好几套路径规则。"""
    root = Path(cli_args.project_root).expanduser().resolve() if cli_args.project_root.strip() else Path.cwd().resolve()
    if cli_args.users_file.strip():
        users_file = Path(cli_args.users_file).expanduser()
        if not users_file.is_absolute():
            users_file = root / users_file
    else:
        users_file = root / ".agent" / "kanban_users.json"
    secret_file = root / ".agent" / "kanban_session_secret"
    attempts_file = root / ".agent" / "kanban_login_attempts.json"
    return users_file, secret_file, attempts_file


def _client_id() -> str:
    """尽量拿到一个能代表"客户端"的标识用于登录限流分桶，拿不到就返回空串
    （退化成纯按用户名限流，仍然有效，只是没法把"同一 IP 换用户名猛试"
    和"同一用户名被不同人试"区分开）。取 X-Forwarded-For 是因为看板通常
    跑在反向代理后面；没有代理时这个头不存在，直接拿不到也没关系。"""
    try:
        headers = st.context.headers  # Streamlit >= 1.37
        fwd = headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return headers.get("Host", "") or ""
    except Exception:
        return ""


def render_login_gate(cli_args) -> bool:
    """登录门禁。返回 True 表示已通过验证，调用方可以继续往下渲染看板；
    返回 False 表示这里已经把登录表单/提示画完了，调用方应直接 return，
    不能再渲染任何看板内容（否则未登录也能看到数据）。"""
    from auth import UserStore, LoginAttemptTracker, make_token, verify_token, get_or_create_secret

    users_file, secret_file, attempts_file = _auth_paths(cli_args)
    store = UserStore(users_file)
    tracker = LoginAttemptTracker(attempts_file)

    if st.session_state.get("authenticated"):
        return True

    secret = get_or_create_secret(secret_file)

    # 优先尝试用 URL 里的免登录 token 自动恢复登录态，这样刷新页面 /
    # 重新打开浏览器标签不会强制要求重新输入密码（12 小时内有效）。
    qp_token = st.query_params.get("auth")
    if qp_token:
        username = verify_token(qp_token, secret)
        if username:
            st.session_state.authenticated = True
            st.session_state.username = username
            return True

    st.markdown("## 🔐 mini-agent 看板登录")

    if store.is_empty():
        st.warning(
            "尚未创建任何账户，看板无法登录。请先在服务器上执行：\n\n"
            f"```\npython apps/mini_agent_kanban/manage_users.py add <用户名> "
            f"--users-file \"{users_file}\"\n```"
        )
        return False

    with st.form("login_form"):
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", use_container_width=True)

    if submitted:
        client_id = _client_id()
        remaining = tracker.seconds_until_unlocked(username, client_id)
        if remaining > 0:
            mins = int(remaining // 60) + 1
            st.error(f"🔒 该账户登录失败次数过多，已被临时锁定，请约 {mins} 分钟后再试。")
        elif store.verify(username, password):
            tracker.record_success(username, client_id)
            st.session_state.authenticated = True
            st.session_state.username = username
            st.query_params["auth"] = make_token(username, secret)
            st.rerun()
        else:
            tracker.record_failure(username, client_id)
            left = max(tracker.max_attempts - _current_fail_count(tracker, username, client_id), 0)
            st.error(f"用户名或密码错误。（连续失败达到上限会临时锁定该账户，剩余尝试次数：{left}）")

    return False


def _current_fail_count(tracker, username: str, client_id: str) -> int:
    """仅用于登录页给用户展示"还剩几次尝试机会"的提示信息，读一次记录文件。"""
    data = tracker._load()  # noqa: SLF001 — 同模块内部工具函数，未对外暴露正式 API
    entry = data.get(tracker._key(username, client_id))
    return entry.get("count", 0) if entry else 0


# ═══════════════════════════════════════════════════════════════════════
# 侧栏：连接配置
# ═══════════════════════════════════════════════════════════════════════
def render_sidebar():
    st.sidebar.markdown("### ⚙️ 连接配置")
    st.session_state.api_base = st.sidebar.text_input("API Base URL", st.session_state.api_base)

    if st.session_state.auto_token:
        token, hit_path, tried = _read_token_from_project(st.session_state.project_root)
        if token:
            st.session_state.token = token
            st.sidebar.caption(f"✅ 已从 `{hit_path}` 自动读取 token（--auto-token）")
        else:
            st.sidebar.warning(
                "❌ 未找到 token 文件（--auto-token 已开启），尝试过：\n"
                + "\n".join(f"- `{p}`" for p in tried)
            )
        # 只读展示，避免自动模式下用户误改却看起来像手动生效；
        # 手动改用命令行去掉 --auto-token 重启即可切回手动输入。
        st.sidebar.text_input("Token（--auto-token 自动读取，只读）", st.session_state.token,
                                type="password", disabled=True)
    else:
        st.session_state.token = st.sidebar.text_input(
            "Token", st.session_state.token, type="password")

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

# 产出物相关工具名——命中时说明这次 tool_result 很可能新增/更新了产出物，
# 用来在流式过程中提前给一条"有新产出物"的提示，不必等整轮结束、页面
# rerun 之后才第一次看到（详见 _stream_turn_into_placeholder 的说明）。
_ARTIFACT_HINT_TOOLS = ("record_artifact", "artifact")


def _stream_turn_into_placeholder(client: AgentClient, turn_id: str, container, last_rendered_user_msg: str = ""):
    """
    订阅 turn_id 的 SSE 流，把这一轮里的内容实时渲染进 `container`。

    之前的实现只用了单个 placeholder，把一整轮里所有的文本 token /
    工具调用 / 工具结果全部拼接进同一个 `<div>`，导致流式过程中看起来
    像"一个大对话框"，和整轮结束后 rerun 拉取正式历史（每条工具事件、
    每段文本都是独立展示）的效果完全不一致。

    这里改成：每当内容类型发生切换（文本 → 工具调用 → 工具结果 → 文本…），
    就在 container 里新开一个 st.empty() 占位符，各自独立更新，从而做到
    "每个独立内容一个单独的框"，且和刷新后的最终效果保持一致。

    返回 True 表示这一轮正常结束（外层可以安全 rerun 去刷新完整历史）。
    """
    cur_kind = None      # 当前正在写入的块类型："text" | None
    cur_ph = None        # 当前块对应的占位符
    cur_text = ""         # 当前文本块已累积的内容（含未处理的原始文本）
    finished = False
    saw_artifact_hint = False

    def _new_block():
        nonlocal cur_ph, cur_text
        cur_ph = container.empty()
        cur_text = ""
        return cur_ph

    def _visible_text(raw: str) -> str:
        # [SYS-STREAM-TOOLUSE] use_system_tool_call 模式下，模型是把工具调用
        # 写成 <tool_use>{...}</tool_use> 这样的纯文本、混在正常回复 token 里
        # 一起流出来的——真正的"这是一次工具调用"事件要等这段文本生成完、
        # 框架解析完才会触发。之前流式阶段是把这段原始 JSON 原样显示出来，
        # 跟轮次结束后（已经被解析剥离、只剩 🔧 调用工具 卡片）的排版完全
        # 不一样。这里检测到 <tool_use 标记就不再往后展示原始 JSON，
        # 用一个"正在调用工具…"占位代替，和最终效果保持一致。
        idx = raw.find("<tool_use")
        if idx == -1:
            return raw
        return raw[:idx].rstrip() + "\n\n🔧 正在调用工具…"

    for evt in client.stream_turn(turn_id, replay=True):
        etype = evt.get("event")
        data = evt.get("data") or {}

        if etype == "turn_start":
            # [SYS-TURN-START-BUBBLE] 这一轮的用户输入——不管是本浏览器刚发的
            # 第二轮消息，还是别的客户端发的——立刻显示出来，不用等这一轮结束
            # 后 rerun 刷新正式历史才第一次看到。
            #
            # 但如果调用方发现"正式历史"（entries）本身已经包含这条消息了
            # （常见于：chat() 提交后立即 rerun，这次 rerun 时后台已经把用户
            # 消息写进 agent.history，entries 循环已经画过一条一样的气泡），
            # 这里就跳过，否则会变成同一句话显示两条一模一样的气泡。
            msg = data.get("message", "")
            cur_kind = None
            if msg and msg != last_rendered_user_msg:
                ph = _new_block()
                ph.markdown(f'<div class="msg-user">{_esc_html(msg)}</div>', unsafe_allow_html=True)
                cur_ph = None  # 独立气泡，不再被后续文本复用

        elif etype == "agent_prefix":
            # 发言角色切换（比如主 Agent → GoalJudge）：新角色的话另起
            # 一个框，避免和上一位发言者的文本挤在同一个 div 里。
            name = data.get("agent_name") or ""
            cur_kind = None
            if name:
                ph = _new_block()
                ph.markdown(
                    f'<div style="margin:6px 0 0;font-size:11px;color:#888;">▸ {_esc_html(name)}</div>',
                    unsafe_allow_html=True,
                )
                cur_ph = None  # 这条本身就是独立小标签，不再被后续文本复用

        elif etype == "token":
            if cur_kind != "text":
                cur_kind = "text"
                _new_block()
            cur_text += data.get("text", "")
            cur_ph.markdown(f'<div class="msg-agent">{_esc_html(_visible_text(cur_text))}▌</div>', unsafe_allow_html=True)

        elif etype == "reasoning":
            # 思维链 token：淡化展示，不计入最终正文
            pass

        elif etype == "tool_call":
            cur_kind = None
            ph = _new_block()
            tool_name = _esc_html(data.get("tool_name", "?"))
            tool_input = _esc_html(str(data.get("tool_input", ""))[:300])
            ph.markdown(
                f'<div class="msg-tool">🔧 调用工具 <b>{tool_name}</b> · 参数: {tool_input}</div>',
                unsafe_allow_html=True,
            )

        elif etype == "tool_result":
            cur_kind = None
            ph = _new_block()
            tool_name_raw = data.get("tool_name", "?")
            tool_name = _esc_html(tool_name_raw)
            result = _esc_html(str(data.get("result", ""))[:300])
            ph.markdown(
                f'<div class="msg-tool">✅ 工具结果 <b>{tool_name}</b> · {result}</div>',
                unsafe_allow_html=True,
            )
            if any(h in tool_name_raw for h in _ARTIFACT_HINT_TOOLS):
                saw_artifact_hint = True

        elif etype == "tool_error":
            cur_kind = None
            ph = _new_block()
            tool_name = _esc_html(data.get("tool_name", "?"))
            err = _esc_html(str(data.get("error", data.get("message", "")))[:300])
            ph.markdown(
                f'<div class="msg-tool-error">❌ 工具出错 <b>{tool_name}</b> · {err}</div>',
                unsafe_allow_html=True,
            )

        elif etype in ("turn_done", "interrupt"):
            final_text = data.get("text", "") or cur_text
            if final_text:
                if cur_kind != "text":
                    _new_block()
                cur_ph.markdown(f'<div class="msg-agent">{_esc_html(final_text)}</div>', unsafe_allow_html=True)
            finished = True
            break

        elif etype == "error":
            ph = _new_block()
            ph.markdown(
                f'<div class="msg-agent">⚠️ {_esc_html(data.get("message", "发生错误"))}</div>',
                unsafe_allow_html=True,
            )
            finished = True
            break

        elif etype == "_error":
            # SSE 连接本身失败（网络/超时），退回轮询模式，让外层 rerun 兜底
            if cur_kind == "text" and cur_text and cur_ph is not None:
                cur_ph.markdown(f'<div class="msg-agent">{_esc_html(cur_text)}</div>', unsafe_allow_html=True)
            break

    if finished and saw_artifact_hint:
        # 提前给个轻量提示——正式的产出物卡片仍然在 rerun 后、走
        # render_chat_tab 顶部那段"产出物内联展示"逻辑统一渲染，这里只是
        # 让用户不必盯着空白等，知道"这一轮有新产出物，即将刷新显示"。
        container.markdown(
            '<div style="margin:4px 0;font-size:12px;color:#888;">📦 检测到新产出物，正在刷新…</div>',
            unsafe_allow_html=True,
        )

    return finished


def render_chat_tab(client: AgentClient):
    col_chat, col_events = st.columns([2, 1])

    with col_chat:
        st.markdown("#### 💬 对话")
        cur_status = client.status() or {}
        running_turn_id = cur_status.get("turn_id") if cur_status.get("state") == "running" else None
        if running_turn_id:
            st.caption("⏳ Agent 正在处理中…（下方将实时流式显示输出）")
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
        last_rendered_user_msg = ""
        with chat_box:
            if isinstance(entries, list):
                for e in entries[-60:]:
                    role = e.get("role", "")
                    etype = e.get("_type", "")  # 见 history/entry.py::HType
                    content = e.get("content", "")
                    if isinstance(content, list):
                        # content 可能是多模态 block 列表（tool_use/tool_result/text）
                        content = "\n".join(
                            b.get("text", str(b)) if isinstance(b, dict) else str(b)
                            for b in content
                        )
                    # [SYS-HIST-RENDER] role=="user" 不等于"这是真人打的字"——
                    # tool_result 回注、skill_context、hook_context、file_change、
                    # format_correction、session_resume、compressed 等系统消息也都
                    # 挂着 role="user"（只靠 _type 区分），之前不加区分地全部当
                    # 用户气泡展示，噪音淹没了真实输入，而且这些内容常带
                    # <tool_result> 这类尖括号文本，直接塞进 unsafe_allow_html
                    # 的 div 会被当成真 HTML 标签解析，破坏后面的 DOM 结构，
                    # 导致下一轮的真实用户输入在页面上"看不见"。
                    is_real_user_input = role in ("user", "human") and etype in ("", "user_input", "user_correction")
                    is_agent_reply = role in ("assistant", "agent") and etype in ("", "assistant_reply", "compact_summary")
                    if is_real_user_input:
                        st.markdown(f'<div class="msg-user">{_esc_html(content)}</div>', unsafe_allow_html=True)
                        last_rendered_user_msg = content if isinstance(content, str) else str(content)
                    elif is_agent_reply:
                        st.markdown(f'<div class="msg-agent">{_esc_html(content)}</div>', unsafe_allow_html=True)
                    elif role in ("user", "human", "assistant", "agent"):
                        # 系统内部消息（工具结果回注/skill注入/reminder 等），
                        # 折叠成一行淡色小字，不当聊天气泡展示，避免和真实
                        # 对话混淆，也避免其中的尖括号内容破坏页面结构。
                        label = etype or "system"
                        preview = _esc_html(content)[:80]
                        st.markdown(
                            f'<div style="font-size:11px;color:#666;margin:2px 0;">'
                            f'⚙️ [{label}] {preview}{"…" if len(str(content)) > 80 else ""}</div>',
                            unsafe_allow_html=True,
                        )

            # 产出物内联展示：把当前 session 已登记的产出物（图片/文档等）直接
            # 嵌在对话流里，不用切去"产出预览" Tab 来回找。按 created_at 倒序
            # （最新在前），本次渲染相比上次新出现的条目默认展开，其余折叠，
            # 避免每次刷新都是一整屏都展开的产出物淹没对话内容。
            cur_session_id = cur_status.get("session_id")
            if cur_session_id:
                art_resp = client.list_artifacts(session_id=cur_session_id, limit=20) or {}
                art_items = art_resp.get("items", []) if "_error" not in art_resp else []
                if art_items:
                    seen_key = f"artifacts_seen_count_{cur_session_id}"
                    prev_seen = st.session_state.get(seen_key, 0)
                    new_n = max(len(art_items) - prev_seen, 0)
                    st.markdown(
                        f'<div style="margin:8px 0 4px;font-size:13px;color:#888;">'
                        f'📦 本次会话产出物（{len(art_items)} 项{"，" + str(new_n) + " 项为新增" if new_n else ""}）</div>',
                        unsafe_allow_html=True,
                    )
                    for i, item in enumerate(art_items):
                        mid = item.get("manifest_id")
                        title = item.get("title", "未命名产出")
                        types_str = " ".join(ARTIFACT_TYPE_ICON.get(t, "📦") for t in item.get("types", []))
                        header = (f"{types_str} {title} · {item.get('created_at', '')[:19]} · "
                                  f"{item.get('file_count', 0)} 个文件")
                        with st.expander(header, expanded=(i < new_n)):
                            detail = client.get_artifact(mid, session_id=cur_session_id) or {}
                            if "_error" in detail:
                                st.error(detail["_error"])
                                continue
                            if detail.get("description"):
                                st.markdown(f"> {detail['description']}")
                            for idx, f in enumerate(detail.get("files", [])):
                                _render_artifact_file(client, mid, cur_session_id, idx, f)
                                st.divider()
                    st.session_state[seen_key] = len(art_items)

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

        new_turn_id = None
        if send and msg.strip():
            res = client.chat(msg.strip())
            if res and "_error" in res:
                st.error(res["_error"])
            else:
                new_turn_id = res.get("turn_id")
            st.rerun()  # 立即刷新一次，先把用户刚发的消息显示出来
        if interrupt:
            client.interrupt()
            st.rerun()

        # 实时流式输出：优先处理"刚发出的这一轮"，否则接管"页面加载/刷新时
        # 发现仍在跑的那一轮"（比如另一个客户端发的消息，或本页刷新过）。
        turn_to_stream = new_turn_id or running_turn_id
        if turn_to_stream:
            with chat_box:
                finished = _stream_turn_into_placeholder(client, turn_to_stream, chat_box, last_rendered_user_msg)
            if finished:
                st.rerun()  # 该轮结束，刷新一次把正式历史（含工具事件）拉齐

        if st.button("🗑️ 清空历史"):
            client.clear_history()
            st.rerun()

        # 工具活动/权限审批放在对话内容之外、页面最下方，不打断消息阅读体验
        has_pending_perm = any(e.get("type") == "permission_req" for e in tool_events)
        if tool_events:
            with st.expander("🔧 最近工具活动", expanded=has_pending_perm):
                for e in tool_events[-30:]:
                    etype = e.get("type")
                    if etype == "permission_req":
                        req_id = e.get("req_id")
                        st.markdown(f"""
<div class="permission-card">
  <b>🔐 权限请求：{_esc_html(e.get('tool_name','未知工具'))}</b><br/>
  <code style="font-size:11px;">{_esc_html(str(e.get('tool_input',''))[:300])}</code>
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
# Tab 4.5: 产出预览（语义化产出物看板，区别于上面按目录浏览的 Tab4）
# ═══════════════════════════════════════════════════════════════════════
ARTIFACT_TYPE_ICON = {
    "image": "🖼️", "document": "📄", "pdf": "📕",
    "code": "💻", "text": "📝", "other": "📦",
}


def _render_artifact_file(client: AgentClient, manifest_id: str, session_id: str, idx: int, f: dict):
    ftype = f.get("type", "other")
    title = f.get("title") or f.get("path", "").split("/")[-1]
    icon = ARTIFACT_TYPE_ICON.get(ftype, "📦")
    file_url = client.artifact_file_url(manifest_id, index=idx, session_id=session_id)
    download_url = client.artifact_file_url(manifest_id, index=idx, session_id=session_id, download=True)

    st.markdown(f"**{icon} {title}**  ·  `{f.get('path','')}`")
    if ftype == "image":
        st.image(file_url, caption=title)
    elif ftype == "pdf":
        st.markdown(f"[🔎 在新标签页打开预览]({file_url})  ·  [⬇️ 下载]({download_url})")
    elif ftype in ("code", "text"):
        data = client.fs_read(f.get("path", "")) or {}
        content = data.get("content")
        if content is not None:
            lang = "python" if f.get("path", "").endswith(".py") else None
            st.code(content[:5000], language=lang)
        st.markdown(f"[⬇️ 下载]({download_url})")
    else:
        st.markdown(f"文档类产出暂不支持内联预览，请下载查看：[⬇️ 下载]({download_url})")
    st.caption(f"大小: {f.get('size', '?')} bytes")


def render_artifacts_preview_tab(client: AgentClient):
    st.markdown("#### 🖼️ 产出预览")
    st.caption("按任务/会话登记的产出物（文档、图片等命令行不便展示的内容）。"
               "可通过 `?manifest_id=xxx` 或 `?session_id=xxx` 深链接直达。")

    c1, c2 = st.columns([3, 1])
    session_filter = c1.text_input("按 Session ID 过滤（留空=全部）", st.session_state.artifacts_session_filter)
    st.session_state.artifacts_session_filter = session_filter
    if c2.button("🔄 刷新列表"):
        st.rerun()

    resp = client.list_artifacts(session_id=session_filter or None, limit=100)
    if not resp or "_error" in (resp or {}):
        st.warning((resp or {}).get("_error", "暂无产出物数据"))
        return

    items = resp.get("items", [])
    if not items:
        st.info("暂无产出物记录。任务完成后可通过 record_artifact() 登记产出。")
        return

    # 若 URL 深链接指定了 manifest_id，优先展开该项
    open_id = st.session_state.artifacts_open_id

    for item in items:
        mid = item.get("manifest_id")
        sid = item.get("session_id")
        title = item.get("title", "未命名产出")
        types_str = " ".join(ARTIFACT_TYPE_ICON.get(t, "📦") for t in item.get("types", []))
        header = f"{types_str} {title} · session={sid} · {item.get('created_at','')[:19]} · {item.get('file_count',0)} 个文件"
        expanded = (mid == open_id)
        with st.expander(header, expanded=expanded):
            share_link = f"?manifest_id={mid}"
            st.caption(f"🔗 分享链接参数: `{share_link}`（拼到看板 URL 后即可直达）")
            detail = client.get_artifact(mid, session_id=sid) or {}
            if "_error" in detail:
                st.error(detail["_error"])
                continue
            if detail.get("description"):
                st.markdown(f"> {detail['description']}")
            for idx, f in enumerate(detail.get("files", [])):
                _render_artifact_file(client, mid, sid, idx, f)
                st.divider()


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
    cli_args = _parse_cli_args()
    inject_css()
    init_state()
    apply_deep_link_query_params()

    if cli_args.require_login:
        if not render_login_gate(cli_args):
            return  # 登录表单已经渲染完，未通过验证前不能往下渲染任何看板内容
        with st.sidebar:
            st.caption(f"👤 已登录：{st.session_state.get('username', '')}")
            if st.button("🚪 退出登录"):
                st.session_state.authenticated = False
                st.session_state.pop("username", None)
                if "auth" in st.query_params:
                    del st.query_params["auth"]
                st.rerun()
            st.divider()

    client = render_sidebar()

    if not client.health():
        st.info("请先在左侧确认 API Base URL / Token，并确保 mini-agent daemon 已启动。")
        return

    render_topbar(client)

    tabs = st.tabs(["💬 对话", "🗂️ 会话管理", "📌 目标看板", "📁 产出物", "🖼️ 产出预览", "🧠 自我状态", "🔧 诊断"])
    with tabs[0]:
        render_chat_tab(client)
    with tabs[1]:
        render_sessions_tab(client)
    with tabs[2]:
        render_kanban_tab(client)
    with tabs[3]:
        render_artifacts_tab(client)
    with tabs[4]:
        render_artifacts_preview_tab(client)
    with tabs[5]:
        render_self_tab(client)
    with tabs[6]:
        render_diagnostics_tab(client)

    if st.session_state.get("auto_refresh"):
        time.sleep(3)
        st.rerun()


if __name__ == "__main__":
    main()
