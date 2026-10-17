"""📚 书架：列出所有小说，可打开进入驾驶舱（M2）。新建向导见 ui/wizard.py。"""

from __future__ import annotations

import streamlit as st

from novel_writer import tool_api
from novel_writer.settings import GEARS, QUALITIES
from ui.components import open_novel


def page_shelf() -> None:
    st.header("📚 书架")
    result = tool_api.list_novels()
    if not result["ok"]:
        st.error(result["error"]["message"])
        return
    novels = result["data"]["novels"]
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
