"""📚 书架：列出所有小说，可打开进入驾驶舱（M2）。新建向导见 ui/wizard.py。"""

from __future__ import annotations

import streamlit as st

from novel_writer import tool_api
from novel_writer.settings import GEARS, QUALITIES
from ui.components import INBOX_ICON, INBOX_LABEL, open_novel


def _render_global_inbox() -> None:
    res = tool_api.inbox_all()
    if not res["ok"] or not res["data"]["total"]:
        return
    data = res["data"]
    with st.expander(f"📥 全部小说待处理 {data['total']} 项（阻塞 {data['blocking']} 项）",
                     expanded=data["blocking"] > 0):
        for book in data["books"]:
            if not book["total"]:
                continue
            st.markdown(f"**{book['title']}**　挡位 {book['gear']}　{book['total']} 项")
            for it in book["items"]:
                st.markdown(f"　{INBOX_ICON.get(it['severity'], '•')} {INBOX_LABEL.get(it['kind'], it['kind'])}　"
                            f"{it.get('title') or ''}　—　{it['message']}")


def _render_batch_button(has_novels: bool) -> None:
    if not has_novels:
        return
    with st.expander("⚡ 批量推进自动挡（G2 / G3）", expanded=False):
        st.caption("每本自动挡小说最多推进 1 章，在后台运行；G0 / G1 的书不会被触碰。"
                   "定时推进默认关闭，需要时在 daemon 中手动添加 cron 行。")
        if st.button("▶ 后台批量推进（每本 1 章）", key="shelf_batch_go"):
            res = tool_api.spawn_advance_all_auto(max_chapters=1)
            if res["ok"]:
                st.success("已在后台启动。日志：" + str(res["data"].get("log") or "（测试模式未启动）"))
            else:
                st.error(res["error"]["message"])


def page_shelf() -> None:
    st.header("📚 书架")
    _render_global_inbox()
    result = tool_api.list_novels()
    if not result["ok"]:
        st.error(result["error"]["message"])
        return
    novels = result["data"]["novels"]
    _render_batch_button(bool(novels))
    if not novels:
        st.info("书架还是空的，去左侧「➕ 新建小说」创建第一本。")
        return
    for n in novels:
        with st.container(border=True):
            if n.get("error"):
                st.error(f"{n['workspace']}：{n['error']}")
                continue
            c1, c2 = st.columns([5, 1])
            c1.subheader(n["title"])
            c2.button("打开", key=f"open_{n['workspace']}", on_click=open_novel, args=(n["workspace"],))
            total = n["total_chapters_planned"] or "?"
            st.caption(
                f"挡位 {n['gear']} {GEARS.get(n['gear'], '')} · 质量档 {QUALITIES.get(n['quality'], n['quality'])} · "
                f"{n['chapters_written']}/{total} 章 · {n['total_words']} 字"
            )
            st.code(n["workspace"], language=None)
