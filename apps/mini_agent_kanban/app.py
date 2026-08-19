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
  - Tab5.5 ⚙️ 配置：分类展示/编辑 agent_config.json（kanban_config_management_plan.md）
  - Tab6 🔧 诊断：/diagnostics 原始信息，方便调试

运行方式：
    streamlit run apps/mini_agent_kanban/app.py
"""
import html
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

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


def _inject_tab_switch_script(tab_label: str):
    """[daemon_stability_and_ux_improvement_plan.md 补充 / 看板顶栏跳转]
    st.tabs() 没有官方 API 可以\"程序化切换到第 N 个 tab\"——这里复用
    `_inject_scroll_script()` 同款 `window.parent.document` + JS 注入的
    做法：Streamlit 的 tabs 在 DOM 里渲染成一组
    `button[data-baseweb="tab"]`，按钮的可见文本就是传给 `st.tabs([...])`
    的字符串（含 emoji），直接按"文本包含关系"找到对应按钮并 `.click()`，
    不依赖固定的 tab 顺序/下标（顺序调整不会悄悄破坏跳转功能）。

    找不到匹配按钮（比如 tab 文案以后改了）时静默不做任何事，不抛异常
    影响页面其余部分渲染——这是纯粹的体验增强，不是关键路径。
    """
    safe_label = json.dumps(tab_label)
    script = f"""
    <script>
    (function() {{
        const doc = window.parent.document;
        const target = {safe_label};
        const btns = doc.querySelectorAll('button[data-baseweb="tab"]');
        for (const b of btns) {{
            if (b.textContent && b.textContent.indexOf(target) !== -1) {{
                b.click();
                break;
            }}
        }}
    }})();
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

# ── 看板本地偏好持久化 ────────────────────────────────────────────────────────
# [新增] 目标看板"只看某几种状态"这类纯 UI 偏好，用户希望"下次打开还记得
# 上次选的"——这跟 URL query params 那套"每个标签页/每次打开各自独立"的
# 深链接语义（见 get_active_session_id 等函数的说明）刚好相反：这里要的是
# "不管从哪个 URL、哪次打开，都用同一份偏好"，所以不适合走 query_params，
# 改成一个跟 app.py 同目录的本地小 JSON 文件，读写都做好异常兜底（文件不
# 存在/损坏/无写权限时静默退化为"这次先用默认值，不阻断使用"）。
_KANBAN_PREFS_PATH = Path(__file__).resolve().parent / ".kanban_prefs.json"


def _load_kanban_prefs() -> dict:
    try:
        return json.loads(_KANBAN_PREFS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_kanban_pref(key: str, value) -> None:
    prefs = _load_kanban_prefs()
    prefs[key] = value
    try:
        _KANBAN_PREFS_PATH.write_text(
            json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except Exception:
        pass

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

/* 顶部 tab 切换栏（st.tabs）在窗口不够宽时默认是横向滚动、超出部分被
   遮住看不见（不是真的换行）。这里强制 tab-list 换行展示，宽度不够时
   自动变成多行，不再需要横向滚动才能看到剩下的 tab；同时隐藏原本用来
   横向滚动的左右箭头按钮（换行之后不再需要）。选择器同时覆盖
   data-baseweb 和 data-testid 两种属性，兼容不同 streamlit 版本的
   DOM 结构差异。 */
div[data-testid="stTabs"] [data-baseweb="tab-list"],
div[data-testid="stTabs"] div[role="tablist"] {
    flex-wrap: wrap !important;
    overflow-x: visible !important;
    row-gap: 4px;
}
div[data-testid="stTabs"] button[data-baseweb="tab"],
div[data-testid="stTabs"] button[role="tab"] {
    white-space: nowrap;
    flex: 0 0 auto;
}
div[data-testid="stTabs"] button[data-testid="stTabsScrollButton"] {
    display: none !important;
}
/* streamlit 原生的"选中态"是一根绝对定位、靠 transform: translateX +
   宽度动画滑到当前 tab 下方的高亮条，这套定位算法假设所有 tab 在同一行
   里——换行成多行后，这根条要么停在第一行的某个位置不动，要么整条跨
   到别的行，看着就是"选中状态跟当前点的 tab 对不上"。这里直接隐藏这根
   滑动高亮条，改成给每个 tab 按钮自己按 aria-selected 状态加下划线/
   高亮底色，选中状态永远跟着按钮本身走，不受换行影响。 */
div[data-testid="stTabs"] [data-baseweb="tab-highlight"] {
    display: none !important;
}
div[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"],
div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    color: #ff4b4b;
    border-bottom: 2.5px solid #ff4b4b;
}
div[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="false"],
div[data-testid="stTabs"] button[role="tab"][aria-selected="false"] {
    border-bottom: 2.5px solid transparent;
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
        # [daemon_stability_and_ux_improvement_plan.md 补充 / 看板顶栏跳转]
        # 顶栏"正在执行"列表点击"🔍 查看并控制"后，记录要跳转定位到的
        # Goal/Objective id、Cron job id，供对应 tab 渲染时高亮/置顶展示；
        # "_pending_tab_switch" 记录本次 rerun 后需要用 JS 点击切到哪个
        # tab（见 `_inject_tab_switch_script()`），消费一次后清空，不是
        # 持久状态。
        "kanban_focus_node_id": None,
        "cron_focus_job_id": None,
        "_pending_tab_switch": None,
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
        submitted = st.form_submit_button("登录", width='stretch')

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


def _render_scheduling_pause_control(client: AgentClient, autostat: dict) -> None:
    """[看板"停止调度"功能] 顶栏常驻的全局调度暂停/恢复控件。

    暂停后 AutonomousLoop.tick() 直接短路，cron job / Objective 推进 /
    软目标 derive 全部不再自动触发，但不影响手动调试：cron"⏰ Cron 任务"
    tab 的"立即触发"、"📌 目标看板"tab 里对 Goal/Objective 的手动增删改，
    在暂停期间仍然照常可用——这正是"停下来但还能手动调 Goal/Cron"的入口。
    """
    paused = bool(autostat.get("scheduling_paused"))

    if paused:
        reason = autostat.get("scheduling_paused_reason") or ""
        paused_at = autostat.get("scheduling_paused_at")
        paused_at_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(paused_at)) if paused_at else "—"
        with st.container():
            st.warning(
                f"⏸️ 自动调度已全局暂停（{paused_at_str} 起）"
                + (f"，原因：{reason}" if reason else "")
                + "。cron / Objective / 软目标 derive 均不会自动触发，"
                "但仍可在下方 tab 手动触发 cron job、增删改 Goal/Objective 进行调试。"
            )
            if st.button("▶️ 恢复调度", key="resume_scheduling_btn",
                         help="撤销暂停，AutonomousLoop 从下一次 tick 起恢复正常按当前自主等级执行；"
                              "不会补跑暂停期间错过的 cron 周期。"):
                res = client.resume_scheduling()
                if res and "_error" in res:
                    st.error(res["_error"])
                else:
                    st.success("已恢复调度。")
                st.rerun()
    else:
        if st.button("⏸️ 暂停全部调度", key="pause_scheduling_btn",
                      help="全局停止 cron job / Objective 推进 / 软目标 derive 等一切自动调度，"
                           "不影响当前正在跑的任务和手动调试操作；状态会持久化，daemon 重启后仍保持暂停。"):
                res = client.pause_scheduling()
                if res and "_error" in res:
                    st.error(res["_error"])
                else:
                    st.success("已暂停全部自动调度。")
                st.rerun()


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
        if ex.get("status") == "running" and not ex.get("is_stale")
    ]
    # [看板『正在执行』实时性修复] 与 stale_wfs 同一套展示逻辑：is_stale
    # 为 True 代表磁盘状态还是 running，但进程内已经找不到对应 worker
    # （多半是 daemon 崩溃/重启后遗留），不计入上方"正在执行"，单独列出并
    # 提供清理入口。
    stale_objs = [
        ex for ex in (autostat.get("objective_executions") or [])
        if ex.get("status") == "running" and ex.get("is_stale")
    ]

    cron_resp = client.cron_jobs() or {}
    running_crons = [
        job for job in (cron_resp.get("jobs") or [])
        if "_error" not in cron_resp and job.get("execution_phase") == "running"
    ]

    running_wfs = []
    stale_wfs = []
    wf_runs = client.workflow_runs() or {}
    if "_error" not in wf_runs:
        for r in (wf_runs.get("runs") or []):
            if r.get("status") != "running":
                continue
            (stale_wfs if r.get("is_stale") else running_wfs).append(r)

    if not running_objs and not running_crons and not running_wfs and not stale_wfs and not stale_objs:
        return

    n = len(running_objs) + len(running_crons) + len(running_wfs)
    if n:
        with st.expander(f"⚙️ daemon 正在执行 {n} 项任务（点击查看）", expanded=True):
            # [daemon_stability_and_ux_improvement_plan.md 补充 / 看板顶栏跳转]
            # 之前这里只是纯文本 markdown 列表，看不出"这项任务到底是 Goal
            # 派生的 Objective 执行、还是 cron 定时任务、还是 workflow"，
            # 更没有办法从这里直接跳转到对应 tab 做暂停/终止等控制——只能
            # 自己记住标题，再手动切 tab 翻找。这里改成"来源标签 + 一句话
            # 摘要 + 跳转按钮"的三列布局，点击按钮设置对应的
            # focus 状态并用 JS 把 tab 切过去，目标 tab 会据此高亮/置顶
            # 展示这一项，不需要用户自己找。
            for ex in running_objs:
                title = ex.get("title") or ex.get("objective_id", "")
                step = ex.get("current_step") or ""
                progress = ex.get("progress") or ""
                detail = f"　·　{step}" if step else ""
                c1, c2 = st.columns([6, 1])
                c1.markdown(f"🎯 **来源：目标(Goal)**　{title}　({progress}){detail}")
                if c2.button("🔍 查看并控制", key=f"jump_obj_{ex.get('execution_id') or ex.get('objective_id')}"):
                    st.session_state["kanban_focus_node_id"] = ex.get("objective_id")
                    st.session_state["_pending_tab_switch"] = "📌 目标看板"
                    st.rerun()
            for job in running_crons:
                job_id = job.get("id", "")
                name = job.get("name") or job_id
                c1, c2 = st.columns([6, 1])
                c1.markdown(f"⏰ **来源：Cron 定时任务**　{name}　`{job_id}`")
                if c2.button("🔍 查看并控制", key=f"jump_cron_{job_id}"):
                    st.session_state["cron_focus_job_id"] = job_id
                    st.session_state["_pending_tab_switch"] = "⏰ Cron 任务"
                    st.rerun()
            for r in running_wfs:
                wf_name = r.get("workflow_name") or r.get("name") or ""
                rid = r.get("workflow_session_id", "")
                label = r.get("summary_line") or ""
                detail = f"　·　{label}" if label else ""
                c1, c2 = st.columns([6, 1])
                c1.markdown(f"🔄 **来源：工作流(Workflow)**　{wf_name}　`{rid}`{detail}")
                if c2.button("🔍 查看并控制", key=f"jump_wf_{rid}"):
                    st.session_state["wf_active_run_id"] = rid
                    st.session_state["_pending_tab_switch"] = "🔄 工作流"
                    st.rerun()

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

    if stale_objs:
        with st.expander(
            f"⚠️ 发现 {len(stale_objs)} 条疑似失效的 Objective 执行记录（不计入上方"
            "\"正在执行\"，daemon 重启/崩溃后遗留，实际早已不再运行）",
            expanded=False,
        ):
            for ex in stale_objs:
                exec_id = ex.get("execution_id", "")
                title = ex.get("title") or ex.get("objective_id", "")
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"🎯 **{title}**　`{exec_id}`　({ex.get('progress', '')})")
                if c2.button("🧹 标记为已中断", key=f"obj_mark_interrupted_{exec_id}"):
                    res = client.cancel_objective(exec_id)
                    if res and "_error" in res:
                        st.error(res["_error"])
                    else:
                        st.success("已标记为已中断（cancelled）。")
                        st.rerun()


def _render_gating_detail(client: AgentClient, gating: dict) -> None:
    """[P3] 展开 ResourceArbiter.diagnose() 的规则详情，外加 cron 通道因
    仲裁被跳过触发的累计次数（P1 上线后 cron_job_runner 也会受这三条
    规则约束，之前只有 autonomous/objective 通道受影响，这里一并展示，
    避免用户只看到"仲裁降级/暂停"却不知道 cron 侧受没受影响）。"""
    for rule in gating.get("rules", []):
        passed = rule.get("passed")
        icon = "✅" if passed else "⛔"
        label = rule.get("label", rule.get("rule", ""))
        reason = rule.get("reason", "")
        st.caption(f"{icon} {label}：{reason}")

    exec_status = client.execution_model_status() or {}
    if "_error" not in exec_status:
        skipped = exec_status.get("cron", {}).get("arbiter_skipped_count", 0)
        if skipped:
            st.caption(f"⏰ cron 通道累计因本仲裁被跳过触发 {skipped} 次")


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

    # [scheduling_unification_and_kanban_visibility_improvement_plan.md P3]
    # ResourceArbiter 的三态门控此前只在某个 Goal 的详情诊断里临时查一次
    # （见 render_kanban_tab 里针对单个 Goal 的诊断展开），用户如果不先
    # 找到一个具体 Goal，完全没有入口知道"现在自主任务为什么没在跑"。
    # 这里改成顶栏常驻展示，无论停在哪个 tab 都能看到；数据来自
    # /v1/autonomous/status 的 "gating" 字段（ResourceArbiter.diagnose()，
    # 后端已有，未改动）。
    gating = autostat.get("gating") or {}
    gating_state = gating.get("gating_state", "full")
    _GATING_BADGE = {
        "full": ("🟢", "空闲可执行"),
        "degraded": ("🟡", "降级运行"),
        "blocked": ("🔴", "已暂停"),
    }
    gating_icon, gating_label = _GATING_BADGE.get(gating_state, ("⚪", "未知"))

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
  <div class="item"><span class="label">仲裁</span> {gating_icon} {gating_label}</div>
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

    _render_scheduling_pause_control(client, autostat)

    _render_daemon_current_tasks(client, autostat)

    if gating_state != "full":
        with st.expander(
            f"{gating_icon} 资源仲裁：{gating_label}"
            + (f"（{gating.get('gating_reason')}）" if gating.get("gating_reason") else ""),
            expanded=True,
        ):
            _render_gating_detail(client, gating)

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

    _render_sentinel_panel(client)


# [kanban_perception_gaps_improvement_plan.md 方向 A] "⚠️ 系统状态哨兵"
# ——跟上面"📥 全局待办中心"是姊妹关系但语义不同：待办中心的每一条都有
# 明确的下一步操作（批准/拒绝/查看），这里聚合的是"系统状态可能不太
# 对劲，用户大概率没注意到"的信号，很多条目本身不需要用户立即做什么，
# 只是提醒留意，等它自己好转，或者用户判断后决定要不要介入。两者刻意
# 不合并（合并会让待办中心变嘈杂、稀释高优先级信息），做成顶栏一个
# 独立的可折叠区块，跟待办中心并列，不是子集关系（方向 A.0）。
def _render_sentinel_panel(client: AgentClient) -> None:
    data = client.sentinel_summary() or {}
    if "_error" in data:
        # 静默失败：哨兵面板是增强能力，不应影响顶栏其它内容的展示
        return

    total = data.get("total_count", 0)
    if not total:
        return

    with st.expander(f"⚠️ 系统状态哨兵：发现 {total} 项可能需要留意（点击展开）", expanded=True):
        cron_items = data.get("cron_jobs_with_failures") or []
        if cron_items:
            st.markdown(f"**⏰ {len(cron_items)} 个 cron job 连续失败**")
            for job in cron_items:
                enabled_badge = "🟢 已启用" if job.get("enabled") else "⚪ 已停用"
                c1, c2 = st.columns([6, 1])
                c1.caption(
                    f"{enabled_badge}　**{job.get('name')}**　"
                    f"连续失败 {job.get('consecutive_failures')} 次"
                    + (f"　·　{job.get('last_error')}" if job.get("last_error") else "")
                )
                if c2.button("跳转", key=f"sentinel_jump_cron_{job.get('job_id')}"):
                    st.session_state["cron_focus_job_id"] = job.get("job_id")
                    st.session_state["_pending_tab_switch"] = "⏰ Cron 任务"
                    st.rerun()

        obj_items = data.get("stuck_objective_steps") or []
        if obj_items:
            st.markdown(f"**🎯 {len(obj_items)} 个 Objective 正卡在重试循环里**")
            for ex in obj_items:
                st.caption(
                    f"**{ex.get('title')}**　最多重试 {ex.get('max_retry_count')} 次"
                    + (f"　·　{ex.get('last_error')}" if ex.get("last_error") else "")
                )

        qb = data.get("quarantine_backlog") or {}
        if qb.get("pending_count"):
            st.markdown(f"**📚 wiki 隔离区积压 {qb.get('pending_count')} 条**")
            st.caption("格式损坏/解析失败的 wiki 页面，可通过 CLI `quarantine` 命令查看/修复明细。")

        llm_state = data.get("llm_failover_state") or {}
        if llm_state.get("switched_from_preferred"):
            st.markdown("**🔀 LLM 已切换到备用配置**（不在首选 provider/model 上，详见\"🧠 自我状态\"tab）")

        ratio = data.get("arbitration_recent_ratio") or {}
        ratios = ratio.get("ratios") or {}
        if ratios and (ratios.get("degraded", 0) + ratios.get("blocked", 0)) > 0:
            incomplete_note = "（数据不完整，可能因为期间状态变化过于频繁）" if ratio.get("incomplete") else ""
            st.markdown(
                f"**🗓️ 过去 {ratio.get('window_days', 7):.0f} 天资源仲裁**：🟢 正常 "
                f"{ratios.get('full', 0):.0%} · 🟡 降级 {ratios.get('degraded', 0):.0%} · "
                f"🔴 阻塞 {ratios.get('blocked', 0):.0%}{incomplete_note}"
            )


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


def _respond_interaction_or_warn(client: AgentClient, req_id: str, **kwargs) -> None:
    """[BUGFIX] 之前所有按钮点击后直接 client.respond_interaction(...) + st.rerun()，
    完全没检查返回值。AgentClient 的约定是"失败不抛异常，返回带 _error 字段的 dict"
    （见 client.py::_post），所以只要后端 404/403/超时，这里之前是彻底静默的——
    页面刷新了、卡片却因为后端根本没消费这个 req_id 而继续留在 pending 列表里，
    表现为"点了发送好像没反应，只有 daemon 命令行那边回复才会消失"。
    现在检查 _error，失败时用 st.error 展示原因并保留卡片，不再盲目 rerun。
    """
    result = client.respond_interaction(req_id, **kwargs)
    if isinstance(result, dict) and result.get("_error"):
        st.error(f"回复失败（req_id={req_id}）：{result['_error']}")
        return
    st.rerun()


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
            _respond_interaction_or_warn(client, req_id, answer="/confirm")
        if gc2.button("❌ /cancel 放弃本次目标", key=f"ix_cancel_{req_id}"):
            _respond_interaction_or_warn(client, req_id, answer="/cancel")
        with st.form(f"ix_form_{req_id}", clear_on_submit=True):
            revise_text = st.text_input(
                "或输入修改意见（会据此重新生成下一版验收标准草案）",
                key=f"ix_input_{req_id}",
            )
            if st.form_submit_button("提交修改意见"):
                if revise_text.strip():
                    _respond_interaction_or_warn(client, req_id, answer=revise_text.strip())

    elif item.mode == "yes_no":
        cc1, cc2 = st.columns(2)
        if cc1.button("✅ 是", key=f"ix_yes_{req_id}"):
            _respond_interaction_or_warn(client, req_id, confirmed=True)
        if cc2.button("❌ 否", key=f"ix_no_{req_id}"):
            _respond_interaction_or_warn(client, req_id, confirmed=False)

    elif item.mode == "choices":
        for idx, opt in enumerate(item.options):
            if st.button(f"{idx + 1}. {opt}", key=f"ix_opt_{req_id}_{idx}"):
                _respond_interaction_or_warn(client, req_id, choice_index=idx)

    else:  # freeform
        with st.form(f"ix_form_{req_id}", clear_on_submit=True):
            free_text = st.text_input("回复", key=f"ix_free_{req_id}")
            if st.form_submit_button("发送"):
                _respond_interaction_or_warn(client, req_id, answer=free_text)

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
            send = c1.form_submit_button("发送 ➤", width='stretch')
            interrupt = c2.form_submit_button("⏹ 中断当前任务", width='stretch')

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
            width='stretch',
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
        if bc2.button("🔄 刷新列表", key="sessions_refresh_banner", width='stretch'):
            st.session_state["_sessions_baseline_ids"] = current_ids
            st.rerun()


def render_sessions_tab(client: AgentClient):
    st.markdown("#### 🗂️ 会话管理")
    _render_sessions_change_banner(client)

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("➕ 新建会话", width='stretch'):
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
        # 注意：这里的"📎已固定"是看板本地"并排对比区"概念（见下方
        # _render_pinned_sessions_panel），跟后端 session.pinned（cleanup
        # 清理保护）是两码事，故用"🔒已保护"区分，避免用户混淆两个"pin"。
        protected_mark = " 🔒已保护" if s.get("pinned") else ""
        with st.expander(f"🗂️ {sid}{current_mark}{bound_mark}{pinned_mark}{protected_mark}　·　轮次 {s.get('turns', '?')}　·　{s.get('age', s.get('updated_at',''))}"):
            st.json(s, expanded=False)
            cc1, cc2, cc3, cc4, cc5 = st.columns(5)
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
            is_protected = bool(s.get("pinned"))
            protect_label = "🔓 取消清理保护" if is_protected else "🔒 保护（防清理）"
            if cc4.button(protect_label, key=f"protect_{sid}",
                           help="开启后，批量清理（下方\"🧹 批量清理旧会话\"）永远不会删除这个 session"):
                res = client.unpin_session(sid) if is_protected else client.pin_session(sid)
                if res and "_error" not in res:
                    st.success("已取消保护" if is_protected else "已加入清理保护")
                else:
                    st.error((res or {}).get("_error", "操作失败"))
                st.rerun()
            if cc5.button("🗑️ 删除", key=f"del_{sid}"):
                client.delete_session(sid)
                st.rerun()

    pc1, pc2, pc3 = st.columns([1, 2, 1])
    if pc1.button("⬅️ 上一页", disabled=(page <= 0), width='stretch'):
        st.session_state["sessions_page"] = page - 1
        st.rerun()
    pc2.markdown(
        f"<div style='text-align:center'>第 {page + 1} / {total_pages} 页　"
        f"（共 {total} 个会话）</div>",
        unsafe_allow_html=True,
    )
    if pc3.button("下一页 ➡️", disabled=(page >= total_pages - 1), width='stretch'):
        st.session_state["sessions_page"] = page + 1
        st.rerun()

    _render_session_cleanup_panel(client)
    _render_pinned_sessions_panel_entry(client)


def _render_session_cleanup_panel(client: AgentClient) -> None:
    """[Session 清理功能看板集成] 批量清理长期不用的旧 session。

    规则与 CLI `/session cleanup`（见 evolution/session_cleanup.py、
    next_doc/session_cleanup_design.md）完全一致，都是走同一套后端判定：
    当前 session / 🔒已保护 / goal 仍在跑的 session / 最近 N 个 / 最近 N 天
    内永远不删；其余候选删除的 session 还要看是否已抽取过知识（或内容太少
    不需要抽取）。这里只是给判定结果加一层看板 UI：先"预览"（dry-run）
    看看会删哪些，确认无误再"确认执行"。

    额外支持"含孤儿目录"（勾选后一并扫描/清理有目录、没 meta.json 的残留
    目录，见 session_cleanup.py::scan_orphan_session_dirs），默认关闭。
    """
    st.markdown("---")
    with st.expander("🧹 批量清理旧会话（Session Cleanup）"):
        st.caption(
            "自动清理长期不用的旧 session，释放磁盘空间。规则：当前会话 / "
            "🔒已保护 / 目标仍在进行中的会话 / 最近 N 个 / 最近 N 天内的会话"
            "永远不会被清理。owner 权限。"
        )
        cc1, cc2, cc3 = st.columns(3)
        keep_days = cc1.number_input("保留最近 N 天", min_value=0, value=30, step=1,
                                      key="cleanup_keep_days",
                                      help="更新时间在这天数以内的会话永远保留")
        keep_count = cc2.number_input("保留最近 N 个", min_value=0, value=20, step=1,
                                       key="cleanup_keep_count",
                                       help="按更新时间倒序，最新的这么多个会话永远保留")
        extract_first = cc3.checkbox(
            "先补跑知识抽取", value=False, key="cleanup_extract_first",
            help="候选删除但还没抽取过知识的会话，删除前先离线抽取一次知识"
                 "（会调用 LLM，耗时更长；不勾选则这类会话本次跳过、不删）",
        )

        oc1, oc2 = st.columns(2)
        include_orphans = oc1.checkbox(
            "含孤儿目录", value=False, key="cleanup_include_orphans",
            help="额外扫描/清理磁盘上「有目录、没 meta.json」的孤儿目录——"
                 "一轮对话没跑完就中断（daemon 重启/被杀/cron 子 agent 提前"
                 "失败）时会留下这种残留目录，普通的会话列表和清理都看不到它们，"
                 "需要单独勾选才会处理。",
        )
        orphan_min_age_hours = oc2.number_input(
            "孤儿目录最小年龄（小时）", min_value=0.0, value=6.0, step=1.0,
            key="cleanup_orphan_min_age_hours", disabled=not include_orphans,
            help="安全网：目录创建后要等一轮对话跑完才会写 meta.json，太新的"
                 "孤儿目录很可能是正在进行中的第一轮，不足这个年龄不会被判定为孤儿",
        )

        bc1, bc2 = st.columns(2)
        preview_key = "session_cleanup_preview"
        if bc1.button("🔍 预览（dry-run，不会真的删除）", width='stretch'):
            res = client.cleanup_sessions(
                dry_run=True, keep_recent_days=float(keep_days),
                keep_recent_count=int(keep_count), extract_first=extract_first,
                include_orphans=include_orphans,
                orphan_min_age_hours=float(orphan_min_age_hours),
            )
            if res and "_error" not in res:
                st.session_state[preview_key] = res
            else:
                st.session_state.pop(preview_key, None)
                st.error((res or {}).get("_error", "预览失败"))

        preview = st.session_state.get(preview_key)
        if preview:
            st.info(preview.get("summary", ""))
            deleted = preview.get("deleted", [])
            skipped = preview.get("skipped_pending_extraction", [])
            failed = preview.get("failed", [])
            if deleted:
                st.markdown(f"**将删除（{len(deleted)}）**")
                st.dataframe(
                    [{"session_id": i["session_id"], "title": i["title"],
                      "updated_at": i["updated_at"], "turns": i["turns"],
                      "reason": i["reason"]} for i in deleted],
                    width='stretch', hide_index=True,
                )
            if skipped:
                st.markdown(f"**待抽取，本次跳过（{len(skipped)}）**")
                st.dataframe(
                    [{"session_id": i["session_id"], "title": i["title"],
                      "reason": i["reason"]} for i in skipped],
                    width='stretch', hide_index=True,
                )
            if failed:
                st.markdown(f"**失败（{len(failed)}）**")
                st.dataframe(
                    [{"session_id": i["session_id"], "title": i["title"],
                      "reason": i["reason"]} for i in failed],
                    width='stretch', hide_index=True,
                )

            orphan_deleted = preview.get("orphan_deleted", [])
            orphan_failed = preview.get("orphan_failed", [])
            if preview.get("orphan_total_scanned"):
                st.markdown("---")
                orphan_mb = sum(i.get("size_bytes", 0) for i in orphan_deleted) / 1024 / 1024
                st.info(
                    f"孤儿目录：共扫描 {preview['orphan_total_scanned']} 个，"
                    f"保留 {preview.get('orphan_kept_count', 0)} 个，"
                    f"将删除 {len(orphan_deleted)} 个（约 {orphan_mb:.1f} MB）。"
                )
                if orphan_deleted:
                    st.markdown(f"**孤儿目录 · 将删除（{len(orphan_deleted)}）**")
                    st.dataframe(
                        [{"dir_name": i["dir_name"], "last_activity": i["last_activity"],
                          "size_mb": round(i.get("size_bytes", 0) / 1024 / 1024, 2),
                          "reason": i["reason"]} for i in orphan_deleted],
                        width='stretch', hide_index=True,
                    )
                if orphan_failed:
                    st.markdown(f"**孤儿目录 · 失败（{len(orphan_failed)}）**")
                    st.dataframe(
                        [{"dir_name": i["dir_name"], "reason": i["reason"]}
                         for i in orphan_failed],
                        width='stretch', hide_index=True,
                    )

            has_anything_to_delete = bool(deleted or orphan_deleted)
            if has_anything_to_delete:
                confirm = bc2.checkbox("我已确认以上列表，执行删除", key="cleanup_confirm")
                if st.button("⚠️ 确认执行清理（不可撤销）", disabled=not confirm,
                              width='stretch', type="primary"):
                    res = client.cleanup_sessions(
                        dry_run=False, keep_recent_days=float(keep_days),
                        keep_recent_count=int(keep_count), extract_first=extract_first,
                        include_orphans=include_orphans,
                        orphan_min_age_hours=float(orphan_min_age_hours),
                    )
                    if res and "_error" not in res:
                        st.success(res.get("summary", "清理完成"))
                        st.session_state.pop(preview_key, None)
                        st.session_state.pop("cleanup_confirm", None)
                        st.rerun()
                    else:
                        st.error((res or {}).get("_error", "清理执行失败"))
            else:
                st.caption("没有可删除的会话，无需执行。")


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


def _render_objective_execution_detail(client: AgentClient, execution: dict, key_prefix: str = "") -> None:
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
        "running": "🏃 执行中", "paused": "⏸️ 已暂停（资源受限）",
        "paused_for_fairness": "⏸️ 已暂停（公平调度让出）",
        "paused_by_user": "📌 已暂停（用户）",
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
        edited_tag = " ✏️已编辑" if s.get("edited_by_user") else ""
        with st.expander(f"🔍 查看详情 · 步骤 {step_idx + 1 if isinstance(step_idx, int) else '?'}{edited_tag}"):
            trace = client.objective_step_trace(exec_id, step_idx) if exec_id and isinstance(step_idx, int) else None
            if not trace or "_error" in (trace or {}):
                st.caption((trace or {}).get("_error", "暂时无法获取执行细节。"))
            else:
                if trace.get("from_raw_history"):
                    st.caption("ℹ️ 该步骤记录已被压缩，以下内容从压缩前的原始日志里找回。")
                entries = trace.get("entries") or []
                if not entries:
                    st.caption(trace.get("note") or "没有可展示的执行细节。")
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

            # [daemon_stability_and_ux_improvement_plan.md P2-10] "编辑 step
            # 产出并继续"——只对已完成（done）的 step 开放，且不重新执行该
            # step，只是把修正后的 result_summary/artifacts 写回去，后续
            # step 会读到修正后的版本继续。与上方的 reset-step（若该 UI
            # 存在）互补：这一步基本做对了、只是描述有小问题时用这个，不需要
            # 整步重跑模型。
            if s.get("status") == "done" and exec_id and isinstance(step_idx, int):
                st.divider()
                with st.form(key=f"{key_prefix}obj_edit_step_{exec_id}_{step_idx}"):
                    st.caption("✏️ 编辑此步骤的产出（不会重新执行这一步，后续步骤将基于修正后的结果继续）")
                    new_summary = st.text_area(
                        "结果摘要", value=s.get("result_summary", ""), height=100,
                        key=f"{key_prefix}obj_edit_summary_{exec_id}_{step_idx}",
                    )
                    new_artifacts_text = st.text_input(
                        "产出文件路径（逗号分隔，留空表示不修改）",
                        value=", ".join(s.get("artifacts") or []),
                        key=f"{key_prefix}obj_edit_artifacts_{exec_id}_{step_idx}",
                    )
                    if st.form_submit_button("💾 保存并用于后续步骤"):
                        new_artifacts = [p.strip() for p in new_artifacts_text.split(",") if p.strip()] \
                            if new_artifacts_text.strip() else None
                        res = client.edit_objective_step(
                            exec_id, step_idx,
                            result_summary=new_summary if new_summary != s.get("result_summary", "") else None,
                            artifacts=new_artifacts,
                        )
                        if res and "_error" in res:
                            st.error(res["_error"])
                        else:
                            st.toast("✏️ 已保存，下一步将基于修正后的结果继续", icon="✏️")
                            st.rerun()

    if not exec_id or ex_status in ("completed", "cancelled"):
        return

    # [daemon_stability_and_ux_improvement_plan.md P1-11] 干预操作的一致
    # 反馈——终止/重试/暂停/恢复/插话此前"点了按钮，后台生效"缺少明确的即时
    # 反馈，用户容易因为看不到反馈而重复点击。这里给每个操作补一句
    # st.toast() 即时确认（跨 st.rerun() 仍会展示，不需要额外的 session_state
    # 搬运），对已经有服务端过渡态标记的暂停操作（pause_requested）额外保留
    # 持续显示的 caption，直到状态真正落地。
    if execution.get("pause_requested"):
        st.caption("⏸️ 暂停请求已发送，将在当前步骤完成后生效……")
    b1, b2, b3, b4 = st.columns(4)
    if ex_status in ("running", "paused", "paused_for_fairness", "paused_by_user", "failed", "pending"):
        if b1.button("🛑 终止", key=f"{key_prefix}obj_cancel_{exec_id}"):
            res = client.cancel_objective(exec_id)
            if res and "_error" in res:
                st.error(res["_error"])
            else:
                st.toast("🛑 终止请求已发送，正在停止该 Objective……", icon="🛑")
            st.rerun()
    if ex_status in ("running", "failed"):
        if b2.button("🔁 重试当前步", key=f"{key_prefix}obj_retry_{exec_id}"):
            res = client.retry_objective(exec_id)
            if res and "_error" in res:
                st.error(res["_error"])
            else:
                st.toast("🔁 重试请求已发送，正在重新提交当前步骤……", icon="🔁")
            st.rerun()
    # [daemon_stability_and_ux_improvement_plan.md P1-5] "⏸️ 暂停"/"▶️ 恢复"
    # 与终止/重试/插话并列——暂停是"临时叫停，之后原样恢复"，与"终止"
    # （彻底结束）语义不同，填补此前只有"终止/重来"没有中间态的缺口。
    if ex_status in ("running", "paused_for_fairness") and not execution.get("pause_requested"):
        if b4.button("⏸️ 暂停", key=f"{key_prefix}obj_pause_{exec_id}"):
            res = client.pause_objective(exec_id)
            if res and "_error" in res:
                st.error(res["_error"])
            else:
                st.toast("⏸️ 暂停请求已发送，将在当前步骤完成后生效……", icon="⏸️")
            st.rerun()
    elif ex_status == "paused_by_user":
        if b4.button("▶️ 恢复", key=f"{key_prefix}obj_resume_{exec_id}"):
            res = client.resume_objective(exec_id)
            if res and "_error" in res:
                st.error(res["_error"])
            else:
                st.toast("▶️ 恢复请求已发送，正在从断点继续……", icon="▶️")
            st.rerun()
    with b3.popover("💬 插话"):
        guidance = st.text_area("补充说明（下次提交当前步骤时会附带这段话）",
                                 key=f"{key_prefix}obj_guidance_text_{exec_id}", height=80)
        if st.button("发送", key=f"{key_prefix}obj_guidance_send_{exec_id}"):
            if guidance.strip():
                res = client.inject_objective_guidance(exec_id, guidance.strip())
                if res and "_error" in res:
                    st.error(res["_error"])
                else:
                    st.success("✏️ 已记录，将在下次提交该步骤时附带这段说明")
                    st.toast("✏️ 插话已保存，下一步将附带这段说明", icon="✏️")


def _render_goal_output_manifests(client: AgentClient, goal_id: str, key_prefix: str = "", limit: int = 5) -> None:
    """读 .agent/daemon_run_outputs/goals/<goal_id>/latest.json 找到最新一轮目录名，倒序展开
    最近 `limit` 轮的 manifest.json，只列文件名 + 备注，不做文件预览/下载。
    目录/文件不存在（这一轮改造上线之前的历史 Goal，或还没跑过一轮）时
    静默不展示，不报错打扰用户。"""
    base = f".agent/daemon_run_outputs/goals/{goal_id}"
    try:
        latest_resp = client.fs_read(f"{base}/latest.json")
    except Exception:
        return
    content = (latest_resp or {}).get("content") if isinstance(latest_resp, dict) else None
    if not content:
        return
    try:
        latest = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return
    latest_dir = latest.get("latest_dir")
    if not latest_dir:
        return

    # [goal_cron_output_directory_convention_plan.md §5 开放问题 3] recurring
    # Goal 用 "cycle_%04d"、一次性 Goal 用 "run_%04d"，这里从 latest_dir 本身
    # 反推前缀，不再硬编码 "cycle_"，两种命名都能正确倒推出历史轮次/子任务
    # 目录名列表。
    dir_prefix, _, num_str = str(latest_dir).rpartition("_")
    try:
        cycle_no = int(num_str) if dir_prefix else None
    except ValueError:
        cycle_no, dir_prefix = None, ""

    with st.expander(f"📂 查看产出（最新：{latest_dir}）"):
        dir_names = (
            [f"{dir_prefix}_{i:04d}" for i in range(cycle_no, max(cycle_no - limit, 0), -1)]
            if cycle_no else [latest_dir]
        )
        for dir_name in dir_names:
            try:
                m_resp = client.fs_read(f"{base}/{dir_name}/manifest.json")
                manifest = json.loads((m_resp or {}).get("content") or "{}")
            except Exception:
                continue
            if not manifest:
                continue
            st.caption(f"**{dir_name}** · {manifest.get('status', '')} · {manifest.get('task_summary', '')}")
            for a in manifest.get("artifacts") or []:
                path = a.get("path", "") if isinstance(a, dict) else str(a)
                if path:
                    st.markdown(f"&nbsp;&nbsp;- `{path}`", unsafe_allow_html=True)
            if manifest.get("progress_note"):
                st.caption(f"　备注：{manifest['progress_note']}")


