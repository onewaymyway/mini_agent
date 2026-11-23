"""ui/research_page.py — 🔎 调研（P5）：已放行选题的调研、证据包、来源卡（可删除）、手动补充资料（粘贴文本或网址）。"""

from __future__ import annotations

import streamlit as st

from article_garden import joblog, settings
from article_garden.planning import states
from article_garden.research import pack, runner, sources
from ui import state
from ui.jobs import spawn
from ui.topbar import render_topbar

STATUS_NAMES = {states.APPROVED: "已放行", states.RESEARCHING: "调研中", states.RESEARCHED: "已调研",
                states.DROPPED: "已放弃"}


def render_research_page() -> None:
    ctx = state.current_ctx()
    if ctx is None:
        return
    render_topbar(ctx, "🔎 调研")
    topics = [r for r in states.load_topics(ctx) if r["status"] in STATUS_NAMES]
    if not topics:
        st.info("还没有已放行的选题。先在「💡 选题」中放行，再回到这里调研。")
        return
    busy = joblog.is_running(ctx.root, scopes=("research",))
    st.dataframe([{"编号": r["id"], "选题": r["title"], "状态": STATUS_NAMES[r["status"]]} for r in topics],
                 hide_index=True, use_container_width=True)
    labels = {r["id"]: f"{r['id']}  {r['title']}" for r in topics}
    tid = st.selectbox("选择选题", list(labels), format_func=lambda i: labels[i], key="res_sel")
    topic = states.get(ctx, tid)

    eff, _ = settings.effective_settings(ctx)
    c1, c2 = st.columns([1, 2])
    if c1.button("开始调研（后台）", disabled=busy or topic["status"] == states.RESEARCHING,
                 type="primary", key="res_go"):
        spawn("research.py", ctx.id, tid)
        st.success("已在后台开始调研，进度见「▶ 运行日志」。")
    st.caption(f"搜索后端 {eff['search']['provider']} ｜ 每题最多 {eff['budget']['max_fetch_pages_per_topic']} 页 ｜ "
               f"摘录上限 {eff['thresholds']['excerpt_chars_max']} 字")

    ev = pack.read(ctx, tid)
    if ev:
        if ev.get("sufficient"):
            st.success(f"证据充分：要点覆盖 {ev['coverage']:.0%}，可信来源 {ev['sources_credible']} 条")
        else:
            st.warning("证据不足：" + "；".join(ev.get("reasons") or []))
        if ev.get("points"):
            st.dataframe([{"要点": p["name"], "覆盖来源": "、".join(p["sources"]) or "（未覆盖）"}
                          for p in ev["points"]], hide_index=True, use_container_width=True)
        if ev.get("library_links"):
            st.caption("库内已有、可互链的文章：" + "；".join(f"{x['id']} {x['title']}" for x in ev["library_links"]))
    else:
        st.info("还没有证据包。点击「开始调研」。")

    st.markdown("**来源卡**")
    cards = sources.list_cards(ctx, tid)
    if not cards:
        st.caption("没有来源卡。")
    for c in cards:
        with st.container(border=True):
            st.markdown(f"**{c['id']}**　{c['title']}　`{c['credibility']}`　{'手动' if c['source_type'] != 'web' else '网页'}")
            st.caption(c["url"] + f"　｜　抓取于 {c['fetched_at']}")
            with st.expander("摘录"):
                st.write(c["excerpt"])
            if st.button("删除这条来源", key=f"rm_{c['id']}"):
                runner.remove_source(ctx, tid, c["id"])
                st.rerun()

    st.markdown("**手动补充资料**")
    t1, t2 = st.columns(2)
    with t1:
        text = st.text_area("粘贴资料正文", key="man_text", height=120)
        title = st.text_input("标题（可选）", key="man_title")
        cred = st.selectbox("可信度", ["A", "B", "C", "D"], index=2, key="man_cred")
        if st.button("添加为手动来源", key="man_add"):
            try:
                runner.add_manual_text(ctx, tid, text, title=title, credibility=cred)
                pack.evaluate(ctx, tid, topic, ctx.load_charter()[0].get("sources", {}).get("min_credibility") or "B")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with t2:
        url = st.text_input("或粘贴网址（后台抓取）", key="man_url")
        if st.button("抓取网址并加入", key="man_url_go", disabled=not url.strip()):
            spawn("research.py", ctx.id, tid, "--add-url", url.strip())
            st.success("已在后台抓取，完成后刷新本页。")
