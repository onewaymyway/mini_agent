"""ui/runs_page.py — ▶ 运行日志：读取本库 logs/job.jsonl，实时轮询展示（所有后台任务都写这里）。"""

from __future__ import annotations

import streamlit as st
from datetime import datetime

from article_garden import joblog
from ui import state
from ui.topbar import render_topbar

try:  # 较新的 Streamlit 才有 run_every；没有则退化为手动刷新
    _fragment = st.fragment(run_every=3)
except TypeError:  # pragma: no cover
    _fragment = st.fragment


def render_runs_page() -> None:
    ctx = state.current_ctx()
    if ctx is None:
        return
    render_topbar(ctx, "▶ 运行日志")
    from article_garden import budget
    running = joblog.running_runs(ctx.root)
    starts = []
    for r in running:
        try:
            starts.append(datetime.fromisoformat(r["ts"]).timestamp())
        except (KeyError, ValueError):
            pass
    if not running:
        budget.clear_stop(ctx)          # 没有运行中的任务时，遗留的停止请求没有任何意义
    if running:
        names = "、".join(r.get("run", "") for r in running)
        st.info(f"正在运行：{names}")
    if running and budget.stop_pending_for(ctx, starts):
        st.warning("正在停止……（当前模型调用返回后立即结束，选题会退回原状态）")
    if st.button("■ 请求停止正在运行的任务", key="runs_stop", disabled=not running):
        budget.request_stop(ctx)
        st.rerun()
    _log_panel(str(ctx.id))


@_fragment
def _log_panel(domain_id: str) -> None:
    from article_garden.domains import DomainContext
    ctx = DomainContext(domain_id)
    level = st.radio("级别", ["全部", "仅警告与错误"], horizontal=True, key="runs_level")
    rows = joblog.tail(ctx.root, limit=300)
    if level != "全部":
        rows = [r for r in rows if r.get("level") in ("warn", "error")]
    if not rows:
        st.info("还没有运行记录。摘要、调研、成文等后台任务（P1 起）都会在这里显示步骤时间表与实时日志。")
        return
    st.code("\n".join(joblog.format_row(r) for r in rows[-200:]), language=None)
