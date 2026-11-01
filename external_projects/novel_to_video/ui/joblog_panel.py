"""ui/joblog_panel.py — 通用实时日志面板。

同时反映两类运行状态：
  * 后台任务（素材 G-2/G-3、合成、封面、性别检索）：jobs 的状态；
  * 章节队列（单章或批量运行）：runtime 的状态，显示当前章节与阶段。
面板每 2 秒刷新；运行中可请求停止；任务或队列结束时整页刷新一次，使列表与画廊显示最新结果。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import streamlit as st

from novel_to_video import joblog, runtime, tool_api

REFRESH_SECONDS = 2
JOB_LABEL = {"idle": "空闲", "running": "运行中", "done": "已完成", "stopped": "已停止", "failed": "失败"}


def render_job_panel(pdir: str, scopes: Optional[List[str]], *, title: str = "运行日志",
                     height: int = 280, limit: int = 200) -> None:
    _panel(pdir, tuple(scopes) if scopes else None, title, height, limit)


def _states(pdir: str):
    js = tool_api.job_status(pdir)["data"]
    rt = runtime.read_state(Path(pdir))
    job_running = js.get("state") == "running"
    queue_busy = rt.get("state") in ("running", "stopping")
    return js, rt, job_running, queue_busy


@st.fragment(run_every=REFRESH_SECONDS)
def _panel(pdir: str, scopes, title: str, height: int, limit: int) -> None:
    key = f"_jobpanel_prev:{pdir}:{','.join(scopes or ['all'])}"
    js, rt, job_running, queue_busy = _states(pdir)
    busy = job_running or queue_busy
    previously = st.session_state.get(key, False)
    st.session_state[key] = busy
    if previously and not busy:
        st.rerun(scope="app")  # 刚结束：整页刷新，列表与画廊显示最新结果

    if queue_busy:
        cur = f"第 {rt['chapter']} 章" if rt.get("chapter") else "章节间"
        state_text = f"🟢 运行中（章节队列）· {cur}"
        detail = rt.get("message") or ""
    elif job_running:
        state_text = f"🟢 运行中 · {js.get('label') or '后台任务'}"
        detail = ""
    else:
        state_text = f"⚪ 空闲 · 最近一次：{js.get('label') or '—'}（{JOB_LABEL.get(js.get('state'), js.get('state', '空闲'))}）"
        detail = ""
    st.markdown(f"**{title}**　·　{state_text}")
    if detail:
        st.caption(detail)

    if busy:
        if st.button("请求停止", key=f"jobstop:{pdir}:{','.join(scopes or ['all'])}"):
            r = tool_api.stop_queue(pdir) if queue_busy else tool_api.job_stop(pdir)
            ok = r["ok"]
            (st.success if ok else st.error)("已请求停止，将在当前实体或章节完成后停止" if ok else r["error"]["message"])
    rows = tool_api.job_logs(pdir, list(scopes) if scopes else None, limit)["data"]
    text = "\n".join(joblog.format_row(r) for r in rows) or "（暂无日志）"
    with st.container(height=height, border=True):
        st.code(text, language="text")
