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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import streamlit as st
import streamlit.components.v1 as components

from client import AgentClient
from diff_view import parse_unified_diff, summarize_files


def _esc_html(text) -> str:
    """转义 HTML 特殊字符后再插入 unsafe_allow_html 的 div。

    聊天历史里的 content 经常带 <tool_result>...</tool_result>、代码块里的
    <xxx> 等尖括号文本，如果不转义直接塞进 unsafe_allow_html，会被浏览器
    当成真实 HTML 标签解析，轻则样式错乱，重则把后面本该正常显示的消息
    "吃掉"（未闭合/嵌套标签打乱了后续 DOM 结构）。这里统一转义，并把换行
    还原成 <br>，保持原有的换行显示效果。
    """
    return html.escape(str(text)).replace("\n", "<br>")


# 折叠阈值：内容原文超过这个字符数就用 <details> 折叠，而不是硬截断丢内容。
_COLLAPSE_THRESHOLD = 200


def _collapsible_html(inline_html: str, full_content: str, threshold: int = _COLLAPSE_THRESHOLD) -> str:
    """
    构造一段"内容短就直接显示、内容长就折叠、点击展开"的 HTML 片段。

    之前工具调用参数/结果/系统消息摘要都是硬编码 [:80]/[:300] 直接截断，
    超出部分永久丢失，只能去别的 Tab 里翻原始事件才能看全。这里改成用
    原生 <details>/<summary>——浏览器自带展开/收起交互，不需要额外 JS，
    也不占用 Streamlit 的 rerun 周期（st.expander 每次展开/收起都会触发
    一次 rerun，在这种一行套一行的密集列表里体验很差）。

    inline_html: 已经组装好、可以直接展示的"标题行" HTML（自己负责转义）。
    full_content: 未转义的原始完整内容，函数内部负责转义和折叠判断。
    """
    if len(full_content) <= threshold:
        return f'{inline_html}{_esc_html(full_content)}'
    return (
        f'<details class="tool-collapsible"><summary>{inline_html}'
        f'{_esc_html(full_content[:threshold])}… '
        f'<span style="color:#888;font-size:11px;">(点击展开，共 {len(full_content)} 字符)</span>'
        f'</summary><div class="tool-full-body">{_esc_html(full_content)}</div></details>'
    )



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
    if etype == "interaction_req":
        kind = e.get("kind", "?")
        return f"💬 请求交互 `{kind}` · {str(e.get('summary', e.get('question', e.get('prompt_text',''))))[:200]}"
    if etype == "interaction_done":
        return f"✅ 已回答 req={e.get('req_id','')} · 来源={e.get('reason','')}"
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

# agent 更细粒度的"正在干什么"（见 bridge.py::AgentBridge._phase / 后端
# /status 的 activity 字段）。顶栏（render_topbar）和"对话"tab 顶部的
# 会话信息条（_render_chat_session_info）共用同一份映射，避免出现两处
# 文案不一致。
_ACTIVITY_LABELS = {
    "waiting_input": "💤 空闲",
    "waiting_permission": "🛑 等权限确认",
    "calling_model": "🧠 调用模型中",
    "calling_tool": "🔧 调用工具中",
}

GOAL_STATUS_COLUMNS = [
    ("active", "🔵 进行中"),
    ("paused", "⏸️ 暂停"),
    ("completed", "✅ 已完成"),
    ("failed", "✗ 执行失败"),
    ("cancelled", "🚫 已终止"),
    ("abandoned", "🗑️ 已放弃"),
]

# workflow机制改进计划（P7）二、2.2：StepStatus 归并为 5 栏展示。
# 每项：(展示列标题, 归入这一列的 StepResult.status 取值集合)
WORKFLOW_STEP_COLUMNS = [
    ("⚪ 未开始", ("pending",)),
    ("🟠 进行中", ("running",)),
    ("✅ 已完成", ("done", "skipped")),
    # needs_fix：workflow_mechanism_improvement_proposal.md §4.3 新增状态，
    # 表示"结构性/配置错误，重试无效"，与瞬时失败（failed/gate_failed）
    # 一起归入"需要关注"列，卡片上会额外提示"这是定义问题，请先修改"。
    ("🔴 需要关注", ("gate_failed", "failed", "needs_fix")),
    ("🟣 等待审批", ("awaiting_approval",)),
]

WORKFLOW_RUN_STATUS_LABELS = {
    "running": "🟠 运行中",
    "paused": "⏸️ 已暂停",
    "awaiting_approval": "🟣 等待审批",
    "done": "✅ 已完成",
    "failed": "❌ 失败",
    "partial": "🟡 部分完成",
    "cancelled": "🗑️ 已取消",
}


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

