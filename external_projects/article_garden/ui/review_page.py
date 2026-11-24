"""ui/review_page.py — ✍ 审阅（P6）：成稿与校验结果、证据并排、通过 / 发布 / 放弃 / 退回重写（附意见）。

页面只调用 writing.runner 的函数；成文本身在后台子进程中运行（write_article.py），进度见「▶ 运行日志」。
"""

from __future__ import annotations

import streamlit as st

from article_garden import joblog, store
from article_garden.library import catalog, front_matter
from article_garden.planning import states
from article_garden.research import pack
from article_garden.writing import runner
from ui import state
from ui.jobs import spawn
from ui.topbar import render_topbar

STATUS_NAMES = {
    states.RESEARCHED: "已调研", states.DRAFTING: "成文中", states.FAILED: "成文失败",
    states.DRAFTED: "待审阅", states.REVIEWED: "已审阅", states.PUBLISHED: "已发布",
}


def _article_body(ctx, aid: str):
    row = next((r for r in catalog.list_articles(ctx) if r["id"] == aid), None)
    if not row:
        return None, None
    text = ctx.path(row["path"]).read_text(encoding="utf-8")
    meta, body = front_matter.split(text)
    return meta, body


def _batch_section(ctx, rows, busy: bool) -> None:
    """批量成文：列出可成文的选题（已调研或成文失败，且证据充分），勾选后在后台逐个成文。"""
    st.markdown("**批量成文**")
    ready = [r for r in rows if runner.writable(ctx, r["id"])]
    if not ready:
        st.caption("没有可批量成文的选题（需「已调研」或「成文失败」，且证据充分）。")
        return
    st.caption("勾选后在后台逐个成文，一题失败不影响其它；点击停止或预算用尽则整批结束。成稿后仍需逐篇审阅。")

    def _set_all(value: bool) -> None:
        for r in ready:
            st.session_state[f"wb_{r['id']}"] = value
    b1, b2, _ = st.columns([1, 1, 6])
    b1.button("全选", key="wb_all", on_click=_set_all, args=(True,))
    b2.button("清空", key="wb_none", on_click=_set_all, args=(False,))
    picked = []
    for r in ready:
        if st.checkbox(f"{r['id']}  {r['title']}（{STATUS_NAMES[r['status']]}）", key=f"wb_{r['id']}"):
            picked.append(r["id"])
    if st.button(f"批量成文（{len(picked)} 个，后台）", type="primary", key="wb_go",
                 disabled=busy or not picked):
        spawn("write_article.py", ctx.id, "--batch", ",".join(picked))
        st.success("已在后台开始批量成文，进度见「▶ 运行日志」。完成后刷新本页。")


