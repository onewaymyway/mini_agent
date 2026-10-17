"""💡 灵感箱：随手记，评估后整合 / 暂存 / 丢弃（M4）。"""

from __future__ import annotations

import streamlit as st

from novel_writer import tool_api
from ui.components import REASON_LABEL, STATUS_LABEL, show_error


def render_ideas(ws: str) -> None:
    with st.form("idea_form", clear_on_submit=True):
        text = st.text_area("记一条灵感", height=80, key="idea_text")
        if st.form_submit_button("记下"):
            r = tool_api.add_idea(ws, text)
            if r["ok"]:
                st.success("已记下。点「▶ 评估」让它进入判断。")
            else:
                show_error(r)
    res = tool_api.ideas(ws)
    if not res["ok"]:
        show_error(res)
        return
    rows = res["data"]["ideas"]
    if not rows:
        st.info("灵感箱是空的。")
        return
    for row in reversed(rows):
        with st.container(border=True):
            st.markdown(row["text"])
            c1, c2 = st.columns([3, 1])
            label = STATUS_LABEL.get(row["state"], row["state"])
            if row["decision"]:
                target = f"（第 {row['target_chapter']} 章）" if row["target_chapter"] else ""
                c1.caption(f"{label} · 结论：{row['decision']}{target}　{row['summary']}")
            else:
                c1.caption(label)
            if row["state"] in ("pending", "failed", "interrupted") and c2.button("▶ 评估", key=f"idea_run_{row['id']}"):
                r = tool_api.run_novel(ws, mode="single", step_id=f"idea.{row['id']}")
                if r["ok"]:
                    st.toast("已提交评估。稍后刷新查看结论。")
                else:
                    show_error(r)
    st.caption("评估由后台 worker 执行；总控台里也能看到对应步骤（类别：修订/推演/灵感，始终手动）。" + REASON_LABEL.get("manual", ""))