.chat-session-info {
    display:flex; gap:16px; flex-wrap:wrap; align-items:center;
    background:#181a24; border:1px solid #2a2d3a; border-radius:8px;
    padding:6px 12px; margin-bottom:6px; font-size:12.5px;
}
.chat-session-info .item {display:flex; align-items:center; gap:5px; color:#ddd;}
.chat-session-info .label {color:#888; font-size:11px; text-transform:uppercase; letter-spacing:.5px;}

.kanban-col {
    background:#14161f; border:1px solid #262838; border-radius:10px;
    padding:8px 10px; min-height:40px;
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
.tool-collapsible summary {
    cursor: pointer; list-style: none; user-select: none;
}
.tool-collapsible summary::-webkit-details-marker { display: none; }
.tool-collapsible summary::before {
    content: "▸ "; color: #888; font-size: 11px;
}
.tool-collapsible[open] summary::before {
    content: "▾ "; color: #888; font-size: 11px;
}
.tool-collapsible .tool-full-body {
    white-space: pre-wrap; word-break: break-word; margin-top: 6px;
    max-height: 320px; overflow-y: auto;
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
        # workflow机制改进计划（P7）二、Streamlit "🔄 工作流" Tab
        "wf_active_run_id": None,
        "wf_history_open_id": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def apply_deep_link_query_params():
    """支持通过 URL 深链接直接定位到某次产出/某个 session 的产出列表：
        ?manifest_id=xxx            打开该 manifest 详情
        ?session_id=xxx             产出预览 tab 按该 session 过滤
        ?session_id=xxx&tab=artifacts   同上，并提示应打开产出预览 tab

    ?session_id= 现在身兼两职（有意如此，不是冲突）：
      1) 沿用原语义，预填"产出预览"tab 的 session 过滤框；
      2) 新增语义（见 get_active_session_id()）——决定"这个浏览器标签页
         绑定到哪个 session 对话"。两者语义天然一致（都是"这个 URL 指向
         哪个 session"），同一个 query param 打开时两处效果一起生效，
         分享一条深链接既能定位产出，也能直接把对方带到同一个对话里，
         不用额外发第二个参数。
    只在参数存在时写入 session_state，交由渲染函数消费；不清空未出现的参数，
    避免用户手动改 URL 时把其它 state 冲掉。"""
    qp = st.query_params
    manifest_id = qp.get("manifest_id")
    session_id = qp.get("session_id")
    if manifest_id:
        st.session_state.artifacts_open_id = manifest_id
    if session_id:
        st.session_state.artifacts_session_filter = session_id


def update_query_params(**kv) -> None:
    """[P1 改造] 统一的 URL query params 写入入口——以后任何地方要改
    query_params，只准调这个函数，不要在别处直接 `st.query_params[...] = x`。

    背景（踩过的坑）：之前"绑定会话"按钮在 `st.query_params[...] = x`
    之后又手动调了一次 `st.rerun()`，而 Streamlit（>=1.30）修改
    query_params 本身就会自动触发一次重跑，两次重跑在同一次交互里抢跑，
    表现为地址栏 URL 瞬间跳到新值又立刻被回滚成旧值。规则很简单：
    **写 query_params 之后不要再手动 st.rerun()**，这个函数把这条规则
    锁死在一个地方，不用每个调用点都记着别犯这个错。

    传值为空字符串/None/False 表示删除该 key（用于"解绑"这类场景）。
    """
    for k, v in kv.items():
        if v:
            st.query_params[k] = v
        elif k in st.query_params:
            del st.query_params[k]


def get_active_session_id() -> str:
    """当前看板页面（浏览器标签页）绑定的 session_id。

    有意不放进 st.session_state：st.session_state 是"这次浏览器连接"的
    状态，而 URL query params 才是真正每个标签页/每次打开各自独立的东西
    ——"开多个看板页面各自对应不同 session、互不干扰"这个需求，必须靠
    URL 里的 session_id 来实现，放 session_state 起不到隔离作用。
    返回空字符串表示"未绑定，使用后端全局默认 session"，向后兼容旧行为
    （旧版本看板 / 没显式选过 session 的用户完全无感知）。
    """
    return st.query_params.get("session_id", "") or ""


def set_active_session_id(session_id: str) -> None:
    """把这个标签页绑定到指定 session（写入 URL，不写 session_state，理由同上）。
    传空字符串 / None 表示解绑，退回全局默认 session。"""
    update_query_params(session_id=session_id)


def get_pinned_session_ids() -> list[str]:
    """[P1 新增] "同页多会话并排查看"用到的固定会话列表，同样放 URL
    （`?pinned=sid1,sid2`），理由和 session_id 一致：要"这个标签页
    并排看哪几个 session"能通过分享链接复现、且多个标签页互不干扰。
    """
    raw = st.query_params.get("pinned", "") or ""
    return [s for s in raw.split(",") if s]


def toggle_pinned_session(session_id: str) -> None:
    """把某个 session 加入/移出"并排对比"列表（已在列表里则移除，否则加入）。"""
    pinned = get_pinned_session_ids()
    if session_id in pinned:
        pinned = [s for s in pinned if s != session_id]
    else:
        pinned = pinned + [session_id]
    update_query_params(pinned=",".join(pinned) if pinned else None)



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
            # [P1 一致性修复] 同"绑定会话"按钮踩过的坑：query_params 写入
            # 后不再手动 st.rerun()，交给它自带的自动重跑生效，避免两次
            # 重跑竞态导致 URL 里的 auth token 闪现又消失。
            update_query_params(auth=make_token(username, secret))
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
        "⏱️ 自动刷新（状态条/事件流每 2~3 秒）", value=st.session_state.get("auto_refresh", True),
        help="控制顶部状态条和事件流两块的局部自动刷新（P0 改造后已不再阻塞整页，"
             "关闭时这两块只在你手动点刷新/切换 tab 时更新）。",
    )

    st.sidebar.caption("提示：daemon 模式启动后默认监听 http://127.0.0.1:8765/v1")

    # ── 本页面绑定的 session（多会话并行的基础）──────────────────────────
    # 选一个 session 绑定到"这个浏览器标签页"（写进 URL，见
    # get_active_session_id()）。同一个 daemon 下，用不同 session_id 打开
    # 多个看板页面 / 标签页，即可同时对话多个不重叠的 session；这里的
    # 下拉框和 URL 是双向的：改 URL 也会反映到这个下拉框上。
    st.sidebar.markdown("### 🗂️ 本页面对话 session")
    sessions_data = client.sessions(limit=50) or {}
    sess_options = ["(全局默认)"]
    if "_error" not in sessions_data:
        sess_options += [s.get("id", "") for s in sessions_data.get("sessions", []) if s.get("id")]
    cur_sid = get_active_session_id()
    idx = sess_options.index(cur_sid) if cur_sid in sess_options else 0
    choice = st.sidebar.selectbox(
        "绑定到 session", sess_options, index=idx, key="session_switcher_select",
        help="决定这个标签页跟哪个 session 对话。不同标签页可以各选不同的 "
             "session，实现同时进行多个互不干扰的对话；选「(全局默认)」保持旧行为。",
    )
    if choice != (cur_sid or "(全局默认)"):
        set_active_session_id("" if choice == "(全局默认)" else choice)
        st.rerun()
    if cur_sid:
        st.sidebar.caption(f"🔗 分享此对话：把当前浏览器地址栏的链接（含 `session_id={cur_sid}`）发给别人")

    return client


# ═══════════════════════════════════════════════════════════════════════
# 顶部状态条
# ═══════════════════════════════════════════════════════════════════════
def render_topbar(client: AgentClient, session_id: str = ""):
    """[P0 改造] 原来这块状态条完全靠 main() 末尾的
    `if auto_refresh: time.sleep(3); st.rerun()` 来刷新——那是"整页阻塞
    3 秒再重跑一次"，意味着这 3 秒里全页面（包括其它 tab、正在填的表单）
    都被冻结，而且不管用户当前在不在看这个状态条，都要陪着一起等。
    改成 st.fragment(run_every=...)：只有这个函数自己按周期重跑，
    页面其它部分（对话输入框、其它 tab 里的表单）不受影响，且状态数字
    的刷新周期跟"整页刷新周期"解耦，可以刷得更快而不拖慢全页。"""
    if not st.session_state.get("auto_refresh", True):
        _render_topbar_body(client, session_id)
        return
    _render_topbar_fragment(client, session_id)


@st.fragment(run_every="3s")
def _render_topbar_fragment(client: AgentClient, session_id: str = ""):
    _render_topbar_body(client, session_id)


_INITIATOR_LABEL = {
    "user": "🙋 用户",
    "cron": "⏰ Cron",
    "scheduled": "⏰ Cron",
    "autonomous": "🤖 自主任务",
}


def _render_queue_panel(client: AgentClient, session_id: str = "") -> None:
    """[看板新增] 展示 InputQueue 里排队等待处理的请求（`/v1/turns` 里
    state=="queued" 的条目）——用户消息、cron 触发、自主任务提交的都走
    同一条 InputQueue，agent 正忙（running）时后面的请求只能排队，之前
    看板完全没有地方能看到"现在积压了几个、都是什么"。
    """
    turns_data = client.turns(session_id=session_id) or {}
    if "_error" in turns_data:
        st.warning(f"排队信息获取失败：{turns_data['_error']}")
        return
    all_turns = turns_data.get("turns", [])
    queued = [t for t in all_turns if t.get("state") == "queued"]
    if not queued:
        st.caption("当前没有排队中的请求")
        return
    now = time.time()
    # list_turns() 内部按插入顺序（dict 保序）、routes.py 又整体 reversed()
    # 返回，这里重新按 started_at 升序排，第一条就是"下一个会被处理的"。
    queued.sort(key=lambda t: t.get("started_at") or 0)
    for i, t in enumerate(queued):
        waited = now - (t.get("started_at") or now)
        initiator_label = _INITIATOR_LABEL.get(t.get("initiator", "user"), t.get("initiator", "—"))
        input_preview = (t.get("input") or "")[:120]
        st.caption(
            f"#{i + 1}　{initiator_label}　·　已等待 {waited:.0f}s　·　"
            f"`{t.get('turn_id', '')[:8]}`"
        )
        st.text(input_preview + ("…" if len(t.get("input") or "") > 120 else ""))


def _render_daemon_current_tasks(client: AgentClient, autostat: dict) -> None:
    """[看板新增] 顶栏只展示当前 session 的 activity（calling_tool/calling_model
    等），完全反映不出 daemon 后台（AutonomousLoop）此刻实际在跑哪个 Objective、
    进行到第几步，也看不出有没有 workflow 正在后台运行——这些都是跟"当前
    session"无关、但同样是"daemon 正在干什么"的一部分。这里额外聚合展示：
      1. 正在执行中的 Objective（来自 /autonomous/status 的 objective_executions，
         status == running），显示标题 + 当前步骤 + 进度；
      2. 正在运行中的 workflow（来自 /workflow_runs，status == running）。

    [孤儿运行修复] /workflow_runs 里 status=="running" 只代表"上次落盘时是
    这个状态"——如果 daemon 在那之后崩溃/重启过，没有任何东西会把它改回
    终态，会一直显示"运行中"，但实际上早就没有线程在处理了。后端现在会
    额外算出 is_stale 字段（进行中状态 + 进程内 registry 找不到活跃控制 =
    孤儿记录），这里按 is_stale 拆成两组：真正在跑的正常展示；疑似孤儿的
    单独标出并给一个"标记为已中断"的清理入口，点掉之后就不会再被算作
    "正在运行"。
    """
    running_objs = [
        ex for ex in (autostat.get("objective_executions") or [])
        if ex.get("status") == "running"
    ]

    running_wfs = []
    stale_wfs = []
    wf_runs = client.workflow_runs() or {}
    if "_error" not in wf_runs:
        for r in (wf_runs.get("runs") or []):
            if r.get("status") != "running":
                continue
            (stale_wfs if r.get("is_stale") else running_wfs).append(r)

    if not running_objs and not running_wfs and not stale_wfs:
        return

    lines = []
    for ex in running_objs:
        title = ex.get("title") or ex.get("objective_id", "")
        step = ex.get("current_step") or ""
        progress = ex.get("progress") or ""
        detail = f"　·　{step}" if step else ""
        lines.append(f"🎯 **{title}**　({progress}){detail}")
    for r in running_wfs:
        wf_name = r.get("workflow_name") or r.get("name") or ""
        rid = r.get("workflow_session_id", "")
        label = r.get("summary_line") or ""
        detail = f"　·　{label}" if label else ""
        lines.append(f"🔄 **{wf_name}**　`{rid}`{detail}")

    n = len(running_objs) + len(running_wfs)
    if n:
        with st.expander(f"⚙️ daemon 正在执行 {n} 项任务（点击查看）", expanded=True):
            for line in lines:
                st.markdown(line)

    if stale_wfs:
        with st.expander(
            f"⚠️ 发现 {len(stale_wfs)} 条疑似失效的 workflow 运行记录（不计入上方"
            "\"正在执行\"，daemon 重启/崩溃后遗留，实际早已不再运行）",
            expanded=False,
        ):
            for r in stale_wfs:
                rid = r.get("workflow_session_id", "")
                wf_name = r.get("workflow_name") or r.get("name") or ""
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"🔄 **{wf_name}**　`{rid}`　{r.get('summary_line', '')}")
                if c2.button("🧹 标记为已中断", key=f"wf_mark_interrupted_{rid}"):
                    res = client.mark_workflow_run_interrupted(rid)
                    if res and "_error" in res:
                        st.error(res["_error"])
                    else:
                        st.success("已标记为已中断（cancelled）。")
                        st.rerun()


def _render_topbar_body(client: AgentClient, session_id: str = ""):
    status = client.status(session_id=session_id) or {}
    if "_error" in status:
        st.warning(f"状态获取失败：{status['_error']}")
        return

    state = status.get("state", "unknown")
    icon, label = STATE_LABELS.get(state, STATE_LABELS["unknown"])
    autonomy = status.get("autonomy_level", "passive")
    tick_count = status.get("tick_count", 0)
    subscribers = status.get("subscribers", 0)

    pending = client.pending_permissions(session_id=session_id) or {}
    pending_list = pending.get("permissions", [])
    pending_n = len(pending_list) if isinstance(pending_list, list) else 0

    # [BUGFIX] /goal 协商、ask_user 系列工具走的是"通用交互"网关
    # （interaction_gate），跟"工具调用权限审批"（permission_gate）是
    # 两套完全独立的机制。之前这里只查询/展示了 pending_permissions，
    # 导致 /goal 命令生成验收标准草案后，看板完全看不到"请确认检查
    # 标准"这个提示，也没有任何地方可以回复 /confirm、/cancel 或修改
    # 意见——命令行那边能看到是因为它走了 term.interruptible_prompt()
    # 本地这一路，但看板这边从来没对接过。
    pending_ix = client.pending_interactions(session_id=session_id) or {}
    pending_ix_list = pending_ix.get("interactions", [])
    pending_ix_n = len(pending_ix_list) if isinstance(pending_ix_list, list) else 0

    autostat = client.autonomous_status() or {}
    next_tick = autostat.get("next_tick_in")
    next_tick_str = f"{next_tick:.0f}s" if isinstance(next_tick, (int, float)) else "—"

    # 更细粒度的"agent 正在干什么"（见 bridge.py::AgentBridge._phase / 后端
    # /status 的 activity 字段），这份映射本身在模块级定义（_ACTIVITY_LABELS），
    # 顶栏和"对话"tab 里的会话信息条都复用同一份，避免出现两份不一致的文案。
    activity = status.get("activity")
    activity_label = _ACTIVITY_LABELS.get(activity, activity or "—")
    if activity == "calling_tool" and status.get("activity_detail"):
        activity_label = f"🔧 {status['activity_detail']}"
    model_label = status.get("model") or "—"
    sid_label = status.get("session_id") or "—"
    # [看板新增] queue_depth 后端 StatusResponse 里一直有这个字段
    # （bridge.py::InputQueue.depth），但之前看板从没读过——用户发消息 /
    # cron 触发 / 自主任务提交时，如果 agent 正忙（running），这些请求会
    # 排在 InputQueue 里等 AgentRunner 依次 dequeue，之前完全看不出来
    # "现在还有几个请求在排队等着"。
    queue_depth = status.get("queue_depth", 0)

    st.markdown(f"""
<div class="topbar">
  <div class="item"><span class="label">状态</span> {icon} {label}</div>
  <div class="item"><span class="label">动作</span> {activity_label}</div>
  <div class="item"><span class="label">模型</span> {model_label}</div>
  <div class="item"><span class="label">Session</span> {sid_label}</div>
  <div class="item"><span class="label">Turn</span> {status.get('turn_id') or '—'}</div>
  <div class="item"><span class="label">自主等级</span> {autonomy}</div>
  <div class="item"><span class="label">距下次Tick</span> {next_tick_str}</div>
  <div class="item"><span class="label">Tick计数</span> {tick_count}</div>
  <div class="item"><span class="label">订阅者</span> {subscribers}</div>
  <div class="item"><span class="label">排队中</span> {'🟡 ' + str(queue_depth) if queue_depth else '0'}</div>
  <div class="item"><span class="label">待审批</span> {'🔴 ' + str(pending_n) if pending_n else '0'}</div>
  <div class="item"><span class="label">待回答</span> {'🔴 ' + str(pending_ix_n) if pending_ix_n else '0'}</div>
</div>
""", unsafe_allow_html=True)
    if status.get("session_dir"):
        st.caption(f"📁 session 目录: `{status['session_dir']}`")

    _render_daemon_current_tasks(client, autostat)

    if queue_depth:
        with st.expander(f"🕓 有 {queue_depth} 个请求在排队等待处理", expanded=False):
            _render_queue_panel(client, session_id)

    if pending_n:
        with st.expander(f"⚠️ 有 {pending_n} 个待审批权限请求，点击处理", expanded=True):
            render_permissions(client, pending_list)

    if pending_ix_n:
        with st.expander(f"💬 有 {pending_ix_n} 个待回答的交互请求，点击处理", expanded=True):
            render_interactions(client, pending_ix_list)

    _render_global_inbox(client, session_id)


def _render_global_inbox(client: AgentClient, current_session_id: str = "") -> None:
    """[看板与自主性改进方案 Track A] 全局待办通知中心：跨所有 session 聚合
    pending 权限/交互请求 + 执行失败的 Objective，解决"后台自主任务卡在
    权限审批上，但用户停留在别的 tab/session 完全看不到"的问题（P1）。

    与上面 pending_n/pending_ix_n（只查当前 session）不同，这里调用
    /v1/inbox，跨 SessionAgentPool 里所有活跃 session 扫描——即使用户停留
    在"目标看板" tab、当前 session 里根本没有待办，只要*任意*一个 session
    有待办，这里也能看到。
    """
    inbox = client.inbox() or {}
    if "_error" in inbox:
        # 静默失败：inbox 是增强能力，不应影响其它顶栏内容的展示
        return
    items = inbox.get("items", [])
    if not items:
        return

    n = len(items)
    with st.expander(f"📥 全局待办中心：共有 {n} 条跨会话待办（点击展开）", expanded=False):
        icon_by_type = {
            "permission": "🛑",
            "interaction": "💬",
            "objective_failed": "✗",
        }
        for it in items:
            icon = icon_by_type.get(it.get("type"), "•")
            sid = it.get("session_id")
            same_session = sid and sid == current_session_id
            cols = st.columns([5, 1]) if sid else [st.container()]
            with cols[0]:
                where = f"（会话 `{sid}`）" if sid else "（Objective 执行）"
                st.caption(f"{icon} {it.get('summary', '')} {where}")
            if sid and not same_session:
                with cols[1]:
                    if st.button("跳转", key=f"inbox_jump_{it.get('type')}_{it.get('req_id') or it.get('execution_id')}"):
                        # 遵守"query_params 写入后不手动 rerun"的规范：
                        # update_query_params 内部已经会触发一次重跑。
                        update_query_params(session_id=sid)


_INTERACTION_KIND_LABEL = {
    "ask_user":         "🙋 Agent 提问",
    "ask_user_confirm": "🙋 Agent 请求确认",
    "ask_user_choice":  "🙋 Agent 请求选择",
    "goal_negotiation": "🎯 目标验收标准确认",
    "repl_prompt":      "⌨️ 命令行内部请求输入",
}


@dataclass
class PendingInteraction:
    """[P1 重构] 统一的"待回答交互请求"数据模型。

    之前 render_interactions 是直接在渲染循环里按 req["kind"] 一条条
    if/elif 判断、每个分支自己从 req["data"] 里挖字段、自己拼 HTML——
    渲染逻辑和"这个 kind 该怎么取数据"混在一起，以后每新增一种 kind
    就要在渲染函数里再插一个分支，函数只会越写越长。

    现在拆成两步：
      1. _build_pending_interaction()：只负责"从原始 req 里取出数据"，
         归一成这个 dataclass（body 展示什么、要哪种输入控件 mode、
         choices 模式下的选项列表）。新增一种 kind 只需要在这一个
         函数里加一段"怎么从 data 里取值"，不用碰渲染代码。
      2. _render_pending_interaction()：只负责"按 mode 渲染控件"，
         渲染逻辑与"kind 具体是什么"完全解耦——mode 的取值远少于
         kind（未来新增的 kind 大概率能复用已有的四种 mode 之一）。
    """
    req_id: str
    kind: str
    label: str
    body: str
    mode: str                          # "confirm_freeform" | "yes_no" | "choices" | "freeform"
    options: list = field(default_factory=list)


def _build_pending_interaction(req: dict) -> PendingInteraction:
    """把后端返回的原始交互请求，归一成 PendingInteraction。"""
    req_id = req.get("req_id")
    kind = req.get("kind", "")
    data = req.get("data", {}) or {}
    label = _INTERACTION_KIND_LABEL.get(kind, f"🙋 {kind or '未知交互'}")

    if kind == "goal_negotiation":
        # data["summary"] 就是 GoalSpec.render_summary_for_user() 的原文——
        # 里面包含目标文本、验收标准列表、验证方式等，与命令行里
        # R.console.print(spec.render_summary_for_user()) 展示的完全一致。
        return PendingInteraction(
            req_id, kind, label, body=str(data.get("summary", "")), mode="confirm_freeform",
        )

    if kind == "ask_user_confirm":
        return PendingInteraction(
            req_id, kind, label,
            body=str(data.get("question", data.get("prompt_text", ""))),
            mode="yes_no",
        )

    if kind == "ask_user_choice":
        return PendingInteraction(
            req_id, kind, label,
            body=str(data.get("question", "")),
            mode="choices",
            options=list(data.get("options") or data.get("choices") or []),
        )

    # ask_user / repl_prompt / 其它未预见的 kind：统一退化成
    # "展示提示文字 + 一个自由文本框"，保证至少能回答，不会因为遇到
    # 没特殊适配的 kind 就彻底没法操作。
    return PendingInteraction(
        req_id, kind, label,
        body=str(data.get("question", data.get("prompt_text", data.get("hint", "")))),
        mode="freeform",
    )


def _render_pending_interaction(client: AgentClient, item: PendingInteraction) -> None:
    """按 mode 渲染交互控件——只关心 mode，不关心具体 kind 是什么。"""
    req_id = item.req_id
    st.markdown(f"**{item.label}**")

    if item.body:
        st.markdown(
            f'<div class="permission-card"><pre style="white-space:pre-wrap;'
            f'font-size:12px;margin:0;">{_esc_html(item.body)}</pre></div>'
            if item.mode == "confirm_freeform" else
            f'<div class="permission-card">{_esc_html(item.body)}</div>',
            unsafe_allow_html=True,
        )

    if item.mode == "confirm_freeform":
        gc1, gc2 = st.columns(2)
        if gc1.button("✅ /confirm 确认并开始执行", key=f"ix_confirm_{req_id}"):
            client.respond_interaction(req_id, answer="/confirm")
            st.rerun()
        if gc2.button("❌ /cancel 放弃本次目标", key=f"ix_cancel_{req_id}"):
            client.respond_interaction(req_id, answer="/cancel")
            st.rerun()
        with st.form(f"ix_form_{req_id}", clear_on_submit=True):
            revise_text = st.text_input(
                "或输入修改意见（会据此重新生成下一版验收标准草案）",
                key=f"ix_input_{req_id}",
            )
            if st.form_submit_button("提交修改意见"):
                if revise_text.strip():
                    client.respond_interaction(req_id, answer=revise_text.strip())
                    st.rerun()

    elif item.mode == "yes_no":
        cc1, cc2 = st.columns(2)
        if cc1.button("✅ 是", key=f"ix_yes_{req_id}"):
            client.respond_interaction(req_id, confirmed=True)
            st.rerun()
        if cc2.button("❌ 否", key=f"ix_no_{req_id}"):
            client.respond_interaction(req_id, confirmed=False)
            st.rerun()

    elif item.mode == "choices":
        for idx, opt in enumerate(item.options):
            if st.button(f"{idx + 1}. {opt}", key=f"ix_opt_{req_id}_{idx}"):
                client.respond_interaction(req_id, choice_index=idx)
                st.rerun()

    else:  # freeform
        with st.form(f"ix_form_{req_id}", clear_on_submit=True):
            free_text = st.text_input("回复", key=f"ix_free_{req_id}")
            if st.form_submit_button("发送"):
                client.respond_interaction(req_id, answer=free_text)
                st.rerun()

    st.divider()


def render_interactions(client: AgentClient, pending_list):
    """渲染并处理通用交互式请求（ask_user 系列工具 / /goal 协商 / 任意 slash
    命令内部的 prompt_user()）。数据组装（_build_pending_interaction）和
    渲染（_render_pending_interaction）已分离，见 PendingInteraction 的说明。
    """
    for req in pending_list:
        item = _build_pending_interaction(req)
        _render_pending_interaction(client, item)


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

    [BUGFIX] 新增"因等待权限审批而暂停"的信号：函数内部遇到 permission_req
    时会 break 出循环（见下方 permission_req 分支的详细说明），此时通过
    nonlocal `_paused_for_permission`（调用方用 `_last_stream_paused_holder`
    读取）告知外层"不是真的结束，但应该立即 rerun 一次"，让顶部状态条 /
    底部审批面板尽快出现并变得可点击，而不是傻等最长 3 秒的自动刷新。
    """
    cur_kind = None      # 当前正在写入的块类型："text" | None
    cur_ph = None        # 当前块对应的占位符
    cur_text = ""         # 当前文本块已累积的内容（含未处理的原始文本）
    finished = False
    paused_for_permission = False
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
            tool_input_raw = str(data.get("tool_input", ""))
            body = _collapsible_html(f'🔧 调用工具 <b>{tool_name}</b> · 参数: ', tool_input_raw)
            ph.markdown(f'<div class="msg-tool">{body}</div>', unsafe_allow_html=True)

        elif etype == "tool_result":
            cur_kind = None
            ph = _new_block()
            tool_name_raw = data.get("tool_name", "?")
            tool_name = _esc_html(tool_name_raw)
            result_raw = str(data.get("result", ""))
            body = _collapsible_html(f'✅ 工具结果 <b>{tool_name}</b> · ', result_raw)
            ph.markdown(f'<div class="msg-tool">{body}</div>', unsafe_allow_html=True)
            if any(h in tool_name_raw for h in _ARTIFACT_HINT_TOOLS):
                saw_artifact_hint = True

        elif etype == "tool_error":
            cur_kind = None
            ph = _new_block()
            tool_name = _esc_html(data.get("tool_name", "?"))
            err_raw = str(data.get("error", data.get("message", "")))
            body = _collapsible_html(f'❌ 工具出错 <b>{tool_name}</b> · ', err_raw)
            ph.markdown(f'<div class="msg-tool-error">{body}</div>', unsafe_allow_html=True)

        elif etype == "permission_req":
            # [BUGFIX] 之前这里完全没有处理 permission_req：Streamlit 单线程
            # 执行模型下，这个 for 循环正阻塞在 client.stream_turn() 的同步
            # 网络读取上，只有等这一轮真正结束（turn_done/error）才会 return，
            # 脚本才能重新跑一遍、让按钮点击生效。
            # 但工具调用需要审批时，agent 那一轮会一直"卡"在
            # PermissionGuard.check() 里等审批结果——不会产生 turn_done。
            # 于是就出现了自死锁：要批准/拒绝就得点按钮 → 点按钮要等页面
            # 重新运行一遍 → 页面重新运行不了，因为脚本正卡在这个 for 循环里
            # 等这一轮完成 → 这一轮完成不了，因为在等审批。
            #
            # 修复：一看到 permission_req 就主动 break，把控制权交还给
            # Streamlit（不再傻等 turn_done）。这样本次脚本运行能正常跑完，
            # 顶部状态条 / 底部"最近工具活动"面板下一次刷新（自动刷新每 3
            # 秒，或用户任意点击都会触发 rerun）就能看到这个待审批请求并且
            # 按钮真正可点。turn 本身没有结束，之后（无论从哪个客户端批准）
            # 都会被这里通过 replay=True 重新订阅并继续渲染。
            cur_kind = None
            ph = _new_block()
            tool_name = _esc_html(data.get("tool_name", "?"))
            tool_input_raw = str(data.get("tool_input", ""))
            body = _collapsible_html(
                f'🔐 等待权限审批 <b>{tool_name}</b>（请到上方"待审批"或下方"最近工具活动"里处理）· 参数: ',
                tool_input_raw,
            )
            ph.markdown(f'<div class="permission-card">{body}</div>', unsafe_allow_html=True)
            paused_for_permission = True
            break

        elif etype == "interaction_req":
            # [BUGFIX] 与上面 permission_req 完全相同的死锁模式，触发场景
            # 不是"工具权限审批"而是"通用交互式提问"——最典型的就是
            # `/goal <目标文本>` 生成验收标准草案后进入的确认子对话
            # （goal_negotiation），以及 ask_user / ask_user_confirm /
            # ask_user_choice 三个工具。这一轮同样不会产生 turn_done，
            # 直到有人（CLI 或看板）回答为止，所以这里也必须主动 break，
            # 交出控制权，让顶部"待回答"面板（render_interactions）能在
            # 下一次运行里出现并且可以点/填。
            cur_kind = None
            ph = _new_block()
            kind = data.get("kind", "?")
            hint = str(data.get("summary", data.get("question", data.get("prompt_text", ""))))
            body = _collapsible_html(
                f'💬 等待交互回答 <b>{_esc_html(kind)}</b>（请到上方"待回答"里处理）· ',
                hint,
            )
            ph.markdown(f'<div class="permission-card">{body}</div>', unsafe_allow_html=True)
            paused_for_permission = True
            break

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

    return finished, paused_for_permission


def _render_chat_session_info(status: dict) -> None:
    """[UI 改进] "对话"tab 顶部的会话信息条。

    之前这里只留了一句注释"这些信息已经在顶栏常驻展示，不重复渲染"——
    但顶栏是"全局"的一条横幅，字段多、字号小，容易被忽略；用户明确
    希望在对话区域本身就能一眼看到"现在用的什么模型、agent 是空闲/
    调用工具/等模型结果、session 目录在哪"，不用去找页面最上面那条不
    起眼的状态条。这里复用调用方已经拿到的 status（不额外发请求），
    只挑用户关心的这几项，做成对话区域自己的小信息条。
    """
    if "_error" in status:
        st.warning(f"会话状态获取失败：{status['_error']}")
        return

    activity = status.get("activity")
    activity_label = _ACTIVITY_LABELS.get(activity, activity or "—")
    if activity == "calling_tool" and status.get("activity_detail"):
        activity_label = f"🔧 {status['activity_detail']}"
    model_label = status.get("model") or "—"
    sid_label = status.get("session_id") or "—"
    session_dir = status.get("session_dir") or "—"

    st.markdown(f"""
<div class="chat-session-info">
  <span class="item"><span class="label">模型</span> {_esc_html(model_label)}</span>
  <span class="item"><span class="label">状态</span> {activity_label}</span>
  <span class="item"><span class="label">Session</span> {_esc_html(sid_label)}</span>
</div>
""", unsafe_allow_html=True)
    st.caption(f"📁 {session_dir}")


def render_chat_tab(client: AgentClient, session_id: str = ""):
    col_chat, col_events = st.columns([2, 1])

    with col_chat:
        st.markdown("#### 💬 对话")
        cur_status = client.status(session_id=session_id) or {}
        running_turn_id = cur_status.get("turn_id") if cur_status.get("state") == "running" else None
        _render_chat_session_info(cur_status)

        if running_turn_id:
            st.caption("⏳ Agent 正在处理中…（下方将实时流式显示输出）")
        # 拉最近事件，把 tool_call / tool_result / tool_error / permission_req 也
        # 渲染进聊天流里，让用户能看到 Agent 实际调用了什么工具、结果是什么。
        ev_data = client.events(since_id=0, limit=100, session_id=session_id) or {}
        tool_events = [
            e for e in ev_data.get("events", [])
            if e.get("type") in ("tool_call", "tool_result", "tool_error",
                                  "permission_req", "permission_done")
        ]

        # [FIX] 对话消息列表（含产出物内联展示）之前是纯内联代码，不在任何
        # st.fragment 里，只能靠"自己发消息/清空历史/流式结束"这几个触发点
        # 手动 st.rerun() 刷新——別的客户端往同一个 session 发消息、或产出物
        # 新增，这边看不到，得点"手动刷新全部"（整页 rerun）才行。事件流因为
        # 有独立的 2s fragment 就没有这个问题。这里改成和事件流同款做法：
        # 抽成 _render_chat_messages，按 auto_refresh 开关决定是否用
        # @st.fragment(run_every="2s") 包裹，做局部自动刷新。
        chat_box = st.container(height=460, border=True)
        with chat_box:
            last_rendered_user_msg = _render_chat_messages(client, session_id)
        _inject_scroll_script()

        with st.form("chat_form", clear_on_submit=True):
            msg = st.text_area("输入消息", height=80, label_visibility="collapsed",
                                placeholder="和 Agent 说点什么…")
            c1, c2 = st.columns([1, 1])
            send = c1.form_submit_button("发送 ➤", use_container_width=True)
            interrupt = c2.form_submit_button("⏹ 中断当前任务", use_container_width=True)

        new_turn_id = None
        if send and msg.strip():
            res = client.chat(msg.strip(), session_id=session_id)
            if res and "_error" in res:
                st.error(res["_error"])
            else:
                new_turn_id = res.get("turn_id")
            st.rerun()  # 立即刷新一次，先把用户刚发的消息显示出来
        if interrupt:
            client.interrupt(session_id=session_id)
            st.rerun()

        # 实时流式输出：优先处理"刚发出的这一轮"，否则接管"页面加载/刷新时
        # 发现仍在跑的那一轮"（比如另一个客户端发的消息，或本页刷新过）。
        turn_to_stream = new_turn_id or running_turn_id
        if turn_to_stream:
            with chat_box:
                finished, paused_for_permission = _stream_turn_into_placeholder(
                    client, turn_to_stream, chat_box, last_rendered_user_msg
                )
            if finished or paused_for_permission:
                # [BUGFIX] 之前只有 finished 才 rerun；等待权限审批时也要立即
                # rerun 一次——不然要等最长 3 秒的自动刷新，页面在此期间
                # 看起来像是"卡住了，权限请求既看不到也点不了"。
                st.rerun()

        if st.button("🗑️ 清空历史"):
            client.clear_history(session_id=session_id)
            _reset_events_cache(session_id)
            st.session_state.pop(_chat_load_limit_key(session_id), None)
            st.rerun()

        # 工具活动/权限审批放在对话内容之外、页面最下方，不打断消息阅读体验
        has_pending_perm = any(e.get("type") == "permission_req" for e in tool_events)
        if tool_events:
            with st.expander("🔧 最近工具活动", expanded=has_pending_perm):
                for e in tool_events[-30:]:
                    etype = e.get("type")
                    if etype == "permission_req":
                        req_id = e.get("req_id")
                        _perm_input_body = _collapsible_html("", str(e.get('tool_input', '')))
                        st.markdown(f"""
<div class="permission-card">
  <b>🔐 权限请求：{_esc_html(e.get('tool_name','未知工具'))}</b><br/>
  <code style="font-size:11px;">{_perm_input_body}</code>
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
        _render_events_panel(client, session_id)


def _render_chat_messages(client: AgentClient, session_id: str = "") -> str:
    """[BUGFIX] 见 render_chat_tab 里的说明：对话消息列表原来不在任何
    st.fragment 里，靠不上局部自动刷新，只能等用户自己触发交互或点
    "手动刷新全部"。这里改成和事件流（_render_events_panel）一样的模式：
    用 auto_refresh 开关决定是否套 @st.fragment(run_every="2s")。

    返回 last_rendered_user_msg，供调用方在流式渲染时判断"这条用户消息
    是不是已经在正式历史里画过了"，避免重复展示（见 _stream_turn_into_placeholder）。
    """
    if st.session_state.get("auto_refresh", True):
        return _render_chat_messages_fragment(client, session_id)
    return _render_chat_messages_body(client, session_id)


@st.fragment(run_every="2s")
def _render_chat_messages_fragment(client: AgentClient, session_id: str = "") -> str:
    return _render_chat_messages_body(client, session_id)


def _chat_load_limit_key(session_id: str) -> str:
    return f"chat_load_limit_{session_id or 'default'}"


def _render_chat_messages_body(client: AgentClient, session_id: str = "") -> str:
    # [看板分页改进] 默认只拉最新一页（100 条），不再一次性拉取整个 session
    # 的全量历史再截断——长会话下能显著减少每次 2s 轮询的传输/反序列化
    # 开销。用户点"加载更早消息"时才把 limit 加大重新拉一次。
    load_limit = st.session_state.get(_chat_load_limit_key(session_id), 100)
    hist = client.history(session_id=session_id, limit=load_limit) or {}
    entries = hist.get("messages", [])
    has_more = hist.get("has_more", False)
    total = hist.get("total", 0)

    if has_more:
        def _load_more(_key=_chat_load_limit_key(session_id), _cur=load_limit):
            st.session_state[_key] = _cur + 100

        st.button(
            f"⬆️ 加载更早消息（已加载 {len(entries)} / {total} 条）",
            key=f"load_more_history_{session_id or 'default'}",
            on_click=_load_more,
            use_container_width=True,
        )

    last_rendered_user_msg = ""
    if isinstance(entries, list):
        for e in entries:
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
                # 用和流式阶段一样的 .msg-tool 卡片样式展示，长内容用
                # <details> 折叠、点击展开，不再硬截断丢内容。
                label = etype or "system"
                content_str = content if isinstance(content, str) else str(content)
                body = _collapsible_html(f'⚙️ [{label}] ', content_str)
                st.markdown(f'<div class="msg-tool">{body}</div>', unsafe_allow_html=True)

    # 产出物内联展示：把当前 session 已登记的产出物（图片/文档等）直接
    # 嵌在对话流里，不用切去"产出预览" Tab 来回找。按 created_at 倒序
    # （最新在前），本次渲染相比上次新出现的条目默认展开，其余折叠，
    # 避免每次刷新都是一整屏都展开的产出物淹没对话内容。
    cur_status = client.status(session_id=session_id) or {}
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
                    else:
                        if detail.get("description"):
                            st.markdown(f"> {detail['description']}")
                        for idx, f in enumerate(detail.get("files", [])):
                            _render_artifact_file(client, mid, cur_session_id, idx, f)
                            st.divider()
            st.session_state[seen_key] = len(art_items)

    # 滚动锚点：每次渲染后用下面注入的 JS 把它滚到可视区域，从而把整个
    # 固定高度容器滚到底部，实现"自动滚动到最新消息"。
    st.markdown('<div id="chat-bottom-anchor"></div>', unsafe_allow_html=True)
    return last_rendered_user_msg


def _render_events_panel(client: AgentClient, session_id: str = ""):
    """[P0 改造] 原来事件流的刷新完全依赖 main() 末尾的全局 3 秒阻塞轮询，
    切到别的 tab 也会跟着一起被 3 秒 sleep 拖住。拆成独立函数 + fragment
    局部刷新后，事件流可以用比全页更短、更贴近"实时"的周期刷新（这里用
    2 秒），且不影响用户在对话框里正在输入的内容（fragment 外的部分不
    会被这个刷新打断）。

    注意：这仍然是轮询，不是真推送——真正的推送（订阅 /v1/stream 的
    EventSource）留给 P2 阶段，见改进方案文档第 1.1 节。这一步的收益是
    "去掉整页阻塞 + 刷新周期从 3s 降到 2s 且可独立配置"，不是"消除轮询"。
    """
    if st.session_state.get("auto_refresh", True):
        _render_events_panel_fragment(client, session_id)
    else:
        _render_events_panel_body(client, session_id)


@st.fragment(run_every="2s")
def _render_events_panel_fragment(client: AgentClient, session_id: str = ""):
    _render_events_panel_body(client, session_id)


def _fetch_events_incremental(client: AgentClient, session_id: str, cache_cap: int = 300) -> list:
    """[看板分页改进] 用 since_id 做增量拉取，累加到 session_state 里的本地
    缓存，而不是像之前那样每次 fragment 触发（2-3 秒一次）都从
    since_id=0 重新拉一遍"最近 N 条"——量大时那样等于反复拉取、反复渲染
    同一批已经看过的事件。

    本地缓存超过 cache_cap 条时从头部裁掉，语义上仍然是"保留最近 N 条"，
    只是不用每次都把这 N 条从头传输一遍。
    """
    cache_key = f"events_cache_{session_id or 'default'}"
    last_id_key = f"events_last_id_{session_id or 'default'}"
    cache: list = st.session_state.get(cache_key, [])
    last_id: int = st.session_state.get(last_id_key, 0)

    ev = client.events(since_id=last_id, limit=2000, session_id=session_id) or {}
    if "_error" in ev:
        return cache
    new_events = ev.get("events", [])
    if new_events:
        cache = cache + new_events
        if len(cache) > cache_cap:
            cache = cache[-cache_cap:]
        st.session_state[cache_key] = cache
        st.session_state[last_id_key] = max(last_id, ev.get("max_id", last_id))
    return cache


def _reset_events_cache(session_id: str = "") -> None:
    """切换 session / 清空历史时调用，避免把上一个 session 的事件残留在
    下一个 session 的本地缓存里。"""
    st.session_state.pop(f"events_cache_{session_id or 'default'}", None)
    st.session_state.pop(f"events_last_id_{session_id or 'default'}", None)


def _render_events_panel_body(client: AgentClient, session_id: str = ""):
    events = _fetch_events_incremental(client, session_id)
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
def _render_sessions_change_banner(client: AgentClient) -> None:
    """[P2 新增] 跨标签页会话变化感知。

    背景：之前"某个标签页新建/删除了 session，其它已打开的看板标签页
    不会自动感知，得手动刷新"。真正的解法是后端在 new_session/delete_session
    时广播一个全局 SSE 事件，前端订阅——但那需要浏览器原生 EventSource
    接入 /v1/stream，这在 Streamlit 里要做一个真正的双向自定义组件
    （st.components.v1.declare_component + 独立打包的前端），涉及组件
    协议、构建流程，没有一个跑起来的 Streamlit 实例根本没法验证是否真的
    工作，风险和收益不成正比，所以这一步没有做（详见文档 P2 第 1 项的
    说明），留给"是否迁移前端框架"的决策一起评估。

    这里退而求其次，用 Streamlit 已有的能力解决"能感知"这个实际诉求：
    用一个低频 fragment（5s）轻量拉一次会话 id 列表，跟本标签页第一次
    看到的"基线"比较，变了就弹一条提示，由用户自己点"刷新"决定什么时候
    重新渲染整个列表——不做成"定时静默刷新整个列表"，是因为 st.expander
    的展开状态在 fragment 重跑时未必能保住，静默刷新会在用户正展开看
    某个 session 详情时把它收起来，体验比"手动刷新"更差。
    """
    if st.session_state.get("auto_refresh", True):
        _render_sessions_change_banner_fragment(client)
    else:
        _render_sessions_change_banner_body(client)


@st.fragment(run_every="5s")
def _render_sessions_change_banner_fragment(client: AgentClient) -> None:
    _render_sessions_change_banner_body(client)


def _render_sessions_change_banner_body(client: AgentClient) -> None:
    data = client.sessions(limit=50) or {}
    if "_error" in data:
        return
    current_ids = tuple(sorted(s.get("id", "") for s in data.get("sessions", [])))

    baseline = st.session_state.get("_sessions_baseline_ids")
    if baseline is None:
        # 本标签页第一次看到会话列表，记为基线，不弹提示。
        st.session_state["_sessions_baseline_ids"] = current_ids
        return

    if current_ids != baseline:
        bc1, bc2 = st.columns([5, 1])
        bc1.warning("🔔 检测到会话列表有变化（可能是其它标签页新建/删除了会话，或后台自动创建了新会话）")
        if bc2.button("🔄 刷新列表", key="sessions_refresh_banner", use_container_width=True):
            st.session_state["_sessions_baseline_ids"] = current_ids
            st.rerun()


def render_sessions_tab(client: AgentClient):
    st.markdown("#### 🗂️ 会话管理")
    _render_sessions_change_banner(client)

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("➕ 新建会话", use_container_width=True):
            res = client.new_session()
            if res and "_error" not in res:
                st.success("已创建新会话")
                new_sid = res.get("session_id")
                if new_sid:
                    # 顺手把本页面绑定到刚创建的新会话，免得用户创建后还要
                    # 再点一次"本页面绑定到此会话"。
                    # [BUGFIX] 这里不能再手动 st.rerun()：st.query_params
                    # 赋值本身就会自动触发一次重跑（Streamlit >=1.30 的
                    # 行为），紧接着再手动调一次 st.rerun() 等于同一次交互
                    # 触发两次重跑，两条 ForwardMsg 前后脚到达浏览器，表现
                    # 就是地址栏 URL 先跳到新值又立刻被回滚成重跑前的旧值。
                    set_active_session_id(new_sid)
                else:
                    st.rerun()
            else:
                st.error((res or {}).get("_error", "创建失败"))
                st.rerun()

    # [看板分页改进] session 数量特别多时（>50）之前只能看到最新的 50 个，
    # 更早的完全不可见。这里加标准的 offset 分页，页码存在 session_state
    # 里，翻页只重新拉当前这一页，不再一次性拉 200 条塞满页面。
    page_size = 50
    page = st.session_state.get("sessions_page", 0)
    data = client.sessions(limit=page_size, offset=page * page_size) or {}
    if "_error" in data:
        st.info(f"会话列表不可用：{data['_error']}（可能未开启 session 持久化）")
        return

    sessions = data.get("sessions", [])
    total = data.get("total", len(sessions))
    total_pages = max(1, (total + page_size - 1) // page_size)
    # 如果因为删除等操作导致当前页码超出范围（比如删完最后一页的会话），
    # 退回最后一页而不是直接显示"暂无会话记录"的空白页。
    if page > 0 and not sessions and page >= total_pages:
        st.session_state["sessions_page"] = max(0, total_pages - 1)
        st.rerun()
    if not sessions:
        st.info("暂无会话记录")
        return

    for s in sessions:
        sid = s.get("id", "")
        current_mark = " 🟢当前" if s.get("is_current") else ""
        bound_mark = " 📌本页面" if sid == get_active_session_id() else ""
        pinned_mark = " 📎已固定" if sid in get_pinned_session_ids() else ""
        with st.expander(f"🗂️ {sid}{current_mark}{bound_mark}{pinned_mark}　·　轮次 {s.get('turns', '?')}　·　{s.get('age', s.get('updated_at',''))}"):
            st.json(s, expanded=False)
            cc1, cc2, cc3, cc4 = st.columns(4)
            if cc1.button("▶️ 恢复此会话（全局）", key=f"resume_{sid}",
                           help="改变服务端全局默认 session，影响所有没有单独绑定 session 的客户端"):
                res = client.resume_session(sid)
                _reset_events_cache("")
                st.session_state.pop(_chat_load_limit_key(""), None)
                st.success("已切换") if res and "_error" not in res else st.error(res.get("_error", "失败"))
                st.rerun()
            if cc2.button("📌 本页面绑定到此会话", key=f"bind_{sid}",
                           help="只影响这个浏览器标签页（写入 URL），不影响其它标签页/客户端"):
                # [BUGFIX] 不要在 st.query_params 赋值后紧跟手动 st.rerun()。
                # query_params 赋值本身已经会自动触发重跑，额外再调一次会
                # 造成同一次交互里连续两次重跑的竞态：第二次重跑打断第一次
                # 重跑对地址栏的 URL 更新，表现为"URL 瞬间跳到新值又变回
                # 旧值"。去掉这行多余的 rerun 即可让 URL 正常停留在新值上。
                set_active_session_id(sid)
            if cc3.button("📎 加入/移出并排对比", key=f"pin_{sid}",
                           help="加入下方的并排对比区，可以同时看多个 session 的状态和最近事件，不用来回切换标签页"):
                toggle_pinned_session(sid)
            if cc4.button("🗑️ 删除", key=f"del_{sid}"):
                client.delete_session(sid)
                st.rerun()

    pc1, pc2, pc3 = st.columns([1, 2, 1])
    if pc1.button("⬅️ 上一页", disabled=(page <= 0), use_container_width=True):
        st.session_state["sessions_page"] = page - 1
        st.rerun()
    pc2.markdown(
        f"<div style='text-align:center'>第 {page + 1} / {total_pages} 页　"
        f"（共 {total} 个会话）</div>",
        unsafe_allow_html=True,
    )
    if pc3.button("下一页 ➡️", disabled=(page >= total_pages - 1), use_container_width=True):
        st.session_state["sessions_page"] = page + 1
        st.rerun()

    _render_pinned_sessions_panel_entry(client)


def _render_pinned_sessions_panel_entry(client: AgentClient) -> None:
    pinned = get_pinned_session_ids()
    if not pinned:
        return
    if st.session_state.get("auto_refresh", True):
        _render_pinned_sessions_panel_fragment(client)
    else:
        _render_pinned_sessions_panel(client)


@st.fragment(run_every="3s")
def _render_pinned_sessions_panel_fragment(client: AgentClient) -> None:
    _render_pinned_sessions_panel(client)


def _render_pinned_sessions_panel(client: AgentClient) -> None:
    """[P1 新增] "同页多会话并排查看"——之前只能靠"一个标签页绑定一个
    session、要同时盯 N 个 session 就开 N 个浏览器标签"来实现多会话查看，
    来回切换标签页体验不好。这里在会话列表下方加一个并排区域，把
    `?pinned=` 里固定的几个 session 的状态 + 最近事件在同一屏里各占一列
    展示，不用离开这个页面就能同时看好几个 session 在干什么。
    """
    pinned = get_pinned_session_ids()
    if not pinned:
        return
    st.markdown("---")
    st.markdown(f"#### 📎 并排对比（{len(pinned)} 个会话）")
    cols = st.columns(len(pinned))
    for col, sid in zip(cols, pinned):
        with col:
            st.markdown(f"**🗂️ `{sid}`**")
            if st.button("✖️ 取消固定", key=f"unpin_{sid}"):
                # 同样遵守"query_params 写入后不手动 rerun"的规范
                toggle_pinned_session(sid)
            status = client.status(session_id=sid) or {}
            if "_error" in status:
                st.warning(status["_error"])
                continue
            state = status.get("state", "unknown")
            icon, label = STATE_LABELS.get(state, STATE_LABELS["unknown"])
            st.caption(f"{icon} {label}　·　动作: {status.get('activity') or '—'}")
            events = _fetch_events_incremental(client, sid, cache_cap=100)
            box = st.container(height=260)
            with box:
                for e in events[-30:]:
                    ts = e.get("ts")
                    t_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""
                    text = _event_text(e)
                    if text:
                        st.caption(f"`{t_str}` {text}")


# ═══════════════════════════════════════════════════════════════════════
# Tab 3: 看板（Goals / Objectives / Cron）
# ═══════════════════════════════════════════════════════════════════════
_EXEC_STEP_ICONS = {"done": "✅", "running": "▶️", "failed": "✗", "pending": "・"}


def _render_objective_execution_detail(client: AgentClient, execution: dict) -> None:
    """[看板改进] 渲染单个 Objective 的真实执行计划/进度：ObjectiveExecutor
    拆出的每一步、每一步的状态、结果摘要。之前看板只显示 GoalBacklog 里
    手填的 progress_notes（跟真实执行进度是两套数据，容易脱节/看不出
    "到底做到哪一步了"），这里改成直接读 ObjectiveExecutor 的权威状态。

    [Track D 新增] 底部追加"终止 / 重试当前步 / 插话"三个操作按钮，只在
    对应状态下显示——不是任何时候都能操作（比如已完成的 Objective 没有
    "终止"的意义）。"""
    ex_status = execution.get("status", "unknown")
    exec_id = execution.get("execution_id", "")
    steps = execution.get("steps") or []
    done, total = 0, len(steps)
    for s in steps:
        if s.get("status") == "done":
            done += 1
    status_label = {
        "running": "🏃 执行中", "paused": "⏸️ 已暂停",
        "completed": "✅ 已完成", "failed": "✗ 执行失败", "pending": "⏳ 待启动",
        "cancelled": "🚫 已终止",
    }.get(ex_status, ex_status)
    lines = [f'<div class="meta">{status_label}　步骤 {done}/{total}</div>']
    if execution.get("progress_notes"):
        lines.append(f'<div class="meta" style="color:#c77;">{_esc_html(execution["progress_notes"])}</div>')
    for s in steps:
        icon = _EXEC_STEP_ICONS.get(s.get("status"), "・" if s.get("status") != "blocked" else "⏳")
        desc = _esc_html(str(s.get("description", ""))[:100])
        extra = ""
        if s.get("status") == "running":
            extra = " ← 当前步骤"
        elif s.get("status") == "blocked":
            extra = '　<span style="color:#b80;">与其他 Objective 路径冲突，排队中</span>'
        elif s.get("status") == "failed" and s.get("error_msg"):
            extra = f'　<span style="color:#c77;">{_esc_html(str(s["error_msg"])[:80])}</span>'
        lines.append(f'<div class="meta" style="padding-left:6px;">{icon} {desc}{extra}</div>')
    st.markdown(
        f'<div style="background:#f7f7f7;border-radius:6px;padding:6px 8px;margin:4px 0;">'
        + "".join(lines) + "</div>",
        unsafe_allow_html=True,
    )

    # [Track E 执行细节可钻取] 每个已经跑过（done/failed）的 step 提供一个
    # "查看详情"入口，展开后展示完整的 tool_call/tool_result 序列，而不
    # 只是卡片正文里那行截断到 100 字的描述/摘要——排查"这一步到底干了
    # 什么"时才需要展开，默认收起不占地方。
    for s in steps:
        if s.get("status") not in ("done", "failed"):
            continue
        step_idx = s.get("step_index")
        with st.expander(f"🔍 查看详情 · 步骤 {step_idx + 1 if isinstance(step_idx, int) else '?'}"):
            trace = client.objective_step_trace(exec_id, step_idx) if exec_id and isinstance(step_idx, int) else None
            if not trace or "_error" in (trace or {}):
                st.caption((trace or {}).get("_error", "暂时无法获取执行细节。"))
                continue
            if trace.get("from_raw_history"):
                st.caption("ℹ️ 该步骤记录已被压缩，以下内容从压缩前的原始日志里找回。")
            entries = trace.get("entries") or []
            if not entries:
                st.caption(trace.get("note") or "没有可展示的执行细节。")
                continue
            for entry in entries:
                etype = entry.get("type")
                if etype == "user_input":
                    st.markdown(f"**📝 提交内容**\n\n{entry.get('text', '')}")
                elif etype == "assistant_reply":
                    for part in entry.get("parts") or []:
                        if part.get("kind") == "text" and part.get("text"):
                            st.markdown(part["text"])
                        elif part.get("kind") == "tool_call":
                            st.markdown(f"**🔧 调用工具：`{part.get('tool_name', '')}`**")
                            st.json(part.get("tool_input") or {})
                elif etype == "tool_result":
                    with st.container():
                        st.markdown("**↩️ 工具结果**")
                        st.code(entry.get("text", ""), language=None)

    if not exec_id or ex_status in ("completed", "cancelled"):
        return
    b1, b2, b3 = st.columns(3)
    if ex_status in ("running", "paused", "failed", "pending"):
        if b1.button("🛑 终止", key=f"obj_cancel_{exec_id}"):
            res = client.cancel_objective(exec_id)
            if res and "_error" in res:
                st.error(res["_error"])
            st.rerun()
    if ex_status in ("running", "failed"):
        if b2.button("🔁 重试当前步", key=f"obj_retry_{exec_id}"):
            res = client.retry_objective(exec_id)
            if res and "_error" in res:
                st.error(res["_error"])
            st.rerun()
    with b3.popover("💬 插话"):
        guidance = st.text_area("补充说明（下次提交当前步骤时会附带这段话）",
                                 key=f"obj_guidance_text_{exec_id}", height=80)
        if st.button("发送", key=f"obj_guidance_send_{exec_id}"):
            if guidance.strip():
                res = client.inject_objective_guidance(exec_id, guidance.strip())
                if res and "_error" in res:
                    st.error(res["_error"])
                else:
                    st.success("已记录，将在下次提交该步骤时附带这段说明")


def _render_goal_card(
    client: AgentClient, n: dict, status_key: str, indent: bool = False, note: str = "",
    execution: Optional[dict] = None,
) -> None:
    """渲染单张 Goal/Objective 卡片 + 状态切换下拉框。
    从 render_kanban_tab 里抽出来，供"按层级嵌套展示"复用，行为
    （状态切换后调 client.update_goal 并 rerun）跟改造前完全一致，
    只是现在可能带缩进（Objective 挂在其 parent Goal 卡片下面时）。

    execution — 该 Objective 对应的 ObjectiveExecutor 执行记录（若有，
    来自 /v1/autonomous/status 里的 objective_executions，按 objective_id
    匹配），非 None 时额外渲染真实的分步计划/进度。"""
    level_tag = "🎯目标" if n.get("level") == "objective" else "🌱心愿"
    wrapper_style = "margin-left:18px;border-left:2px solid #ddd;padding-left:8px;" if indent else ""
    note_html = f'<div class="meta" style="color:#c77;">{_esc_html(note)}</div>' if note else ""

    # [P1 新增] 优先展示关联 WorkThread 的 cumulative_progress——这是
    # agent 实际推进过程中动态更新的记录，比需要手动回写的 progress_notes
    # 更贴近"现在做到哪一步了"。两者都没有时不展示这一行，避免卡片里
    # 出现"进展：（空）"这种没意义的占位。
    progress_text = n.get("work_thread_progress") or n.get("progress_notes") or ""
    progress_html = (
        f'<div class="meta">📈 进展：{_esc_html(progress_text)}</div>' if progress_text else ""
    )
    next_suggested = n.get("work_thread_next_suggested") or ""
    next_html = (
        f'<div class="meta" style="color:#888;">👉 建议下一步：{_esc_html(next_suggested)}</div>'
        if next_suggested else ""
    )

    st.markdown(f"""
<div class="kanban-card" style="{wrapper_style}">
  <div class="title">{level_tag} {_esc_html(n.get('title','(无标题)'))}</div>
  <div class="meta">来源:{n.get('source','')}　优先级:{n.get('priority',0)}</div>
  {progress_html}
  {next_html}
  {note_html}
</div>
""", unsafe_allow_html=True)
    if execution is not None:
        _render_objective_execution_detail(client, execution)
    new_status = st.selectbox(
        "状态", [s for s, _ in GOAL_STATUS_COLUMNS],
        index=[s for s, _ in GOAL_STATUS_COLUMNS].index(status_key),
        key=f"goalstatus_{n.get('id')}", label_visibility="collapsed",
    )
    if new_status != status_key:
        client.update_goal(n.get("id"), status=new_status)
        st.rerun()


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
        # 用于给"父 Goal 在别的状态列"的 Objective 显示父标题
        title_by_id = {n.get("id"): n.get("title", "") for n in all_nodes}

        # [看板改进] 按 objective_id 索引 ObjectiveExecutor 的真实执行记录，
        # 供下面每张 Objective 卡片渲染分步计划/进度（见
        # _render_objective_execution_detail）。同一个 objective_id 理论上
        # 同时只有一个 running/paused execution，取最新一条即可。
        autostat_for_cards = client.autonomous_status() or {}
        exec_by_objective_id: dict = {}
        for _ex in autostat_for_cards.get("objective_executions", []):
            _oid = _ex.get("objective_id")
            if not _oid:
                continue
            _prev = exec_by_objective_id.get(_oid)
            if _prev is None or _ex.get("started_at", 0) >= _prev.get("started_at", 0):
                exec_by_objective_id[_oid] = _ex

        cols = st.columns(len(GOAL_STATUS_COLUMNS))
        for col, (status_key, status_label) in zip(cols, GOAL_STATUS_COLUMNS):
            with col:
                st.markdown(f'<div class="kanban-col"><h4>{status_label}</h4>', unsafe_allow_html=True)
                bucket = [n for n in all_nodes if n.get("status") == status_key]
                # [P1 改造] 之前 Goal/Objective 拍平成一个列表按 status 平铺，
                # goals.json 里本来存在的父子关系（parent_id/children_ids）
                # 在看板上完全看不出来。这里改成：先列该列里的 Goal，紧跟着
                # 缩进列出它在本列里的子 Objective；至于父 Goal 当前处于
                # 别的状态列的 Objective（比如 Goal 还在"进行中"、其中一个
                # Objective 已经"完成"），不会凭空消失——单独放在本列末尾，
                # 并标注父 Goal 标题，避免用户以为数据丢了。
                goal_nodes = [n for n in bucket if n.get("level") != "objective"]
                obj_nodes = [n for n in bucket if n.get("level") == "objective"]
                rendered_obj_ids = set()

                for g in goal_nodes:
                    _render_goal_card(client, g, status_key)
                    children = [o for o in obj_nodes if o.get("parent_id") == g.get("id")]
                    for o in children:
                        _render_goal_card(
                            client, o, status_key, indent=True,
                            execution=exec_by_objective_id.get(o.get("id")),
                        )
                        rendered_obj_ids.add(o.get("id"))

                leftover = [o for o in obj_nodes if o.get("id") not in rendered_obj_ids]
                if leftover:
                    if goal_nodes:
                        st.caption("↓ 以下 Objective 的父 Goal 在其它状态列")
                    for o in leftover:
                        parent_title = title_by_id.get(o.get("parent_id"), "") if o.get("parent_id") else ""
                        note = f"父目标：{parent_title}" if parent_title else "（无父目标）"
                        _render_goal_card(
                            client, o, status_key, indent=True, note=note,
                            execution=exec_by_objective_id.get(o.get("id")),
                        )

                st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### ⏰ Cron Jobs")

    with st.expander("➕ 新建 Cron Job"):
        with st.form("new_cron_job"):
            cj_name = st.text_input("名称", key="cj_name")
            cj_schedule = st.text_input(
                "调度 (interval:<秒数> 或 cron:<表达式>)",
                placeholder="例如 interval:3600 或 cron:0 9 * * *",
                key="cj_schedule",
            )
            cj_task = st.text_area("任务内容 (task_template)", height=80, key="cj_task")
            cj_desc = st.text_area("描述（可选）", height=60, key="cj_desc")
            cj_submitted = st.form_submit_button("创建")
        if cj_submitted:
            if not (cj_name.strip() and cj_schedule.strip() and cj_task.strip()):
                st.error("名称、调度、任务内容均为必填")
            else:
                res = client.add_cron_job(
                    cj_name.strip(), cj_schedule.strip(), cj_task.strip(), cj_desc.strip()
                )
                if res and "_error" in res:
                    st.error(res["_error"])
                st.rerun()

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
    st.markdown("#### 🩺 为什么没有执行？（自主调度诊断）")
    autostat = client.autonomous_status() or {}
    if "_error" in autostat:
        st.warning(f"诊断信息获取失败：{autostat['_error']}")
    else:
        loop_active = autostat.get("loop_active", False)
        if not loop_active:
            st.error(
                "🔴 AutonomousLoop 未挂载在当前 daemon 上（`loop_active=False`）——"
                "无论 autonomy_level 配的是 maintenance 还是 autonomous，"
                "**tick 根本没有在跑**，Objective 永远不会被自动执行。"
                "需要确认 daemon 是以正确的模式启动的（挂载了 Self/AutonomousLoop），"
                "而不是只有一个普通的单 session Agent 在跑。"
            )
        else:
            has_work = autostat.get("has_actionable_work", False)
            next_tick = autostat.get("next_tick_in")
            slots = autostat.get("objective_slots") or {}
            dc1, dc2, dc3 = st.columns(3)
            dc1.metric("自主等级", autostat.get("autonomy_level", "—"))
            dc2.metric("距下次 Tick", f"{next_tick:.0f}s" if isinstance(next_tick, (int, float)) else "—")
            dc3.metric("可执行 Objective", "有" if has_work else "无")
            if not has_work:
                st.info(
                    "GoalBacklog 里没有 status=active 的 Objective——Goal 本身不算，"
                    "得先拆出 Objective 子节点才会被调度（`maintenance` 档位会在每次 "
                    "tick 时自动补拆，等下一次 tick 即可；如果一直没有，检查 Goal "
                    "是不是已经是 active 状态）。"
                )
            if slots:
                running, max_slots = slots.get("running", 0), slots.get("max", "?")
                st.caption(f"🎫 Objective 并发槽位：{running} / {max_slots}"
                           + ("（已占满，新 Objective 要等有槽位空出来才会启动）" if running and running >= (max_slots or 0) else ""))

            gating = autostat.get("gating")
            if gating:
                if gating.get("can_run_autonomous"):
                    st.success("✅ 资源仲裁（ResourceArbiter）三条规则均通过，理论上下次 tick 就会启动 Objective")
                else:
                    st.warning("⛔ 资源仲裁未通过，以下规则挡住了本次自主任务提交：")
                for rule in gating.get("rules", []):
                    icon = "✅" if rule.get("passed") else "⛔"
                    extra = {k: v for k, v in rule.items() if k not in ("rule", "label", "passed", "reason")}
                    extra_str = f"（{extra}）" if extra else ""
                    st.caption(f"{icon} **{rule.get('label')}**：{rule.get('reason')}{extra_str}")

    st.markdown("---")
    st.markdown("#### 🎯 Objective 执行进度")
    execs = autostat.get("objective_executions", [])
    if not execs:
        st.caption("当前没有正在执行的 Objective")
    for ex in execs:
        st.json(ex, expanded=False)

    # ── 主动推荐与数字分身机制设计方案：日报 / 推荐 / 决策画像 卡片 ──────
    # 三张卡片均为只读展示（数据由对应 cron job 或用户手动执行 /digest daily、
    # /next refresh、/decision_profile update 生成），这里不重复触发生成，
    # 避免看板刷新页面时意外产生额外的 LLM 调用。
    st.markdown("---")
    st.markdown("#### 🗞️ 每日融合日报 / 💡 主动推荐 / 🧭 决策画像")
    d1, d2, d3 = st.columns(3)

    with d1:
        st.markdown("**🗞️ 每日融合日报**")
        digest_resp = client.daily_digest() or {}
        if "_error" in digest_resp:
            st.caption(f"获取失败：{digest_resp['_error']}")
        else:
            digest = digest_resp.get("digest")
            if not digest:
                st.caption("暂无日报（daily_digest 未生成，或已在 agent_config.json 中禁用）")
            else:
                st.caption(f"日期：{digest.get('day', '?')}")
                behavior = digest.get("behavior") or {}
                if behavior:
                    st.json(behavior, expanded=False)
                deltas = digest.get("goal_deltas") or []
                if deltas:
                    for gd in deltas[:5]:
                        st.markdown(f"- {gd.get('title', gd.get('id', ''))}: {gd.get('summary', '')}")
                else:
                    st.caption("当天没有目标进展变化")

    with d2:
        st.markdown("**💡 主动推荐**")
        next_resp = client.next_actions() or {}
        if "_error" in next_resp:
            st.caption(f"获取失败：{next_resp['_error']}")
        else:
            next_data = next_resp.get("next_actions")
            if not next_data or not next_data.get("items"):
                st.caption("暂无推荐候选（没有停滞目标或注意力错配，或功能已禁用）")
            else:
                for item in next_data["items"][:5]:
                    kind_label = "🐢 停滞目标" if item.get("kind") == "stale_goal" else "👀 注意力错配"
                    st.markdown(f"**#{item.get('rank')} {kind_label}：{item.get('title')}**")
                    st.caption(item.get("reason", ""))

    with d3:
        st.markdown("**🧭 决策画像**")
        profile_resp = client.decision_profile() or {}
        if "_error" in profile_resp:
            st.caption(f"获取失败：{profile_resp['_error']}")
        elif not profile_resp.get("exists"):
            st.caption("暂无决策画像（默认关闭，`/cron enable sys:decision_profile_update` 或 `/decision_profile update` 手动生成）")
        else:
            patterns = profile_resp.get("patterns", [])
            if not patterns:
                st.caption("画像文件存在，但还没有归纳出任何模式")
            for p in patterns[:5]:
                confidence = float(p.get("confidence") or 0)
                st.markdown(f"- **{p.get('pattern', '')}**（置信度 {confidence:.0%}）")
                if p.get("contradicted_by"):
                    st.caption(f"⚠️ 存在矛盾证据：{p.get('contradicted_by')}")
            with st.expander("查看完整画像文档"):
                st.markdown(profile_resp.get("markdown") or "")



# ═══════════════════════════════════════════════════════════════════════
# Tab: 🔄 工作流（workflow机制改进计划（P7）二）
# ═══════════════════════════════════════════════════════════════════════

import re as _wf_re


def _wf_extract_input_params(yaml_text: str) -> list:
    """扫描工作流 YAML，找出运行前需要用户填写的参数名。两个来源都要覆盖：

    1. `{param}` 占位符（不含 '.' 的形式，`{step_id.output}` 这类运行时占位符
       排除）——旧写法，prompt 里直接插值。思路与 schema.py::validate() 里
       check_placeholders 一致，但这里拿到的是整份原始 YAML 文本（含注释、
       python_step 的 params 示例代码），必须先做两处收窄，否则会把注释里的
       示例（如 `# run_workflow(inputs={"doc_path": "..."}）`）或嵌套花括号
       （如注释里的 `{doc_path: "{doc_path}"}`）也误判成参数：
       a. 先去掉所有整行注释（`#` 开头，含前导空白）；
       b. 占位符名字限定为合法标识符字符，避免 `[^}]+` 这种"贪到下一个 `}`
          为止"的写法被注释里的嵌套花括号切出半截、含引号/冒号的假参数。
    2. `type: human_input` + `input_key: xxx` ——workflow_mechanism_improvement_
       proposal.md §1 之后的推荐写法：不在 prompt 里写 `{param}`，而是声明
       一个 intake 型 human_input 步骤，`input_key` 命中 `run_workflow(inputs=)`
       里的同名 key 时直接取值、不阻塞等待。这种写法下 YAML 里可能完全不出现
       `{param}` 字面量，只扫第 1 类会漏掉参数（比如 zhihu_content_publish
       这类新版示例 workflow）。
    """
    cleaned_lines = []
    for line in yaml_text.splitlines():
        if line.strip().startswith("#"):
            continue
        # 剥离行内尾部注释（`  # ...`）——要求 '#' 前有至少一个空白，避免误伤
        # 值本身含 '#' 的情况（这批 workflow YAML 里没有这种用法，足够安全）。
        line = _wf_re.sub(r"\s+#.*$", "", line)
        cleaned_lines.append(line)
    cleaned_text = "\n".join(cleaned_lines)

    keys = []
    for m in _wf_re.finditer(r"\{([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\}", cleaned_text):
        key = m.group(1)
        if "." not in key and key not in keys:
            keys.append(key)
    for m in _wf_re.finditer(r"^\s*input_key:\s*[\"']?([a-zA-Z_][a-zA-Z0-9_]*)[\"']?\s*$", cleaned_text, _wf_re.MULTILINE):
        key = m.group(1)
        if key not in keys:
            keys.append(key)
    return keys


def _wf_project_root(client: AgentClient) -> str:
    """拿 project_root 绝对路径，用于把 /fs/list 返回的相对路径拼成
    run_workflow(inputs=...)/output_export_dir 需要的绝对路径。取不到时
    返回空字符串，调用方按"拼不出绝对路径，只能手动输入"降级处理。"""
    try:
        st_resp = client.status() or {}
        return st_resp.get("project_root") or ""
    except Exception:
        return ""


def _render_fs_picker(client: AgentClient, state_key: str, *,
                       allow_upload: bool = False) -> Optional[str]:
    """输入文件选择器（workflow 运行面板专用）：浏览 project_root 内的目录树，
    点某个文件返回它的绝对路径；allow_upload=True 时额外提供一个文件上传
    控件，把用户浏览器本地文件传到当前浏览目录下（通过 /fs/upload），上传完
    直接可选。

    注意：/fs/* 系列接口本身 jail 在 project_root 内（fs_helper.py::
    FsHelper._safe_path），只能选到项目目录内的文件。

    [output_export_dir 改动] 输出目录的选择曾经也接到过这个组件（dir 模式 +
    新建子目录），实际用下来逐级点击浏览比直接打一行绝对路径更麻烦，已改回
    普通 st.text_input（见 _render_workflow_run_panel），不再提供目录浏览器。

    返回值：本次调用里用户新选中的绝对路径（None 表示本次没有新选择，
    调用方应保留之前已选的值，不要覆盖成 None）。
    """
    root = _wf_project_root(client)
    browse_key = f"{state_key}_fs_browse_dir"
    if browse_key not in st.session_state:
        st.session_state[browse_key] = "."

    nav1, nav2 = st.columns([4, 1])
    nav1.caption(f"当前浏览：`{st.session_state[browse_key]}`（相对 project_root）")
    if nav2.button("⬆️ 上级", key=f"{state_key}_fs_up"):
        parts = st.session_state[browse_key].rstrip("/").split("/")
        st.session_state[browse_key] = "/".join(parts[:-1]) or "."
        st.rerun()

    listing = client.fs_list(st.session_state[browse_key]) or {}
    if "_error" in listing:
        st.warning(f"目录浏览失败：{listing['_error']}")
        return None

    picked: Optional[str] = None
    entries = listing.get("entries", [])
    if not entries:
        st.caption("（空目录）")
    for e in entries:
        name = e.get("name", "")
        is_dir = e.get("is_dir", False)
        rel_path = e.get("path") or name
        c1, c2 = st.columns([4, 1])
        c1.write(("📁 " if is_dir else "📄 ") + name)
        if is_dir:
            if c2.button("进入", key=f"{state_key}_fs_open_{rel_path}"):
                st.session_state[browse_key] = rel_path
                st.rerun()
        else:
            if c2.button("选它", key=f"{state_key}_fs_pick_{rel_path}"):
                picked = f"{root.rstrip('/')}/{rel_path}" if root else rel_path

    if allow_upload:
        up = st.file_uploader("或从本地上传文件到当前目录", key=f"{state_key}_fs_upload")
        if up is not None:
            target_rel = up.name if st.session_state[browse_key] in (".", "") \
                else f"{st.session_state[browse_key]}/{up.name}"
            res = client.fs_upload(target_rel, up.getvalue(), filename=up.name) or {}
            if "_error" in res:
                st.error(f"上传失败：{res['_error']}")
            else:
                picked = f"{root.rstrip('/')}/{target_rel}" if root else target_rel
                st.success(f"已上传到 `{picked}`")

    return picked


def _render_workflow_run_panel(client: AgentClient):
    st.markdown("##### ▶️ 运行面板")
    wf_data = client.workflows() or {}
    if "_error" in wf_data:
        st.warning(f"工作流列表获取失败：{wf_data['_error']}")
        return
    workflows = wf_data.get("workflows", [])
    if not workflows:
        st.info("暂无已保存的工作流（可让主 Agent 调用 generate_workflow / create_workflow_from_template 创建）。")
        return

    names = [w["name"] for w in workflows]
    selected = st.selectbox("选择工作流", names, key="wf_selected_name")

    yaml_resp = client.workflow_yaml(selected) or {}
    yaml_text = yaml_resp.get("yaml", "")
    params = _wf_extract_input_params(yaml_text) if yaml_text else []

    inputs = {}
    if params:
        st.caption("需要填写的参数：")
        cols = st.columns(min(len(params), 3) or 1)
        for i, p in enumerate(params):
            with cols[i % len(cols)]:
                input_key = f"wf_input_{selected}_{p}"
                inputs[p] = st.text_input(p, key=input_key)
                # [输入文件夹支持] 参数名看起来像路径（path/file/dir/目录/
                # 文件），才展示浏览器——不是每个参数都值得占屏幕空间，纯
                # 文本参数（如 topic/env）用不上这个。是否真的是路径类
                # 参数，最终仍由用户自己判断，这里只是命中关键词时给个入口。
                if any(kw in p.lower() for kw in ("path", "file", "dir", "目录", "文件")):
                    with st.expander(f"📂 从项目内选择 {p}"):
                        picked = _render_fs_picker(
                            client, input_key, allow_upload=True,
                        )
                        if picked:
                            st.session_state[input_key] = picked
                            st.rerun()
    else:
        st.caption("该工作流没有需要用户填写的 {param} 占位符。")

    if "mode: autonomous" in yaml_text:
        st.caption("🤖 该工作流声明为 `autonomous` 模式：不含需要人工介入的步骤，可放心全自动跑完。")

    with st.expander("查看 YAML 定义"):
        st.code(yaml_text, language="yaml")

    with st.expander("⚙️ 运行选项（可控性护栏）"):
        oc1, oc2 = st.columns(2)
        force_serial = oc1.checkbox(
            "强制全部串行", key=f"wf_force_serial_{selected}",
            help="忽略并行分批，本次运行退化为单线程顺序执行，适合调试/临时资源受限场景，不改 YAML。",
        )
        require_all_inputs_upfront = oc2.checkbox(
            "要求输入一次性给全（拒绝中途阻塞）", key=f"wf_require_all_inputs_{selected}",
            help="开启后，凡是 human_input 步骤没有对应 input_key/未能从上面参数解析到值，"
                 "启动前直接报错，不会等到运行中途才卡住。",
        )
        output_export_dir = st.text_input(
            "完成后复制产出到此目录（可选，留空则不复制）", key=f"wf_output_export_dir_{selected}",
            help="工作流跑到终态（成功/失败/部分完成都算）后，会把本次执行"
                 " output/ 目录下的所有文件复制到这里；留空则跳过这一步，"
                 "行为与不填完全一致。",
            placeholder="例如 /home/user/Downloads/zhihu_output",
        ).strip()

    c1, c2 = st.columns([1, 1])
    if c1.button("🔍 预览执行计划", key="wf_preview_btn"):
        preview = client.preview_workflow(selected, inputs)
        if preview and "_error" in preview:
            st.error(preview["_error"])
        else:
            st.json(preview, expanded=True)

    if c2.button("🚀 运行", key="wf_run_btn", type="primary"):
        res = client.run_workflow(
            selected, inputs, background=True,
            force_serial=force_serial or None,
            require_all_inputs_upfront=require_all_inputs_upfront,
            output_export_dir=output_export_dir or None,
        )
        if res and "_error" in res:
            st.error(res["_error"])
        else:
            st.session_state.wf_active_run_id = res.get("workflow_session_id")
            st.rerun()

    _render_workflow_stats_panel(client, selected)


def _render_workflow_stats_panel(client: AgentClient, selected: str):
    """[P9-1a workflow_system_next_directions.md §1.2a] 历史执行统计视图：
    对已跑过的次数做即时聚合展示，帮助判断"这个工作流稳不稳、哪个 step
    最容易卡"，纯只读，不产生新的落盘数据。"""
    with st.expander("📊 历史执行统计", expanded=False):
        stats = client.workflow_stats(selected) or {}
        if "_error" in stats:
            st.warning(f"统计数据获取失败：{stats['_error']}")
            return

        total_runs = stats.get("total_runs", 0)
        if not total_runs:
            st.caption("该工作流暂无历史执行记录，跑过之后这里会显示成功率与各步骤耗时/评分统计。")
            return

        m1, m2 = st.columns(2)
        m1.metric("累计执行次数", total_runs)
        m2.metric("成功率", f"{stats.get('success_rate', 0.0) * 100:.1f}%")

        step_stats = stats.get("step_stats", {})
        if step_stats:
            st.caption("各步骤表现：")
            rows = []
            for step_id, s in step_stats.items():
                rows.append({
                    "步骤": step_id,
                    "出现次数": s.get("total", 0),
                    "完成率": f"{(1 - s.get('fail_rate', 0.0)) * 100:.1f}%",
                    "平均耗时(s)": s.get("avg_duration", 0.0),
                    "平均评分": s.get("avg_score") if s.get("avg_score") is not None else "-",
                    "平均重试次数": s.get("avg_retries_used", 0.0),
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)

        condition_stats = stats.get("condition_stats", {})
        if condition_stats:
            st.caption("条件分支（condition）实际执行比例：")
            rows = [
                {"步骤": step_id, "实际执行比例": f"{c.get('true_rate', 0.0) * 100:.1f}%", "样本数": c.get("total", 0)}
                for step_id, c in condition_stats.items()
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_workflow_step_card(client: AgentClient, run_id: str, step_id: str, sr: dict):
    status = sr.get("status", "pending")
    score = sr.get("score")
    duration = sr.get("duration_seconds", 0.0)
    meta = f"{duration:.1f}s"
    if score is not None:
        meta += f"　评分 {score}"
    if status == "needs_fix":
        meta += "　⚠️ 定义问题，重跑无效"
    st.markdown(f"""
<div class="kanban-card">
  <div class="title">{step_id}</div>
  <div class="meta">{meta}</div>
</div>
""", unsafe_allow_html=True)
    output = sr.get("output") or ""
    if output:
        preview_text = output[:200].replace("\n", " ")
        if len(output) > 200:
            preview_text += "..."
        st.caption(preview_text)
    # [workflow_mechanism_improvement_proposal.md §4.1] 出错信息不再只在 failed
    # 时展示——gate_failed/needs_fix 同样需要错误原因才能决定下一步动作。
    if status in ("failed", "gate_failed", "needs_fix") and sr.get("error"):
        error_type = sr.get("error_type")
        prefix = f"{error_type}: " if error_type else ""
        st.caption(f"❌ {prefix}{sr['error']}")

    # [§4.2] 结构性错误（needs_fix）或普通失败，都可以直接在卡片上改 step 定义，
    # 不用回到主 Agent 对话里贴 patch_workflow_step 的 JSON。
    if status in ("failed", "needs_fix", "gate_failed"):
        workflow_name = st.session_state.get("wf_selected_name") or ""
        with st.expander("🛠️ 修改此步骤定义并续跑"):
            wf_name_input = st.text_input(
                "所属工作流名称", value=workflow_name, key=f"wf_patch_wfname_{run_id}_{step_id}",
                help="需要与该 run 对应的工作流名称一致，才能改到正确的定义文件。",
            )
            new_prompt = st.text_area(
                "新的 prompt（留空则不改）", key=f"wf_patch_prompt_{run_id}_{step_id}", height=100,
            )
            new_timeout = st.number_input(
                "新的 timeout 秒数（0=不改）", min_value=0, value=0, step=10,
                key=f"wf_patch_timeout_{run_id}_{step_id}",
            )
            if st.button("保存修改并从此步骤续跑", key=f"wf_patch_btn_{run_id}_{step_id}"):
                patch = {}
                if new_prompt.strip():
                    patch["prompt"] = new_prompt
                if new_timeout:
                    patch["timeout"] = int(new_timeout)
                if not patch:
                    st.warning("没有填写任何要修改的字段。")
                elif not wf_name_input:
                    st.warning("请先填写工作流名称。")
                else:
                    res = client.patch_workflow_step(wf_name_input, step_id, patch)
                    if res and "_error" in res:
                        st.error(res["_error"])
                    else:
                        client.resume_workflow_run(run_id, background=True, force_rerun_from=step_id)
                        st.success("已保存修改，正在从此步骤续跑…")
                        st.rerun()

    if status == "awaiting_approval":
        reason_key = f"wf_reject_reason_{run_id}_{step_id}"
        reason = st.text_input("拒绝原因（可选）", key=reason_key, label_visibility="collapsed", placeholder="拒绝原因（可选）")
        ac1, ac2 = st.columns(2)
        if ac1.button("✅ 批准", key=f"wf_approve_{run_id}_{step_id}"):
            client.approve_workflow_step(run_id)
            st.rerun()
        if ac2.button("❌ 拒绝", key=f"wf_reject_{run_id}_{step_id}"):
            client.reject_workflow_step(run_id, reason)
            st.rerun()

    if status == "done":
        with st.expander("✏️ 编辑此步骤输出并续跑"):
            edited = st.text_area("新的输出内容", value=output, key=f"wf_override_{run_id}_{step_id}", height=120)
            if st.button("以此结果继续（重跑下游步骤）", key=f"wf_override_btn_{run_id}_{step_id}"):
                client.override_workflow_step_output(run_id, step_id, edited)
                client.resume_workflow_run(run_id, background=True, force_rerun_from=step_id)
                st.rerun()


def _render_workflow_run_detail(client: AgentClient, run_id: str):
    """[P0 改造] 原来跑到 status=="running" 就 `time.sleep(2); st.rerun()`，
    同样是整页阻塞。这里只在"确实在运行"时才挂一个局部 fragment 自动刷新
    （跑完就不再挂，避免和 P0 计划里提到的"空转耗资源"问题相反地反而
    一直占着一个刷新中的 fragment）。"""
    detail = client.workflow_run_detail(run_id)
    if detail and "_error" in detail:
        st.error(f"执行详情获取失败：{detail['_error']}")
        return
    if detail.get("status") == "running":
        _render_workflow_run_detail_fragment(client, run_id)
    else:
        _render_workflow_run_detail_body(client, run_id, detail)


@st.fragment(run_every="2s")
def _render_workflow_run_detail_fragment(client: AgentClient, run_id: str):
    detail = client.workflow_run_detail(run_id)
    if detail and "_error" in detail:
        st.error(f"执行详情获取失败：{detail['_error']}")
        return
    _render_workflow_run_detail_body(client, run_id, detail)


def _render_workflow_run_detail_body(client: AgentClient, run_id: str, detail: dict):
    status = detail.get("status", "unknown")
    is_stale = detail.get("is_stale", False)
    st.markdown(f"##### 🔄 {detail.get('workflow_name', run_id)}　`{run_id}`")
    if is_stale:
        st.warning(
            "⚠️ 状态显示为「运行中」，但进程内已经没有活跃控制——大概率是 daemon "
            "在这次执行完成前重启/崩溃了，实际早就没有线程在处理。暂停/取消按钮"
            "对这种孤儿记录会报错（因为它们依赖的进程内控制状态已经不存在），"
            "可以点下面「标记为已中断」直接清理。"
        )
        if st.button("🧹 标记为已中断", key=f"wf_mark_interrupted_detail_{run_id}"):
            res = client.mark_workflow_run_interrupted(run_id)
            if res and "_error" in res:
                st.error(res["_error"])
            else:
                st.success("已标记为已中断（cancelled）。")
                st.rerun()
    else:
        st.caption(f"状态：{WORKFLOW_RUN_STATUS_LABELS.get(status, status)}")

        tc1, tc2, tc3 = st.columns(3)
        if tc1.button("⏸️ 暂停", key=f"wf_pause_{run_id}"):
            client.pause_workflow_run(run_id)
            st.rerun()
        if tc2.button("🛑 取消", key=f"wf_cancel_{run_id}"):
            client.cancel_workflow_run(run_id)
            st.rerun()
        if tc3.button("▶️ 续跑", key=f"wf_resume_{run_id}"):
            client.resume_workflow_run(run_id, background=True)
            st.rerun()

    step_results = detail.get("step_results", {})
    cols = st.columns(len(WORKFLOW_STEP_COLUMNS))
    for col, (label, status_values) in zip(cols, WORKFLOW_STEP_COLUMNS):
        with col:
            st.markdown(f'<div class="kanban-col"><h4>{label}</h4>', unsafe_allow_html=True)
            for step_id, sr in step_results.items():
                if sr.get("status") in status_values:
                    _render_workflow_step_card(client, run_id, step_id, sr)
            st.markdown("</div>", unsafe_allow_html=True)

    if detail.get("output_dir"):
        st.caption(f"📁 本次执行输出目录：`{detail['output_dir']}`")
    if detail.get("output_export_dir"):
        if status in ("done", "failed", "partial", "cancelled"):
            st.caption(f"📤 已（尝试）复制到外部目录：`{detail['output_export_dir']}`（详情见下方历史事件）")
        else:
            st.caption(f"📤 完成后将复制产出到：`{detail['output_export_dir']}`")


def render_workflow_tab(client: AgentClient):
    st.markdown("#### 🔄 工作流")

    _render_workflow_run_panel(client)
    st.markdown("---")

    active_run_id = st.session_state.get("wf_active_run_id")
    if active_run_id:
        _render_workflow_run_detail(client, active_run_id)
        st.markdown("---")

    with st.expander("📜 历史执行记录", expanded=not active_run_id):
        runs_resp = client.workflow_runs() or {}
        if "_error" in runs_resp:
            st.warning(f"执行记录获取失败：{runs_resp['_error']}")
        else:
            runs = runs_resp.get("runs", [])
            if not runs:
                st.caption("暂无工作流执行记录。")
            for r in sorted(runs, key=lambda x: x.get("started_at", 0), reverse=True):
                rid = r.get("workflow_session_id")
                label = r.get("summary_line", rid)
                if r.get("is_stale"):
                    label = f"⚠️ {label}（疑似孤儿记录，daemon 重启后遗留）"
                rc1, rc2 = st.columns([5, 1])
                rc1.markdown(f"`{rid}`　{label}")
                if rc2.button("查看", key=f"wf_open_{rid}"):
                    st.session_state.wf_active_run_id = rid
                    st.rerun()


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
# Tab: 进化提案（看板与自主性改进方案 Track I —— 第八轮补齐的看板可视化）
# ═══════════════════════════════════════════════════════════════════════
_PROPOSAL_RISK_LABEL = {"low": "🟢 低风险", "high": "🟡 需人工审核"}


def render_evolution_proposals_tab(client: AgentClient):
    """[Track I] 展示所有 evolve/* 提案分支的风险分级 + diff + 一键合并按钮。

    对应第七轮实施记录"未完成/待续"里标注的看板可视化半成品：CLI 侧
    （`/evolution proposals`/`/evolution merge`）已经能完整覆盖"查看分级 +
    一键合并"，这里只是给同一套后端能力（`classify_proposal_risk()`/
    `StateRepo.merge_branch()`，经由本轮新增的 `/v1/evolution/proposals*`
    REST 端点）接上看板 UI，判断逻辑不重新实现。
    """
    st.markdown("#### 🧬 进化提案")
    st.caption(
        "列出所有待处理的 evolve/* 提案分支。低风险（只改文档/lesson 规则，"
        "tier ≤ T1，且无 eval 回归）可以一键合并；中/高风险维持人工审核，"
        "需要展开查看判定依据并二次确认后才能强制合并。"
    )

    if st.button("🔄 刷新提案列表", key="evo_proposals_refresh"):
        st.rerun()

    resp = client.evolution_proposals()
    if resp and "_error" in resp:
        st.error(f"获取提案列表失败：{resp['_error']}")
        return
    items = (resp or {}).get("items") or []
    if not items:
        st.info("当前没有待处理的进化提案分支。")
        return

    for item in items:
        branch = item.get("branch", "")
        risk = item.get("risk", "high")
        risk_label = _PROPOSAL_RISK_LABEL.get(risk, risk)
        with st.container(border=True):
            top1, top2, top3 = st.columns([3, 1, 1])
            top1.markdown(f"**{branch}**")
            top2.markdown(risk_label)
            top3.caption(f"tier {item.get('max_tier') or '-'} · {item.get('commit_count', 0)} commits")

            reasons = item.get("reasons") or []
            if reasons:
                st.caption("判定依据：" + "；".join(reasons))

            changed_paths = item.get("changed_paths") or []
            if changed_paths:
                preview = ", ".join(changed_paths[:8])
                more = f" 等共 {len(changed_paths)} 个文件" if len(changed_paths) > 8 else ""
                st.caption(f"改动文件：{preview}{more}")

            with st.expander("📄 查看 diff"):
                diff_resp = client.evolution_proposal_diff(branch)
                if diff_resp and "_error" in diff_resp:
                    st.caption(f"获取 diff 失败：{diff_resp['_error']}")
                else:
                    diff_text = (diff_resp or {}).get("diff") or ""
                    if not diff_text:
                        st.caption("（无 diff 内容）")
                    else:
                        files = parse_unified_diff(diff_text)
                        summary = summarize_files(files)
                        if summary:
                            st.caption(f"摘要：{summary}")
                        if len(files) == 1 and not files[0].path:
                            # 未能按文件切分（比如不认识的 diff 格式），退回整体展示，
                            # 与升级前的行为完全一致，不改变可用性。
                            st.code(diff_text[:20000], language="diff")
                            if len(diff_text) > 20000:
                                st.caption("diff 内容过长，已截断展示前 20000 字符。")
                        else:
                            for fd in files:
                                with st.expander(f"📝 {fd.summary}", expanded=(len(files) == 1)):
                                    if fd.is_binary:
                                        st.caption("二进制文件，无法显示逐行差异。")
                                    else:
                                        st.code(fd.body[:20000], language="diff")
                                        if len(fd.body) > 20000:
                                            st.caption("该文件 diff 过长，已截断展示前 20000 字符。")

            if risk == "low":
                if st.button("✅ 一键合并", key=f"evo_merge_low_{branch}"):
                    res = client.merge_evolution_proposal(branch, force=False)
                    if res and "_error" in res:
                        st.error(f"合并失败：{res['_error']}")
                    else:
                        st.success(f"已合并 {branch} → {res.get('merged_into', '')}（commit {str(res.get('commit',''))[:8]}）")
                        st.rerun()
            else:
                st.warning("⚠️ 该提案需要人工审核，不建议直接合并。确认已阅读上方判定依据和 diff 后，"
                           "可以勾选下方确认框并强制合并。")
                confirm_key = f"evo_force_confirm_{branch}"
                confirmed = st.checkbox("我已人工审核过这份提案的 diff，确认要强制合并", key=confirm_key)
                if st.button("⚠️ 强制合并", key=f"evo_merge_force_{branch}", disabled=not confirmed):
                    res = client.merge_evolution_proposal(branch, force=True)
                    if res and "_error" in res:
                        st.error(f"合并失败：{res['_error']}")
                    else:
                        st.success(f"已强制合并 {branch} → {res.get('merged_into', '')}（commit {str(res.get('commit',''))[:8]}）")
                        st.rerun()


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
                # [P1 一致性修复] 同上，写 query_params 后不再手动 st.rerun()。
                update_query_params(auth=None)
            st.divider()

    client = render_sidebar()

    if not client.health():
        st.info("请先在左侧确认 API Base URL / Token，并确保 mini-agent daemon 已启动。")
        return

    render_topbar(client, get_active_session_id())

    tabs = st.tabs(["💬 对话", "🗂️ 会话管理", "📌 目标看板", "🔄 工作流", "📁 产出物", "🖼️ 产出预览",
                    "🧠 自我状态", "🧬 进化提案", "🔧 诊断"])
    with tabs[0]:
        render_chat_tab(client, get_active_session_id())
    with tabs[1]:
        render_sessions_tab(client)
    with tabs[2]:
        render_kanban_tab(client)
    with tabs[3]:
        render_workflow_tab(client)
    with tabs[4]:
        render_artifacts_tab(client)
    with tabs[5]:
        render_artifacts_preview_tab(client)
    with tabs[6]:
        render_self_tab(client)
    with tabs[7]:
        render_evolution_proposals_tab(client)
    with tabs[8]:
        render_diagnostics_tab(client)

    # [P0 改造] 原来这里是 `if auto_refresh: time.sleep(3); st.rerun()`——
    # 整页阻塞 3 秒再重跑，期间所有 tab、所有正在填的表单都被冻结。
    # 现在"状态条"（render_topbar）和"事件流"（_render_events_panel）
    # 已经各自用 st.fragment(run_every=...) 做局部刷新，不再需要这个
    # 全局阻塞轮询兜底。auto_refresh 这个开关现在的语义变成"是否启用这
    # 两个 fragment 的自动刷新"，在各自函数内部读取，这里不用再处理。


if __name__ == "__main__":
    main()