def _render_execution_spec_summary(spec: dict) -> None:
    """把 execution_spec dict 渲染成可读摘要（看板本地实现，不依赖后端渲染
    出的文本，方便反馈迭代后立即重绘）。"""
    deliverables = spec.get("deliverables") or []
    handoff = spec.get("handoff_fields") or []
    subdirs = spec.get("sub_directories") or []
    criteria = spec.get("per_cycle_criteria") or []
    overall = spec.get("overall_completion_criteria") or []
    constraints = spec.get("special_constraints") or []
    if deliverables:
        st.markdown("**产出物：**")
        for d in deliverables:
            req = "每轮必需" if d.get("required_every_cycle", True) else "可选"
            st.caption(f"- {d.get('name', '')}（{req}）：{d.get('description', '')}")
    if handoff:
        st.markdown("**跨轮传递：**")
        for h in handoff:
            st.caption(f"- `{h.get('key', '')}`：{h.get('description', '')}")
    if subdirs:
        st.markdown("**子目录：**")
        for s in subdirs:
            st.caption(f"- {s.get('name', '')}：{s.get('purpose', '')}")
    if criteria:
        st.markdown("**每轮完成标准：**")
        for c in criteria:
            st.caption(f"- [{c.get('verification_method', 'manual_review')}] {c.get('text', '')}")
    if overall:
        st.markdown("**整体完成标准：**")
        for c in overall:
            st.caption(f"- [{c.get('verification_method', 'manual_review')}] {c.get('text', '')}")
    if constraints:
        st.markdown("**特殊约束：**")
        for s in constraints:
            st.caption(f"- {s}")
    if not (deliverables or handoff or subdirs or criteria or overall or constraints):
        st.caption("（当前草稿全部字段为空，等价于沿用默认通用行为，不额外拼接任何 prompt 文字）")


# [goal_execution_spec_generation_plan.md §6.2 / implementation_record.md
# §12 后续建议顺序第 1 条"差异高亮"] `revise()`/"从模板重新起草"整段
# 覆盖草稿之后，前端对比新旧两份 JSON 生成的差异标注——不需要额外 LLM
# 调用，纯本地字符串/字典比较。每个 section 用一个"识别 key"匹配同一条
# 目在新旧草稿里是否还存在：deliverables 按 name、handoff_fields 按
# key、sub_directories 按 name、两种 criteria 按 text、special_
# constraints（纯字符串列表）按值本身。
_SPEC_DIFF_SECTIONS = [
    ("deliverables", "产出物", "name"),
    ("handoff_fields", "跨轮传递", "key"),
    ("sub_directories", "子目录", "name"),
    ("per_cycle_criteria", "每轮完成标准", "text"),
    ("overall_completion_criteria", "整体完成标准", "text"),
]


def _compute_spec_diff(old_spec: dict, new_spec: dict) -> dict:
    """返回 `{field_name: {"added": [...], "removed": [...], "changed":
    [(old_item, new_item), ...]}}`，只包含真正有差异的 section（section
    内容完全相同则不出现在结果里，调用方据此判断"这次改了什么"）。"""
    diff: dict = {}
    for field_name, _label, key_field in _SPEC_DIFF_SECTIONS:
        old_items = old_spec.get(field_name) or []
        new_items = new_spec.get(field_name) or []
        old_by_key = {item.get(key_field, ""): item for item in old_items}
        new_by_key = {item.get(key_field, ""): item for item in new_items}
        added = [new_by_key[k] for k in new_by_key if k not in old_by_key]
        removed = [old_by_key[k] for k in old_by_key if k not in new_by_key]
        changed = [
            (old_by_key[k], new_by_key[k])
            for k in new_by_key
            if k in old_by_key and old_by_key[k] != new_by_key[k]
        ]
        if added or removed or changed:
            diff[field_name] = {"added": added, "removed": removed, "changed": changed}

    old_constraints = list(old_spec.get("special_constraints") or [])
    new_constraints = list(new_spec.get("special_constraints") or [])
    if old_constraints != new_constraints:
        c_added = [c for c in new_constraints if c not in old_constraints]
        c_removed = [c for c in old_constraints if c not in new_constraints]
        if c_added or c_removed:
            diff["special_constraints"] = {"added": c_added, "removed": c_removed, "changed": []}
    return diff


def _render_spec_diff(diff: dict) -> None:
    """把 `_compute_spec_diff()` 的结果渲染成"➕ 新增 / ➖ 删除 / ✏️ 改写"
    三类标注，用户不用重新通读整份规范去猜"这次改了什么"（方案 §6.1 最后
    一段"通读成本过高会让用户倾向于直接确认"要解决的问题）。"""
    if not diff:
        st.caption("这次重新生成后，各字段内容与上一版完全一致（可能只是措辞层面的重新生成，或本来就锁定了全部字段）。")
        return
    section_labels = {f: label for f, label, _ in _SPEC_DIFF_SECTIONS}
    section_labels["special_constraints"] = "特殊约束"
    text_field = {f: kf for f, _, kf in _SPEC_DIFF_SECTIONS}
    for field_name, d in diff.items():
        label = section_labels.get(field_name, field_name)
        st.markdown(f"**{label}：**")
        key_field = text_field.get(field_name)
        for item in d["added"]:
            text = item if isinstance(item, str) else item.get(key_field, "")
            st.markdown(f"<span style='color:#1a7f37;'>➕ 新增：{_esc_html(str(text))}</span>", unsafe_allow_html=True)
        for item in d["removed"]:
            text = item if isinstance(item, str) else item.get(key_field, "")
            st.markdown(
                f"<span style='color:#cf222e;text-decoration:line-through;'>➖ 删除：{_esc_html(str(text))}</span>",
                unsafe_allow_html=True,
            )
        for old_item, new_item in d.get("changed", []):
            text = new_item.get(key_field, "") if isinstance(new_item, dict) else str(new_item)
            st.markdown(f"<span style='color:#9a6700;'>✏️ 改写：{_esc_html(str(text))}</span>", unsafe_allow_html=True)


def _render_goal_execution_spec_widget(
    client: "AgentClient", goal_id: str, key_prefix: str = "",
    on_confirm_extra: Optional[Callable[[], None]] = None,
    goal_title: str = "", goal_description: str = "",
) -> bool:
    """返回 True 表示当前已经是"已确认"状态（调用方可据此决定要不要再提示
    用户"建议先确认规范"之类的文案，不影响主流程是否能继续）。"""
    draft_key = f"{key_prefix}ges_draft_{goal_id}"
    path_key = f"{key_prefix}ges_path_{goal_id}"
    # [implementation_record.md §12 后续建议顺序第 1 条"差异高亮"] 每次
    # `revise()`/"从模板重新起草"整段覆盖草稿前，把覆盖前的版本存进这里；
    # 渲染时如果存在就对比算出差异标注，展示一次后即清空（不跨会话持久化
    # ——差异只对"刚刚这一次改动"有意义，下次改动会覆盖掉上一次的对比
    # 基线）。
    diff_key = f"{key_prefix}ges_diffprev_{goal_id}"

    if draft_key not in st.session_state:
        existing = client.get_execution_spec(goal_id)
        if existing and not existing.get("_error") and existing.get("spec"):
            st.session_state[draft_key] = existing["spec"]

    spec = st.session_state.get(draft_key)

    # [goal_execution_spec_generation_plan.md §3 输入源 1 /
    # implementation_record.md §7.5 未实施清单第 2 条] 展示这份草稿最近一次
    # 生成/修订时实际走的路径（llm/agent），让用户知道有没有读取过项目内容。
    _path_label = {"llm": "纯 LLM（未读取项目内容）", "agent": "只读探索 Agent（读取过项目内容）"}
    effective_path = st.session_state.get(path_key)
    if effective_path:
        st.caption(f"🧭 上次生成走的路径：{_path_label.get(effective_path, effective_path)}")

    if spec and spec.get("confirmed"):
        st.success(f"✅ 执行规范已确认（第 {spec.get('version', 1)} 版），下次触发即生效。")
        with st.expander("查看当前执行规范", expanded=False):
            _render_execution_spec_summary(spec)
            if st.button("♻️ 生成新草稿（重新想一遍细节）", key=f"{key_prefix}ges_regen_{goal_id}"):
                del st.session_state[draft_key]
                st.session_state.pop(path_key, None)
                st.session_state.pop(diff_key, None)
                st.rerun()
        return True

    if spec is None:
        tpl_res = client.execution_spec_templates(goal_title=goal_title, goal_description=goal_description) or {}
        templates = tpl_res.get("templates", []) if not tpl_res.get("_error") else []
        suggested_id = tpl_res.get("suggested_template_id") if not tpl_res.get("_error") else None
        tpl_labels = ["（不使用模板，完全从零生成）"] + [f"{t['id']} · {t['name']}" for t in templates]
        # [goal_execution_spec_generation_plan.md §7 末段] 关键词粗略匹配命中
        # 某个模板时默认预选它，用户仍然可以在下拉框里改选或选"不用模板"。
        default_idx = 0
        if suggested_id:
            for i, t in enumerate(templates):
                if t.get("id") == suggested_id:
                    default_idx = i + 1  # +1 因为第 0 项是"不使用模板"
                    break
        gcol1, gcol2 = st.columns([2, 1])
        tpl_choice = gcol1.selectbox(
            "起草方式" + ("（已根据 Goal 描述自动推荐）" if suggested_id else ""),
            tpl_labels, index=default_idx, key=f"{key_prefix}ges_tpl_{goal_id}",
        )
        # [goal_execution_spec_generation_plan.md §3 输入源 3] 只在选了模板
        # （代表这不是"➕ 新建目标"里全新创建、还没跑过一轮的 Goal）时才
        # 展示这个勾选框——刚创建、从未执行过的 Goal 打开这个开关也只会
        # 拿到空历史，徒增一次无意义的选项；已有历史的 Goal 才谈得上
        # "从上一轮执行记录反推"。REST 层已支持这个参数，这里只是补上
        # 看板侧此前遗漏的开关（见实施记录未实施清单第 5 条）。
        from_history = False
        if key_prefix != "newgoal_":
            from_history = st.checkbox(
                "从最近一轮的执行记录反推草稿内容（该 Goal 已经跑过至少一轮时有效，"
                "否则等同于不勾选）",
                value=False, key=f"{key_prefix}ges_fromhist_{goal_id}",
            )
        # [goal_execution_spec_generation_plan.md §3 输入源 1 /
        # implementation_record.md §7.5/§9 未实施清单第 2 条] 单次覆盖
        # `builder_mode`，不改配置文件。默认"跟随配置默认"（不传 mode，
        # 服务端回退配置文件里的 goal_execution_spec.builder_mode，默认
        # "auto"）。
        mode_labels = {
            "": "跟随配置默认（当前配置的 builder_mode，通常是 auto）",
            "auto": "自动判断（关键词规则命中项目相关诉求才起 Agent）",
            "llm": "纯 LLM（不读取项目内容，速度快）",
            "agent": "只读探索 Agent（先看一眼项目再生成，更贴合实际）",
        }
        mode_choice = st.selectbox(
            "生成路径", list(mode_labels.keys()), format_func=lambda k: mode_labels[k],
            index=0, key=f"{key_prefix}ges_mode_{goal_id}",
        )
        if gcol2.button("📋 生成执行规范草稿", key=f"{key_prefix}ges_gen_{goal_id}"):
            template_id = tpl_choice.split(" · ")[0] if tpl_choice != tpl_labels[0] else ""
            res = client.generate_execution_spec(
                goal_id, template_id=template_id, from_history=from_history, mode=mode_choice,
            )
            if res and res.get("_error"):
                st.error(f"生成失败：{res['_error']}")
            else:
                st.session_state[draft_key] = res.get("spec")
                st.session_state[path_key] = res.get("effective_path")
                st.rerun()
        return False

    # 有草稿、未确认：展示摘要 + 字段级锁定 + 反馈迭代 + 确认/放弃
    if spec.get("generation_error"):
        st.warning(f"⚠️ 上次生成存在问题（已保存为空草稿，可补充意见重新生成）：{spec['generation_error']}")
    st.caption(f"📝 执行规范草稿（第 {spec.get('version', 1)} 版，未确认，不影响执行）")
    _render_execution_spec_summary(spec)

    # [goal_execution_spec_generation_plan.md §6.1 最后一段 / implementation_
    # record.md §12 后续建议顺序第 1 条"差异高亮"] 上一次操作（补充意见
    # 重新生成 / 从模板重新起草）覆盖草稿之后，展示这次相比上一版改了
    # 什么，避免用户为了确认"没有意外改动"重新通读整份规范。
    diff_prev = st.session_state.get(diff_key)
    if diff_prev is not None:
        with st.expander("🔍 与上一版的差异", expanded=True):
            _render_spec_diff(_compute_spec_diff(diff_prev, spec))
            if st.button("知道了，收起差异", key=f"{key_prefix}ges_diffack_{goal_id}"):
                st.session_state.pop(diff_key, None)
                st.rerun()

    lock_sections = [
        ("deliverables", "产出物"), ("handoff_fields", "跨轮传递"),
        ("sub_directories", "子目录"), ("per_cycle_criteria", "每轮标准"),
        ("special_constraints", "特殊约束"),
    ]
    prior_locked = set(spec.get("locked_fields") or [])
    lock_cols = st.columns(len(lock_sections))
    locked_now = []
    for (field_name, label), col in zip(lock_sections, lock_cols):
        if col.checkbox(f"🔒{label}", key=f"{key_prefix}ges_lock_{field_name}_{goal_id}",
                         value=field_name in prior_locked):
            locked_now.append(field_name)

    mode_labels = {
        "": "跟随配置默认",
        "auto": "自动判断",
        "llm": "纯 LLM",
        "agent": "只读探索 Agent",
    }
    revise_mode = st.selectbox(
        "本次重新生成走的路径", list(mode_labels.keys()), format_func=lambda k: mode_labels[k],
        index=0, key=f"{key_prefix}ges_revise_mode_{goal_id}",
    )
    with st.form(f"{key_prefix}ges_revise_form_{goal_id}"):
        feedback = st.text_area("补充意见（提交后，未勾选🔒锁定的部分会据此重新生成，已锁定的原样保留）", height=60)
        rcol1, rcol2, rcol3 = st.columns(3)
        revise_click = rcol1.form_submit_button("🔄 补充意见重新生成")
        confirm_click = rcol2.form_submit_button("✅ 确认使用此规范")
        skip_click = rcol3.form_submit_button("❌ 放弃草稿")

    if revise_click:
        if not feedback.strip():
            st.error("补充意见不能为空")
        else:
            res = client.revise_execution_spec(goal_id, feedback.strip(), locked_fields=locked_now, mode=revise_mode)
            if res and res.get("_error"):
                st.error(f"重新生成失败：{res['_error']}")
            else:
                st.session_state[diff_key] = spec
                st.session_state[draft_key] = res.get("spec")
                st.session_state[path_key] = res.get("effective_path")
                st.rerun()
    elif confirm_click:
        res = client.confirm_execution_spec(goal_id)
        if res and res.get("_error"):
            st.error(f"确认失败：{res['_error']}")
        else:
            st.session_state[draft_key] = res.get("spec")
            st.session_state.pop(diff_key, None)
            if on_confirm_extra:
                on_confirm_extra()
            st.rerun()
    elif skip_click:
        del st.session_state[draft_key]
        st.session_state.pop(path_key, None)
        st.session_state.pop(diff_key, None)
        st.rerun()

    # [goal_execution_spec_generation_plan.md §6.1 / 实施记录未实施清单
    # 第 1 项] "从模板重新起草"：不必先放弃当前草稿再重新走一遍"生成"
    # 入口——这里直接调用同一个 generate 接口，用选中的模板整段覆盖当前
    # 草稿（`build_draft()` 固定生成"第 1 版"，等价于推倒重来，不是在
    # 当前版本号上累加、也不是"合并"；真想保留已经改好的部分，应该用
    # 上面的「🔄 补充意见重新生成」+ 字段锁定，而不是这个按钮）。
    with st.expander("📄 从模板重新起草（会整段覆盖当前草稿，不会保留已改内容）"):
        tpl_res2 = client.execution_spec_templates(goal_title=goal_title, goal_description=goal_description) or {}
        templates2 = tpl_res2.get("templates", []) if not tpl_res2.get("_error") else []
        tpl_labels2 = ["（不使用模板，完全从零生成）"] + [f"{t['id']} · {t['name']}" for t in templates2]
        rtcol1, rtcol2 = st.columns([2, 1])
        tpl_choice2 = rtcol1.selectbox("选择模板", tpl_labels2, key=f"{key_prefix}ges_retpl_{goal_id}")
        if rtcol2.button("♻️ 用此模板重新起草", key=f"{key_prefix}ges_regen_tpl_{goal_id}"):
            template_id2 = tpl_choice2.split(" · ")[0] if tpl_choice2 != tpl_labels2[0] else ""
            res = client.generate_execution_spec(goal_id, template_id=template_id2)
            if res and res.get("_error"):
                st.error(f"重新起草失败：{res['_error']}")
            else:
                st.session_state[diff_key] = spec
                st.session_state[draft_key] = res.get("spec")
                st.session_state[path_key] = res.get("effective_path")
                st.rerun()

    return False


_PHASE_LABELS = {
    "explore": "🔍 探索",
    "converge": "⚖️ 收敛",
    "running": "✅ 长期执行",
    "tidy": "🧹 整理",
    "auto": "🤖 自动",
}


def _render_goal_execution_phase_widget(client: AgentClient, goal_id: str, key_prefix: str = "") -> None:
    """[goal_execution_phase_improvement_plan.md Stage C] Goal 卡片上的执行
    阶段徽章 + 手动切换折叠区。默认折叠展示当前阶段（含 auto 模式下系统
    自动判定出的 stability_score），展开后允许用户切换 explore/converge/
    running/tidy/auto 五态，非 auto 默认隐式锁定（与 CLI `/agent goals
    phase set` 行为一致），并提供解锁按钮。

    读取失败（goal_id 为空、接口异常）时静默不展示，不影响卡片其他内容。
    """
    if not goal_id:
        return
    resp = client.get_execution_phase(goal_id)
    if not resp or resp.get("_error"):
        return
    phase = resp.get("phase") or {}
    mode = phase.get("mode", "auto")
    locked = phase.get("locked", False)
    score = phase.get("stability_score", 0.0)
    label = _PHASE_LABELS.get(mode, mode)
    lock_tag = "🔒" if locked else ""

    with st.expander(f"{label} {lock_tag}　执行阶段", expanded=False):
        st.caption(f"stability_score: {score:.2f}　·　已在当前阶段 {phase.get('cycles_in_mode', 0)} 轮")
        history = phase.get("mode_history") or []
        if history:
            recent = history[-3:]
            for m in recent:
                st.caption(f"　{m.get('from','')} → {m.get('to','')}（{m.get('reason','')}）")

        mode_options = ["auto", "explore", "converge", "running", "tidy"]
        current_idx = mode_options.index(mode) if mode in mode_options else 0
        col1, col2 = st.columns([3, 1])
        new_mode = col1.selectbox(
            "切换阶段", mode_options, index=current_idx,
            format_func=lambda m: _PHASE_LABELS.get(m, m),
            key=f"{key_prefix}phase_select_{goal_id}",
        )
        if col2.button("应用", key=f"{key_prefix}phase_apply_{goal_id}"):
            res = client.set_execution_phase(goal_id, new_mode)
            if res and res.get("_error"):
                st.error(f"切换阶段失败：{res['_error']}")
            else:
                st.rerun()
        if locked:
            if st.button("🔓 解除锁定（交回自动判定）", key=f"{key_prefix}phase_unlock_{goal_id}"):
                res = client.unlock_execution_phase(goal_id)
                if res and res.get("_error"):
                    st.error(f"解除锁定失败：{res['_error']}")
                else:
                    st.rerun()


# [goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md Stage 1/2/3 看板
# 集成] 参数值输入控件——跟白名单参数一一对应，而不是让用户填自由文本
# `param=value`：CLI 场景下打错参数名/值格式只是重新敲一遍命令，看板场景
# 里"表单提交出错却不知道错哪"体验更差，所以每个参数配一个贴合取值范围的
# 控件（下拉框/滑块/文本域），从交互层面排除掉"参数名拼错""execution_phase
# 填了白名单外的字符串"这类问题，而不是提交后才靠后端校验报错。
_TUNING_PARAM_LABELS = {
    "schedule": "🕐 调度间隔 (schedule)",
    "priority": "🔢 优先级 (priority)",
    "execution_phase": "🧭 执行阶段 (execution_phase)",
    "task_template": "📝 任务模板 (task_template)",
    "regenerate_spec": "♻️ 重新生成执行规范草稿 (regenerate_spec)",
}


def _render_tuning_change_value_input(param: str, key_prefix: str, goal_id: str, *, current_task_template: str = ""):
    """按参数名渲染对应取值控件，返回 (value, disabled)——disabled=True 时
    调用方应该禁用提交按钮（比如 regenerate_spec 不需要用户填值，但仍要
    走同一套草案确认流程，不代表'什么都不用做'）。"""
    if param == "schedule":
        return st.text_input(
            "新的调度表达式", placeholder="interval:3600 或 cron:0 9 * * *",
            key=f"{key_prefix}tune_val_schedule_{goal_id}",
        ), False
    if param == "priority":
        return st.slider(
            "新优先级", 0, 100, 50, key=f"{key_prefix}tune_val_priority_{goal_id}",
        ), False
    if param == "execution_phase":
        return st.selectbox(
            "新阶段", ["auto", "explore", "converge", "running", "tidy"],
            format_func=lambda m: _PHASE_LABELS.get(m, m),
            key=f"{key_prefix}tune_val_phase_{goal_id}",
        ), False
    if param == "task_template":
        # [诊断与调优展示补全] 默认值带上当前生效的 task_template（诊断
        # 报告里已经取到，见 _render_goal_cycle_diagnostics_widget），用户
        # 通常是"在现有基础上改一点"而不是从零重写，预填能避免误操作把
        # 之前确认过的内容整段丢掉；仍然可以清空重写。
        return st.text_area(
            "新的任务模板文本（cron 触发时注入的任务描述）", height=80,
            value=current_task_template,
            key=f"{key_prefix}tune_val_template_{goal_id}",
        ), False
    # regenerate_spec：固定为 true，不需要用户填值——只是"要不要触发重新
    # 生成一份草稿"这个二元决定，值本身没有意义。
    st.caption("提交后会调用『重新生成执行规范草稿』，只生成新草稿，不自动确认——"
               "确认仍需要在『⏰ 周期性设置』折叠区里手动操作。")
    return True, False


def _render_tuning_proposal_card(client: AgentClient, goal_id: str, p: dict, key_prefix: str) -> None:
    """渲染单条草案：改动 diff + 状态相应的操作按钮（draft→确认/拒绝，
    confirmed→应用/拒绝），与 CLI `_print_tuning_proposal()` 展示同样的
    字段，保证两端看到的信息一致。"""
    status = p.get("status", "draft")
    status_label = {"draft": "📝 草稿", "confirmed": "✅ 已确认（待应用）"}.get(status, status)
    source_label = "👤 用户提出" if p.get("source") == "user_request" else "⚙️ 规则触发"
    with st.container(border=True):
        st.caption(f"{status_label}　·　{source_label}　·　`{p.get('id', '')}`")
        for c in p.get("proposed_changes", []):
            reason = f"　—　{c.get('reason')}" if c.get("reason") else ""
            st.markdown(
                f"**{_TUNING_PARAM_LABELS.get(c.get('param'), c.get('param'))}**："
                f"`{c.get('from')}` → `{c.get('to')}`{reason}"
            )
        bcol1, bcol2 = st.columns(2)
        if status == "draft":
            if bcol1.button("✅ 确认草案", key=f"{key_prefix}tune_confirm_{p.get('id')}"):
                res = client.confirm_tuning_proposal(goal_id, p.get("id"))
                if res and res.get("_error"):
                    st.error(f"确认失败：{res['_error']}")
                else:
                    st.rerun()
        elif status == "confirmed":
            if bcol1.button("🚀 应用（立即生效）", key=f"{key_prefix}tune_apply_{p.get('id')}"):
                res = client.apply_tuning_proposal(goal_id, p.get("id"))
                if res and res.get("_error"):
                    st.error(f"应用失败：{res['_error']}")
                else:
                    applied = res.get("proposal", {})
                    fail_results = [
                        r for r in applied.get("apply_results", []) if not r.get("ok", True)
                    ]
                    if fail_results:
                        for r in fail_results:
                            st.warning(f"⚠️ `{r.get('param')}` 应用失败：{r.get('error', '未知原因')}")
                    st.toast("✅ 调优草案已应用", icon="✅")
                    st.rerun()
        if status in ("draft", "confirmed"):
            if bcol2.button("❌ 拒绝", key=f"{key_prefix}tune_reject_{p.get('id')}"):
                res = client.reject_tuning_proposal(goal_id, p.get("id"))
                if res and res.get("_error"):
                    st.error(f"拒绝失败：{res['_error']}")
                else:
                    st.rerun()


def _render_goal_tuning_widget(client: AgentClient, goal_id: str, key_prefix: str = "", *, current_task_template: str = "") -> None:
    """[Stage 2/3 看板集成] 调优草案区块：待处理草案列表 + 两种生成入口
    （规则建议 / 手动指定参数，Stage 3 自然语言意见需要后端配置开关，未
    开启时后端会返回明确的 400 错误，这里原样展示，不做二次判断）。
    """
    resp = client.list_tuning_proposals(goal_id)
    if resp and resp.get("_error"):
        st.caption(f"调优草案加载失败：{resp['_error']}")
        return
    proposals = (resp or {}).get("proposals") or []
    pending = [p for p in proposals if p.get("status") in ("draft", "confirmed")]
    history = [p for p in proposals if p.get("status") in ("applied", "rejected")]

    if pending:
        for p in pending:
            _render_tuning_proposal_card(client, goal_id, p, key_prefix)
    else:
        st.caption("暂无待处理的调优草案。")

    acol1, acol2 = st.columns(2)
    if acol1.button("🔍 基于诊断规则生成建议", key=f"{key_prefix}tune_suggest_{goal_id}",
                     help="不调用 LLM，只根据诊断报告里已有的规则信号（比如 cron 连续跳过）判断"):
        res = client.suggest_tuning_proposal(goal_id)
        if res and res.get("_error"):
            st.error(f"生成失败：{res['_error']}")
        elif not (res or {}).get("proposal"):
            st.toast("当前没有规则命中的建议", icon="ℹ️")
        else:
            st.rerun()

    with st.expander("✍️ 手动生成草案", expanded=False):
        # [Stage 3] 自然语言意见——是否真的生效取决于服务端配置开关
        # cycle_tuning.tuning_llm_parse_enabled，未开启时后端返回 400，
        # 这里直接展示后端的错误信息，不在看板侧重复维护一份"是否开启"的
        # 判断逻辑（避免两处状态不一致）。
        nl_text = st.text_area(
            "自然语言改进意见（可选，需要管理员已开启该功能）", height=60,
            placeholder="例如：这个任务最近老是被跳过，帮我放宽一下触发间隔",
            key=f"{key_prefix}tune_nl_{goal_id}",
        )
        if st.button("提交自然语言意见", key=f"{key_prefix}tune_nl_submit_{goal_id}"):
            if not nl_text.strip():
                st.error("意见内容不能为空")
            else:
                res = client.create_tuning_proposal(goal_id, nl_text=nl_text.strip())
                if res and res.get("_error"):
                    st.error(f"提交失败：{res['_error']}")
                elif not (res or {}).get("proposal"):
                    st.warning("未能把这条意见解析成结构化改动，请改用下面的『指定参数改动』。")
                else:
                    st.rerun()

        st.markdown("---")
        st.caption("或直接指定要改的参数（白名单内，与 CLI `tune param=value` 等价）：")
        from mini_agent.perception.cycle_tuning import WHITELIST_PARAMS
        param_choice = st.selectbox(
            "参数", list(WHITELIST_PARAMS),
            format_func=lambda k: _TUNING_PARAM_LABELS.get(k, k),
            key=f"{key_prefix}tune_param_{goal_id}",
        )
        value, _ = _render_tuning_change_value_input(
            param_choice, key_prefix, goal_id,
            current_task_template=current_task_template if param_choice == "task_template" else "",
        )
        reason = st.text_input("理由（可选，会记录进草案里）", key=f"{key_prefix}tune_reason_{goal_id}")
        if st.button("生成草案", key=f"{key_prefix}tune_manual_submit_{goal_id}"):
            changes = [{"param": param_choice, "to": value, "reason": reason}]
            res = client.create_tuning_proposal(goal_id, changes=changes)
            if res and res.get("_error"):
                st.error(f"生成失败：{res['_error']}")
            else:
                st.rerun()

    if history:
        with st.expander(f"🗂️ 历史草案（{len(history)}）", expanded=False):
            for p in history[-5:]:
                status_label = {"applied": "✅ 已应用", "rejected": "❌ 已拒绝"}.get(
                    p.get("status"), p.get("status")
                )
                summary = "；".join(
                    f"{c.get('param')}→{c.get('to')}" for c in p.get("proposed_changes", [])
                )
                st.caption(f"{status_label}　`{p.get('id', '')}`　{summary}")


def _render_goal_cycle_diagnostics_widget(client: AgentClient, goal_id: str, key_prefix: str = "") -> None:
    """[goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md Stage 1/2/3
    看板集成] Goal 卡片上的跨轮次诊断报告 + 调优草案入口。

    交互设计取舍（对应用户的问题"看板里怎么做这件事合理"）：
      1. 复用已有的\"badge + 默认折叠 expander\"范式（与执行阶段徽章
         `_render_goal_execution_phase_widget` 同款），而不是新开一个独立
         Tab——诊断/调优是围绕单个 Goal 的操作，跟着 Goal 卡片走，用户不用
         在\"看板\"和\"诊断中心\"之间切换、重新定位到同一个 Goal。
      2. 诊断报告本身走跟 execution_phase/output_manifests 一致的\"随卡片
         渲染就取一次\"（纯本地文件聚合，成本跟读一次 goals.json 相当），
         徽章直接反映健康状态（🟢/🟡/🔴），不需要用户先展开才知道\"这个
         Goal 是不是有问题\"——诊断的核心价值就是\"扫一眼就知道要不要管\"，
         如果还要点开才能看到红绿灯，这个价值就打了折扣。
      3. LLM 自然语言摘要（Stage 3）**不**跟着卡片自动生成——那是要花一次
         LLM 调用的，扫视整个看板时不应该因为卡片多而触发一堆后台 LLM
         请求；改成按钮触发，用户主动点了才算一次。
      4. 调优草案的生成/确认/应用完整复用已有的 draft→confirm→apply 状态机
         和 REST 接口，不在看板侧另起一套逻辑；表单按参数类型给对应控件
         （下拉框/滑块/文本域）而不是自由文本 `param=value`，让看板用户
         不需要记参数名和取值格式，出错概率比 CLI 自由文本更低。
      5. 只在 Goal 层级（非 Objective）渲染——调优的白名单参数
         （schedule/priority/execution_phase/task_template/regenerate_spec）
         全部是挂在 Goal 上的概念，Objective 没有对应语义。
    读取失败时静默不展示，不影响卡片其它内容（与 execution_phase 同一
    降级策略）。
    """
    if not goal_id:
        return
    resp = client.get_cycle_diagnostics(goal_id)
    if not resp or resp.get("_error"):
        return
    diag = (resp or {}).get("diagnostics") or {}
    if not diag.get("found", True):
        return

    alerts = diag.get("recent_health_alerts") or []
    cron_health = diag.get("cron_health") or {}
    # 徽章：有 alert 就是黄，cron 健康信号里标了 unhealthy/critical 一类
    # 才升级到红——具体判定逻辑完全信任后端 `check_phase_health()` /
    # `cron_health` 已经算好的结果，看板不重新发明健康判定标准。
    severity = str(cron_health.get("status", "")).lower()
    if severity in ("unhealthy", "critical", "failing"):
        health_icon = "🔴"
    elif alerts:
        health_icon = "🟡"
    else:
        health_icon = "🟢"

    with st.expander(f"{health_icon} 诊断与调优", expanded=False):
        st.caption(
            f"已完成 {diag.get('cycle_count', 0)} 轮　·　"
            f"阶段：{_PHASE_LABELS.get(diag.get('execution_phase_mode', 'auto'), diag.get('execution_phase_mode', 'auto'))}"
            f"{'🔒' if diag.get('execution_phase_locked') else ''}"
            f"　·　状态：{diag.get('status', '-')}"
        )
        if alerts:
            for a in alerts:
                st.warning(f"⚠️ {a.get('message', a) if isinstance(a, dict) else a}")
        if cron_health:
            ch_msg = cron_health.get("message") or cron_health.get("status") or ""
            if ch_msg:
                st.caption(f"⏰ cron 健康：{ch_msg}")

        # [诊断与调优展示补全] task_template 是唯二的自由文本调优参数
        # （另一个 regenerate_spec 不需要"当前值"），用户点开调优表单前
        # 应该先看到"现在生效的是哪段文本"，否则手动生成草案时只能凭记忆
        # 编一份新的，容易把之前确认过的内容覆盖掉。report 里早就有
        # task_template 字段（诊断报告构建时从绑定的 cron job 读出），
        # 只是之前没有渲染过；这里用只读文本框展示，过长时自带滚动条，
        # 不占用卡片的固定高度。
        current_template = diag.get("task_template")
        if current_template:
            st.caption("📝 当前 task_template（cron 触发时注入的任务描述）：")
            st.text_area(
                "当前 task_template", value=current_template, height=100,
                disabled=True, label_visibility="collapsed",
                key=f"{key_prefix}diag_cur_template_{goal_id}",
            )

        summary_key = f"{key_prefix}diag_llm_summary_{goal_id}"
        if st.button("🤖 生成自然语言摘要", key=f"{key_prefix}diag_summarize_btn_{goal_id}",
                     help="需要管理员已开启配置 cycle_tuning.diagnostics_llm_summary_enabled"):
            resp2 = client.get_cycle_diagnostics(goal_id, summarize=True)
            summary = ((resp2 or {}).get("diagnostics") or {}).get("llm_summary")
            st.session_state[summary_key] = summary or "（未生成——功能未开启，或本次 LLM 调用未返回有效结果）"
        if summary_key in st.session_state:
            st.info(st.session_state[summary_key])

        recent = diag.get("recent_cycle_summaries") or []
        if recent:
            with st.expander(f"最近几轮产出（{len(recent)}）", expanded=False):
                for item in reversed(recent[-5:]):
                    label = item.get("label") or item.get("cycle") or ""
                    text = item.get("summary") or item.get("progress_notes") or ""
                    st.caption(f"`{label}` {text}")

        mechanism_notes = diag.get("mechanism_notes") or []
        if mechanism_notes:
            with st.expander("机制说明", expanded=False):
                for note in mechanism_notes:
                    st.caption(f"- {note}")

        st.markdown("---")
        st.markdown("##### 🔧 调优草案")
        _render_goal_tuning_widget(
            client, goal_id, key_prefix=key_prefix,
            current_task_template=current_template or "",
        )


