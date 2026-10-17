"""🧵 线索：伏笔与张力（只读，M2）。手动登记/兑现标记属于 M4。"""

from __future__ import annotations

import streamlit as st

from novel_writer import tool_api
from ui.components import show_error


def render_threads(ws: str) -> None:
    res = tool_api.threads(ws)
    if not res["ok"]:
        show_error(res)
        return
    data = res["data"]
    tracker = data["plot_tracker"]
    if tracker is None and not data["foreshadowing_md"]:
        st.info("还没有伏笔数据。蓝图确认后，运行 register_plot 步骤即可生成。")
        return
    if isinstance(tracker, dict):
        items = tracker.get("foreshadowing") or tracker.get("threads") or []
        if isinstance(items, list) and items:
            import pandas as pd

            rows = [{k: (v if not isinstance(v, (list, dict)) else str(v)) for k, v in it.items()}
                    for it in items if isinstance(it, dict)]
            if rows:
                st.subheader("伏笔")
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    if data["foreshadowing_md"]:
        with st.expander("伏笔注册表（Markdown）", expanded=False):
            st.markdown(data["foreshadowing_md"])
    if data["tension_md"]:
        with st.expander("张力曲线", expanded=False):
            st.markdown(data["tension_md"])
    with st.expander("plot_tracker.json", expanded=False):
        st.json(tracker or {})
