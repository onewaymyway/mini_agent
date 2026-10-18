"""📖 阅读：章节目录、正文预览，以及导出全书合稿（M3）。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from novel_writer import tool_api
from ui.components import show_error, show_df

EXPORT_LABELS = {"md": "Markdown", "txt": "纯文本 (txt)", "docx": "Word (docx)", "epub": "电子书 (epub)"}


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
    show_df([{"章": r["chapter"], "标题": r["title"], "字数": r["words"], "文件": r["file"] or "—"} for r in rows],
                 width="stretch", hide_index=True)
    c_fmt, c_btn = st.columns([1, 2])
    fmt = c_fmt.selectbox("导出格式", list(EXPORT_LABELS), format_func=lambda f: EXPORT_LABELS[f], key="rd_fmt")
    if c_btn.button("导出全书合稿", key="rd_export", type="primary"):
        r = tool_api.export(ws, fmt)
        if r["ok"]:
            st.session_state["rd_last_export"] = r["data"]
            st.success(f"已导出 {r['data']['chapters']} 章 · {r['data']['words']} 字 → {r['data']['file']}")
        else:
            show_error(r)
    last = st.session_state.get("rd_last_export")
    if last and (ws_path := Path(ws) / last["file"]).is_file():
        st.download_button(f"下载 {last['file'].rsplit('.', 1)[-1]} 文件", data=ws_path.read_bytes(),
                           file_name=ws_path.name, key="rd_download")
    chosen = st.selectbox("预览章节", [r["chapter"] for r in rows],
                          format_func=lambda n: f"第{n}章 {next(r['title'] for r in rows if r['chapter'] == n)}",
                          key="rd_pick")
    detail = tool_api.chapter(ws, chosen)
    if detail["ok"]:
        st.markdown(detail["data"]["text"] or "（尚无正文）")