def render_review_page() -> None:
    ctx = state.current_ctx()
    if ctx is None:
        return
    render_topbar(ctx, "✍ 审阅")
    rows = [r for r in states.load_topics(ctx) if r["status"] in STATUS_NAMES]
    if not rows:
        st.info("还没有可成文或待审阅的选题。先在「🔎 调研」中完成证据充分的调研，再回到这里成文。")
        return
    st.dataframe([{"编号": r["id"], "选题": r["title"], "状态": STATUS_NAMES[r["status"]],
                   "文章": r.get("article_id") or ""} for r in rows], hide_index=True, use_container_width=True)
    busy = joblog.is_running(ctx.root, scopes=(runner.SCOPE,))
    _batch_section(ctx, rows, busy)
    st.divider()
    labels = {r["id"]: f"{r['id']}  {r['title']}" for r in rows}
    tid = st.selectbox("选择选题", list(labels), format_func=lambda i: labels[i], key="rev_sel")
    topic = states.get(ctx, tid)
    busy = joblog.is_running(ctx.root, scopes=(runner.SCOPE,))
    d = ctx.path("drafts", tid)

    if topic["status"] in (states.RESEARCHED, states.FAILED):
        if st.button("开始成文（后台）", type="primary", disabled=busy, key="rev_go"):
            spawn("write_article.py", ctx.id, tid)
            st.success("已在后台开始成文，进度见「▶ 运行日志」。完成后刷新本页。")
    if topic["status"] == states.DRAFTING:
        st.info("成文进行中……完成后刷新本页查看结果。")

    failed = store.read_json(d / "failed.json", default=None)
    if topic["status"] == states.FAILED and failed:
        st.error("成文未通过：" + "；".join(failed.get("reasons") or []))
        st.caption("中间产物（大纲、各节草稿、最后一次输出）已保留在 drafts 目录，可在下方手动处理或退回重写。")

    ck = store.read_json(d / "checks.json", default=None)
    if ck:
        st.caption(f"评审 {ck.get('critic_rounds', 0)} 轮 ｜ 修复 {ck.get('budget_used', 0)}/{ck.get('budget_max', 0)} 轮")

    aid = topic.get("article_id")
    if not aid:
        return
    meta, body = _article_body(ctx, aid)
    if body is None:
        st.warning(f"找不到文章文件：{aid}")
        return
    status_label = {"draft": "草稿", "reviewed": "已审阅", "published": "已发布", "archived": "已归档"}
    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"**{aid}**　{meta.get('title', '')}　`{status_label.get(meta.get('status'), meta.get('status'))}`")
        with st.container(border=True):
            st.markdown(body)
    with right:
        ev = pack.read(ctx, tid) or {}
        st.markdown("**证据**")
        if ev.get("points"):
            st.dataframe([{"要点": p["name"], "覆盖来源": "、".join(p["sources"]) or "（未覆盖）"} for p in ev["points"]],
                         hide_index=True, use_container_width=True)
        from article_garden.research import sources
        for c in sources.list_cards(ctx, tid):
            st.caption(f"{c['id']}　{c['title']}　`{c['credibility']}`　{c['url'] or '手动资料'}")
        st.markdown("**审阅**")
        if topic["status"] == states.DRAFTED:
            if st.button("通过审阅", key="rev_ok"):
                runner.approve(ctx, tid)
                st.rerun()
        if topic["status"] in (states.DRAFTED, states.REVIEWED):
            rv = st.text_area("按意见修改（保留原文，只改意见涉及的小节）", key="rev_rv_note", height=90,
                              placeholder="例如：第二节补充一个具体例子；语气再克制一些；删掉关于……的描述")
            if st.button("按意见修改（后台）", key="rev_rv", disabled=(busy or not rv.strip())):
                spawn("write_article.py", ctx.id, tid, "--revise", rv.strip())
                st.success("已在后台修改，进度见「▶ 运行日志」；完成后刷新本页，修改后需重新审阅。")
            last = (ck or {}).get("last_revision")
            if last:
                st.caption(f"上次修改：{last.get('at', '')[:16]}，改动小节：{'、'.join(last.get('changed_sections') or [])}；意见：{last.get('feedback', '')[:60]}")
            note = st.text_area("退回重写并附意见（整篇重新生成）", key="rev_note", height=90)
            if st.button("退回重写（后台）", key="rev_rw", disabled=(busy or not note.strip())):
                spawn("write_article.py", ctx.id, tid, "--feedback", note.strip())
                st.success("已在后台重写，完成后刷新本页。")
        if topic["status"] == states.REVIEWED:
            st.caption("发布前请确认内容；发布后文章进入导出范围（导出在 P7 提供）。")
            if st.button("确认发布", key="rev_pub", type="primary"):
                runner.publish(ctx, tid)
                st.rerun()
        if topic["status"] == states.PUBLISHED:
            st.success("已发布。")
        if topic["status"] in (states.DRAFTED, states.REVIEWED, states.FAILED):
            reason = st.text_input("放弃原因（必填）", key="rev_drop")
            if st.button("放弃此选题", key="rev_drop_go", disabled=not reason.strip()):
                runner.abandon(ctx, tid, reason)
                st.rerun()
