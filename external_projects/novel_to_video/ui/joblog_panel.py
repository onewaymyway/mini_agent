"""ui/joblog_panel.py — 通用实时日志面板：显示后台任务状态与最近日志，任务结束后自动刷新所在页面。

  * 面板是 fragment，每 2 秒读取一次状态与日志，只重绘自身；
  * 任务由运行中变为结束时，整页刷新一次（例如性别列表、素材画廊会随之更新）；
  * 运行中可请求停止（当前实体或步骤完成后停止）。
"""

from __future__ import annotations

from typing import List, Optional

import streamlit as st

from novel_to_video import joblog
from novel_to_video import tool_api
from pathlib import Path

REFRESH_SECONDS = 2
JOB_LABEL = {"idle": "空闲", "running": "运行中", "done": "已完成", "stopped": "已停止", "failed": "失败"}


def render_job_panel(pdir: str, scopes: Optional[List[str]], *, title: str = "运行日志",
                     height: int = 280, limit: int = 200) -> None:
    _panel(pdir, tuple(scopes) if scopes else None, title, height, limit)


@st.fragment(run_every=REFRESH_SECONDS)
def _panel(pdir: str, scopes, title: str, height: int, limit: int) -> None:
    key = f"_jobpanel_prev:{pdir}:{','.join(scopes or ['all'])}"
    js = tool_api.job_status(pdir)["data"]
    running = js.get("state") == "running"
    previously = st.session_state.get(key, False)
    st.session_state[key] = running
    if previously and not running:
        st.rerun(scope="app")  # 任务刚结束：整页刷新，让列表与画廊显示最新结果

    label = js.get("label") or "—"
    state = JOB_LABEL.get(js.get("state"), js.get("state", "空闲"))
    head = f"**{title}**　·　{'🟢' if running else '⚪'} {state}"
    if js.get("label"):
        head += f"　·　{label}"
    st.markdown(head)

    c1, c2 = st.columns([3, 1])
    if running:
        if c2.button("请求停止", key=f"jobstop:{pdir}:{','.join(scopes or ['all'])}"):
            r = tool_api.job_stop(pdir)
            (st.success if r["ok"] else st.error)("已请求停止" if r["ok"] else r["error"]["message"])
    rows = tool_api.job_logs(pdir, list(scopes) if scopes else None, limit)["data"]
    text = "\n".join(joblog.format_row(r) for r in rows) or "（暂无日志）"
    with st.container(height=height, border=True):
        st.code(text, language="text")
