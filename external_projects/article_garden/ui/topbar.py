"""ui/topbar.py — 每个页面顶部的状态条：当前文章库、挡位、后台任务。"""

from __future__ import annotations

import streamlit as st

from article_garden import joblog, settings
from article_garden.domains import DomainContext


def render_topbar(ctx: DomainContext, title: str) -> None:
    eff, _ = settings.effective_settings(ctx)
    busy = joblog.is_running(ctx.root)
    c1, c2, c3 = st.columns([3, 1, 2])
    c1.subheader(title)
    c2.caption(f"📚 {ctx.info()['name']}")
    c2.caption(f"挡位 **{eff['automation']['gear']}**")
    c3.caption("⏳ 后台任务进行中（见「运行日志」）" if busy else "后台任务：无")
    st.divider()


def render_placeholder(ctx: DomainContext, title: str, phase: str, what: str, empty_hint: str) -> None:
    """尚未实现的页面：说明它将做什么、哪一期提供、现在可以做什么（空状态即引导）。"""
    render_topbar(ctx, title)
    st.info(f"此页面在 **{phase}** 提供。\n\n{what}")
    st.caption(empty_hint)