def _render_goal_card(
    client: AgentClient, n: dict, status_key: str, indent: bool = False, note: str = "",
    execution: Optional[dict] = None, cron_next_run_by_id: Optional[dict] = None,
    key_prefix: str = "",
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
    #
    # [进展信息过长/无滚动条修复] cumulative_progress 是 agent 持续追加
    # 写入的自由文本，长期运行的 Goal 很容易攒到几十行，之前整段原样塞进
    # 卡片的一个 <div>，卡片被撑得极长，而这段 HTML 本身又没有固定高度/
    # overflow 设置，也就没有滚动条——用户反馈的正是这个观察。
    # 处理方式：按行拆成条目，卡片正文只放最新一条（一眼看到"现在做到
    # 哪了"，不撑高卡片），完整历史移到卡片下方的折叠区，按时间倒序、
    # 复用已有的 `_client_side_page()`（跟外部信息 external_context 同一
    # 套分页交互）分页展示，每页 10 条——用户可以点击"下一页"，或者
    # （因为整段内容此时已经不是超长单块，而是分页后的短列表）用浏览器
    # 自身滚动查看，不再依赖卡片内部的滚动条。
    progress_text = n.get("work_thread_progress") or n.get("progress_notes") or ""
    progress_lines = [ln.strip() for ln in progress_text.splitlines() if ln.strip()]
    if len(progress_lines) <= 1:
        progress_html = (
            f'<div class="meta">📈 进展：{_esc_html(progress_text)}</div>' if progress_text else ""
        )
    else:
        # 约定：agent 追加进展时把最新内容写在最后一行，卡片正文只展示
        # 这一条最新记录（截断超长单行，避免单行本身又把卡片撑高）。
        latest_line = progress_lines[-1]
        latest_display = latest_line if len(latest_line) <= 120 else latest_line[:120] + "…"
        progress_html = (
            f'<div class="meta">📈 进展（共 {len(progress_lines)} 条，最新）：'
            f'{_esc_html(latest_display)}</div>'
        )
    next_suggested = n.get("work_thread_next_suggested") or ""
    next_html = (
        f'<div class="meta" style="color:#888;">👉 建议下一步：{_esc_html(next_suggested)}</div>'
        if next_suggested else ""
    )

    # [goal_cron_visibility_and_intervention_improvement_plan.md Track A]
    # Goal 卡片（非 Objective）展示周期性状态——之前 GoalNode.to_dict() 已经
    # 带了 recurring/cycle_count 字段，但看板从未渲染过，用户完全看不出一个
    # Goal 是不是在周期性运转、跑到第几轮。
    recur_html = ""
    if n.get("level") != "objective" and n.get("recurring"):
        pending = "　⏭️ 下一轮将被跳过" if n.get("skip_next_cycle") else ""
        # [scheduling_unification_and_kanban_visibility_improvement_plan.md
        # P4] "下次触发时间"只从绑定的 CronJob 读（next_run_str，
        # cron_scheduler.py 已有此方法），不在 Goal 侧重新计算一遍——
        # 避免出现两套时间原语各自算出不一致的数字。cron_next_run_by_id
        # 由调用方（render_kanban_tab）从同一次 autonomous_status() 里
        # 已经取到的 cron_jobs 列表构建，找不到对应绑定时留空不展示。
        next_run_html = ""
        cron_job_id = n.get("recurrence_cron_job_id")
        if cron_next_run_by_id and cron_job_id in cron_next_run_by_id:
            next_run_html = f"　·　下次触发：{cron_next_run_by_id[cron_job_id]}"
        recur_html = (
            f'<div class="meta" style="color:#2a7;">🔁 周期性 · 已完成 {n.get("cycle_count", 0)} 轮'
            f'{pending}{next_run_html}</div>'
        )
    elif n.get("level") != "objective" and n.get("source") != "agent_derived":
        recur_html = '<div class="meta" style="color:#999;">未设为周期性</div>'
    cron_source_html = ""
    if n.get("level") == "objective" and n.get("source") == "cron":
        cron_source_html = '<div class="meta" style="color:#2a7;">⏰ 由 cron 周期触发</div>'

    # [goal-provenance-guide.md] source（谁负责决定创建它：user/
    # agent_derived/novelty_candidate）之外，再展示 source_initiator
    # （创建它的那次调用发生在哪个轮次里：user/cron/external/
    # autonomous_loop）——两者是正交维度，之前看板只展示了前者，用户
    # 完全看不出"一个 source=user 的 Goal，到底是我自己敲的命令，还是
    # Agent 在处理一轮 cron 触发的对话时帮我创建的"。只在两者不同、且
    # source_initiator 不是默认值 "user" 时额外展示一行，避免绝大多数
    # 正常手动创建的 Goal 卡片上多一行没有信息量的"user"。
    initiator = n.get("source_initiator", "user")
    initiator_html = ""
    if initiator and initiator != "user":
        _initiator_label = {
            "cron": "⏰ 由 cron 触发的对话中创建",
            "external": "📡 由外部输入触发的对话中创建",
            "autonomous_loop": "🤖 由自主 tick 直接派生",
        }.get(initiator, f"由 {initiator} 触发的对话中创建")
        initiator_html = f'<div class="meta" style="color:#b7791f;">{_initiator_label}</div>'

    st.markdown(f"""
<div class="kanban-card" style="{wrapper_style}">
  <div class="title">{level_tag} {_esc_html(n.get('title','(无标题)'))}</div>
  <div class="meta">来源:{n.get('source','')}　优先级:{n.get('priority',0)}</div>
  {initiator_html}
  {recur_html}
  {cron_source_html}
  {progress_html}
  {next_html}
  {note_html}
</div>
""", unsafe_allow_html=True)

    # [进展信息过长/无滚动条修复] 卡片正文只展示最新一条进展（见上面
    # progress_html 的处理），完整历史（倒序、最新在前）放在这个折叠区，
    # 每页 10 条，与 external_context 用同一套 `_client_side_page()` 分页
    # 交互——点击翻页或滚动浏览均可，不再是一个又长又没有滚动条的整段
    # HTML。只在条目数 > 1 时展示（否则卡片正文已经是完整内容，重复一份
    # 折叠区没有信息量）。
    if len(progress_lines) > 1:
        with st.expander(f"📈 进展历史（共 {len(progress_lines)} 条，倒序）", expanded=False):
            reversed_progress = list(reversed(progress_lines))
            progress_page_key = f"{key_prefix}goal_progress_page_{n.get('id')}"
            for idx, line in zip(
                range(len(reversed_progress), 0, -1),
                _client_side_page(reversed_progress, 10, progress_page_key),
            ):
                st.caption(f"`#{idx}` {line}")

    # [P7 新增，见 watchlist_notification_goal_design.md §6 P7] GoalRelevanceEngine
    # Stage② 判定 relevant=true 时会把外部信息摘要挂到 external_context 上
    # （只读展示，不提供在这里手动清空的按钮——生命周期跟随 Goal 本身，见 §8 开放项 3）。
    external_context = n.get("external_context") or []
    if external_context:
        with st.expander(f"🔗 相关外部信息（{len(external_context)} 条）"):
            reversed_context = list(reversed(external_context))
            page_key = f"{key_prefix}goal_extctx_page_{n.get('id')}"
            for item in _client_side_page(reversed_context, 5, page_key):
                occurred_at = item.get("occurred_at")
                ts_str = (
                    time.strftime("%m-%d %H:%M", time.localtime(occurred_at))
                    if occurred_at else "-"
                )
                st.caption(f"`{ts_str}` **{item.get('title', '')}**：{item.get('snippet', '')}")

    # [goal_cron_output_directory_convention_plan.md §4/§5 开放问题 3] Goal
    # 卡片（周期性 + 一次性均覆盖）追加"📂 查看产出"折叠区：读
    # .agent/daemon_run_outputs/goals/<goal_id>/latest.json + 最近几轮
    # manifest.json，只列文件名，不做预览/下载——避免看板膨胀成文件管理器，
    # 需要的话用户直接去 .agent/daemon_run_outputs/ 目录看（沿用已有的
    # /fs/* 只读接口，不新增专用后端路由）。一次性 Goal 还没跑出任何子
    # Objective 收尾时 latest.json 不存在，函数内部会静默不展示。
    if n.get("level") != "objective":
        _render_goal_output_manifests(client, n.get("id", ""), key_prefix=key_prefix)

    # [goal_execution_phase_improvement_plan.md Stage C] Goal 卡片（非
    # Objective）展示执行阶段徽章 + 手动切换下拉框——explore/converge/
    # running/tidy/auto 五态，与 execution_spec 折叠区一样只在 Goal 层级
    # 渲染，Objective 卡片不重复展示（阶段是挂在 Goal 上的概念）。
    if n.get("level") != "objective":
        _render_goal_execution_phase_widget(client, n.get("id", ""), key_prefix=key_prefix)

    # [goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md Stage 1/2/3
    # 看板集成] 跟执行阶段徽章同级——诊断/调优是"围绕这个 Goal 的操作"，不是
    # "周期性设置"的子项，一次性 Goal（未绑定 cron）也一样有阶段/健康信号
    # 可看，不应该只在绑定了周期性之后才暴露入口。
    if n.get("level") != "objective":
        _render_goal_cycle_diagnostics_widget(client, n.get("id", ""), key_prefix=key_prefix)

    if execution is not None:
        _render_objective_execution_detail(client, execution, key_prefix=key_prefix)
    # [bugfix / 顶栏『查看并控制』跳转崩溃] status_key 不总是
    # GOAL_STATUS_COLUMNS 里的 6 个看板列之一——Goal/Objective 的 status 字段
    # 在其它子系统（cron 桥接、fairness 调度、旧版本数据兼容等）里是"不透明
    # 字符串"，实际出现过的值远不止这 6 个（如 paused_by_user、
    # paused_for_fairness、blocked、dormant，以及历史数据里遗留的 cleaned
    # 等）。之前这里直接 `.index(status_key)`，一旦当前节点的真实 status 不在
    # 列表里就抛 ValueError 整页崩掉——顶栏"🔍 查看并控制"跳转（见上面
    # focus_node 那次调用，传的是节点的原始 status，未经过分栏筛选）最容易
    # 触发。这里改成：status_key 不在标准 6 项里时，把它作为一个额外选项
    # 追加到下拉框末尽（标签直接显示原始字符串，提示"当前非标准状态"），
    # 保证下拉框一定能定位到当前值，不再崩溃；用户不特意去改的话也不会
    # 误触发一次状态写回。
    _status_options = [s for s, _ in GOAL_STATUS_COLUMNS]
    if status_key not in _status_options:
        _status_options = _status_options + [status_key]
        st.caption(f"⚠️ 当前状态「{status_key}」不是标准看板状态之一（可能来自其它子系统"
                   "写入或历史数据），已作为额外选项加入下拉框，避免页面崩溃。")
    new_status = st.selectbox(
        "状态", _status_options,
        index=_status_options.index(status_key),
        key=f"{key_prefix}goalstatus_{n.get('id')}", label_visibility="collapsed",
    )
    if new_status != status_key:
        client.update_goal(n.get("id"), status=new_status)
        st.rerun()

    # [看板 Goal 编辑功能新增] 之前只能改状态，标题写错/描述过时时只能删了
    # 重建（丢失 external_context/work_thread 等关联历史）。这里补一个折叠的
    # 编辑表单，直接 PATCH title/description/priority——同一把 update_fields
    # 锁保护，不会跟 ObjectiveExecutor 的状态同步写入冲突。
    with st.expander("✏️ 编辑标题/描述/优先级", expanded=False):
        with st.form(f"{key_prefix}edit_goal_{n.get('id')}"):
            edit_title = st.text_input("标题", value=n.get("title", ""))
            edit_desc = st.text_area("描述", value=n.get("description", ""), height=80)
            edit_priority = st.slider("优先级", 0, 100, int(n.get("priority", 50)))
            save = st.form_submit_button("保存")
        if save:
            fields = {}
            if edit_title.strip() and edit_title.strip() != n.get("title", ""):
                fields["title"] = edit_title.strip()
            if edit_desc != n.get("description", ""):
                fields["description"] = edit_desc
            if edit_priority != int(n.get("priority", 50)):
                fields["priority"] = edit_priority
            if fields:
                res = client.update_goal(n.get("id"), **fields)
                if res and "_error" in res:
                    st.error(res["_error"])
                else:
                    st.rerun()
            else:
                st.caption("没有改动。")

    # [goal_cron_visibility_and_intervention_improvement_plan.md Track A/B]
    # 周期性绑定/解绑/跳过一轮——之前这三个操作只有 CLI（/agent goals
    # recur|unrecur、/cron add-goal-cycle）能做，看板没有对应入口。
    # 只在 Goal 级卡片上展示（Objective 子节点没有自己的周期性绑定）。
    if n.get("level") != "objective":
        with st.expander("⏰ 周期性设置", expanded=False):
            if n.get("recurring"):
                next_run_caption = ""
                cron_job_id = n.get("recurrence_cron_job_id")
                if cron_next_run_by_id and cron_job_id in cron_next_run_by_id:
                    next_run_caption = f" · 下次触发：{cron_next_run_by_id[cron_job_id]}"
                st.caption(
                    f"已绑定 cron job `{n.get('recurrence_cron_job_id', '?')}` · "
                    f"已完成 {n.get('cycle_count', 0)} 轮{next_run_caption}"
                )
                bc1, bc2, bc3, bc4 = st.columns(4)
                if bc1.button("⏭️ 跳过下一轮", key=f"{key_prefix}skipcycle_{n.get('id')}",
                               disabled=bool(n.get("skip_next_cycle"))):
                    res = client.skip_goal_next_cycle(n.get("id"))
                    if res and "_error" in res:
                        st.error(res["_error"])
                    st.rerun()
                # [goal_cron_task_optimization_holistic_plan.md 方向 C]
                # "跳过"是二元的（跑/不跑），这里补一个中间态：下一轮仍然
                # 触发，但要求 agent 从简处理，不引入新方案/结构性变更。
                if bc2.button("🪶 下一轮从简", key=f"{key_prefix}lightweight_{n.get('id')}",
                               disabled=bool(n.get("next_cycle_lightweight")),
                               help="下一次触发仍会照常执行，但只做最小限度的同步/巡检"):
                    res = client.lightweight_goal_next_cycle(n.get("id"))
                    if res and "_error" in res:
                        st.error(res["_error"])
                    st.rerun()
                if bc3.button("🛑 取消周期性", key=f"{key_prefix}unrecur_{n.get('id')}"):
                    res = client.unrecur_goal(n.get("id"))
                    if res and "_error" in res:
                        st.error(res["_error"])
                    st.rerun()
                # [goal_output_directory_and_execution_phase_redesign_plan.md
                # Stage 9] 请求下一次触发附加一次"历史数据迁移"任务，把旧
                # 模型（每轮一个 cycle_NNNN/ 目录）遗留的历史产出搬进新的
                # 固定四目录模型。只打一次性标记，disabled 状态复用同一个
                # 字段判断，避免重复点击。
                if bc4.button("📦 迁移历史数据", key=f"{key_prefix}migratelegacy_{n.get('id')}",
                               disabled=bool(n.get("legacy_migration_requested")),
                               help="下一次触发时附加一次搬迁任务：把旧的 cycle_NNNN/ 目录内容搬进新的 output/ 结构"):
                    res = client.migrate_goal_legacy_cycles(n.get("id"))
                    if res and "_error" in res:
                        st.error(res["_error"])
                    st.rerun()
                if n.get("legacy_migration_requested"):
                    st.caption("📦 已标记：下一轮触发时会附加一次历史数据迁移任务")
                if n.get("next_cycle_lightweight"):
                    st.caption("🪶 下一轮已标记为从简执行")
                # [goal_execution_spec_generation_plan.md §6.1 最后一条] 已绑定
                # 周期性、但从未生成过规范的既有 Goal，补一个「生成执行规范」
                # 入口，走同一套草稿确认流程；确认后下一轮触发即生效，不需要
                # 先解绑再重新绑定。
                st.markdown("---")
                _render_goal_execution_spec_widget(
                    client, n.get("id"), key_prefix=key_prefix,
                    goal_title=n.get("title", ""), goal_description=n.get("description", ""),
                )
            else:
                st.caption("这个 Goal 还不是周期性的——绑定后会按 schedule 自动派生并启动新一轮。")
                _render_goal_execution_spec_widget(
                    client, n.get("id"), key_prefix=key_prefix,
                    goal_title=n.get("title", ""), goal_description=n.get("description", ""),
                )
                # [goal_execution_spec_generation_plan.md §5 第二段] 一次性、拆了
                # 多个子 Objective 的 Goal，如果确认过规范且填了
                # overall_completion_criteria，正常情况下最后一个子 Objective
                # 完成时会自动判定一次；这里补一个手动重判入口，对应 CLI
                # `spec close-check`，用于"上次判定是继续、后续补充了材料想重判"
                # 或排查"为什么一直没自动关闭"。
                # [implementation_record.md §11 后续建议顺序第 1/2 条]
                # 持久化展示上一次判定结果 + 单次覆盖是否走 Agent 路径，
                # 与草稿生成的"生成路径"下拉框 + `effective_path` 展示是
                # 同一风格。
                last_check = n.get("overall_completion_last_check")
                if last_check:
                    _cc_outcome = last_check.get("outcome")
                    _cc_icon = "✅" if _cc_outcome == "closed" else "ℹ️"
                    _cc_path = "只读探索 Agent" if last_check.get("used_agent") else "纯 LLM"
                    _cc_at = last_check.get("at")
                    _cc_at_str = time.strftime("%m-%d %H:%M", time.localtime(_cc_at)) if _cc_at else "未知时间"
                    st.caption(
                        f"{_cc_icon} 上次整体关闭判定（{_cc_at_str}，走 {_cc_path} 路径）："
                        f"{'已关闭' if _cc_outcome == 'closed' else '暂不关闭'}"
                    )
                _cc_path_labels = {"": "跟随配置默认", "agent": "只读探索 Agent", "llm": "纯 LLM"}
                _cc_path_choice = st.selectbox(
                    "整体关闭判定路径", list(_cc_path_labels.keys()),
                    format_func=lambda k: _cc_path_labels[k],
                    key=f"{key_prefix}ges_closecheck_path_{n.get('id')}",
                )
                if st.button("🔁 手动重判整体是否可以关闭", key=f"{key_prefix}ges_closecheck_{n.get('id')}"):
                    _cc_use_agent = {"": None, "agent": True, "llm": False}[_cc_path_choice]
                    res = client.close_check_execution_spec(n.get("id"), use_agent=_cc_use_agent)
                    if res and res.get("_error"):
                        st.error(res["_error"])
                    else:
                        outcome = res.get("outcome")
                        if outcome == "closed":
                            st.success("判定为整体已完成，Goal 已标记为 completed。")
                        elif outcome == "kept_open":
                            st.info("判定为暂不关闭（继续保持 active），详见下方进展记录。")
                        else:
                            st.caption(res.get("reason") or "未触发判定：可能子 Objective 未全部终态、"
                                                              "规范未确认，或 overall_completion_criteria 为空。")
                        st.rerun()
                st.markdown("---")
                st.caption("生成/确认执行规范是可选步骤——不想先想细节，可以直接下面绑定周期性（跳过规范）。")
                with st.form(f"{key_prefix}recur_form_{n.get('id')}"):
                    r_schedule = st.text_input(
                        "调度 (interval:<秒数> 或 cron:<表达式>)",
                        placeholder="例如 interval:86400（每天一次）",
                    )
                    r_task = st.text_area("每轮任务内容（留空则复用 Goal 描述）", height=60)
                    r_submit = st.form_submit_button("🔁 设为周期性")
                if r_submit:
                    if not r_schedule.strip():
                        st.error("调度不能为空")
                    else:
                        res = client.recur_goal(n.get("id"), r_schedule.strip(), r_task.strip())
                        if res and "_error" in res:
                            st.error(res["_error"])
                        else:
                            st.rerun()

    # [goal_cron_feedback_and_output_policy_plan.md Track E] 用户对本节点
    # 提意见——持久化合入 description，此后所有基于这个 Goal/Objective 派生
    # 的执行都会带着这条意见（区别于 objective_executor 的临时补充说明，
    # 那个只影响下一次提交的一个 step）。历史意见展开可回看。
    with st.expander("💬 提意见", expanded=False):
        user_feedback = n.get("user_feedback") or []
        if user_feedback:
            for item in reversed(user_feedback):
                at = item.get("at")
                ts_str = time.strftime("%m-%d %H:%M", time.localtime(at)) if at else "-"
                st.caption(f"`{ts_str}` {item.get('text', '')}")
        else:
            st.caption("还没有意见记录。")
        with st.form(f"{key_prefix}goal_feedback_{n.get('id')}", clear_on_submit=True):
            fb_text = st.text_area("你的意见（会永久合入这个节点的说明，之后每次执行都会带着）", height=60)
            fb_submit = st.form_submit_button("提交意见")
        if fb_submit:
            if not fb_text.strip():
                st.error("意见内容不能为空")
            else:
                res = client.add_goal_feedback(n.get("id"), fb_text.strip())
                if res and "_error" in res:
                    st.error(res["_error"])
                else:
                    st.rerun()


# [kanban_perception_gaps_improvement_plan.md 方向 D.1] "📈 完成率趋势"
# ——跟上面 growth_health_trend 是完全平行的模式（同一套"每日一条快照 +
# 折叠区块里才拉取"的展示约定），数据来源是新增的 `objective_trend.py`，
# 快照挂在 `POST /v1/growth/scan`（cron sys:growth_advisor_daily 每日
# 调用）上顺带记录，不是独立调度点。
def _render_objective_completion_trend(client: "AgentClient"):
    with st.expander("📈 完成率趋势", expanded=False):
        try:
            data = client.objective_completion_trend(limit=30) or {}
        except Exception as e:
            st.caption(f"趋势数据加载失败：{e}")
            return
        if data and "_error" in data:
            st.caption(f"趋势数据加载失败：{data['_error']}")
            return
        rows = data.get("completion_trend") or []
        if not rows:
            st.caption("暂无历史快照——完成率趋势在每天一轮的自动扫描"
                        "（sys:growth_advisor_daily）结束后才会记一条，"
                        "至少运行几天后才能看到走势。")
            return
        import pandas as pd
        df = pd.DataFrame(rows)
        df["日期"] = df["recorded_at"].apply(
            lambda ts: time.strftime("%m-%d", time.localtime(ts)) if ts else ""
        )
        df = df.set_index("日期")
        chart_cols = {
            "objectives_completed_today": "当日完成数",
            "objectives_failed_today": "当日失败数",
        }
        present = [c for c in chart_cols if c in df.columns]
        if present:
            st.line_chart(df[present].rename(columns=chart_cols))
        latest = rows[-1]
        c1, c2, c3 = st.columns(3)
        c1.metric("最近一次完成数", latest.get("objectives_completed_today", 0))
        c2.metric("最近一次失败数", latest.get("objectives_failed_today", 0))
        c3.metric("平均重试次数", latest.get("avg_retry_count", 0))


def _render_cycle_health_overview(client: "AgentClient"):
    """[能力 D，见 next_doc/goal_cron_cycle_proactive_patrol_and_health_
    overview_plan.md §3.3] 看板顶部跨 recurring Goal 的健康总览区块。数据
    优先来自能力 C 的巡检快照（`data_source="patrol_snapshot"`），巡检未
    开启时后端自动退化为现算（`data_source="live"`），本函数不区分两条
    路径的渲染逻辑，只是标注一下数据来源和更新时间，避免用户误以为总览
    是绝对实时的（§3.3 最后一条）。

    每行提供一个"🔍 定位"按钮，复用已有的
    `st.session_state["kanban_focus_node_id"]` 跳转机制（focus_node 那段
    代码已经存在，这里只需要设置这个 session_state）。
    """
    with st.expander("🩺 健康总览", expanded=False):
        try:
            data = client.get_cycle_diagnostics_overview() or {}
        except Exception as e:
            st.caption(f"健康总览加载失败：{e}")
            return
        if data and "_error" in data:
            st.caption(f"健康总览加载失败：{data['_error']}")
            return

        data_source = data.get("data_source", "live")
        generated_at = data.get("generated_at") or 0
        if data_source == "patrol_snapshot" and generated_at:
            age_min = max(0, int((time.time() - generated_at) / 60))
            st.caption(f"最近一次巡检：{age_min} 分钟前")
        else:
            st.caption(
                "实时计算（未开启主动巡检）。开启主动巡检后，这里会显示"
                "最近一次后台巡检的结果，无需每次打开看板都重新计算——"
                "详见 `cycle_patrol.enabled` 配置项。"
            )

        goals = data.get("goals") or []
        review_triggers = data.get("review_triggers") or {}
        try:
            from mini_agent.evolution.cycle_patrol import _review_trigger_messages
            for msg in _review_trigger_messages(review_triggers):
                st.info(f"🔎 {msg}")
        except Exception:
            pass
        if not goals:
            st.caption("暂无周期性（recurring）Goal，或均处于健康状态。")
            return

        severity_icon = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
        counts = {"red": 0, "yellow": 0, "green": 0}
        for g in goals:
            counts[g.get("severity", "green")] = counts.get(g.get("severity", "green"), 0) + 1
        st.caption(
            f"🔴 {counts.get('red', 0)}　🟡 {counts.get('yellow', 0)}　"
            f"🟢 {counts.get('green', 0)}　（共 {len(goals)} 个）"
        )

        # 绿色数量较多时默认只显示统计数字（上面那一行），逐条列出的是
        # 红/黄以及少量绿色兜底，避免大规模部署下总览区块本身刷屏。
        show_rows = [g for g in goals if g.get("severity") != "green"] or goals[:5]
        for g in show_rows:
            icon = severity_icon.get(g.get("severity", "green"), "🟢")
            cols = st.columns([5, 2, 2, 2, 1])
            cols[0].write(f"{icon} {g.get('title', '')}")
            cols[1].caption(f"告警 {g.get('alert_count', 0)}")
            cols[2].caption(_PHASE_LABELS.get(g.get("execution_phase_mode", "auto"), g.get("execution_phase_mode", "auto")))
            cols[3].caption("📝 待确认草案" if g.get("has_pending_tuning_proposal") else "")
            if cols[4].button("🔍", key=f"overview_focus_{g.get('goal_id', '')}", help="定位到该 Goal 卡片"):
                st.session_state["kanban_focus_node_id"] = g.get("goal_id")
                st.rerun()


def render_kanban_tab(client: AgentClient):
    st.markdown("#### 📌 目标看板 (Goal Backlog)")

    _render_cycle_health_overview(client)

    _render_objective_completion_trend(client)

    with st.expander("➕ 新建目标"):
        with st.form("new_goal", clear_on_submit=True):
            title = st.text_input("标题", key="_new_goal_title_input")
            desc = st.text_area("描述", height=60, key="_new_goal_desc_input")
            priority = st.slider("优先级", 0, 100, 50)
            # [goal_execution_spec_generation_plan.md §6.3] 默认不勾选——多数
            # 随手创建的一次性 Goal 不值得投入一次 LLM 调用去想细节，是否需要
            # 由用户主动决定，不在新建这一步强推。适用场景是"一次性但会拆多个
            # 子 Objective、需要跨子任务传递信息/整体完成判定"的 Goal。
            gen_spec = st.checkbox("同时生成一次性 Goal 的执行规范（用于会拆多个子任务的场景）", value=False)
            submitted = st.form_submit_button("创建")
        if submitted and title.strip():
            res = client.add_goal(title.strip(), desc, priority)
            if res and "_error" in res:
                st.error(f"创建失败：{res['_error']}")
            else:
                # st.toast() 跨 st.rerun() 仍会展示（见 2071 行附近同类用法的
                # 说明），不需要额外的 session_state 标记就能在刷新后的页面上
                # 显示"创建成功"提示；表单本身用 clear_on_submit=True 清空
                # 标题/描述/优先级，避免用户看到"点了创建，输入框却还留着刚
                # 才填的内容"，误以为没提交成功。
                st.toast(f"✅ 目标「{title.strip()}」已创建", icon="✅")
                new_goal = res.get("goal") if isinstance(res, dict) else None
                new_goal_id = (new_goal or {}).get("id") if isinstance(new_goal, dict) else None
                if gen_spec and new_goal_id:
                    st.session_state["_ges_pending_new_goal"] = new_goal_id
                    st.session_state["_ges_pending_new_goal_title"] = (new_goal or {}).get("title", "")
                    st.session_state["_ges_pending_new_goal_desc"] = (new_goal or {}).get("description", "")
            st.rerun()
        elif submitted and not title.strip():
            st.error("标题不能为空")

        # [cross_goal_experience_reuse_plan.md] "🔍 查找相似的历史执行规范"
        # ——独立于表单提交按钮（st.form 内只能有一个 submit 按钮），复用
        # 上面标题/描述输入框当前的值（通过显式 key 从 session_state 读取，
        # 不需要用户重新输入一遍）。纯查询、不修改任何状态，找到的候选只是
        # 展示摘要供用户自己判断要不要复制进描述里参考，不做任何自动应用。
        if st.button("🔍 查找相似的历史执行规范", key="_find_similar_goal_specs_btn"):
            cur_title = st.session_state.get("_new_goal_title_input", "").strip()
            cur_desc = st.session_state.get("_new_goal_desc_input", "").strip()
            if not cur_title and not cur_desc:
                st.caption("请先在上面填写标题或描述，再点击查找。")
            else:
                resp = client.similar_confirmed_goal_specs(cur_title, cur_desc) or {}
                candidates = resp.get("candidates") or []
                if "_error" in resp:
                    st.caption(f"查找失败：{resp['_error']}")
                elif not candidates:
                    st.caption("没有找到相似度足够高的历史 Goal（或历史 Goal 都还没确认过执行规范）。")
                else:
                    for cand in candidates:
                        with st.expander(
                            f"「{cand.get('title', '')}」（相似度 {cand.get('similarity', 0):.0%}）",
                            expanded=False,
                        ):
                            st.markdown(cand.get("spec_summary", ""))

    # 新建目标时勾选了"同时生成执行规范"——创建请求本身只返回新 Goal 的 id，
    # 拿到 id 之后才能调用生成接口，所以草稿确认区块放在表单外面单独渲染，
    # 跨 rerun 用 session_state 记住"当前正在为哪个新建的 Goal 走这个流程"
    # （连同 title/description 一起存，因为模板自动匹配需要这两个字段，
    # 而表单本身已经 clear_on_submit 清空，不能在这里重新从表单读取）。
    _pending_new_goal_id = st.session_state.get("_ges_pending_new_goal")
    if _pending_new_goal_id:
        with st.container(border=True):
            st.markdown(f"##### 📋 为新建目标生成执行规范（`{_pending_new_goal_id}`）")
            confirmed = _render_goal_execution_spec_widget(
                client, _pending_new_goal_id, key_prefix="newgoal_",
                goal_title=st.session_state.get("_ges_pending_new_goal_title", ""),
                goal_description=st.session_state.get("_ges_pending_new_goal_desc", ""),
            )
            if st.button("收起（稍后可在该 Goal 卡片下继续）", key="_ges_pending_dismiss"):
                del st.session_state["_ges_pending_new_goal"]
                st.session_state.pop("_ges_pending_new_goal_title", None)
                st.session_state.pop("_ges_pending_new_goal_desc", None)
                st.rerun()
            if confirmed:
                del st.session_state["_ges_pending_new_goal"]
                st.session_state.pop("_ges_pending_new_goal_title", None)
                st.session_state.pop("_ges_pending_new_goal_desc", None)

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

        # [P4] 供 recurring Goal 卡片展示"下次触发"，单一数据源见
        # _render_goal_card 内的说明——同一次 autonomous_status() 调用里
        # 顺带取，不用再单独请求 /cron/jobs。
        cron_next_run_by_id = {
            j.get("id"): j.get("next_run_str", "-")
            for j in autostat_for_cards.get("cron_jobs", [])
        }

        # [daemon_stability_and_ux_improvement_plan.md 补充 / 看板顶栏跳转]
        # 从顶栏"正在执行"列表点击"🔍 查看并控制"跳转过来时，
        # `kanban_focus_node_id` 会被设成对应的 objective_id——这里单独
        # 找出这个节点，用已有的 `_render_goal_card()` 在筛选/分栏逻辑
        # 之前先高亮渲染一遍，不管它实际处于哪个状态列（用户可能筛选
        # 了只看某几个状态，看不见的话跳转就白跳了），并提供"清除定位"
        # 按钮退出高亮态，回到正常的分栏视图。
        focus_id = st.session_state.get("kanban_focus_node_id")
        if focus_id:
            focus_node = next((n for n in all_nodes if n.get("id") == focus_id), None)
            with st.container(border=True):
                fc1, fc2 = st.columns([6, 1])
                fc1.markdown("🎯 **已定位**（来自顶栏『正在执行』跳转）")
                if fc2.button("❌ 清除定位", key="kanban_focus_clear"):
                    st.session_state["kanban_focus_node_id"] = None
                    st.rerun()
                if focus_node is None:
                    st.caption("这个节点已经不存在了（可能已被删除，或不属于当前 session 的目标数据）。")
                else:
                    _render_goal_card(
                        client, focus_node, focus_node.get("status", ""),
                        execution=exec_by_objective_id.get(focus_node.get("id")),
                        cron_next_run_by_id=cron_next_run_by_id,
                        key_prefix="focus_",
                    )
            st.markdown("---")

        # [新增] 状态筛选：默认显示全部状态；用户的选择记到本地 prefs 文件里，
        # 下次打开（不管是不是同一个浏览器标签页/URL）都按上次选的显示——
        # 见 _load_kanban_prefs()/_save_kanban_pref() 的说明，这是有意跟
        # session_id/pinned 那套 query_params 深链接机制分开的一套持久化。
        all_status_keys = [s for s, _ in GOAL_STATUS_COLUMNS]
        label_by_key = dict(GOAL_STATUS_COLUMNS)
        key_by_label = {label: s for s, label in GOAL_STATUS_COLUMNS}

        if "goal_status_filter" not in st.session_state:
            saved = _load_kanban_prefs().get("goal_status_filter")
            if isinstance(saved, list) and saved and all(s in all_status_keys for s in saved):
                st.session_state["goal_status_filter"] = saved
            else:
                st.session_state["goal_status_filter"] = list(all_status_keys)

        selected_labels = st.multiselect(
            "🔍 只显示以下状态",
            options=[label for _, label in GOAL_STATUS_COLUMNS],
            default=[label_by_key[k] for k in st.session_state["goal_status_filter"] if k in label_by_key],
            key="goal_status_filter_widget",
        )
        selected_keys = [key_by_label[label] for label in selected_labels]
        if selected_keys != st.session_state["goal_status_filter"]:
            st.session_state["goal_status_filter"] = selected_keys
            _save_kanban_pref("goal_status_filter", selected_keys)

        visible_columns = [
            (status_key, status_label) for status_key, status_label in GOAL_STATUS_COLUMNS
            if status_key in selected_keys
        ]

        if not visible_columns:
            st.info("没有选中任何状态——请在上面的筛选框里至少勾选一个要显示的状态。")
        else:
            cols = st.columns(len(visible_columns))
            for col, (status_key, status_label) in zip(cols, visible_columns):
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
                        _render_goal_card(
                            client, g, status_key, cron_next_run_by_id=cron_next_run_by_id,
                        )
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
                from mini_agent.evolution.cron_scheduler import validate_schedule
                schedule_error = validate_schedule(cj_schedule.strip())
                if schedule_error:
                    st.error(f"调度格式不合法：{schedule_error}")
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
        job_id = j.get("id")
        is_system = bool(j.get("is_system")) or str(job_id).startswith("sys:")
        c1, c2, c3, c4, c5 = st.columns([3, 2, 1, 1, 1])
        c1.markdown(f"**{j.get('name')}**　`{j.get('schedule')}`")
        c2.caption(f"下次: {j.get('next_run_str','?')}　运行次数: {j.get('run_count',0)}")
        if c3.button("▶️ 立即运行", key=f"runjob_{job_id}"):
            client.run_cron_job_now(job_id)
            st.rerun()
        toggle_label = "⏸️ 禁用" if j.get("enabled") else "▶️ 启用"
        if c4.button(toggle_label, key=f"togglejob_{job_id}"):
            client.update_cron_job(job_id, enabled=not j.get("enabled"))
            st.rerun()
        # [看板 cron 面板补齐删除功能] 系统内置 job（sys: 前缀）后端拒绝
        # 删除，只允许禁用——这里不给它展示删除按钮，避免用户点了却收到
        # 一个后端 400 报错，体验更直接。用户自定义 job 删除前需要二次
        # 确认（confirm_delete_cron_<id> 这个 session_state 标记控制），
        # 避免误触直接把 job 删没了。
        if not is_system:
            confirm_key = f"confirm_delete_cron_{job_id}"
            if not st.session_state.get(confirm_key):
                if c5.button("🗑️ 删除", key=f"deletejob_{job_id}"):
                    st.session_state[confirm_key] = True
                    st.rerun()
            else:
                if c5.button("⚠️ 确认删除", key=f"deletejob_confirm_{job_id}"):
                    result = client.delete_cron_job(job_id)
                    st.session_state.pop(confirm_key, None)
                    if isinstance(result, dict) and result.get("_error"):
                        st.error(f"删除失败：{result['_error']}")
                    else:
                        st.success(f"已删除 cron job：{j.get('name')}")
                    st.rerun()
                if c5.button("取消", key=f"deletejob_cancel_{job_id}"):
                    st.session_state.pop(confirm_key, None)
                    st.rerun()
        else:
            c5.caption("系统任务")

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
            st.dataframe(rows, width='stretch', hide_index=True)

        condition_stats = stats.get("condition_stats", {})
        if condition_stats:
            st.caption("条件分支（condition）实际执行比例：")
            rows = [
                {"步骤": step_id, "实际执行比例": f"{c.get('true_rate', 0.0) * 100:.1f}%", "样本数": c.get("total", 0)}
                for step_id, c in condition_stats.items()
            ]
            st.dataframe(rows, width='stretch', hide_index=True)


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

    st.divider()
    _render_self_diagnosis_feedback(client)

    st.divider()
    _render_goal_execution_fairness(client)

    st.divider()
    _render_system_connectivity(client)

    st.divider()
    _render_execution_model_status(client)

    st.divider()
    _render_scheduling_overview(client)

    st.divider()
    _render_llm_pool_status(client)

    st.divider()
    _render_growth_health_trend(client)
    # [kanban_perception_gaps_improvement_plan.md 方向 D.2] 记忆库增长
    # 趋势——`growth_health_trend.jsonl` 里已经有 total_entries 等字段，
    # 覆盖了"记忆总条数走势"这个需求，不需要为此单独另起一份存储，只是
    # 换一个展示位置：跟"🌱 成长顾问"tab 复用同一个 `_render_growth_
    # health_trend()` 组件和同一份数据源。

    st.divider()
    _render_goal_stuck_stats(client)


# [goal_stuck_stats_and_llm_progress_judge_plan.md §1] "🧊 Goal Stuck 历史
# 统计"——纯只读聚合，不提供任何操作按钮，只用于回答"这个项目历史上到底
# 有多少次 Goal 被判定卡住"，为后续要不要上更高成本的机制（比如并行多
# 路径择优）提供真实频率参考，而不是凭感觉决定。
def _render_goal_stuck_stats(client: AgentClient) -> None:
    st.markdown("**🧊 Goal Stuck 历史统计**（只读，参考用）")
    data = client.goal_mode_stuck_stats() or {}
    if "_error" in data:
        st.caption(f"读取失败：{data['_error']}")
        return
    total = data.get("total_sessions", 0)
    if total == 0:
        st.caption("暂无 goal_mode 会话历史。")
        return
    stuck_count = data.get("stuck_count", 0)
    stuck_ratio = data.get("stuck_ratio", 0.0)
    recent_count = data.get("recent_stuck_count", 0)
    window_days = data.get("recent_window_days", 30)
    c1, c2, c3 = st.columns(3)
    c1.metric("历史会话总数", total)
    c2.metric("被判 stuck 次数", stuck_count, delta=f"占比 {stuck_ratio:.1%}")
    c3.metric(f"近 {window_days} 天 stuck", recent_count)

    top_texts = data.get("top_stuck_goal_texts") or []
    if top_texts:
        with st.expander(f"反复卡住的目标（共 {len(top_texts)} 个不同描述）", expanded=False):
            for item in top_texts:
                goal_text = item.get("goal_text", "")
                count = item.get("count", 0)
                excerpt = item.get("last_final_report_excerpt", "")
                st.markdown(f"- **{count} 次** — {goal_text}")
                if excerpt:
                    st.caption(f"　最近一次终态摘要：{excerpt}")
    elif stuck_count > 0:
        st.caption("有 stuck 记录，但未能归并出目标描述（可能是较早版本的历史数据）。")


# [kanban_perception_gaps_improvement_plan.md 方向 B.1] "🔀 LLM 故障转移状态"
# ——把已经在内存里现成可用的 LLMClientPool.snapshot() 接上一个只读展示，
# 让"daemon 正在因为限流不断切 key/切配置"这件事不再是用户完全无从得知、
# 只能等所有 fallback 耗尽才发现的黑箱。
def _render_llm_pool_status(client: AgentClient) -> None:
    st.markdown("**🔀 LLM 故障转移状态**")
    data = client.llm_pool_status() or {}
    if "_error" in data:
        st.caption(f"读取失败：{data['_error']}")
        return
    if not data.get("enabled"):
        st.caption("未配置故障转移链（llm_fallback_chain 为空），仅使用单一配置。")
        return

    entries = data.get("entries") or []
    if data.get("switched_from_preferred"):
        st.warning("⚠️ 当前已切换到备用配置，不在首选 provider/model 上")
    else:
        st.caption("✅ 当前使用首选配置")

    for i, entry in enumerate(entries):
        active = entry.get("active")
        label = entry.get("label", f"配置 {i}")
        prefix = "🟢 " if active else "⚪ "
        st.markdown(f"{prefix}**{label}**" + ("（当前激活）" if active else ""))
        keys = entry.get("keys") or []
        if keys:
            for k in keys:
                avail = k.get("available")
                icon = "🟢" if avail else "🔴"
                cooldown = k.get("cooldown_remaining", 0)
                cooldown_txt = f"，冷却剩余 {cooldown}s" if cooldown else ""
                st.caption(
                    f"　{icon} key `...{k.get('key_suffix', '')}` "
                    f"失败次数:{k.get('fail_count', 0)}{cooldown_txt}"
                )

    st.markdown("**📊 LLM 调用统计**（近 7 天，按天聚合）")
    stats_resp = client.llm_call_stats(days=7)
    if stats_resp and "_error" in stats_resp:
        st.caption(f"读取失败：{stats_resp['_error']}")
    else:
        series = (stats_resp or {}).get("series") or []
        if not series:
            st.caption("暂无调用记录")
        else:
            import pandas as pd
            df = pd.DataFrame(series).set_index("day")
            st.bar_chart(df[["call_count", "error_count"]])
            latest = series[-1]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("今日调用", latest.get("call_count", 0))
            c2.metric("今日失败", latest.get("error_count", 0))
            c3.metric("今日输入 token", latest.get("total_input_tokens", 0))
            c4.metric("今日输出 token", latest.get("total_output_tokens", 0))


# ═══════════════════════════════════════════════════════════════════════
# Tab: 🌱 成长顾问（next_doc/growth_advisor_design.md，P1 里程碑）
# ═══════════════════════════════════════════════════════════════════════
# [用户反馈] "运行了一天，成长顾问里的数据都是 0" ——候选/报告数=0 本身
# 不区分"扫描过但没匹配到"和"压根没扫描过/被关掉了"，用户没法自己判断
# 卡在哪一步，只能来问。这里把 `/growth/summary` 里新增的 `diagnostics`
# 字段渲染成一个默认折叠的自检面板：配置快照 + 上次扫描命中了哪些主题
# 各多少条 + 记忆窗口内条目数 + 后台定时任务有没有真的跑过。纯只读展示，
# 不做任何判断/建议式文案（"卡在哪一步"交给用户自己对着这几个数字判断）。
def _render_growth_diagnostics(diagnostics: dict, client: "AgentClient" = None):
    if not diagnostics:
        return
    with st.expander("🩺 我的数据 / 诊断信息（为什么候选是 0？点开看）"):
        cfg = diagnostics.get("config", {})
        scan = diagnostics.get("signal_scan", {})
        mem = diagnostics.get("memory", {})
        cron_jobs = diagnostics.get("cron_jobs", {})

        st.markdown("**配置**")
        st.write(
            f"- 功能开关：{'✅ 已开启' if cfg.get('enabled') else '❌ 已关闭'}\n"
            f"- 成为候选所需最少证据条数：{cfg.get('min_evidence_count')}\n"
            f"- 推送频率：`{cfg.get('notification_frequency')}` "
            f"（置信度阈值 {cfg.get('notification_min_confidence')}）\n"
            f"- 关注领域黑名单：{cfg.get('excluded_topics') or '（无）'}\n"
            f"- LLM 增强信号归纳：{'✅ 已开启' if cfg.get('llm_signal_augment_enabled') else '默认关闭'}"
        )

        # [P4-5] 类别历史采纳率——解释"为什么这条会被优先推送"。
        category_rates = diagnostics.get("category_acceptance_rate") or {}
        if category_rates:
            st.caption(
                "各类别历史采纳率（影响推送优先级）："
                + "，".join(f"{cat} {rate * 100:.0f}%" for cat, rate in category_rates.items())
            )

        st.markdown("**最近一次信号扫描**")
        last_scan_at = scan.get("last_scan_at")
        if last_scan_at:
            st.caption(f"扫描时间：{time.strftime('%Y-%m-%d %H:%M', time.localtime(last_scan_at))}"
                        f"（扫描窗口：最近 {scan.get('window_days')} 天）")
        else:
            st.caption("还没有扫描记录——点上面「🔍 立即为我看看」手动触发一次。")
        hit_counts = scan.get("topic_hit_counts") or {}
        tracked = scan.get("topics_tracked") or []
        if hit_counts:
            for topic in tracked:
                n = hit_counts.get(topic, 0)
                st.write(f"- {topic}：{n} 条命中" + ("" if n else "（暂无）"))
        else:
            st.caption("目前所有内置主题都还没有命中任何记忆条目。")
        topic_hit_counts_note = diagnostics.get("topic_hit_counts_note")
        if topic_hit_counts_note:
            st.caption(f"ℹ️ {topic_hit_counts_note}")

        st.markdown("**记忆数据**")
        st.write(
            f"- 记忆总条数：{mem.get('total_entries', 0)}\n"
            f"- 落在扫描窗口内的条数：{mem.get('entries_in_scan_window', 0)}"
        )

        # [LLM 增强路径可观测性] 三个 opt-in LLM 调用点各自"最近一次调用
        # 结果"——开关开了不代表在正常工作，这里让用户能直接看出来，而
        # 不是只能靠"候选/分类看起来没变化"去猜。
        st.markdown("**LLM 增强调用状态**")
        llm_status = diagnostics.get("llm_call_status") or {}
        _LLM_CALL_LABELS = {
            "signal_augment": "信号扫描增强（llm_signal_augment_enabled）",
            "report_quality": "报告正文润色（report_quality_llm_enabled）",
            "topic_category": "主题分类（topic_category_llm_enabled）",
        }
        _LLM_OUTCOME_LABELS = {
            "success": "✅ 成功",
            "no_new_topics": "✅ 成功（本次没有新发现）",
            "empty_response": "⚠️ 调用成功但响应为空",
            "skipped_insufficient_unmatched": "ℹ️ 未命中记忆太少，本次跳过调用",
            "parse_error": "⚠️ 响应解析失败",
            "error": "❌ 调用抛出异常",
        }
        if not llm_status:
            st.caption("三个 LLM 增强开关目前都还没被触发过（要么全部关闭，要么还没跑过一轮 cron/scan）。")
        else:
            for call_type, label in _LLM_CALL_LABELS.items():
                info = llm_status.get(call_type)
                if not info:
                    st.caption(f"- {label}：尚未触发过")
                    continue
                outcome = info.get("outcome", "")
                ts = info.get("ts")
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "未知时间"
                detail = info.get("detail") or ""
                st.write(
                    f"- {label}：{_LLM_OUTCOME_LABELS.get(outcome, outcome)}（{when}）"
                    + (f"　`{detail}`" if detail else "")
                )

        # [反馈粒度细化] "方向没错，报告没写好"的累计次数，跟正常的
        # dismiss（方向级信号）分开展示，避免被误当成"这个方向不受欢迎"。
        report_quality_flags = diagnostics.get("report_quality_flags_count", 0)
        if report_quality_flags:
            st.caption(
                f"ℹ️ 历史上有 {report_quality_flags} 次「方向没错，是报告没写好」的忽略反馈"
                "——这些不会压低对应方向的置信度，明细可在「月度成长复盘」的"
                "报告质量待改进列表里查看。"
            )

        # [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 2]
        # 反馈模式——纯统计展示，帮用户/系统看见"最近更容易忽略什么"，
        # 不据此调整任何候选排序（明细分布折叠展示，避免默认就把一堆
        # 数字堆在诊断面板正文里）。
        feedback_pattern = diagnostics.get("feedback_pattern") or {}
        if feedback_pattern.get("summary_text"):
            st.markdown("**反馈模式**")
            st.write(feedback_pattern["summary_text"])
            if feedback_pattern.get("llm_insight"):
                # [方向 2 第二步] LLM 归纳的自然语言总结，跟规则式统计
                # 并列展示（不是替换），用 caption 弱化一下视觉权重，
                # 提示这是"补充解读"而不是更权威的结论。
                st.caption(f"💡 {feedback_pattern['llm_insight']}")
            reason_dist = feedback_pattern.get("reason_distribution") or {}
            category_dist = feedback_pattern.get("category_distribution") or {}
            if reason_dist or category_dist:
                with st.expander("查看详细分布", expanded=False):
                    if reason_dist:
                        st.caption(
                            "按忽略原因：" + "，".join(
                                f"{_DISMISS_REASON_DIAGNOSTICS_LABELS.get(r, r)} {n} 次"
                                for r, n in reason_dist.items()
                            )
                        )
                    if category_dist:
                        st.caption(
                            "按方向类别：" + "，".join(
                                f"{cat} {n} 次" for cat, n in category_dist.items()
                            )
                        )

        st.markdown("**后台定时任务**")
        if cron_jobs.get("_note"):
            st.caption(cron_jobs["_note"])
        else:
            for jid, label in (("sys:growth_advisor_daily", "每日扫描"),
                                ("sys:growth_monthly_retrospective", "月度复盘")):
                j = cron_jobs.get(jid)
                if not j:
                    st.caption(f"- {label}（{jid}）：未注册")
                    continue
                last_run = (
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(j["last_run_at"]))
                    if j.get("last_run_at") else "从未运行过"
                )
                st.write(
                    f"- {label}：{'✅ 已启用' if j.get('enabled') else '❌ 已禁用'}，"
                    f"上次运行 {last_run}，累计运行 {j.get('run_count', 0)} 次"
                    + (f"，连续 {j['consecutive_skip_count']} 次到点未触发"
                       if j.get("consecutive_skip_count") else "")
                )


