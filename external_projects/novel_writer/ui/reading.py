"""📖 阅读：章节目录、正文预览，以及导出全书合稿（M3）。"""

from __future__ import annotations

import streamlit as st

from novel_writer import tool_api
from ui.components import show_error


def render_reading(ws: str) -> None:
    res = tool_api.chapters(ws)
    if not res["ok"]:
        show_error(res)
        return
    rows = res["data"]["chapters"]
    if not rows:
        st.info("还没有章节。")
        return
    total = sum(r["words"] for r in rows)
    st.caption(f"共 {len(rows)} 章 · {total} 字")
    st.dataframe([{"章": r["chapter"], "标题": r["title"], "字数": r["words"], "文件": r["file"] or "—"} for r in rows],
                 use_container_width=True, hide_index=True)
    if st.button("导出全书合稿（Markdown）", key="rd_export", type="primary"):
        r = tool_api.export(ws)
        if r["ok"]:
            st.success(f"已导出 {r['data']['chapters']} 章 · {r['data']['words']} 字 → {r['data']['file']}")
        else:
            show_error(r)
    chosen = st.selectbox("预览章节", [r["chapter"] for r in rows],
                          format_func=lambda n: f"第{n}章 {next(r['title'] for r in rows if r['chapter'] == n)}",
                          key="rd_pick")
    detail = tool_api.chapter(ws, chosen)
    if detail["ok"]:
        st.markdown(detail["data"]["text"] or "（尚无正文）")
