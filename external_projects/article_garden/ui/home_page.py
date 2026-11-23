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

    from article_garden import budget, joblog, settings as gsettings
    from article_garden.gears import allowed as gear_allowed
    from ui.jobs import spawn

    eff, _ = gsettings.effective_settings(ctx)
    gear = eff["automation"]["gear"]
    busy = joblog.recently_active(ctx.root, scopes=("cycle",))
    can_run, why = gear_allowed(eff, "cycle")
    c1, c2 = st.columns(2)
    if c1.button("▶ 运行一轮（后台）", disabled=(not can_run or busy), type="primary", key="home_cycle",
                 help=why if not can_run else "按当前挡位与开关自动执行各步骤，进度见「▶ 运行日志」"):
        spawn("cycle.py", ctx.id)
        st.success("已在后台开始一轮闭环，进度见「▶ 运行日志」。")
    if c2.button("导出静态站", key="home_export"):
        from article_garden import export
        out = export.export_site(ctx)
        st.success(f"已导出：{out / 'index.html'}")
    u = budget.usage(ctx)
    st.caption(f"挡位 {gear} ｜ 今日 LLM 调用 {u['domain']['calls']}/{u['domain']['limit']}"
               f"（全局 {u['global']['calls']}/{u['global']['limit']}）")

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
