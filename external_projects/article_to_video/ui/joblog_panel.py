"""ui/joblog_panel.py — 实时运行面板：运行状态、步骤时间表、后台日志。

  * 每 2 秒局部刷新（st.fragment），只重绘本面板，不影响页面上的表单；
  * 日志区固定高度、自动滚动到最新一行；
  * 运行中可请求停止；任务刚结束时整页刷新一次，使页面上的列表显示最新结果；
  * 既显示本界面启动的后台任务，也显示命令行等其它进程启动的运行（据日志活跃度判断，此时不能从界面停止）。
"""

from __future__ import annotations

import html as _html
from pathlib import Path
from typing import List, Optional

import streamlit as st
import streamlit.components.v1 as components

from article_to_video import joblog, timeline
from ui import jobs

REFRESH_SECONDS = 2
JOB_LABEL = {"running": "运行中", "done": "已完成", "stopped": "已停止", "error": "失败"}


def build_log_html(text: str, height: int) -> str:
    """日志区的 HTML：固定高度的可滚动区域，加载后自动滚到底部（最新一行）。文本经过转义。"""
    body = _html.escape(text)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;background:transparent;}}
#log{{box-sizing:border-box;height:{int(height)}px;overflow-y:auto;margin:0;padding:8px 10px;
border:1px solid rgba(128,128,128,.35);border-radius:6px;background:#f7f7f7;color:#222;
font:12px/1.5 Consolas,'Courier New',monospace;white-space:pre-wrap;word-break:break-all;}}
@media (prefers-color-scheme: dark){{#log{{background:#1e1e1e;color:#ddd;}}}}
</style></head><body><pre id="log">{body}</pre>
<script>
(function(){{
  var el = document.getElementById('log');
  function toBottom(){{ el.scrollTop = el.scrollHeight; }}
  toBottom();
  window.addEventListener('load', toBottom);
}})();
</script></body></html>"""


def run_state(pdir: Path, scopes: Optional[List[str]]) -> dict:
    """当前运行状态：local=本界面的后台任务在跑；external=其它进程（如命令行）近期仍在写日志。"""
    job = jobs.status(pdir)
    local = bool(job and job["state"] == "running")
    external = False
    if not local:
        runs = timeline.build_timeline(pdir, busy=False, scopes=scopes, limit=1)
        has_open = bool(runs) and runs[0]["status"] == "interrupted"   # 有未结束的运行事件
        external = has_open and joblog.recently_active(pdir, scopes)
    return {"job": job, "local": local, "external": external, "busy": local or external}


def render_run_monitor(pdir: Path, scopes: Optional[List[str]] = None, *, title: str = "运行监控",
                       show_timeline: bool = True, height: int = 300, limit: int = 300) -> None:
    """运行状态 + 步骤时间表 + 实时日志。"""
    _monitor(str(pdir), tuple(scopes) if scopes else None, title, show_timeline, height, limit)


def render_job_panel(pdir: Path, scopes: Optional[List[str]] = None, *, title: str = "运行日志",
                     height: int = 260, limit: int = 300) -> None:
    """只有运行状态与日志（没有时间表）。"""
    _monitor(str(pdir), tuple(scopes) if scopes else None, title, False, height, limit)


def _timeline_block(pdir: Path, scopes, busy: bool) -> None:
    runs = timeline.build_timeline(pdir, busy=busy, scopes=scopes, limit=8)
    st.markdown("**步骤时间表**")
    if not runs:
        st.caption("（还没有运行记录）")
        return
    cur = runs[0]
    mark = timeline.STATUS_MARK.get(cur["status"], "")
    st.caption(f"{mark} {cur['run']}　·　{timeline.STATUS_TEXT.get(cur['status'], cur['status'])}"
               f"　·　开始 {(cur['started_at'] or '')[11:19]}　·　总用时 {timeline.fmt_seconds(cur['seconds'])}")
    rows = timeline.step_rows(cur)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    if cur["status"] in ("failed", "stopped") and cur.get("error"):
        st.error(cur["error"])
    failed = [s for s in cur["steps"] if s["status"] == "failed" and s.get("traceback")]
    tb = (failed[-1].get("traceback") if failed else None) or cur.get("traceback")
    if tb:
        with st.expander("失败堆栈"):
            st.code(tb)
    if len(runs) > 1:
        with st.expander(f"更早的运行（{len(runs) - 1} 次）"):
            for r in runs[1:]:
                st.caption(f"{timeline.STATUS_MARK.get(r['status'], '')} {r['run']}　·　"
                           f"{(r['started_at'] or '')[5:19]}　·　{timeline.STATUS_TEXT.get(r['status'], r['status'])}"
                           f"　·　{timeline.fmt_seconds(r['seconds'])}")
                r_rows = timeline.step_rows(r)
                if r_rows:
                    st.dataframe(r_rows, use_container_width=True, hide_index=True)


@st.fragment(run_every=REFRESH_SECONDS)
def _monitor(pdir_s: str, scopes, title: str, show_timeline: bool, height: int, limit: int) -> None:
    pdir = Path(pdir_s)
    key = f"_monitor_prev:{pdir_s}:{','.join(scopes or ['all'])}"
    state = run_state(pdir, list(scopes) if scopes else None)
    previously = st.session_state.get(key, False)
    st.session_state[key] = state["busy"]
    if previously and not state["busy"]:
        st.rerun(scope="app")   # 刚结束：整页刷新，页面上的列表与状态显示最新结果

    job = state["job"]
    if state["local"]:
        status_text = f"🟢 运行中 · {job['label']}"
    elif state["external"]:
        status_text = "🟢 运行中（由命令行或其它进程启动）"
    elif job and (not scopes or job.get("scope") in scopes):
        status_text = f"⚪ 空闲 · 最近一次：{job['label']}（{JOB_LABEL.get(job['state'], job['state'])}）"
    else:
        status_text = "⚪ 空闲"
    st.markdown(f"**{title}**　·　{status_text}")

    if state["local"]:
        if st.button("⏹ 请求停止", key=f"monitor_stop:{pdir_s}:{','.join(scopes or ['all'])}"):
            jobs.stop(pdir)
            st.success("已请求停止，正在中断当前请求…")
    elif state["external"]:
        st.caption("该运行不是由本界面启动的，只能查看，不能从这里停止。")

    if show_timeline:
        _timeline_block(pdir, list(scopes) if scopes else None, state["busy"])

    rows = joblog.tail(pdir, limit, list(scopes) if scopes else None)
    text = "\n".join(joblog.format_row(r) for r in rows) or "（暂无日志）"
    st.markdown("**后台日志**")
    components.html(build_log_html(text, height), height=height + 4, scrolling=False)
