"""📚 书架 与 ➕ 新建向导（M0 简版；完整向导在 M2 实现）。"""

from __future__ import annotations

import streamlit as st

from novel_writer import tool_api
from novel_writer.settings import GEARS, QUALITIES


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
            st.subheader(n["title"])
            total = n["total_chapters_planned"] or "?"
            st.caption(
                f"挡位 {n['gear']} {GEARS.get(n['gear'], '')} · 质量档 {QUALITIES.get(n['quality'], n['quality'])} · "
                f"{n['chapters_written']}/{total} 章 · {n['total_words']} 字"
            )
            st.code(n["workspace"], language=None)


def page_create() -> None:
    st.header("➕ 新建小说")
    st.caption("M0 简版：只创建工作目录与初始文件。蓝图等内容由后续里程碑的流水线生成。")
    with st.form("create_novel"):
        title = st.text_input("书名")
        workspace = st.text_input("工作目录（留空 = 默认 data/novels/<书名>_<时间戳>/）")
        gear = st.selectbox("控制挡位", list(GEARS), format_func=lambda g: f"{g} {GEARS[g]}", index=0)
        quality = st.selectbox("质量档位", list(QUALITIES), format_func=lambda q: QUALITIES[q], index=1)
        submitted = st.form_submit_button("创建")
    if submitted:
        if not title.strip():
            st.warning("请填写书名")
            return
        result = tool_api.create_novel(title, workspace=workspace.strip() or None, gear=gear, quality=quality)
        if result["ok"]:
            st.success(f"已创建：{result['data']['workspace']}")
        else:
            st.error(result["error"]["message"])