# [next_doc/growth_advisor_improvement_plan_v4.md 方向三 N1] 诊断面板
# 健康度趋势——放在可折叠区块里，用户展开才拉取 `/growth/health_trend`，
# 不影响 tab 默认加载速度。
def _render_growth_health_trend(client: "AgentClient"):
    with st.expander("📈 健康度趋势", expanded=False):
        try:
            data = client.growth_health_trend(limit=30) or {}
        except Exception as e:
            st.caption(f"趋势数据加载失败：{e}")
            return
        rows = data.get("health_trend") or []
        if not rows:
            st.caption("暂无历史快照——健康度趋势在每天一轮的自动扫描"
                        "（sys:growth_advisor_daily）结束后才会记一条，"
                        "至少运行几天后才能看到走势。")
            return
        import pandas as pd
        df = pd.DataFrame(rows)
        df["日期"] = df["recorded_at"].apply(
            lambda ts: time.strftime("%m-%d", time.localtime(ts)) if ts else ""
        )
        df = df.set_index("日期")
        chart_cols = {
            "total_entries": "记忆总条数",
            "backfill_candidates_count": "待回填候选数",
            "topics_tracked_count": "关注主题数",
        }
        present = [c for c in chart_cols if c in df.columns]
        if present:
            st.line_chart(df[present].rename(columns=chart_cols))
        st.caption(
            f"共 {len(rows)} 个数据点。趋势只是把 `/growth/summary` 诊断区块"
            "里的数字每天记一条，不是新的统计口径——对不上的时候，以"
            "「诊断」区块当前展示的数字为准。"
        )


# [next_doc/growth_advisor_improvement_plan_v2.md P4-1] "Agent 对你的了解"
# + "当前关键词列表"——用户反馈"看板应该增加用户的 profile 信息""应该增加
# 成长顾问实际使用的关键词列表"。默认展开（不是诊断信息，是用户想看的），
# 跟上面纯排障用的 `_render_growth_diagnostics` 分开摆放。
def _render_growth_profile_and_keywords(client: "AgentClient", diagnostics: dict):
    user_profile = diagnostics.get("user_profile") or {}
    st.markdown("**🧠 Agent 对你的了解**")
    if not user_profile.get("summary") and not user_profile.get("tech_stack") and not user_profile.get("habits"):
        st.caption("还在观察中，攒够一定数量的记忆后会自动生成画像。")
    else:
        if user_profile.get("summary"):
            st.write(user_profile["summary"])
        if user_profile.get("tech_stack"):
            st.caption("技术栈：" + "、".join(user_profile["tech_stack"]))
        if user_profile.get("habits"):
            st.caption("习惯：" + "、".join(user_profile["habits"]))
        if user_profile.get("updated_at"):
            st.caption(f"更新时间：{time.strftime('%Y-%m-%d %H:%M', time.localtime(user_profile['updated_at']))}")
        # [next_doc/growth_advisor_diagnostics_and_language_fix_plan.md
        # 方向二] 展示当前检测到的用户常用语言，方便确认画像语言是否符合
        # 预期（检测结果由 profile.py::detect_primary_language 生成）。
        if user_profile.get("preferred_language"):
            st.caption(f"检测到的常用语言：`{user_profile['preferred_language']}`")

    # [next_doc/memory_backfill_and_profile_update_plan.md 看板展示]
    # "待复核"特征：距今超过 stale_after_days 天没有被新记忆再次印证，
    # 提醒用户这些可能已经过时（去留仍由下一次画像刷新时 LLM 判断，
    # 这里只做提示，不提供手动删除入口，避免绕过增量更新的既有机制）。
    stale_tech = user_profile.get("stale_tech_stack") or []
    stale_habits = user_profile.get("stale_habits") or []
    if stale_tech or stale_habits:
        stale_days = user_profile.get("stale_after_days", 90)
        with st.expander(f"🕰️ {len(stale_tech) + len(stale_habits)} 条特征已超过 {stale_days} 天未被印证，待下次画像刷新复核"):
            if stale_tech:
                st.caption("技术栈：" + "、".join(stale_tech))
            if stale_habits:
                st.caption("习惯：" + "、".join(stale_habits))

    # [next_doc/memory_backfill_and_profile_update_plan.md M1 看板展示]
    # 记忆回填状态：还有多少存量 session 符合回填条件、系统内置回填
    # cron job 上一次/下一次运行时间——帮用户判断"记忆条目少"是不是因为
    # 回填还没跑到，而不是功能本身没生效。
    backfill_count = (diagnostics.get("memory") or {}).get("backfill_candidates_count", 0)
    backfill_computed_at = (diagnostics.get("memory") or {}).get("backfill_candidates_count_computed_at")
    backfill_job = (diagnostics.get("cron_jobs") or {}).get("sys:memory_backfill_scan")
    if backfill_count or backfill_job:
        st.markdown("**🗄️ 记忆回填状态**")
        if backfill_count:
            st.caption(f"发现 {backfill_count} 个会话有实质内容但尚未生成记忆摘要，等待下一次回填扫描。")
        else:
            st.caption("暂无待回填的存量会话。")
        # [next_doc/growth_diagnostics_backfill_count_cache_plan.md]
        # 这个数字走 5 分钟 TTL 缓存（避免每次打开面板都全量扫描一遍
        # session 目录，曾在 session 数量多时触发过诊断快照超时），这里
        # 提示数据的计算时间，并提供一键强制刷新拿真实最新值的入口。
        if backfill_computed_at:
            age_seconds = max(0, time.time() - backfill_computed_at)
            age_note = (
                "刚刚更新" if age_seconds < 60
                else f"{int(age_seconds // 60)} 分钟前更新"
            )
            refresh_col, note_col = st.columns([1, 3])
            with refresh_col:
                if client is not None and st.button("🔄 刷新诊断数据", key="growth_diag_refresh_btn"):
                    fresh = client.growth_summary(refresh_diagnostics=True)
                    if isinstance(fresh, dict) and fresh.get("_error"):
                        st.error(f"刷新失败：{fresh['_error']}")
                    else:
                        st.rerun()
            with note_col:
                st.caption(f"待回填候选数（{age_note}，点左侧按钮拿最新数据）")
        if backfill_job:
            if not backfill_job.get("enabled"):
                st.caption("回填任务当前已关闭（`sys:memory_backfill_scan`）。")
            else:
                last_run = backfill_job.get("last_run_at")
                next_run = backfill_job.get("next_run_at")
                last_run_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(last_run)) if last_run else "尚未运行"
                next_run_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(next_run)) if next_run else "未知"
                st.caption(f"上次运行：{last_run_text}　下次运行：{next_run_text}")
        else:
            st.caption("（当前非 daemon 模式，无法显示自动回填任务的运行状态；可用 `/memory backfill` 手动执行。）")

    st.markdown("**🔑 当前关键词列表**")
    topics_detail = (diagnostics.get("signal_scan") or {}).get("topics_detail") or []
    built_in = [t for t in topics_detail if t.get("source") == "built_in"]
    learned = [t for t in topics_detail if t.get("source") == "llm_learned" and not t.get("confirmed_by_user")]
    user_added = [t for t in topics_detail if t.get("source") == "user_added" or (t.get("source") == "llm_learned" and t.get("confirmed_by_user"))]

    if built_in:
        st.caption("内置：" + "、".join(f"`{t['topic']}`" for t in built_in))
        # [P4-7] 内置主题此前只能展示，看不到隐藏按钮——后端一直支持
        # （remove_topic_keyword 会把内置主题记进黑名单），这里补上 UI。
        with st.expander("🙈 隐藏某个内置主题"):
            for t in built_in:
                if st.button(f"隐藏「{t['topic']}」", key=f"growth_kw_hide_{t['topic']}"):
                    client.growth_keyword_remove(t["topic"])
                    st.rerun()
    hidden_builtin = diagnostics.get("hidden_builtin_topics") or []
    if hidden_builtin:
        st.caption("已隐藏的内置主题（不会再出现在扫描里）：")
        for topic in hidden_builtin:
            cols = st.columns([4, 1])
            cols[0].write(f"⚪ ~~{topic}~~")
            if cols[1].button("↩️ 恢复", key=f"growth_kw_restore_{topic}"):
                client.growth_keyword_restore(topic)
                st.rerun()
    for t in learned:
        cols = st.columns([4, 1, 1])
        streak = t.get("consecutive_scan_hits", 0)
        streak_hint = f"（连续命中 {streak} 次，满 3 次自动保留）" if streak else ""
        cols[0].write(f"🟡 待确认：**{t['topic']}**（{', '.join(t['keywords'])}）{streak_hint}")
        if cols[1].button("✅ 保留", key=f"growth_kw_confirm_{t['topic']}"):
            client.growth_keyword_confirm(t["topic"])
            st.rerun()
        if cols[2].button("❌ 不要", key=f"growth_kw_reject_{t['topic']}"):
            client.growth_keyword_remove(t["topic"])
            st.rerun()
    for t in user_added:
        cols = st.columns([5, 1])
        auto_tag = " 🤖 自动保留" if t.get("auto_confirmed") else ""
        cols[0].write(f"🔵 **{t['topic']}**（{', '.join(t['keywords'])}）{auto_tag}")
        if cols[1].button("❌ 删除", key=f"growth_kw_remove_{t['topic']}"):
            client.growth_keyword_remove(t["topic"])
            st.rerun()

    with st.form("growth_add_keyword_form", clear_on_submit=True):
        st.caption("➕ 添加自定义关注主题")
        new_topic = st.text_input("主题名", key="growth_new_topic")
        new_keywords = st.text_input("关键词（逗号分隔）", key="growth_new_keywords")
        if st.form_submit_button("添加"):
            if new_topic.strip() and new_keywords.strip():
                result = client.growth_keyword_add(new_topic.strip(), new_keywords.strip())
                if result and "_error" in result:
                    st.error(result["_error"])
                else:
                    st.success(f"已添加「{new_topic.strip()}」")
                    st.rerun()
            else:
                st.warning("主题名和关键词都不能为空")


def render_growth_tab(client: "AgentClient"):
    st.markdown("#### 🌱 成长顾问 (Growth Advisor)")
    st.caption(
        "从最近的对话/记忆里发现的成长方向候选——只是建议，采纳与否始终由你决定，"
        "忽略的方向短期内不会重复出现。"
    )

    col_a, col_b = st.columns([1, 5])
    with col_a:
        if st.button("🔍 立即为我看看", key="growth_scan_btn"):
            with st.spinner("正在扫描最近的信号..."):
                result = client.growth_scan()
            # [BUGFIX] 之前这里用 st.error/st.info/st.success + 紧跟的无条件
            # st.rerun()：Streamlit 的 rerun 会立刻重新执行整个脚本，这些
            # 消息在还没被用户看到之前就被下一次渲染冲掉了，表现为"点了按钮
            # 好像没反应"——不管扫描成功、跳过还是报错，用户都看不到反馈。
            # 改用 st.toast()（跨 st.rerun() 仍会展示，见上面 2098 行附近
            # 同类用法），不需要额外的 session_state 搬运。
            if result and "_error" in result:
                st.toast(f"❌ 扫描失败：{result['_error']}", icon="❌")
            elif result and result.get("skipped"):
                st.toast(f"ℹ️ 跳过：{result.get('reason')}", icon="ℹ️")
            else:
                n_c = len(result.get("new_candidates", [])) if result else 0
                n_r = len(result.get("reports", [])) if result else 0
                st.toast(f"✅ 完成：新增/更新候选 {n_c} 条，生成调研报告 {n_r} 份。", icon="✅")
            st.rerun()

    data = client.growth_summary() or {}
    if "_error" in data:
        st.warning(data["_error"])
        return

    # [next_doc/growth_advisor_design.md] 第 8 节第 1 条：功能默认开启，
    # 首次触达必须透明告知。是否展示过跨会话持久化在
    # growth_advisor_state.json 里（由 `/growth/summary` 一并返回），
    # 展示后调用 `/growth/first_touch_ack` 落盘、之后不再重复弹出。
    if not data.get("first_touch_notice_shown"):
        st.info(
            "已为你开启「成长顾问」：它会用你已有的对话记忆、目标记录等信息，"
            "每天悄悄看一眼有没有值得推进的成长方向，生成调研报告放在这里，"
            "不会额外采集新数据。不想要的话可以在「⚙️ 配置」里随时关闭。",
            icon="🌱",
        )
        client.growth_first_touch_ack()

    candidates = data.get("candidates", [])
    reports = {r["report_id"]: r for r in data.get("reports", [])}
    retro = data.get("retrospective", {})
    diagnostics = data.get("diagnostics", {})

    _render_growth_diagnostics(diagnostics, client)
    _render_growth_health_trend(client)
    _render_growth_profile_and_keywords(client, diagnostics)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("候选总数", retro.get("total_candidates", 0))
    c2.metric("已采纳", retro.get("accepted", 0))
    c3.metric("已忽略", retro.get("dismissed", 0))
    c4.metric("调研报告", retro.get("reports_generated", 0))

    # P2：采纳率 + 主题排行（方案第 6 节"推荐命中率"指标），只在有过采纳/
    # 忽略决策时才展示，避免新用户看到一个没有意义的 0%。
    acceptance_rate = retro.get("acceptance_rate")
    if acceptance_rate is not None:
        st.caption(f"推荐采纳率：**{acceptance_rate * 100:.1f}%**（基于已做出采纳/忽略决策的候选）")
        top_accepted = retro.get("top_accepted_topics") or []
        top_dismissed = retro.get("top_dismissed_topics") or []
        if top_accepted or top_dismissed:
            with st.expander("按主题看采纳/忽略排行"):
                ta, td = st.columns(2)
                with ta:
                    st.markdown("**最常被采纳**")
                    for title, n in top_accepted:
                        st.write(f"- {title}（{n} 次）")
                with td:
                    st.markdown("**最常被忽略**")
                    for title, n in top_dismissed:
                        st.write(f"- {title}（{n} 次）")

    # [反馈粒度细化] "方向没错，是报告没写好"的排行——跟上面"最常被
    # 忽略"分开展示，前者不代表用户不喜欢这个方向，只是报告质量该改进。
    top_report_quality_flags = retro.get("top_report_quality_flags") or []
    if top_report_quality_flags:
        with st.expander(
            f"📄 报告质量待改进（{retro.get('report_quality_flags_total', 0)} 次「方向没错，报告没写好」反馈）"
        ):
            st.caption("这些方向本身没有被判定为不受欢迎，只是生成的调研报告没能打动用户，"
                       "考虑打开 report_quality_llm_enabled 或人工改写模板。")
            for title, n in top_report_quality_flags:
                st.write(f"- {title}（{n} 次）")

    # P3：跨候选能力地图聚合（growth_topic_map）——按主题聚合的完整推进
    # 轨迹（含峰值置信度、历史累计出现/采纳/忽略次数），比 4.1 节的
    # Top5 排行更完整，默认折叠，避免挤占候选列表的首屏空间。
    topic_map = retro.get("topic_map") or []
    if topic_map:
        with st.expander(f"🗺️ 成长主题地图（{len(topic_map)} 个方向）"):
            st.caption("这里是历史累计视角，跟上面诊断面板「最近一次扫描」的命中计数是两个口径。")
            for row in topic_map:
                status_label = {
                    "pending": "待处理", "accepted": "已采纳",
                    "dismissed": "已忽略", "expired": "已过期",
                }.get(row.get("current_status"), row.get("current_status"))
                st.write(
                    f"- **{row.get('topic')}** — {status_label}"
                    f"（峰值置信度 {row.get('peak_confidence', 0):.2f}，"
                    f"出现 {row.get('occurrences', 0)} 次，"
                    f"采纳 {row.get('times_accepted', 0)} / "
                    f"忽略 {row.get('times_dismissed', 0)}）"
                )
                # [P4-6] 简单文字走势：只展示证据数的涨跌箭头序列，不引入
                # 图表库——"简单折线都可以"，这里选了更轻量的文字版本。
                trend = row.get("evidence_trend") or []
                if len(trend) >= 2:
                    counts = [pt["evidence_count"] for pt in trend]
                    arrows = "".join(
                        "↗" if b > a else ("↘" if b < a else "→")
                        for a, b in zip(counts, counts[1:])
                    )
                    st.caption(f"　证据数走势（最近 {len(counts)} 轮）：{counts[0]} {arrows} {counts[-1]}")
                # [growth_advisor_active_search_and_lifecycle_plan.md
                # 方向二] 用该主题最新一条候选的 id 查完整轨迹。
                map_cid = row.get("candidate_id")
                if map_cid and st.button(
                    "🕒 查看轨迹", key=f"growth_map_timeline_{map_cid}"
                ):
                    _render_growth_topic_timeline(client, map_cid)

    _render_growth_followups(client)
    _render_growth_alignment(client)
    _render_growth_pursuits(client)
    _render_growth_report_refresh_candidates(client)
    _render_growth_report_viewer(client, candidates)

    pending = [c for c in candidates if c.get("status") == "pending"]
    if not pending:
        st.caption("当前没有待处理的候选。点击上方按钮手动触发一轮扫描，"
                    "或等待每日自动扫描（sys:growth_advisor_daily）。")
        return

    st.markdown("**待处理候选**")
    if _sortable_available():
        _render_growth_kanban_dragdrop(client, candidates)
    else:
        _render_growth_pending_list(client, pending)


def _render_growth_report_viewer(client: "AgentClient", candidates: list[dict]):
    """[修复] 顶部「调研报告」计数是历史累计总数，但此前只有「待处理候选」
    卡片上才有「📄 查看报告」按钮——候选一旦被采纳/忽略/过期，报告就从
    界面上"消失"了（数字显示有、点不到）。这里补一个不依赖候选当前状态
    的独立查看入口：只要候选身上挂着 report_id（不管 pending/accepted/
    dismissed/expired），都能在这里选中查看，与拖拽看板/列表视图各自的
    按钮并存，不影响原有交互。"""
    has_report = [c for c in candidates if c.get("report_id")]
    if not has_report:
        return
    with st.expander(f"📄 查看调研报告（{len(has_report)} 个候选已生成）", expanded=False):
        _STATUS_LABEL = {
            "pending": "🕗 待处理", "accepted": "✅ 已采纳",
            "dismissed": "🙈 已忽略", "expired": "⌛ 已过期",
        }
        # [交互改进] 原来用下拉框逐个切换查看，选中态和操作按钮的对应关系
        # 不直观，且每次只能看一个、来回切换成本高。改成把所有候选平铺
        # 展开，每个候选自带「查看/收起报告」「落地为 Goal」两个操作，
        # 报告内容按候选各自缓存在 session_state 里，避免重复请求。
        for c in sorted(has_report, key=lambda x: -x.get("confidence", 0)):
            status_label = _STATUS_LABEL.get(c.get("status"), c.get("status", ""))
            candidate_id = c.get("candidate_id", "")
            short_id = str(candidate_id)[:8]
            st.markdown(f"**[{status_label}] {c.get('title', '')}** · `{short_id}`")

            cache_key = f"growth_report_cache_{candidate_id}"
            vcol1, vcol2 = st.columns([1, 1])
            is_shown = st.session_state.get(cache_key) is not None
            toggle_label = "收起报告" if is_shown else "查看报告"
            if vcol1.button(toggle_label, key=f"growth_report_viewer_btn_{candidate_id}"):
                if is_shown:
                    st.session_state.pop(cache_key, None)
                else:
                    rep = client.growth_report(c["report_id"])
                    st.session_state[cache_key] = rep or {"_error": "读取报告失败"}
                st.rerun()

            # [采纳即启动] 默认情况下这一步已经由 accept 动作自动做完
            # （生成 Goal → 生成并确认执行规范 → 绑定周期性），这里只是
            # 展示状态；用户仍可以随时去「🎯 目标」tab 手动暂停/调整。
            if c.get("linked_goal_id"):
                vcol2.caption(f"🔄 已落地为 Goal（{c['linked_goal_id'][:8]}）")
            else:
                if vcol2.button("🚀 落地为 Goal（继续调研）", key=f"growth_report_viewer_adopt_btn_{candidate_id}"):
                    result = client.growth_candidate_adopt_goal(candidate_id)
                    if result and "_error" not in result:
                        st.success(
                            "已创建 Goal，可以在「🎯 目标」tab 里把它设为周期性，"
                            "由成长顾问之前的调研报告继续深入。"
                        )
                        st.rerun()
                    else:
                        st.error((result or {}).get("_error", "落地失败"))

            rep = st.session_state.get(cache_key)
            if rep is not None:
                if "_error" not in rep:
                    st.caption(
                        f"生成于 {rep.get('generated_at', '')} · "
                        f"证据 {rep.get('evidence_count_at_generation', c.get('evidence_count', 0))} 条"
                    )
                    st.markdown(rep.get("body", "（报告正文为空）"))
                else:
                    st.error(rep.get("_error", "读取报告失败"))
            st.divider()




def _render_growth_followups(client: "AgentClient"):
    """[P4-3] 采纳后回访：候选被采纳一段时间后，问一次"有没有真的推进"，
    答案反馈进置信度调权。默认折叠展示，避免没有待回访项时挤占首屏。"""
    data = client.growth_followups() or {}
    followups = data.get("followups") or []
    if not followups:
        return
    with st.expander(f"📮 该回访一下了（{len(followups)} 个方向）", expanded=True):
        st.caption("这些方向是你之前采纳的，过去一段时间没再问过——推进得怎么样了？")
        for c in followups:
            cols = st.columns([4, 1, 1])
            cols[0].write(f"**{c.get('title')}**")
            if cols[1].button("✅ 有推进", key=f"growth_followup_progressed_{c['candidate_id']}"):
                client.growth_followup_record(c["candidate_id"], "progressed")
                st.rerun()
            if cols[2].button("🕒 还没空", key=f"growth_followup_stalled_{c['candidate_id']}"):
                client.growth_followup_record(c["candidate_id"], "stalled")
                st.rerun()


def _render_growth_alignment(client: "AgentClient"):
    """[growth_advisor_autonomy_deepening_plan.md 方向 A3] 兴趣方向 ⇄
    Goal 对齐分析：展示"有兴趣信号但还没建目标"的方向，提供"全部采纳"
    批量入口——复用 `auto_pursue_candidate()` 整条链路，单次最多处理
    `goal_alignment_adopt_all_max_batch`（默认 3）条，避免一次点击就
    意外触发过多 LLM 调用；剩余条目留到下次点击继续处理。

    [growth_advisor_autonomy_deepening_plan_v2.md 方向 2] 同时展示
    `llm_suggested_matches`（LLM 认为语义相关但字面不完全一致的候选
    配对），每条带一个"🔗 关联"按钮，确认后调用 `confirm_llm_suggested_
    match()` 把对应候选关联到已存在的 Goal（不新建）。"""
    data = client.growth_align() or {}
    if not data.get("enabled", True):
        return
    unmatched = data.get("unmatched_interests") or []
    suggested = data.get("llm_suggested_matches") or []
    if not unmatched and not suggested:
        return
    with st.expander(f"🧭 有兴趣但还没建目标（{len(unmatched)} 个方向）", expanded=False):
        st.caption("这些方向最近反复出现在你的活动里，但还没有对应的目标——"
                   "可以逐条查看，或点击下面按钮批量落地成自主持续调研的目标。")
        for row in unmatched:
            mark = "（已有候选记录，可批量落地）" if row.get("candidate_id") else "（还没有候选记录，需先 /growth scan）"
            st.write(f"- {row.get('topic')} · 证据数={row.get('evidence_count')} {mark}")
        adoptable = [r for r in unmatched if r.get("candidate_id")]
        if adoptable and st.button(f"🚀 全部采纳（最多一次处理 {min(len(adoptable), 3)} 条）", key="growth_align_adopt_all"):
            result = client.growth_align_adopt_all() or {}
            processed = result.get("processed") or []
            for entry in processed:
                if entry.get("goal_id"):
                    st.toast(f"「{entry.get('topic')}」已落地为目标", icon="✅")
                else:
                    st.toast(f"「{entry.get('topic')}」落地失败：{'；'.join(entry.get('errors') or ['未知原因'])}", icon="⚠️")
            remaining = result.get("remaining_count", 0)
            if remaining:
                remaining_topics = result.get("remaining_topics") or []
                topics_str = "、".join(remaining_topics) if remaining_topics else ""
                st.info(f"还有 {remaining} 条未处理（本次批量上限已用完），可再次点击继续。" + (f"待处理：{topics_str}" if topics_str else ""))
            st.rerun()

        # [growth_advisor_autonomy_deepening_plan_v2.md 方向 2] LLM 建议
        # 的语义相关配对：字面不完全一致，只展示，用户逐条确认后才正式
        # 关联到已存在的 Goal（不新建）。
        if suggested:
            st.markdown("**🔗 语义相关的建议**（字面不完全一致，确认后关联到已有目标）")
            for row in suggested:
                cols = st.columns([4, 1])
                cols[0].write(f"{row.get('topic')} ≈ 目标「{row.get('goal_title')}」")
                if cols[1].button("🔗 关联", key=f"growth_align_confirm_{row.get('topic')}_{row.get('goal_id')}"):
                    confirm_result = client.growth_align_confirm_match(row.get("topic"), row.get("goal_id")) or {}
                    if confirm_result.get("ok"):
                        st.toast(f"已将「{row.get('topic')}」关联到「{row.get('goal_title')}」", icon="🔗")
                    else:
                        st.toast(f"关联失败：{confirm_result.get('reason')}", icon="⚠️")
                    st.rerun()


