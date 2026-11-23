"""ui/home_page.py — 🏠 首页（工作台）：待办收件箱 + 流水线状态 + 下一步建议。"""

from __future__ import annotations

import streamlit as st

from article_garden import overview
from ui import state
from ui.topbar import render_topbar

_ICON = {"done": "✅", "todo": "⬜", "pending": "🔒"}


def render_home_page() -> None:
    ctx = state.current_ctx()
    if ctx is None:
        return
    render_topbar(ctx, "🏠 首页")

    st.button("▶ 运行一轮", disabled=True, help="完整闭环（cycle）在 P7 提供；目前各步骤随各期逐步开放")

    st.markdown("##### 流水线")
    stages = overview.pipeline_status(ctx)
    cols = st.columns(len(stages))
    for col, s in zip(cols, stages):
        col.markdown(f"{_ICON[s['state']]} **{s['label']}**")
        col.caption(s["detail"])
    st.caption("✅ 已完成　⬜ 待处理　🔒 尚未实现（括号内为所在期次）")

    left, right = st.columns(2)
    with left:
        st.markdown("##### 待办收件箱")
        items = overview.inbox_items(ctx)
        if not items:
            st.success("没有待处理事项。")
        for it in items:
            with st.container(border=True):
                st.markdown(f"**{it['title']}**")
                st.caption(it.get("detail", ""))
                if st.button("去处理", key=f"inbox_{it['kind']}_{it.get('id', '')}"):
                    state.goto(it["page"])
    with right:
        st.markdown("##### 下一步建议")
        steps = overview.next_steps(ctx)
        if not steps:
            st.write("暂无建议。")
        for i, s in enumerate(steps):
            with st.container(border=True):
                st.write(s["text"])
                if s.get("hint"):
                    st.caption(s["hint"])
                if st.button("前往", key=f"step_{i}", disabled=not s["enabled"] and s["page"] != "settings"):
                    state.goto(s["page"])
