"""ui/wizard_page.py — ➕ 新建文章库向导，以及 🗂 所有文章库总览。"""

from __future__ import annotations

import streamlit as st

from article_garden import domains, templates
from ui import state


def render_wizard_page() -> None:
    st.subheader("➕ 新建文章库")
    st.caption("一个文章库就是一个领域，拥有完全独立的文章、知识树、选题与设置，互不共享数据。"
               "新库的自动化开关一律保守默认（全关、G0），不继承其它库。")

    existing = domains.list_domains()
    tpls = templates.list_templates()
    tpl_labels = {t["id"]: f"{t['label']} — {t['description']}" for t in tpls}
    options = [t["id"] for t in tpls]
    if existing:
        options.append("__copy__")
        tpl_labels["__copy__"] = "复制另一个文章库的章程 — 只复制章程，不含文章与数据"

    with st.form("new_domain"):
        name = st.text_input("名称", placeholder="例如：人工智能")
        did = st.text_input("文章库 id", placeholder="例如：ai（小写字母开头，仅含小写字母/数字/下划线/连字符）",
                            help="用于目录名与链接，创建后不可改；显示名称以后可改")
        choice = st.radio("章程模板", options, format_func=lambda k: tpl_labels[k],
                          index=options.index("ai") if "ai" in options else 0)
        copy_from = None
        if choice == "__copy__":
            copy_from = st.selectbox("复制哪个库的章程", [d["id"] for d in existing],
                                     format_func=lambda i: next(d["name"] for d in existing if d["id"] == i))
        submitted = st.form_submit_button("创建")
    if submitted:
        try:
            ctx = domains.create_domain(did, name, template="blank" if choice == "__copy__" else choice,
                                        copy_charter_from=copy_from)
        except (domains.DomainError, KeyError) as exc:
            st.error(str(exc))
            return
        state.set_domain(ctx.id)
        st.success(f"已创建文章库「{name}」。下一步：到「设置 → 章程」确认领域范围，再导入文章（P1）。")
        st.rerun()


def render_domains_overview_page() -> None:
    st.subheader("🗂 所有文章库")
    rows = domains.list_domains(include_archived=True)
    if not rows:
        st.info("还没有文章库，请先到「➕ 新建文章库」创建一个。")
        return
    show_archived = st.checkbox("显示已归档", value=False)
    cur = state.current_domain_id()
    cols = st.columns(3)
    i = 0
    for d in rows:
        archived = d.get("status") == domains.STATUS_ARCHIVED
        if archived and not show_archived:
            continue
        with cols[i % 3].container(border=True):
            st.markdown(f"**{d['name']}**" + ("　🗄 已归档" if archived else ("　◀ 当前" if d["id"] == cur else "")))
            st.caption(f"id：`{d['id']}` · 模板：{d.get('template', '-')}")
            try:
                n = domains.DomainContext(d["id"]).article_count()
            except domains.DomainError:
                n = "?"
            st.write(f"文章：{n} 篇")
            st.caption(f"最近活动：{str(d.get('last_activity', ''))[:16].replace('T', ' ')}")
            if not archived and d["id"] != cur and st.button("进入", key=f"enter_{d['id']}"):
                state.set_domain(d["id"])
                st.rerun()
        i += 1