def _render_growth_pursuits(client: "AgentClient"):
    """[growth_advisor_autonomy_deepening_plan.md 方向 D1/D2] "🔄 正在
    自主推进"总览：已采纳且关联了 Goal 的候选，直接在成长顾问 tab 里
    展示进展（第几轮、下次执行时间）和饱和度信号，并提供就近的暂停/
    恢复入口——不要求用户理解"这背后是一个 Goal + 一个 cron job"，
    对用户暴露的心智模型始终是"成长顾问在帮我调研 X"。"""
    data = client.growth_pursuits() or {}
    rows = data.get("pursuits") or []
    if not rows:
        return
    active = [r for r in rows if r.get("recurring")]
    paused = [r for r in rows if not r.get("recurring")]
    with st.expander(f"🔄 正在自主推进（{len(active)} 个方向）", expanded=bool(active)):
        st.caption("这些是你采纳过、成长顾问正在按周期自动持续调研的方向——素材持续追加到同一份"
                   "页面，不需要你手动触发。")
        # [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 4]
        # 跨方向全局视角摘要：把已经分散展示的饱和度/参与度信号聚合成
        # 一句话，帮用户判断"该先看哪几个方向"——纯展示，不自动排序/
        # 暂停任何方向，怎么处理仍然由用户自己决定。
        if active:
            try:
                portfolio = client.growth_pursuits_portfolio_summary() or {}
            except Exception:
                portfolio = {}
            attention = portfolio.get("attention_needed") or []
            if attention:
                names = "、".join(f"「{a.get('title')}」" for a in attention[:3])
                more = f" 等 {len(attention)} 个" if len(attention) > 3 else ""
                st.info(f"💡 {len(attention)} 个方向可能需要你看一眼：{names}{more}")
            elif portfolio.get("total"):
                st.caption(f"{portfolio.get('total')} 个方向都在正常推进，暂时没有需要特别关注的。")
            # [规划维度候选] 调研路径关联信号：哪些方向内容上互相有共现，
            # 值得关联查看——纯提示，不改变任何排序/执行，怎么处理仍然
            # 由用户自己决定。
            try:
                related_data = client.growth_pursuits_related_directions() or {}
            except Exception:
                related_data = {}
            relations = related_data.get("relations") or []
            if relations:
                lines = "；".join(
                    f"「{r.get('title')}」↔「{r.get('related_title')}」"
                    for r in relations[:3]
                )
                more = f" 等 {len(relations)} 组" if len(relations) > 3 else ""
                st.caption(f"🔗 内容上可能有关联，值得互相参考：{lines}{more}")
        # [growth_advisor_autonomy_deepening_plan_v2.md 方向 5] 批量操作
        # 入口：单个方向的暂停/恢复已经很短，但同时有多个方向在跑时，
        # 逐个点还是麻烦（比如要出差一段时间）。这里只是循环调用已有的
        # `unrecur_goal()` / `recur_goal()`（跟单条"⏸ 暂停"完全同一个
        # 后端能力），不新增后端接口，也不自动触发——仍然要用户显式
        # 点击 + 确认。"全部恢复"故意不做（详见方案文档 5 节理由）。
        if active:
            with st.popover("⚙ 批量操作"):
                st.caption(f"将对全部 {len(active)} 个正在自主推进的方向生效。")
                if st.button(f"⏸ 全部暂停（{len(active)} 个）", key="growth_pursuit_pause_all"):
                    for row in active:
                        client.unrecur_goal(row["goal_id"])
                    st.toast(f"已暂停全部 {len(active)} 个方向的自主调研", icon="⏸")
                    st.rerun()
                new_schedule = st.selectbox(
                    "批量调整频率为：", ["interval:86400", "interval:604800"],
                    format_func=lambda s: {"interval:86400": "每天", "interval:604800": "每周"}.get(s, s),
                    key="growth_pursuit_batch_schedule",
                )
                if st.button(f"⚙ 全部调整为该频率（{len(active)} 个）", key="growth_pursuit_batch_reschedule"):
                    for row in active:
                        client.recur_goal(row["goal_id"], new_schedule)
                    st.toast(f"已将 {len(active)} 个方向的调度频率批量调整", icon="⚙")
                    st.rerun()
        for row in active:
            saturation = row.get("saturation") or {}
            cols = st.columns([4, 1.2, 1])
            with cols[0]:
                label = f"**{row.get('title')}** — 第 {row.get('cycle_count', 0)} 轮"
                if row.get("next_run_at"):
                    label += f" · 下次 {row['next_run_at']}"
                st.write(label)
                schedule_line = f"调度：{row.get('schedule') or '（未知）'}"
                # [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md
                # 方向 6] 调研风格标记：纯展示，帮用户理解"这个方向的素材
                # 会偏实操案例、还是偏结构化脉络、还是偏打卡提醒"。未分类
                # （旧 Goal）时不展示，不影响既有布局。
                pursuit_style = row.get("pursuit_style")
                if pursuit_style:
                    schedule_line += f" · 🧭 {pursuit_style}"
                st.caption(schedule_line)
                if saturation.get("saturated"):
                    st.warning(
                        f"⚠️ 最近连续 {saturation.get('streak')} 轮新增内容不多了，"
                        "可能已经了解得差不多，考虑降低频率或先告一段落。"
                    )
                    # [growth_advisor_autonomy_deepening_plan_v2.md 方向 1]
                    # 只在实际触发过 LLM 复核时展示，跟规则式判断分开
                    # 呈现，不互相覆盖——LLM 认为其实有实质推进时单独
                    # 提示，避免用户只看到规则式结论就误以为已经定论。
                    if saturation.get("llm_reviewed"):
                        if saturation.get("llm_verdict") is False:
                            st.caption(
                                f"🤖 LLM 复核认为其实有实质推进（{saturation.get('llm_reason') or '未提供理由'}），"
                                "仅供参考，上面的规则式判断不受影响。"
                            )
                        elif saturation.get("llm_verdict") is True:
                            st.caption(
                                f"🤖 LLM 复核也认为确实低增量（{saturation.get('llm_reason') or '未提供理由'}）。"
                            )
                # [growth_advisor_autonomy_deepening_plan_v2.md 方向 3]
                # 饱和度历史趋势：只读、按需拉取，帮用户判断"降频建议
                # 有没有用"——比如降频之后新增内容是不是又回升了。
                with st.expander("📈 饱和度走势", expanded=False):
                    trend = client.growth_pursuit_saturation_trend(row["goal_id"]) or {}
                    points = trend.get("saturation_trend") or []
                    if not points:
                        st.caption("暂无足够历史数据（需要至少两轮才能开始计算增量）。")
                    else:
                        marks = "".join("🔴" if p.get("low_increment") else "🟢" for p in points)
                        st.caption(f"最近 {len(points)} 轮（🟢 有实质增量 · 🔴 疑似低增量）：{marks}")
                        # [方向 1] 有 LLM 复核过的轮次额外标一个 🤖，
                        # 悬停/展开不方便时至少能看出"这几轮有没有被
                        # 复核过"，具体理由仍以上面最新一条的 caption 为准。
                        reviewed = [p for p in points if p.get("llm_reviewed")]
                        if reviewed:
                            st.caption(f"其中 {len(reviewed)} 轮触发过 LLM 复核（🤖）。")
                # [方向 C2] 还没打包进推送消息的"本轮新增摘要"，看板里先
                # 展示出来，不用等下一次推送才看到最新进展。
                pending_digest = row.get("pending_digest") or []
                if pending_digest:
                    latest_topics = "、".join(pending_digest[-1].get("new_subtopics") or [])
                    st.caption(f"🆕 本轮新增：{latest_topics}")
                # [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md
                # 方向 1] 素材参与度：纯展示，不做警告/阻断，用户自己
                # 判断要不要点进去看。从未查看过时单独提示一句，跟"已经
                # 看过、但又新了几轮"区分开，语义更准确。
                engagement = row.get("engagement") or {}
                cycles_since = engagement.get("cycles_since_last_view")
                if engagement.get("last_viewed_cycle") is None:
                    if cycles_since:
                        st.caption(f"👀 你还没查看过这份素材（已有 {cycles_since} 轮内容）")
                elif cycles_since:
                    st.caption(f"👀 距你上次查看已经过了 {cycles_since} 轮新内容")
            with cols[1]:
                if st.button("⏸ 暂停", key=f"growth_pursuit_pause_{row['goal_id']}"):
                    client.unrecur_goal(row["goal_id"])
                    st.toast(f"已暂停「{row.get('title')}」的自主调研", icon="⏸")
                    st.rerun()
            with cols[2]:
                if st.button("📄 素材", key=f"growth_pursuit_view_{row['goal_id']}"):
                    # [方向 1] 记一次查看埋点；失败不阻塞打开素材本身。
                    try:
                        client.growth_pursuit_view_material(row["goal_id"])
                    except Exception:
                        pass
                    st.session_state["_growth_pursuit_view_goal"] = row["goal_id"]
                    st.rerun()
        if paused:
            st.markdown("**已暂停**")
            for row in paused:
                cols = st.columns([4, 1])
                cols[0].write(f"{row.get('title')} — 已完成 {row.get('cycle_count', 0)} 轮")
                if cols[1].button("▶ 恢复", key=f"growth_pursuit_resume_{row['goal_id']}"):
                    schedule = row.get("schedule") or "interval:86400"
                    client.recur_goal(row["goal_id"], schedule)
                    st.toast(f"已恢复「{row.get('title')}」的自主调研", icon="▶")
                    st.rerun()

    viewing_goal = st.session_state.get("_growth_pursuit_view_goal")
    if viewing_goal:
        row = next((r for r in rows if r.get("goal_id") == viewing_goal), None)
        if row is not None:
            with st.expander(f"📄 「{row.get('title')}」当前素材", expanded=True):
                spec_resp = client.get_execution_spec(viewing_goal)
                if spec_resp and "_error" not in spec_resp:
                    st.caption("执行规范里声明的产出物（wiki 页面）请到「🎯 目标」tab 对应 Goal 的"
                               "输出目录里查看最新内容——这里先展示轮次/来源等元信息。")
                    st.json(spec_resp, expanded=False)
                else:
                    st.caption("该 Goal 还没有执行规范，或暂时无法读取。")
                if st.button("收起", key="growth_pursuit_view_close"):
                    st.session_state.pop("_growth_pursuit_view_goal", None)
                    st.rerun()


def _render_growth_report_refresh_candidates(client: "AgentClient"):
    """[P4-4] 增量刷新：候选证据数比生成报告时又明显增长，提示"要不要
    更新一下这份报告"，不强制、只提示。"""
    data = client.growth_reports_refresh_candidates() or {}
    rows = data.get("refresh_candidates") or []
    if not rows:
        return
    with st.expander(f"🔄 有 {len(rows)} 份报告可以更新一下了"):
        st.caption("这些方向自从生成报告后，又积累了不少新的相关记忆——要不要重新生成一份？")
        for row in rows:
            cols = st.columns([4, 1])
            cols[0].write(
                f"**{row.get('title')}** — 新增证据 {row.get('new_evidence')} 条"
                f"（{row.get('evidence_count_at_generation')} → {row.get('evidence_count')}）"
            )
            if cols[1].button("🔄 更新", key=f"growth_refresh_report_{row['candidate_id']}"):
                with st.spinner("正在重新生成报告..."):
                    client.growth_candidate_refresh_report(row["candidate_id"])
                st.rerun()


def _sortable_available() -> bool:
    try:
        import streamlit_sortables  # noqa: F401
        return True
    except ImportError:
        return False


_GROWTH_DISMISS_REASON_OPTIONS = [
    ("unspecified", "不说明原因"),
    ("not_interested", "不感兴趣"),
    ("bad_timing", "方向可以，但现在不是时候"),
    ("report_not_useful", "方向没错，是报告没写好"),
    ("already_exists", "已存在该主题（和已有方向重复）"),
]

# [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 2] 诊断面板
# "反馈模式"区块展示用的短标签，跟上面 selectbox 里的长句子分开——诊断
# 区块是概览性质，用更短的词更适合跟数字拼在一句 caption 里。
_DISMISS_REASON_DIAGNOSTICS_LABELS = {
    "unspecified": "未说明原因",
    "not_interested": "不感兴趣",
    "bad_timing": "时机不对",
    "report_not_useful": "报告没写好",
    "already_exists": "已存在该主题",
}


# [next_doc/growth_advisor_active_search_and_lifecycle_plan.md 方向二]
# 单个主题的成长轨迹时间线：一条 SVG 水平轴线 + 每个事件一个节点/图标，
# 悬停可看完整文案；下面附文字列表兜底（SVG 在极窄屏幕/打印场景可能
# 显示不佳，文字列表始终可读）。不引入图表库，纯手写 SVG，跟项目里
# 其它可视化区块（P4-6 走势箭头）一样保持轻量。
_GROWTH_TIMELINE_STAGE_ICONS = {
    "discovered": "🔍",
    "report_generated": "📄",
    "accepted": "✅",
    "dismissed": "🙈",
    "goal_linked": "🎯",
    "goal_active": "🚧",
    "goal_completed": "🏁",
    "goal_stalled": "⚠️",
}
_GROWTH_TIMELINE_STAGE_COLORS = {
    "discovered": "#8888aa",
    "report_generated": "#4a90d9",
    "accepted": "#2e9e5b",
    "dismissed": "#b0705a",
    "goal_linked": "#c98a2b",
    "goal_active": "#c98a2b",
    "goal_completed": "#2e9e5b",
    "goal_stalled": "#b0705a",
}


def _build_growth_timeline_svg(topic: str, events: list[dict]) -> str:
    n = len(events)
    left_pad, right_pad, top = 40, 40, 70
    width = max(560, left_pad + right_pad + (n - 1) * 140 if n > 1 else 560)
    height = 150
    axis_y = top
    if n == 1:
        xs = [width / 2]
    else:
        span = width - left_pad - right_pad
        xs = [left_pad + span * i / (n - 1) for i in range(n)]

    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto;font-family:sans-serif;">',
        f'<line x1="{left_pad}" y1="{axis_y}" x2="{width - right_pad}" y2="{axis_y}" '
        f'stroke="#cccccc" stroke-width="2" />',
    ]
    for i, e in enumerate(events):
        x = xs[i]
        stage = e.get("stage", "")
        color = _GROWTH_TIMELINE_STAGE_COLORS.get(stage, "#888888")
        icon = _GROWTH_TIMELINE_STAGE_ICONS.get(stage, "•")
        ts = e.get("ts")
        ts_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "?"
        label = _esc_html(e.get("label", ""))[:24]
        label_y = axis_y - 26 if i % 2 == 0 else axis_y + 40
        line_y2 = axis_y - 8 if i % 2 == 0 else axis_y + 8
        parts.append(f'<line x1="{x}" y1="{axis_y}" x2="{x}" y2="{line_y2}" stroke="{color}" stroke-width="2" />')
        parts.append(f'<circle cx="{x}" cy="{axis_y}" r="7" fill="{color}"><title>{label} ({ts_str})</title></circle>')
        parts.append(
            f'<text x="{x}" y="{label_y}" text-anchor="middle" font-size="11" fill="#333333">{icon} {label}</text>'
        )
        parts.append(
            f'<text x="{x}" y="{label_y + (14 if i % 2 == 0 else -14)}" '
            f'text-anchor="middle" font-size="10" fill="#999999">{ts_str}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _render_growth_topic_timeline(client: "AgentClient", candidate_id: str) -> None:
    data = client.growth_candidate_timeline(candidate_id)
    if not data or "_error" in data:
        st.error((data or {}).get("_error", "读取成长轨迹失败"))
        return
    events = data.get("events") or []
    topic = data.get("topic", "")
    if not events:
        st.caption("暂无可展示的成长轨迹。")
        return
    st.markdown(f"**{topic} 的成长轨迹**")
    try:
        svg = _build_growth_timeline_svg(topic, events)
        st.markdown(svg, unsafe_allow_html=True)
    except Exception:
        # SVG 渲染失败（极端数据/环境问题）不应该挡住文字版兜底。
        pass
    with st.expander("查看文字版详情", expanded=False):
        for e in events:
            ts = e.get("ts")
            ts_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "?"
            icon = _GROWTH_TIMELINE_STAGE_ICONS.get(e.get("stage"), "•")
            st.write(f"{icon} `{ts_str}` {e.get('label', '')}")


def _render_growth_pending_list(client: "AgentClient", pending: list[dict]):
    """P1 起就有的列表 + 按钮渲染方式。作为 `streamlit-sortables` 未安装
    时的兜底路径保留——不强制要求这个可选依赖。"""
    for c in sorted(pending, key=lambda x: -x.get("confidence", 0)):
        with st.container(border=True):
            title_line = f"**{c['title']}**"
            if c.get("origin") == "pursuit_spinoff":
                # [方向 3] 纯展示标记，帮用户理解"为什么会突然冒出这个
                # 建议"——不是来自对话记忆，而是从另一个正在推进的方向里
                # 牵出来的衍生话题。
                title_line += "  🔗 来自你正在推进的方向"
            st.markdown(f"{title_line}  \n{c.get('rationale', '')}")
            st.caption(
                f"置信度 {c.get('confidence', 0)} · 证据 {c.get('evidence_count', 0)} 条"
            )
            # [看板卡顿修复] 之前"忽略原因" selectbox 直接摆在 st.container
            # 里，Streamlit 的默认行为是"任何 widget 值一变化就立刻触发整页
            # rerun"——虽然选完原因本身并不会调用 client（只是本地算出
            # reason_value，真正提交在下面按钮点击时才发生），但这次多余
            # 的整页 rerun 会重新跑一遍页面函数，包括本 tab 之外那些较重的
            # API 调用（诊断信息、回访、Goal 对齐分析等），表现为"选完原因
            # 卡住好一阵才刷新"。用 st.form 包住"选原因 + 采纳/忽略"这一组
            # 交互：st.form 内部的 widget 变化不会触发 rerun，只有点击
            # `st.form_submit_button` 才会——这样选原因不再触发任何 rerun，
            # 真正提交（调用 growth_candidate_action）仍然只发生在点击
            # 「采纳」/「忽略」的那一刻，跟需求描述的"选完原因、点了采纳/
            # 忽略才提交"完全一致。「查看报告」/「轨迹」跟"原因"无关，且
            # `st.button` 不允许出现在 `st.form` 内部，保留在 form 外。
            reason_key = f"growth_dismiss_reason_{c['candidate_id']}"
            with st.form(key=f"growth_form_{c['candidate_id']}"):
                reason_label = st.selectbox(
                    "忽略原因（可选，仅在点「忽略」时生效）",
                    options=[label for _, label in _GROWTH_DISMISS_REASON_OPTIONS],
                    key=reason_key,
                    label_visibility="collapsed",
                )
                fb1, fb2 = st.columns(2)
                accept_clicked = fb1.form_submit_button("✅ 采纳", key=f"growth_accept_{c['candidate_id']}")
                dismiss_clicked = fb2.form_submit_button("🙈 忽略", key=f"growth_dismiss_{c['candidate_id']}")

            reason_value = next(
                (v for v, label in _GROWTH_DISMISS_REASON_OPTIONS if label == reason_label),
                "unspecified",
            )
            if accept_clicked:
                # [采纳即启动] accept 现在默认会自动触发"生成报告 → 落地
                # 为 Goal → 生成并确认执行规范 → 绑定周期性"整条链路，
                # 响应体里的 `pursuit` 字段带回这一路的结果/失败信息，
                # 尽力而为地提示用户，不阻塞 rerun。
                resp = client.growth_candidate_action(c["candidate_id"], "accept")
                pursuit = (resp or {}).get("pursuit") or {}
                goal = pursuit.get("goal")
                if goal:
                    if pursuit.get("cron_job"):
                        st.toast(f"🔄 已开始自主持续调研：{goal.get('title', '')}（每天一轮）", icon="🌱")
                    else:
                        st.toast(f"已创建 Goal：{goal.get('title', '')}（周期性绑定未完成，可到「🎯 目标」tab 手动设置）", icon="⚠️")
                for err in pursuit.get("errors") or []:
                    st.toast(err, icon="⚠️")
                st.rerun()
            if dismiss_clicked:
                dismiss_reason = None if reason_value == "unspecified" else reason_value
                client.growth_candidate_action(c["candidate_id"], "dismiss", reason=dismiss_reason)
                st.rerun()

            b3, b4 = st.columns(2)
            report_id = c.get("report_id")
            if report_id and b3.button("📄 查看报告", key=f"growth_report_{c['candidate_id']}"):
                rep = client.growth_report(report_id)
                if rep and "_error" not in rep:
                    st.markdown(rep.get("body", "（报告正文为空）"))
                else:
                    st.error((rep or {}).get("_error", "读取报告失败"))
            if b4.button("🕒 轨迹", key=f"growth_timeline_{c['candidate_id']}"):
                _render_growth_topic_timeline(client, c["candidate_id"])


# P3：拖拽式看板视图（此前一直是"列表 + 采纳/忽略两个按钮"，方案 P3
# 计划项最后一条）。用 `streamlit-sortables`（可选依赖，未安装时自动
# 回退到 `_render_growth_pending_list`，不影响原有功能）渲染
# 待处理/已采纳/已忽略三列，拖动卡片到目标列即视为对应操作。
_GROWTH_KANBAN_COLUMNS = [
    ("pending", "🕗 待处理"),
    ("accepted", "✅ 已采纳"),
    ("dismissed", "🙈 已忽略"),
]


def _growth_card_label(c: dict) -> str:
    # sort_items 用字符串本身作为拖拽项的显示文本兼唯一标识，标题可能
    # 重复（同一主题被 dismiss 后冷却期结束重新生成），拼上 candidate_id
    # 前 8 位保证同一批渲染里不会有两张标签完全相同的卡片。
    conf = c.get("confidence", 0)
    short_id = str(c.get("candidate_id", ""))[:8]
    origin_tag = " 🔗" if c.get("origin") == "pursuit_spinoff" else ""
    return f"{c.get('title', '')}{origin_tag}（置信度 {conf}） · {short_id}"


def _render_growth_kanban_dragdrop(client: "AgentClient", candidates: list[dict]):
    from streamlit_sortables import sort_items

    by_status: dict[str, list[dict]] = {"pending": [], "accepted": [], "dismissed": []}
    for c in candidates:
        status = c.get("status")
        if status in by_status:
            by_status[status].append(c)

    label_to_id: dict[str, str] = {}
    containers = []
    for status, header in _GROWTH_KANBAN_COLUMNS:
        items = sorted(by_status[status], key=lambda x: -x.get("confidence", 0))
        labels = []
        for c in items:
            label = _growth_card_label(c)
            label_to_id[label] = c["candidate_id"]
            labels.append(label)
        containers.append({"header": header, "items": labels})

    st.caption(
        "拖动卡片到「已采纳」/「已忽略」即完成对应操作；"
        "从已采纳/已忽略拖回待处理不会生效（暂不支持撤销）。"
        "拖拽方式忽略的候选不会记录具体原因（记为「不说明原因」），"
        "如果想标注「方向没错，是报告没写好」这类细化原因，请切到"
        "列表视图操作。"
    )
    result = sort_items(
        containers, multi_containers=True, direction="horizontal",
        key="growth_kanban_dragdrop",
    )

    # result 是拖拽后每列的最新 label 列表；跟拖拽前的 by_status 对比，
    # 找出真正发生了"跨列移动"的卡片，只对这些卡片调用一次
    # growth_candidate_action，而不是无脑对整列重放操作（否则每次
    # rerun 都会对本来就已经是 accepted 的卡片重复调用 accept）。
    id_to_status = {c["candidate_id"]: c.get("status") for c in candidates}
    moved = False
    pursuit_notes: list[tuple[str, str]] = []  # [采纳即启动] (message, icon) 汇总，拖拽循环结束后统一 toast
    for col in result:
        header = col.get("header", "")
        target_status = next((s for s, h in _GROWTH_KANBAN_COLUMNS if h == header), None)
        if target_status not in ("accepted", "dismissed"):
            continue  # 拖回"待处理"不支持撤销，忽略
        for label in col.get("items", []):
            cand_id = label_to_id.get(label)
            if cand_id is None:
                continue
            if id_to_status.get(cand_id) != target_status:
                resp = client.growth_candidate_action(
                    cand_id, "accept" if target_status == "accepted" else "dismiss"
                )
                moved = True
                if target_status == "accepted":
                    pursuit = (resp or {}).get("pursuit") or {}
                    goal = pursuit.get("goal")
                    if goal:
                        if pursuit.get("cron_job"):
                            pursuit_notes.append((f"🔄 已开始自主持续调研：{goal.get('title', '')}（每天一轮）", "🌱"))
                        else:
                            pursuit_notes.append((f"已创建 Goal：{goal.get('title', '')}（周期性绑定未完成，可到「🎯 目标」tab 手动设置）", "⚠️"))
                    for err in pursuit.get("errors") or []:
                        pursuit_notes.append((err, "⚠️"))
    if moved:
        for msg, icon in pursuit_notes:
            st.toast(msg, icon=icon)
        st.rerun()

    # [growth_advisor_active_search_and_lifecycle_plan.md 方向二] 拖拽
    # 卡片本身是纯字符串标签，没有按钮承载位，用一个下拉选择 + 单独按钮
    # 补上轨迹查看入口，不影响既有的拖拽交互本身。
    if candidates:
        options = {_growth_card_label(c): c["candidate_id"] for c in candidates}
        chosen_label = st.selectbox(
            "查看某个候选的成长轨迹", options=list(options.keys()),
            key="growth_dragdrop_timeline_select",
        )
        if st.button("🕒 查看轨迹", key="growth_dragdrop_timeline_btn"):
            _render_growth_topic_timeline(client, options[chosen_label])


# ═══════════════════════════════════════════════════════════════════════
# Tab: 🎓 能力学习（next_doc/persona_capability_learning_design.md §7）
# ═══════════════════════════════════════════════════════════════════════
# 四个区域——人设管理区（Track 增删改）/ 进度展示区（大纲覆盖状态 + 学习
# 台账）/ 人设草稿区（§10.3）/ 待回答问题区（异步问答队列）。真实检索
# （`CapabilityLearningConfig.retriever_enabled`）与 cron 定时自动推进
# （`sys:capability_learning_cycle` / `sys:capability_question_sweep`）
# 现已默认开启——Track 一旦创建为 active 状态，就会按各自 cadence 自动
# 检索沉淀，不再需要用户手动敲 `/capability cycle`。仍可以在看板「⏰ Cron
# 任务」Tab 里单独 disable 这两个 job，或去配置里关掉 retriever_enabled
# 只保留手动触发。
def render_capability_tab(client: "AgentClient"):
    st.markdown("#### 🎓 能力学习 / 人设养成 (Capability Learning)")
    st.caption(
        "给 Agent 一个能力方向或人设描述，它会持续、克制地检索沉淀成 wiki 知识；"
        "遇到只有你知道的信息会异步向你提问，不会打断你当前的事。"
        "🟢 真实检索与 cron 定时自动推进默认已开启（每 6 小时一轮），"
        "也可以随时用 `/capability cycle` 手动触发一轮；"
        "如果想暂停自动运行，去「⏰ Cron 任务」Tab 关闭 "
        "`sys:capability_learning_cycle` 即可，不影响手动触发。"
    )

    tracks_resp = client.capability_tracks()
    if isinstance(tracks_resp, dict) and tracks_resp.get("_error"):
        st.warning(f"拉取能力 Track 列表失败：{tracks_resp['_error']}")
        tracks = []
    else:
        tracks = tracks_resp.get("tracks", []) if isinstance(tracks_resp, dict) else []

    # ── 7.1 人设管理区 ───────────────────────────────────────────────
    with st.expander("➕ 新建能力 / 人设方向", expanded=not tracks):
        with st.form("capability_new_track_form"):
            new_title = st.text_input("标题", placeholder="股票分析能力")
            new_desc = st.text_area("方向描述", placeholder="希望你具备强大的股票分析能力")
            new_type = st.selectbox("类型", ["knowledge", "persona"], format_func=lambda x: {
                "knowledge": "知识能力（沉淀 wiki 知识）",
                "persona": "角色人设（养成 .agent/personas 人设文件）",
            }[x])
            new_wiki_tag = st.text_input(
                "wiki 命名空间（可留空自动生成）", placeholder="capability:stock_analysis",
            )
            new_llm_draft = st.checkbox(
                "用 LLM 起草初始大纲（§14 P2，需要 agent 有可用的 LLM 上下文；"
                "起草失败会静默创建空大纲，不报错）",
                value=False,
            )
            submitted = st.form_submit_button("创建")
        if submitted:
            if not new_title or not new_desc:
                st.warning("标题和方向描述都不能为空。")
            else:
                resp = client.create_capability_track(
                    title=new_title, persona_desc=new_desc,
                    target_type=new_type, wiki_tag=new_wiki_tag,
                    llm_draft=new_llm_draft,
                )
                if isinstance(resp, dict) and resp.get("_error"):
                    st.error(f"创建失败：{resp['_error']}")
                else:
                    outline_n = len(resp.get("outline", []) or [])
                    draft_note = f"，已起草 {outline_n} 个子主题" if new_llm_draft and outline_n else ""
                    st.success(f"已创建 Track：{resp.get('title', new_title)}{draft_note}")
                    st.rerun()

    if not tracks:
        st.info("还没有任何能力 Track，先在上面创建一个。")
        return

    st.markdown("##### 人设 / 能力方向列表")
    refresh_all_col, refresh_all_hint_col = st.columns([1, 3])
    with refresh_all_col:
        if st.button("🔄 刷新所有存量", key="cap_refresh_all_btn"):
            resp = client.refresh_all_capability_topics()
            if isinstance(resp, dict) and resp.get("_error"):
                st.error(f"刷新失败：{resp['_error']}")
            elif isinstance(resp, dict) and resp.get("topics_reset", 0) == 0:
                st.info("没有已覆盖的子主题需要刷新。")
            else:
                st.success(
                    f"已把 {resp.get('topics_reset', 0)} 个已覆盖子主题重置为待刷新"
                    f"（涉及 {resp.get('tracks_affected', 0)} 个 Track），"
                    f"下一轮能力学习循环会重新检索。"
                )
                st.rerun()
    with refresh_all_hint_col:
        st.caption(
            "把所有 Track 里已判定「已覆盖」的子主题重置为待刷新，不用等 30 天的"
            "周期性刷新窗口。不会清空已有 wiki 内容，重新检索到新内容前旧页面仍可读。"
        )
    for track in tracks:
        outline = track.get("outline", []) or []
        total = len(outline)
        covered = sum(1 for t in outline if t.get("coverage_state") == "covered")
        status = track.get("status", "active")
        status_badge = {"active": "🟢 进行中", "paused": "⏸️ 已暂停", "archived": "📦 已归档"}.get(status, status)
        type_badge = "🧑‍🎓 人设" if track.get("target_type") == "persona" else "📚 知识"
        with st.expander(
            f"{type_badge} {track.get('title', '(未命名)')} — {status_badge} — "
            f"覆盖 {covered}/{total or '?'}",
        ):
            st.caption(track.get("persona_desc", ""))
            st.caption(f"wiki_tag: `{track.get('wiki_tag', '')}`　track_id: `{track.get('track_id', '')}`")
            if st.button("🔄 刷新此 Track", key=f"cap_refresh_track_btn_{track.get('track_id', '')}"):
                resp = client.refresh_all_capability_topics(track_id=track.get("track_id", ""))
                if isinstance(resp, dict) and resp.get("_error"):
                    st.error(f"刷新失败：{resp['_error']}")
                elif isinstance(resp, dict) and resp.get("topics_reset", 0) == 0:
                    st.info("这个 Track 没有已覆盖的子主题需要刷新。")
                else:
                    st.success(f"已把 {resp.get('topics_reset', 0)} 个已覆盖子主题重置为待刷新。")
                    st.rerun()

            # ── 7.2 进度展示区：大纲覆盖状态 + 学习台账 ──────────────
            if outline:
                st.markdown("**能力大纲覆盖状态**")
                state_icon = {"uncovered": "⬜", "partial": "🟨", "covered": "✅"}
                for topic in outline:
                    page_ids = topic.get("wiki_page_ids", []) or []
                    icon = state_icon.get(topic.get("coverage_state", "uncovered"), "⬜")
                    row_cols = st.columns([5, 1]) if page_ids else [st.container()]
                    with row_cols[0]:
                        st.write(f"{icon} {topic.get('name', '')}"
                                 + (f"　（关联 {len(page_ids)} 篇 wiki 页面）" if page_ids else ""))
                    # 直接查看对应 wiki 内容，不只是显示"关联 N 篇"这个数字
                    if page_ids:
                        with row_cols[1]:
                            view_key = f"cap_wiki_view_{track.get('track_id', '')}_{topic.get('topic_id', '')}"
                            if st.button("查看", key=f"cap_wiki_btn_{view_key}"):
                                st.session_state[view_key] = not st.session_state.get(view_key, False)
                        if st.session_state.get(view_key):
                            for pid in page_ids:
                                page_resp = client.capability_wiki_page(pid)
                                with st.expander(f"📄 {pid}", expanded=True):
                                    if isinstance(page_resp, dict) and page_resp.get("_error"):
                                        st.caption(f"页面加载失败：{page_resp['_error']}")
                                    else:
                                        st.markdown(page_resp.get("body", "") if isinstance(page_resp, dict) else "")
            else:
                st.caption("这个 Track 还没有大纲子主题。")

            ledger_resp = client.capability_track_ledger(track.get("track_id", ""), limit=10)
            entries = ledger_resp.get("entries", []) if isinstance(ledger_resp, dict) else []
            if entries:
                st.markdown("**最近学习台账**")
                action_label = {
                    "researched": "🔍 已检索沉淀", "question_raised": "❓ 已生成问题",
                    "question_answered": "💬 已消费回答", "skipped": "⏭️ 已跳过",
                    "miss_observed": "📌 记录到一次检索未命中",
                }
                for entry in entries[:10]:
                    ts = entry.get("cycle_ts")
                    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""
                    st.caption(
                        f"{when}　{action_label.get(entry.get('action', ''), entry.get('action', ''))}"
                        f"　{entry.get('summary', '')}"
                    )
            else:
                st.caption(
                    "还没有学习台账记录——cron 会按 6 小时一轮自动推进，"
                    "也可以用 `/capability cycle` 立即手动跑一轮看效果。"
                )

            # ── §10.3 人设草稿区（仅 persona 型 Track）──────────────────
            # 相对最简版本的改进：① 完成度用进度条 + 逐维度勾选清单展示，
            # 不再只是一句纯文字摘要；② 草稿预览分"渲染效果"/"源码"两个
            # Tab，渲染效果更接近 `/role use` 之后实际生效的样子；③ 从
            # 已落盘草稿加载时也一并取回 completeness（GET 端点已同步
            # 补齐），不用强制先点一次生成才能看到进度；④ 检测到草稿里
            # 嵌入了 §10.4-2 真人模仿安全提示时单独用 st.warning 高亮，
            # 不会被淹没在一大段 markdown 源码里；⑤ 未达完成度时给「发布」
            # 按钮加一句弱提醒（不阻断，用户仍可强行发布），呼应 §10.3
            # "所有权在用户"的原则。
            if track.get("target_type") == "persona":
                st.markdown("**人设草稿（§10.3）**")
                st.caption(
                    "草稿由目前已回答的问题合成，随时可以刷新预览；发布是显式动作，"
                    "点击「发布」之前不会写入 `.agent/personas/`。"
                )

                draft_text = st.session_state.get(f"cap_persona_draft_text_{track['track_id']}")
                completeness = st.session_state.get(f"cap_persona_draft_completeness_{track['track_id']}")
                if draft_text is None:
                    existing = client.get_capability_persona_draft(track["track_id"])
                    if not (isinstance(existing, dict) and existing.get("_error")):
                        draft_text = existing.get("draft", "")
                        completeness = existing.get("completeness")

                if completeness:
                    total = completeness.get("total", 0) or 0
                    answered = completeness.get("answered", 0) or 0
                    missing = completeness.get("missing_topic_names") or []
                    ratio = (answered / total) if total else 0.0
                    st.progress(min(max(ratio, 0.0), 1.0), text=f"完成度 {answered}/{total} 个维度")
                    if missing:
                        with st.popover(f"🔎 查看尚缺的 {len(missing)} 个维度"):
                            for name in missing:
                                st.write(f"⬜ {name}")
                    else:
                        st.caption("✅ 各维度均已有用户回答的信息。")

                draft_cols = st.columns(2)
                with draft_cols[0]:
                    if st.button("📝 生成/刷新草稿", key=f"cap_persona_draft_{track['track_id']}"):
                        resp = client.draft_capability_persona(track["track_id"])
                        if isinstance(resp, dict) and resp.get("_error"):
                            st.error(f"生成草稿失败：{resp['_error']}")
                        else:
                            st.session_state[f"cap_persona_draft_text_{track['track_id']}"] = resp.get("draft", "")
                            st.session_state[f"cap_persona_draft_completeness_{track['track_id']}"] = resp.get("completeness", {})
                            st.rerun()
                with draft_cols[1]:
                    publish_confirm_key = f"cap_persona_publish_confirm_{track['track_id']}"
                    incomplete = bool(completeness and (completeness.get("missing_topic_names") or []))
                    if st.session_state.get(publish_confirm_key):
                        if incomplete:
                            st.caption("⚠️ 还有维度缺信息，仍可发布，但建议先补全再正式启用。")
                        if st.button("⚠️ 确认发布", key=f"cap_persona_publish_go_{track['track_id']}"):
                            resp = client.publish_capability_persona(track["track_id"])
                            st.session_state.pop(publish_confirm_key, None)
                            if isinstance(resp, dict) and resp.get("_error"):
                                st.error(f"发布失败：{resp['_error']}")
                            else:
                                st.success(f"已发布到 {resp.get('published_path', '')}，可用 `/role use` 激活。")
                    elif st.button("🚀 发布", key=f"cap_persona_publish_{track['track_id']}"):
                        st.session_state[publish_confirm_key] = True
                        st.rerun()

                if draft_text:
                    if "安全提示" in draft_text or "真人" in draft_text:
                        st.warning(
                            "这份草稿的方向描述里检测到可能指向某个真实公众人物本人的"
                            "表述（§10.4-2 启发式识别，可能误报/漏报）。建议改为"
                            "\"参考某种风格但作为原创虚构人物\"，发布前请自行确认。"
                        )
                    preview_tab, source_tab = st.tabs(["预览效果", "源码"])
                    with preview_tab:
                        # frontmatter 之后的正文部分渲染成 markdown，更接近
                        # `/role use` 之后实际呈现的效果；frontmatter 本身
                        # 保留在源码 Tab 里查看即可，避免渲染成一堆无意义文字。
                        body = draft_text
                        if body.startswith("---"):
                            parts = body.split("---", 2)
                            if len(parts) == 3:
                                body = parts[2]
                        st.markdown(body)
                    with source_tab:
                        st.code(draft_text, language="markdown")
                else:
                    st.caption("还没有草稿，点「生成/刷新草稿」创建第一版。")

            # ── §11.4 知识范围绑定卡片（仅 knowledge 型、有 wiki_tag 的 Track）──
            wiki_tag = track.get("wiki_tag", "")
            if track.get("target_type") != "persona" and wiki_tag:
                st.markdown("**知识范围绑定 —— 被以下角色引用**")
                personas_resp = client.list_capability_personas()
                personas = personas_resp.get("personas", []) if isinstance(personas_resp, dict) else []
                if not personas:
                    st.caption("当前没有任何已定义的角色人设（`.agent/personas/`）。")
                else:
                    bound = [p for p in personas if wiki_tag in (p.get("wiki_scopes") or [])]
                    if bound:
                        st.caption("、".join(p.get("display_name") or p.get("name") for p in bound))
                    else:
                        st.caption("暂无角色绑定这个知识范围。")
                    with st.popover("🔗 管理绑定角色"):
                        for p in personas:
                            pname = p.get("name", "")
                            scopes = list(p.get("wiki_scopes") or [])
                            checked = wiki_tag in scopes
                            new_checked = st.checkbox(
                                p.get("display_name") or pname,
                                value=checked,
                                key=f"cap_scope_{track['track_id']}_{pname}",
                            )
                            if new_checked != checked:
                                if new_checked:
                                    scopes.append(wiki_tag)
                                else:
                                    scopes = [s for s in scopes if s != wiki_tag]
                                resp = client.set_persona_wiki_scopes(pname, scopes)
                                if isinstance(resp, dict) and resp.get("_error"):
                                    st.error(f"更新 {pname} 的 wiki_scopes 失败：{resp['_error']}")
                                else:
                                    st.rerun()

            # 管理操作
            cols = st.columns(4)
            with cols[0]:
                if status == "active" and st.button("⏸️ 暂停", key=f"cap_pause_{track['track_id']}"):
                    client.update_capability_track(track["track_id"], status="paused")
                    st.rerun()
                elif status == "paused" and st.button("▶️ 恢复", key=f"cap_resume_{track['track_id']}"):
                    client.update_capability_track(track["track_id"], status="active")
                    st.rerun()
            with cols[1]:
                confirm_key = f"cap_del_confirm_{track['track_id']}"
                if st.session_state.get(confirm_key):
                    if st.button("⚠️ 确认删除", key=f"cap_del_go_{track['track_id']}"):
                        client.delete_capability_track(track["track_id"])
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                elif st.button("🗑️ 删除", key=f"cap_del_{track['track_id']}"):
                    st.session_state[confirm_key] = True
                    st.rerun()
            st.caption("删除只下线这个 Track，不会删除已经沉淀的 wiki 页面。")

    # ── §11.4 弱提示：未绑定任何知识范围的角色（不强推送，仅展示） ─────
    knowledge_tags = [t.get("wiki_tag", "") for t in tracks
                       if t.get("target_type") != "persona" and t.get("wiki_tag")]
    if knowledge_tags:
        personas_resp = client.list_capability_personas()
        personas = personas_resp.get("personas", []) if isinstance(personas_resp, dict) else []
        unbound = [p for p in personas if not (p.get("wiki_scopes") or [])]
        if unbound:
            names = "、".join(p.get("display_name") or p.get("name") for p in unbound)
            st.caption(
                f"💡 {names} 目前不限定知识范围。如果想让某个角色显得更专业，"
                "可以在对应知识 Track 的「知识范围绑定」里勾选关联。"
            )

    # ── 7.3 待回答问题区 ─────────────────────────────────────────────
    st.markdown("---")
    st.markdown("##### ❓ 待回答问题")
    questions_resp = client.capability_questions(status="pending")
    pending_questions = questions_resp.get("questions", []) if isinstance(questions_resp, dict) else []
    if not pending_questions:
        st.caption("目前没有待回答的问题。")
    else:
        track_titles = {t["track_id"]: t.get("title", "") for t in tracks}
        for q in pending_questions:
            with st.container(border=True):
                st.write(f"**{q.get('question', '')}**")
                if q.get("hint"):
                    st.caption(f"提示：{q['hint']}")
                st.caption(f"来自：{track_titles.get(q.get('track_id', ''), q.get('track_id', ''))}")
                ans_key = f"cap_q_answer_{q['question_id']}"
                answer_text = st.text_input("你的回答", key=ans_key)
                bcols = st.columns([1, 1, 6])
                with bcols[0]:
                    if st.button("提交", key=f"cap_q_submit_{q['question_id']}"):
                        if answer_text.strip():
                            client.answer_capability_question(q["question_id"], answer_text.strip())
                            st.rerun()
                        else:
                            st.warning("回答不能为空。")
                with bcols[1]:
                    if st.button("忽略", key=f"cap_q_dismiss_{q['question_id']}"):
                        client.dismiss_capability_question(q["question_id"])
                        st.rerun()

    with st.expander("🗂️ 历史问答（已回答 / 已忽略 / 已过期）"):
        history_resp = client.capability_questions()
        all_questions = history_resp.get("questions", []) if isinstance(history_resp, dict) else []
        history = [q for q in all_questions if q.get("status") != "pending"]
        if not history:
            st.caption("还没有历史问答记录。")
        else:
            for q in history[:30]:
                st.caption(
                    f"[{q.get('status', '')}] {q.get('question', '')}"
                    + (f"　→ {q.get('answer', '')}" if q.get("answer") else "")
                )

    # ── v0.21 §13.2-f 大纲动态生长建议区 ──────────────────────────────
    # 消费已回答问题时由 llm_helper 提炼出的"大纲之外新关注点"建议，
    # 采纳后追加为新子主题、忽略则不动大纲——始终是人工决定，不自动追加。
    st.markdown("---")
    st.markdown("##### 💡 大纲扩展建议")
    suggestions_resp = client.capability_outline_suggestions(status="pending")
    pending_suggestions = (
        suggestions_resp.get("suggestions", []) if isinstance(suggestions_resp, dict) else []
    )
    if not pending_suggestions:
        st.caption("目前没有待处理的大纲扩展建议（回答异步问题后，若答案里提到明显的新方向，会在这里出现）。")
    else:
        track_titles = {t["track_id"]: t.get("title", "") for t in tracks}
        for s in pending_suggestions:
            with st.container(border=True):
                st.write(f"建议新增子主题：**{s.get('suggested_name', '')}**")
                if s.get("rationale"):
                    st.caption(s["rationale"])
                st.caption(f"来自：{track_titles.get(s.get('track_id', ''), s.get('track_id', ''))}")
                bcols = st.columns([1, 1, 6])
                with bcols[0]:
                    if st.button("采纳", key=f"cap_sug_accept_{s['suggestion_id']}"):
                        client.accept_capability_outline_suggestion(s["suggestion_id"])
                        st.rerun()
                with bcols[1]:
                    if st.button("忽略", key=f"cap_sug_dismiss_{s['suggestion_id']}"):
                        client.dismiss_capability_outline_suggestion(s["suggestion_id"])
                        st.rerun()

    # ── v0.21 第 3 项：Persona 详情页镜像视图 ──────────────────────────
    # §11.4 已经在 Track 详情页做了"被以下角色引用"的正向视图；这里补一个
    # 反向的"🎭 已发布角色一览"，按角色列出各自绑定的 wiki_scopes，实现
    # 设计文档 §11.2 末尾"双向可见"——不新开独立 Persona 管理 Tab，直接挂
    # 在能力学习 Tab 里，和上面知识范围绑定卡片共用同一份 personas 数据源。
    st.markdown("---")
    st.markdown("##### 🎭 已发布角色一览")
    all_personas_resp = client.list_capability_personas()
    all_personas = (
        all_personas_resp.get("personas", []) if isinstance(all_personas_resp, dict) else []
    )
    if not all_personas:
        st.caption("目前没有已发布的角色（`.agent/personas/` 下暂无文件）。")
    else:
        for p in all_personas:
            scopes = p.get("wiki_scopes") or []
            scope_note = "、".join(scopes) if scopes else "不限定范围（检索全库）"
            st.caption(f"**{p.get('display_name') or p.get('name')}** — {scope_note}")


# ═══════════════════════════════════════════════════════════════════════
# 自诊断信号闭环深化（next_doc/self_diagnosis_feedback_loop_deepening_plan.md
# P1-P4）看板可视化。四路信号此前只落盘/写进 activity_digest.jsonl，人工
# 要看只能翻文件或翻晨报文本；这里接上 GET /v1/self/diagnosis_feedback，
# 一次性展示排序候选清单 + 建议回看结论 + 能力弱点 diff + skill 有效性。
# 全部只读展示，不提供"一键采纳"之类的执行按钮——遵循计划文档 §3 明确写的
# 边界："执行与否、采纳与否始终是人工决定"。
# ═══════════════════════════════════════════════════════════════════════
_VERDICT_LABEL = {
    "improved": "✅ 已改善", "worse": "🔴 变差了", "unchanged": "➖ 无明显变化",
    "no_action_taken": "❔ 未采纳/无人使用", "effective": "✅ 有效",
    "low_effectiveness": "🔴 低有效性", "inconclusive": "❔ 样本不足",
}


def _render_self_diagnosis_feedback(client: AgentClient):
    st.markdown("#### 🩺 自诊断信号闭环")
    st.caption(
        "self_diagnosis_feedback_loop_deepening_plan.md P1-P4：四路自诊断信号"
        "（工具/技能/知识缺口/能力弱点）聚合排序 + 历史建议是否真的见效的回看。"
        "纯只读展示，是否采纳仍由人工决定。"
    )
    if st.button("🔄 刷新", key="diag_feedback_refresh"):
        st.rerun()

    resp = client.self_diagnosis_feedback() or {}
    if "_error" in resp:
        st.warning(f"获取失败：{resp['_error']}")
        return

    # P1 —— 改进信号聚合器：排序后的候选清单
    with st.expander("📋 改进候选清单（P1 improvement_backlog）", expanded=True):
        backlog = resp.get("improvement_backlog")
        items = (backlog or {}).get("items") or []
        if not items:
            st.caption("暂无数据（cron job `sys:improvement_backlog_merge` 还未跑过一轮，或暂无候选）。")
        else:
            ran_at = backlog.get("ran_at")
            if ran_at:
                st.caption(f"最近一次汇总：{time.strftime('%Y-%m-%d %H:%M', time.localtime(ran_at))}")
            sorted_items = sorted(items, key=lambda x: x.get("score", 0), reverse=True)
            for it in sorted_items[:10]:
                st.markdown(
                    f"**{it.get('subject','')}** · 分数 {it.get('score',0)} · "
                    f"来源 `{it.get('source','')}`/`{it.get('kind','')}`"
                )
                st.caption(it.get("summary", ""))

    # P2 —— 建议采纳率回看
    with st.expander("🔁 建议采纳率回看（P2 suggestion_outcome_review）"):
        review = resp.get("suggestion_outcome_review")
        if not review:
            st.caption("暂无数据（cron job `sys:suggestion_outcome_review` 每 14 天跑一次）。")
        else:
            at = review.get("at")
            if at:
                st.caption(f"最近一次回看：{time.strftime('%Y-%m-%d %H:%M', time.localtime(at))}")
            findings = review.get("findings") or []
            for f in findings:
                verdict = f.get("verdict", "")
                st.markdown(
                    f"🔧 `{f.get('tool_name','')}` — {_VERDICT_LABEL.get(verdict, verdict)}　"
                    f"基线失败率 {f.get('baseline_failure_rate', 0):.2f} → "
                    f"当前 {f.get('current_failure_rate') if f.get('current_failure_rate') is not None else '-'} "
                    f"（调用 {f.get('current_call_count', 0)} 次）"
                )

    # P3 —— 能力自画像时间序列快照 diff
    with st.expander("📈 能力弱点变化趋势（P3 self_model_snapshot_diff）"):
        diff = resp.get("self_model_snapshot_diff")
        if not diff:
            st.caption("暂无数据（cron job `sys:self_model_snapshot` 日频跑一次，且需要至少两次快照才有 diff）。")
        else:
            change = diff.get("weak_count_change")
            old_at = diff.get("old_at")
            if change is not None:
                trend = "🔴 变多了" if change > 0 else ("✅ 变少了" if change < 0 else "➖ 持平")
                base_desc = (
                    f"（对比 {time.strftime('%Y-%m-%d', time.localtime(old_at))}）" if old_at else "（首次快照，无历史对比）"
                )
                st.markdown(f"弱项数量变化：{change:+d} {trend} {base_desc}")
            old_domains = set(diff.get("weak_domains_old") or [])
            new_domains = set(diff.get("weak_domains_new") or [])
            added = sorted(new_domains - old_domains)
            removed = sorted(old_domains - new_domains)
            if added:
                st.caption(f"新增弱项：{', '.join(added)}")
            if removed:
                st.caption(f"已改善（不再是弱项）：{', '.join(removed)}")
            if not added and not removed and diff.get("weak_domains_new"):
                st.caption(f"当前弱项：{', '.join(sorted(new_domains))}")

    # P4 —— skill 结果有效性审计
    with st.expander("🧪 Skill 结果有效性审计（P4 skill_effectiveness）"):
        skill_findings = resp.get("skill_effectiveness") or []
        if not skill_findings:
            st.caption("暂无数据（并入 `sys:self_maintain` 健康巡检流程，且激活组/对照组样本量需各 ≥3 才下结论）。")
        else:
            for f in skill_findings:
                verdict = f.get("verdict", "")
                st.markdown(
                    f"🧩 `{f.get('skill_name','')}` — {_VERDICT_LABEL.get(verdict, verdict)}　"
                    f"激活组失败率 {f.get('active_failure_rate', '-')}"
                    f"（{f.get('active_sessions', 0)} 个 session）vs "
                    f"对照组 {f.get('baseline_failure_rate', '-')}"
                    f"（{f.get('baseline_sessions', 0)} 个 session）"
                )


# ═══════════════════════════════════════════════════════════════════════
# Goal 执行公平性调度（next_doc/goal_execution_fairness_improvement_plan.md
# P5）看板可视化。P1-P3 的调度决策此前只能靠翻 goals.json/
# activity_digest.jsonl 猜"哪些 Goal 最近获得了执行机会、哪些被冷落"；这里
# 接上 GET /v1/self/goal_fairness，按实际调度顺序（last_scheduled_at 升序）
# 展示每个 active Goal 的 priority/老化加成/effective_priority。纯只读
# 展示，不提供"手动调整调度顺序"之类的交互——人工干预仍走已有的"改
# priority/改 status"通用手段（见设计文档 P5 小节"不做"）。
# ═══════════════════════════════════════════════════════════════════════

def _render_goal_execution_fairness(client: AgentClient):
    st.markdown("#### ⚖️ 执行公平性")
    st.caption(
        "goal_execution_fairness_improvement_plan.md P1-P3：调度不再只看静态 "
        "priority，而是优先照顾最近一段时间没获得过执行机会的 Goal，并对长期"
        "停滞的 Goal 临时提升有效优先级。下表按实际调度顺序排列（最久没轮到的"
        "排最前）。"
    )
    if st.button("🔄 刷新", key="goal_fairness_refresh"):
        st.rerun()

    resp = client.goal_fairness() or {}
    if "_error" in resp:
        st.warning(f"获取失败：{resp['_error']}")
        return

    strategy = resp.get("strategy", "fair_round_robin")
    strategy_label = "公平轮询（fair_round_robin）" if strategy == "fair_round_robin" else "纯优先级（priority，旧行为）"
    st.caption(f"当前调度策略：**{strategy_label}**")

    rows = resp.get("goals") or []
    if not rows:
        st.caption("暂无 active Goal。")
        return

    now = time.time()

    def _fmt_ago(ts: float) -> str:
        if not ts:
            return "从未被调度"
        days = (now - ts) / 86400
        if days < 1:
            return f"{days * 24:.1f} 小时前"
        return f"{days:.1f} 天前"

    for r in rows:
        boost = r.get("aging_boost", 0) or 0
        boost_note = f"（含老化加成 +{boost:.1f}）" if boost > 0 else ""
        st.markdown(
            f"**{r.get('title','')}** · priority {r.get('priority', 0)} → "
            f"effective {r.get('effective_priority', 0)}{boost_note} · "
            f"{r.get('objective_count', 0)} 个 active Objective"
        )
        st.caption(
            f"上次调度：{_fmt_ago(r.get('last_scheduled_at', 0))}　"
            f"上次进展：{_fmt_ago(r.get('last_touched_at', 0))}"
        )


# ═══════════════════════════════════════════════════════════════════════
# 系统关联性断点 + 缺失重要功能改进方案 P1（next_doc/
# system_connectivity_gaps_and_missing_capabilities_plan.md）看板可视化。
# F1-F4 四个模块此前只往各自的 json/jsonl 文件里落盘，人工要看只能手工
# 翻文件——这恰好是方案文档反复批评别的模块犯的"埋头产生、没人看"的
# 毛病。这里接上 GET /v1/self/system_connectivity，把四路数据摆出来。
# 纯只读展示，不提供任何"一键采纳/清空"之类的操作按钮。
# ═══════════════════════════════════════════════════════════════════════

def _render_system_connectivity(client: AgentClient):
    st.markdown("#### 🔗 系统关联性（决策消费 / 失败模式 / 建议反馈 / 纠正事件）")
    st.caption(
        "system_connectivity_gaps_and_missing_capabilities_plan.md P1：F1-F4 "
        "四个新模块产出的数据一次性展示，纯只读。"
    )
    if st.button("🔄 刷新", key="system_connectivity_refresh"):
        st.rerun()

    resp = client.system_connectivity() or {}
    if "_error" in resp:
        st.warning(f"获取失败：{resp['_error']}")
        return

    # F1 —— 决策消费率
    with st.expander("📚 决策消费率（F1 decision_consumption）", expanded=True):
        dc = resp.get("decision_consumption")
        if not dc:
            st.caption("暂无数据（`decision_consumption_enabled` 默认关闭，或尚无检索记录）。")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("检索次数", dc.get("total_retrievals", 0))
            c2.metric("被采纳次数", dc.get("consumed", 0))
            c3.metric("消费率", f"{dc.get('consumption_rate', 0):.0%}")

    # F2 —— 统一失败模式库
    with st.expander("🩹 高频失败模式（F2 failure_pattern_store）"):
        patterns = resp.get("failure_patterns") or []
        if not patterns:
            st.caption("暂无数据（cron job `sys:failure_pattern_aggregation` 还未跑过一轮，或暂无失败记录）。")
        else:
            for p in patterns[:15]:
                st.markdown(
                    f"**{p.get('task_category','')}** · 根因 `{p.get('root_cause_tag','')}` · "
                    f"来源 `{p.get('source','')}` · 出现 {p.get('occurrence_count', 0)} 次"
                )
                st.caption(p.get("example_summary", ""))

    # F3 —— 建议反馈累积权重账本
    with st.expander("⚖️ 建议采纳/拒绝账本（F3 suggestion_feedback_ledger）"):
        ledger = resp.get("suggestion_feedback") or {}
        if not ledger:
            st.caption("暂无数据（尚无建议被采纳或拒绝过）。")
        else:
            rows = sorted(
                ledger.items(),
                key=lambda kv: kv[1].get("rejected", 0) + kv[1].get("accepted", 0),
                reverse=True,
            )
            for category, entry in rows[:15]:
                accepted = entry.get("accepted", 0)
                rejected = entry.get("rejected", 0)
                note = ""
                if rejected >= 3 and accepted == 0:
                    note = "（🔴 已打折 ×0.7）"
                elif accepted >= 2:
                    note = "（✅ 已加成 ×1.15）"
                st.markdown(f"**{category}** · 采纳 {accepted} / 拒绝 {rejected} {note}")

    # F4 —— 用户纠正事件
    with st.expander("✏️ 最近的用户纠正事件（F4 correction_writer）"):
        corrections = resp.get("recent_corrections") or []
        if not corrections:
            st.caption("暂无数据（尚未发生过能定位到具体 wiki 页面的用户纠正）。")
        else:
            for c in reversed(corrections[-15:]):
                ts = c.get("ts")
                ts_label = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""
                mark = "✅ 已标记 stale" if c.get("marked_stale") else "❌ 标记失败"
                st.caption(
                    f"`{ts_label}` 页面 `{c.get('page_id','')}` — {mark}　"
                    f"{c.get('correction_text','')[:80]}"
                )


# ═══════════════════════════════════════════════════════════════════════
# 执行模型 + 调度心跳（next_doc/daemon_execution_model_and_scheduler_
# heartbeat_improvement_plan.md）看板可视化。这两个改动都是默认关闭的灰度
# 开关（objective_persistent_worker_enabled / scheduler_heartbeat_enabled），
# 此前"开没开、起没起作用"只能靠翻配置文件或 attach 进程猜——这里接上
# GET /v1/self/execution_model_status，把当前生效的执行模式、持久 Worker
# 活跃 execution 数、心跳线程存活状态一次性展示出来。纯只读展示，不提供
# 任何"一键切换开关"之类的操作（切换仍然走 agent_config.json /
# "⚙️ 配置"tab 的通用改配置手段，重启 daemon 才会生效——这不是运行时可以
# 热切换的开关，看板上放一个按钮反而会让人误以为点一下就能生效）。
# ═══════════════════════════════════════════════════════════════════════

def _render_execution_overview(client: AgentClient, exec_resp: dict):
    """[kanban_execution_visibility_and_control_plan.md 阶段 C] 统一执行
    总览：正在执行 / 排队等待 / 异常已回收 / 最近完成 四栏，替代此前"只有
    孤立的累计数字、看不出具体是哪个任务"的观测盲区。

    exec_resp 是调用方已经拉取好的 execution_model_status() 响应（含新增
    的 recent_recoveries 字段），这里不重复请求；cron/objective 的明细
    另外拉取一次。
    """
    st.markdown("##### 📋 执行总览")

    if st.button("🚨 立即回收卡死任务", key="force_reap_all",
                 help="不等 watchdog 下一次 tick，立刻对 cron / Objective step / "
                      "隔离线程池三条链路各跑一次卡死回收扫描。"):
        res = client.force_reap("all")
        if res and "_error" in res:
            st.error(res["_error"])
        else:
            reaped = (res or {}).get("reaped", {})
            n_cron = len(reaped.get("cron_job") or [])
            n_step = len(reaped.get("objective_step") or [])
            n_pool = 1 if (reaped.get("isolated_pool") or {}).get("rebuilt") else 0
            total = n_cron + n_step + n_pool
            if total:
                st.success(f"本次回收了 {total} 个卡死任务（cron {n_cron} / Objective step {n_step} / 隔离池重建 {n_pool}）。")
            else:
                st.info("本次扫描没有发现卡死任务。")
        st.rerun()

    autostat = client.autonomous_status() or {}
    executions = autostat.get("objective_executions") or []
    cron_data = client.cron_jobs() or {}
    cron_jobs = cron_data.get("jobs") or []

    running_items, queued_items, done_items = [], [], []
    now = time.time()
    for ex in executions:
        status = ex.get("status")
        if status == "running":
            elapsed = now - (ex.get("started_at") or now)
            running_items.append(
                f"🎯 **{ex.get('title', ex.get('objective_id', ''))}** —— "
                f"{ex.get('current_step') or '（无当前步骤描述）'}"
                f"　已运行 {elapsed / 60:.1f} 分钟"
            )
        elif status in ("completed",):
            finished = ex.get("finished_at") or 0
            if finished and (now - finished) < 30 * 60:
                done_items.append(f"🎯 {ex.get('title', ex.get('objective_id', ''))}")

    for job in cron_jobs:
        phase = job.get("execution_phase", "not_running")
        name = job.get("name") or job.get("id", "")
        if phase == "running":
            running_items.append(f"⏰ **{name}**（cron，正在执行）")
        elif phase == "queued":
            queued_items.append(f"⏰ **{name}**（cron，排队等待并发槽位）")

    recoveries = exec_resp.get("recent_recoveries") or []
    kind_label = {"cron_job": "⏰ Cron", "objective_step": "🎯 Objective", "isolated_pool": "🧵 隔离线程池"}

    # [daemon_stability_and_ux_improvement_plan.md P1-9] 检测"🔴 异常已回收"
    # 计数环比上一次刷新明显增长时，用归纳性提示直接指出问题范围（例如"都是
    # 同一个 job_id"），而不是只把数字标红、让用户自己从原始事件列表里翻规律。
    # 用 session_state 记录"上一次刷新时看到的最新事件时间戳"，本次渲染时
    # 只统计比它更新的事件为"新增"，避免每次 rerun 都重复提示同一批旧事件。
    prev_seen_ts = st.session_state.get("_exec_overview_last_seen_recovery_ts", 0.0)
    new_events = [ev for ev in recoveries if (ev.get("time") or 0) > prev_seen_ts]
    summary_msg = None
    if new_events:
        window_start = now - 10 * 60  # 最近 10 分钟窗口内做归纳
        recent_window = [ev for ev in new_events if (ev.get("time") or 0) >= window_start] or new_events
        by_id: dict = {}
        for ev in recent_window:
            key = ev.get("id") or f"({kind_label.get(ev.get('kind'), ev.get('kind', ''))}，无 id)"
            by_id.setdefault(key, []).append(ev)
        total_new = len(recent_window)
        if total_new >= 2:
            top_id, top_events = max(by_id.items(), key=lambda kv: len(kv[1]))
            if len(top_events) >= 2 and len(top_events) == total_new:
                # 全部新增事件都集中在同一个 id 上——归纳成一句话直接点名。
                kind = kind_label.get(top_events[0].get("kind"), top_events[0].get("kind", ""))
                summary_msg = (
                    f"⚠️ 过去 {int((now - min(ev.get('time', now) for ev in top_events)) / 60) or 1} "
                    f"分钟内有 {total_new} 次{kind}卡死回收，都指向同一个 `{top_id}`，建议优先排查它。"
                )
            elif len(top_events) >= 2:
                kind = kind_label.get(top_events[0].get("kind"), top_events[0].get("kind", ""))
                summary_msg = (
                    f"⚠️ 过去 {int((now - min(ev.get('time', now) for ev in recent_window)) / 60) or 1} "
                    f"分钟内新增 {total_new} 次卡死回收，其中 {len(top_events)} 次集中在同一个 "
                    f"{kind} `{top_id}`，建议优先排查它。"
                )
            else:
                summary_msg = (
                    f"⚠️ 过去 {int((now - min(ev.get('time', now) for ev in recent_window)) / 60) or 1} "
                    f"分钟内新增 {total_new} 次卡死回收，分散在不同任务上，可能是系统性问题（例如某个工具/API "
                    f"全局失效），建议查看下方明细列表。"
                )
    if recoveries:
        st.session_state["_exec_overview_last_seen_recovery_ts"] = max(ev.get("time") or 0 for ev in recoveries)
    if summary_msg:
        st.warning(summary_msg)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown("**🟢 正在执行**")
        if running_items:
            for it in running_items:
                st.markdown(f"- {it}")
        else:
            st.caption("当前没有正在执行的任务。")
    with col2:
        st.markdown("**🟡 排队等待**")
        if queued_items:
            for it in queued_items:
                st.markdown(f"- {it}")
        else:
            st.caption("当前没有排队等待的任务。")
    with col3:
        st.markdown("**🔴 异常/已回收**")
        if recoveries:
            for ev in recoveries[:10]:
                ts = datetime.fromtimestamp(ev.get("time", 0)).strftime("%H:%M:%S") if ev.get("time") else "?"
                label = kind_label.get(ev.get("kind"), ev.get("kind", ""))
                obj_id = f"`{ev['id']}` " if ev.get("id") else ""
                st.markdown(f"- {ts} {label} {obj_id}—— {ev.get('detail', '')}")
            if len(recoveries) > 10:
                st.caption(f"（还有 {len(recoveries) - 10} 条更早的记录未展示）")
        else:
            st.caption("最近没有发生过卡死回收。")
    with col4:
        st.markdown("**⚪ 最近完成**")
        if done_items:
            for it in done_items:
                st.markdown(f"- {it}")
        else:
            st.caption("最近 30 分钟内没有新完成的 Objective。")


def _render_execution_model_status(client: AgentClient):
    st.markdown("#### ⚙️ 执行模型（目标级持久 Worker / 调度心跳）")
    st.caption(
        "daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md："
        "两个默认关闭的灰度开关，开启需要改 agent_config.json 并重启 daemon，"
        "这里只做只读状态展示。"
    )
    if st.button("🔄 刷新", key="execution_model_status_refresh"):
        st.rerun()

    resp = client.execution_model_status() or {}
    if "_error" in resp:
        st.warning(f"获取失败：{resp['_error']}")
        return

    _render_execution_overview(client, resp)
    st.divider()

    mode = resp.get("objective_execution_mode", "shared_queue")
    mode_label = {
        "persistent": "🟢 目标级持久 Worker（真并行 + 跨 step 上下文连续）",
        "isolated": "🟡 隔离 Runner（真并行，但每步失忆）",
        "shared_queue": "⚪ 共享队列（默认，串行，无独立并发）",
    }.get(mode, mode)
    st.markdown(f"**Objective 执行模式**：{mode_label}")

    col1, col2 = st.columns(2)
    with col1:
        pw = resp.get("persistent_worker") or {}
        st.markdown("**目标级持久 Worker**")
        if pw.get("enabled"):
            st.metric("活跃 execution 数（真并行线程数）", pw.get("active_execution_count", 0))
            ids = pw.get("active_execution_ids") or []
            if ids:
                st.caption("活跃 execution_id：" + "、".join(ids[:10]))
            st.caption(f"idle TTL：{pw.get('idle_ttl_seconds', 0):.0f} 秒")
        else:
            st.caption("未开启（`objective_persistent_worker_enabled=False`，默认值）。")

        iso = resp.get("isolated_runner") or {}
        if iso.get("enabled"):
            st.markdown("**隔离 Runner（旧路径）**")
            st.caption(f"max_workers：{iso.get('max_workers', 0)}")

    with col2:
        hb = resp.get("scheduler_heartbeat") or {}
        st.markdown("**调度心跳独立化**")
        if hb.get("enabled"):
            alive = hb.get("alive")
            st.markdown("🟢 运行中" if alive else "🔴 已启用但线程未存活（异常，建议检查日志）")
            st.caption(
                f"轮询间隔 {hb.get('poll_interval_seconds', 0):.1f}s / "
                f"AutonomousLoop tick 周期 {hb.get('tick_interval_seconds', 0):.1f}s"
            )
        else:
            st.caption(
                "未开启（`scheduler_heartbeat_enabled=False`，默认值）—— "
                "AutonomousLoop 仍走原有的\"主循环 dequeue 超时后顺带 tick\"路径。"
            )

    st.divider()
    st.markdown("**🩹 卡死回收累计计数**")
    st.caption(
        "以下四个数字是各条链路自 daemon 启动以来累计的卡死回收次数。"
        "如果在本次看板会话期间任一数字发生了增长（下方 🔴 标红），说明最近"
        "确实发生过卡死回收，建议查看上方「📋 执行总览」的「🔴 异常/已回收」栏。"
    )
    counters = {
        "cron 卡死回收次数": (resp.get("cron") or {}).get("reaped_job_count", 0),
        "Objective step 卡死回收次数": (resp.get("objective_executor") or {}).get("stale_step_reap_count", 0),
        "持久 Worker discard 次数": (resp.get("persistent_worker") or {}).get("discarded_worker_count", 0),
        "隔离线程池整体重建次数": (resp.get("isolated_runner") or {}).get("pool_rebuild_count", 0),
    }
    baseline = st.session_state.setdefault("_exec_model_counter_baseline", dict(counters))
    ccols = st.columns(len(counters))
    for c, (label, value) in zip(ccols, counters.items()):
        grew = value > baseline.get(label, value)
        with c:
            if grew:
                st.metric(label, value, delta=value - baseline[label], delta_color="inverse")
                st.caption("🔴 本次会话内新增，建议关注")
            else:
                st.metric(label, value)


# ═══════════════════════════════════════════════════════════════════════
# 统一调度总览（goal_cron_unified_scheduler_improvement_plan.md P4）：把
# Goal / 普通 cron / goal_cycle 三条执行通道当前的运行/排队/跳过状态，以及
# 三者共享的 ResourceArbiter 仲裁结果，聚合在一个区块里展示。后端聚合端点
# GET /v1/self/scheduling_overview 在 P4 上一轮已完成，本轮补齐看板 UI 展示
# 部分。纯只读展示，不提供任何"直接介入调度"的操作按钮——与"🩺 自诊断信号
# 闭环"一致，观测和决策分离。
# ═══════════════════════════════════════════════════════════════════════
_GATING_STATE_LABEL = {
    "full": "🟢 full（资源充足）",
    "degraded": "🟡 degraded（收紧中）",
    "blocked": "🔴 blocked（硬限流）",
}


def _render_scheduling_overview(client: AgentClient):
    st.markdown("#### 🕹️ 统一调度总览")
    st.caption(
        "goal_cron_unified_scheduler_improvement_plan.md P4：Goal → Objective / "
        "普通 cron / goal_cycle 三条执行通道当前各自的运行、排队、跳过状态，"
        "以及三者共享的 ResourceArbiter 仲裁结果一次性展示，不必再在"
        "「⚖️ 执行公平性」「⚙️ 执行模型」「🔄 工作流/Cron」面板之间来回切换拼图。"
    )
    if st.button("🔄 刷新", key="scheduling_overview_refresh"):
        st.rerun()

    resp = client.scheduling_overview() or {}
    if "_error" in resp:
        st.warning(f"获取失败：{resp['_error']}")
        return

    gating = resp.get("gating") or {}
    state = gating.get("state")
    st.markdown(f"**当前仲裁状态**：{_GATING_STATE_LABEL.get(state, state or '未知')}")
    reason = gating.get("reason")
    if reason:
        st.caption(reason)

    mode = resp.get("scheduling_mode") or {}
    unified_on = mode.get("unified_arbitration_enabled")
    adaptive_on = mode.get("adaptive_concurrency_enabled")
    degrade_on = mode.get("resource_gating_degraded_enabled")
    mode_bits = [
        f"统一仲裁 {'🟢开' if unified_on else '⚪关'}",
        f"自适应并发 {'🟢开' if adaptive_on else '⚪关'}",
        f"degraded 收紧并发 {'🟢开' if degrade_on else '⚪关'}",
    ]
    st.markdown(f"**当前调度模式**：{' · '.join(mode_bits)}")
    if unified_on:
        weights = mode.get("channel_weights") or {}
        st.caption(f"通道权重 channel_weights：goal={weights.get('goal', 1.0)} / cron={weights.get('cron', 1.0)}")
        alloc = mode.get("degraded_allocation")
        if alloc:
            st.caption(f"当前 degraded 槽位分配：goal={alloc.get('goal', 0)} / cron={alloc.get('cron', 0)}")

    usage = resp.get("usage_breakdown")
    if usage:
        budget = usage.get("daily_token_budget", 0) or 0
        used_total = usage.get("used_today", 0) or 0
        ratio = (used_total / budget) if budget else 0.0
        st.progress(min(ratio, 1.0), text=f"今日预算消耗 {used_total}/{budget}（{ratio:.0%}）")
        u1, u2, u3 = st.columns(3)
        u1.metric("Goal 通道消耗", usage.get("used_today_goals", 0))
        u2.metric("cron 通道消耗", usage.get("used_today_cron", 0))
        u3.metric("探索沙盒消耗", usage.get("used_today_exploration", 0))
    else:
        st.caption("暂无预算分项数据（self_profile.json 尚不可用）。")

    st.divider()
    g1, g2, g3 = st.columns(3)

    with g1:
        st.markdown("**🎯 Goal 通道**")
        goal_ch = resp.get("goal_channel") or {}
        slots = goal_ch.get("objective_slots")
        if slots:
            st.metric("并发槽位（运行中/当前上限）", f"{slots.get('running', 0)}/{slots.get('max', 0)}")
            st.caption(f"静态上限 static_cap={slots.get('static_cap', 0)}")
        else:
            st.caption("暂无数据（ObjectiveExecutor 未注入）。")
        head = goal_ch.get("queue_head_goal")
        if head:
            ts = head.get("last_scheduled_at")
            ts_label = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "从未调度"
            st.caption(f"公平队首：**{head.get('title', head.get('goal_id',''))}**（上次调度 {ts_label}）")
        else:
            st.caption("当前没有 active Goal。")

    with g2:
        st.markdown("**⏱️ 普通 cron 通道**")
        cron_ch = resp.get("cron_channel") or {}
        c1, c2 = st.columns(2)
        max_c = cron_ch.get("max_concurrent")
        c1.metric("运行中", cron_ch.get("running", 0))
        c2.metric("排队中", cron_ch.get("queued", 0))
        if max_c is not None:
            static_c = cron_ch.get("static_max_concurrent")
            extra = f"（静态上限 {static_c}）" if static_c is not None and static_c != max_c else ""
            st.caption(f"当前并发上限：{max_c}{extra}")
        st.caption(f"仲裁累计跳过次数（进程内）：{cron_ch.get('arbiter_skipped_count', 0)}")
        over = cron_ch.get("jobs_over_skip_threshold") or []
        if over:
            st.markdown("🔴 **连续跳过超阈值的 job**")
            for j in over:
                st.caption(f"`{j.get('name', j.get('job_id',''))}` — 已连续跳过 {j.get('consecutive_skip_count', 0)} 次")
        else:
            st.caption("没有 job 连续跳过超过阈值。")

    with g3:
        st.markdown("**🔁 goal_cycle 通道**")
        gc_ch = resp.get("goal_cycle_channel") or {}
        st.metric("待触发数 / 总数", f"{gc_ch.get('pending_due_count', 0)}/{gc_ch.get('total_count', 0)}")
        recent = gc_ch.get("recent") or []
        if recent:
            st.markdown("最近触发")
            for r in recent:
                ts = r.get("last_run_at")
                ts_label = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "-"
                skip = r.get("consecutive_skip_count", 0)
                health = "🔴" if skip > 0 else "🟢"
                st.caption(
                    f"{health} **{r.get('goal_title', r.get('job_id',''))}** "
                    f"— 第 {ts_label} 次，累计 {r.get('run_count', 0)} 轮"
                    + (f"，连续跳过 {skip} 次" if skip else "")
                )
        else:
            st.caption("暂无 goal_cycle job。")


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
# Tab: ⏰ Cron 任务（专属执行机制：进度/超时/卡死检测/prompt 编辑）
# ═══════════════════════════════════════════════════════════════════════

_CRON_STATUS_LABEL = {
    "idle": "⚪ 空闲",
    "running": "🔵 执行中",
    "needs_human_review": "🔴 需要人工介入",
    "timed_out": "🟡 上次超时（已保留进度，下次续接）",
}

# [执行阶段] CronJobRunner.execution_phase() 的三态：not_running/queued/running，
# 与 state.json 里的 idle/running/needs_human_review/timed_out 是两套独立的
# 状态机——前者是"这次触发有没有真正开始跑"（进程内瞬时状态，daemon 重启后
# 归零），后者是"上一次执行结果如何"（持久化在 state.json）。两者分开展示，
# 避免"明明还在排队却显示状态还是上次的 idle/timed_out"造成的困惑。
_CRON_PHASE_LABEL = {
    "not_running": None,           # 不在跑，跟随 status 展示即可，不额外提示
    "queued": "🟠 排队中（等待并发槽位）",
    "running": "🔵 正在执行",
}


_CRON_RUN_EVENT_TITLE = {
    "run_started": "▶️ 开始执行",
    "timed_out": "⏱️ 超时终止",
    "max_steps_reached": "🚫 达到步数上限",
    "stuck_recover": "🔁 卡死检测：尝试恢复",
    "stuck_give_up": "🛑 卡死检测：放弃（需人工介入）",
    "step_error": "❌ 单步执行异常",
    "run_finished": "🏁 执行结束",
}


def _render_cron_run_timeline(events: list[dict]) -> None:
    """[cron_run_debug_detail_improvement_plan.md ③] 把 runs/<run_id>.jsonl
    原始事件列表渲染成可读时间线，替换掉原来的 `st.json(events)` 原始
    转储——调试时不用再从几百个 tool_use/tool_result 混杂的 JSON 里
    自己肉眼找。

    纯展示层改动：只读事件字段，新字段（`full_text`/`tool_calls`）缺失
    时（旧数据，只有 `text_preview`）对应区块显示为空，不报错，保持向
    后兼容。
    """
    if not events:
        st.caption("（本次执行没有记录到任何事件）")
        return

    for ev in events:
        ev_type = ev.get("type", "")
        step_index = ev.get("step_index")
        title = _CRON_RUN_EVENT_TITLE.get(ev_type, ev_type or "（未知事件）")
        step_note = f" · 第 {step_index} 步" if isinstance(step_index, int) else ""

        if ev_type == "run_started":
            st.markdown(f"**{title}**")
            st.caption(
                f"任务：{ev.get('job_name', '?')}　·　"
                f"超时上限 {ev.get('timeout_seconds', '?')}s　·　"
                f"最大步数 {ev.get('max_steps', '?')}"
            )
        elif ev_type == "step":
            st.markdown(f"**{title}{step_note}**")
            full_text = ev.get("full_text") or ev.get("text_preview") or ""
            if full_text:
                truncated = len(full_text) >= STEP_FULL_TEXT_MAX_CHARS_UI
                with st.expander("完整输出" + ("（已截断）" if truncated else ""), expanded=False):
                    st.markdown(full_text)
            tool_calls = ev.get("tool_calls") or []
            if tool_calls:
                st.caption(f"🔧 本步工具调用 {len(tool_calls)} 次")
                for i, tc in enumerate(tool_calls):
                    with st.expander(f"🔧 {tc.get('name', '?')} · 第 {i + 1} 次调用", expanded=False):
                        st.markdown("**input**")
                        st.code(str(tc.get("input", "")), language="json")
                        st.markdown("**output**")
                        st.code(str(tc.get("output", "")), language="text")
            if ev.get("error"):
                st.error(ev["error"])
        elif ev_type in ("stuck_recover", "stuck_give_up", "timed_out", "max_steps_reached"):
            st.markdown(f"**{title}{step_note}**")
        elif ev_type == "step_error":
            st.markdown(f"**{title}{step_note}**")
            if ev.get("error"):
                st.error(ev["error"])
        elif ev_type == "run_finished":
            dur = ev.get("duration_seconds")
            dur_note = f"，耗时 {dur:.1f}s" if isinstance(dur, (int, float)) else ""
            st.markdown(
                f"**{title}** · 状态 `{ev.get('status', '?')}` · "
                f"共 {ev.get('steps_executed', '?')} 步{dur_note}"
            )
        else:
            # 未识别的事件类型：不丢数据，原样兜底展示。
            st.markdown(f"**{title}{step_note}**")
            st.json(ev, expanded=False)
        st.divider()


# 与 cron_job_executor.STEP_FULL_TEXT_MAX_CHARS 保持一致，仅用于展示层判断
# "是否可能被截断"的提示文案，不参与截断本身（截断发生在写事件时）。
STEP_FULL_TEXT_MAX_CHARS_UI = 8000


def render_cron_jobs_tab(client: AgentClient):
    """展示每个 cron job 的调度信息（来自 /cron/jobs）+ 专属执行状态
    （来自 /cron/jobs/{id}/workspace，对应 evolution/cron_job_workspace.py
    落盘的 state.json/config.json/runs/）。

    needs_human_review 状态的 job 会高亮显示，并提供"重置为 idle"按钮
    （对应 CronJobExecutor 的 StuckDetector 判定 GIVE_UP 或单步异常后的
    人工介入路径）。
    """
    st.markdown("#### ⏰ Cron 任务")
    st.caption(
        "cron job 现在跑在独立后台线程里，不会和其它 cron job 或用户对话"
        "互相阻塞；单次执行有超时/步数上限兜底，输出连续雷同时会自动判定"
        "\"卡住\"并停止，需要人工确认后才会继续调度。"
    )

    if st.button("🔄 刷新", key="cron_jobs_refresh"):
        st.rerun()

    resp = client.cron_jobs()
    if resp and "_error" in resp:
        st.error(f"获取 cron job 列表失败：{resp['_error']}")
        return
    jobs = (resp or {}).get("jobs") or []
    if not jobs:
        st.info("当前没有 cron job。")
        return

    # [daemon_stability_and_ux_improvement_plan.md 补充 / 看板顶栏跳转]
    # 从顶栏"正在执行"跳转过来时，`cron_focus_job_id` 记录了目标 job_id——
    # 把它排到列表最前面并给一句提示，而不是让用户在一长串 job 里自己找；
    # 提供"清除定位"按钮恢复默认（按原始顺序展示全部）。
    focus_job_id = st.session_state.get("cron_focus_job_id")
    if focus_job_id:
        fc1, fc2 = st.columns([6, 1])
        focus_job = next((j for j in jobs if j.get("id") == focus_job_id), None)
        focus_name = focus_job.get("name", focus_job_id) if focus_job else focus_job_id
        fc1.success(f"🎯 已定位到 Cron 任务：**{focus_name}**（来自顶栏『正在执行』跳转，已置顶展示）")
        if fc2.button("❌ 清除定位", key="cron_focus_clear"):
            st.session_state["cron_focus_job_id"] = None
            st.rerun()
        jobs = sorted(jobs, key=lambda j: 0 if j.get("id") == focus_job_id else 1)

    for job in jobs:
        job_id = job.get("id", "")
        is_system_job = bool(job.get("is_system")) or job_id.startswith("sys:")
        exec_phase = job.get("execution_phase", "not_running")
        with st.container(border=True):
            top1, top2, top3 = st.columns([3, 2, 2])
            name_label = f"**{job.get('name', job_id)}**"
            if is_system_job:
                name_label += "　🛠️ 系统任务"
            top1.markdown(name_label)
            top1.caption(job_id)
            top2.caption(f"schedule: `{job.get('schedule', '')}`")
            # [P2] 展示排队优先级——同一次 tick 内多个 job 同时到期时，
            # priority 数值大的先被提交，帮助解释"为什么这个先跑那个后跑"。
            top2.caption(f"priority: {job.get('priority', 0)}")
            top3.caption(f"下次运行：{job.get('next_run_str', '-')}")
            phase_label = _CRON_PHASE_LABEL.get(exec_phase)
            if phase_label:
                top3.caption(phase_label)

            desc = job.get("description", "")
            if desc:
                st.caption(desc)

            # [资源仲裁可见性] 连续因仲裁被跳过触发的次数——sys: job 不受
            # 仲裁约束（见 §3.2），恒为 0，这里只对非 system job 展示，
            # 避免误导。达到告警阈值时用 🔴 高亮，未达到时用普通 caption，
            # 具体阈值配置见 GET/PATCH /v1/self/config 的 cron.skip_alert_threshold
            # （或看板"⚙️ 配置"tab），本处只展示计数本身，不重复读取全局阈值。
            if not is_system_job:
                skip_count = job.get("consecutive_skip_count", 0)
                if skip_count > 0:
                    st.caption(f"⚖️ 连续因资源仲裁被跳过触发：{skip_count} 次")

            ws_resp = client.cron_job_workspace(job_id)
            if ws_resp and "_error" in ws_resp:
                st.caption(f"（无法获取执行状态：{ws_resp['_error']}）")
            else:
                state = (ws_resp or {}).get("state") or {}
                config = (ws_resp or {}).get("config") or {}
                is_running = (ws_resp or {}).get("is_running", False)
                status = state.get("status", "idle")
                status_label = _CRON_STATUS_LABEL.get(status, status)
                # execution_phase 更细粒度地区分"排队中"和"真正在跑"；
                # not_running 时退回 state.json 里的上一次结果状态展示。
                if exec_phase == "queued":
                    display_status = "🟠 排队中"
                elif exec_phase == "running":
                    display_status = "🔵 执行中"
                else:
                    display_status = status_label

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("状态", display_status)
                c2.metric("累计运行次数", job.get("run_count", 0))
                c3.metric("连续失败次数", state.get("consecutive_failures", 0))
                c4.metric("超时上限", f"{config.get('timeout_seconds', 1200) // 60} 分钟")
                st.caption(
                    f"最大步数 max_steps={config.get('max_steps', 60)} · "
                    f"卡死检测：相似度阈值 {config.get('stuck_similarity_threshold', 0.92)} / "
                    f"连续 {config.get('stuck_consecutive_limit', 3)} 步触发 / "
                    f"最多恢复 {config.get('stuck_max_recoveries', 2)} 次后判定 GIVE_UP"
                )

                progress = state.get("progress_summary", "")
                if progress:
                    with st.expander("📋 上次遗留的进度摘要"):
                        st.text(progress)

                last_error = state.get("last_error", "")
                if status == "needs_human_review" and last_error:
                    st.error(f"上次执行判定异常：{last_error}")

                last_started = state.get("last_run_started_at")
                last_finished = state.get("last_run_finished_at")
                if last_started:
                    started_str = time.strftime("%m-%d %H:%M", time.localtime(last_started))
                    if last_finished and last_finished >= last_started:
                        dur_min = (last_finished - last_started) / 60.0
                        st.caption(f"上次执行：{started_str} 起，耗时 {dur_min:.1f} 分钟")
                    else:
                        st.caption(f"上次执行：{started_str} 起（尚未结束）")

                if status == "needs_human_review" and not is_running:
                    if st.button("✅ 确认已处理，重置为空闲", key=f"cron_reset_{job_id}"):
                        res = client.reset_cron_job(job_id)
                        if res and "_error" in res:
                            st.error(f"重置失败：{res['_error']}")
                        else:
                            st.success("已重置，下次触发将从头开始执行。")
                            st.rerun()

                # [最近执行记录只有 run_id，看不出是否成功] 优先用带成功/
                # 失败判定的 recent_runs_summary；旧版后端（未升级）没有这个
                # 字段时回退到只有 run_id 的旧列表，保持向后兼容不报错。
                recent_runs_summary = (ws_resp or {}).get("recent_runs_summary")
                recent_runs = (ws_resp or {}).get("recent_runs") or []
                _RUN_STATUS_BADGE = {
                    "success": "✅ 成功",
                    "timed_out": "⏱️ 超时",
                    "failed": "❌ 失败",
                    "crashed_or_running": "❓ 未知（进程异常退出/仍在运行）",
                }
                if recent_runs_summary is not None:
                    if recent_runs_summary:
                        with st.expander(f"🗒️ 最近执行记录（{len(recent_runs_summary)} 条）"):
                            for run in recent_runs_summary:
                                run_id = run.get("run_id", "")
                                started_at = run.get("started_at")
                                time_str = (
                                    time.strftime("%m-%d %H:%M:%S", time.localtime(started_at))
                                    if started_at else run_id
                                )
                                badge = _RUN_STATUS_BADGE.get(run.get("status"), run.get("status") or "未知")
                                dur = run.get("duration_seconds")
                                dur_note = f"，耗时 {dur:.1f}s" if isinstance(dur, (int, float)) else ""
                                st.markdown(f"**{time_str}** {badge}{dur_note}")
                                if not run.get("success") and run.get("error"):
                                    st.error(run["error"])
                                if st.button(f"查看事件详情 {run_id}", key=f"cron_run_{job_id}_{run_id}"):
                                    events_resp = client.cron_job_run_events(job_id, run_id)
                                    if events_resp and "_error" in events_resp:
                                        st.error(f"获取执行事件失败：{events_resp['_error']}")
                                    else:
                                        _render_cron_run_timeline((events_resp or {}).get("events") or [])
                                st.divider()
                elif recent_runs:
                    with st.expander(f"🗒️ 最近执行记录（{len(recent_runs)} 条）"):
                        for run_id in recent_runs:
                            if st.button(f"查看 {run_id}", key=f"cron_run_{job_id}_{run_id}"):
                                events_resp = client.cron_job_run_events(job_id, run_id)
                                if events_resp and "_error" in events_resp:
                                    st.error(f"获取执行事件失败：{events_resp['_error']}")
                                else:
                                    _render_cron_run_timeline((events_resp or {}).get("events") or [])

                with st.expander("✏️ 编辑任务 Prompt"):
                    prompt_resp = client.cron_job_prompt(job_id)
                    if prompt_resp and "_error" in prompt_resp:
                        st.caption(f"获取 prompt 失败：{prompt_resp['_error']}")
                    else:
                        current_prompt = (prompt_resp or {}).get("prompt", "")
                        new_prompt = st.text_area(
                            "prompt.md（支持 {{task_description}} / {{progress}} 占位符）",
                            value=current_prompt, height=160,
                            key=f"cron_prompt_{job_id}",
                        )
                        if st.button("💾 保存 prompt", key=f"cron_prompt_save_{job_id}"):
                            save_res = client.update_cron_job_prompt(job_id, new_prompt)
                            if save_res and "_error" in save_res:
                                st.error(f"保存失败：{save_res['_error']}")
                            else:
                                st.success("已保存，下次该 job 触发时生效。")

            # [goal_cron_feedback_and_output_policy_plan.md Track E] 用户对本
            # CronJob 提意见——持久化写入 description/task_template（及
            # dedicated 模式下的 prompt.md）；若绑定了 Goal（run_mode=goal_cycle）
            # 会自动双向同步。复用 P1-P4 观测面板的卡片样式，保持视觉一致。
            with st.expander("💬 提意见", expanded=False):
                job_feedback = job.get("user_feedback") or []
                if job_feedback:
                    for item in reversed(job_feedback):
                        at = item.get("at")
                        ts_str = time.strftime("%m-%d %H:%M", time.localtime(at)) if at else "-"
                        st.caption(f"`{ts_str}` {item.get('text', '')}")
                else:
                    st.caption("还没有意见记录。")
                with st.form(f"cron_feedback_{job_id}", clear_on_submit=True):
                    fb_text = st.text_area(
                        "你的意见（会永久合入这个 job 的说明，之后每次触发都会带着）",
                        height=60, key=f"cron_feedback_text_{job_id}",
                    )
                    fb_submit = st.form_submit_button("提交意见")
                if fb_submit:
                    if not fb_text.strip():
                        st.error("意见内容不能为空")
                    else:
                        res = client.add_cron_job_feedback(job_id, fb_text.strip())
                        if res and "_error" in res:
                            st.error(res["_error"])
                        else:
                            st.rerun()

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("▶️ 立即运行一次", key=f"cron_run_now_{job_id}"):
                    res = client.run_cron_job_now(job_id)
                    if res and "_error" in res:
                        st.error(f"触发失败：{res['_error']}")
                    else:
                        st.success("已触发，稍后可在上方状态区看到执行进度。")
                        st.rerun()
            with btn_col2:
                enabled = job.get("enabled", True)
                toggle_label = "⏸️ 禁用" if enabled else "▶️ 启用"
                if st.button(toggle_label, key=f"cron_toggle_{job_id}"):
                    res = client.update_cron_job(job_id, enabled=not enabled)
                    if res and "_error" in res:
                        st.error(f"操作失败：{res['_error']}")
                    else:
                        st.rerun()

            with st.expander("🔢 调整优先级"):
                new_priority = st.number_input(
                    "priority（数值越大，同一次 tick 内到期时越先被触发；不做抢占）",
                    value=int(job.get("priority", 0)), step=1,
                    key=f"cron_priority_{job_id}",
                )
                if st.button("💾 保存优先级", key=f"cron_priority_save_{job_id}"):
                    res = client.update_cron_job(job_id, priority=int(new_priority))
                    if res and "_error" in res:
                        st.error(f"保存失败：{res['_error']}")
                    else:
                        st.success("已保存。")
                        st.rerun()

            # [新增] 编辑该 job 专属的执行限制覆盖（超时/最大步数/卡死检测
            # 参数）。字段没被这个 job 显式覆盖过时，输入框预填的是"当前
            # 生效值"，其实际来源是全局 CronConfig 的默认值——运维之后调整
            # 全局默认，这个 job 会自动跟着变（见 PUT /cron/jobs/{id}/config
            # 和 CronJobWorkspace.write_config_overrides() 的说明），不需要
            # 用户手动同步。只有点了下面的"保存"才会把值固化成这个 job 自己
            # 的显式覆盖；"恢复为全局默认"会清除覆盖，重新跟随全局配置。
            with st.expander("⚙️ 调整执行配置（超时 / 步数 / 卡死检测）"):
                cfg_ws_resp = client.cron_job_workspace(job_id)
                if cfg_ws_resp and "_error" in cfg_ws_resp:
                    st.caption(f"获取当前配置失败：{cfg_ws_resp['_error']}")
                else:
                    effective_cfg = (cfg_ws_resp or {}).get("config") or {}
                    overrides = (cfg_ws_resp or {}).get("config_overrides") or {}

                    def _field_caption(field_key, label):
                        return f"{label}（{'已自定义' if field_key in overrides else '跟随全局默认'}）"

                    cfg_c1, cfg_c2 = st.columns(2)
                    new_timeout_min = cfg_c1.number_input(
                        _field_caption("timeout_seconds", "超时（分钟）"),
                        min_value=1,
                        value=max(1, int(effective_cfg.get("timeout_seconds", 1200)) // 60),
                        step=1,
                        key=f"cron_cfg_timeout_{job_id}",
                    )
                    new_max_steps = cfg_c2.number_input(
                        _field_caption("max_steps", "最大步数"),
                        min_value=1,
                        value=int(effective_cfg.get("max_steps", 60)),
                        step=1,
                        key=f"cron_cfg_max_steps_{job_id}",
                    )
                    cfg_c3, cfg_c4, cfg_c5 = st.columns(3)
                    new_similarity = cfg_c3.number_input(
                        _field_caption("stuck_similarity_threshold", "卡死相似度阈值"),
                        min_value=0.01, max_value=1.0,
                        value=float(effective_cfg.get("stuck_similarity_threshold", 0.92)),
                        step=0.01, format="%.2f",
                        key=f"cron_cfg_similarity_{job_id}",
                    )
                    new_consecutive = cfg_c4.number_input(
                        _field_caption("stuck_consecutive_limit", "连续雷同步数"),
                        min_value=1,
                        value=int(effective_cfg.get("stuck_consecutive_limit", 3)),
                        step=1,
                        key=f"cron_cfg_consecutive_{job_id}",
                    )
                    new_recoveries = cfg_c5.number_input(
                        _field_caption("stuck_max_recoveries", "最多恢复次数"),
                        min_value=0,
                        value=int(effective_cfg.get("stuck_max_recoveries", 2)),
                        step=1,
                        key=f"cron_cfg_recoveries_{job_id}",
                    )

                    save_col, reset_col = st.columns(2)
                    with save_col:
                        if st.button("💾 保存为自定义配置", key=f"cron_cfg_save_{job_id}"):
                            res = client.update_cron_job_config(
                                job_id,
                                timeout_seconds=int(new_timeout_min) * 60,
                                max_steps=int(new_max_steps),
                                stuck_similarity_threshold=float(new_similarity),
                                stuck_consecutive_limit=int(new_consecutive),
                                stuck_max_recoveries=int(new_recoveries),
                            )
                            if res and "_error" in res:
                                st.error(f"保存失败：{res['_error']}")
                            else:
                                st.success("已保存，下次该 job 触发时生效。")
                                st.rerun()
                    with reset_col:
                        if st.button("↩️ 恢复为全局默认", key=f"cron_cfg_reset_{job_id}"):
                            res = client.update_cron_job_config(
                                job_id,
                                timeout_seconds=None,
                                max_steps=None,
                                stuck_similarity_threshold=None,
                                stuck_consecutive_limit=None,
                                stuck_max_recoveries=None,
                            )
                            if res and "_error" in res:
                                st.error(f"重置失败：{res['_error']}")
                            else:
                                st.success("已恢复跟随全局默认。")
                                st.rerun()

            # [看板 cron 任务标签页补齐删除功能] 此前只有"目标看板"tab里
            # 才能删除 cron job，本 tab（Cron 任务）只有运行/启停/改优先级，
            # 没有删除入口——用户想删一个非 sys: 前缀的自定义 job 必须切
            # 换到目标看板才行，体验割裂。这里补一份与目标看板一致的删除
            # UI（is_system 的 job 不展示删除按钮、只能禁用；非 system job
            # 删除前二次确认，用 confirm_key 这个 session_state 标记控制）。
            if not is_system_job:
                st.markdown("###### 🗑️ 删除任务")
                confirm_key = f"cron_tab_confirm_delete_{job_id}"
                if not st.session_state.get(confirm_key):
                    if st.button("🗑️ 删除", key=f"cron_tab_delete_{job_id}"):
                        st.session_state[confirm_key] = True
                        st.rerun()
                else:
                    dc1, dc2 = st.columns(2)
                    with dc1:
                        if st.button("⚠️ 确认删除", key=f"cron_tab_delete_confirm_{job_id}"):
                            result = client.delete_cron_job(job_id)
                            st.session_state.pop(confirm_key, None)
                            if isinstance(result, dict) and result.get("_error"):
                                st.error(f"删除失败：{result['_error']}")
                            else:
                                st.success(f"已删除 cron job：{job.get('name')}")
                            st.rerun()
                    with dc2:
                        if st.button("取消", key=f"cron_tab_delete_cancel_{job_id}"):
                            st.session_state.pop(confirm_key, None)
                            st.rerun()
            else:
                st.caption("系统内置任务，不可删除，只能禁用。")

    st.divider()
    with st.expander("➕ 新建 cron job"):
        new_name = st.text_input("名称", key="cron_new_name")
        new_schedule = st.text_input(
            "schedule（interval:<秒> 或 cron:<分 时 日 月 周>）",
            value="interval:3600", key="cron_new_schedule",
        )
        new_template = st.text_area("任务描述（task_template）", key="cron_new_template")
        new_desc = st.text_input("说明（可选）", key="cron_new_desc")
        new_priority = st.number_input(
            "priority（默认 0，数值越大同一次 tick 内到期时越先被触发）",
            value=0, step=1, key="cron_new_priority",
        )
        if st.button("创建", key="cron_new_submit"):
            if not new_name.strip() or not new_template.strip():
                st.warning("名称和任务描述不能为空。")
            else:
                from mini_agent.evolution.cron_scheduler import validate_schedule
                schedule_error = validate_schedule(new_schedule)
                if schedule_error:
                    st.warning(f"schedule 格式不合法：{schedule_error}")
                else:
                    res = client.add_cron_job(
                        new_name, new_schedule, new_template, new_desc,
                        priority=int(new_priority),
                    )
                    if res and "_error" in res:
                        st.error(f"创建失败：{res['_error']}")
                    else:
                        st.success("已创建。")
                        st.rerun()


# ═══════════════════════════════════════════════════════════════════════
# Tab: 🗓️ 全局日程（scheduling_unification_and_kanban_visibility_
#          improvement_plan.md P5）
# ═══════════════════════════════════════════════════════════════════════

_GATING_BADGE_MAP = {
    "full": ("🟢", "空闲可执行"),
    "degraded": ("🟡", "降级运行"),
    "blocked": ("🔴", "已暂停"),
}


def render_global_schedule_tab(client: AgentClient):
    """[P5] 把三类此前分散在不同 tab 的时间信息合并成一条时间线：
    - 未来 24 小时内到期的 cron job（含 P2 的 priority）
    - 有 recurring goal 绑定的下一次触发（复用 P4 的单一数据源：
      cron_jobs 列表里的 next_run_str，不重复计算）
    - 最近一次仲裁状态变化的时间点（何时从 full 变成 degraded/blocked，
      何时恢复，来自 P5 新增的 /v1/autonomous/gating_history）

    依赖 P2（priority 字段）/P3（仲裁状态可见）/P4（recurring 下次触发
    单一数据源）已经落地的数据，本 tab 本身是纯展示层，不新增调度逻辑。
    """
    st.markdown("#### 🗓️ 全局日程")
    st.caption(
        "把 cron job 到期时间、周期性 Goal 下次触发、仲裁状态变化时间线"
        "合并展示，定位\"为什么现在没有任务在跑\"时不用再挨个 tab 翻。"
    )

    if st.button("🔄 刷新", key="global_schedule_refresh"):
        st.rerun()

    autostat = client.autonomous_status() or {}
    if "_error" in autostat:
        st.error(f"获取自主执行状态失败：{autostat['_error']}")
        return

    # ── 顶部：仲裁状态一览（复用顶栏同款徽标语义，这里展开常驻显示，不用点开）──
    gating = autostat.get("gating") or {}
    gating_state = gating.get("gating_state", "full")
    gating_icon, gating_label = _GATING_BADGE_MAP.get(gating_state, ("⚪", "未知"))
    st.markdown(f"**当前仲裁状态：{gating_icon} {gating_label}** — {gating.get('gating_reason', '')}")

    st.divider()

    # ── 区块 1：未来 24 小时内到期的 cron job（含 priority），按到期时间排序 ──
    st.markdown("##### ⏰ 未来 24 小时内到期的 cron job")
    cron_jobs = autostat.get("cron_jobs") or []
    upcoming = [j for j in cron_jobs if j.get("enabled") and (j.get("next_run_in") or 0) <= 24 * 3600]
    upcoming.sort(key=lambda j: j.get("next_run_in", 0))
    if not upcoming:
        st.caption("未来 24 小时内没有到期的 cron job。")
    else:
        for j in upcoming:
            c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
            c1.markdown(f"**{j.get('name', j.get('id', ''))}**")
            c1.caption(j.get("id", ""))
            c2.caption(f"下次：{j.get('next_run_str', '-')}")
            c3.caption(f"priority: {j.get('priority', 0)}")
            c4.caption(f"已运行: {j.get('run_count', 0)} 次")

    st.divider()

    # ── 区块 2：绑定了 recurring 的 Goal 下次触发（单一数据源：cron_jobs） ──
    st.markdown("##### 🔁 周期性 Goal 下次触发")
    cron_next_run_by_id = {j.get("id"): j.get("next_run_str", "-") for j in cron_jobs}
    goals_data = client.goals() or {}
    if "_error" in goals_data:
        st.caption(f"目标数据获取失败：{goals_data['_error']}")
    else:
        recurring_goals = [
            n for n in (goals_data.get("goals") or [])
            if n.get("level") != "objective" and n.get("recurring")
        ]
        if not recurring_goals:
            st.caption("当前没有设置周期性触发的 Goal。")
        else:
            for n in recurring_goals:
                cron_job_id = n.get("recurrence_cron_job_id")
                next_run = cron_next_run_by_id.get(cron_job_id, "-") if cron_job_id else "-（未绑定 cron job）"
                c1, c2, c3 = st.columns([3, 2, 2])
                c1.markdown(f"**{n.get('title', n.get('id', ''))}**")
                c2.caption(f"下次触发：{next_run}")
                c3.caption(f"已完成 {n.get('cycle_count', 0)} 轮")

    st.divider()

    # ── 区块 3：仲裁状态变化时间线（何时 full → degraded/blocked，何时恢复）──
    st.markdown("##### 📈 仲裁状态变化时间线")
    hist_resp = client.gating_history(limit=50)
    if hist_resp and "_error" in hist_resp:
        st.caption(f"获取仲裁状态历史失败：{hist_resp['_error']}")
    else:
        # [kanban_perception_gaps_improvement_plan.md 方向 C] 逐条时间线在
        # 条目多的时候人眼很难心算出"过去 7 天有百分之多少的时间处于
        # degraded/blocked"，这里在时间线上方加一行聚合占比摘要。
        ratio_summary = (hist_resp or {}).get("ratio_summary") or {}
        ratios = ratio_summary.get("ratios") or {}
        if any(ratios.values()):
            incomplete_note = "（数据不完整，可能因为期间状态变化过于频繁）" if ratio_summary.get("incomplete") else ""
            st.caption(
                f"过去 {ratio_summary.get('window_days', 7):.0f} 天：🟢 正常 "
                f"{ratios.get('full', 0):.0%} · 🟡 降级 {ratios.get('degraded', 0):.0%} · "
                f"🔴 阻塞 {ratios.get('blocked', 0):.0%}{incomplete_note}"
            )
        history = (hist_resp or {}).get("history") or []
        if not history:
            st.caption("暂无状态变化记录（仲裁状态自看板启用以来一直保持不变，"
                       "或 daemon 尚未产生足够的 /autonomous/status 轮询记录）。")
        else:
            # 最新的排在最上面，更符合"看最近发生了什么"的阅读习惯。
            for entry in reversed(history):
                icon, label = _GATING_BADGE_MAP.get(entry.get("state", "full"), ("⚪", "未知"))
                st.caption(
                    f"{entry.get('at_str', '?')} — {icon} {label}"
                    f"（{entry.get('reason', '')}）"
                )

    st.divider()
    _render_fairness_diagnostics(client)


# [goal_fairness_scheduling_diagnostics_plan.md] "⚖️ 调度公平性诊断"——纯
# 只读快照，不提供任何操作按钮。回答"公平轮询/老化加成/时间片抢占这几个
# 默认值拍脑袋定的参数，现在实际状态是什么样"，帮助判断参数是否需要调整，
# 而不是凭感觉猜。
def _render_fairness_diagnostics(client: AgentClient) -> None:
    with st.expander("⚖️ 调度公平性诊断（只读）", expanded=False):
        data = client.fairness_diagnostics() or {}
        if "_error" in data:
            st.caption(f"读取失败：{data['_error']}")
            return
        enabled = data.get("time_slicing_enabled", False)
        st.markdown(f"时间片抢占（P4）：{'🟢 已开启' if enabled else '⚪ 未开启（默认关闭）'}")

        cfg = data.get("config") or {}
        st.caption(
            f"当前生效参数 — 老化加成：每停滞 1 天 +{cfg.get('aging_boost_per_day', 1.0):.2f}"
            f"（累计上限 {cfg.get('aging_boost_max_days', 14.0):.0f} 天），"
            f"停滞判定阈值 {cfg.get('stale_days', 7.0):.0f} 天；"
            f"抢占触发条件：单次时间片 ≥{cfg.get('yield_after_steps', 3)} 步 或 "
            f"≥{cfg.get('yield_after_seconds', 900.0):.0f} 秒"
        )

        paused_count = data.get("paused_for_fairness_count", 0)
        active_count = data.get("active_objectives_count", 0)
        boosted_count = data.get("goals_with_active_aging_boost", 0)
        c1, c2, c3 = st.columns(3)
        c1.metric("当前 active objectives", active_count)
        c2.metric("当前老化加成生效数", boosted_count)
        c3.metric("当前因抢占暂停数", paused_count)

        objectives = data.get("objectives") or []
        if objectives:
            with st.expander(f"逐个 objective 明细（共 {len(objectives)} 条，按 effective_priority 排序）", expanded=False):
                for item in objectives:
                    flags = []
                    if item.get("is_running"):
                        flags.append("▶️运行中")
                    if item.get("is_paused_for_fairness"):
                        flags.append("⏸️已让出")
                    flag_str = f"（{' '.join(flags)}）" if flags else ""
                    st.caption(
                        f"{item.get('objective_id', '')}：priority={item.get('priority', 0):.1f} "
                        f"+ aging_boost={item.get('aging_boost', 0):.2f} "
                        f"= effective={item.get('effective_priority', 0):.2f}{flag_str}"
                    )


# ═══════════════════════════════════════════════════════════════════════
# Tab: ⚙️ 配置管理（kanban_config_management_plan.md）
# ═══════════════════════════════════════════════════════════════════════
def _render_config_field_widget(field: dict, widget_key: str):
    """按字段类型渲染对应的编辑控件，返回 (新值, 是否被改动)。

    敏感字段（sensitive=True）只展示是否已配置，不提供编辑控件——修改
    需要用户手工编辑 agent_config.json，见 config_catalog.py 模块头部说明。
    """
    json_key = field["json_key"]
    label = field.get("label") or json_key
    value = field.get("value")
    ftype = field.get("type")

    if field.get("sensitive"):
        st.text_input(
            f"{label}（`{json_key}`）", value="已配置 ✓" if value else "未配置",
            disabled=True, key=widget_key,
        )
        return value, False

    if ftype == "bool":
        new_v = st.checkbox(f"{label}（`{json_key}`）", value=bool(value), key=widget_key)
    elif ftype == "int":
        new_v = st.number_input(
            f"{label}（`{json_key}`）", value=int(value or 0), step=1, format="%d", key=widget_key,
        )
        new_v = int(new_v)
    elif ftype == "float":
        new_v = st.number_input(
            f"{label}（`{json_key}`）", value=float(value or 0.0), key=widget_key,
        )
    elif ftype == "list":
        # [next_doc/growth_advisor_design.md] 第 5 节"设置入口"：
        # excluded_topics 这类字符串列表字段，此前落进 else 分支被当成
        # 普通文本框（会显示成 "['a', 'b']" 这种 repr，编辑体验很差且容易
        # 保存出脏数据）。改成一行一项的文本域，空行/首尾空白自动忽略，
        # 这是通用改动，不止对 growth_advisor 生效——任何 list 类型的配置
        # 字段都会受益。
        items = value if isinstance(value, list) else []
        text_value = "\n".join(str(v) for v in items)
        new_text = st.text_area(
            f"{label}（`{json_key}`，每行一项）", value=text_value, key=widget_key, height=100,
        )
        new_v = [line.strip() for line in new_text.splitlines() if line.strip()]
        changed = new_v != items
        return new_v, changed
    elif ftype == "dict":
        # [P4-5 code review 补丁] `category_notification_frequency` 这类
        # dict 类型配置字段此前会落进下面的 else 分支，被当成普通文本框
        # ——显示成 Python dict 的 repr（`{'技术类': 'kanban_only'}`），
        # 编辑后保存的是一整条字符串而不是 dict，`apply_updates()` 不做
        # 类型校验会直接原样写进 JSON，导致下次加载 `GrowthAdvisorConfig`
        # 时这个字段类型不对——跟上面 `list` 分支的修复是同一类问题，
        # 用同样的思路修：一行一项，`key=value` 格式，空行/无 `=` 的行
        # 忽略。只处理 str->str 这种简单场景（目前唯一的 dict 类型配置
        # 字段就是这种），不支持嵌套结构。
        items = value if isinstance(value, dict) else {}
        text_value = "\n".join(f"{k}={v}" for k, v in items.items())
        new_text = st.text_area(
            f"{label}（`{json_key}`，每行一项，格式 key=value）",
            value=text_value, key=widget_key, height=100,
        )
        new_v: dict = {}
        for line in new_text.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            if k:
                new_v[k] = v
        changed = new_v != items
        return new_v, changed
    else:  # str / other，一律按文本框处理，None 显示为空字符串
        new_v = st.text_input(f"{label}（`{json_key}`）", value="" if value is None else str(value), key=widget_key)

    changed = new_v != value
    return new_v, changed


def _render_config_category(client: AgentClient, cat: dict, filter_kw: str):
    fields = cat["fields"]
    if filter_kw:
        kw = filter_kw.strip().lower()
        fields = [
            f for f in fields
            if kw in f["json_key"].lower() or kw in (f.get("label") or "").lower()
        ]
        if not fields:
            return
    n_customized = sum(1 for f in cat["fields"] if f["customized"])
    badge = f"（{n_customized} 项已自定义）" if n_customized else ""
    with st.expander(f"{cat['icon']} {cat['label']} {badge}", expanded=bool(filter_kw or n_customized)):
        with st.form(key=f"cfgform_{cat['id']}"):
            pending = {}
            for f in fields:
                widget_key = f"cfgfield_{cat['id']}_{f['json_key']}"
                new_v, changed = _render_config_field_widget(f, widget_key)
                if changed and not f.get("sensitive"):
                    pending[f["json_key"]] = new_v
            submitted = st.form_submit_button(f"💾 保存「{cat['label']}」的改动")
            if submitted:
                if not pending:
                    st.info("没有检测到改动。")
                else:
                    updates = [{"json_key": k, "value": v} for k, v in pending.items()]
                    resp = client.config_update(updates)
                    if resp and "_error" in resp:
                        st.error(f"保存失败：{resp['_error']}")
                    else:
                        st.success(f"已保存 {len(pending)} 项改动，重启 agent 进程后生效。")
                        st.session_state.pop("_config_status_cache", None)
                        st.rerun()


def render_config_tab(client: AgentClient):
    """[kanban_config_management_plan.md] 读取并分类展示/编辑
    agent_config.json——把此前只能靠翻文档/翻源码才知道"有哪些配置项、
    分别控制什么功能、当前是否被自定义过"的信息，统一到看板里。

    机制说明（详见设计文档）：
      - 字段目录来自 config_catalog.py，按功能域分类，每类一个可折叠区块。
      - 每个字段展示"当前生效值 / 默认值 / 是否已自定义"，一眼看出哪些功能
        被改过默认行为。
      - 编辑仅覆盖 bool/int/float/str 这类可以用单值控件安全表达的字段；
        list/dict 类复杂字段（mcp_servers、隐私 secrets 等）不在此提供编辑，
        仍需直接编辑 JSON 文件（原因见设计文档"设计边界"）。
      - 保存按分类分别提交（每类一个 st.form + 独立保存按钮），只提交本类
        里实际改动过的字段，避免"改了一个开关却把其它没碰过的字段也重新
        提交一遍"的误操作风险。
      - 所有修改都需要重启 agent 进程才会生效（AppConfig 目前是进程启动时
        一次性加载，没有热重载机制）——每次保存成功后都会提示这一点。
    """
    st.markdown("#### ⚙️ 配置管理")
    st.caption(
        "读取并分类展示 agent_config.json 的配置项状态，支持直接在看板里修改。"
        "🔶 标记的分类表示其中有字段已偏离默认值。修改保存后需要**重启 agent "
        "进程**才会生效。"
    )

    col1, col2 = st.columns([3, 1])
    with col1:
        filter_kw = st.text_input(
            "🔍 按字段名/说明筛选（留空显示全部分类）", key="config_filter_kw",
        )
    with col2:
        if st.button("🔄 刷新", key="config_refresh"):
            st.session_state.pop("_config_status_cache", None)
            st.rerun()

    if "_config_status_cache" not in st.session_state:
        st.session_state["_config_status_cache"] = client.config_status()
    resp = st.session_state["_config_status_cache"] or {}

    if "_error" in resp:
        st.warning(f"获取失败：{resp['_error']}")
        return

    st.caption(f"配置文件：`{resp.get('config_path', '')}`")

    categories = resp.get("categories") or []
    total_customized = sum(
        1 for c in categories for f in c["fields"] if f["customized"]
    )
    st.caption(f"共 {len(categories)} 个分类 · {sum(len(c['fields']) for c in categories)} 个可见字段 · "
               f"{total_customized} 项已偏离默认值")

    for cat in categories:
        _render_config_category(client, cat, filter_kw)


# ═══════════════════════════════════════════════════════════════════════
# Tab 6: 诊断
# ═══════════════════════════════════════════════════════════════════════
def render_diagnostics_tab(client: AgentClient):
    st.markdown("#### 🔧 诊断信息")
    diag = client.diagnostics() or {}
    st.json(diag, expanded=True)


# ═══════════════════════════════════════════════════════════════════════
# Tab: 📛 错误日志（~/.agent/logs/error.jsonl 错误类型分布统计）
# ═══════════════════════════════════════════════════════════════════════
def render_error_log_tab(client: AgentClient):
    st.markdown("#### 📛 错误日志统计")
    st.caption("数据来源：`~/.agent/logs/error.jsonl`（全局错误日志，跨 project/session）。")

    col_scope, col_filter = st.columns([1, 1])
    with col_scope:
        scope_label = st.radio(
            "统计范围", ["全部", "仅当天"], horizontal=True, key="error_log_scope"
        )
        scope = "today" if scope_label == "仅当天" else "all"
    with col_filter:
        exclude_te = st.checkbox(
            "过滤 mini_agent.tool_executor 相关错误",
            value=True,
            key="error_log_exclude_te",
            help="这类记录多为工具调用失败时的兜底日志，占比往往极高但通常不重要，"
                 "默认剔除以便看清其它真正需要关注的错误；取消勾选可查看全部。",
        )

    if st.button("🔄 刷新统计", key="error_log_refresh"):
        st.rerun()

    stats = client.error_log_stats(scope=scope, exclude_tool_executor=exclude_te) or {}
    if stats.get("_error"):
        st.error(f"获取错误日志统计失败：{stats['_error']}")
        return

    if not stats.get("log_exists"):
        st.info("错误日志文件尚不存在，说明目前还没有记录到任何异常，一切正常 🎉")
        return

    total = stats.get("total", 0)
    excluded = stats.get("excluded", 0)
    shown = stats.get("shown", 0)

    c1, c2, c3 = st.columns(3)
    c1.metric("范围内总数", total)
    c2.metric("已过滤（tool_executor）", excluded)
    c3.metric("参与统计", shown)

    if shown == 0:
        if total > 0:
            st.info("当前范围内的记录全部被 tool_executor 过滤规则剔除，"
                     "可取消勾选上面的过滤选项查看完整分布。")
        else:
            st.info("当前范围内没有错误记录。")
        return

    by_type = stats.get("by_type") or []
    by_where = stats.get("by_where") or []

    st.markdown("##### 按异常类型（exc_type）分布")
    if by_type:
        try:
            import pandas as pd
            df_type = pd.DataFrame(by_type).set_index("name")
            st.bar_chart(df_type["count"])
        except Exception:
            pass
        st.dataframe(
            [{"异常类型": r["name"], "次数": r["count"]} for r in by_type],
            width='stretch', hide_index=True,
        )
    else:
        st.caption("暂无数据。")

    st.markdown("##### 按发生位置（where）分布 Top N")
    if by_where:
        st.dataframe(
            [{"位置": r["name"], "次数": r["count"]} for r in by_where],
            width='stretch', hide_index=True,
        )
    else:
        st.caption("暂无数据。")

    with st.expander("原始统计 JSON"):
        st.json(stats, expanded=False)


# ═══════════════════════════════════════════════════════════════════════
# Tab: 🧪 混合执行（hybrid_exec，next_doc/hybrid_exec_design_plan.md P4）
# ═══════════════════════════════════════════════════════════════════════
def render_hybrid_exec_tab(client: AgentClient):
    """脚本/LLM/Agent 混合执行系统的只读观测面板：按 task_id 展示当前脚本
    仓库状态（active 版本/创建方式/累计成功率/连续失败次数）+ run 统计
    （总次数/成功率/各 tier 命中分布/最近一次执行结果）。纯只读，不提供
    在线编辑——需要强制重新探索/手动退役某个版本时，仍是改 workflow YAML
    的 params.force_reexplore 或直接操作 `.agent/hybrid_exec/scripts/`
    目录（详见 next_doc/hybrid_exec_design_plan.md）。"""
    st.markdown("#### 🧪 混合执行（脚本 / LLM / Agent）")
    st.caption(
        "每个 task_id 对应一个可复用的执行任务：优先用脚本执行（成本最低），"
        "脚本坏了先尝试用 LLM/Agent 自动修复，修不好或还没有脚本时才降级到 "
        "LLM/Agent 直接给答案。详见 next_doc/hybrid_exec_design_plan.md。"
    )

    resp = client.hybrid_exec_summary() or {}
    if "_error" in resp:
        st.caption(f"获取汇总失败：{resp['_error']}")
        return

    tasks = resp.get("tasks") or []
    if not tasks:
        st.info("暂无 hybrid_exec 执行记录（还没有任何 task_id 跑过，或 `.agent/hybrid_exec/` 目录不存在）。")
        return

    total_tasks = len(tasks)
    active_script_count = sum(1 for t in tasks if t.get("active_version") is not None)
    c1, c2 = st.columns(2)
    c1.metric("task 总数", total_tasks)
    c2.metric("当前有可用脚本", active_script_count)

    st.divider()

    STATUS_LABEL = {"active": "✅ 生效中", "none": "⚪ 无脚本（走 LLM/Agent）"}
    TIER_LABEL = {"script": "脚本", "llm": "LLM", "agent": "Agent"}

    for t in tasks:
        task_id = t.get("task_id", "?")
        status = t.get("active_status", "none")
        with st.expander(f"{STATUS_LABEL.get(status, status)} · `{task_id}`", expanded=False):
            if "_script_error" in t:
                st.caption(f"读取脚本仓库失败：{t['_script_error']}")
            elif t.get("active_version") is not None:
                sc, fc = t.get("active_success_count", 0), t.get("active_fail_count", 0)
                total = sc + fc
                rate_text = f"{sc / total:.0%}" if total else "-"
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("当前版本", f"v{t['active_version']}")
                col2.metric("累计成功率", rate_text, help=f"{sc} 成功 / {fc} 失败")
                col3.metric("连续失败", t.get("active_consecutive_fail", 0))
                col4.metric("历史版本数", t.get("version_count", 0))
                st.caption(f"当前版本由 {t.get('active_created_by', '?')} 产出")
            else:
                st.caption(f"暂无生效脚本（历史版本数：{t.get('version_count', 0)}，全部已退役或从未探索成功）")

            if "_run_error" in t:
                st.caption(f"读取 run 统计失败：{t['_run_error']}")
                continue
            run_summary = t.get("run_summary")
            if not run_summary:
                st.caption("暂无 run 记录。")
                continue

            total_runs = run_summary.get("total_runs", 0)
            success_runs = run_summary.get("success_runs", 0)
            run_rate_text = f"{success_runs / total_runs:.0%}" if total_runs else "-"
            rc1, rc2, rc3 = st.columns(3)
            rc1.metric("总执行次数", total_runs)
            rc2.metric("执行成功率", run_rate_text)
            rc3.metric("最近一次", "✅ 成功" if run_summary.get("last_run_ok") else "❌ 失败")

            tier_counts = run_summary.get("tier_counts") or {}
            if tier_counts:
                tier_text = " · ".join(
                    f"{TIER_LABEL.get(k, k)} {v} 次" for k, v in sorted(tier_counts.items())
                )
                st.caption(f"命中分布：{tier_text}")
            last_run_at = run_summary.get("last_run_at")
            if last_run_at:
                st.caption(f"最近一次执行时间：{last_run_at}（UTC）")


# ═══════════════════════════════════════════════════════════════════════
# 分页辅助（外部数据分页显示改进计划，next_doc/external_input_pagination_plan.md）
# ═══════════════════════════════════════════════════════════════════════
def _client_side_page(items: list, page_size: int, state_key: str) -> list:
    """给已经全量拿到的小型列表加统一的客户端"上一页/下一页"分页——纯
    本地切片，不发起新请求，不改变接口返回的顺序/内容语义（比如路由
    规则的匹配优先级顺序）。用于 sources/policies/watchlist/tiers 这类
    配置驱动、体量通常不大但仍可能增长的列表；天然增长、无上限的数据
    （事件流水/发送记录/告警）用下面的 `_load_more_control()`，两者分页
    方式不同的原因见设计文档。"""
    total = len(items)
    page_count = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(st.session_state.get(state_key, 0), page_count - 1))
    st.session_state[state_key] = page
    if total > page_size:
        c1, c2, c3 = st.columns([1, 2, 1])
        if c1.button("⬅️ 上一页", key=f"{state_key}_prev", disabled=page <= 0):
            st.session_state[state_key] = page - 1
            st.rerun()
        c2.caption(f"第 {page + 1} / {page_count} 页 · 共 {total} 条")
        if c3.button("下一页 ➡️", key=f"{state_key}_next", disabled=page >= page_count - 1):
            st.session_state[state_key] = page + 1
            st.rerun()
    start = page * page_size
    return items[start:start + page_size]


def _load_more_control(state_key: str, default_limit: int, step: int, has_more: bool) -> None:
    """天然增长、无上限数据（事件流水/发送记录/待处理告警）的"⬇️ 加载
    更多"控件：点击后把 `session_state[state_key]`（当前请求 limit）
    增加 `step`，并 `st.rerun()`——数据源本身是"按时间倒序取最近 N 条"，
    重新整页请求比维护增量缓存更简单可靠。调用方负责在拿到 `has_more`
    后调用本函数渲染按钮；`state_key` 对应的 limit 由调用方在请求前用
    `st.session_state.get(state_key, default_limit)` 读取。"""
    if has_more:
        if st.button("⬇️ 加载更多", key=f"{state_key}_more"):
            st.session_state[state_key] = st.session_state.get(state_key, default_limit) + step
            st.rerun()
    elif st.session_state.get(state_key, default_limit) > default_limit:
        st.caption("已加载全部记录。")


# ═══════════════════════════════════════════════════════════════════════
# Tab: 🔌 外部输入（External Input Gateway，P6）
# ═══════════════════════════════════════════════════════════════════════
def render_external_input_tab(client: AgentClient):
    """[外部输入网关设计方案 §6/P6] 展示已注册 source 列表/健康度、
    policies.yaml 路由规则、最近的 external.* 事件流水，以及待处理的
    notify_only 告警——四块内容分别对应设计文档 §6 列出的看板改动点。
    仍不提供在线编辑 sources.yaml/policies.yaml 内容本身的表单（YAML 还是
    直接编辑文件更直接），但额外提供一个"重新加载配置"按钮：改完文件后
    点一下即可让新配置生效而不需要重启 daemon——生效前会先对新增/改动的
    source 做一次可用性检测，检测不过就拒绝生效并提示错误，检测通过才
    切换配置并提示"已生效"。这个按钮的即时反馈和下方"待处理告警"/
    "最近事件流水"两块是同一次操作的两种呈现（GatewayPoller.reload() 会
    同时发布一条事件），互为补充，不冲突。
    """
    st.markdown("#### 🔌 外部输入网关 (External Input Gateway)")
    st.caption(
        "监控外部世界（RSS/JSON API/网页变化等）产生的信号，按 policies.yaml "
        "路由到「只通知」「生成目标候选」或「直接触发 Agent 处理」三档，默认最省钱、"
        "不会意外放大成大量 LLM 调用。详见 next_doc/external_input_gateway_design.md。"
    )

    col_refresh, col_reload = st.columns([1, 1])
    if col_refresh.button("🔄 刷新", key="ei_refresh"):
        st.rerun()
    if col_reload.button(
        "🔁 重新加载配置（sources.yaml）",
        key="ei_reload_sources",
        help="修改 .agent/external_input/sources.yaml 后点这里即可生效，不需要重启 daemon；"
             "会先对新增/改动的来源做一次可用性检测，检测不通过则保留原配置不生效。",
    ):
        with st.spinner("正在校验并加载新配置…"):
            reload_res = client.reload_external_input_sources()
        if reload_res and "_error" in reload_res:
            st.error(f"重新加载失败：{reload_res['_error']}")
        elif reload_res and reload_res.get("ok"):
            st.success(
                f"✅ 新配置已生效：新增 {len(reload_res.get('added') or [])} 个、"
                f"更新 {len(reload_res.get('updated') or [])} 个、"
                f"移除 {len(reload_res.get('removed') or [])} 个。"
            )
            if reload_res.get("added"):
                st.caption(f"新增：{', '.join(reload_res['added'])}")
            if reload_res.get("updated"):
                st.caption(f"更新：{', '.join(reload_res['updated'])}")
            if reload_res.get("removed"):
                st.caption(f"移除：{', '.join(reload_res['removed'])}")
            st.rerun()
        else:
            st.error("⛔ 新配置校验未通过，已保留原配置继续运行：")
            for err in (reload_res or {}).get("errors") or []:
                st.caption(f"　`{err.get('id')}`（{err.get('type')}）：{err.get('error')}")

    # ── 1. 已注册 source 列表（类型/状态/上次轮询时间/健康度）────────────
    st.markdown("##### 📡 已注册来源")
    src_resp = client.external_input_sources()
    if src_resp and "_error" in src_resp:
        st.error(f"获取来源列表失败：{src_resp['_error']}")
    else:
        src_resp = src_resp or {}
        if not src_resp.get("poller_available", True):
            st.warning(
                "GatewayPoller 当前不可用（可能不是 daemon 模式，或构造失败）——"
                "下方只展示 sources.yaml 里的静态配置，健康度字段全部为空。"
            )
        sources = src_resp.get("sources") or []
        if not sources:
            st.info("暂无已配置的外部输入来源。编辑 `.agent/external_input/sources.yaml` 添加。")
        for src in _client_side_page(sources, 10, "ei_sources_page"):
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
                c1.markdown(f"**{src['id']}**　`{src['type']}`")
                c2.markdown("✅ 启用" if src.get("enabled") else "⏸️ 已禁用")
                running = src.get("is_running")
                if running is None:
                    c3.caption("运行状态未知")
                else:
                    c3.markdown("🟢 运行中" if running else "⚪ 未运行")
                if src.get("circuit_open"):
                    c4.markdown(f"🔴 熔断（连续失败 {src.get('consecutive_failures', 0)} 次）")
                elif src.get("consecutive_failures"):
                    c4.markdown(f"🟡 近期有失败（{src.get('consecutive_failures')} 次）")
                else:
                    c4.markdown("🟢 健康")
                last_poll = src.get("last_poll_ts")
                if last_poll:
                    ago = max(0, time.time() - last_poll)
                    st.caption(f"上次轮询：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_poll))}（{ago:.0f} 秒前）· 间隔 {src.get('interval_seconds')} 秒")
                else:
                    st.caption(f"尚无轮询记录 · 间隔 {src.get('interval_seconds')} 秒")
                if src.get("last_error"):
                    st.caption(f"最近错误：{src['last_error']}")

    st.divider()

    # ── 2. policies.yaml 路由规则可视化（只读）───────────────────────────
    st.markdown("##### 🧭 路由规则 (policies.yaml)")
    st.caption("按顺序匹配，命中第一条规则即生效；都不匹配则默认 `notify_only`。")
    pol_resp = client.external_input_policies()
    if pol_resp and "_error" in pol_resp:
        st.error(f"获取路由规则失败：{pol_resp['_error']}")
    else:
        rules = (pol_resp or {}).get("rules") or []
        if not rules:
            st.info("暂无自定义路由规则，所有事件均按默认 `notify_only` 处理。")
        else:
            _ACTION_LABEL = {
                "notify_only": "📥 notify_only（只通知）",
                "enqueue_turn": "⚡ enqueue_turn（直接触发 Agent）",
            }
            # 分页只影响本页展示的起始编号，序号仍按 rules 里的真实下标
            # （即匹配优先级）计算，不会因为翻页而错乱。
            indexed_rules = list(enumerate(rules))
            for i, rule in _client_side_page(indexed_rules, 10, "ei_policies_page"):
                match_desc = ", ".join(f"{k}={v}" for k, v in (rule.get("match") or {}).items()) or "（匹配所有事件）"
                st.markdown(f"{i + 1}. **{match_desc}** → {_ACTION_LABEL.get(rule.get('action'), rule.get('action'))}")
                if rule.get("enqueue"):
                    st.caption(f"　enqueue 参数：{rule['enqueue']}")

    st.divider()

    # ── 3. 待处理的 notify_only 告警（专用分页端点，带 ack 按钮）────────
    st.markdown("##### 🔔 待处理告警")
    alerts_limit = st.session_state.get("ei_alerts_limit", 20)
    alerts_resp = client.external_input_alerts(limit=alerts_limit) or {}
    if "_error" in alerts_resp:
        st.caption("获取待处理告警失败，暂不展示告警列表。")
    else:
        alerts = alerts_resp.get("alerts") or []
        if not alerts:
            st.info("当前没有待处理的外部输入告警。")
        else:
            st.caption(f"共 {alerts_resp.get('total', len(alerts))} 条未处理，当前展示 {len(alerts)} 条。")
        # [BUGFIX] 根因是 alerts.jsonl 里可能出现 alert_id 完全相同的
        # 多条记录（policy.py::_notify_only 已经修复不再新增重复写入，
        # 但历史遗留数据/极端情况下仍可能存在），单纯用 alert_id 当
        # st.button 的 key 会在这种情况下触发
        # StreamlitDuplicateElementKey 崩溃、导致整个页面渲染不出来。
        # 这里额外拼上循环下标兜底，保证 key 一定唯一，不因为数据层面
        # 偶发的重复就让整个看板炸掉。
        for idx, alert in enumerate(alerts):
            cols = st.columns([5, 1])
            alert_summary = alert.get("title") or f"外部输入告警（{alert.get('source_type', '')}）"
            cols[0].caption(f"🌐 {alert_summary}")
            if cols[1].button("已读", key=f"ei_ack_{idx}_{alert.get('alert_id')}"):
                res = client.ack_external_alert(alert["alert_id"])
                if res and "_error" in res:
                    st.error(f"标记失败：{res['_error']}")
                else:
                    st.rerun()
        _load_more_control("ei_alerts_limit", 20, 20, bool(alerts_resp.get("has_more")))

    st.divider()

    # ── 3.4 新颖信号候选（独立通道，需人工确认，见改造方案 §2）───────────
    novelty_resp = client.novelty_candidates(limit=20) or {}
    novelty_total = novelty_resp.get("total", 0) if "_error" not in novelty_resp else 0
    with st.expander(f"🌟 新颖信号候选（{novelty_total} 条待确认）", expanded=novelty_total > 0):
        if "_error" in novelty_resp:
            st.caption("获取新颖信号候选失败。")
        else:
            candidates = novelty_resp.get("candidates") or []
            if not candidates:
                st.info("当前没有待确认的新颖信号候选。")
            for idx, cand in enumerate(candidates):
                with st.expander(cand.get("suggested_title") or cand.get("title", "（无标题）")):
                    st.caption(f"原始标题：{cand.get('title', '')}")
                    if cand.get("detail"):
                        st.caption(f"详情：{cand['detail']}")
                    if cand.get("url"):
                        st.caption(f"链接：{cand['url']}")
                    if cand.get("reason"):
                        st.caption(f"重要性判断理由：{cand['reason']}")
                    bcol1, bcol2 = st.columns(2)
                    if bcol1.button("✅ 创建目标", key=f"novelty_confirm_{idx}_{cand.get('candidate_id')}"):
                        res = client.confirm_novelty_candidate(cand["candidate_id"])
                        if res and "_error" in res:
                            st.error(f"创建失败：{res['_error']}")
                        else:
                            st.success(f"已创建目标：{res.get('goal_title', '')}")
                            st.rerun()
                    if bcol2.button("✖️ 忽略", key=f"novelty_dismiss_{idx}_{cand.get('candidate_id')}"):
                        res = client.dismiss_novelty_candidate(cand["candidate_id"])
                        if res and "_error" in res:
                            st.error(f"忽略失败：{res['_error']}")
                        else:
                            st.rerun()

    st.divider()

    # ── 3.6 外部知识反馈闭环（P1-P5，只读汇总面板）───────────────────────
    st.markdown("##### 🧠 外部知识反馈闭环（P1-P5）")
    st.caption(
        "候选队列过期巡检 / wiki 利用率 / 阈值自校准 / 外部趋势×能力薄弱点候选 / "
        "生态定位扫描 / 月度战略回顾——均为对已有链路的巡检-统计-回看补充，"
        "全部只读展示，详见 next_doc/external_knowledge_feedback_loop_improvement_plan.md。"
    )
    fb_resp = client.feedback_loop_summary() or {}
    if "_error" in fb_resp:
        st.caption(f"获取汇总失败：{fb_resp['_error']}")
    else:
        p1 = fb_resp.get("candidate_queue_triage") or {}
        p2 = fb_resp.get("wiki_utility_audit") or {}
        p3 = fb_resp.get("relevance_threshold_calibration") or {}
        p4a = fb_resp.get("external_trend_capability_link") or {}
        p4b = fb_resp.get("ecosystem_positioning_scan") or {}
        p5 = fb_resp.get("monthly_trend_retrospective") or {}

        with st.expander("🗂️ 候选队列过期巡检（P1）"):
            if "_error" in p1:
                st.caption(f"获取失败：{p1['_error']}")
            else:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("待确认", p1.get("pending", 0))
                c2.metric("已过期", p1.get("expired", 0))
                c3.metric("已确认", p1.get("confirmed", 0))
                c4.metric("已忽略", p1.get("dismissed", 0))
                st.caption("超过 30 天仍未处理的候选会被自动标记为「已过期」，不会一直占着上面的「🌟 新颖信号候选」审核视野。")

        with st.expander("📖 wiki 利用率（P2）"):
            if "_error" in p2:
                st.caption(f"获取失败：{p2['_error']}")
            else:
                st.caption(f"近 30 天有检索命中统计的页面共 {p2.get('total_pages_with_stats', 0)} 个。")
                top_used = p2.get("top_used") or []
                if not top_used:
                    st.info("暂无利用率统计（cron job 尚未跑过，或最近没有检索命中）。")
                for row in top_used:
                    st.caption(
                        f"`{row.get('page_id', '')}` · 命中 {row.get('hit_count', 0)} 次 · "
                        f"精排依据 {row.get('grounded_count', 0)} 次"
                    )

        with st.expander("🎚️ 阈值自校准（P3）"):
            if "_error" in p3:
                st.caption(f"获取失败：{p3['_error']}")
            else:
                st.metric("当前生效阈值", f"{p3.get('current_threshold', 0):.3f}")
                history = p3.get("history") or []
                if history:
                    st.caption("最近调整记录：")
                    for h in reversed(history):
                        ts = h.get("at")
                        ts_str = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "-"
                        st.caption(
                            f"`{ts_str}` {h.get('old_threshold', '-')} → "
                            f"{h.get('new_threshold', '-')}（{h.get('reason', '')}）"
                        )
                else:
                    st.caption("尚未发生过自动调整（可能还在 28 天 warmup 期内，或样本量不足）。")

        with st.expander(f"🔗 外部趋势×能力薄弱点候选（P4，{p4a.get('candidate_count', 0)} 条）"):
            if "_error" in p4a:
                st.caption(f"获取失败：{p4a['_error']}")
            else:
                for c in p4a.get("candidates") or []:
                    st.markdown(f"**{c.get('capability_domain', '')}**")
                    st.caption(f"依据 wiki 页面：{', '.join(c.get('wiki_page_ids') or [])}")
                    st.caption(c.get("rationale", ""))
                if not p4a.get("candidates"):
                    st.info("当前没有候选（草稿供人工审核，不会自动创建 Goal）。")

        with st.expander("🧭 生态定位扫描（P4）"):
            if "_error" in p4b:
                st.caption(f"获取失败：{p4b['_error']}")
            else:
                c1, c2 = st.columns(2)
                c1.metric("已沉淀 external_ecosystem 页面数", p4b.get("ecosystem_pages_count", 0))
                last_run = p4b.get("last_run_at")
                c2.caption(
                    "上次运行：" + (time.strftime("%Y-%m-%d %H:%M", time.localtime(last_run)) if last_run else "尚未运行")
                )
                st.caption(
                    "默认禁用，需要在 `agent_config.json` 里配置 `ecosystem_positioning.seeds`"
                    "（同类 agent 框架/开源项目名称列表）后到「⏰ Cron 任务」页签启用 "
                    "`sys:ecosystem_positioning_scan`。"
                )

        with st.expander(f"📅 月度战略回顾（P5{'，最新：' + p5.get('latest_month', '') if p5.get('latest_month') else ''}）"):
            if "_error" in p5:
                st.caption(f"获取失败：{p5['_error']}")
            elif not p5.get("latest_content"):
                st.info("尚未产出月度回顾文档（每月 1 日由 `sys:monthly_trend_retrospective` 自动生成）。")
            else:
                st.markdown(p5["latest_content"])
                other_months = [m for m in (p5.get("months") or []) if m != p5.get("latest_month")]
                if other_months:
                    st.caption(f"历史归档（{len(other_months)} 期）：{', '.join(sorted(other_months, reverse=True))}")

    st.divider()

    # ── 3.5 来源健康趋势（成功率/延迟，见改造方案 §3）────────────────────
    st.markdown("##### 📈 来源健康趋势")
    since_days = st.selectbox(
        "时间窗口", [7, 14, 30], index=0, key="ei_health_history_since_days",
        format_func=lambda d: f"最近 {d} 天",
    )
    health_resp = client.external_input_health_history(since_days=since_days) or {}
    if "_error" in health_resp:
        st.caption("获取来源健康趋势失败。")
    else:
        by_source = health_resp.get("sources") or {}
        if not by_source:
            st.info("暂无轮询历史记录，稍后再来看看。")
        for sid, stat in by_source.items():
            sr = stat.get("success_rate")
            with st.expander(
                f"{sid} · 成功率 {f'{sr * 100:.1f}%' if sr is not None else '-'} · "
                f"平均延迟 {stat.get('avg_duration_ms') or '-'} ms",
            ):
                c1, c2, c3 = st.columns(3)
                c1.metric("总轮询次数", stat.get("total_polls", 0))
                c2.metric("成功率", f"{sr * 100:.1f}%" if sr is not None else "-")
                c3.metric("P95 延迟(ms)", stat.get("p95_duration_ms") or "-")
                timeline = stat.get("timeline") or []
                if timeline:
                    try:
                        import pandas as pd
                        df = pd.DataFrame(timeline).set_index("date")
                        st.line_chart(df[["success_rate", "avg_duration_ms"]])
                    except Exception:
                        for row in timeline:
                            sr_val = row.get("success_rate")
                            sr_str = f"{sr_val * 100:.1f}%" if sr_val is not None else "-"
                            st.caption(
                                f"{row['date']}：成功率 {sr_str}，"
                                f"平均延迟 {row.get('avg_duration_ms') or '-'} ms"
                            )
                else:
                    st.caption("暂无按天分桶数据。")

    st.divider()

    # ── 4. 最近事件流水（供人工核对路由是否符合预期）─────────────────────
    st.markdown("##### 📜 最近事件流水")
    events_limit = st.session_state.get("ei_events_limit", 50)
    events_resp = client.external_input_events(limit=events_limit) or {}
    events = events_resp.get("events") or []
    if not events:
        st.caption("暂无 external.* 事件记录。")
    else:
        for evt in events:
            payload = evt.get("payload") or {}
            ts = evt.get("ts")
            ts_str = time.strftime("%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"
            st.caption(
                f"`{ts_str}` **{evt.get('event_type', '')}** "
                f"（来源 `{evt.get('source', '')}`，tier={evt.get('tier', '-')}）"
                f"：{payload.get('title', '')}"
            )
        _load_more_control("ei_events_limit", 50, 50, bool(events_resp.get("has_more")))

    st.divider()

    # ── 5. 归档查询（回顾式查询，见改造方案 §4）───────────────────────────
    st.markdown("##### 🗄️ 归档查询")
    st.caption("归档数据只读，仅供查询，不支持任何操作按钮。")
    ac1, ac2, ac3, ac4 = st.columns([1, 1, 1, 2])
    category = ac1.selectbox("类别", ["external_input", "notification"], key="ei_archive_category")
    since = ac2.text_input("起始月份（YYYY-MM）", key="ei_archive_since")
    until = ac3.text_input("截止月份（YYYY-MM）", key="ei_archive_until")
    keyword = ac4.text_input("关键词（可选）", key="ei_archive_keyword")
    if st.button("🔍 查询归档", key="ei_archive_query_btn"):
        if not since or not until:
            st.warning("请填写起止月份（格式 YYYY-MM）。")
        else:
            resp = client.archive_query(category, since, until, keyword=keyword) or {}
            if "_error" in resp:
                st.error(f"查询失败：{resp['_error']}")
            else:
                records = resp.get("records") or []
                st.caption(f"共 {resp.get('total', len(records))} 条命中。")
                if not records:
                    st.info("没有查到符合条件的归档记录。")
                for rec in records:
                    ts = rec.get("created_at") or rec.get("matched_at") or rec.get("occurred_at")
                    ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"
                    st.caption(f"`{ts_str}` **{rec.get('title', '')}**（来源 `{rec.get('source', rec.get('source_id', ''))}`）")


# ═══════════════════════════════════════════════════════════════════════
# Tab: 🔔 关注与通知（Watchlist & Notification，P7）
# ═══════════════════════════════════════════════════════════════════════
def render_notification_tab(client: AgentClient):
    """[watchlist_notification_goal_design.md §6/P7] 展示关注对象列表、
    分级汇报 tier 配置（含 cron job 运行时状态）、通知发送记录三块内容，
    全部只读——跟 render_external_input_tab 一样，配置本身还是靠直接编辑
    `.agent/external_input/watchlist.yaml`/`.agent/notification/report_tiers.yaml`，
    这里不提供在线编辑表单。
    """
    st.markdown("#### 🔔 关注与通知 (Watchlist & Notification)")
    st.caption(
        "关注对象命中后按用户配置的频率（tier）打包汇报，而不是一有动静就打扰；"
        "外部信息若与正在推进的某个 Goal 相关，会自动挂到该 Goal 的「相关外部信息」上，"
        "必要时还会主动把 Goal 拉回执行队列（详见 next_doc/watchlist_notification_goal_design.md）。"
    )

    if st.button("🔄 刷新", key="notif_refresh"):
        st.rerun()

    # ── 1. 关注对象列表（watchlist.yaml，只读）───────────────────────────
    st.markdown("##### 👀 关注对象 (watchlist.yaml)")
    wl_resp = client.notification_watchlist()
    if wl_resp and "_error" in wl_resp:
        st.error(f"获取关注对象列表失败：{wl_resp['_error']}")
    else:
        items = (wl_resp or {}).get("items") or []
        if not items:
            st.info(
                "暂无关注对象配置。编辑 `.agent/external_input/watchlist.yaml` 添加"
                "（每条至少包含 `id`/`keywords`/`report_tier`）。"
            )
        for item in _client_side_page(items, 10, "notif_watchlist_page"):
            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 2, 1])
                c1.markdown(f"**{item['id']}**　`{item.get('match_type', 'keyword')}`")
                c2.caption("关键词：" + "、".join(item.get("keywords") or []))
                c3.markdown("✅ 启用" if item.get("enabled", True) else "⏸️ 已禁用")
                st.caption(
                    f"汇报 tier：`{item.get('report_tier', '-')}` · "
                    f"去重窗口：{item.get('dedup_window_seconds', '-')} 秒"
                    + (f" · 通知渠道：{', '.join(item['notify_channels'])}" if item.get("notify_channels") else "")
                )

    st.divider()

    # ── 2. 分级汇报 tier 配置（report_tiers.yaml + cron job 运行时状态）───
    st.markdown("##### 📊 分级汇报 (report_tiers.yaml)")
    tiers_resp = client.notification_report_tiers()
    if tiers_resp and "_error" in tiers_resp:
        st.error(f"获取分级汇报配置失败：{tiers_resp['_error']}")
    else:
        tiers = (tiers_resp or {}).get("tiers") or []
        if not tiers:
            st.info(
                "暂无分级汇报配置。复制 `.agent/notification/report_tiers.yaml.example` "
                "为 `report_tiers.yaml` 后按需修改。"
            )
        for tier in _client_side_page(tiers, 10, "notif_tiers_page"):
            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 2, 2])
                c1.markdown(f"**{tier['id']}**　`{tier.get('schedule', '-')}`")
                if tier.get("job_enabled") is None:
                    c2.caption("job 运行时状态未知（cron scheduler 不可用）")
                else:
                    c2.markdown("✅ job 启用" if tier["job_enabled"] else "⏸️ job 已禁用")
                if tier.get("next_run_str"):
                    c3.caption(f"下次触发：{tier['next_run_str']}")
                st.caption(
                    f"通知渠道：{', '.join(tier.get('notify_channels') or [])} · "
                    f"连续空转次数：{tier.get('idle_streak', 0)}"
                )

    st.divider()

    # ── 3. 待处理汇报（汇报独立存储 新增：独立存储 + 专用端点，含完整正文）─────────
    # 跟"🔔 待处理告警"（外部输入网关 notify_only）是两个完全独立的东西：
    # 这里是"你关注的对象按周期打包汇总了一份清单"，不是需要你处理的
    # 告警。存储在独立的 reports.jsonl，也不再出现在 /v1/inbox 全局
    # 待办中心里。
    st.markdown("##### 📋 待处理汇报")
    reports_limit = st.session_state.get("notif_reports_limit", 20)
    reports_resp = client.notification_pending_reports(limit=reports_limit) or {}
    if "_error" in reports_resp:
        st.caption("获取待处理汇报失败，暂不展示。")
    else:
        reports = reports_resp.get("reports") or []
        if not reports:
            st.info("当前没有未读的关注对象汇报。")
        else:
            st.caption(f"共 {reports_resp.get('total', len(reports))} 条未读，当前展示 {len(reports)} 条。")
        for idx, rep in enumerate(reports):
            rid = rep.get("report_id")
            with st.expander(f"📋 {rep.get('title', '(无标题)')}", expanded=False):
                st.markdown(rep.get("detail") or "（无正文）")
                if st.button("标记已读", key=f"notif_report_ack_{idx}_{rid}"):
                    res = client.ack_notification_report(rid)
                    if res and "_error" in res:
                        st.error(f"标记失败：{res['_error']}")
                    else:
                        st.rerun()
        _load_more_control("notif_reports_limit", 20, 20, bool(reports_resp.get("has_more")))

    st.divider()

    # ── 4. 通知发送记录（NotificationDispatcher，只读，分页展示）────────
    st.markdown("##### 📮 通知发送记录")
    dispatch_limit = st.session_state.get("notif_dispatch_limit", 50)
    log_resp = client.notification_dispatch_log(limit=dispatch_limit)
    if log_resp and "_error" in log_resp:
        st.error(f"获取通知发送记录失败：{log_resp['_error']}")
    else:
        log_resp = log_resp or {}
        entries = log_resp.get("entries") or []
        if not entries:
            st.caption("暂无通知发送记录。")
        for entry in entries:
            ts = entry.get("created_at")
            ts_str = time.strftime("%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"
            results = entry.get("results") or {}
            status_str = "、".join(
                f"{ch}{'✅' if ok else '❌'}" for ch, ok in results.items()
            )
            st.caption(
                f"`{ts_str}` **{entry.get('title', '')}**"
                f"（来源 `{entry.get('source', '')}`）　{status_str}"
            )
        _load_more_control("notif_dispatch_limit", 50, 50, bool(log_resp.get("has_more")))


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
                    "🧠 自我状态", "🌱 成长顾问", "🎓 能力学习", "🧬 进化提案", "⏰ Cron 任务", "🗓️ 全局日程", "🔌 外部输入",
                    "🔔 关注与通知", "⚙️ 配置", "🔧 诊断", "🧪 混合执行", "📛 错误日志"])

    # [daemon_stability_and_ux_improvement_plan.md 补充 / 看板顶栏跳转]
    # 顶栏"正在执行"列表的"🔍 查看并控制"按钮点击后，把目标 tab 名记到
    # `_pending_tab_switch` 并 rerun；tabs 渲染出来之后（DOM 里已经有对应
    # 的 tab 按钮了）才能真正点击切换，所以要放在 `st.tabs(...)` 之后、
    # 消费一次就清空（不是持久状态，避免每次 rerun 都重复触发跳转，把
    # 用户手动点开的其它 tab 又切回去）。
    pending_tab = st.session_state.pop("_pending_tab_switch", None)
    if pending_tab:
        _inject_tab_switch_script(pending_tab)

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
        render_growth_tab(client)
    with tabs[8]:
        render_capability_tab(client)
    with tabs[9]:
        render_evolution_proposals_tab(client)
    with tabs[10]:
        render_cron_jobs_tab(client)
    with tabs[11]:
        render_global_schedule_tab(client)
    with tabs[12]:
        render_external_input_tab(client)
    with tabs[13]:
        render_notification_tab(client)
    with tabs[14]:
        render_config_tab(client)
    with tabs[15]:
        render_diagnostics_tab(client)
    with tabs[16]:
        render_hybrid_exec_tab(client)
    with tabs[17]:
        render_error_log_tab(client)

    # [P0 改造] 原来这里是 `if auto_refresh: time.sleep(3); st.rerun()`——
    # 整页阻塞 3 秒再重跑，期间所有 tab、所有正在填的表单都被冻结。
    # 现在"状态条"（render_topbar）和"事件流"（_render_events_panel）
    # 已经各自用 st.fragment(run_every=...) 做局部刷新，不再需要这个
    # 全局阻塞轮询兜底。auto_refresh 这个开关现在的语义变成"是否启用这
    # 两个 fragment 的自动刷新"，在各自函数内部读取，这里不用再处理。


if __name__ == "__main__":
    main()